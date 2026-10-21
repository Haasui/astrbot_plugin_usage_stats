"""纯逻辑：用量记录的累加与聚合。不 import AstrBot，可单独测。

字段名刻意对齐 AstrBot 的 TokenUsage（input_other / input_cached / output），
但本模块不依赖它——边界转换在 main.py 里做。
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

MAIN_AGENT = "main_agent"
"""归因不到插件时的归属：主对话，或内置 star。"""

NO_SESSION = "no_session"
"""插件发起的调用拿不到会话（见 docs/usage-attribution.md 第 5 节）。"""


@dataclass(frozen=True)
class Usage:
    """一次调用的 token 用量。

    缓存命中单独一栏——它和普通输入的价差可能是几十倍，
    混在一起算出来的钱是错的。这和 AstrBot 的 TokenUsage 是同一种切法。
    """

    input_other: int = 0
    input_cached: int = 0
    output: int = 0
    cache_reported: int = 0
    """有多少次调用的响应里**真的带了**缓存字段。

    和中转站的计价表是两回事：表里没有「缓存命中」这一行的供应商，返回的 usage
    里通常也没有 cached_tokens。这种记成 0，界面上要显示「—」而不是「0%」——
    0% 会被读成「命中了 0 次」，而事实是「根本没告诉过我们」。
    """

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_other + other.input_other,
            self.input_cached + other.input_cached,
            self.output + other.output,
            self.cache_reported + other.cache_reported,
        )

    @property
    def total(self) -> int:
        return self.input_other + self.input_cached + self.output


@dataclass(frozen=True)
class Shape:
    """一次请求的**构成**，全是字符数，不是 token。

    用来回答「为什么这次是 42K」：system、历史、当前 prompt、工具定义各自多肥。
    只数长度，不读内容 —— 日志里不许出现正文。
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
    # 会话只收集、不聚合：遍历调用栈本来就要做（为了归因），顺手捞出来的东西几乎免费。
    # 展示走 debug 日志，不进 /usage——那儿要保持一屏看完。以后想按会话聚合不用重写。
    session: str = NO_SESSION
    shape: Shape | None = None

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

    def add(self, call: Call) -> None:
        self.calls += 1
        if not call.ok:
            self.failed += 1
        self.images += call.images
        self.usage = self.usage + call.usage

    def to_dict(self) -> dict:
        return {
            "calls": self.calls,
            "failed": self.failed,
            "images": self.images,
            "input_other": self.usage.input_other,
            "input_cached": self.usage.input_cached,
            "output": self.usage.output,
            "total": self.usage.total,
        }


def fold_by_model(calls: Iterable[Call]) -> dict[str, dict[str, Bucket]]:
    """plugin -> 模型。回答「谁最贵」和「它在用什么模型」，不分时间。"""
    out: dict[str, dict[str, Bucket]] = defaultdict(lambda: defaultdict(Bucket))
    for call in calls:
        out[call.plugin][call.model_key].add(call)
    return {plugin: dict(models) for plugin, models in out.items()}


def tidy_model_key(key: str) -> str:
    """把 `provider|model` 里重复的模型名去掉，**只给展示用**。

    存的键不动 —— 改了键会让旧数据对不上。这里只处理显示：

        openai/deepseek-ai/DeepSeek-R1-0528-Qwen3-8B|deepseek-ai/DeepSeek-R1-0528-Qwen3-8B
        → openai/deepseek-ai/DeepSeek-R1-0528-Qwen3-8B

    provider id 末尾已经带上模型名时，再拼一遍纯属噪音。
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
    """plugin -> 天 -> 模型。

    落盘用这个形状：时间维度是账单要的，模型维度是「谁在用贵模型」要的。
    """
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
                    images=bucket.images,
                    usage=bucket.usage,
                )
                continue
            current.calls += bucket.calls
            current.failed += bucket.failed
            current.images += bucket.images
            current.usage = current.usage + bucket.usage


def total_usage(calls: Iterable[Call]) -> Usage:
    acc = Usage()
    for call in calls:
        acc = acc + call.usage
    return acc


def cutoff_for_days(days: int, now: float | None = None, tz_offset_hours: int = 8) -> str:
    """「最近 N 天」的天键下限。**N=1 就是只有今天**，不是「今天加昨天」。

    这个换算是**一处**。它曾经在 /usage 和 WebUI 视图里各写一份，一份用
    days * 86400、另一份用 (days - 1) * 86400 —— 于是点「今天」出来两天，
    而两边的代码各自看都对。留着两份就是给下一次漂移留门。
    """
    if days <= 0:
        return ""
    base = now if now is not None else time.time()
    return day_key(base - (days - 1) * 86400, tz_offset_hours)


def render_call_line(call: Call, tz_offset_hours: int = 8) -> str:
    """一行一笔调用，键=值。

    grep 找得出「哪次特别肥」，awk 算得出分布；人眼也读得懂。
    分隔符只用空格，值里不会出现空格（会话名是冒号分隔的）。
    """
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
    """大数字压成 1.2M / 345.6K。命令行里动辄九位数，一半行宽都给它占了。"""
    number = float(value or 0)
    for unit, size in (("B", 10**9), ("M", 10**6), ("K", 10**3)):
        if abs(number) >= size:
            return f"{number / size:.1f}{unit}"
    return str(int(number))


def fold_by_model_across(
    view: Mapping[str, Mapping[str, Bucket]],
) -> list[tuple[str, Bucket]]:
    """插件 → 模型 → 格 摊平成「模型 → 合计」，按 token 降序。

    快捷指令要的是「哪些模型花了多少」，不是「哪个插件花了多少」。同一个模型在
    不同插件下会被合并 —— 这个口径里的占比和命中率才是可比的，因为单位相同。
    跨**模型**比 token 占比则没有意义：同一个 token 在不同模型上价钱差几十倍。
    """
    merged: dict[str, Bucket] = {}
    for models in view.values():
        for key, bucket in models.items():
            current = merged.get(key)
            if current is None:
                merged[key] = Bucket(
                    calls=bucket.calls,
                    failed=bucket.failed,
                    images=bucket.images,
                    usage=bucket.usage,
                )
                continue
            current.calls += bucket.calls
            current.failed += bucket.failed
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
    """快捷输出：一行一个模型，按 token 降序。

    只给调用数、token、缓存命中 —— 插件拆分在明细里，运行期计数在状态里。
    那两样是不同口径（本次运行 vs 落盘历史 + 本次），混在一屏会让人以为数字对不上。
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
    """缓存命中的展示文本。没有输入 token 时给 `empty`，**不写 0%**。

    0% 是「测了，没命中」；—是「这一格没有分母」。同一个状态两种写法，
    会让人把「没跑过缓存」读成「缓存全 miss」。

    `/usage`、API 的插件级、API 的模型级都走这一个函数 —— 之前它们各写各的，
    结果同一个格子插件行显示「—」、模型行显示「0%」。
    """
    # 供应商压根没上报缓存字段：显示「—」而不是「0%」。
    # 0% 会被读成「命中了 0 次」，事实是「没告诉我们」。
    #
    # 但只要有命中数，就说明这个字段在 —— 缓存不会凭空出现。这一条比
    # cache_reported 可靠：后者靠扒 raw_completion，流式响应的那一栏常常是空的，
    # 于是每次调用都判成「没上报」，整列显示「—」，而右边「缓存」明明有数字。
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


def reports_cache(response: Any) -> bool:
    """供应商的原始响应里有没有缓存命中字段。

    只看 AstrBot 归一化后的 `TokenUsage` 判断不出来：没上报和上报了 0 都是 0。
    所以要扒 `raw_completion` 的 usage——那里才看得出字段在不在。

    注意它只是**辅助**证据：流式响应的 `raw_completion` 常常取不到，这里会一路
    False。真正可靠的是命中数本身（见 `usage_from`）。
    """
    raw = getattr(response, "raw_completion", None)
    usage = getattr(raw, "usage", None)
    if usage is None:
        return False
    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None and getattr(details, "cached_tokens", None) is not None:
        return True
    # DeepSeek / Anthropic 把命中数放在别的字段名下
    for attr in ("prompt_cache_hit_tokens", "cache_read_input_tokens"):
        if getattr(usage, attr, None) is not None:
            return True
    return False


def usage_from(raw: Any, response: Any = None) -> Usage:
    """AstrBot 的 TokenUsage -> 本插件的 Usage。

    字段名是同一种切法（缓存命中单独一栏），所以这里是逐字段搬运。
    provider 没填 usage 时 raw 是 None，记 0 但调用次数照记。
    """
    if raw is None:
        return Usage()
    cached = int(getattr(raw, "input_cached", 0) or 0)
    return Usage(
        input_other=int(getattr(raw, "input_other", 0) or 0),
        input_cached=cached,
        output=int(getattr(raw, "output", 0) or 0),
        # 命中数为正本身就是「这家上报了」的证据 —— 缓存数不会凭空出现。
        # 只看 reports_cache 不够：它要扒 raw_completion，而流式响应的那一栏
        # 常常取不到，于是每次都判成「没上报」，命中率整列变成「—」。
        cache_reported=1 if (cached > 0 or reports_cache(response)) else 0,
    )