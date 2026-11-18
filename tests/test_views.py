"""插件iframe的结构、状态与可访问性约束。

确认采用内联区域，不依赖宿主可能拦截的原生对话框。页面交互由Node冒烟补充，
这些断言不代替浏览器排版检查。
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
    assert "原文含会话标识，分享前请脱敏" in html
    assert 'id="log-reader"' in html and 'id="log-view"' in html
    assert re.search(r'id="log-body"[^>]*hidden', html)
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
    assert 'document.hidden' in app


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
    assert "最多 5000 行" in html and "不含日汇总" in html
    assert "留空表示缺价，0 表示该项不计费" in html
    assert "USD / 百万 Token" in html
    for key in ("settings-enabled", "settings-detail-history", "settings-keep-days", "settings-max-events", "settings-save", "settings-revert", "settings-status"):
        assert 'id="' + key + '"' in html
    assert "详细历史配置（只读）" not in html and "请在插件配置" not in html
    assert "detail_history 默认" not in html and "detail_max_events 默认" not in html
    assert "0 表示不按日期清理" in html and "清理日汇总和调用明细" in app
    assert 'api.apiPost("settings", { settings: draft, revision: revision })' in app
    assert "result.saved !== true" in app and "settingsSnapshot.writable" in app
    assert "collection.detail_enabled" in app and "collection.detail_available" in app
    assert "excluded_legacy_calls" in app
    assert 'id="reset-cancel"' in html and re.search(r'id="reset-status"[^>]*role="status"', html)
    assert re.search(r'id="reset-confirmation"[^>]*hidden', html)
    assert re.search(r'id="reset-confirm"[^>]*disabled', html)


def test_responsive_layout_does_not_scale_whole_page():
    css = (VIEWS / "style.css").read_text(encoding="utf-8")
    assert "flex-wrap:wrap" in css
    assert "@media(max-width:520px)" in css
    assert 'data-theme="dark"' in css
    assert not re.search(r"(?:transform\s*:\s*scale|zoom\s*:)", css)
    assert ":focus-visible" in css and "[hidden]" in css



def test_compact_topbar_keeps_native_filters_with_narrow_disclosure():
    html = (VIEWS / "index.html").read_text(encoding="utf-8")
    css = (VIEWS / "style.css").read_text(encoding="utf-8")
    assert '<h1>用量统计</h1>' in html and 'class="workspace-heading"' in html
    assert html.count('class="icon tab-icon"') == 6
    assert "USAGE DASHBOARD" not in html
    assert '<section class="filters"' in html and 'class="filters panel"' not in html
    assert '<select id="range"' in html
    for key in ("range", "filter-model", "filter-plugin", "filter-provider", "filter-result"):
        assert re.search(r'<select id="' + key + r'"[^>]*aria-label=', html)
    assert "--panel-radius:12px" in css and "--control-radius:8px" in css
    assert "min-height:44px" in css and "height:40px" in css
    assert "inset 0 -2px 0 var(--accent)" in css
    assert 'aria-expanded="false" aria-controls="filter-options"' in html
    assert '.filters:not(.is-expanded) #filter-options' in css
    assert 'class="filter-label"' in html
    assert re.search(r'id="stats-range-summary"[^>]*>尚未读取统计<', html)


def test_price_statistics_catalog_and_inline_manual_override():
    html = (VIEWS / "index.html").read_text(encoding="utf-8")
    app = (VIEWS / "app.js").read_text(encoding="utf-8")
    helper = (VIEWS / "dashboard.js").read_text(encoding="utf-8")
    for key in ("price-total", "price-summary", "price-search", "price-source", "price-sync", "price-table", "price-editor", "price-remove", "price-page-note"):
        assert 'id="' + key + '"' in html
    assert "调用数" in html and "估算成本" in html and "输入价 ($/1M)" in html
    assert 'api.apiPost("price_sync", { source: "models.dev" })' in app
    assert "H.pricing(data)" in app and "H.pricePage(" in app
    assert "data.pricing" in helper and "estimated_cost: null" in helper
    assert "只保存手工覆盖，不改写公共目录" in html
    assert "日汇总保留调用数、失败数与 Token 用量" in html
    for obsolete in ("旧汇总", "旧日汇总", "旧历史", "旧日桶"):
        assert obsolete not in html
    assert "按结果筛选或查询一天中的部分时段" in html


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
    titles = re.findall(r'\$\("status"\)\.setAttribute\("title",([^;]+);', app)
    for title in titles:
        assert "patched_count" not in title and "recorded_count" not in title and "Provider" not in title
    assert "record.runtime_error" in app and "collection.detail_error" in app


def test_refined_state_controls_and_old_script_targets_exist():
    html = (VIEWS / "index.html").read_text(encoding="utf-8")
    app = (VIEWS / "app.js").read_text(encoding="utf-8")
    ids = re.findall(r'id="([^"]+)"', html)
    assert len(ids) == len(set(ids)), "duplicate element IDs"
    required = {"filterbar", "stats-range-summary", "app-feedback", "data-asof",
                "events-empty", "events-empty-title", "events-empty-description", "events-enable-details",
                "events-table-scroll", "events-pager", "performance-enable-details", "cost-coverage",
                "price-view", "price-scope-note", "reset-confirmation", "reset-confirm",
                "coverage-alerts", "coverage-details", "coverage-summary", "log-wrap",
                "token-extra", "coverage-open", "filter-coverage"}
    assert required <= set(ids)
    referenced = set(re.findall(r'\$\("([^"]+)"\)', app))
    assert referenced <= set(ids), "controller references missing HTML elements: " + str(referenced - set(ids))
    assert re.search(r'id="app-feedback"[^>]*role="status"[^>]*aria-live="polite"', html)


def test_four_primary_metrics_and_separate_secondary_performance():
    html = (VIEWS / "index.html").read_text(encoding="utf-8")
    css = (VIEWS / "style.css").read_text(encoding="utf-8")
    workbench = html.split('class="overview-grid panel workbench"', 1)[1]
    assert workbench.index('class="workbench-head"') < workbench.index('class="kpis"') < workbench.index('id="chart"')
    primary = workbench.split('class="kpis"', 1)[1].split('</div>', 1)[0]
    assert "非账单" in primary
    assert primary.count('<article class="kpi') == 4
    for key in ("total", "calls", "cost", "success"):
        assert 'id="kpi-' + key + '"' in primary
    for key in ("tps", "cache"):
        assert 'id="kpi-' + key + '"' not in primary
        assert 'id="kpi-' + key + '"' in html
    assert 'class="panel performance-panel"' in html
    assert 'class="distribution-layout"' in html and 'class="metrics performance-metrics"' in html
    assert '--canvas:#f8fafc' in css and '--accent-soft:#e9eefc' in css
    assert '--canvas:#151c27' in css and '--accent-soft:#2a3a5d' in css
    assert "font-variant-numeric:tabular-nums" in css
    assert "#tab-analysis #table td:first-child { background:var(--canvas);" in css


def test_empty_details_use_an_action_instead_of_an_empty_wide_table():
    html = (VIEWS / "index.html").read_text(encoding="utf-8")
    for key in ("events-empty", "events-table-scroll", "events-pager"):
        assert re.search(r'id="' + key + r'"[^>]*hidden', html)
    assert re.search(r'<button[^>]*id="events-enable-details"', html)
    assert re.search(r'<button[^>]*id="performance-enable-details"[^>]*hidden', html)


def test_price_scope_and_catalog_have_explicit_controls_without_nested_vertical_scroll():
    html = (VIEWS / "index.html").read_text(encoding="utf-8")
    css = (VIEWS / "style.css").read_text(encoding="utf-8")
    assert re.search(r'<select id="price-view"[^>]*>\s*<option value="used" selected>', html)
    assert '<option value="all">全部目录</option>' in html
    assert re.search(r'<span id="price-source"', html)
    assert re.search(r'id="price-scope-note"[^>]*role="status"', html)
    assert html.index('id="price-editor"') < html.index('id="price-table"')
    assert html.index(">估算成本</th>") < html.index(">调用数</th>")
    assert html.index('id="events-pager"') < html.index('id="events-table-scroll"')
    assert re.search(r'\.price-scroll\s*\{[^}]*max-height:none', css)
    for name in ("usage-cell", "usage-value", "usage-track", "usage-fill"):
        assert "." + name in css


def test_management_progressive_disclosure_and_operation_icons():
    html = (VIEWS / "index.html").read_text(encoding="utf-8")
    css = (VIEWS / "style.css").read_text(encoding="utf-8")
    assert 'class="management-grid"' in html
    opening = re.search(r'<details id="coverage-details"[^>]*>', html).group(0)
    assert not re.search(r'\bopen(?:\s|=|>)', opening)
    disclosure = html.split(opening, 1)[1].split("</details>", 1)[0]
    assert 'id="coverage-warnings"' in disclosure
    assert html.index('id="coverage-alerts"') < html.index(opening)
    assert "#coverage-warnings {" in css and "#coverage-warnings { color:var(--danger)" not in css
    assert 'id="log-wrap" type="checkbox"' in html
    assert "checked" not in re.search(r'<input id="log-wrap"[^>]*>', html).group(0)
    assert ".log.is-wrapped" in css
    for key in ("refresh", "clear-filters"):
        button = re.search(r'<button[^>]*id="' + key + r'"[^>]*>.*?</button>', html).group(0)
        assert "<svg" in button and 'aria-hidden="true"' in button and 'aria-label=' in button
    assert "↻" not in html and "×" not in html


def test_release_ui_preserves_scope_and_readable_metric_help():
    html = (VIEWS / "index.html").read_text(encoding="utf-8")
    css = (VIEWS / "style.css").read_text(encoding="utf-8")
    app = (VIEWS / "app.js").read_text(encoding="utf-8")
    assert "原始参考项" not in html + app
    assert html.count("估算费用 · USD（非账单）") == 2
    assert "未缓存输入 + 缓存读取 + 输出" in html
    assert "输出 Token/秒" in html and "无完整缓存样本" in app
    assert 'class="muted performance-note"' not in html
    for topic in ("chart", "composition", "tps", "cache", "export", "price", "privacy"):
        assert re.search(r'<details[^>]+data-help="' + topic + r'"[^>]*><summary aria-label="[^"]+"', html)
    assert html.count('class="setting-copy"') == 2
    assert "table td:first-child { position:sticky" in css
    assert "overflow-x:auto; max-height:none" in css
    assert not re.search(r"https?://", html.replace('http://www.w3.org/2000/svg', ''))
    version = re.search(r"^version:\s*([0-9.]+)", (ROOT / "metadata.yaml").read_text(encoding="utf-8"), re.M).group(1)
    assert re.findall(r"[?]v=([0-9.]+)", html) == [version] * 3


def main() -> None:
    cases = [value for name, value in globals().items() if name.startswith("test_") and callable(value)]
    for case in cases:
        case()
        print(f"  ok  {case.__name__}")
    print(f"{len(cases)} 组用例全部通过")


if __name__ == "__main__":
    main()
