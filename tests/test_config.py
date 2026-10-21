"""配置契约自检：`_conf_schema.json` 和 `main.py` 里的 `_cfg()` 必须对得上。

    python tests/test_config.py

这份检查有来历：`memory_cap` 的说明曾经写着「落盘（P1）还没做，重启即清空」，
而落盘早就实现了 —— 用户在配置界面看到的是反话。文档和代码分家不会报错，
只会静默地骗人。

查四件事：

1. 代码里读的键，schema 里都有 —— 否则界面上根本调不到
2. schema 里的键，代码里都读了 —— 否则是个摆设开关
3. 两边的默认值一致 —— 否则界面显示一个数、代码用另一个
4. 说明文字不描述「开发进度」—— 那种话一定会过期
5. 英文翻译覆盖同样的键 —— 它是同一份事实的第二份副本，会独立漂移

动态拼出来的键（f-string 之类）这里看不见，那属于另一类问题。
"""

from __future__ import annotations

import ast
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
TREE = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
METADATA_TEXT = (ROOT / "metadata.yaml").read_text(encoding="utf-8")
I18N = json.loads(
    (ROOT / ".astrbot-plugin" / "i18n" / "en-US.json").read_text(encoding="utf-8")
)

# 英文说明里同样不该出现的措辞。中文那份修过一次，英文那份漏了同一个错。
EN_STALE = ("not implemented yet", "not yet implemented", "coming soon", "unimplemented")

# 说明文字里不该出现的措辞：它们在描述「做到哪一步了」，而不是「这东西怎么用」。
# 一旦功能补上，这类话就变成假话，而且没有任何机制会提醒你。
STALE_PHRASES = ("还没做", "尚未", "待实现", "TODO", "暂不支持")

# _cfg() 里的默认值必须是字面量，否则静态读不出来 —— 读不出来就该改写法
LITERAL_DEFAULTS = (ast.Constant, ast.List, ast.Tuple, ast.Dict, ast.Set)


def _cfg_defaults() -> dict:
    """收集 `self._cfg("key", default)`。只认字面量，动态的跳过。"""
    found: dict[str, object] = {}
    for node in ast.walk(TREE):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name != "_cfg" or len(node.args) < 2:
            continue
        key_node, default_node = node.args[0], node.args[1]
        if not isinstance(key_node, ast.Constant) or not isinstance(key_node.value, str):
            continue
        if not isinstance(default_node, LITERAL_DEFAULTS):
            continue
        found[key_node.value] = ast.literal_eval(default_node)
    return found


CODE = _cfg_defaults()


def test_code_keys_all_exist_in_schema():
    missing = sorted(k for k in CODE if k not in SCHEMA)
    assert not missing, f"代码读了这些键，但 schema 里没有，界面上调不到：{missing}"


def test_schema_keys_are_all_read():
    unused = sorted(k for k in SCHEMA if k not in CODE)
    assert not unused, f"schema 里有这些键，但代码从不读，是个摆设开关：{unused}"


def test_defaults_agree():
    mismatched = []
    for key, code_default in CODE.items():
        if key not in SCHEMA:
            continue
        schema_default = SCHEMA[key].get("default")
        # JSON 的 true/false 与 Python 的 True/False 比得了，int 与 float 也能比
        if schema_default != code_default:
            mismatched.append(f"{key}: schema={schema_default!r} 代码={code_default!r}")
    assert not mismatched, "两边默认值不一致，界面显示一个数、代码用另一个：\n  " + "\n  ".join(mismatched)


def test_every_entry_is_complete():
    for key, entry in SCHEMA.items():
        for field in ("description", "type", "default"):
            assert field in entry, f"{key} 缺 {field}"
        assert isinstance(entry["type"], str) and entry["type"], f"{key} 的 type 不是字符串"


def test_hints_do_not_describe_progress():
    bad = []
    for key, entry in SCHEMA.items():
        text = str(entry.get("hint", "")) + str(entry.get("description", ""))
        for phrase in STALE_PHRASES:
            if phrase in text:
                bad.append(f"{key} 的说明里有「{phrase}」")
    assert not bad, (
        "说明文字不该写「做到哪一步了」—— 功能补上之后它就变成假话了：\n  " + "\n  ".join(bad)
    )


def test_translation_covers_every_config_key():
    """翻译是同一份事实的第二份副本 —— 漏一个键，英文用户就看不到那项配置。"""
    missing = sorted(set(SCHEMA) ^ set(I18N["config"]))
    assert not missing, f"只在一侧存在的配置键：{missing}"


def test_translation_entries_are_complete():
    for key, entry in I18N["config"].items():
        for field in ("description", "hint"):
            assert entry.get(field), f"en-US.json 的 {key} 缺 {field}"


def test_translation_does_not_describe_progress():
    """英文那份曾经和中文一样写着「还没做」—— 修了中文没修英文。"""
    bad = []
    for key, entry in I18N["config"].items():
        text = f"{entry.get('description', '')} {entry.get('hint', '')}".lower()
        for phrase in EN_STALE + STALE_PHRASES:
            if phrase.lower() in text:
                bad.append(f"{key} 含「{phrase}」")
    assert not bad, "说明文字不该写「做到哪一步了」：\n  " + "\n  ".join(bad)


def test_metadata_and_translation_agree():
    """市场展示的三个字段，两边都要有 —— 少一个英文用户就看到空白。"""
    for field in ("display_name", "short_desc", "desc"):
        assert re.search(rf"^{field}:", METADATA_TEXT, re.M), f"metadata.yaml 缺 {field}"
        assert I18N["metadata"].get(field), f"en-US.json 的 metadata 缺 {field}"


def main() -> None:
    cases = [
        test_code_keys_all_exist_in_schema,
        test_schema_keys_are_all_read,
        test_defaults_agree,
        test_every_entry_is_complete,
        test_hints_do_not_describe_progress,
        test_translation_covers_every_config_key,
        test_translation_entries_are_complete,
        test_translation_does_not_describe_progress,
        test_metadata_and_translation_agree,
    ]
    print(f"  （读到 {len(CODE)} 个 _cfg 键、{len(SCHEMA)} 个 schema 键）")
    for case in cases:
        case()
        print(f"  ok  {case.__name__}")
    print(f"{len(cases)} 组用例全部通过")


if __name__ == "__main__":
    main()
