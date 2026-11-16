"""验证 patch.py 的类收集与还原逻辑——用假类，不需要 AstrBot。

    python tests/test_patch.py
"""

from __future__ import annotations

import asyncio
import inspect
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from astrbot_plugin_usage_stats.patch import (  # noqa: E402
    MARK,
    PatchRegistry,
    collect_targets,
    make_wrapper,
)


class Base:
    async def text_chat(self, **kwargs):
        return "base"

    async def text_chat_stream(self, **kwargs):
        if False:
            yield None


class Child(Base):
    async def text_chat(self, **kwargs):     # 覆写：真正干活的那种
        return "child"

    async def only_here(self, **kwargs):
        return "x"


class GrandChild(Child):
    pass                                      # 不覆写，不该被单独打补丁


def test_collect_covers_base_and_overrides_only() -> None:
    targets = collect_targets(Base)
    pairs = {(c.__name__, n) for c, n in targets}
    assert ("Base", "text_chat") in pairs, "基类自己定义了就要收"
    assert ("Child", "text_chat") in pairs, "覆写基类的子类必须收——否则一个调用都记不到"
    assert ("GrandChild", "text_chat") not in pairs, "继承来的不该重复打"
    assert ("Child", "text_chat_stream") not in pairs, "没覆写就不该收"


def test_install_patches_subclass_not_just_base() -> None:
    reg = PatchRegistry()
    seen: list[str] = []

    def record(provider, method, response, kwargs, elapsed_ms=0):
        seen.append(f"{type(provider).__name__}.{method}")

    done = reg.install(collect_targets(Base), make_wrapper(record))
    assert any(d.startswith("Child.") for d in done), done
    assert reg.installed == len(done)
    assert getattr(Child.__dict__["text_chat"], MARK, False) is True
    reg.restore()
    assert reg.installed == 0
    assert getattr(Child.__dict__["text_chat"], MARK, False) is False
    assert Base.text_chat.__qualname__.endswith("Base.text_chat")


def test_install_is_idempotent() -> None:
    reg = PatchRegistry()
    reg.install(collect_targets(Base), make_wrapper(lambda *a: None))
    before = getattr(Child.__dict__["text_chat"], MARK, False)
    again = PatchRegistry().install(collect_targets(Base), make_wrapper(lambda *a: None))
    assert again == [], "已经打过就不该再叠一层"
    assert before is True
    reg.restore()


def test_wrapper_calls_sync_method_without_breaking_it() -> None:
    """同步方法不能因为记账被搞挂。

    非 async 的方法直接 await 会抛 TypeError —— 那是**我们把别人的调用弄挂了**，
    而这里只该记账。
    """
    class Holder:
        def text_chat(self, **kwargs):
            return "sync-result"

    seen = []
    wrapped = make_wrapper(lambda p, m, r, k, ms=0: seen.append((m, r)))(
        Holder.__dict__["text_chat"], "text_chat"
    )
    assert wrapped(Holder()) == "sync-result", "原样返回"
    assert seen == [("text_chat", "sync-result")]


def test_wrapper_awaits_plain_function_returning_coroutine() -> None:
    """被装饰器包成普通函数的 async def：结果还是可等待的，要等完再记。"""
    class Holder:
        def text_chat(self, **kwargs):
            async def inner():
                return "late"

            return inner()

    seen = []
    wrapped = make_wrapper(lambda p, m, r, k, ms=0: seen.append(r))(
        Holder.__dict__["text_chat"], "text_chat"
    )
    assert asyncio.run(wrapped(Holder())) == "late"
    assert seen == ["late"], "要记等完之后的返回值，不是协程对象"


def test_stream_failure_is_recorded_as_failure() -> None:
    """流中途断了要记成一次失败。

    之前的 finally 是「收到过片就拿最后一片记」—— 于是流式故障被记成成功，
    failed 一列永远看不到它。
    """
    calls = []

    class Holder:
        async def text_chat_stream(self, **kwargs):
            yield "chunk-1"
            raise RuntimeError("断了")

    wrapped = make_wrapper(lambda p, m, r, k, ms=0: calls.append(r))(
        Holder.__dict__["text_chat_stream"], "text_chat_stream"
    )

    async def consume():
        got = []
        try:
            async for chunk in wrapped(Holder()):
                got.append(chunk)
        except RuntimeError:
            pass
        return got

    assert asyncio.run(consume()) == ["chunk-1"], "故障前收到的片照常给出去"
    assert calls == [None], "记成失败（response=None），不是拿最后一片当成功"


def test_stream_early_close_records_cancellation() -> None:
    """消费方提前收手记取消，旧五参接口也不能伪装成成功。"""
    calls = []

    class Holder:
        async def text_chat_stream(self, **kwargs):
            yield "chunk-1"
            yield "chunk-2"

    wrapped = make_wrapper(lambda p, m, r, k, ms=0: calls.append(r))(
        Holder.__dict__["text_chat_stream"], "text_chat_stream"
    )

    async def consume_one():
        agen = wrapped(Holder())
        first = await agen.__anext__()
        await agen.aclose()
        return first

    assert asyncio.run(consume_one()) == "chunk-1"
    assert calls == [None]


def test_wrapper_preserves_method_identity() -> None:
    """要带 functools.wraps —— 名字和 __wrapped__ 丢了，靠内省找方法的代码就找不到。"""
    class Holder:
        async def text_chat(self, **kwargs):
            """文档"""

    original = Holder.__dict__["text_chat"]
    wrapped = make_wrapper(lambda *a: None)(original, "text_chat")
    assert wrapped.__name__ == "text_chat"
    assert wrapped.__doc__ == original.__doc__
    assert wrapped.__wrapped__ is original
    assert inspect.iscoroutinefunction(wrapped), "包装后还得看得出是协程函数"


def test_install_skips_missing_method() -> None:
    reg = PatchRegistry()
    done = reg.install([(Base, "no_such_method")], make_wrapper(lambda *a: None))
    assert done == []


def test_all_nonstream_failures_record_once_and_preserve_exception() -> None:
    error = RuntimeError("private error text")

    class Holder:
        def sync(self):
            raise error

        async def coro(self):
            raise error

        def awaitable(self):
            return self.coro()

    for name in ("sync", "coro", "awaitable"):
        calls = []
        wrapped = make_wrapper(lambda *a: calls.append(a))(getattr(Holder, name), name)
        try:
            value = wrapped(Holder())
            if name != "sync":
                asyncio.run(value)
        except RuntimeError as caught:
            assert caught is error
        else:
            raise AssertionError("original exception was swallowed")
        assert len(calls) == 1 and len(calls[0]) == 5 and calls[0][2] is None


def test_stream_timing_empty_and_cancel_metadata() -> None:
    from types import SimpleNamespace
    from unittest.mock import patch

    class Holder:
        async def content(self):
            yield SimpleNamespace(completion_text="", usage={"output": 0})
            yield SimpleNamespace(completion_text="token")
            yield SimpleNamespace(completion_text="", usage={"output": 1})

        async def empty(self):
            if False:
                yield None

        async def canceled(self):
            raise asyncio.CancelledError()

    async def consume(stream):
        return [chunk async for chunk in stream]

    calls = []
    factory = make_wrapper(lambda *a: calls.append(a), capture_timing=True)
    ticks = iter([0.0, 0.025, 0.1])
    with patch("astrbot_plugin_usage_stats.patch.time", SimpleNamespace(monotonic=lambda: next(ticks))):
        assert len(asyncio.run(consume(factory(Holder.content, "content")(Holder())))) == 3
    assert calls[-1][4] == 100 and calls[-1][5] == {"result": "success", "ttft_ms": 25}
    asyncio.run(consume(factory(Holder.empty, "empty")(Holder())))
    assert calls[-1][2] is None and calls[-1][5] == {"result": "success", "ttft_ms": None}
    try:
        asyncio.run(factory(Holder.canceled, "canceled")(Holder()))
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("cancellation swallowed")
    assert calls[-1][2] is None and calls[-1][5]["result"] == "canceled"


def test_nested_super_records_once_for_all_paths() -> None:
    class Parent:
        def sync(self):
            return "sync"

        async def coro(self):
            return "async"

        def awaitable(self):
            return self.coro()

        async def stream(self):
            yield "stream"

    class Child(Parent):
        def sync(self):
            return super().sync()

        async def coro(self):
            return await super().coro()

        def awaitable(self):
            return super().awaitable()

        async def stream(self):
            async for item in super().stream():
                yield item

    calls = []
    registry = PatchRegistry()
    registry.install(collect_targets(Parent, ("sync", "coro", "awaitable", "stream")),
                     make_wrapper(lambda *a: calls.append(a)))

    async def consume():
        assert await Child().coro() == "async"
        assert await Child().awaitable() == "async"
        assert [x async for x in Child().stream()] == ["stream"]

    try:
        assert Child().sync() == "sync"
        asyncio.run(consume())
        assert [call[1] for call in calls] == ["sync", "coro", "awaitable", "stream"]
    finally:
        registry.restore()


def test_independent_tasks_and_calls_between_chunks_are_not_suppressed() -> None:
    calls = []

    class Holder:
        async def coro(self, spawn=False):
            if spawn:
                await asyncio.create_task(self.coro())
            return "ok"

        async def stream(self):
            yield "chunk"
            yield "chunk"

    registry = PatchRegistry()
    registry.install(collect_targets(Holder, ("coro", "stream")), make_wrapper(lambda *a: calls.append(a)))

    async def run():
        await Holder().coro(spawn=True)
        stream = Holder().stream()
        await anext(stream)
        await Holder().coro()
        await stream.aclose()

    try:
        asyncio.run(run())
        assert len(calls) == 4
    finally:
        registry.restore()


def test_record_failures_never_replace_provider_result_or_error() -> None:
    def broken_record(*args):
        raise ValueError("accounting error")

    error = RuntimeError("provider error")

    async def fails(self):
        raise error

    def succeeds(self):
        return "ok"

    factory = make_wrapper(broken_record, capture_timing=True)
    assert factory(succeeds, "sync")(object()) == "ok"
    try:
        asyncio.run(factory(fails, "coro")(object()))
    except RuntimeError as caught:
        assert caught is error
    else:
        raise AssertionError("provider error swallowed")


def test_stream_usage_only_has_no_ttft_and_early_close_is_canceled() -> None:
    from types import SimpleNamespace

    class Holder:
        async def stream(self):
            yield SimpleNamespace(completion_text="", raw_completion={"usage": {"output_tokens": 1}})
            yield SimpleNamespace(completion_text="")

    calls = []
    wrapped = make_wrapper(lambda *a: calls.append(a), capture_timing=True)(Holder.stream, "stream")

    async def run():
        assert len([x async for x in wrapped(Holder())]) == 2
        stream = wrapped(Holder())
        await anext(stream)
        await stream.aclose()

    asyncio.run(run())
    assert len(calls) == 2
    assert calls[0][5] == {"result": "success", "ttft_ms": None}
    assert calls[1][2] is None and calls[1][5] == {"result": "canceled", "ttft_ms": None}


def test_task_cancellation_of_stream_preserves_cancellation() -> None:
    calls = []
    entered = None

    class Holder:
        async def stream(self):
            yield "token"
            entered.set()
            await asyncio.Future()

    wrapped = make_wrapper(lambda *a: calls.append(a), capture_timing=True)(Holder.stream, "stream")

    async def run():
        nonlocal entered
        entered = asyncio.Event()

        async def consume():
            return [x async for x in wrapped(Holder())]

        task = asyncio.create_task(consume())
        await entered.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("stream cancellation swallowed")

    asyncio.run(run())
    assert len(calls) == 1 and calls[0][2] is None
    assert calls[0][5]["result"] == "canceled" and calls[0][5]["ttft_ms"] is not None


def test_nested_stream_across_consumer_tasks_records_once() -> None:
    calls = []

    class Parent:
        async def stream(self):
            yield "a"
            yield "b"

        async def helper(self):
            return "helper"

    class Child(Parent):
        async def stream(self):
            async for x in super().stream():
                await self.helper()
                yield x

    registry = PatchRegistry()
    registry.install(collect_targets(Parent, ("stream", "helper")), make_wrapper(lambda *a: calls.append(a)))

    async def run():
        stream = Child().stream()
        assert await asyncio.create_task(anext(stream)) == "a"
        assert await asyncio.create_task(anext(stream)) == "b"
        try:
            await asyncio.create_task(anext(stream))
        except StopAsyncIteration:
            pass

    try:
        asyncio.run(run())
        assert len(calls) == 1 and calls[0][1] == "stream"
    finally:
        registry.restore()


def test_failure_and_cancellation_metadata_cover_plain_awaitable() -> None:
    for error, expected in ((RuntimeError("secret"), "failed"), (asyncio.CancelledError(), "canceled")):
        calls = []

        def original(self):
            async def wait():
                raise error
            return wait()

        wrapped = make_wrapper(lambda *a: calls.append(a), capture_timing=True)(original, "call")
        try:
            asyncio.run(wrapped(object()))
        except BaseException as caught:
            assert caught is error
        else:
            raise AssertionError("original exception swallowed")
        assert len(calls) == 1 and calls[0][2] is None
        assert calls[0][5] == {"result": expected, "ttft_ms": None}


def main() -> None:
    cases = [
        test_stream_usage_only_has_no_ttft_and_early_close_is_canceled,
        test_task_cancellation_of_stream_preserves_cancellation,
        test_nested_stream_across_consumer_tasks_records_once,
        test_failure_and_cancellation_metadata_cover_plain_awaitable,
        test_all_nonstream_failures_record_once_and_preserve_exception,
        test_stream_timing_empty_and_cancel_metadata,
        test_nested_super_records_once_for_all_paths,
        test_independent_tasks_and_calls_between_chunks_are_not_suppressed,
        test_record_failures_never_replace_provider_result_or_error,
        test_collect_covers_base_and_overrides_only,
        test_install_patches_subclass_not_just_base,
        test_install_is_idempotent,
        test_install_skips_missing_method,
        test_wrapper_calls_sync_method_without_breaking_it,
        test_wrapper_awaits_plain_function_returning_coroutine,
        test_stream_failure_is_recorded_as_failure,
        test_stream_early_close_records_cancellation,
        test_wrapper_preserves_method_identity,
    ]
    for case in cases:
        case()
        print(f"  ok  {case.__name__}")
    print(f"{len(cases)} 组用例全部通过")


if __name__ == "__main__":
    main()
