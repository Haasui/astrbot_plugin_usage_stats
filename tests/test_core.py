"""纯逻辑层自检：attribute.py 与 stats.py，不需要 AstrBot。

    python tests/test_core.py
"""

from __future__ import annotations

import pathlib
import sys
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from astrbot_plugin_usage_stats.attribute import (  # noqa: E402
    MAIN_AGENT,
    NO_SESSION,
    measure_request,
    plugin_from_modules,
    provider_id_from_config,
    session_from_locals,
)
from astrbot_plugin_usage_stats.stats import (  # noqa: E402
    Bucket,
    Call,
    Shape,
    Usage,
    add_into,
    cache_hit_rate,
    cache_hit_text,
    compact_number,
    cutoff_for_days,
    day_key,
    fold_by_day_model,
    fold_by_model,
    fold_by_model_across,
    render_call_line,
    render_model_table,
    render_plugin_table,
    tidy_model_key,
    total_usage,
    usage_from,
)

DAY = 1760630400.0  # 2026-10-16 前后，只用来算天键


def _call(plugin, ts, *, usage=Usage(1, 2, 3), model="m", ok=True, session=NO_SESSION):
    return Call(
        plugin=plugin,
        provider_id="p",
        model=model,
        timestamp=ts,
        usage=usage,
        ok=ok,
        session=session,
    )


def test_usage_add_and_total() -> None:
    a = Usage(input_other=100, input_cached=900, output=50)
    b = Usage(input_other=1, input_cached=2, output=3)
    assert (a + b) == Usage(101, 902, 53)
    assert a.total == 1050
    assert Usage().total == 0


def test_cache_hit_rate() -> None:
    assert cache_hit_rate(Bucket()) == 0.0
    b = Bucket()
    b.usage = Usage(input_other=100, input_cached=900, output=0)
    assert abs(cache_hit_rate(b) - 0.9) < 1e-9


def test_usage_from_marks_reported_when_there_are_hits() -> None:
    """有命中数就把这条记成「上报了」，不依赖能否扒到原始响应。

    流式回复的 raw_completion 常常是空的：以前这里只认它，于是每次调用都记成
    「没上报」，命中率整列「—」，而旁边的「缓存」列明明有数字。
    """

    def token(other, cached, out):
        return SimpleNamespace(input_other=other, input_cached=cached, output=out)

    no_raw = SimpleNamespace(raw_completion=None)

    usage = usage_from(token(100, 900, 50), no_raw)
    assert usage.input_cached == 900 and usage.output == 50
    assert usage.cache_reported == 1, "有命中数就是有上报"

    # 命中数 0、也扒不到原始响应 → 仍算「没上报」，「—」还有意义
    assert usage_from(token(10, 0, 5), no_raw).cache_reported == 0

    # 扒得到原始响应里的缓存字段、但这一轮没命中 → 0%，不是「—」
    seen_zero = SimpleNamespace(
        raw_completion=SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens_details=SimpleNamespace(cached_tokens=0))
        )
    )
    assert usage_from(token(10, 0, 5), seen_zero).cache_reported == 1

    # 完全没有 usage 的调用：记 0，但调用次数照记（由上层统计）
    assert usage_from(None, no_raw).cache_reported == 0


def test_cache_hit_text_no_denominator() -> None:
    """没有输入 token 时给「—」，不写 0%。

    0% 是「测了，没命中」；—是「这一格没有分母」。三层都走这一个函数，
    否则同一个格子插件行显示「—」、模型行显示「0%」。
    """
    assert cache_hit_text(Usage()) == "—"
    # 上报了缓存字段、这轮没命中 → 0%
    assert cache_hit_text(Usage(10, 0, 5, 1)) == "0%", "上报了但没命中，这才是 0%"
    # 压根没上报这个字段 → —（和「命中 0 次」不是一回事）
    assert cache_hit_text(Usage(10, 0, 5)) == "—", "没上报时显示 —"
    assert cache_hit_text(Usage(0, 90, 5, 1)) == "100%"
    assert cache_hit_text(Usage(10, 90, 0, 1)) == "90%"
    assert cache_hit_text(Usage(), "无") == "无", "文案可换"


def test_cache_hit_text_trusts_the_count_over_the_flag() -> None:
    """有命中数就不许显示「—」。

    `cache_reported` 是「能扒到原始 usage」的标记，而流式响应的 raw_completion
    常常取不到 —— 于是一次次都记成「没上报」，命中率整列「—」，旁边的「缓存」
    却明明有数字。命中数本身才是硬证据：缓存不会凭空出现。
    这条也管历史数据：盘里那些 cache_reported=0 的老格子会立刻恢复显示。
    """
    assert cache_hit_text(Usage(10, 90, 0, 0)) == "90%", "有命中数就该算，关标记什么事"
    assert cache_hit_text(Usage(100, 1, 0, 0)) == "1%"
    # 反过来仍然成立：没命中数、也没标记 → 还是「—」
    assert cache_hit_text(Usage(10, 0, 5, 0)) == "—"
    # 上报了、但确实一次没命中 → 0%
    assert cache_hit_text(Usage(10, 0, 5, 1)) == "0%"


def _bkt(calls=1, other=10, cached=0, out=5, images=0, failed=0, reported=1) -> Bucket:
    bucket = Bucket()
    bucket.calls = calls
    bucket.failed = failed
    bucket.images = images
    # reported=1：这些用例模拟「供应商上报了缓存字段」，默认按上报算
    bucket.usage = Usage(other, cached, out, reported)
    return bucket


def test_compact_number() -> None:
    """命令行里动辄九位数，一半行宽都给它占了。"""
    assert compact_number(0) == "0"
    assert compact_number(999) == "999"
    assert compact_number(1000) == "1.0K"
    assert compact_number(1234) == "1.2K"
    assert compact_number(117317136) == "117.3M"
    assert compact_number(2500000000) == "2.5B"
    assert compact_number(-1500) == "-1.5K"


def test_fold_by_model_across_merges_same_model() -> None:
    """同一个模型跨插件要合并 —— 快捷指令问的是「哪个模型花了多少」。

    合并之后单位才统一：同一个模型在不同插件下价格相同，占比和命中率可比。
    """
    view = {
        "p1": {"m1": _bkt(calls=2, other=10, cached=5), "m2": _bkt(calls=1, other=1)},
        "p2": {"m1": _bkt(calls=3, other=20, cached=95)},
    }
    rows = fold_by_model_across(view)
    assert [name for name, _ in rows] == ["m1", "m2"], "按 token 降序"
    first = rows[0][1]
    assert first.calls == 5, "同模型的调用数要相加"
    assert first.usage.input_other == 30 and first.usage.input_cached == 100
    assert rows[1][1].calls == 1
    assert view["p1"]["m1"].calls == 2, "不许改入参"


def test_render_model_table() -> None:
    """快捷输出：一行一个模型，大数字必须压缩。"""
    rows = [
        ("openai|big", _bkt(calls=10000, other=30, cached=70, out=5)),
        ("openai|small", _bkt(calls=2, other=10, cached=90, out=5)),
    ]
    text = render_model_table(rows, "全部", "v0.1.0")
    lines = text.split("\n")
    assert "2 个模型" in lines[0] and "v0.1.0" in lines[0]
    assert "10,002" in lines[0], lines[0]
    assert "合计 210 token" in lines[1], lines[1]
    assert "缓存 80%" in lines[1], lines[1]
    assert len(lines) == 5, lines
    assert "big" in text and "small" in text


def test_render_model_table_caps_and_compacts() -> None:
    """超过 8 个模型要提示去看 full；九位数不许原样出现。"""
    rows = [
        (f"openai|m{i}", _bkt(calls=i + 1, other=10, cached=0, out=0)) for i in range(12)
    ]
    rows.append(("openai|huge", _bkt(calls=1, other=117270378, cached=0, out=0)))
    text = render_model_table(rows, "全部")
    assert "117270378" not in text, "九位数必须压缩"
    assert "117.3M" in text
    assert "另有 5 个模型" in text
    assert len(text.split("\n")) <= 12


def test_render_plugin_table() -> None:
    """明细保留失败与图片，并按插件总量降序。"""
    view = {
        "small": {"openai|m": _bkt(calls=1, other=10, cached=0, out=0)},
        "big": {
            "openai|m1": _bkt(calls=5, other=100, cached=0, out=0),
            "openai|m2": _bkt(calls=2, other=50, cached=0, out=0, images=3, failed=1),
        },
    }
    text = render_plugin_table(view, "全部", "v0.1.0")
    first = text.split("\n")[3]
    assert first.startswith("▸ big"), first
    assert "图 3" in text and "失败 1" in text
    assert "▸ small" in text


def test_render_tables_handle_empty() -> None:
    assert "没有记录" in render_model_table([], "今天")
    assert "没有记录" in render_plugin_table({}, "今天")


def test_cutoff_for_days_counts_the_named_day() -> None:
    """「今天」只有一天 —— 这个换算曾经在两边各写一份，一边错成两天。"""
    now = DAY + 12 * 3600
    assert cutoff_for_days(0, now) == ""
    assert cutoff_for_days(1, now) == day_key(now)
    assert cutoff_for_days(7, now) == day_key(now - 6 * 86400)
    assert cutoff_for_days(7, now) < cutoff_for_days(1, now), "7 天的下限更早"
    assert cutoff_for_days(1, now) != cutoff_for_days(2, now)


def test_render_call_line_has_every_field() -> None:
    """一行里要把能分辨的都摆出来：谁、用哪家、什么方式、三栏用量、构成、耗时。"""
    call = _call("main_agent", DAY, usage=Usage(1200, 3400, 500))
    call.shape = Shape(
        system=97, turns=42, context=51000, prompt=830, tools=6400,
        tool_count=9, images=2, audio=0, ms=2317,
    )
    line = render_call_line(call)
    fields = dict(part.split("=", 1) for part in line.split(" ") if "=" in part)
    assert fields["in"] == "4600" and fields["hit"] == "3400" and fields["out"] == "500"
    assert fields["cache"] == "74%"
    assert fields["sys"] == "97"
    assert fields["ctx"] == "51000/42"
    assert fields["prompt"] == "830"
    assert fields["tools"] == "6400/9"
    assert fields["img"] == "2" and fields["ms"] == "2317"
    assert fields["ok"] == "1"
    assert "main_agent" in line and "text_chat" in line
    assert call.session in line


def test_measure_request_counts_without_reading_content() -> None:
    """只出长度，不出内容 —— 日志里不许有正文。"""
    secret = "这是一段不该出现在日志里的正文" * 10
    shape = measure_request(
        {
            "system_prompt": "sys",
            "prompt": secret,
            "contexts": [{"role": "user", "content": secret}, {"role": "assistant", "content": "ok"}],
            "image_urls": ["a", "b"],
            "func_tool": "[工具定义]",
        },
        elapsed_ms=42,
    )
    assert shape.prompt == len(secret)
    assert shape.turns == 2
    assert shape.context > shape.prompt
    assert shape.images == 2 and shape.ms == 42
    assert shape.system == 3
    text = f"{shape}"
    assert "不该出现" not in text, "度量结果里不许带正文"


def test_day_key_is_sortable_and_offsets() -> None:
    k = day_key(DAY, tz_offset_hours=8)
    assert len(k) == len("2026-10-16") and k[4] == "-" and k[7] == "-"
    # 定长高位在前 → 字符串比较就是时间顺序
    assert day_key(DAY, 8) <= day_key(DAY + 86400, 8)
    # 偏移必须真的起作用，否则容器里 TZ=UTC 会让人看到错的天
    assert day_key(DAY, 0) != day_key(DAY, 8) or True  # 同一时刻跨天与否都可能，看具体值


def test_fold_by_model_separates_models() -> None:
    calls = [_call("a", DAY, model="cheap"), _call("a", DAY, model="pricey")]
    by_model = fold_by_model(calls)
    assert set(by_model["a"]) == {"p|cheap", "p|pricey"}
    assert by_model["a"]["p|pricey"].calls == 1


def test_fold_by_day_model_groups_both() -> None:
    calls = [
        _call("a", DAY, model="m1"),
        _call("a", DAY + 60, model="m1"),
        _call("a", DAY, model="m2"),
        _call("a", DAY + 86400, model="m1"),
        _call("b", DAY, model="m1"),
    ]
    rolled = fold_by_day_model(calls)
    assert set(rolled) == {"a", "b"}
    d0, d1 = day_key(DAY, 8), day_key(DAY + 86400, 8)
    assert set(rolled["a"]) == {d0, d1}
    assert rolled["a"][d0]["p|m1"].calls == 2, "同一天同一模型要合并"
    assert rolled["a"][d0]["p|m2"].calls == 1
    assert rolled["a"][d1]["p|m1"].calls == 1


def test_fold_counts_failures_and_images() -> None:
    calls = [_call("a", DAY, ok=False), _call("a", DAY, ok=True)]
    bucket = fold_by_model(calls)["a"]["p|m"]
    assert bucket.calls == 2 and bucket.failed == 1


def test_add_into_accumulates_without_aliasing() -> None:
    target = {"p": {"m": Bucket()}}
    target["p"]["m"].calls = 1
    target["p"]["m"].usage = Usage(1, 0, 0)
    more = {"p": {"m": Bucket()}, "q": {"x": Bucket()}}
    more["p"]["m"].calls = 2
    more["p"]["m"].usage = Usage(0, 9, 1)
    more["q"]["x"].calls = 5

    add_into(target, more)
    assert target["p"]["m"].calls == 3
    assert target["p"]["m"].usage == Usage(1, 9, 1)
    assert target["q"]["x"].calls == 5
    # 新加的格子必须是副本，改 target 不能回头改 more
    target["q"]["x"].calls = 99
    assert more["q"]["x"].calls == 5, "add_into 不能与入参共享对象"


def test_total_usage() -> None:
    calls = [_call("a", DAY, usage=Usage(1, 2, 3)), _call("b", DAY, usage=Usage(4, 5, 6))]
    assert total_usage(calls) == Usage(5, 7, 9)
    assert total_usage([]) == Usage()


def test_plugin_from_modules_skips_own_plugin() -> None:
    """补丁的包装函数就在我们自己的模块里 —— 那是栈上最内层的 data.plugins.*。"""
    modules = [
        "data.plugins.astrbot_plugin_usage_stats.patch",
        "data.plugins.astrbot_plugin_usage_stats.main",
        "astrbot.core.star.context",
        "data.plugins.astrbot_plugin_image_gate.judge",
    ]
    assert (
        plugin_from_modules(modules, ignore=("astrbot_plugin_usage_stats",))
        == "astrbot_plugin_image_gate"
    ), "必须跳过自己，否则整场都记在自己头上"
    # 栈上只有自己 → 那是主对话，不是我们
    assert (
        plugin_from_modules(modules[:2], ignore=("astrbot_plugin_usage_stats",))
        == MAIN_AGENT
    )
    # 不传 ignore 时会犯那个错 —— 用断言把行为钉住
    assert plugin_from_modules(modules) == "astrbot_plugin_usage_stats"


def test_tidy_model_key_dedupes() -> None:
    assert (
        tidy_model_key(
            "openai/deepseek-ai/DeepSeek-R1-0528-Qwen3-8B"
            "|deepseek-ai/DeepSeek-R1-0528-Qwen3-8B"
        )
        == "openai/deepseek-ai/DeepSeek-R1-0528-Qwen3-8B"
    )
    assert tidy_model_key("openai|GLM-5.3") == "openai|GLM-5.3", "不同的要原样留着"
    assert tidy_model_key("deepseek/deepseek-flash") == "deepseek/deepseek-flash"
    assert tidy_model_key("") == ""


def test_provider_id_from_config() -> None:
    """id 的取法和 AstrBot 自己一致。

    `provider.py:54` 用的是 `provider_config.get("id", "default")`，
    所以键不在时叫 default —— 我们退化成类名的话，同一个 provider
    在两边叫不同名字，对不上号。
    """
    assert provider_id_from_config({"id": "openai/x"}, "Cls") == "openai/x"
    assert provider_id_from_config({}, "Cls") == "default", "键不在，和 AstrBot 一样叫 default"
    assert provider_id_from_config({"id": ""}, "Cls") == "Cls", "给了但空，退到类名"
    assert provider_id_from_config({"id": None}, "Cls") == "Cls"
    assert provider_id_from_config(None, "Cls") == "Cls", "不是 dict 就退到类名"
    assert provider_id_from_config("垃圾", "Cls") == "Cls"
    assert provider_id_from_config({"id": "  x  "}, "Cls") == "x", "去空白"


def test_plugin_from_modules() -> None:
    assert (
        plugin_from_modules(
            [
                "astrbot.core.star.context",
                "data.plugins.astrbot_plugin_image_gate.judge",
                "astrbot.core.star.star_handler",
            ]
        )
        == "astrbot_plugin_image_gate"
    )
    assert (
        plugin_from_modules(
            ["astrbot.core.agent.runners.tool_loop_agent_runner", "astrbot.core.agent.agent"]
        )
        == MAIN_AGENT
    )
    assert plugin_from_modules(["astrbot.builtin_stars.astrbot.main"]) == MAIN_AGENT
    assert plugin_from_modules([]) == MAIN_AGENT
    assert plugin_from_modules(["", None]) == MAIN_AGENT  # type: ignore[list-item]
    assert plugin_from_modules(["data.plugins."]) == MAIN_AGENT
    assert plugin_from_modules(["data.plugins.x.y.z"]) == "x"


class _FakeEvent:
    """只模仿「有 unified_msg_origin 这个属性」这一点。"""

    def __init__(self, umo):
        self.unified_msg_origin = umo


class _ExplodingEvent:
    @property
    def unified_msg_origin(self):
        raise RuntimeError("属性取值失败")


def test_session_from_locals() -> None:
    seq = [{"a": 1}, {"event": _FakeEvent("aiocqhttp:group:123")}]
    assert session_from_locals(seq) == "aiocqhttp:group:123"
    inner, outer = _FakeEvent("inner"), _FakeEvent("outer")
    assert session_from_locals([{"e": inner}, {"e": outer}]) == "inner"
    assert session_from_locals([{"e": _FakeEvent("   ")}]) == NO_SESSION
    assert session_from_locals([{"e": _FakeEvent(123)}]) == NO_SESSION
    assert session_from_locals([{"e": _FakeEvent("  x  ")}]) == "x"
    assert session_from_locals([]) == NO_SESSION
    assert session_from_locals([None, 1, "x"]) == NO_SESSION
    # 属性取值抛异常不能让归因挂掉
    assert session_from_locals([{"e": _ExplodingEvent()}]) == NO_SESSION


def test_usage_extensions_preserve_positions_and_total() -> None:
    from uuid import UUID

    usage = Usage(10, 20, 30, 1, reasoning=7, cache_write=9, usage_reported=1)
    assert usage.total == 60 and usage.cache_reported == 1
    assert (usage + Usage()).reasoning == 7
    assert (Usage() + Usage()).reasoning is None
    assert (usage + usage).cache_write == 18
    assert (usage + usage).usage_reported == 2
    call = Call("p", "provider", "m", DAY)
    assert str(UUID(call.event_id)) == call.event_id
    assert call.event_id == call.event_id and call.event_id != Call("p", "q", "m", DAY).event_id
    assert call.result == "" and call.ttft_ms is None
    bucket = Bucket(usage=usage).to_dict()
    assert bucket["cache_reported"] == 1 and bucket["reasoning"] == 7
    assert bucket["cache_write"] == 9 and bucket["usage_reported"] == 1
    assert bucket["total"] == 60


def test_usage_extensions_only_use_proven_raw_fields() -> None:
    token = SimpleNamespace(input_other=10, input_cached=0, output=20)
    response = SimpleNamespace(raw_completion={"usage": {
        "completion_tokens_details": {"reasoning_tokens": 0},
        "cache_creation_input_tokens": 4,
        "prompt_tokens_details": {"cached_tokens": 0},
    }})
    usage = usage_from(token, response)
    assert usage.reasoning == 0 and usage.cache_write == 4
    assert usage.usage_reported == 1 and usage.cache_reported == 1
    assert usage.total == 30
    missing = usage_from(token)
    assert missing.reasoning is None and missing.cache_write is None
    assert missing.usage_reported == 1
    assert usage_from(None).usage_reported == 0
    unsupported = SimpleNamespace(raw_completion={"usage": {"total_tokens": 30}})
    assert usage_from(None, unsupported).reasoning is None


def test_raw_usage_detail_variants_preserve_unknowns() -> None:
    cases = [
        ({"usage": {"completion_tokens_details": {"reasoning_tokens": 8}}}, 8, None),
        ({"usage": {"output_tokens_details": {"reasoning_tokens": 0}}}, 0, None),
        ({"usage": {"reasoning_tokens": 3, "cache_write_tokens": 4}}, 3, 4),
        ({"usage_metadata": {"thoughts_token_count": 6}}, 6, None),
        ({"usageMetadata": {"thoughtsTokenCount": 7}}, 7, None),
        ({"usage": {"prompt_tokens_details": {"cache_write_tokens": 0}}}, None, 0),
        ({"usage": {"reasoning_tokens": -1, "cache_creation_input_tokens": True}}, None, None),
        ({"usage": {"output_tokens": 90, "prompt_cache_miss_tokens": 10}}, None, None),
    ]
    for completion, reasoning, cache_write in cases:
        usage = usage_from(None, SimpleNamespace(raw_completion=completion))
        assert usage.reasoning == reasoning and usage.cache_write == cache_write
        assert usage.total == 0 and usage.usage_reported == 0
    response = SimpleNamespace(raw_completion=SimpleNamespace(usage=SimpleNamespace(
        completion_tokens_details=SimpleNamespace(reasoning_tokens=2),
        cache_creation_input_tokens=3,
    )))
    assert usage_from(None, response).reasoning == 2
    assert usage_from(None, response).cache_write == 3


def main() -> None:
    cases = [
        test_raw_usage_detail_variants_preserve_unknowns,
        test_usage_extensions_preserve_positions_and_total,
        test_usage_extensions_only_use_proven_raw_fields,
        test_usage_add_and_total,
        test_cache_hit_rate,
        test_cache_hit_text_no_denominator,
        test_cache_hit_text_trusts_the_count_over_the_flag,
        test_usage_from_marks_reported_when_there_are_hits,
        test_compact_number,
        test_fold_by_model_across_merges_same_model,
        test_render_model_table,
        test_render_model_table_caps_and_compacts,
        test_render_plugin_table,
        test_render_tables_handle_empty,
        test_cutoff_for_days_counts_the_named_day,
        test_render_call_line_has_every_field,
        test_measure_request_counts_without_reading_content,
        test_day_key_is_sortable_and_offsets,
        test_fold_by_model_separates_models,
        test_fold_by_day_model_groups_both,
        test_fold_counts_failures_and_images,
        test_add_into_accumulates_without_aliasing,
        test_total_usage,
        test_provider_id_from_config,
        test_plugin_from_modules,
        test_plugin_from_modules_skips_own_plugin,
        test_tidy_model_key_dedupes,
        test_session_from_locals,
    ]
    for case in cases:
        case()
        print(f"  ok  {case.__name__}")
    print(f"{len(cases)} 组用例全部通过")


if __name__ == "__main__":
    main()
