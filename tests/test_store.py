"""落盘层自检：纯逻辑不碰磁盘，IO 部分用临时目录。

    python tests/test_store.py
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from astrbot_plugin_usage_stats import store  # noqa: E402
from astrbot_plugin_usage_stats.stats import (  # noqa: E402
    Bucket,
    Call,
    Usage,
    day_key,
    fold_by_day_model,
)

DAY = 1760630400.0


def _b(calls=1, other=10, cached=0, out=5, failed=0, images=0) -> Bucket:
    b = Bucket()
    b.calls = calls
    b.failed = failed
    b.images = images
    b.usage = Usage(other, cached, out)
    return b


def _cell(calls, other, cached, out, total):
    return {
        "calls": calls, "failed": 0, "images": 0,
        "input_other": other, "input_cached": cached, "output": out, "total": total,
    }


def test_snapshot_uses_bucket_dict() -> None:
    d = day_key(DAY)
    rolled = {"p": {d: {"m": _b(calls=3, other=7, cached=2, out=1)}}}
    snap = store.snapshot(rolled)
    assert snap["p"][d]["m"]["calls"] == 3
    assert snap["p"][d]["m"]["total"] == 10


def test_merge_adds_and_recomputes_total() -> None:
    d = day_key(DAY)
    a = {"p": {d: {"m": _cell(1, 10, 0, 5, 15)}}}
    b = {"p": {d: {"m": _cell(2, 0, 90, 1, 91)}}}
    cell = store.merge(a, b)["p"][d]["m"]
    assert cell["calls"] == 3
    assert (cell["input_other"], cell["input_cached"], cell["output"]) == (10, 90, 6)
    assert cell["total"] == 106, "total 必须重算，不能沿用旧值"


def test_merge_does_not_mutate_inputs() -> None:
    d = day_key(DAY)
    a = {"p": {d: {"m": {"calls": 1}}}}
    b = {"p": {d: {"m": {"calls": 2}}}}
    store.merge(a, b)
    assert a["p"][d]["m"]["calls"] == 1 and b["p"][d]["m"]["calls"] == 2


def test_merge_handles_new_plugin_day_model() -> None:
    d0, d1 = day_key(DAY), day_key(DAY + 86400)
    m = store.merge({"a": {d0: {"m1": {"calls": 1}}}}, {"b": {d1: {"m2": {"calls": 5}}}})
    assert set(m) == {"a", "b"}
    assert m["b"][d1]["m2"]["calls"] == 5
    m2 = store.merge({"a": {d0: {"m1": {"calls": 1}}}}, {"a": {d0: {"m2": {"calls": 7}}}})
    assert set(m2["a"][d0]) == {"m1", "m2"}, "同一天的不同模型要能并存"


def test_merge_is_idempotent_with_a_fixed_baseline() -> None:
    """保存流程每次都用「同一个基线 + 当前内存全量」重算，所以重复保存不能翻倍。"""
    d = day_key(DAY)
    baseline = {"p": {d: {"m": _cell(1, 10, 0, 5, 15)}}}
    extra = {"p": {d: {"m": _cell(2, 4, 0, 1, 5)}}}
    once = store.merge(baseline, extra)
    twice = store.merge(baseline, extra)
    assert once == twice
    assert once["p"][d]["m"]["calls"] == 3, "不是 4 —— 基线不随保存变化"


def test_prune_drops_old_and_empty() -> None:
    now = DAY
    fresh, stale = day_key(now), day_key(now - 90 * 86400)
    buckets = {"p": {fresh: {"m": {}}, stale: {"m": {}}}, "q": {stale: {"m": {}}}}
    out = store.prune(buckets, now, keep_days=60)
    assert set(out) == {"p"}, "只剩旧天的插件要整个丢掉"
    assert set(out["p"]) == {fresh}


def test_prune_keep_days_zero_keeps_all() -> None:
    stale = day_key(DAY - 900 * 86400)
    assert set(store.prune({"p": {stale: {"m": {}}}}, DAY, keep_days=0)["p"]) == {stale}


def test_as_buckets_tolerates_junk() -> None:
    d = day_key(DAY)
    data = {
        "p": {d: {"m": {"calls": 2, "input_other": 5}}},
        "bad_plugin": "not a dict",
        "q": {d: {"bad_model": "not a dict"}},
    }
    out = store.as_buckets(data)
    assert out["p"][d]["m"].calls == 2
    assert out["p"][d]["m"].usage == Usage(5, 0, 0), "缺的字段当 0"
    assert set(out) == {"p"}, "畸形的插件/模型要跳过而不是抛"
    assert store.as_buckets({}) == {}
    assert store.as_buckets("nonsense") == {}  # type: ignore[arg-type]


def test_by_plugin_model_flattens_days() -> None:
    d0, d1 = day_key(DAY), day_key(DAY + 86400)
    nested = store.as_buckets({
        "p": {
            d0: {"m": {"calls": 2, "input_other": 10, "output": 1}},
            d1: {"m": {"calls": 3, "input_other": 5, "output": 4}, "n": {"calls": 1}},
        }
    })
    flat = store.by_plugin_model(nested)
    assert flat["p"]["m"].calls == 5
    assert flat["p"]["m"].usage == Usage(15, 0, 5)
    assert flat["p"]["n"].calls == 1


def test_window_view_filters_and_aggregates() -> None:
    d0, d1, d2 = day_key(DAY), day_key(DAY + 86400), day_key(DAY + 2 * 86400)
    nested = store.as_buckets({
        "p": {
            d0: {"m": {"calls": 2, "input_other": 10, "output": 1}},
            d1: {"m": {"calls": 3, "input_other": 5, "output": 4}, "n": {"calls": 1}},
            d2: {"m": {"calls": 9, "input_other": 100}},
        }
    })
    view, series, by_plugin = store.window_view(nested, cutoff=d1)
    assert view["p"]["m"].calls == 3 + 9, "跨天合并"
    assert view["p"]["m"].usage == Usage(105, 0, 4)
    assert view["p"]["n"].calls == 1
    assert [s["day"] for s in series] == [d1, d2], "截断在卷首，且升序"
    assert series[0]["calls"] == 3 + 1
    assert series[0]["total"] == 5 + 4, "n 只有调用次数、没有 token，不该被算进总量"
    assert series[1]["total"] == 100
    # 按插件的同一份序列：用来把图切成单个插件
    assert [s["day"] for s in by_plugin["p"]] == [d1, d2]
    assert by_plugin["p"][0]["total"] == 5 + 4


def test_window_view_empty_cutoff_keeps_all() -> None:
    d0, d1 = day_key(DAY), day_key(DAY + 86400)
    nested = store.as_buckets({"p": {d0: {"m": {"calls": 1}}, d1: {"m": {"calls": 2}}}})
    view, series, by_plugin = store.window_view(nested)
    assert view["p"]["m"].calls == 3
    assert len(series) == 2 and len(by_plugin["p"]) == 2


def test_window_view_does_not_mutate_input() -> None:
    d0 = day_key(DAY)
    nested = store.as_buckets({"p": {d0: {"m": {"calls": 2}}}})
    view, _, _ = store.window_view(nested)
    view["p"]["m"].calls = 99
    assert nested["p"][d0]["m"].calls == 2, "窗口视图必须是副本"


def test_window_view_until_excludes_upper_bound() -> None:
    d0, d1, d2 = day_key(DAY), day_key(DAY + 86400), day_key(DAY + 2 * 86400)
    nested = store.as_buckets({
        "p": {
            d0: {"m": {"calls": 1, "input_other": 10}},
            d1: {"m": {"calls": 2, "input_other": 20}},
            d2: {"m": {"calls": 4, "input_other": 40}},
        }
    })
    view, series, _ = store.window_view(nested, d1, d2)
    assert view["p"]["m"].calls == 2, "只要 d1"
    assert [s["day"] for s in series] == [d1]
    # 上界不含自己：d2 被排除
    view2, _, _ = store.window_view(nested, d1, d2)
    assert sum(b.calls for b in view2["p"].values()) == 2
    # 两界合成「上一个同长周期」
    prev, _, _ = store.window_view(nested, d0, d1)
    assert prev["p"]["m"].calls == 1


def test_series_for_narrows_to_plugin_and_model() -> None:
    d0, d1 = day_key(DAY), day_key(DAY + 86400)
    nested = store.as_buckets({
        "p": {
            d0: {"m1": {"calls": 1, "input_other": 10}, "m2": {"calls": 2, "input_other": 20}},
            d1: {"m1": {"calls": 4, "input_other": 40}},
        },
        "q": {d0: {"m1": {"calls": 8, "input_other": 80}}},
    })
    all_models = store.series_for(nested, "p")
    assert [s["day"] for s in all_models] == [d0, d1], "按天升序"
    assert all_models[0]["calls"] == 3 and all_models[0]["total"] == 30, "同一天里两个模型相加"

    one = store.series_for(nested, "p", "m1")
    assert [s["calls"] for s in one] == [1, 4], "只要 m1"
    assert one[0]["total"] == 10

    assert store.series_for(nested, "q", "m1")[0]["calls"] == 8, "同名模型按插件分开"
    assert store.series_for(nested, "p", "nope") == [], "没有的模型给空表，不抛"

    # **回归**：前端带回来的是展示名（过了 tidy_model_key），不是原始键。
    # 只认原始键的话，界面上点「趋势」会永远得到空图。
    tidy_nested = store.as_buckets(
        store.snapshot(
            fold_by_day_model([
                Call(plugin="t", provider_id="openai/gpt-4o", model="gpt-4o",
                     timestamp=DAY, usage=Usage(2, 3, 4), ok=True,
                     method="text_chat", session="s"),
            ])
        )
    )
    raw_key = sum(p["total"] for p in store.series_for(tidy_nested, "t", "openai/gpt-4o|gpt-4o"))
    tidy_key = sum(p["total"] for p in store.series_for(tidy_nested, "t", "openai/gpt-4o"))
    assert raw_key == tidy_key == 9, f"原始键 {raw_key} 与展示名 {tidy_key} 都要能取到"
    assert store.series_for(nested, "nope") == []
    # 界也要管用
    assert len(store.series_for(nested, "p", "", d1)) == 1
    assert len(store.series_for(nested, "p", "", "", d1)) == 1


def test_as_buckets_does_not_silently_zero() -> None:
    """把已经是 Bucket 的结构喂进去，不许静默变成 0。

    `_view_days` 里只要上游少一层 snapshot，整张表就会全零 —— 不报错，
    视图上只是「这个范围没有记录」。所以这里必须原样收下。
    """
    day = day_key(DAY)
    bucket = _b(calls=3, other=11, cached=22, out=33)
    nested = store.as_buckets({"p": {day: {"m": bucket}}})
    assert nested["p"][day]["m"].usage.total == 66, "喂 Bucket 也要算进来"
    assert nested["p"][day]["m"].calls == 3

    # 形状不对的仍然跳过，不抛
    assert store.as_buckets({"p": {day: {"m": "垃圾"}}}) == {}
    assert store.as_buckets({"p": "垃圾"}) == {}
    assert store.as_buckets(None) == {}


def test_totals_agree_across_paths() -> None:
    """同一份数据，四处算出来的总量必须一样。

    /usage 走 by_plugin_model 求和，api_stats 走逐插件累加 Usage，
    环比走 aggregate 的三栏相加，图表走 window_view 的按天序列。
    四条路写法不同 —— 对不上的时候，用户看到的数字取决于他看哪儿。
    """
    calls = [
        Call(plugin="p", provider_id="openai", model="m1", timestamp=DAY,
             usage=Usage(10, 90, 5), ok=True, images=2, method="text_chat", session="s"),
        Call(plugin="p", provider_id="openai", model="m2", timestamp=DAY + 86400,
             usage=Usage(7, 0, 3), ok=False, images=0, method="text_chat_stream", session=""),
        Call(plugin="q", provider_id="zhipu", model="m1", timestamp=DAY + 86400,
             usage=Usage(1, 2, 4), ok=True, images=1, method="text_chat", session="s"),
        # provider id 以模型名结尾 —— 这条的展示名与原始键**不一样**
        Call(plugin="p", provider_id="openai/gpt-4o", model="gpt-4o", timestamp=DAY,
             usage=Usage(2, 3, 4), ok=True, images=0, method="text_chat", session="s"),
    ]
    # 走的就是 main._view_days 那条路：fold -> snapshot -> as_buckets
    nested = store.as_buckets(store.snapshot(fold_by_day_model(calls)))
    view, series, by_plugin = store.window_view(nested)

    by_command = sum(
        b.usage.total
        for models in store.by_plugin_model(nested).values()
        for b in models.values()
    )
    by_api = sum(
        sum((b.usage for b in models.values()), Usage()).total for models in view.values()
    )
    by_aggregate = store.aggregate(view)["tokens"]
    by_series = sum(point["total"] for point in series)
    expected = sum(c.usage.total for c in calls)

    assert by_command == by_api == by_aggregate == by_series == expected, (
        f"总量对不上：/usage={by_command} api={by_api} 环比={by_aggregate} "
        f"图表={by_series} 期望={expected}"
    )

    # 每天：按插件分开的序列之和 == 当天的合计。
    # 视图的堆叠柱靠这条才能铺满 —— 少一块就是柱子上方空一截，而且不会报错。
    per_day: dict[str, int] = {}
    for points in by_plugin.values():
        for point in points:
            per_day[point["day"]] = per_day.get(point["day"], 0) + point["total"]
    assert per_day == {p["day"]: p["total"] for p in series}, (per_day, series)

    # 调用次数、失败、图片也一起对
    calls_by_series = sum(point["calls"] for point in series)
    calls_by_api = sum(sum(b.calls for b in m.values()) for m in view.values())
    calls_by_aggregate = store.aggregate(view)["calls"]
    assert calls_by_series == calls_by_api == calls_by_aggregate == len(calls)

    # 下钻取序列也不能多算：两条 m1 分属不同插件，各算各的
    assert sum(p["total"] for p in store.series_for(nested, "p", "openai|m1")) == 105
    assert sum(p["total"] for p in store.series_for(nested, "q", "zhipu|m1")) == 7



def test_aggregate_sums_everything() -> None:
    d0 = day_key(DAY)
    nested = store.as_buckets({
        "a": {d0: {"m": {"calls": 2, "input_other": 10, "input_cached": 90, "output": 5}}},
        "b": {d0: {"n": {"calls": 3, "failed": 1, "images": 4, "input_other": 1}}},
    })
    # aggregate 吃的是 window_view 的输出（插件 → 模型），不是三层的那份
    view, _, _ = store.window_view(nested)
    totals = store.aggregate(view)
    assert totals["calls"] == 5 and totals["failed"] == 1 and totals["images"] == 4
    assert totals["input_other"] == 11 and totals["input_cached"] == 90 and totals["output"] == 5
    assert totals["tokens"] == 106, "tokens = input_other + input_cached + output"
    assert store.aggregate({})["tokens"] == 0


def test_window_view_empty_input() -> None:
    view, series, by_plugin = store.window_view({})
    assert view == {} and series == [] and by_plugin == {}


def _tmp() -> pathlib.Path | None:
    """能写就返回一个可写目录，两处都写不了才返回 None。

    先试系统临时目录。受限环境（沙箱、只读挂载）写不了它，就退到插件目录
    外面的一个下划线目录 —— 那里通常可写，落盘用例就不必整组跳过。
    不这么做的话，「未验证」和「验证不了」会被混成同一件事。
    """
    try:
        d = pathlib.Path(tempfile.mkdtemp())
        probe = d / ".probe"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        return d
    except OSError:
        pass
    fallback = pathlib.Path(__file__).resolve().parents[2] / "_tmp_test"
    try:
        fallback.mkdir(parents=True, exist_ok=True)
        probe = fallback / ".probe"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        return fallback
    except OSError:
        return None


def test_load_missing_and_broken(d: pathlib.Path) -> None:
    assert store.load(d / "nope.json") == {}, "文件不存在要给空"
    bad = d / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    assert store.load(bad) == {}, "坏 JSON 要给空，不能抛"
    wrong = d / "v.json"
    wrong.write_text('{"version": 999, "buckets": {"p": {}}}', encoding="utf-8")
    assert store.load(wrong) == {}, "版本不符要当空，不能硬解析"
    no_buckets = d / "nb.json"
    no_buckets.write_text('{"version": 1}', encoding="utf-8")
    assert store.load(no_buckets) == {}


def test_load_version_policy(d: pathlib.Path) -> None:
    """版本比本插件旧要照收 —— 升级插件不该把用户的历史清掉。

    对照实现（token_sql 的 storage.py）也是把旧版本列进 `LEGACY_SCHEMA_VERSIONS`
    一起接受，只拒不认识的。更新的版本读不准，宁可当空。
    """
    buckets = {"p": {day_key(DAY): {"m": {"calls": 3}}}}

    def write(name: str, payload: dict) -> pathlib.Path:
        target = d / name
        target.write_text(json.dumps(payload), encoding="utf-8")
        return target

    older = write("older.json", {"version": store.FORMAT_VERSION - 1, "buckets": buckets})
    assert store.load(older) == buckets, "旧版本要照收"
    same = write("same.json", {"version": store.FORMAT_VERSION, "buckets": buckets})
    assert store.load(same) == buckets
    newer = write("newer.json", {"version": store.FORMAT_VERSION + 1, "buckets": buckets})
    assert store.load(newer) == {}, "更新的版本说不准，宁可当空"
    nover = write("nover.json", {"buckets": buckets})
    assert store.load(nover) == buckets, "没版本号的（手写或导入）按形状收"


def test_save_leaves_no_temp_files(d: pathlib.Path) -> None:
    """不管成没成，都不能留下临时文件。

    临时文件用唯一名（mkstemp），残留只可能来自失败路径 —— 所以两条路都要查。
    """
    path = d / "tmpdir" / "usage.json"
    assert store.save(path, {"p": {}}) is True
    assert list(path.parent.glob(path.name + ".*")) == [], "成功之后不该有残留"

    bad = d / "tmpdir" / "bad.json"
    assert store.save(bad, {"p": object()}) is False, "序列化不了要给 False，不能抛"
    assert list(bad.parent.glob(bad.name + ".*")) == [], "失败之后也不该有残留"
    assert not bad.exists(), "更不能留下半个文件"


def test_save_then_load_round_trip(d: pathlib.Path) -> None:
    path = d / "sub" / "usage.json"
    data = {"p": {day_key(DAY): {"m": {"calls": 3, "total": 30}}}}
    assert store.save(path, data) is True
    assert path.exists(), "save 要建好父目录"
    assert list(path.parent.glob(path.name + ".*")) == [], "临时文件要已被搬走"
    assert store.load(path) == data


def test_save_failure_is_not_fatal(d: pathlib.Path) -> None:
    """写盘失败（比如把文件当目录用）要给 False，不能抛 —— 记账故障不能影响对话。"""
    blocker = d / "blocker"
    blocker.write_text("x", encoding="utf-8")
    assert store.save(blocker / "sub" / "u.json", {"p": {}}) is False


def test_cache_and_optional_usage_round_trip_without_inflating_total() -> None:
    d = day_key(DAY)
    call = Call("p", "q", "m", DAY, Usage(10, 20, 30, 1, 7, 9, 1))
    snap = store.snapshot(fold_by_day_model([call]))
    merged = store.merge(snap, snap)
    cell = merged["p"][d]["q|m"]
    assert cell["total"] == 120 and cell["cache_reported"] == 2
    assert cell["reasoning"] == 14 and cell["cache_write"] == 18
    assert cell["usage_reported"] == 2
    restored = store.as_buckets(merged)["p"][d]["q|m"].usage
    assert restored == call.usage + call.usage
    legacy = store.as_buckets({"p": {d: {"m": {"input_other": 5}}}})["p"][d]["m"].usage
    assert legacy.reasoning is None and legacy.cache_write is None
    unknown = store.merge({"p": {d: {"m": {"calls": 1}}}}, {"p": {d: {"m": {"calls": 1}}}})
    assert unknown["p"][d]["m"].get("reasoning") is None


def test_merge_cache_reported_is_not_a_token() -> None:
    cell = {"input_other": 1, "input_cached": 2, "output": 3, "cache_reported": 1}
    data = {"p": {"day": {"m": cell}}}
    assert store.merge(data, data)["p"]["day"]["m"]["total"] == 12


def main() -> None:
    pure = [
        test_cache_and_optional_usage_round_trip_without_inflating_total,
        test_merge_cache_reported_is_not_a_token,
        test_snapshot_uses_bucket_dict,
        test_merge_adds_and_recomputes_total,
        test_merge_does_not_mutate_inputs,
        test_merge_handles_new_plugin_day_model,
        test_merge_is_idempotent_with_a_fixed_baseline,
        test_prune_drops_old_and_empty,
        test_prune_keep_days_zero_keeps_all,
        test_as_buckets_tolerates_junk,
        test_by_plugin_model_flattens_days,
        test_window_view_filters_and_aggregates,
        test_window_view_empty_cutoff_keeps_all,
        test_window_view_does_not_mutate_input,
        test_window_view_empty_input,
        test_window_view_until_excludes_upper_bound,
        test_aggregate_sums_everything,
        test_as_buckets_does_not_silently_zero,
        test_totals_agree_across_paths,
        test_series_for_narrows_to_plugin_and_model,
    ]
    for case in pure:
        case()
        print(f"  ok  {case.__name__}")

    io_cases = [
        test_load_missing_and_broken,
        test_save_then_load_round_trip,
        test_save_failure_is_not_fatal,
        test_load_version_policy,
        test_save_leaves_no_temp_files,
    ]
    d = _tmp()
    if d is None:
        print(f"  跳过 {len(io_cases)} 组：这个环境写不了临时目录（纯逻辑部分不受影响）")
    else:
        for case in io_cases:
            case(d)
            print(f"  ok  {case.__name__}")

    ran = len(pure) + (0 if d is None else len(io_cases))
    print(f"{ran} 组用例全部通过")


if __name__ == "__main__":
    main()
