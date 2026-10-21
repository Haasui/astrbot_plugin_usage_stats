"""落盘层：天 × 模型计数的序列化、合并与滚动清理。

**纯标准库，不 import AstrBot**（只 import 同层的 stats）。除 `load` / `save` 外都是纯函数。

落盘内容只有计数：没有消息正文、没有提示词、没有会话内容。

形状（插件 → 天 → 模型 → 计数）：:

    {
      "version": 1,
      "saved_at": 1760630400,
      "buckets": {
        "astrbot_plugin_image_gate": {
          "2026-10-16": {
            "openai|GLM-5.3-Flash": {"calls": 7, "failed": 0, "images": 2,
                                     "input_other": 300, "input_cached": 900,
                                     "output": 40, "total": 1240}
          }
        }
      }
    }

为什么是「天 × 模型」：账单要时间维度，而「谁在用贵模型」要模型维度。
天而不是小时 —— 账单按天算，而且键少一个数量级。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

from .stats import Bucket, Usage, day_key, tidy_model_key

FORMAT_VERSION = 1
"""落盘格式版本。

比它新的版本当空（那边可能换了布局，硬解析会读出错的数）；比它旧的照收——
这个形状历来只做过加法，`as_buckets` 对缺字段一律当 0，升级插件不该清掉历史。
"""

KEEP_DAYS = 60
"""保留多少天。更旧的在保存时丢掉。"""

COUNT_KEYS = (
    "calls",
    "failed",
    "images",
    "input_other",
    "input_cached",
    "cache_reported",
    "output",
)
"""参与累加的字段。`total` 是派生的，不在这里。"""


def snapshot(rolled: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> dict:
    """`stats.fold_by_day_model()` 的结果 → 普通 dict，准备落盘。"""
    return {
        plugin: {
            day: {model: bucket.to_dict() for model, bucket in models.items()}
            for day, models in days.items()
        }
        for plugin, days in rolled.items()
    }


def merge(base: dict, extra: dict) -> dict:
    """两个 snapshot 相加。

    可以直接相加，是因为两侧覆盖的调用不相交：base 来自上次运行落盘的数据，
    extra 来自本次运行内存里的全部记录。**同一天也安全** —— 记的是不同的调用。
    """
    out: dict = {
        p: {d: {m: dict(b) for m, b in models.items()} for d, models in days.items()}
        for p, days in base.items()
    }
    for plugin, days in extra.items():
        target = out.setdefault(plugin, {})
        for day, models in days.items():
            cell = target.setdefault(day, {})
            for model, bucket in models.items():
                current = cell.get(model)
                if current is None:
                    cell[model] = dict(bucket)
                    continue
                for key in COUNT_KEYS:
                    current[key] = int(current.get(key, 0) or 0) + int(
                        bucket.get(key, 0) or 0
                    )
                current["total"] = (
                    int(current.get("input_other", 0) or 0)
                    + int(current.get("input_cached", 0) or 0)
                    + int(current.get("cache_reported", 0) or 0)
                    + int(current.get("output", 0) or 0)
                )
    return out


def prune(
    buckets: dict,
    now: float,
    keep_days: int = KEEP_DAYS,
    tz_offset_hours: int = 8,
) -> dict:
    """丢掉旧的天，并清掉已经空掉的插件和模型。

    天键是 `YYYY-MM-DD`，定长且高位在前 —— **字符串比较就是时间顺序**，不必解析时间。
    """
    cutoff = day_key(now - keep_days * 86400, tz_offset_hours) if keep_days > 0 else ""
    out: dict = {}
    for plugin, days in buckets.items():
        kept = {
            day: {m: dict(b) for m, b in models.items()}
            for day, models in days.items()
            if day >= cutoff
        }
        if kept:
            out[plugin] = kept
    return out


def as_buckets(buckets: dict) -> dict[str, dict[str, dict[str, Bucket]]]:
    """落盘 dict → 内存 `Bucket`，形状不变。字段缺失一律当 0，不抛。"""
    out: dict[str, dict[str, dict[str, Bucket]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    if not isinstance(buckets, dict):
        return {}
    for plugin, days in buckets.items():
        if not isinstance(days, dict):
            continue
        for day, models in days.items():
            if not isinstance(models, dict):
                continue
            for model, cell in models.items():
                if isinstance(cell, Bucket):
                    # 已经是内存对象（上游少了一层 snapshot 之类）。原样收下：
                    # 直接 continue 会让整张表**静默变 0**，而且不报错，很难查。
                    out[plugin][day][model] = cell
                    continue
                if not isinstance(cell, dict):
                    continue
                bucket = Bucket()
                bucket.calls = int(cell.get("calls", 0) or 0)
                bucket.failed = int(cell.get("failed", 0) or 0)
                bucket.images = int(cell.get("images", 0) or 0)
                bucket.usage = Usage(
                    input_other=int(cell.get("input_other", 0) or 0),
                    input_cached=int(cell.get("input_cached", 0) or 0),
                    output=int(cell.get("output", 0) or 0),
                    cache_reported=int(cell.get("cache_reported", 0) or 0),
                )
                out[plugin][day][model] = bucket
    return {p: {d: dict(m) for d, m in days.items()} for p, days in out.items()}


def by_plugin_model(
    nested: Mapping[str, Mapping[str, Mapping[str, Bucket]]],
) -> dict[str, dict[str, Bucket]]:
    """把天维度压掉：插件 → 模型。用于展示。"""
    out: dict[str, dict[str, Bucket]] = defaultdict(lambda: defaultdict(Bucket))
    for plugin, days in nested.items():
        for models in days.values():
            for model, bucket in models.items():
                target = out[plugin][model]
                target.calls += bucket.calls
                target.failed += bucket.failed
                target.images += bucket.images
                target.usage = target.usage + bucket.usage
    return {p: dict(m) for p, m in out.items()}


def window_view(
    nested: Mapping[str, Mapping[str, Mapping[str, Bucket]]],
    cutoff: str = "",
    until: str = "",
) -> tuple[dict[str, dict[str, Bucket]], list[dict], dict[str, list[dict]]]:
    """按天筛选，并汇总成展示要的两块数据。

    Args:
        nested: 插件 -> 天 -> 模型 -> Bucket。
        cutoff: 只保留天键 >= 它的数据。空串表示不设下界。
        until: 只保留天键 < 它的数据。空串表示不设上界。
            「上一个同长周期」就是拿这两个界一起圈出来的。

    Returns:
        (view, series, by_plugin)
        view —— 插件 -> 模型 -> Bucket，跨天合并；
        series —— [{"day", "calls", "total"}]，全部插件合计，按天升序；
        by_plugin —— 同一个序列，但按插件分开（给「看这一个插件的趋势」用）。

    不修改入参：新建的格子都是副本。
    """
    view: dict[str, dict[str, Bucket]] = {}
    by_day: dict[str, dict[str, int]] = {}
    plugin_day: dict[str, dict[str, dict[str, int]]] = {}
    for plugin, days in nested.items():
        for day, models in days.items():
            if cutoff and day < cutoff:
                continue
            if until and day >= until:
                continue
            cell = view.setdefault(plugin, {})
            day_cell = by_day.setdefault(day, {"calls": 0, "total": 0})
            plugin_cell = plugin_day.setdefault(plugin, {}).setdefault(
                day, {"calls": 0, "total": 0}
            )
            for model, bucket in models.items():
                current = cell.get(model)
                if current is None:
                    cell[model] = Bucket(
                        calls=bucket.calls,
                        failed=bucket.failed,
                        images=bucket.images,
                        usage=bucket.usage,
                    )
                else:
                    current.calls += bucket.calls
                    current.failed += bucket.failed
                    current.images += bucket.images
                    current.usage = current.usage + bucket.usage
                day_cell["calls"] += bucket.calls
                day_cell["total"] += bucket.usage.total
                plugin_cell["calls"] += bucket.calls
                plugin_cell["total"] += bucket.usage.total

    def _series(source: dict[str, dict[str, int]]) -> list[dict]:
        return [
            {"day": day, "calls": detail["calls"], "total": detail["total"]}
            for day, detail in sorted(source.items())
        ]

    by_plugin = {plugin: _series(days) for plugin, days in plugin_day.items()}
    return view, _series(by_day), by_plugin


def aggregate(view: Mapping[str, Mapping[str, Bucket]]) -> dict:
    """把一个 window_view 的 view 汇总成一组总量。环比要用它各算一次。"""
    calls = failed = images = other = cached = output = 0
    for models in view.values():
        for bucket in models.values():
            calls += bucket.calls
            failed += bucket.failed
            images += bucket.images
            other += bucket.usage.input_other
            cached += bucket.usage.input_cached
            output += bucket.usage.output
    return {
        "tokens": other + cached + output,
        "calls": calls,
        "failed": failed,
        "images": images,
        "input_other": other,
        "input_cached": cached,
        "output": output,
    }


def series_for(
    nested: Mapping[str, Mapping[str, Mapping[str, Bucket]]],
    plugin: str,
    model: str = "",
    cutoff: str = "",
    until: str = "",
) -> list[dict]:
    """取某一个插件（可选再限定某一个模型）的按天序列。

    window_view 的 by_plugin 只到插件一级，模型级没有 —— 而把模型级也常驻在响应里，
    等于让每 30 秒一次的轮询都多背一份全量数据。所以只在真有人下钻时才走这条路。
    """
    days: dict[str, dict[str, int]] = {}
    for day, models in (nested.get(plugin) or {}).items():
        if cutoff and day < cutoff:
            continue
        if until and day >= until:
            continue
        for name, bucket in models.items():
            # 前端带回来的是**展示名**（api_stats 里过了 tidy_model_key），
            # 而这里的键是原始的 `provider|model`。两个都得认 ——
            # 只认原始键的话，界面上点「趋势」会永远得到空图。
            if model and name != model and tidy_model_key(name) != model:
                continue
            cell = days.setdefault(day, {"day": day, "calls": 0, "total": 0})
            cell["calls"] += bucket.calls
            cell["total"] += bucket.usage.total
    return [days[d] for d in sorted(days)]


def load(path: str | Path) -> dict:
    """读回数据。文件不存在 / 损坏 / 版本比本插件新，一律给空 dict。

    版本比本插件**旧**的照收。对照实现（token_sql 的 storage.py）也是这么做的：
    它把旧版本放进 `LEGACY_SCHEMA_VERSIONS` 一起接受，只拒不认识的。
    """
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    version = raw.get("version")
    if isinstance(version, int) and version > FORMAT_VERSION:
        return {}
    buckets = raw.get("buckets")
    return buckets if isinstance(buckets, dict) else {}


def save(path: str | Path, buckets: dict) -> bool:
    """写盘。先写同目录下的唯一临时文件，再原子替换。

    临时文件用唯一名而不是固定的 `.tmp`：同一个数据目录上万一有两个写入方，
    固定名字会互相覆盖，替换时可能把半截内容搬过去。

    失败一律给 False，不往上抛 —— 调用方在记账路径上。
    """
    target = Path(path)
    payload = {
        "version": FORMAT_VERSION,
        "saved_at": int(time.time()),
        "buckets": buckets,
    }
    tmp: str | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, tmp = tempfile.mkstemp(
            dir=str(target.parent), prefix=target.name + ".", suffix=".tmp"
        )
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False)
        os.replace(tmp, target)
        return True
    except (OSError, TypeError, ValueError):
        # TypeError / ValueError：buckets 里有 json 序列化不了的东西。
        # 那也算「保存失败」，不该往上抛。
        return False
    finally:
        if tmp is not None and os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
