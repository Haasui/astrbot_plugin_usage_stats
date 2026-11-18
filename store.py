"""JSON 日汇总的序列化、合并与滚动清理，不依赖 AstrBot。

计数按插件、天和 provider/模型键分组，与版本、保存时间和明细提交证明一起存储。
不保存消息正文、提示词或会话标识。提交约束见 docs/ARCHITECTURE.md。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

from .stats import Bucket, Usage, day_key, optional_sum, tidy_model_key

FORMAT_VERSION = 1
"""支持的日汇总格式版本。拒绝更高版本，接受旧版本并为缺失计数提供兼容值。"""

KEEP_DAYS = 60
"""保留多少天。更旧的在保存时丢掉。"""

COUNT_KEYS = (
    "calls",
    "failed",
    "canceled",
    "images",
    "input_other",
    "input_cached",
    "cache_reported",
    "usage_reported",
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
    """合并两个 snapshot，要求两侧调用互不重叠。

    base 是最近一次成功保存的基线，extra 是待保存调用；同一天的不同调用可以相加。
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
                for key in ("reasoning", "cache_write"):
                    current[key] = optional_sum(current.get(key), bucket.get(key))
                current["total"] = (
                    int(current.get("input_other", 0) or 0)
                    + int(current.get("input_cached", 0) or 0)
                    + int(current.get("output", 0) or 0)
                )
    return out


def prune(
    buckets: dict,
    now: float,
    keep_days: int = KEEP_DAYS,
    tz_offset_hours: int = 8,
) -> dict:
    """删除过期日汇总，并移除没有保留日期的插件；keep_days<=0 不按天数裁剪。

    YYYY-MM-DD 天键的字符串顺序与日期顺序一致。
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
    """将落盘字典转换为同形状的 Bucket；缺失常规计数按 0，可选计数保持未知。"""
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
                    # 接受已经转换的内存对象，避免重复转换时丢失记录。
                    out[plugin][day][model] = cell
                    continue
                if not isinstance(cell, dict):
                    continue
                bucket = Bucket()
                bucket.calls = int(cell.get("calls", 0) or 0)
                bucket.failed = int(cell.get("failed", 0) or 0)
                bucket.canceled = int(cell.get("canceled", 0) or 0)
                bucket.images = int(cell.get("images", 0) or 0)
                bucket.usage = Usage(
                    input_other=int(cell.get("input_other", 0) or 0),
                    input_cached=int(cell.get("input_cached", 0) or 0),
                    output=int(cell.get("output", 0) or 0),
                    cache_reported=int(cell.get("cache_reported", 0) or 0),
                    reasoning=cell.get("reasoning"),
                    cache_write=cell.get("cache_write"),
                    usage_reported=int(cell.get("usage_reported", 0) or 0),
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
                target.canceled += bucket.canceled
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
                        canceled=bucket.canceled,
                        images=bucket.images,
                        usage=bucket.usage,
                    )
                else:
                    current.calls += bucket.calls
                    current.failed += bucket.failed
                    current.canceled += bucket.canceled
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
    calls = failed = canceled = images = other = cached = output = 0
    for models in view.values():
        for bucket in models.values():
            calls += bucket.calls
            failed += bucket.failed
            canceled += bucket.canceled
            images += bucket.images
            other += bucket.usage.input_other
            cached += bucket.usage.input_cached
            output += bucket.usage.output
    return {
        "tokens": other + cached + output,
        "calls": calls,
        "failed": failed,
        "canceled": canceled,
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
    """取指定插件及可选模型的按天序列，供下钻时按需查询。

    常规响应只保留插件级序列，避免每次轮询携带全量模型序列。
    """
    days: dict[str, dict[str, int]] = {}
    for day, models in (nested.get(plugin) or {}).items():
        if cutoff and day < cutoff:
            continue
        if until and day >= until:
            continue
        for name, bucket in models.items():
            # 兼容原始 provider|model 键和 tidy_model_key 生成的展示名。
            if model and name != model and tidy_model_key(name) != model:
                continue
            cell = days.setdefault(day, {"day": day, "calls": 0, "total": 0})
            cell["calls"] += bucket.calls
            cell["total"] += bucket.usage.total
    return [days[d] for d in sorted(days)]


def load(path: str | Path) -> dict:
    """读取日汇总，接受旧版本；文件不可读、损坏或格式版本更高时返回空字典。"""
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


def load_checkpoint(path: str | Path) -> str:
    """Read the detail inclusion proof stored beside daily totals."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        value = raw.get("event_checkpoint", "") if isinstance(raw, dict) else ""
        return value if isinstance(value, str) and len(value) <= 128 else ""
    except (OSError, ValueError):
        return ""


def save(path: str | Path, buckets: dict, event_checkpoint: str = "") -> bool:
    """写入同目录唯一临时文件后原子替换，避免共享临时文件名造成写入冲突。

    I/O 或序列化失败返回 False，不向记账调用方抛出。
    """
    target = Path(path)
    payload = {
        "version": FORMAT_VERSION,
        "saved_at": int(time.time()),
        "buckets": buckets,
        "event_checkpoint": event_checkpoint,
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
