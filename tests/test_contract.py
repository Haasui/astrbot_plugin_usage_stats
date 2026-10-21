"""跨文件契约自检：同一份事实有多个副本时，必须一起对。

    python tests/test_contract.py

这个项目里同一件事常常要在两三个地方各写一遍 —— 插件名在代码常量和市场元数据里，
版本号也是，保留天数甚至在三个地方，接口名在注册处和调用处各写一次。

副本不会自己保持同步。分家之后通常也不报错，只是出现「市场显示 v0.1.0、
插件自己说 v0.2.0」这种说不通的现象，或者声明的页面目录根本不存在。

所以这里不测行为，只测**几份副本是不是还是同一个值**。不 import AstrBot。
"""

from __future__ import annotations

import ast
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from astrbot_plugin_usage_stats import store  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
METADATA = (ROOT / "metadata.yaml").read_text(encoding="utf-8")
MAIN_TEXT = (ROOT / "main.py").read_text(encoding="utf-8")
SCHEMA = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
TREE = ast.parse(MAIN_TEXT)
VIEW_JS = (ROOT / "views" / "usage" / "app.js").read_text(encoding="utf-8")


def _constant(name: str):
    """读 main.py 的模块级字符串常量。走语法树，不执行它（那要 AstrBot）。"""
    for node in TREE.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError("main.py 里找不到模块级常量 " + name)


def _metadata_scalar(key: str):
    """读 metadata.yaml 的一个顶层标量。为一行数据引 YAML 库不值得。"""
    match = re.search(rf"^{key}:\s*(.+?)\s*$", METADATA, re.M)
    return match.group(1).strip().strip('"') if match else None


VERSION = _constant("VERSION")
PLUGIN_NAME = _constant("PLUGIN_NAME")


def test_version_is_the_same_in_code_and_metadata():
    """插件自己报的版本和市场里显示的必须是同一个。"""
    assert VERSION == _metadata_scalar("version"), (
        f"main.VERSION={VERSION!r}，metadata.yaml={_metadata_scalar('version')!r}"
    )


def test_plugin_name_matches_metadata_and_directory():
    """插件名决定数据目录和 Web 路由，三处必须是同一个。

    AstrBot 用目录名认插件，我们用它算 plugin_data 下的目录，metadata 里还要写一遍
    给市场看。任一处不同，数据就落错地方。
    """
    assert PLUGIN_NAME == _metadata_scalar("name"), (
        f"main.PLUGIN_NAME={PLUGIN_NAME!r}，metadata.yaml={_metadata_scalar('name')!r}"
    )
    assert PLUGIN_NAME == ROOT.name, (
        f"main.PLUGIN_NAME={PLUGIN_NAME!r}，目录名={ROOT.name!r}"
    )


def test_declared_pages_exist():
    """metadata 声明的每个页面，views/ 下都要真的有一份。

    对不上的话，市场里点开就是 404，而且不会有任何日志。
    """
    declared = re.findall(r"^\s*-\s*name:\s*(\S+)\s*$", METADATA, re.M)
    assert declared, "metadata.yaml 里没声明任何 pages"
    for name in declared:
        page = ROOT / "views" / name / "index.html"
        assert page.exists(), f"声明了页面 {name}，但 {page} 不存在"


def test_package_contains_every_runtime_file():
    """打好的包里必须含 views/ 和 .astrbot-plugin/ 下的文件。

    源目录里有、包里没有，装上去就是「WebUI 那一项消失了」——不报错、不提示。
    这个包真的这么发出去过一次：修 bug 重打包时手写了文件清单，漏掉
    views/usage/ 的 index.html、style.css、app.js 三个文件，侧边栏那项直接不见。
    """
    import zipfile

    dist = ROOT.parent / "_dist"
    zips = sorted(dist.glob("astrbot_plugin_usage_stats-*.zip")) if dist.exists() else []
    if not zips:
        return  # 还没打过包，跳过
    latest = max(zips, key=lambda p: p.stat().st_mtime)
    with zipfile.ZipFile(latest) as archive:
        names = set(archive.namelist())

    required = ["main.py", "_conf_schema.json", "metadata.yaml", "LICENSE",
                ".astrbot-plugin/i18n/en-US.json"]
    for name in re.findall(r"^\s*-\s*name:\s*(\S+)\s*$", METADATA, re.M):
        required.append(f"views/{name}/index.html")
        required.append(f"views/{name}/app.js")
        required.append(f"views/{name}/style.css")

    missing = [item for item in required if item not in names]
    assert not missing, f"{latest.name} 里缺 {missing}"


def test_keep_days_default_is_the_same_everywhere():
    """60 这个数写在三个地方：store 的常量、schema 的默认值、_cfg 的默认参数。

    schema 与 _cfg 那一对由 test_config.py 钉；这里补上 store 那份。
    """
    assert store.KEEP_DAYS == SCHEMA["keep_days"]["default"], (
        f"store.KEEP_DAYS={store.KEEP_DAYS}，schema={SCHEMA['keep_days']['default']}"
    )


def test_view_calls_the_route_that_is_registered():
    """后端注册的路由和前端 apiGet 的名字要对得上。

    Bridge 会把 apiGet 打到 /<插件名>/<名字>，所以两边各写一次这个名字 ——
    改了一处没改另一处就是取不到数。
    """
    marker = 'f"/{PLUGIN_NAME}/'
    index = MAIN_TEXT.find(marker)
    assert index >= 0, "main.py 里找不到注册的视图路由"
    tail = MAIN_TEXT[index + len(marker):]
    route = re.match(r"\w+", tail).group(0)
    # 必须是**每一处**都对：只看"存在一个对的"会漏掉新增的调用点 ——
    # 加了调用日志面板之后，改坏其中一个名字，presence 检查照样过。
    called = set(re.findall(r'apiGet\(\s*"([^"]*)"', VIEW_JS))
    assert called == {route}, (
        f"后端注册了 {route}，app.js 里调的是 {sorted(called)}",
    )
    assert f'apiGet("{route}"' in VIEW_JS, (
        f"后端注册了 {route}，但 app.js 里没有 apiGet"
    )


def test_verify_doc_cites_real_tests():
    """VERIFY.md 提到的每个用例名，都要真的存在。

    1.3 那张表的全部意义就是「这条已由某个用例覆盖」。引用一个不存在的用例名，
    等于把「没验过」说成「验过了」—— 而这份文档正是给上机的人看的。
    """
    text = (ROOT / "docs" / "VERIFY.md").read_text(encoding="utf-8")
    cited = set(re.findall(r"`(test_[a-z_]+)`", text))
    assert cited, "VERIFY.md 里没引用任何用例"
    known: set[str] = set()
    for path in (ROOT / "tests").glob("*.py"):
        known.update(
            re.findall(r"^def (test_[a-z_]+)", path.read_text(encoding="utf-8"), re.M)
        )
    missing = sorted(cited - known)
    assert not missing, f"VERIFY.md 引用了不存在的用例：{missing}"


def main() -> None:
    cases = [
        test_version_is_the_same_in_code_and_metadata,
        test_plugin_name_matches_metadata_and_directory,
        test_declared_pages_exist,
        test_package_contains_every_runtime_file,
        test_keep_days_default_is_the_same_everywhere,
        test_view_calls_the_route_that_is_registered,
        test_verify_doc_cites_real_tests,
    ]
    for case in cases:
        case()
        print(f"  ok  {case.__name__}")
    print(f"{len(cases)} 组用例全部通过")


if __name__ == "__main__":
    main()
