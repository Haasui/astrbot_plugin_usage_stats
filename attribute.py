"""归因：这次模型调用是谁发起的。

**这一层是纯逻辑**——只做一件事：给一串模块名，挑出插件名。
调用栈的获取放在 main.py，这样本模块能脱离 AstrBot 单独测。

依据（已核实）：astrbot/core/star/star_manager.py:724
    prefix = "astrbot.builtin_stars." if is_reserved else "data.plugins."
插件模块名必然是 data.plugins.<插件目录名>. 开头。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .stats import Shape

PLUGIN_PREFIX = "data.plugins."
BUILTIN_PREFIX = "astrbot.builtin_stars."

MAIN_AGENT = "main_agent"

NO_SESSION = "no_session"
"""插件发起的调用拿不到会话时落这里。"""

def measure_request(kwargs: Mapping[str, Any], elapsed_ms: int = 0) -> Shape:
    """量一次请求的构成。**只数长度，不读内容** —— 日志里不许出现正文。

    这几个字段是「为什么这次这么肥」的答案：
    system 是人格、ctx 是历史（轮数 + 总字符）、prompt 是当前这一句（含插件
    注入进去的东西）、tools 是所有插件注册的工具定义 —— 工具定义每轮都重发，
    却从来没人算它。

    tools 是 str() 的近似长度，不是 token 数；用来比大小够用。
    """

    def width(value: Any) -> int:
        if not value:
            return 0
        if isinstance(value, str):
            return len(value)
        try:
            return len(str(value))
        except Exception:  # noqa: BLE001 —— 量不出来就当 0，别影响记账
            return 0

    contexts = kwargs.get("contexts") or []
    try:
        turns = len(contexts)
        context_chars = sum(width(item) for item in contexts)
    except TypeError:  # 不是序列就放弃这一项
        turns, context_chars = 0, 0

    tool = kwargs.get("func_tool")
    tool_count = 0
    if tool is not None:
        try:
            tool_count = len(getattr(tool, "tools", tool) or [])
        except Exception:  # noqa: BLE001
            tool_count = 0

    return Shape(
        system=width(kwargs.get("system_prompt")),
        turns=turns,
        context=context_chars,
        prompt=width(kwargs.get("prompt")),
        tools=width(tool),
        tool_count=tool_count,
        images=len(kwargs.get("image_urls") or []),
        audio=len(kwargs.get("audio_urls") or []),
        ms=int(elapsed_ms or 0),
    )


SESSION_ATTR = "unified_msg_origin"
"""AstrMessageEvent 上的会话标识（astr_message_event.py:106）。
格式 platform_name:message_type:session_id。"""


def session_from_locals(locals_seq: Iterable[object]) -> str:
    """从调用栈的局部变量里捞出会话标识。

    为什么需要它：主对话那条路上，会话是经 kwargs 传进 provider 的
    （tool_loop_agent_runner.py:514 的 conversation_id）；但插件调用的
    llm_generate 签名里根本没有会话参数（context.py:175-186）。

    好在做归因时本来就要遍历调用栈——顺手看看谁手里攥着 AstrMessageEvent 就行。
    那个对象带 unified_msg_origin。

    Args:
        locals_seq: 各层栈帧的局部变量，由内向外。

    Returns:
        会话标识（umo），或 NO_SESSION。

    已知局限：插件若在**后台任务**里调模型（定时任务、主动能力），栈上没有 event，
    只能落 NO_SESSION。
    """
    for locals_dict in locals_seq:
        if not isinstance(locals_dict, dict):
            continue
        for value in locals_dict.values():
            try:
                umo = getattr(value, SESSION_ATTR, None)
            except Exception:  # noqa: BLE001
                # 有些对象的属性是 property，取值可能抛——不能让归因挂掉
                continue
            if isinstance(umo, str) and umo.strip():
                return umo.strip()
    return NO_SESSION


def plugin_from_modules(modules: Iterable[str], ignore: Iterable[str] = ()) -> str:
    """从调用栈的模块名序列里挑出插件目录名。

    Args:
        modules: 模块名，**从内向外**（第一个是 text_chat 的直接调用者）。
        ignore: 要跳过的插件目录名。**调用方必须把自己传进来** —— 补丁的包装函数
            就活在本插件的模块里，那是栈上最靠近 text_chat 的 `data.plugins.*`，
            不跳过的话每一次调用都会被算到自己头上。

    Returns:
        插件目录名，例如 astrbot_plugin_image_gate；
        内置 star 和找不到的情况一律归 MAIN_AGENT。

    为什么要从内向外扫：插件调用链是

        Provider.text_chat
          ↑ astrbot.core.star.context        (llm_generate)
          ↑ data.plugins.<插件>.xxx          ← 要的就是它

    主对话那条链上不会出现 data.plugins.*（实际调用发生在 agent runner 里，
    插件的处理器早就返回了），所以自然落到 MAIN_AGENT，不需要额外判断。
    """
    skip = {item for item in ignore if item}
    for name in modules:
        if not name:
            continue
        if name.startswith(PLUGIN_PREFIX):
            head = name[len(PLUGIN_PREFIX) :].split(".", 1)[0]
            if head and head not in skip:
                return head
        if name.startswith(BUILTIN_PREFIX):
            # 内置 star 不是插件，归因没有意义
            return MAIN_AGENT
    return MAIN_AGENT

def provider_id_from_config(cfg: Any, fallback: str) -> str:
    """从 provider 的 `provider_config` 里取 id。

    缺失时和 AstrBot 自己保持一致：`provider.py:54` 用的是
    `self.provider_config.get("id", "default")`，所以键不在时叫 `default`。
    不一致的话，同一个 provider 在 AstrBot 的日志里叫 `default`、
    在我们的表里叫类名，两边对不上号。

    `cfg` 不是 dict（脱离了 manager 手工构造的 provider）或 id 是空串时，
    才退到 `fallback`（调用方传类名）。
    """
    if isinstance(cfg, dict):
        if "id" in cfg:
            value = str(cfg.get("id") or "").strip()
            if value:
                return value
        else:
            return "default"
    return fallback
