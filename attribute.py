"""从模块名识别来源插件、提取会话标识并测量请求构成。

调用栈由 main.py 提供，本模块不依赖 AstrBot。插件归因使用
data.plugins.<插件目录名> 前缀，内置 star 归为主对话。
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
    """测量 system、历史、prompt 和工具定义的长度，不保留正文。

    非字符串用 str() 的长度近似，包括 tools；这些长度不是 Token 数。
    另记录历史条数、工具数、图片数、音频数与耗时。
    """

    def width(value: Any) -> int:
        if not value:
            return 0
        if isinstance(value, str):
            return len(value)
        try:
            return len(str(value))
        except Exception:  # noqa: BLE001；长度测量失败按 0 处理，不中断记账
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
"""AstrMessageEvent 上的会话标识，格式为 platform_name:message_type:session_id。"""


def session_from_locals(locals_seq: Iterable[object]) -> str:
    """从调用栈局部变量中查找 unified_msg_origin，补充调用参数缺失的会话标识。

    Args:
        locals_seq: 各层栈帧的局部变量，由内向外。

    Returns:
        首个非空会话标识，或 NO_SESSION。后台任务栈上没有 event 时无法取得标识。
    """
    for locals_dict in locals_seq:
        if not isinstance(locals_dict, dict):
            continue
        for value in locals_dict.values():
            try:
                umo = getattr(value, SESSION_ATTR, None)
            except Exception:  # noqa: BLE001
                # 属性访问可能抛异常，跳过该对象以继续查找
                continue
            if isinstance(umo, str) and umo.strip():
                return umo.strip()
    return NO_SESSION


def plugin_from_modules(modules: Iterable[str], ignore: Iterable[str] = ()) -> str:
    """从调用栈的模块名序列里挑出插件目录名。

    Args:
        modules: 模块名，由内向外。
        ignore: 要跳过的插件目录名。调用方须包含本插件目录名，
            避免将包装函数所在模块误认为调用来源。

    Returns:
        插件目录名，例如 astrbot_plugin_image_gate；
        内置 star 和找不到的情况一律归 MAIN_AGENT。

    从内向外扫描，选择离 provider 调用最近的未忽略插件模块；
    先遇到内置 star 或没有匹配时返回 MAIN_AGENT。
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
            # 内置 star 归入主对话
            return MAIN_AGENT
    return MAIN_AGENT

def provider_id_from_config(cfg: Any, fallback: str) -> str:
    """从 provider_config 取 id；字典缺少 id 键时返回 default。

    cfg 不是字典或 id 为空时使用 fallback（调用方传入的类名）。
    保留缺键与空值的区别，避免连接标识与宿主日志不一致。
    """
    if isinstance(cfg, dict):
        if "id" in cfg:
            value = str(cfg.get("id") or "").strip()
            if value:
                return value
        else:
            return "default"
    return fallback
