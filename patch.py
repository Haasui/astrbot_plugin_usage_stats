"""越界层：给 provider 打补丁，捕获所有模型调用。

**这是整个方案里唯一越界的地方。** 依据与取舍见
../../docs/usage-attribution.md 第 2、3、6 节。

两个必须做对的地方，都是读源码才发现的：

1. **补丁打在类上，不是实例上。**
   `provider/manager.py:812` 每次 `load_provider` 都会 `self.inst_map[id] = inst` 造新实例，
   `:823 reload()` 还会遍历替换。实例级补丁在用户改一次模型之后就失效了。

2. **要打的是每个具体子类，不是基类。**
   `provider.py:100` 的 `Provider.text_chat` 方法体只有一句 `...`（抽象桩），
   真正干活的是 `sources/openai_source.py` 这类子类里覆写的版本——**子类会遮蔽基类**。
   只补基类 = 一个调用都记不到。

本模块不 import AstrBot：目标类由 main.py 查好传进来，这里只做「打/还原」。
"""

from __future__ import annotations

import functools
import inspect
import time
from typing import Any, Callable

MARK = "__usage_stats_patched__"
"""打在包装函数上的标记。还原时靠它确认「当前这个方法还是我打的那个」。"""


def collect_targets(base: type, names: tuple[str, ...] = ("text_chat", "text_chat_stream")) -> list[tuple[type, str]]:
    """找出所有需要打补丁的 (类, 方法名)。

    递归遍历子类，只收「方法定义在**该类自己的 __dict__** 里」的那些——
    继承来的不用重复打。这样既覆盖了覆写基类的具体实现，也不会漏掉真正实现基类方法的类。

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
                # 已经打过就别叠——热重载时最容易出这个
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

        只在「当前值仍然是自己打的那个」时才写回，避免踩掉别人后打的补丁；
        **绝不用 delattr**——那会连基类继承来的方法一起删掉。
        """
        done: list[str] = []
        while self._saved:
            cls, name, original = self._saved.pop()
            current = cls.__dict__.get(name)
            if getattr(current, MARK, False):
                setattr(cls, name, original)
                done.append(f"{cls.__name__}.{name}")
        return done


def _safe_record(record: Callable[..., None], provider: Any, method_name: str,
                response: Any, kwargs: dict, started: float = 0.0) -> None:
    """记账永远不往上抛 —— 它跑在 provider 的调用路径里。"""
    try:
        elapsed = int((time.monotonic() - started) * 1000) if started else 0
        record(provider, method_name, response, kwargs, elapsed)
    except Exception:  # noqa: BLE001
        pass


def make_wrapper(record: Callable[..., None]):
    """造一个包装器工厂。

    record(provider, method_name, response, kwargs) 由调用方提供。
    """

    def factory(original: Any, method_name: str) -> Any:
        if inspect.isasyncgenfunction(original):
            # text_chat_stream 是异步生成器（provider.py:139 返回 AsyncGenerator）。
            # 这里不能 await——await 一个异步生成器直接就是 TypeError。
            @functools.wraps(original)
            async def agen_wrapper(self, *args, **kwargs):
                started = time.monotonic()
                last = None
                raised = False
                try:
                    async for chunk in original(self, *args, **kwargs):
                        last = chunk
                        yield chunk
                except GeneratorExit:
                    # 消费方提前收手，不是模型失败。照常按最后一片记。
                    raise
                except BaseException:  # noqa: BLE001
                    # 流中途断了：这是一次**失败**的调用。
                    # 之前是先收下最后一片、再在 finally 里把它当成功记 ——
                    # 于是流式故障永远不会出现在 failed 一列里。
                    raised = True
                    raise
                finally:
                    if raised:
                        _safe_record(record, self, method_name, None, kwargs, started)
                    elif last is not None:
                        _safe_record(record, self, method_name, last, kwargs, started)

            return agen_wrapper

        if inspect.iscoroutinefunction(original):
            @functools.wraps(original)
            async def coro_wrapper(self, *args, **kwargs):
                started = time.monotonic()
                response = await original(self, *args, **kwargs)
                _safe_record(record, self, method_name, response, kwargs, started)
                return response

            return coro_wrapper

        # 剩下的两种：同步方法，或者被装饰器包成了普通函数的 async def
        # （后者返回的还是可等待对象）。**不能直接 await 前者** —— 那会抛
        # TypeError，等于记账把别人的调用搞挂了，而这里只该记账。
        @functools.wraps(original)
        def plain_wrapper(self, *args, **kwargs):
            started = time.monotonic()
            result = original(self, *args, **kwargs)
            if inspect.isawaitable(result):
                async def wait():
                    value = await result
                    _safe_record(record, self, method_name, value, kwargs, started)
                    return value

                return wait()
            _safe_record(record, self, method_name, result, kwargs, started)
            return result

        return plain_wrapper

    return factory

