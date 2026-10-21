"""视图源码的约束：插件页跑在受限 iframe 里，模态框会被直接拦掉。

浏览器对 iframe 里的 `confirm` / `alert` / `prompt` 会返回 false（不弹窗、不报错）。
0.2.6 的「重置」就是因为这个才写成按钮自身的两步确认：`window.confirm` 返回 false，
处理函数第一行就 return，点了什么都不会发生，连报错都没有。

所以：需要确认的地方一律做成按钮自身的两步确认（见 `app.js` 里的 reset 按钮）。

这条只能查源码 —— 它约束的是「别用哪个 API」，不是某次运行的结果。
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
VIEWS = ROOT / "views" / "usage"
DIALOG = re.compile(r"\b(?:window\.)?(?:confirm|alert|prompt)\s*\(")


def test_views_do_not_open_blocking_dialogs() -> None:
    for name in ("app.js", "index.html"):
        text = (VIEWS / name).read_text(encoding="utf-8")
        hit = DIALOG.search(text)
        assert not hit, f"{name} 里出现了模态框调用：{hit.group(0)}"


def main() -> None:
    cases = [test_views_do_not_open_blocking_dialogs]
    for case in cases:
        case()
        print(f"  ok  {case.__name__}")
    print(f"{len(cases)} 组用例全部通过")


if __name__ == "__main__":
    main()
