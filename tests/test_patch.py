"""验证 patch.py 的类收集与还原逻辑——用假类，不需要 AstrBot。

    python tests/test_patch.py
"""

from __future__ import annotations

import asyncio
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


def test_stream_early_close_records_last_chunk() -> None:
    """消费方提前收手不是模型失败，按最后一片照常记。"""
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
    assert calls == ["chunk-1"]


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
    assert asyncio.iscoroutinefunction(wrapped), "包装后还得看得出是协程函数"


def test_install_skips_missing_method() -> None:
    reg = PatchRegistry()
    done = reg.install([(Base, "no_such_method")], make_wrapper(lambda *a: None))
    assert done == []


def main() -> None:
    cases = [
        test_collect_covers_base_and_overrides_only,
        test_install_patches_subclass_not_just_base,
        test_install_is_idempotent,
        test_install_skips_missing_method,
        test_wrapper_calls_sync_method_without_breaking_it,
        test_wrapper_awaits_plain_function_returning_coroutine,
        test_stream_failure_is_recorded_as_failure,
        test_stream_early_close_records_last_chunk,
        test_wrapper_preserves_method_identity,
    ]
    for case in cases:
        case()
        print(f"  ok  {case.__name__}")
    print(f"{len(cases)} 组用例全部通过")


if __name__ == "__main__":
    main()
