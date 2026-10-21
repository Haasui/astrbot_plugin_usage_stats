"""结构不变量自检：把「三条自律」里能静态确认的部分钉住。

    python tests/test_invariants.py

`main.py` 依赖 AstrBot，跑不了普通单测。但它的**形状**能解析，而形状正是后续改动
最容易破坏的地方：

- `_record` 是不是整个包在 try 里、except 有没有偷偷往上抛（记账不能伤对话）
- `terminate` 有没有调 `restore()`（卸载是越界唯一的退路）
- 有没有出现 `delattr`（那条路会真的删掉别人的方法）
- `enabled` 检查是不是排在打补丁之前（默认关闭）
- 记账有没有写 provider 的属性（只读不改）

这些检查只读语法树，不执行代码，所以不会因为 AstrBot 不在就跳过。
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "main.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)
CLASS = next(
    node
    for node in TREE.body
    if isinstance(node, ast.ClassDef) and node.name == "UsageStatsPlugin"
)


def _method(name):
    for node in CLASS.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"main.py 里找不到 {name}()")


def _strip_docstring(body):
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return body[1:]
    return body


def _walk_no_nested(node):
    """遍历但不下进嵌套函数 —— 那里面的 raise 是它自己的事。"""
    stack = [node]
    while stack:
        cur = stack.pop()
        yield cur
        for child in ast.iter_child_nodes(cur):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            stack.append(child)


def _calls_named(node, attr):
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
            if sub.func.attr == attr:
                return True
    return False


def _raises(handler):
    return any(isinstance(n, ast.Raise) for n in _walk_no_nested(handler))


def _mentions(node, name):
    """名字或属性都算。self._cfg 是 Attribute，只查 Name 会漏。"""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id == name:
            return True
        if isinstance(sub, ast.Attribute) and sub.attr == name:
            return True
    return False


def test_record_body_is_fully_wrapped():
    body = _strip_docstring(_method("_record").body)
    assert len(body) == 1 and isinstance(body[0], ast.Try), (
        "_record 的函数体应该只有一条 try —— 少包一层，记账异常就会往上冒，"
        "而它是在 provider 的调用路径里跑的"
    )
    for handler in body[0].handlers:
        assert not _raises(handler), "except 里不能重新抛出"


def test_record_resets_reentrancy_guard():
    outer = _strip_docstring(_method("_record").body)[0]
    inner = next((s for s in outer.body if isinstance(s, ast.Try)), None)
    assert inner is not None, "内层还要有一个 try，用来 finally 复位重入标记"
    assert inner.finalbody, "内层 try 必须有 finally"
    assert _calls_named(inner.finalbody[0], "reset"), "finally 里要 reset 重入标记"


def test_record_does_not_write_provider_attributes():
    for node in _walk_no_nested(_method("_record")):
        targets = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        for target in targets:
            assert not _mentions(target, "provider"), (
                "记账只读 provider，不许给它赋值 —— 那是「只读不改」那条自律"
            )


def test_terminate_restores_patches():
    assert _calls_named(_method("terminate"), "restore"), (
        "卸载必须调用 registry.restore()，这是越界唯一的退路"
    )


def test_terminate_cancels_refresh_task():
    assert _calls_named(_method("terminate"), "cancel"), "卸载要停掉重扫任务"


def test_no_delattr_anywhere():
    offenders = [
        n.attr for n in ast.walk(TREE) if isinstance(n, ast.Attribute)
    ] + [n.id for n in ast.walk(TREE) if isinstance(n, ast.Name)]
    assert "delattr" not in offenders, (
        "不许用 delattr 还原 —— 那会真的删掉类上的方法，别人可能还在用"
    )


def test_enabled_check_precedes_patching():
    stmts = _strip_docstring(_method("initialize").body)
    guard = None
    hooked = None
    for index, stmt in enumerate(stmts):
        if isinstance(stmt, ast.If) and _mentions(stmt.test, "_cfg") and "enabled" in ast.dump(stmt.test):
            assert any(isinstance(s, ast.Return) for s in stmt.body), (
                "enabled=false 那条分支必须提前返回，不能只是打印一句"
            )
            guard = index
        if _calls_named(stmt, "_hook_providers"):
            hooked = index
    assert guard is not None, "initialize 里应该有 enabled 检查"
    assert hooked is not None, "initialize 里应该调用 _hook_providers"
    assert guard < hooked, "enabled 检查必须排在打补丁之前 —— 默认关闭是三条自律之一"


def test_save_swallows_errors():
    tries = [n for n in ast.walk(_method("_save")) if isinstance(n, ast.Try)]
    assert tries, "_save 要有 try"
    assert any(_calls_named(t, "save") for t in tries), "写盘必须在 try 里"
    for block in tries:
        for handler in block.handlers:
            assert not _raises(handler), "写盘失败不能往上抛 —— 它跑在记账路径里"


def test_cache_hit_goes_through_one_helper():
    """缓存命中的渲染必须走 stats.cache_hit_text。

    之前三层各写各的：插件级在没有输入 token 时给「—」，模型级和 /usage 给「0%」。
    同一个格子两种写法，会让人把「没跑过缓存」读成「缓存全 miss」。
    """
    offenders = sorted(
        {
            node.lineno
            for node in ast.walk(TREE)
            if (isinstance(node, ast.Attribute) and node.attr == "cache_hit_rate")
            or (isinstance(node, ast.Name) and node.id == "cache_hit_rate")
        }
    )
    assert not offenders, (
        f"main.py 第 {offenders} 行还在直接用 cache_hit_rate；"
        "缓存命中的文本一律走 cache_hit_text"
    )


def test_save_prunes_memory_baseline():
    """内存基线要跟着裁剪，否则会话中看到的天数比重启后多。

    落盘写的是裁剪后的数据，但 `_baseline` 不裁的话，本次会话里仍然看得到
    已经开始丢弃的天 —— 重启之后它们才消失。看到的东西应该等于留存下来的东西。
    """
    assigns = [
        node
        for node in _walk_no_nested(_method("_save"))
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Attribute) and target.attr == "_baseline"
            for target in node.targets
        )
    ]
    assert assigns, "_save 里要把裁剪后的结果写回 self._baseline"


def test_refresh_loop_tolerates_cancel():
    found = False
    for block in [n for n in ast.walk(_method("_refresh_loop")) if isinstance(n, ast.Try)]:
        for handler in block.handlers:
            if handler.type and "CancelledError" in ast.dump(handler.type):
                found = True
    assert found, (
        "重扫任务要单独接住 CancelledError —— 它继承自 BaseException，"
        "只写 except Exception 接不到，卸载时会抛出噪音"
    )


def test_both_provider_methods_are_targeted():
    for node in ast.walk(_method("_hook_providers")):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "collect_targets":
                names = [e.value for e in node.args[1].elts]
                assert names == ["text_chat", "text_chat_stream"], (
                    f"补丁目标应是 text_chat 与 text_chat_stream，实际 {names}"
                )
                return
    raise AssertionError("找不到 collect_targets 调用")


def _string_constants(function):
    """函数体里出现过的所有字符串常量。"""
    return {
        node.value
        for node in ast.walk(function)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def test_day_window_has_one_computation() -> None:
    """「最近 N 天」的下限只能算一处。

    曾经 main.py 里有两份：视图用 days * 86400，/usage 用 (days - 1) * 86400，
    于是点「今天」出来两天。两份各自看都对，合起来错 —— 这类只能靠形状拦。
    """
    assert "time.time() - days * 86400" not in SOURCE, (
        "又自己算了一遍天窗；改用 stats.cutoff_for_days"
    )
    assert SOURCE.count("cutoff_for_days(") >= 2, "两处取数都应该走同一个换算"


def test_help_lists_every_handled_subcommand() -> None:
    """help 和分发是两份同样的清单，必须一致。

    少写一条，用户就不知道那条指令存在；反过来写了没实现，就是骗人。
    这类「同一份事实存了两份」的副本，是这个项目里最常复发的一类问题。
    """
    handled = _string_constants(_method("usage_status")) | _string_constants(
        _method("_usage_scope")
    )
    help_text = "\n".join(_string_constants(_method("_help_text")))

    for name in ("full", "day", "week", "status", "help"):
        assert name in handled, f"分发里没有 {name}"
        assert f"/usage {name}" in help_text, f"help 里漏了 {name}"

    # 反方向：help 里提到的子命令都必须真的认
    for name in re.findall(r"^/usage ([a-z]+)", help_text, re.MULTILINE):
        assert name in handled, f"help 承诺了 {name}，分发却不认"


def test_save_clears_the_call_buffer() -> None:
    """_save 成功后必须清空 _calls。

    保存时合并的是「基线 + _calls 全量」，成功后基线就变成那个合并结果。
    如果这时不清空 _calls，下一次保存会把同一批调用再加一遍——每 50 条存一次，
    存 N 次就放大 N 倍。这个 bug 真的发生过：一天记了 5 万次调用、4 亿 token，
    而供应商账单只有 1,370 万，差 29 倍。
    """
    save = _method("_save")
    clears = [
        node
        for node in ast.walk(save)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "clear"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "_calls"
    ]
    assert clears, "_save 里没有 self._calls.clear()：保存一次就会把同一批调用再累加一遍"


def test_run_counter_is_separate_from_the_buffer() -> None:
    """「本次运行记录了多少次」要有独立计数器。

    _calls 每次保存后清空，拿它当计数器用，状态里那个数会在第一次保存后归零。
    """
    assert "self._run_calls" in SOURCE
    init = _method("__init__")
    names = {
        node.attr
        for node in ast.walk(init)
        if isinstance(node, ast.Attribute) and node.attr.startswith("_run")
    }
    assert "_run_calls" in names, "__init__ 里要初始化 self._run_calls"


def main() -> None:
    cases = [
        test_save_clears_the_call_buffer,
        test_run_counter_is_separate_from_the_buffer,
        test_record_body_is_fully_wrapped,
        test_record_resets_reentrancy_guard,
        test_record_does_not_write_provider_attributes,
        test_terminate_restores_patches,
        test_terminate_cancels_refresh_task,
        test_no_delattr_anywhere,
        test_enabled_check_precedes_patching,
        test_save_swallows_errors,
        test_cache_hit_goes_through_one_helper,
        test_save_prunes_memory_baseline,
        test_refresh_loop_tolerates_cancel,
        test_both_provider_methods_are_targeted,
        test_day_window_has_one_computation,
        test_help_lists_every_handled_subcommand,
    ]
    for case in cases:
        case()
        print(f"  ok  {case.__name__}")
    print(f"{len(cases)} 组用例全部通过")


if __name__ == "__main__":
    main()
