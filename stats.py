"""用量数据模型、聚合与文本输出，不依赖 AstrBot。

usage_from 转换宿主用量，保留 input_other / input_cached / output 三列口径。
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping
from uuid import uuid4

MAIN_AGENT = "main_agent"
"""归因不到插件时的归属：主对话，或内置 star。"""

NO_SESSION = "no_session"
"""未取得会话标识时使用的占位值；归因约束见 docs/ARCHITECTURE.md。"""


def optional_sum(left: int | None, right: int | None) -> int | None:
    """Sum reported counters; keep wholly unknown dimensions unknown."""
    if left is None and right is None:
        return None
    return (left or 0) + (right or 0)


@dataclass(frozen=True)
class Usage:
    """一次调用的 Token 用量，按宿主口径区分普通输入、缓存读取与输出。

    缓存读取单独保留，以支持不同单价及缓存命中率计算。
    """

    input_other: int = 0
    """Canonical AstrBot normalized input; preserve this value in APIs and storage.

    Detail cache_write is displayed separately, not subtracted from input_other
    in API responses. The composition chart uses the three normalized columns.
    Pricing may split a proven cache_write subset without changing these fields.
    """
    input_cached: int = 0
    output: int = 0
    cache_reported: int = 0
    """明确上报缓存字段或有正缓存计数的调用数。

    未上报与明确上报 0 需区分，前者不能显示为 0% 命中。
    """

    reasoning: int | None = None
    cache_write: int | None = None
    """Informational raw count; never add again to normalized token totals.

    Price it as a subset of input_other only when that relationship is valid.
    """
    usage_reported: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_other + other.input_other,
            self.input_cached + other.input_cached,
            self.output + other.output,
            self.cache_reported + other.cache_reported,
            optional_sum(self.reasoning, other.reasoning),
            optional_sum(self.cache_write, other.cache_write),
            self.usage_reported + other.usage_reported,
        )

    @property
    def total(self) -> int:
        return self.input_other + self.input_cached + self.output


@dataclass(frozen=True)
class Shape:
    """请求各部分的字符长度、项目数量与耗时，不保留正文。

    长度用于比较请求构成，不能当作 Token 用量。
    """

    system: int = 0
    turns: int = 0
    context: int = 0
    prompt: int = 0
    tools: int = 0
    tool_count: int = 0
    images: int = 0
    audio: int = 0
    ms: int = 0


@dataclass
class Call:
    """一次模型调用的记账条目。"""

    plugin: str
    provider_id: str
    model: str
    timestamp: float
    usage: Usage = field(default_factory=Usage)
    ok: bool = True
    images: int = 0
    method: str = "text_chat"
    # 会话标识用于调试与文本调用日志，不写入日汇总或结构化明细。
    session: str = NO_SESSION
    shape: Shape | None = None
    event_id: str = field(default_factory=lambda: str(uuid4()))
    result: str = ""
    ttft_ms: int | None = None

    @property
    def model_key(self) -> str:
        return f"{self.provider_id}|{self.model}" if self.model else self.provider_id


@dataclass
class Bucket:
    """一个归集格里的计数。"""

    calls: int = 0
    failed: int = 0
    images: int = 0
    usage: Usage = field(default_factory=Usage)
    canceled: int = 0

    def add(self, call: Call) -> None:
        self.calls += 1
        if call.result == "canceled":
            self.canceled += 1
        elif not call.ok:
            self.failed += 1
        self.images += call.images
        self.usage = self.usage + call.usage

    def to_dict(self) -> dict:
        return {
            "calls": self.calls,
            "failed": self.failed,
            "canceled": self.canceled,
            "images": self.images,
            "input_other": self.usage.input_other,
            "input_cached": self.usage.input_cached,
            "output": self.usage.output,
            "cache_reported": self.usage.cache_reported,
            "reasoning": self.usage.reasoning,
            "cache_write": self.usage.cache_write,
            "usage_reported": self.usage.usage_reported,
            "total": self.usage.total,
        }


def fold_by_model(calls: Iterable[Call]) -> dict[str, dict[str, Bucket]]:
    """按插件和模型聚合用量，不分时间。"""
    out: dict[str, dict[str, Bucket]] = defaultdict(lambda: defaultdict(Bucket))
    for call in calls:
        out[call.plugin][call.model_key].add(call)
    return {plugin: dict(models) for plugin, models in out.items()}


def tidy_model_key(key: str) -> str:
    """provider id 已以模型名结尾时，去掉展示键中重复的模型名。

    只用于展示，不修改存储键，以免历史记录无法匹配。
    """
    if "|" not in key:
        return key
    provider_id, _, model = key.partition("|")
    if model and provider_id.endswith(model):
        return provider_id
    return key


def day_key(epoch: float, tz_offset_hours: int = 8) -> str:
    """时间戳 → 天键，形如 2026-10-16。

    用固定小时偏移而不是跟随本地时区：容器里 TZ 常是 UTC，而使用者看的是自己的
    墙上时间，固定偏移比跟着容器走可预期。
    """
    return time.strftime("%Y-%m-%d", time.gmtime(epoch + tz_offset_hours * 3600))


def fold_by_day_model(
    calls: Iterable[Call], tz_offset_hours: int = 8
) -> dict[str, dict[str, dict[str, Bucket]]]:
    """按插件、天和模型聚合，供日汇总存储使用。"""
    out: dict[str, dict[str, dict[str, Bucket]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(Bucket))
    )
    for call in calls:
        out[call.plugin][day_key(call.timestamp, tz_offset_hours)][call.model_key].add(call)
    return {
        plugin: {day: dict(models) for day, models in days.items()}
        for plugin, days in out.items()
    }


def add_into(
    target: dict[str, dict[str, Bucket]],
    more: Mapping[str, Mapping[str, Bucket]],
) -> None:
    """把同形状的 more 累加进 target，**就地修改 target**。

    用于把「落盘的历史」和「本次运行的内存记录」合成一张展示表。
    """
    for plugin, models in more.items():
        cell = target.setdefault(plugin, {})
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
                continue
            current.calls += bucket.calls
            current.failed += bucket.failed
            current.canceled += bucket.canceled
            current.images += bucket.images
            current.usage = current.usage + bucket.usage


def total_usage(calls: Iterable[Call]) -> Usage:
    acc = Usage()
    for call in calls:
        acc = acc + call.usage
    return acc


def cutoff_for_days(days: int, now: float | None = None, tz_offset_hours: int = 8) -> str:
    """最近 N 天的天键下限，包含今天；N=1 仅今天，N<=0 不设下限。

    指令和视图共用此换算，避免天数口径漂移。
    """
    if days <= 0:
        return ""
    base = now if now is not None else time.time()
    return day_key(base - (days - 1) * 86400, tz_offset_hours)


def render_call_line(call: Call, tz_offset_hours: int = 8) -> str:
    """生成单行调用日志，包含时间、来源、用量、请求构成与会话标识。"""
    when = time.strftime(
        "%Y-%m-%d %H:%M:%S", time.gmtime((call.timestamp or 0) + tz_offset_hours * 3600)
    )
    usage = call.usage
    incoming = usage.input_other + usage.input_cached
    parts = [
        when,
        call.plugin or MAIN_AGENT,
        # 和视图同一套收敛：provider 已带模型名时不再重复一遍
        tidy_model_key(call.model_key),
        call.method,
        f"in={incoming}",
        f"hit={usage.input_cached}",
        f"out={usage.output}",
    ]
    if incoming:
        parts.append(f"cache={round(usage.input_cached / incoming * 100)}%")
    shape = call.shape
    if shape is not None:
        parts.extend(
            [
                f"sys={shape.system}",
                f"ctx={shape.context}/{shape.turns}",
                f"prompt={shape.prompt}",
                f"tools={shape.tools}/{shape.tool_count}",
                f"img={shape.images}",
                f"aud={shape.audio}",
                f"ms={shape.ms}",
            ]
        )
    parts.append(f"ok={int(bool(call.ok))}")
    parts.append(call.session or NO_SESSION)
    return " ".join(parts)


def compact_number(value: int) -> str:
    """将大数字压缩为 1.2M / 345.6K 等形式，控制指令输出宽度。"""
    number = float(value or 0)
    for unit, size in (("B", 10**9), ("M", 10**6), ("K", 10**3)):
        if abs(number) >= size:
            return f"{number / size:.1f}{unit}"
    return str(int(number))


def fold_by_model_across(
    view: Mapping[str, Mapping[str, Bucket]],
) -> list[tuple[str, Bucket]]:
    """跨插件合并相同 provider/模型键的用量，按 Token 总量降序。

    此处按用量排序，不表示费用排序。
    """
    merged: dict[str, Bucket] = {}
    for models in view.values():
        for key, bucket in models.items():
            current = merged.get(key)
            if current is None:
                merged[key] = Bucket(
                    calls=bucket.calls,
                    failed=bucket.failed,
                    canceled=bucket.canceled,
                    images=bucket.images,
                    usage=bucket.usage,
                )
                continue
            current.calls += bucket.calls
            current.failed += bucket.failed
            current.canceled += bucket.canceled
            current.images += bucket.images
            current.usage = current.usage + bucket.usage
    return sorted(
        merged.items(), key=lambda item: item[1].usage.total, reverse=True
    )


def render_model_table(
    rows: list[tuple[str, Bucket]],
    label: str,
    version: str = "",
) -> str:
    """快捷输出：一行一个模型，按 Token 降序。

    用量包含历史与待保存调用；运行期计数另在状态指令展示，避免混淆口径。
    """
    if not rows:
        return f"模型用量{label}：没有记录。"

    calls = sum(bucket.calls for _, bucket in rows)
    total = Usage()
    for _, bucket in rows:
        total = total + bucket.usage

    shown = rows[:8]
    width = max(len(tidy_model_key(name)) for name, _ in shown)
    head = f" · {version}" if version else ""
    lines = [
        f"模型用量 {label}{head} · {len(rows)} 个模型 · {calls:,} 次",
        f"合计 {compact_number(total.total)} token（输入 "
        f"{compact_number(total.input_other + total.input_cached)}，命中 "
        f"{compact_number(total.input_cached)}，输出 {compact_number(total.output)}"
        f"；缓存 {cache_hit_text(total)}）",
        "—" * 16,
    ]
    for name, bucket in shown:
        lines.append(
            f"{tidy_model_key(name).ljust(width)}  "
            f"{compact_number(bucket.usage.total):>7}  "
            f"{bucket.calls:>6,} 次  缓存 {cache_hit_text(bucket.usage)}"
        )
    if len(rows) > len(shown):
        lines.append(f"…另有 {len(rows) - len(shown)} 个模型，/usage full 看全部")
    return "\n".join(lines)


def render_plugin_table(
    view: Mapping[str, Mapping[str, Bucket]],
    label: str,
    version: str = "",
) -> str:
    """明细：按插件拆到模型，数字压缩，保留失败与图片。"""
    if not view:
        return f"模型用量{label}：没有记录。"

    grand = Usage()
    for models in view.values():
        for bucket in models.values():
            grand = grand + bucket.usage
    head = f" · {version}" if version else ""
    lines = [
        f"模型用量 {label}{head} · 明细",
        f"合计 {compact_number(grand.total)} token；缓存 {cache_hit_text(grand)}",
        "—" * 16,
    ]
    plugins = sorted(
        view.items(),
        key=lambda kv: sum(b.usage.total for b in kv[1].values()),
        reverse=True,
    )
    for name, models in plugins:
        sub = sorted(models.items(), key=lambda kv: kv[1].usage.total, reverse=True)
        plugin_total = sum(b.usage.total for b in models.values())
        calls_n = sum(b.calls for b in models.values())
        lines.append(f"▸ {name}  {compact_number(plugin_total)} token / {calls_n:,} 次")
        for model_key, bucket in sub[:5]:
            lines.append(
                f"    · {tidy_model_key(model_key)}  "
                f"{compact_number(bucket.usage.total)} / {bucket.calls:,} 次"
                f"（失败 {bucket.failed}，图 {bucket.images}，"
                f"缓存 {cache_hit_text(bucket.usage)}）"
            )
        if len(sub) > 5:
            lines.append(f"    · 其余 {len(sub) - 5} 个模型略")
    return "\n".join(lines)


def cache_hit_text(usage: Usage, empty: str = "—") -> str:
    """生成指令与兼容 API 共用的缓存命中率文本。

    缓存未上报或没有输入分母时返回 empty，避免将未知显示为 0%。
    """
    # 正缓存计数本身就是上报证据，即使原始响应缺少缓存字段也应显示。
    if usage.cache_reported <= 0 and usage.input_cached <= 0 and usage.input_other > 0:
        return empty
    total_in = usage.input_other + usage.input_cached
    if total_in <= 0:
        return empty
    return f"{usage.input_cached / total_in:.0%}"


def cache_hit_rate(bucket: Bucket) -> float:
    """缓存命中率。它是「省下来了」的正向指标，不是一个价格系数。"""
    total_in = bucket.usage.input_other + bucket.usage.input_cached
    if total_in <= 0:
        return 0.0
    return bucket.usage.input_cached / total_in


def _value(obj: Any, name: str) -> Any:
    """Read raw SDK objects and dictionary payloads without serializing content."""
    try:
        return obj.get(name) if isinstance(obj, Mapping) else getattr(obj, name, None)
    except Exception:  # noqa: BLE001
        return None


def _raw_usage(response: Any) -> Any:
    completion = _value(response, "raw_completion")
    for name in ("usage", "usage_metadata", "usageMetadata"):
        usage = _value(completion, name)
        if usage is not None:
            return usage
    return None


def _counter(obj: Any, *paths: tuple[str, ...]) -> int | None:
    for path in paths:
        value = obj
        for name in path:
            value = _value(value, name)
        # Unknown/malformed counters must not become a fabricated zero.
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and value >= 0:
            return value
    return None


def reports_cache(response: Any) -> bool:
    """Whether a raw response explicitly reports cache hits, including zero."""
    return _counter(
        _raw_usage(response),
        ("prompt_tokens_details", "cached_tokens"),
        ("input_tokens_details", "cached_tokens"),
        ("prompt_cache_hit_tokens",),
        ("cache_read_input_tokens",),
        ("cached_content_token_count",),
        ("cachedContentTokenCount",),
    ) is not None


def usage_from(raw: Any, response: Any = None) -> Usage:
    """Copy normalized totals; raw evidence supplies independent detail counters.

    Reasoning is usually part of output and cache writes can be part of input.
    Neither is added to the established three-column total.
    """
    usage = _raw_usage(response)
    cached = int(_value(raw, "input_cached") or 0)
    return Usage(
        input_other=int(_value(raw, "input_other") or 0),
        input_cached=cached,
        output=int(_value(raw, "output") or 0),
        cache_reported=int(cached > 0 or reports_cache(response)),
        reasoning=_counter(
            usage,
            ("completion_tokens_details", "reasoning_tokens"),
            ("output_tokens_details", "reasoning_tokens"),
            ("reasoning_tokens",),
            ("thoughts_token_count",),
            ("thoughtsTokenCount",),
        ),
        cache_write=_counter(
            usage,
            ("cache_creation_input_tokens",),
            ("cache_write_tokens",),
            ("prompt_tokens_details", "cache_write_tokens"),
            ("input_tokens_details", "cache_write_tokens"),
        ),
        # Raw detail metadata alone does not establish normalized input/output totals.
        usage_reported=int(raw is not None),
    )
