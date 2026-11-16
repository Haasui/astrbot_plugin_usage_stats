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


def test_call_log_has_privacy_and_live_feedback():
    html = (VIEWS / "index.html").read_text(encoding="utf-8")
    app = (VIEWS / "app.js").read_text(encoding="utf-8")
    assert "调用日志含会话标识" in html
    assert 'id="log-status" role="status" aria-live="polite"' in html
    assert "logGeneration" in app and "generation !== logGeneration" in app
    assert "payload.path" not in app


def test_dashboard_has_accessible_shared_filters_and_all_tabs():
    html = (VIEWS / "index.html").read_text(encoding="utf-8")
    app = (VIEWS / "app.js").read_text(encoding="utf-8")
    for tab in ("stats", "analysis", "events", "prices", "log", "manage"):
        assert f'id="tab-{tab}-btn" role="tab"' in html
        assert f'aria-controls="tab-{tab}"' in html
        assert f'id="tab-{tab}" role="tabpanel"' in html
        assert f'aria-labelledby="tab-{tab}-btn"' in html
    for key in ("model", "plugin", "provider", "result"):
        assert f'id="filter-{key}"' in html
    assert html.count('type="datetime-local"') == 2
    assert "ArrowRight" in app and "ArrowLeft" in app and '"Home"' in app and '"End"' in app
    assert 'aria-busy' in app
    assert 'if (!document.hidden' in app


def test_remote_content_never_enters_html_or_new_get_routes():
    app = (VIEWS / "app.js").read_text(encoding="utf-8")
    helper = (VIEWS / "dashboard.js").read_text(encoding="utf-8")
    for text in (app, helper):
        assert not re.search(r"\.(?:innerHTML|outerHTML)\s*=|insertAdjacentHTML|document\.write\(", text)
        assert not re.search(r"\b(?:eval|fetch)\s*\(", text)
    routes = re.findall(r'apiGet\("([^\"]+)"', app)
    assert routes and set(routes) == {"stats"}
    assert 'api.apiPost("prices", { prices: prices })' in app
    assert 'api.apiPost("reset", {})' in app
    assert "toISOString()" in helper
    assert "Math.min(200" in helper


def test_prices_export_and_readonly_collection_are_explicit():
    html = (VIEWS / "index.html").read_text(encoding="utf-8")
    app = (VIEWS / "app.js").read_text(encoding="utf-8")
    assert "最多 5000 行" in html and "不含旧汇总" in html
    assert "留空表示缺价，0 表示免费" in html
    assert "USD / 百万 Token" in html
    for key in ("settings-enabled", "settings-detail-history", "settings-keep-days", "settings-max-events", "settings-save", "settings-revert", "settings-status"):
        assert 'id="' + key + '"' in html
    assert "详细历史配置（只读）" not in html and "请在插件配置" not in html
    assert "detail_history 默认" not in html and "detail_max_events 默认" not in html
    assert "0 表示不按日期清理" in html and "缩小保留范围会在保存统计时清理旧数据" in html
    assert 'api.apiPost("settings", { settings: draft, revision: revision })' in app
    assert "result.saved !== true" in app and "settingsSnapshot.writable" in app
    assert "collection.detail_enabled" in app and "collection.detail_available" in app
    assert "excluded_legacy_calls" in app
    assert 'id="reset-cancel"' in html and re.search(r'id="reset-status"[^>]*role="status"', html)
    assert 'setTimeout(function () { disarmReset();' in app


def test_responsive_layout_does_not_scale_whole_page():
    css = (VIEWS / "style.css").read_text(encoding="utf-8")
    assert "flex-wrap:wrap" in css
    assert "@media(max-width:520px)" in css
    assert 'data-theme="dark"' in css
    assert not re.search(r"(?:transform\s*:\s*scale|zoom\s*:)", css)
    assert ":focus-visible" in css and "[hidden]" in css



def test_compact_topbar_uses_native_single_row_filters():
    html = (VIEWS / "index.html").read_text(encoding="utf-8")
    css = (VIEWS / "style.css").read_text(encoding="utf-8")
    assert '<h1 class="sr-only">' in html
    assert "USAGE DASHBOARD" not in html
    assert '<section class="filters"' in html and 'class="filters panel"' not in html
    assert '<select id="range"' in html
    for key in ("range", "filter-model", "filter-plugin", "filter-provider", "filter-result"):
        assert re.search(r'<select id="' + key + r'"[^>]*aria-label=', html)
    assert "padding:12px 14px 14px" in css
    assert "min-height:44px" in css and "height:36px" in css
    assert "inset 0 -2px 0 var(--accent)" in css
    assert '.filters { margin-bottom:12px; padding:0; border:0;' in css


def test_price_statistics_catalog_and_inline_manual_override():
    html = (VIEWS / "index.html").read_text(encoding="utf-8")
    app = (VIEWS / "app.js").read_text(encoding="utf-8")
    helper = (VIEWS / "dashboard.js").read_text(encoding="utf-8")
    for key in ("price-total", "price-summary", "price-search", "price-source", "price-sync", "price-table", "price-editor", "price-remove", "price-page-note"):
        assert 'id="' + key + '"' in html
    assert "调用数" in html and "估算成本" in html and "输入价 ($/1M)" in html
    assert 'api.apiPost("price_sync", { source: "models.dev" })' in app
    assert "H.pricing(data)" in app and "H.pricePage(view.rows" in app
    assert "data.pricing" in helper and "estimated_cost: null" in helper
    assert "只保存手工覆盖，不改写公共目录" in html
    assert "旧汇总保留调用数、失败数与 Token 用量" in html


def test_collection_settings_ranges_and_runtime_disclosure():
    html = (VIEWS / "index.html").read_text(encoding="utf-8")
    app = (VIEWS / "app.js").read_text(encoding="utf-8")
    assert re.search(r'id="settings-keep-days"[^>]*min="0"[^>]*max="3650"[^>]*step="1"', html)
    assert re.search(r'id="settings-max-events"[^>]*min="1"[^>]*max="200000"[^>]*step="1"', html)
    assert re.search(r'id="settings-status"[^>]*role="status"[^>]*aria-live="polite"', html)
    opening = re.search(r'<details id="runtime-diagnostics"[^>]*>', html).group(0)
    assert not re.search(r'\bopen(?:\s|=|>)', opening)
    disclosure = html.split(opening, 1)[1].split("</details>", 1)[0]
    assert 'id="runtime-details"' in disclosure and 'id="wrapped-methods"' in disclosure
    assert "运行诊断" in disclosure and "采集包装方法数按类和方法计数" in disclosure
    assert html.index('id="coverage-warnings"') < html.index(opening)
    assert "collection.wrapped_methods" in app and "后端未返回方法清单" in app
    assert "Provider 补丁" not in app and "等待采集就绪" in app
    title = re.search(r'\$\("status"\)\.setAttribute\("title",([^;]+);', app).group(1)
    assert "patched_count" not in title and "recorded_count" not in title and "Provider" not in title
    assert "record.runtime_error" in app and "collection.detail_error" in app


def main() -> None:
    cases = [value for name, value in globals().items() if name.startswith("test_") and callable(value)]
    for case in cases:
        case()
        print(f"  ok  {case.__name__}")
    print(f"{len(cases)} 组用例全部通过")


if __name__ == "__main__":
    main()
