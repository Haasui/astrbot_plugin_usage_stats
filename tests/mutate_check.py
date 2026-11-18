"""变异检查：逐条破坏结构或配置契约，确认对应用例真的会红。

    python tests/mutate_check.py

一个永远通过的测试等于没有测试。这里不碰工作区里真正的源码 ——
每次变异都写到 ../_mut_check/ 下再跑。只覆盖那几个文件，不堆目录。
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys

SRC = pathlib.Path(__file__).resolve().parents[1]
# 变异体写在插件目录外面，不污染源码；这个目录可以随时删
SANDBOX = SRC.parent / "_mut_check"
PY = sys.executable

# 版本号变异不能把版本字面量写进锚点：发一版它就哑一条，而哑掉的方式是
# 静默的「锚点找不到」—— 堆在输出里没人看。这里每次现读真版本。
REAL_VERSION = re.search(
    r"^version:\s*(\S+)\s*$", (SRC / "metadata.yaml").read_text(encoding="utf-8"), re.M
).group(1)

INVARIANTS = "tests/test_invariants.py"
CONFIG = "tests/test_config.py"
VIEW_SMOKE = "tests/view_smoke.mjs"
VIEW = "views/usage/app.js"
STORE_TEST = "tests/test_store.py"
CORE_TEST = "tests/test_core.py"
PATCH_TEST = "tests/test_patch.py"
CONTRACT = "tests/test_contract.py"

# (说明, 改哪个文件, 旧文本, 新文本, 跑哪个测试, 期望报红的用例)
MUTATIONS = [
    ("except 里重新抛出", "main.py",
     "            self._record_failures += 1",
     "            self._record_failures += 1\n            raise",
     INVARIANTS, "test_record_body_is_fully_wrapped"),
    ("finally 不复位重入标记", "main.py",
     "            finally:\n                _DEPTH.reset(token)",
     "            finally:\n                pass",
     INVARIANTS, "test_record_resets_reentrancy_guard"),
    ("记账时写 provider 属性", "main.py",
     '                provider_id = provider_id_from_config(',
     '                provider.model = "x"\n                provider_id = provider_id_from_config(',
     INVARIANTS, "test_record_does_not_write_provider_attributes"),
    ("卸载不还原补丁", "main.py",
     "        restored = self._registry.restore()",
     "        restored = []",
     INVARIANTS, "test_terminate_restores_patches"),
    ("卸载不停重扫任务", "main.py",
     "            self._refresh_task.cancel()",
     "            pass",
     INVARIANTS, "test_terminate_cancels_refresh_task"),
    ("用 delattr 还原", "main.py",
     "PLUGIN_NAME = ",
     'delattr(object, "x")\n\nPLUGIN_NAME = ',
     INVARIANTS, "test_no_delattr_anywhere"),
    ("enabled 检查被绕开", "main.py",
     '        if not self._cfg("enabled", False):',
     "        if False:",
     INVARIANTS, "test_enabled_check_precedes_patching"),
    ("写盘异常往上抛", "main.py",
     "        except Exception:  # noqa: BLE001\n            self._save_failures += 1",
     "        except Exception:  # noqa: BLE001\n            self._save_failures += 1\n            raise",
     INVARIANTS, "test_save_swallows_errors"),
    ("不接 CancelledError", "main.py",
     "            except asyncio.CancelledError:\n                return\n",
     "",
     INVARIANTS, "test_refresh_loop_tolerates_cancel"),
    ("漏挂 text_chat_stream", "main.py",
     '("text_chat", "text_chat_stream")',
     '("text_chat",)',
     INVARIANTS, "test_both_provider_methods_are_targeted"),
    ("schema 里多一个没人读的开关", "_conf_schema.json",
     '  "debug": {',
     '  "mystery_switch": { "description": "没人读", "type": "bool", "default": false },\n  "debug": {',
     CONFIG, "test_schema_keys_are_all_read"),
    ("代码读了 schema 里没有的键", "main.py",
     'self._cfg("keep_days", 60)',
     'self._cfg("keepdays", 60)',
     CONFIG, "test_code_keys_all_exist_in_schema"),
    ("两边默认值不一致", "_conf_schema.json",
     '"default": 20000',
     '"default": 9999',
     CONFIG, "test_defaults_agree"),
    ("说明里写开发进度", "_conf_schema.json",
     '"description": "落盘保留多少天"',
     '"description": "落盘保留多少天，尚未实现"',
     CONFIG, "test_hints_do_not_describe_progress"),
    # --- 视图层的三条回归：它们对应的错误肉眼看不出来，只有测试能拦 ---
    ("空范围复活旧表", VIEW,
     '    last = { groups: groups, grand: grand, mode: mode };\n    applySortIndicator();\n\n    if (!groups.length) {\n      $("table").hidden = true;\n      $("table-scroll").hidden = true;\n      $("table-scroll-hint").hidden = true;\n      $("empty").hidden = false;\n      return;\n    }\n    $("empty").hidden = true;\n    $("table-scroll").hidden = false;\n    $("table-scroll-hint").hidden = false;\n    $("table").hidden = false;\n    groups = sortRows(groups, state.sort);',
     '    if (!groups.length) {\n      $("table").hidden = true;\n      $("table-scroll").hidden = true;\n      $("table-scroll-hint").hidden = true;\n      $("empty").hidden = false;\n      return;\n    }\n    $("empty").hidden = true;\n    $("table-scroll").hidden = false;\n    $("table-scroll-hint").hidden = false;\n    $("table").hidden = false;\n\n    last = { groups: groups, grand: grand, mode: mode };\n    applySortIndicator();\n    groups = sortRows(groups, state.sort);',
     VIEW_SMOKE, "空范围下点表头没有复活旧表"),
    ("旧响应盖掉新响应", VIEW,
     "        if (seq !== loadSeq) return;   // 有更新的请求在飞，这次的结果丢掉\n        render(data);",
     "        render(data);",
     VIEW_SMOKE, "竞态：旧响应没盖掉新响应"),
    ("首屏失败骨架不收", VIEW,
     '        if (!state.data) clearSkeleton("取不到数据：" + err);\n',
     "",
     VIEW_SMOKE, "首屏失败：骨架收掉并写明原因"),
    ("收起状态没写回", VIEW,
     '        if (wasOpen) state.collapsed[plugin.key] = true;\n        else delete state.collapsed[plugin.key];\n',
     "",
     VIEW_SMOKE, "刷新后收起状态还在"),
    ("焦点失效时静默降级", VIEW,
     '      modelRow = (pluginRow.models || []).filter(function (m) {\n        return m.name === state.focusModel;\n      })[0] || null;\n    }',
     '      modelRow = (pluginRow.models || []).filter(function (m) {\n        return m.name === state.focusModel;\n      })[0] || null;\n      if (!modelRow) state.focusModel = "";\n    }',
     VIEW_SMOKE, "模型没记录时保留下钻并写明原因"),
    ("下钻只认原始键", "store.py",
     '            if model and name != model and tidy_model_key(name) != model:',
     '            if model and name != model:',
     STORE_TEST, "test_series_for_narrows_to_plugin_and_model"),
    ("as_buckets 丢掉已有对象", "store.py",
     '                if isinstance(cell, Bucket):\n                    # 已经是内存对象（上游少了一层 snapshot 之类）。原样收下：\n                    # 直接 continue 会让整张表**静默变 0**，而且不报错，很难查。\n                    out[plugin][day][model] = cell\n                    continue\n',
     "",
     STORE_TEST, "test_as_buckets_does_not_silently_zero"),
    ("内存基线不跟着裁", "main.py",
     '                self._baseline = pruned\n',
     "",
     INVARIANTS, "test_save_prunes_memory_baseline"),
    ("缓存命中又各写各的", "main.py",
     '                        "cache_hit": cache_hit_text(bucket.usage),',
     '                        "cache_hit": f"{cache_hit_rate(bucket):.0%}",',
     INVARIANTS, "test_cache_hit_goes_through_one_helper"),
    ("provider id 退化成类名", "attribute.py",
     '            return "default"',
     "            return fallback",
     CORE_TEST, "test_provider_id_from_config"),
    ("流式故障记成成功", "patch.py",
     '                    if raised:',
     '                    if False:',
     PATCH_TEST, "test_stream_failure_is_recorded_as_failure"),
    ("同步方法也去 await", "patch.py",
     '        if inspect.iscoroutinefunction(original):',
     '        if True:',
     PATCH_TEST, "test_wrapper_calls_sync_method_without_breaking_it"),
    ("旧版本被当空丢掉", "store.py",
     '    if isinstance(version, int) and version > FORMAT_VERSION:',
     "    if version != FORMAT_VERSION:",
     STORE_TEST, "test_load_version_policy"),
    ("失败的临时文件不清理", "store.py",
     '        if tmp is not None and os.path.exists(tmp):',
     '        if False:',
     STORE_TEST, "test_save_leaves_no_temp_files"),
    ("英文说明漏了同一处更正", ".astrbot-plugin/i18n/en-US.json",
     '"hint": "Default 20000. When exceeded, this batch is saved first, then the oldest records are dropped. Set 0 for no limit."',
     '"hint": "Default 20000. Persistence is not implemented yet, so records are lost on restart."',
     CONFIG, "test_translation_does_not_describe_progress"),
    ("翻译漏一个配置键", ".astrbot-plugin/i18n/en-US.json",
     '    "keep_days": {',
     '    "keepdays": {',
     CONFIG, "test_translation_covers_every_config_key"),
    ("版本号两处不一致", "metadata.yaml",
     f"version: {REAL_VERSION}",
     f"version: {REAL_VERSION}-mismatch",
     CONTRACT, "test_version_is_the_same_in_code_and_metadata"),
    ("声明的页面目录不存在", "metadata.yaml",
     '  - name: usage',
     '  - name: usage-missing',
     CONTRACT, "test_declared_pages_exist"),
    ("保留天数三处不一致", "store.py",
     'KEEP_DAYS = 60',
     'KEEP_DAYS = 90',
     CONTRACT, "test_keep_days_default_is_the_same_everywhere"),
    ("前端调错路由名", VIEW,
     'return api.apiGet("stats", params).catch(function (err) {',
     'return api.apiGet("stat", params).catch(function (err) {',
     CONTRACT, "test_view_calls_the_route_that_is_registered"),
    ("清单引用不存在的用例", "docs/archive/VERIFY.md",
     '`test_totals_agree_across_paths`',
     '`test_totals_agree_across_pathes`',
     CONTRACT, "test_verify_doc_cites_real_tests"),
    ("堆叠柱少了 2% 地板", VIEW,
     '        layers.style.height = Math.max(2, Math.round((item.total / max) * 100)) + "%";',
     '        layers.style.height = Math.round((item.total / max) * 100) + "%";',
     VIEW_SMOKE, "千分之一的那天仍有 2% 高度"),
    ("堆叠段比例算错", VIEW,
     '          seg.style.height = (item.total ? (piece.total / item.total) * 100 : 0) + "%";',
     '          seg.style.height = (item.total ? (piece.total / (item.total * 2)) * 100 : 0) + "%";',
     VIEW_SMOKE, "每天堆叠段之和"),
    ("占比条分母用错", VIEW,
     '    fill.style.width = (whole ? Math.round((value / whole) * 100) : 0) + "%";',
     '    fill.style.width = (whole ? Math.round((value / whole) * 50) : 0) + "%";',
     VIEW_SMOKE, "插件占比条之和"),
    ("归一化漏搬一列", VIEW,
     '  var NUM_KEYS = ["total", "input_other", "input_cached", "output",\n                  "calls", "failed", "images", "cache_reported"];',
     '  var NUM_KEYS = ["total", "input_other", "input_cached",\n                  "calls", "failed", "images", "cache_reported"];',
     VIEW_SMOKE, "「main_agent」的输出列"),
    ("命中率只认上报标记", "stats.py",
     "    if usage.cache_reported <= 0 and usage.input_cached <= 0 and usage.input_other > 0:",
     "    if usage.cache_reported <= 0 and usage.input_other > 0:",
     CORE_TEST, "test_cache_hit_text_trusts_the_count_over_the_flag"),
    ("记录时不看命中数", "stats.py",
     "        cache_reported=1 if (cached > 0 or reports_cache(response)) else 0,",
     "        cache_reported=1 if reports_cache(response) else 0,",
     CORE_TEST, "test_usage_from_marks_reported_when_there_are_hits"),
    ("前端又只看标记", VIEW,
     "      g.cache_hit = (g.cache_reported <= 0 && g.input_cached <= 0 && g.input_other > 0)",
     "      g.cache_hit = (g.cache_reported <= 0 && g.input_other > 0)",
     VIEW_SMOKE, "按模型分组时的命中率"),
    ("重置不确认就发请求", VIEW,
     '  resetBtn.addEventListener("click", function () {\n    if (!resetArmed) {',
     '  resetBtn.addEventListener("click", function () {\n    if (false) {',
     VIEW_SMOKE, "第一次点重置只进入待确认"),
    ("视图里用模态框确认", VIEW,
     '  var api = window.AstrBotPluginPage || window.AstrBotPluginView;',
     '  window.confirm("真的要删吗");\n  var api = window.AstrBotPluginPage || window.AstrBotPluginView;',
     "tests/test_views.py", "test_views_do_not_open_blocking_dialogs"),
]

# 沙箱必须是完整包结构：test_store.py 是按包名 import 的，
# 放在别处它测的永远是工作区里那份真代码，改 store.py 根本验不出来。
FILES = [
    "__init__.py", "main.py", "stats.py", "store.py", "attribute.py", "patch.py",
    "_conf_schema.json",
    "metadata.yaml",
    ".astrbot-plugin/i18n/en-US.json",
    "docs/archive/VERIFY.md",
    VIEW, "views/usage/index.html", "views/usage/style.css",
]
TESTS = [
    "test_invariants.py",
    "test_config.py",
    "view_smoke.mjs",
    "test_store.py",
    "test_core.py",
    "test_patch.py",
    "test_contract.py",
    "test_views.py",
]
PKG = SANDBOX / "astrbot_plugin_usage_stats"

originals = {name: (SRC / name).read_text(encoding="utf-8") for name in FILES}
test_sources = {name: (SRC / "tests" / name).read_text(encoding="utf-8") for name in TESTS}

def _clear_bytecode(root: pathlib.Path) -> int:
    """清掉沙箱里的 .pyc。

    CPython 用（源码 mtime, 源码大小）判断缓存能不能用，而 mtime 是**秒级**的。
    变异改写如果没改变文件大小（60 → 90）又落在同一秒里，旧 .pyc 会被继续使用 ——
    于是「已经改坏了」和「验不出来」长得一模一样。这个坑真的踩过一次。
    """
    removed = 0
    for path in root.rglob("*.pyc"):
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    return removed


for relative in [*FILES, *[f"tests/{n}" for n in TESTS]]:
    (PKG / relative).parent.mkdir(parents=True, exist_ok=True)
for name, text in test_sources.items():
    (PKG / "tests" / name).write_text(text, encoding="utf-8")

print(f"清掉 {_clear_bytecode(PKG)} 个过期的 .pyc")
# 本次之后不再生成：这样每次跑都是现编译的源码，不会读到隔夜的缓存
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")

ok = 0
bad = 0
for label, target, old, new, runner, expect in MUTATIONS:
    if old not in originals[target]:
        print(f"  ??    {label}：锚点找不到，这条变异没法做")
        bad += 1
        continue
    for name, text in originals.items():
        (PKG / name).write_text(text, encoding="utf-8")
    (PKG / target).write_text(originals[target].replace(old, new, 1), encoding="utf-8")
    # 视图测试是 .mjs，得用 node 跑；用 Python 去跑它只会得到一个无关的失败
    runtime = "node" if runner.endswith(".mjs") else PY
    proc = subprocess.run(
        [runtime, str(PKG / runner)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=ENV,
    )
    # 两边都要查：Python 的断言信息走 stderr（回溯），node 的走 stdout
    combined = (proc.stdout or "") + (proc.stderr or "")
    caught = proc.returncode != 0 and expect in combined
    print(f"  {'ok  ' if caught else 'FAIL'}  {label:26} {'拦住了' if caught else '没拦住'}  ({expect})")
    ok += 1 if caught else 0
    bad += 0 if caught else 1

# 复原成没变异的版本，免得留下坏文件让人误会
for name, text in originals.items():
    (PKG / name).write_text(text, encoding="utf-8")

print()
print(f"{ok}/{len(MUTATIONS)} 条变异被拦住" + ("" if bad == 0 else f"，{bad} 条漏网"))
sys.exit(0 if bad == 0 else 1)