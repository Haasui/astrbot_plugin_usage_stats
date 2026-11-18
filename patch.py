"""包装 provider 类方法，采集经过这些方法的调用并支持卸载还原。

包装安装在类上，避免 provider 实例更换后失效；子类覆写会遮蔽基类方法，
因此需遍历各类自己定义的实现。目标类由 main.py 传入，本模块不依赖 AstrBot。
采集范围与约束见 docs/ARCHITECTURE.md。
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import threading
import time
from contextvars import ContextVar
from typing import Any, Callable

MARK = "__usage_stats_patched__"
"""打在包装函数上的标记。还原时靠它确认「当前这个方法还是我打的那个」。"""


def collect_targets(base: type, names: tuple[str, ...] = ("text_chat", "text_chat_stream")) -> list[tuple[type, str]]:
    """找出所有需要打补丁的 (类, 方法名)。

    遍历基类及其子类，只收各类 __dict__ 中自己定义的方法，避免重复包装继承的方法。

    Args:
        base: 基类，例如 astrbot.core.provider.provider.Provider
        names: 要捕获的方法名

    Returns:
        [(类, 方法名)]，顺序不保证。
    """
    out: list[tuple[type, str]] = []
    seen: set[type] = set()
    stack: list[type] = [base]
    while stack:
        cls = stack.pop()
        if cls in seen:
            continue
        seen.add(cls)
        for name in names:
            fn = cls.__dict__.get(name)
            if fn is not None and callable(fn):
                out.append((cls, name))
        try:
            stack.extend(cls.__subclasses__())
        except Exception:  # noqa: BLE001
            pass
    return out


class PatchRegistry:
    """登记「类 + 方法名 -> 原函数」，卸载时逐个精确还原。"""

    def __init__(self) -> None:
        self._saved: list[tuple[type, str, Any]] = []

    @property
    def installed(self) -> int:
        return len(self._saved)

    def install(
        self,
        targets: list[tuple[type, str]],
        make_wrapper: Callable[[Any, str], Any],
    ) -> list[str]:
        """给目标打补丁。返回成功打上的 "类名.方法名" 列表。"""
        done: list[str] = []
        for cls, name in targets:
            current = cls.__dict__.get(name)
            if current is None or getattr(current, MARK, False):
                # 已带标记的方法不重复包装，保持安装幂等
                continue
            wrapper = make_wrapper(current, name)
            setattr(wrapper, MARK, True)
            try:
                setattr(cls, name, wrapper)
            except Exception:  # noqa: BLE001
                continue
            self._saved.append((cls, name, current))
            done.append(f"{cls.__name__}.{name}")
        return done

    def restore(self) -> list[str]:
        """精确还原。

        仅还原仍带本插件标记的方法，避免覆盖未带标记的后续替换。
        写回保存的函数，不用 delattr，避免删除类属性改变方法解析。
        """
        done: list[str] = []
        while self._saved:
            cls, name, original = self._saved.pop()
            current = cls.__dict__.get(name)
            if getattr(current, MARK, False):
                setattr(cls, name, original)
                done.append(f"{cls.__name__}.{name}")
        return done


_ACTIVE_CALL: ContextVar[tuple | None] = ContextVar("usage_stats_active_call", default=None)


def _owner() -> tuple:
    # A child task inherits ContextVars, but is a distinct model-call path.
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return threading.get_ident(), task


def _field(obj: Any, name: str) -> Any:
    try:
        return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
    except Exception:  # noqa: BLE001
        return None


def _has_content(chunk: Any, depth: int = 0) -> bool:
    """Only explicit generated text/tool payloads prove a first token arrived."""
    if depth > 5:
        return False
    if isinstance(chunk, str):
        return bool(chunk)
    for name in ("completion_text", "text", "content", "reasoning_content", "reasoning"):
        value = _field(chunk, name)
        if isinstance(value, str) and value:
            return True
        if isinstance(value, (list, tuple)) and any(_has_content(x, depth + 1) for x in value):
            return True
    for name in ("delta", "message", "raw_completion"):
        value = _field(chunk, name)
        if value is not None and _has_content(value, depth + 1):
            return True
    choices = _field(chunk, "choices")
    if isinstance(choices, (list, tuple)) and any(_has_content(x, depth + 1) for x in choices):
        return True
    tools = _field(chunk, "tool_calls")
    if isinstance(tools, (list, tuple)):
        for tool in tools:
            function = _field(tool, "function") or tool
            if any(isinstance(_field(function, key), str) and _field(function, key)
                   for key in ("name", "arguments")):
                return True
    return False


def _safe_record(record: Callable[..., None], provider: Any, method_name: str,
                 response: Any, kwargs: dict, started: float,
                 capture_timing: bool = False, result: str = "success",
                 ttft_ms: int | None = None) -> None:
    """Accounting must never replace a provider's result or original exception."""
    try:
        elapsed = max(0, int((time.monotonic() - started) * 1000))
        args = (provider, method_name, response, kwargs, elapsed)
        if capture_timing:
            record(*args, {"result": result, "ttft_ms": ttft_ms})
        else:
            record(*args)
    except BaseException:  # noqa: BLE001
        pass


def make_wrapper(record: Callable[..., None], capture_timing: bool = False):
    """Wrap providers; by default record receives its original five arguments.

    Opt-in timing adds a sixth metadata dictionary, never response/error text.
    A ContextVar covers invocation and consumption, not time spent in consumers.
    """
    def factory(original: Any, method_name: str) -> Any:
        async def consume_stream(self, stream, kwargs, started, enabled):
            last = None
            ttft = None
            outcome = "canceled"
            completed = False
            try:
                while True:
                    token = _ACTIVE_CALL.set(_owner())
                    try:
                        chunk = await anext(stream)
                    except StopAsyncIteration:
                        completed = True
                        outcome = "success"
                        break
                    finally:
                        _ACTIVE_CALL.reset(token)
                    last = chunk
                    if enabled and capture_timing and ttft is None and _has_content(chunk):
                        ttft = max(0, int((time.monotonic() - started) * 1000))
                    # Never leave a guard active while the consumer is executing.
                    yield chunk
            except (asyncio.CancelledError, GeneratorExit):
                outcome = "canceled"
                raise
            except BaseException:  # noqa: BLE001
                outcome = "failed"
                raise
            finally:
                token = _ACTIVE_CALL.set(_owner())
                try:
                    close = getattr(stream, "aclose", None)
                    if not completed and close is not None:
                        try:
                            await close()
                        except BaseException:  # noqa: BLE001
                            # Keep the original failure/cancellation, not a cleanup error.
                            pass
                    if enabled:
                        _safe_record(record, self, method_name,
                                     last if outcome == "success" else None,
                                     kwargs, started, capture_timing, outcome, ttft)
                finally:
                    _ACTIVE_CALL.reset(token)

        if inspect.isasyncgenfunction(original):
            @functools.wraps(original)
            async def agen_wrapper(self, *args, **kwargs):
                enabled = _ACTIVE_CALL.get() != _owner()
                stream = consume_stream(self, original(self, *args, **kwargs), kwargs,
                                        time.monotonic(), enabled)
                try:
                    async for chunk in stream:
                        yield chunk
                finally:
                    await stream.aclose()

            return agen_wrapper

        if inspect.iscoroutinefunction(original):
            @functools.wraps(original)
            async def coro_wrapper(self, *args, **kwargs):
                owner = _owner()
                if _ACTIVE_CALL.get() == owner:
                    return await original(self, *args, **kwargs)
                started = time.monotonic()
                token = _ACTIVE_CALL.set(owner)
                response = None
                outcome = "success"
                try:
                    response = await original(self, *args, **kwargs)
                    return response
                except asyncio.CancelledError:
                    outcome = "canceled"
                    raise
                except BaseException:  # noqa: BLE001
                    outcome = "failed"
                    raise
                finally:
                    try:
                        _safe_record(record, self, method_name, response, kwargs,
                                     started, capture_timing, outcome)
                    finally:
                        _ACTIVE_CALL.reset(token)

            return coro_wrapper

        @functools.wraps(original)
        def plain_wrapper(self, *args, **kwargs):
            owner = _owner()
            if _ACTIVE_CALL.get() == owner:
                return original(self, *args, **kwargs)
            started = time.monotonic()
            token = _ACTIVE_CALL.set(owner)
            try:
                result = original(self, *args, **kwargs)
            except BaseException as error:  # noqa: BLE001
                outcome = "canceled" if isinstance(error, asyncio.CancelledError) else "failed"
                _safe_record(record, self, method_name, None, kwargs,
                             started, capture_timing, outcome)
                raise
            finally:
                _ACTIVE_CALL.reset(token)
            if inspect.isawaitable(result):
                async def wait():
                    token = _ACTIVE_CALL.set(_owner())
                    response = None
                    outcome = "success"
                    try:
                        response = await result
                        return response
                    except asyncio.CancelledError:
                        outcome = "canceled"
                        raise
                    except BaseException:  # noqa: BLE001
                        outcome = "failed"
                        raise
                    finally:
                        try:
                            _safe_record(record, self, method_name, response, kwargs,
                                         started, capture_timing, outcome)
                        finally:
                            _ACTIVE_CALL.reset(token)

                return wait()
            if inspect.isasyncgen(result):
                return consume_stream(self, result, kwargs, started, True)
            _safe_record(record, self, method_name, result, kwargs, started, capture_timing)
            return result

        return plain_wrapper

    return factory

