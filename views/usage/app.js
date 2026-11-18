/* Usage dashboard. All remote text is rendered with textContent; all GETs use stats. */
(function () {
  "use strict";
  var api = window.AstrBotPluginPage || window.AstrBotPluginView;
  var H = window.UsageDashboard;
  var state = { days: 30, custom: false, start: "", end: "", plugin: "", model: "", provider: "", result: "all",
    group: "plugin", page: 1, page_size: 50, speed_mode: "total", tab: "stats", data: null,
    analysisGroup: "plugins", sort: { key: "tokens", dir: "desc" }, hiddenSeries: new Set() };
  if (window.matchMedia("(max-width: 520px)").matches) state.page_size = 20;
  var loadSeq = 0, exportSeq = 0, pricesDirty = false, priceSaving = false, priceVersion = 0;
  var displayedQuery = null, feedbackText = "", feedbackKind = "";
  var TAB_KEY = "astrbot_usage_stats_tab", RANGE_KEY = "astrbot_usage_stats_days", GROUP_KEY = "astrbot_usage_stats_group";
  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) { var n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined && text !== null) n.textContent = text; return n; }
  function svg(tag, attrs, text) {
    var n = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.keys(attrs || {}).forEach(function (key) { n.setAttribute(key, String(attrs[key])); });
    if (text !== undefined) n.textContent = text;
    return n;
  }
  function setStatus(text, kind) { $("status-text").textContent = text; $("status").className = "status" + (kind ? " is-" + kind : ""); }
  function storageGet(key) { try { return window.localStorage.getItem(key); } catch (e) { return null; } }
  function storageSet(key, value) { try { window.localStorage.setItem(key, value); } catch (e) { /* Restricted iframe. */ } }
  var savedDays = storageGet(RANGE_KEY), savedGroup = storageGet(GROUP_KEY);
  if (savedDays !== null && [0, 1, 7, 30].indexOf(Number(savedDays)) >= 0) state.days = Number(savedDays);
  if (savedGroup === "model" || savedGroup === "plugin") state.group = savedGroup;
  var media = window.matchMedia("(prefers-color-scheme: dark)"), hostDark = null, weSetTheme = false;
  function applyTheme() {
    if (typeof hostDark !== "boolean" && !weSetTheme && document.documentElement.hasAttribute("data-theme")) return;
    weSetTheme = true;
    document.documentElement.setAttribute("data-theme", (typeof hostDark === "boolean" ? hostDark : media.matches) ? "dark" : "light");
  }
  applyTheme();
  if (media.addEventListener) media.addEventListener("change", applyTheme);
  function syncSeg(id, field, value) {
    Array.prototype.forEach.call($(id).children, function (b) {
      var selected = b.dataset[field] === String(value); b.setAttribute("aria-pressed", String(selected)); b.classList.toggle("on", selected);
    });
  }
  function metric(list, label, value, color) {
    var dt = el("dt");
    if (color !== undefined) { var sw = el("span", "swatch"); sw.style.background = color; dt.appendChild(sw); dt.appendChild(document.createTextNode(" " + label)); }
    else dt.textContent = label;
    list.appendChild(dt); list.appendChild(el("dd", "", value));
  }
  function rangeText(query) {
    if (!query) return "尚未读取统计";
    var range = query.start && query.end ? new Date(query.start).toLocaleString() + " 至 " + new Date(query.end).toLocaleString() + "（结束不含）" : query.days === 1 ? "今天" : query.days ? "近 " + query.days + " 天" : "全部时间";
    var filters = [query.model && "模型 " + query.model, query.plugin && "来源 " + H.displayLabel(query.plugin, "plugin"), query.provider && "提供方 " + query.provider, query.result !== "all" && ({ success: "成功", failed: "失败", canceled: "取消" }[query.result] || query.result)].filter(Boolean);
    return range + (filters.length ? " · " + filters.join(" / ") : " · 全部调用");
  }
  function sameQuery(a, b) {
    return !!a && !!b && ["days", "start", "end", "plugin", "model", "provider", "result", "group", "page", "page_size", "speed_mode"].every(function (key) { return a[key] === b[key]; });
  }
  function feedback(message, kind) {
    feedbackText = message || ""; feedbackKind = kind || "";
    var target = $("app-feedback"); target.textContent = feedbackText; target.hidden = !feedbackText || state.tab === "log";
    target.className = "feedback" + (kind ? " is-" + kind : "");
    if (kind === "error" && api) { var retry = el("button", "feedback-retry", "重新读取"); retry.type = "button"; retry.addEventListener("click", function () { load(); }); target.appendChild(retry); }
  }
  function goToDetailsSettings() {
    showTab("manage"); var target = $("settings-detail-history");
    if (target.disabled) target = $("tab-manage-btn");
    target.focus(); if (typeof target.scrollIntoView === "function") target.scrollIntoView({ block: "center", behavior: "smooth" });
  }
  function editingDynamicRegion() {
    var current = document.activeElement, regions = ["chart", "legend", "tbody", "events-body", "prices-body"];
    while (current) { if (current.getAttribute && regions.indexOf(current.getAttribute("id")) >= 0) return true; current = current.parentNode; }
    return false;
  }
  function dateText(seconds) { return H.known(seconds) ? new Date(seconds * 1000).toLocaleString() : "—"; }
  function palette(index) { return "var(--series-" + (index % 6 + 1) + ")"; }
  function renderKpis(o) {
    $("kpi-calls").textContent = H.fmt(o.calls, 0);
    $("kpi-calls-sub").textContent = "成功 " + H.fmt(o.success, 0) + " · 失败 " + H.fmt(o.failed, 0) + " · 取消 " + H.fmt(o.canceled, 0);
    $("kpi-tps").textContent = H.fmt(o.tps);
    $("kpi-tps").classList.toggle("metric-placeholder", !H.known(o.tps));
    $("kpi-tps-sub").textContent = o.tps_samples ? "输出 Token/秒 · " + H.fmt(o.tps_samples, 0) + " 个有效样本" : "暂无有效明细样本";
    $("kpi-total").textContent = H.fmt(o.tokens, 0);
    $("kpi-success").textContent = H.percent(o.success_rate);
    var finished = H.known(o.success) && H.known(o.failed) ? o.success + o.failed : null;
    $("kpi-success-sub").textContent = finished ? H.fmt(finished, 0) + " 次已完成（不含取消）" : finished === 0 ? "暂无已完成调用" : "不含取消";
    $("kpi-cache").textContent = H.percent(o.cache_hit_rate);
    $("kpi-cache").classList.toggle("metric-placeholder", !H.known(o.cache_hit_rate));
    $("kpi-cache-sub").textContent = !H.known(o.cache_hit_rate) ? "无完整缓存样本" : H.known(o.cache_share) ? "有效样本中，缓存读取占总 Token " + H.percent(o.cache_share) : "缓存读取 / 输入";
    $("kpi-cost").textContent = H.moneyShort(o.estimated_cost); $("kpi-cost").setAttribute("title", H.money(o.estimated_cost));
    var total = H.known(o.priced_calls) && H.known(o.unpriced_calls) ? o.priced_calls + o.unpriced_calls : 0;
    $("cost-coverage").hidden = !total; $("cost-coverage").textContent = total ? "已计价 " + H.fmt(o.priced_calls, 0) + " / " + H.fmt(total, 0) + " 次（" + H.percent(o.priced_calls / total) + "）" : "";
    $("cost-coverage").className = "coverage-notice" + (o.unpriced_calls > 0 ? " is-warning" : " is-complete");
    var collection = (state.data || {}).collection || {};
    var noSamples = !o.tps_samples && !o.latency_samples;
    $("performance-enable-details").hidden = !(noSamples && collection.detail_enabled === false);
    if (noSamples && collection.detail_enabled === false) $("kpi-tps-sub").textContent = "开启调用明细后记录性能样本";
  }
  var chartWidth = 0;
  function renderChart() {
    var host = $("chart"); chartWidth = host.clientWidth; host.textContent = "";
    var chart = H.stacks(state.data.timeline, state.group, state.hiddenSeries);
    var legend = $("legend"); legend.textContent = "";
    chart.series.forEach(function (item) {
      var b = el("button"), sw = el("span", "swatch"); b.type = "button";
      sw.style.background = palette(item.index); b.appendChild(sw); b.appendChild(document.createTextNode(H.displayLabel(item.label, state.group) + " · " + H.compact(item.total)));
      b.setAttribute("aria-pressed", String(!state.hiddenSeries.has(item.key)));
      b.setAttribute("aria-label", "切换图表系列 " + item.label);
      b.addEventListener("click", function () {
        if (state.hiddenSeries.has(item.key)) state.hiddenSeries.delete(item.key); else state.hiddenSeries.add(item.key);
        renderChart();
        var replacement = legend.children[item.index]; if (replacement) replacement.focus();
      }); legend.appendChild(b);
    });
    $("chart-detail").textContent = "";
    $("chart-peak").textContent = "按" + ({ day: "日", hour: "小时", "30m": "30 分钟" }[state.data.granularity] || "时间") + "统计 · 可见峰值 " + H.compact(chart.max) + " Token";
    if (!chart.points.length || chart.max === 0) {
      $("chart-caption").textContent = "";
      host.appendChild(el("div", "empty", chart.series.length && state.hiddenSeries.size ? "当前可见系列无用量；可用图例恢复。" : "这个范围内没有 Token 用量。")); return;
    }
    var available = chartWidth || 760, narrow = available < 530, height = narrow ? 214 : 260;
    var left = narrow ? 52 : 68, top = 18, bottom = narrow ? 36 : 40, plotHeight = height - top - bottom;
    // Preserve the full time range. Only very dense selections need local scrolling.
    var wide = (available - left - 15) / chart.points.length < 7;
    var width = wide ? chart.points.length * 14 + left + 15 : available, plotWidth = width - left - 15;
    $("chart-caption").textContent = wide ? "横向滚动查看完整范围" : "";
    var canvas = svg("svg", { viewBox: "0 0 " + width + " " + height, class: "chart-svg", role: "group", "aria-label": "Token 堆叠柱图，左右方向键查看时间桶" });
    if (wide) canvas.style.minWidth = width + "px";
    // Each tick and segment uses the same linear scale. Tiny values stay tiny, not a minimum-height floor.
    for (var tick = 0; tick <= 4; tick++) {
      var y = top + plotHeight * tick / 4;
      canvas.appendChild(svg("line", { x1: left, x2: width - 15, y1: y, y2: y, class: "chart-grid" }));
      canvas.appendChild(svg("text", { x: left - 8, y: y + 4, "text-anchor": "end" }, H.compact(chart.max * (4 - tick) / 4)));
    }
    var bars = [], slot = plotWidth / chart.points.length, barWidth = slot * .66;
    chart.points.forEach(function (point, index) {
      var description = point.label + " · " + H.fmt(point.total) + " Token · " + H.fmt(point.calls) + " 次请求";
      if (state.hiddenSeries.size) description += "（仅可见系列 Token；请求数仍为该时间桶全部请求）";
      point.segments.forEach(function (s) { description += "；" + s.label + " " + H.fmt(s.value); });
      var g = svg("g", { class: "chart-bar", tabindex: index === 0 ? "0" : "-1", role: "img", "aria-label": description });
      g.appendChild(svg("title", {}, description));
      point.segments.forEach(function (s) {
        g.appendChild(svg("rect", { x: left + slot * index + (slot - barWidth) / 2, y: top + plotHeight - (s.offset + s.value) / chart.max * plotHeight,
          width: barWidth, height: s.value / chart.max * plotHeight, fill: palette(s.index) }));
      });
      g.appendChild(svg("rect", { x: left + slot * index, y: top, width: slot, height: plotHeight, class: "chart-hit" }));
      function show() { $("chart-detail").textContent = description; }
      g.addEventListener("focus", show); g.addEventListener("mouseenter", show); g.addEventListener("click", show);
      bars.push(g); canvas.appendChild(g);
      if (index === 0 || index === chart.points.length - 1 || (!narrow && index === Math.floor(chart.points.length / 2))) {
        canvas.appendChild(svg("text", { x: index === 0 ? left : index === chart.points.length - 1 ? width - 15 : left + slot * (index + .5), y: height - 16, "text-anchor": index === 0 ? "start" : index === chart.points.length - 1 ? "end" : "middle" }, point.label.replace(/^\d{4}-/, "")));
      }
    });
    canvas.addEventListener("keydown", function (ev) {
      var index = bars.indexOf(ev.target), next = index;
      if (index < 0) return;
      if (ev.key === "ArrowRight" || ev.key === "ArrowDown") next = Math.min(bars.length - 1, index + 1);
      else if (ev.key === "ArrowLeft" || ev.key === "ArrowUp") next = Math.max(0, index - 1);
      else if (ev.key === "Home") next = 0; else if (ev.key === "End") next = bars.length - 1; else return;
      ev.preventDefault(); bars.forEach(function (b, i) { b.setAttribute("tabindex", i === next ? "0" : "-1"); }); bars[next].focus();
    });
    host.appendChild(canvas);
  }
  function renderDistribution(o) {
    var host = $("donut"), list = $("token-parts"), parts = H.components(o);
    host.textContent = ""; list.textContent = "";
    var total = parts.reduce(function (sum, p) { return sum + (H.known(p.value) ? p.value : 0); }, 0);
    var allKnown = parts.every(function (p) { return H.known(p.value); });
    if (total > 0 && allKnown) {
      var track = el("div", "composition-track"); track.setAttribute("aria-hidden", "true");
      parts.forEach(function (part) {
        var segment = el("span"); segment.style.width = part.value / total * 100 + "%";
        segment.style.backgroundColor = "var(--component-" + (part.index + 1) + ")"; track.appendChild(segment);
      });
      host.appendChild(track);
    } else host.appendChild(el("p", "muted", allKnown ? "暂无 Token 用量" : "分量数据不完整"));
    parts.forEach(function (p) {
      var share = allKnown && total > 0 ? " · " + H.percent(p.value / total) : "";
      metric(list, p.label, H.fmt(p.value) + share, "var(--component-" + (p.index + 1) + ")");
    });
    var extra = $("token-extra"); extra.textContent = "";
    metric(extra, "推理 Token", H.known(o.reasoning) ? H.fmt(o.reasoning) : "未提供");
    metric(extra, "缓存写入 Token", H.known(o.cache_write) ? H.fmt(o.cache_write) : "未提供");
    var performance = $("performance"); performance.textContent = "";
    metric(performance, "平均耗时", H.known(o.average_latency_ms) ? H.fmt(o.average_latency_ms) + " ms" : "暂无样本");
    metric(performance, "耗时样本", H.fmt(o.latency_samples));
    metric(performance, "每分钟请求数", H.fmt(o.rpm));
  }
  function renderAnalysis() {
    var rows = ((state.data || {}).analysis || {})[state.analysisGroup] || [], body = $("tbody"); body.textContent = "";
    ["tokens", "calls", "failed"].forEach(function (key) {
      var selected = state.sort.key === key, ascending = selected && state.sort.dir === "asc";
      var heading = $("th-" + key), button = $("sort-" + key);
      if (selected) heading.setAttribute("aria-sort", ascending ? "ascending" : "descending");
      else heading.removeAttribute("aria-sort");
      var label = { tokens: "Token", calls: "请求数", failed: "失败数" }[key];
      button.setAttribute("aria-label", "按" + label + (selected && !ascending ? "升序" : "降序") + "排列");
    });
    $("table").hidden = !rows.length; $("table-scroll").hidden = !rows.length; $("empty").hidden = Boolean(rows.length);
    $("table-note").textContent = rows.length ? "共 " + H.fmt(rows.length, 0) + " 项" : "";
    var total = rows.reduce(function (sum, row) { return sum + (H.known(row.tokens) ? row.tokens : 0); }, 0);
    H.sorted(rows, state.sort.key, state.sort.dir).forEach(function (row) {
      var tr = el("tr"), name = el("td"), kind = state.analysisGroup === "plugins" ? "plugin" : "model";
      var b = el("button", "name-button", H.displayLabel(row.label || row.key, kind)); b.type = "button";
      b.setAttribute("aria-label", "筛选 " + (row.label || row.key)); b.setAttribute("title", row.key);
      b.addEventListener("click", function () { var field = { plugins: "plugin", models: "model", providers: "provider" }[state.analysisGroup]; state[field] = row.key; $("filter-" + field).value = row.key; changed(); });
      name.appendChild(b); tr.appendChild(name);
      var usage = el("td", "num usage-cell"); usage.appendChild(el("span", "usage-value", H.fmt(row.tokens, 0)));
      if (total > 0 && H.known(row.tokens)) { var track = el("span", "usage-track"), fill = el("span", "usage-fill"); track.setAttribute("aria-hidden", "true"); fill.style.width = Math.max(0, Math.min(100, row.tokens / total * 100)) + "%"; track.appendChild(fill); usage.appendChild(track); }
      tr.appendChild(usage); ["calls", "failed"].forEach(function (key) { tr.appendChild(el("td", "num", H.fmt(row[key], 0))); }); body.appendChild(tr);
    });
  }
  var columns = [
    ["model", "模型", 146], ["result", "结果", 66], ["tokens", "Token", 92], ["timestamp", "时间", 164], ["plugin", "来源插件", 154], ["provider", "提供方", 126],
    ["input_other", "输入", 100], ["input_cached", "缓存读取", 108], ["output", "输出", 100],
    ["reasoning", "推理", 100], ["cache_write", "缓存写入", 110], ["latency_ms", "耗时 ms", 110], ["ttft_ms", "TTFT ms", 110],
    ["tps", "TPS", 100], ["estimated_cost", "估价 USD", 130], ["id", "记录 ID", 180]
  ].map(function (c) { return { key: c[0], label: c[1], width: c[2], defaultWidth: c[2], visible: ["reasoning", "cache_write", "ttft_ms", "id"].indexOf(c[0]) < 0 }; });
  function eventValue(item, key) {
    if (key === "timestamp") return dateText(item[key]);
    if (key === "estimated_cost") return H.money(item[key]);
    if (key === "result") return ({ success: "成功", failed: "失败", canceled: "取消" })[item[key]] || item[key] || "—";
    if (["id", "plugin", "provider", "model"].indexOf(key) >= 0) return item[key] === null || item[key] === undefined || item[key] === "" ? "—" : String(item[key]);
    return H.fmt(item[key]);
  }
  var compactEventTime = new Intl.DateTimeFormat(undefined, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });
  var responsiveColumns = ["model", "result", "tokens"];
  var numericColumns = ["tokens", "input_other", "input_cached", "output", "reasoning", "cache_write", "latency_ms", "ttft_ms", "tps", "estimated_cost"];
  function renderEvents() {
    var data = state.data || {}, events = data.events || {}, items = events.items || [], head = $("events-head"), body = $("events-body"), visible = columns.filter(function (c) { return c.visible; });
    head.textContent = ""; body.textContent = ""; var tr = el("tr");
    var timestampVisible = visible.some(function (c) { return c.key === "timestamp"; });
    visible.forEach(function (c) {
      var th = el("th", numericColumns.indexOf(c.key) >= 0 ? "num" : "", c.label);
      th.setAttribute("scope", "col");
      th.style.width = c.width === c.defaultWidth && responsiveColumns.indexOf(c.key) >= 0 ? "var(--event-" + c.key + "-width, " + c.width + "px)" : c.width + "px";
      tr.appendChild(th);
    });
    head.appendChild(tr);
    items.forEach(function (item) {
      var row = el("tr");
      visible.forEach(function (c) {
        var value = eventValue(item, c.key), cell = el("td", numericColumns.indexOf(c.key) >= 0 ? "num" : c.key === "result" && item.result !== "success" ? "event-non-success" : "", value);
        if (c.key === "model" && timestampVisible && H.known(item.timestamp)) {
          var time = el("small", "event-compact-time", compactEventTime.format(new Date(item.timestamp * 1000)));
          time.setAttribute("aria-hidden", "true"); cell.appendChild(time);
        }
        row.appendChild(cell);
      });
      body.appendChild(row);
    });
    var empty = !items.length, collection = data.collection || {}, coverage = data.coverage || {};
    $("events-table-scroll").hidden = empty; $("events-pager").hidden = empty; $("events-empty").hidden = !empty;
    var columnOptions = $("event-columns").parentNode; if (columnOptions && String(columnOptions.tagName).toLowerCase() === "details") columnOptions.hidden = empty;
    var disabled = empty && collection.detail_enabled === false && !(events.total > 0) && !(coverage.detail_calls > 0);
    $("events-empty-title").textContent = disabled ? "尚未开启调用明细" : "当前范围没有调用明细";
    $("events-empty-description").textContent = disabled ? "开启后会记录后续调用的明细，已有统计仍保留。" : "尝试调整时间或筛选条件。仅有日汇总的调用不会显示在明细列表中。";
    $("events-enable-details").hidden = !disabled; $("export-events").disabled = empty;
    if (empty) { var cell = el("td", "empty", "当前范围没有调用明细。"); cell.setAttribute("colspan", String(visible.length || 1)); var er = el("tr"); er.appendChild(cell); body.appendChild(er); }
    var page = events.page || state.page, pages = events.total_pages || 0;
    if (H.known(events.page) && events.page >= 1) state.page = Math.floor(events.page);
    $("page-note").textContent = "第 " + page + " / " + Math.max(1, pages) + " 页 · 共 " + H.fmt(events.total) + " 条";
    $("events-end-pager").hidden = empty || pages < 2;
    $("page-prev").disabled = $("page-end-prev").disabled = page <= 1;
    $("page-next").disabled = $("page-end-next").disabled = page >= pages;
  }
  columns.forEach(function (c) {
    var wrap = el("div", "column-control"), label = el("label"), check = el("input"), width = el("input"); check.type = "checkbox"; check.checked = c.visible;
    check.setAttribute("aria-label", "显示列 " + c.label); label.appendChild(check); label.appendChild(document.createTextNode(c.label));
    check.addEventListener("change", function () {
      if (!check.checked && columns.filter(function (col) { return col.visible; }).length === 1) { check.checked = true; return; }
      c.visible = check.checked; renderEvents();
    });
    width.type = "number"; width.min = "70"; width.max = "600"; width.value = String(c.width); width.setAttribute("aria-label", c.label + "列宽，像素");
    width.addEventListener("change", function () { c.width = Math.max(70, Math.min(600, Number(width.value) || c.width)); width.value = String(c.width); renderEvents(); });
    wrap.appendChild(label); wrap.appendChild(width); $("event-columns").appendChild(wrap);
  });
  function coverageMessage(message) {
    var rules = [
      ["Daily aggregates and detail contributions differ", "日汇总与明细贡献不一致，负残差已限制为零；请检查数据备份是否来自同一批次。"],
      ["Daily aggregates contain token residuals without calls", "日汇总存在没有对应调用数的 Token 残差，累计用量仍保留。"],
      ["Some details have no matching daily aggregate", "部分明细没有匹配日汇总，已单独计入一次。"],
      ["Legacy daily calls excluded", "部分调用仅有日汇总，无法按结果或一天中的部分时段精确筛选；这些调用未纳入当前结果，数量见上方。"],
      ["Legacy history has daily granularity", "部分调用仅有日汇总；耗时、首 Token 时间与 TPS 仅使用有相应明细的有效样本。"],
      ["Cache-write metadata exceeds normalized input", "缓存写入参考值超过归一化输入；总量保持原口径，相关调用不估价。"],
      ["Some calls did not report usage", "部分调用未提供完整用量；Token 与估价仅汇总已上报样本。"],
      ["Some calls did not report cache usage", "部分调用未上报缓存字段；缓存比率仅使用明确上报的样本。"],
      ["部分旧数据不含明细", "部分调用仅保留日汇总，没有对应明细。"],
      ["Partially cache-reported legacy buckets", "部分日汇总中的调用未全部提供缓存用量；缓存比率只使用缓存信息完整的日汇总或有效明细。"]
    ];
    if (message.indexOf("Trend coarsened") === 0) {
      var match = message.match(/to (\d+) /);
      return "趋势已合并相邻时间桶" + (match ? "，每点包含 " + match[1] + " 个原始区间" : "") + "；单次最多 366 点，累计值保留。";
    }
    for (var i = 0; i < rules.length; i++) if (message.indexOf(rules[i][0]) === 0) return rules[i][1];
    return message;
  }
  function renderCoverage(data) {
    var c = data.coverage || {}, collection = data.collection || {}, list = $("coverage"); list.textContent = "";
    $("filter-coverage").hidden = !(c.excluded_legacy_calls > 0);
    $("filter-coverage").textContent = c.excluded_legacy_calls > 0 ? "有 " + H.fmt(c.excluded_legacy_calls, 0) + " 次仅有日汇总的调用无法匹配本次筛选，未计入结果。" : "";
    metric(list, "仅有日汇总的调用", H.fmt(c.legacy_calls)); metric(list, "有明细的调用", H.fmt(c.detail_calls)); metric(list, "筛选未纳入的日汇总调用", H.fmt(c.excluded_legacy_calls)); metric(list, "最早保留明细", dateText(c.detail_start));
    var runtime = $("runtime-details"); runtime.textContent = "";
    metric(runtime, "采集包装方法数", H.fmt(collection.patched_count, 0)); metric(runtime, "本次记录调用", H.fmt(collection.recorded_count, 0));
    metric(runtime, "等待写入明细", H.fmt(collection.pending)); metric(runtime, "队列超限未保留明细", H.fmt(collection.dropped));
    var methods = $("wrapped-methods"); methods.textContent = "";
    if (!Array.isArray(collection.wrapped_methods)) methods.appendChild(el("li", "muted", "后端未返回方法清单。"));
    else if (!collection.wrapped_methods.length) methods.appendChild(el("li", "muted", "当前没有已包装方法。"));
    else {
      var names = collection.wrapped_methods.filter(function (name) { return typeof name === "string" && name.trim(); });
      if (!names.length) methods.appendChild(el("li", "muted", "后端未返回有效方法清单。"));
      names.forEach(function (name) { methods.appendChild(el("li", "", name)); });
    }
    $("coverage-warnings").textContent = ""; $("coverage-alerts").textContent = "";
    (c.warnings || []).forEach(function (message) {
      var raw = String(message), ordinary = /^(Legacy history has daily granularity|Trend coarsened|Some calls did not report|Some calls have|Partially cache-reported legacy buckets)/.test(raw) || /旧数据不含明细/.test(raw);
      var item = el("li", ordinary ? "" : /^(Daily aggregates|Some details have no matching)/.test(raw) ? "is-error" : "is-warning", coverageMessage(raw));
      $(ordinary ? "coverage-warnings" : "coverage-alerts").appendChild(item);
    });
    $("coverage-alerts").hidden = !$("coverage-alerts").children.length;
    var serious = Array.prototype.some.call($("coverage-alerts").children, function (item) { return item.className === "is-error"; });
    $("coverage-alerts").className = "coverage-alerts" + (serious ? " is-error" : " is-warning");
    $("coverage-details").hidden = !$("coverage-warnings").children.length;
    $("coverage-summary").textContent = "统计范围：" + rangeText(displayedQuery);
    $("collection-status").textContent = "明细采集：" + (collection.detail_enabled === true ? "已开启" : collection.detail_enabled === false ? "未启用" : "状态未知") + "；明细存储：" + (collection.detail_available === true ? "可用" : collection.detail_available === false ? "不可用" : "未知") + (collection.detail_error ? "；" + collection.detail_error : "");
  }
  var settingsSnapshot = null, settingsBaseRevision = null, settingsDirty = false;
  var settingsVersion = 0, settingsSaving = false, settingsRestoring = false, settingsSeen = false;
  var settingsInputs = ["settings-enabled", "settings-detail-history", "settings-keep-days", "settings-max-events"];
  function settingsRecord(value) {
    if (!value || typeof value.enabled !== "boolean" || typeof value.detail_history !== "boolean" || typeof value.writable !== "boolean"
      || !Number.isSafeInteger(value.keep_days) || !Number.isSafeInteger(value.detail_max_events)
      || !Number.isSafeInteger(value.revision) || value.revision < 0 || typeof value.reload_required !== "boolean" || typeof value.runtime_error !== "string") return null;
    return { enabled: value.enabled, detail_history: value.detail_history, keep_days: value.keep_days,
      detail_max_events: value.detail_max_events, writable: value.writable, revision: value.revision,
      reload_required: value.reload_required, runtime_error: value.runtime_error };
  }
  function settingsMessage(text, error) {
    $("settings-status").textContent = text;
    $("settings-status").classList.toggle("is-error", !!error);
  }
  function settingsHint() {
    var needed = $("settings-detail-history").checked && !$("settings-enabled").checked;
    $("settings-detail-hint").hidden = !needed;
    $("settings-detail-hint").textContent = needed ? "用量采集未启用，不会记录新明细。" : "";
    var keep = Number($("settings-keep-days").value), max = Number($("settings-max-events").value);
    var baseline = settingsSnapshot;
    var fewerDays = baseline && Number.isInteger(keep) && keep > 0 && keep <= 3650 && (baseline.keep_days === 0 || keep < baseline.keep_days);
    var fewerDetails = baseline && Number.isInteger(max) && max >= 1 && max <= 200000 && max < baseline.detail_max_events;
    var warning = $("settings-retention-warning");
    warning.hidden = !(fewerDays || fewerDetails);
    warning.textContent = fewerDays ? "保存统计时会按新天数清理日汇总和调用明细。" + (fewerDetails ? "还会按新条数上限清理调用明细。" : "")
      : fewerDetails ? "保存统计时会按新条数上限清理调用明细。" : "";
  }
  function settingsControls() {
    var writable = !!(settingsSnapshot && settingsSnapshot.writable);
    settingsInputs.forEach(function (id) { $(id).disabled = !writable; });
    $("settings-save").disabled = !writable || !settingsDirty || settingsSaving || settingsRestoring;
    $("settings-revert").disabled = !settingsSnapshot || !settingsDirty || settingsSaving || settingsRestoring;
    $("settings-save").textContent = settingsSaving ? "保存中…" : "保存设置";
    settingsHint();
  }
  function applySettings(value) {
    $("settings-enabled").checked = value.enabled;
    $("settings-detail-history").checked = value.detail_history;
    $("settings-keep-days").value = String(value.keep_days);
    $("settings-max-events").value = String(value.detail_max_events);
    settingsBaseRevision = value.revision;
  }
  function renderSettings(value) {
    var record = settingsRecord(value);
    if (!record) {
      settingsSnapshot = null;
      $("settings-runtime").textContent = "后端未返回完整采集设置，请更新后端后刷新。";
      $("settings-runtime").classList.add("is-error");
      if (!settingsDirty) settingsMessage("采集设置暂不可用。", true);
      settingsControls(); return;
    }
    settingsSnapshot = record;
    if (!settingsDirty && !settingsSaving && !settingsRestoring) applySettings(record);
    var notes = [];
    if (!record.writable) notes.push("当前宿主没有配置持久化接口，无法在此保存设置。");
    if (record.keep_days < 0 || record.keep_days > 3650 || record.detail_max_events < 1 || record.detail_max_events > 200000) notes.push("当前配置超出编辑范围，请将保留天数调整为 0-3650、明细数量调整为 1-200000 后保存。");
    if (record.reload_required) notes.push("设置已保存，尚未完全生效，请重载插件。");
    if (record.runtime_error) notes.push("运行状态：" + record.runtime_error);
    $("settings-runtime").textContent = notes.join(" ");
    $("settings-runtime").classList.toggle("is-error", !record.writable || !!record.runtime_error);
    $("settings-runtime").classList.toggle("is-warning", record.reload_required);
    if (!settingsSeen) { settingsMessage(""); settingsSeen = true; }
    settingsControls();
  }
  function settingsInteger(id, min, max, label) {
    var text = String($(id).value).trim(), value = Number(text);
    if (!/^\d+$/.test(text) || !Number.isInteger(value) || value < min || value > max) {
      $(id).setAttribute("aria-invalid", "true"); $(id).focus();
      throw new Error(label + "必须是 " + min + " 至 " + max + " 的整数。");
    }
    $(id).removeAttribute("aria-invalid"); return value;
  }
  function readSettingsDraft() {
    return { enabled: !!$("settings-enabled").checked, detail_history: !!$("settings-detail-history").checked,
      keep_days: settingsInteger("settings-keep-days", 0, 3650, "保留天数"),
      detail_max_events: settingsInteger("settings-max-events", 1, 200000, "明细数量上限") };
  }
  function settingsChanged() {
    if (!settingsSnapshot || !settingsSnapshot.writable) return;
    settingsDirty = true; settingsVersion++;
    settingsMessage("有未保存的修改。"); settingsControls();
  }
  function finishSettingsAction() {
    if (!settingsDirty && settingsSnapshot) applySettings(settingsSnapshot);
    settingsControls();
  }
  function saveSettings() {
    if (settingsSaving || settingsRestoring) return;
    if (!settingsSnapshot || !settingsSnapshot.writable) { settingsMessage("当前无法持久保存设置。", true); return; }
    if (!settingsDirty) return;
    var draft;
    try { draft = readSettingsDraft(); } catch (error) { settingsMessage(error.message, true); return; }
    var submittedVersion = settingsVersion, revision = settingsBaseRevision;
    settingsSaving = true; settingsMessage("正在保存采集设置…"); settingsControls();
    api.ready().then(function () { return api.apiPost("settings", { settings: draft, revision: revision }); }).then(function (result) {
      if (!result || result.saved !== true) throw new Error(result && result.error || "后端未确认设置已保存。");
      var record = settingsRecord(result.settings);
      if (!record || typeof result.applied !== "boolean" || typeof result.reload_required !== "boolean") throw new Error("后端未返回完整保存结果，请刷新确认。");
      settingsSnapshot = record; settingsBaseRevision = record.revision;
      if (submittedVersion === settingsVersion) { settingsDirty = false; applySettings(record); }
      var message = !result.applied || result.reload_required || record.reload_required ? "设置已保存，尚未完全生效，请重载插件。"
        : record.runtime_error ? "已保存，运行状态异常，请查看提示。" : "已保存并生效。";
      if (settingsDirty) message += "当前草稿仍有未保存修改。";
      settingsMessage(message);
      ++loadSeq; return load();
    }).catch(function (error) {
      settingsDirty = true;
      settingsMessage("未能确认保存：" + (error.message || error) + " 当前草稿已保留，可还原服务器设置后重试。", true);
      return load();
    }).then(function () { settingsSaving = false; finishSettingsAction(); });
  }
  function revertSettings() {
    if (settingsSaving || settingsRestoring || !settingsSnapshot) return;
    var requestedVersion = settingsVersion;
    settingsRestoring = true; settingsMessage("正在读取服务器设置…"); settingsControls();
    api.ready().then(function () { return api.apiGet("stats", { view: "dashboard", days: "1", page_size: 1 }); }).then(function (data) {
      var record = settingsRecord(data && data.settings);
      if (!record) throw new Error("后端未返回完整采集设置。");
      settingsSnapshot = record;
      if (requestedVersion === settingsVersion) {
        settingsDirty = false; applySettings(record); settingsMessage("已还原服务器设置。");
      } else settingsMessage("服务器设置已读取，当前草稿仍保留；可再次还原后修改。");
      ++loadSeq; return load();
    }).catch(function (error) { settingsMessage("无法读取服务器设置：" + (error.message || error) + " 当前草稿已保留。", true); })
      .then(function () { settingsRestoring = false; finishSettingsAction(); });
  }
  var priceFields = ["model", "provider", "input", "cache_read", "cache_write", "output"];
  var manualPrices = [], priceView = null, priceDraft = null, priceSearch = "", pricePage = 1, priceSyncing = false;
  var priceButtons = new Map(), priceOpenerKey = null, priceModeReady = false;
  function priceKey(value) { return JSON.stringify([value.provider || "", value.model]); }
  function copyManual(value) {
    var result = { model: value.model, provider: value.provider || "" };
    ["input", "cache_read", "cache_write", "output"].forEach(function (field) { result[field] = H.known(value[field]) ? value[field] : null; });
    return result;
  }
  function closePriceEditor() {
    closeContextHelp(false);
    priceDraft = null; pricesDirty = false; $("price-editor").hidden = true; $("price-edit-status").textContent = ""; $("price-scope-note").textContent = "";
    (priceOpenerKey && priceButtons.get(priceOpenerKey) || $("price-add")).focus();
  }
  function updatePriceScope() {
    var provider = $("price-edit-provider").value.trim(), model = $("price-edit-model").value.trim(), note = $("price-scope-note");
    var original = priceDraft && priceDraft.originalKey ? JSON.parse(priceDraft.originalKey) : null;
    var replaces = original && (original[0] !== provider || original[1] !== model);
    var scope = provider ? "专属价格：仅影响提供方「" + provider + "」" + (model ? "的「" + model + "」" : "") + "。"
      : "通用价格：未设专属价格的提供方也会使用。";
    note.textContent = (replaces ? "保存会替换原价格条目。" : "") + scope + (replaces ? "要保留原价，请取消并手动添加。" : "");
    note.className = "price-scope-note" + (replaces || !provider ? " is-warning" : "");
  }
  function editPrice(row) {
    if (priceSaving) { $("price-status").textContent = "价格正在保存，请稍后编辑其他条目。"; return; }
    if (priceDraft && pricesDirty) { $("price-status").textContent = "请先保存或取消当前价格编辑。"; return; }
    var manual = row ? H.manualPrice(manualPrices, row.model, row.provider) : null;
    var initial = manual || (row ? Object.assign({ model: row.model, provider: row.provider || "" }, row.price || {}) : {});
    priceDraft = { originalKey: manual ? priceKey(manual) : null }; priceOpenerKey = row ? priceKey(row) : null;
    priceVersion++; pricesDirty = false;
    priceFields.forEach(function (field) { $("price-edit-" + field).value = initial[field] === null || initial[field] === undefined ? "" : String(initial[field]); });
    $("price-editor-title").textContent = manual ? "编辑手工价格覆盖" : row ? "为模型设置手工覆盖" : "手动添加模型价格";
    $("price-remove").disabled = !manual; $("price-editor").hidden = false;
    $("price-status").textContent = ""; $("price-edit-status").textContent = ""; updatePriceScope(); $("price-edit-model").focus();
  }
  function renderPriceRows() {
    var view = priceView || { rows: [] }, mode = $("price-view").value === "all" ? "all" : "used";
    var rows = mode === "used" ? view.rows.filter(function (row) { return row.calls > 0; }) : view.rows;
    var page = H.pricePage(rows, priceSearch, pricePage, 50), focusedKey = null;
    priceButtons.forEach(function (button, key) { if (document.activeElement === button) focusedKey = key; });
    priceButtons = new Map(); pricePage = page.page; var body = $("prices-body"); body.textContent = "";
    page.rows.forEach(function (row) {
      var tr = el("tr", row.calls > 0 && row.unpriced_calls > 0 ? "price-row-unpriced" : ""), model = el("td"), name = el("span", "price-model-name", row.model);
      name.setAttribute("title", row.model); model.appendChild(name);
      var source = row.price_source === "manual" ? "手工覆盖" : row.price_source === "models.dev" ? "参考价" : "缺价";
      var origin = row.source_provider && row.source_provider !== row.provider ? " / " + row.source_provider : "";
      var subtitle = (row.provider || "通用") + " · " + source + origin;
      var note = el("small", "price-model-note", subtitle); note.setAttribute("title", subtitle + (row.price_source === "models.dev" ? "（Models.dev）" : "")); model.appendChild(note); tr.appendChild(model);
      var hasCost = H.known(row.estimated_cost) && row.calls !== 0;
      var nonePriced = row.calls > 0 && row.priced_calls === 0 && row.unpriced_calls > 0;
      var cost = el("td", "num price-cost" + (hasCost ? "" : " is-unavailable"), row.calls === 0 ? "未调用" : hasCost ? H.money(row.estimated_cost) : nonePriced ? "未计价" : "未估算");
      if (hasCost && row.unpriced_calls > 0) cost.appendChild(el("small", "price-model-note", "已计价 " + H.fmt(row.priced_calls, 0) + " / " + H.fmt(row.calls, 0) + " 次"));
      else if (nonePriced) cost.appendChild(el("small", "price-model-note", H.fmt(row.calls, 0) + " 次调用"));
      tr.appendChild(cost);
      var action = el("td"), edit = el("button", "price-edit-button", "编辑"); edit.type = "button"; edit.setAttribute("aria-label", "编辑价格：" + row.model + "（" + (row.provider || "通用") + "）"); edit.setAttribute("title", "编辑手工覆盖");
      edit.addEventListener("click", function () { editPrice(row); }); priceButtons.set(priceKey(row), edit); action.appendChild(edit); tr.appendChild(action);
      tr.appendChild(el("td", "num", H.fmt(row.calls, 0))); tr.appendChild(el("td", "num", H.fmt(row.tokens, 0)));
      ["input", "output", "cache_read", "cache_write"].forEach(function (field) { tr.appendChild(el("td", "num", H.quote(row.price && row.price[field]))); });
      body.appendChild(tr);
    });
    if (!page.rows.length) {
      var empty = el("tr"), cell = el("td", "empty", priceSearch ? "没有匹配的模型或提供方。" : mode === "used" ? "当前范围没有已用模型。" : "当前筛选下没有模型价格。可更新目录或手动添加。"); cell.setAttribute("colspan", "9");
      if (priceSearch) { var clear = el("button", "empty-action", "清空搜索"); clear.type = "button"; clear.addEventListener("click", function () { priceSearch = ""; $("price-search").value = ""; pricePage = 1; renderPriceRows(); $("price-search").focus(); }); cell.appendChild(clear); }
      else if (mode === "used") { var all = el("button", "empty-action", "查看全部目录"); all.type = "button"; all.addEventListener("click", function () { $("price-view").value = "all"; pricePage = 1; renderPriceRows(); }); cell.appendChild(all); }
      empty.appendChild(cell); body.appendChild(empty);
    }
    $("price-page-note").textContent = page.start + "-" + page.end + " / " + page.total + " 项";
    $("price-prev").disabled = page.page <= 1; $("price-next").disabled = page.page >= page.pages; $("price-pager").hidden = page.total <= 50;
    if (focusedKey) (priceButtons.get(focusedKey) || $("price-add")).focus();
  }
  function renderPrices(data) {
    if (Array.isArray(data.prices)) manualPrices = data.prices.map(copyManual);
    priceView = H.pricing(data);
    $("price-total").textContent = H.moneyShort(priceView.total_cost); $("price-total").setAttribute("title", H.money(priceView.total_cost));
    if (!priceModeReady) { $("price-view").value = priceView.rows.some(function (row) { return row.calls > 0; }) ? "used" : "all"; priceModeReady = true; }
    var total = H.known(priceView.priced_calls) && H.known(priceView.unpriced_calls) ? priceView.priced_calls + priceView.unpriced_calls : null;
    $("price-summary").textContent = total === 0 ? "当前范围无调用" : H.known(total) ? "已计价 " + H.fmt(priceView.priced_calls, 0) + " / " + H.fmt(total, 0) + " 次调用" : "暂无计价覆盖数据";
    $("price-source-state").textContent = H.known(priceView.updated_at) ? "更新于 " + dateText(priceView.updated_at) : "参考价尚未同步";
    if (data.pricing && data.pricing.sync_error) $("price-source-state").textContent += " · " + data.pricing.sync_error;
    $("price-compat").hidden = priceView.supported;
    $("price-compat").textContent = priceView.supported ? "" : "后端未提供完整模型价格统计，当前展示已有模型与手工价格；逐模型费用未知，请更新后端。";
    renderPriceRows();
  }
  function renderOptions(options) {
    ["model", "plugin", "provider"].forEach(function (key) {
      var select = $("filter-" + key), values = options[key + "s"] || [], label = { model: "全部模型", plugin: "全部插件", provider: "全部提供方" }[key];
      if (document.activeElement === select) return;
      select.textContent = ""; var all = el("option", "", label); all.value = ""; select.appendChild(all);
      // Only backend time-window candidates become options; a selected missing value is explicitly retained.
      values.forEach(function (value) { var option = el("option", "", H.displayLabel(value, key)); option.value = value; select.appendChild(option); });
      if (state[key] && values.indexOf(state[key]) < 0) { var absent = el("option", "", state[key] + "（当前时间范围无记录）"); absent.value = state[key]; select.appendChild(absent); }
      select.value = state[key];
    });
  }
  function render(data, params) {
    state.data = data; displayedQuery = Object.assign({}, params || H.query(state));
    renderKpis(data.overview || {}); renderChart(); renderDistribution(data.overview || {}); renderAnalysis(); renderEvents(); renderCoverage(data); renderSettings(data.settings); renderPrices(data); renderOptions(data.options || {});
    var summary = rangeText(displayedQuery); $("summary").textContent = summary; $("stats-range-summary").textContent = summary; $("stats-range-summary").setAttribute("title", summary);
  }
  function fetchStats(params, retries, seq) {
    return api.apiGet("stats", params).catch(function (error) {
      if (seq !== loadSeq || retries <= 0) throw error;
      return new Promise(function (resolve) { setTimeout(resolve, 500); }).then(function () {
        if (seq !== loadSeq) throw error; return fetchStats(params, retries - 1, seq);
      });
    });
  }
  function failedLoad(error) {
    setStatus("读取失败", "error");
    $("status").setAttribute("title", String(error.message || error));
    feedback(state.data ? "未能更新，仍显示上次成功结果：" + rangeText(displayedQuery) + "。" : !api ? "请从 AstrBot 的插件页面打开用量统计。" : "无法读取统计。", "error");
    if (!state.data) {
      $("chart").textContent = "暂时无法读取统计。"; $("tbody").textContent = ""; $("table").hidden = true; $("table-scroll").hidden = true;
      $("price-summary").textContent = "价格统计读取失败，请重新读取。"; $("price-source-state").textContent = "尚未取得价格状态。";
      $("settings-status").textContent = "采集设置读取失败，未修改任何设置。"; $("settings-runtime").textContent = "请重新读取后再编辑。";
      $("events-empty").hidden = false; $("events-empty-title").textContent = "请求明细读取失败"; $("events-empty-description").textContent = "请重新读取统计后查看。";
      $("events-enable-details").hidden = true; $("events-table-scroll").hidden = true; $("events-pager").hidden = true;
    }
    $("export-events").disabled = !displayedQuery || !((state.data || {}).events || {}).items || !state.data.events.items.length;
  }
  function load(options) {
    var seq = ++loadSeq, params, automatic = options && options.automatic === true;
    try { params = H.query(state); } catch (error) { $("filter-note").textContent = error.message; $("filter-note").className = "filter-error"; $("dashboard").setAttribute("aria-busy", "false"); return Promise.resolve(); }
    setStatus("加载中…", "loading"); $("dashboard").setAttribute("aria-busy", "true");
    if (state.data && !sameQuery(displayedQuery, params)) { feedback("正在读取所选数据，暂时显示：" + rangeText(displayedQuery) + "。", "info"); $("export-events").disabled = true; }
    if (!api) { failedLoad(new Error("桥接未加载，请从 AstrBot 插件页面打开。")); $("dashboard").setAttribute("aria-busy", "false"); return Promise.resolve(); }
    return api.ready().then(function (ctx) {
      if (ctx && typeof ctx.isDark === "boolean") { hostDark = ctx.isDark; applyTheme(); }
      return fetchStats(params, 2, seq);
    }).then(function (data) {
      if (seq !== loadSeq) return;
      if (!data || !data.overview) throw new Error("统计响应缺少 dashboard overview，请更新后端。");
      if (automatic && editingDynamicRegion()) {
        var previous = (state.data || {}).collection || {}, previousPaused = previous.plugin_enabled === false || previous.patched_count === 0;
        setStatus(previous.plugin_enabled === false ? "采集未启用" : previous.patched_count === 0 ? "等待采集就绪" : previous.plugin_enabled === true ? "采集中" : "已更新", previousPaused ? "paused" : "");
        return;
      }
      render(data, params); feedback("", "");
      var collection = data.collection || {}, paused = collection.plugin_enabled === false || collection.patched_count === 0;
      setStatus(collection.plugin_enabled === false ? "采集未启用" : collection.patched_count === 0 ? "等待采集就绪" : collection.plugin_enabled === true ? "采集中" : "已更新", paused ? "paused" : "");
      var updated = new Date().toLocaleTimeString(); $("status").setAttribute("title", "数据读取时间 · " + updated); $("data-asof").textContent = "更新于 " + updated;
    }).catch(function (error) { if (seq === loadSeq) failedLoad(error); })
      .then(function () { if (seq === loadSeq) $("dashboard").setAttribute("aria-busy", "false"); });
  }
  function changed() {
    state.page = 1; pricePage = 1; state.hiddenSeries.clear(); ++exportSeq;
    $("filter-note").className = "sr-only"; $("export-status").textContent = ""; $("filter-note").textContent = "筛选应用到所有统计视图；调用日志仍按最近条数读取。";
    return load();
  }
  $("range").addEventListener("change", function () {
    var draftCustom = $("range").value === "custom"; $("custom-range").hidden = !draftCustom;
    if (draftCustom) { $("filter-note").textContent = "填写起止时间并应用；当前仍显示已应用范围。"; $("filter-note").className = "filter-draft"; }
    else { state.custom = false; state.days = Number($("range").value); storageSet(RANGE_KEY, state.days); changed(); }
  });
  $("apply-range").addEventListener("click", function () {
    var draft = Object.assign({}, state, { custom: true, start: $("start").value, end: $("end").value });
    try { H.query(draft); } catch (error) { $("filter-note").textContent = error.message; $("filter-note").className = "filter-error"; return; }
    state.custom = true; state.start = draft.start; state.end = draft.end; changed();
  });
  ["model", "plugin", "provider", "result"].forEach(function (key) { $("filter-" + key).addEventListener("change", function () { state[key] = $("filter-" + key).value; changed(); }); });
  $("filter-toggle").addEventListener("click", function () {
    var button = $("filter-toggle"), open = button.getAttribute("aria-expanded") !== "true";
    button.setAttribute("aria-expanded", String(open)); button.textContent = open ? "收起筛选" : "筛选";
    $("filterbar").classList.toggle("is-expanded", open);
  });
  $("clear-filters").addEventListener("click", function () { ["model", "plugin", "provider", "result"].forEach(function (key) { state[key] = key === "result" ? "all" : ""; $("filter-" + key).value = state[key]; }); changed(); });
  $("group").addEventListener("click", function (ev) { var b = ev.target.closest("button[data-group]"); if (!b) return; state.group = b.dataset.group; storageSet(GROUP_KEY, state.group); syncSeg("group", "group", state.group); changed(); });
  $("analysis-group").addEventListener("change", function () { state.analysisGroup = $("analysis-group").value; renderAnalysis(); });
  ["tokens", "calls", "failed"].forEach(function (key) { $("sort-" + key).addEventListener("click", function () { state.sort.dir = state.sort.key === key && state.sort.dir === "desc" ? "asc" : "desc"; state.sort.key = key; renderAnalysis(); }); });
  $("page-prev").addEventListener("click", function () { if (state.page > 1) { state.page--; load(); } });
  $("page-next").addEventListener("click", function () { var events = (state.data || {}).events || {}; if (state.page < events.total_pages) { state.page++; load(); } });
  ["prev", "next"].forEach(function (direction) {
    $("page-end-" + direction).addEventListener("click", function () { $("page-" + direction).click(); $("page-note").focus(); });
  });
  $("page-size").addEventListener("change", function () { state.page_size = Math.min(200, Number($("page-size").value) || 50); changed(); });
  function download(content, type, name) {
    var url = URL.createObjectURL(new Blob([content], { type: type })), a = el("a"); a.href = url; a.download = name;
    a.click(); setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }
  $("export-events").addEventListener("click", function () {
    var query, seq = ++exportSeq;
    try { if (!displayedQuery) throw new Error("请先读取统计。"); query = Object.assign({}, displayedQuery, { view: "export" }); } catch (error) { $("export-status").textContent = error.message; return; }
    if (!api) return;
    $("export-events").disabled = true; $("export-status").textContent = "正在导出明细…";
    api.apiGet("stats", query).then(function (result) {
      if (seq !== exportSeq) return;
      if (!result || typeof result.csv !== "string") throw new Error("导出响应无效");
      download(result.csv, "text/csv;charset=utf-8", "usage-details-" + new Date().toISOString().slice(0, 10) + ".csv");
      $("export-status").textContent = "已导出 " + H.fmt(result.returned) + " / " + H.fmt(result.total) + " 条调用明细" + (result.truncated ? "（达到 5000 行上限）" : "") + "。";
    }).catch(function (error) { if (seq === exportSeq) $("export-status").textContent = "导出失败：" + (error.message || error); })
      .then(function () { if (seq === exportSeq) $("export-events").disabled = false; });
  });
  $("price-add").addEventListener("click", function () { editPrice(null); });
  $("price-cancel").addEventListener("click", function () { if (!priceSaving) { closePriceEditor(); $("price-status").textContent = ""; } });
  priceFields.forEach(function (field) { $("price-edit-" + field).addEventListener("input", function () { priceVersion++; pricesDirty = true; updatePriceScope(); $("price-edit-status").textContent = "有未保存的价格修改。"; }); });
  $("price-search").addEventListener("input", function () { priceSearch = $("price-search").value; pricePage = 1; renderPriceRows(); });
  $("price-prev").addEventListener("click", function () { if (pricePage > 1) { pricePage--; renderPriceRows(); } });
  $("price-next").addEventListener("click", function () { pricePage++; renderPriceRows(); });
  function persistPrice(remove) {
    if (priceSaving || !priceDraft) return;
    var prices, value, key, version = priceVersion, draft = priceDraft;
    try {
      if (remove && !draft.originalKey) return;
      if (!remove) {
        value = { model: $("price-edit-model").value.trim(), provider: $("price-edit-provider").value.trim() };
        if (!value.model) throw new Error("请填写原始模型名。");
        ["input", "cache_read", "cache_write", "output"].forEach(function (field) { value[field] = H.price($("price-edit-" + field).value); });
        key = priceKey(value);
        if (key !== draft.originalKey && manualPrices.some(function (p) { return priceKey(p) === key; })) throw new Error("此模型与 Provider 已有手工覆盖，请编辑对应条目。");
      }
      prices = manualPrices.filter(function (p) { return priceKey(p) !== draft.originalKey; }).map(copyManual);
      if (!remove) prices.push(value);
    } catch (error) { $("price-edit-status").textContent = error.message; return; }
    if (!api || !api.apiPost) { $("price-edit-status").textContent = "桥接不支持写入。"; return; }
    priceSaving = true; $("price-save").disabled = true; $("price-remove").disabled = true; $("price-cancel").disabled = true; $("price-edit-status").textContent = remove ? "正在删除手工覆盖…" : "保存中…";
    api.apiPost("prices", { prices: prices }).then(function (result) {
      if (!result || result.saved !== true) throw new Error("后端未确认保存成功。");
      manualPrices = prices; pricesDirty = priceVersion !== version;
      if (draft === priceDraft) { draft.originalKey = remove ? null : key; if (!pricesDirty) closePriceEditor(); }
      $("price-status").textContent = remove ? "手工覆盖已删除，将按剩余匹配价格重新估算；无匹配价格的部分仍显示缺价。" : "手工价格已保存。";
      if (pricesDirty) $("price-edit-status").textContent = "前一版本已保存，当前编辑仍有未保存修改。";
      return load();
    }).catch(function (error) { $("price-edit-status").textContent = "保存失败：" + (error.message || error); })
      .then(function () { priceSaving = false; $("price-save").disabled = false; $("price-remove").disabled = !priceDraft || !priceDraft.originalKey; $("price-cancel").disabled = false; });
  }
  $("price-save").addEventListener("click", function () { persistPrice(false); });
  $("price-remove").addEventListener("click", function () { persistPrice(true); });
  $("price-sync").addEventListener("click", function () {
    if (priceSyncing) return;
    if (!api || !api.apiPost) { $("price-status").textContent = "桥接不支持价格同步。"; return; }
    priceSyncing = true; $("price-sync").disabled = true; $("price-sync").textContent = "更新中…"; $("price-status").textContent = "正在获取 Models.dev 公共参考价，手工覆盖不会被改写…";
    api.apiPost("price_sync", { source: "models.dev" }).then(function (result) {
      if (!result || result.synced !== true || !Number.isInteger(result.rows) || result.rows < 0 || result.source !== "models.dev" || !H.known(result.updated_at)) throw new Error("后端未确认公共价格同步成功。");
      $("price-status").textContent = "公共参考价已更新：" + H.fmt(result.rows, 0) + " 项。";
      return load();
    }).catch(function (error) { $("price-status").textContent = "价格更新失败：" + (error.message || error) + "；原有目录和手工价格继续保留。"; })
      .then(function () { priceSyncing = false; $("price-sync").disabled = false; $("price-sync").textContent = "更新参考价"; });
  });
  var resetBtn = $("reset-stats"), resetArmed = false, resetReady = false, resetTimer = 0, resetReadyTimer = 0, resetting = false;
  function disarmReset() {
    resetArmed = false; resetReady = false; clearTimeout(resetTimer); clearTimeout(resetReadyTimer);
    resetBtn.textContent = "重置统计"; resetBtn.disabled = resetting; resetBtn.classList.remove("armed");
    $("reset-confirmation").hidden = true; $("reset-confirm").disabled = true; $("reset-cancel").hidden = true;
  }
  function cancelReset() { disarmReset(); $("reset-status").textContent = "已取消重置。"; resetBtn.focus(); }
  $("reset-cancel").addEventListener("click", cancelReset);
  [resetBtn, $("reset-confirm"), $("reset-cancel")].forEach(function (button) { button.addEventListener("keydown", function (ev) { if (ev.key === "Escape") cancelReset(); }); });
  resetBtn.addEventListener("click", function () {
    if (resetting || resetArmed) return;
    resetArmed = true; resetReady = false; resetBtn.disabled = true; $("reset-confirmation").hidden = false;
    $("reset-cancel").hidden = false; $("reset-confirm").disabled = true;
    $("reset-status").textContent = "";
    $("reset-cancel").focus();
    resetReadyTimer = setTimeout(function () { if (resetArmed && state.tab === "manage") { resetReady = true; $("reset-confirm").disabled = false; } }, 800);
    resetTimer = setTimeout(function () {
      var focus = document.activeElement; disarmReset();
      $("reset-status").textContent = "确认超时，未清空数据。";
      if (focus === $("reset-confirm") || focus === $("reset-cancel")) resetBtn.focus();
    }, 5000);
  });
  $("reset-confirm").addEventListener("click", function () {
    if (!resetArmed || !resetReady || resetting) return;
    if (!api || !api.apiPost) { disarmReset(); $("reset-status").textContent = "桥接不支持写入，无法重置。"; resetBtn.focus(); return; }
    resetting = true; disarmReset(); ++loadSeq; ++exportSeq; ++logGeneration;
    $("export-events").disabled = true; $("export-status").textContent = "";
    clearLog();
    $("log-status").textContent = "日志视图已清空，文件仍保留。刷新可重新读取。";
    $("log-refresh").disabled = true; $("reset-status").textContent = "正在重置统计…";
    api.apiPost("reset", {}).then(function (result) {
      if (!result || result.saved !== true) throw new Error(result && result.note || "部分数据未能重置，请检查存储后重试。");
      $("reset-status").textContent = "已重置统计。采集设置、价格与文本日志保留。";
      return load();
    }).catch(function (error) { $("reset-status").textContent = "重置失败：" + (error.message || error); })
      .then(function () { resetting = false; resetBtn.disabled = false; $("log-refresh").disabled = false; $("export-events").disabled = !(((state.data || {}).events || {}).items || []).length; if (state.tab === "manage") resetBtn.focus(); });
  });
  var logLines = [], logRecentLines = [];
  var logTail = 200;
  var logGeneration = 0;
  var LOG_PAGE_SIZE = 50;
  var LOG_TIMESTAMP = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?= |$)/;
  var LOG_RECORD = /^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (\S+) (.+?) (text_chat(?:_stream)?) in=(\d+) hit=(\d+) out=(\d+)(?: cache=(\d+%))?(?: sys=\d+ ctx=\d+\/\d+ prompt=\d+ tools=\d+\/\d+ img=\d+ aud=\d+ ms=(\d+))? ok=([01]) .+$/;

  function inOf(line) {
    var m = /(?:^| )in=(\d+)/.exec(line);
    return m ? Number(m[1]) : -1;
  }

  function logEntry(line) {
    var match = LOG_RECORD.exec(line);
    var entry = el("details", "log-entry" + (match ? "" : " log-entry-fallback"));
    var summary = el("summary", "log-entry-summary"), detail = el("div", "log-entry-detail");
    if (match) {
      var split = match[3].indexOf("|"), provider = split > 0 ? match[3].slice(0, split) : "";
      var source = el("span", "log-source", H.displayLabel(match[2], "plugin") + (provider ? " · " + provider : ""));
      source.setAttribute("title", match[2] + (provider ? " · " + provider : ""));
      var model = el("span", "log-model", provider && split + 1 < match[3].length ? match[3].slice(split + 1) : match[3]);
      model.setAttribute("title", match[3]);
      summary.appendChild(el("span", "log-time", match[1]));
      summary.appendChild(model);
      summary.appendChild(source);
      summary.appendChild(el("span", "log-usage", "入 " + H.fmt(Number(match[5]), 0) + " / 出 " + H.fmt(Number(match[7]), 0)));
      summary.appendChild(el("span", "log-elapsed", match[9] === undefined ? "—" : H.fmt(Number(match[9]), 0) + " ms"));
      summary.appendChild(el("span", "log-result" + (match[10] === "0" ? " is-unsuccessful" : ""), match[10] === "0" ? "未成功" : "成功"));
      var meta = el("dl", "log-entry-meta");
      metric(meta, "调用方式", match[4]);
      metric(meta, "缓存命中 Token", H.fmt(Number(match[6]), 0) + (match[8] ? "（" + match[8] + "）" : ""));
      detail.appendChild(meta);
    } else {
      summary.appendChild(el("span", "log-fallback-line", "无法识别的日志格式 · 展开查看原文"));
    }
    var original = el("pre", "log-line-original", line);
    original.setAttribute("aria-label", "调用记录原文");
    detail.appendChild(original);
    entry.appendChild(summary);
    entry.appendChild(detail);
    return entry;
  }

  function displayedLogLines() {
    return $("log-order").value === "input" ? logLines : logRecentLines;
  }

  function appendLogPage(moveFocus) {
    var reader = $("log-reader"), lines = displayedLogLines(), start = reader.children.length;
    var end = Math.min(start + LOG_PAGE_SIZE, lines.length);
    for (var i = start; i < end; i++) reader.appendChild(logEntry(lines[i]));
    if (moveFocus && end > start) reader.children[start].children[0].focus();
  }

  function renderLogView() {
    var lines = displayedLogLines(), raw = $("log-view").value === "raw";
    var reader = $("log-reader"), body = $("log-body"), more = $("log-more");
    reader.hidden = raw || !lines.length;
    $("log-column-labels").hidden = reader.hidden;
    body.hidden = !raw || !lines.length;
    $("log-wrap-control").hidden = !raw;
    body.textContent = raw ? lines.join("\n") : "";
    if (!raw && !reader.children.length) appendLogPage(false);
    var shown = reader.children.length;
    more.hidden = raw || shown >= lines.length;
    if (!more.hidden) more.textContent = "继续显示 " + Math.min(LOG_PAGE_SIZE, lines.length - shown) + " 条";
    $("log-note").textContent = !more.hidden ? "已展开 " + H.fmt(shown, 0) + " / " + H.fmt(lines.length, 0) + " 条" : "";
  }

  function clearLog() {
    logLines = []; logRecentLines = [];
    $("log-reader").textContent = "";
    $("log-export").disabled = true;
    $("log-export-status").textContent = "";
    renderLogView();
  }

  function renderLog(payload, generation) {
    if (generation !== logGeneration) return;
    clearLog();
    var status = $("log-status"), state = payload && payload.state;
    if (!payload || !Array.isArray(payload.lines) || !state) {
      status.textContent = "调用日志响应无效，请重试。";
      return;
    }
    if (state === "disabled") status.textContent = "调用日志未启用。可在插件配置中启用；此功能默认关闭。";
    else if (state === "empty") status.textContent = "暂无已写入的日志。稍后可刷新。";
    else if (state === "unavailable") status.textContent = payload.error_code === "path_unavailable" ? "调用日志数据目录不可用，请检查插件数据目录。" : "调用日志暂时无法读取，请稍后重试。";
    else if (state === "invalid_request") status.textContent = "读取条数无效，请重新选择后重试。";
    else if (state === "partial") status.textContent = "最新一批日志可能尚未写入。";
    else if (state === "truncated") status.textContent = "仅显示最近 " + (payload.returned || 0) + " 条；结果受读取上限限制。";
    else if (state === "ready") status.textContent = "已读取最近 " + H.fmt(payload.returned, 0) + " 条日志。";
    else { status.textContent = "调用日志状态未知，请重试。"; return; }
    if (["ready", "truncated", "partial"].indexOf(state) < 0) return;
    // Keep the historical export order, independently of the reader's displayed order.
    logLines = payload.lines.slice().sort(function (a, b) { return inOf(b) - inOf(a); });
    logRecentLines = payload.lines.slice().reverse().map(function (line) {
      var time = LOG_TIMESTAMP.exec(line);
      return { line: line, time: time ? time[0] : "" };
    }).sort(function (a, b) {
      return a.time < b.time ? 1 : a.time > b.time ? -1 : 0;
    }).map(function (record) { return record.line; });
    $("log-export").disabled = !logLines.length;
    renderLogView();
  }

  function loadLog() {
    var generation = ++logGeneration;
    if (!api || !api.apiGet) {
      clearLog();
      $("log-status").textContent = "日志接口不可用，请刷新页面。";
      return;
    }
    $("log-export-status").textContent = "";
    $("log-status").textContent = "正在读取调用日志。";
    $("log-refresh").disabled = true;
    $("log-export").disabled = true;
    api.apiGet("stats", { tail: logTail }).then(function (payload) {
      renderLog(payload, generation);
    }).catch(function () {
      if (generation !== logGeneration) return;
      clearLog();
      $("log-status").textContent = "调用日志读取失败，请重试。";
    }).then(function () {
      if (generation === logGeneration) $("log-refresh").disabled = false;
    });
  }

  function exportLog() {
    if ($("log-export").disabled || !logLines.length) return;
    var stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
    var blob = new Blob([logLines.join("\n") + "\n"], { type: "text/plain;charset=utf-8" });
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = "usage-calls-" + stamp + ".log";
    a.click();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    $("log-export-status").textContent = "已导出 " + H.fmt(logLines.length, 0) + " 条原文。";
  }

  $("log-size").addEventListener("click", function (ev) {
    var btn = ev.target.closest("button[data-tail]");
    if (!btn) return;
    logTail = Number(btn.dataset.tail);
    Array.prototype.forEach.call($("log-size").children, function (b) {
      var selected = b.dataset.tail === btn.dataset.tail;
      b.classList.toggle("on", selected);
      b.setAttribute("aria-pressed", String(selected));
    });
    loadLog();
  });
  $("log-refresh").addEventListener("click", loadLog);
  $("log-export").addEventListener("click", exportLog);
  $("log-view").addEventListener("change", renderLogView);
  $("log-order").addEventListener("change", function () {
    $("log-reader").textContent = "";
    renderLogView();
  });
  $("log-more").addEventListener("click", function () {
    if ($("log-more").hidden) return;
    appendLogPage(true);
    renderLogView();
  });


  var activeContextHelp = null;
  function closeContextHelp(restoreFocus) {
    if (!activeContextHelp) return;
    var help = activeContextHelp; activeContextHelp = null; help.open = false;
    if (restoreFocus) help.querySelector("summary").focus();
  }
  // The native summary owns opening and exposes its expanded state.
  document.addEventListener("toggle", function (event) {
    var help = event.target;
    if (!help.matches || !help.matches("details.context-help")) return;
    if (help.open && help !== activeContextHelp) { closeContextHelp(false); activeContextHelp = help; }
    else if (!help.open && help === activeContextHelp) activeContextHelp = null;
  }, true);
  document.addEventListener("pointerdown", function (event) {
    if (activeContextHelp && !activeContextHelp.contains(event.target)) closeContextHelp(false);
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && activeContextHelp) {
      event.preventDefault(); closeContextHelp(activeContextHelp.contains(document.activeElement));
    }
  });
  var tabNames = ["stats", "analysis", "events", "prices", "log", "manage"];
  function showTab(name) {
    closeContextHelp(false);
    state.tab = tabNames.indexOf(name) >= 0 ? name : "stats";
    tabNames.forEach(function (tab) { $("tab-" + tab).hidden = state.tab !== tab; });
    var nav = $("tabs"), current = $("tab-" + state.tab + "-btn");
    Array.prototype.forEach.call(nav.children, function (b) { var selected = b === current; b.setAttribute("aria-selected", String(selected)); b.tabIndex = selected ? 0 : -1; b.classList.toggle("on", selected); });
    if (nav.scrollWidth > nav.clientWidth) {
      var strip = nav.getBoundingClientRect(), button = current.getBoundingClientRect();
      if (button.left < strip.left) nav.scrollLeft += button.left - strip.left;
      else if (button.right > strip.right) nav.scrollLeft += button.right - strip.right;
    }
    storageSet(TAB_KEY, state.tab);
    $("filterbar").hidden = state.tab === "log" || state.tab === "manage";
    var viewingLog = state.tab === "log";
    $("data-asof").hidden = viewingLog;
    $("status").hidden = viewingLog;
    $("stats-range-summary").hidden = state.tab === "log" || state.tab === "manage";
    var refreshLabel = viewingLog ? "刷新调用日志" : "刷新统计";
    $("refresh").setAttribute("aria-label", refreshLabel);
    $("refresh").setAttribute("title", refreshLabel);
    feedback(feedbackText, feedbackKind);
    if (state.tab !== "manage") disarmReset();
    if (state.tab === "log" && !logLines.length) loadLog();
  }
  $("tabs").addEventListener("click", function (ev) { var b = ev.target.closest("button[data-tab]"); if (b && b.dataset.tab !== state.tab) showTab(b.dataset.tab); });
  $("tabs").addEventListener("keydown", function (ev) {
    var buttons = $("tabs").children, current = Array.prototype.indexOf.call(buttons, ev.target.closest("button[data-tab]")), next = current;
    if (current < 0) return;
    if (ev.key === "ArrowRight") next = (current + 1) % buttons.length; else if (ev.key === "ArrowLeft") next = (current - 1 + buttons.length) % buttons.length;
    else if (ev.key === "Home") next = 0; else if (ev.key === "End") next = buttons.length - 1; else return;
    ev.preventDefault(); showTab(buttons[next].dataset.tab); buttons[next].focus();
  });
  settingsInputs.forEach(function (id) { $(id).addEventListener("input", settingsChanged); $(id).addEventListener("change", settingsChanged); });
  $("settings-save").addEventListener("click", saveSettings);
  $("settings-revert").addEventListener("click", revertSettings);
  $("refresh").addEventListener("click", function () { if (state.tab === "log") loadLog(); else load(); });
  $("events-enable-details").addEventListener("click", goToDetailsSettings);
  $("performance-enable-details").addEventListener("click", goToDetailsSettings);
  $("coverage-open").addEventListener("click", function () { showTab("manage"); $("coverage-summary").focus(); });
  $("price-view").addEventListener("change", function () { priceModeReady = true; pricePage = 1; renderPriceRows(); });
  $("log-wrap").addEventListener("change", function () { $("log-body").classList.toggle("is-wrapped", $("log-wrap").checked); });
  $("page-size").value = String(state.page_size);
  if (typeof window.ResizeObserver === "function") {
    var chartResizeFrame = 0;
    new window.ResizeObserver(function () {
      if (chartResizeFrame) return;
      // Write in the next frame, outside the ResizeObserver delivery cycle.
      chartResizeFrame = window.requestAnimationFrame(function () {
        chartResizeFrame = 0;
        var host = $("chart"), width = host.clientWidth;
        if (!state.data || state.tab !== "stats" || !width || width === chartWidth) return;
        var focused = document.activeElement, legend = $("legend");
        var index = Array.prototype.indexOf.call(host.querySelectorAll(".chart-bar"), focused);
        var seriesIndex = Array.prototype.indexOf.call(legend.children, focused);
        renderChart();
        if (seriesIndex >= 0 && legend.children[seriesIndex]) legend.children[seriesIndex].focus();
        if (index >= 0) {
          var bars = host.querySelectorAll(".chart-bar");
          if (bars[index]) { bars[0].setAttribute("tabindex", "-1"); bars[index].setAttribute("tabindex", "0"); bars[index].focus(); }
        }
      });
    }).observe($("chart"));
  }
  $("range").value = String(state.days); syncSeg("group", "group", state.group); showTab(storageGet(TAB_KEY));
  load();
  setInterval(function () { if (!document.hidden && state.tab !== "log" && !resetting && !priceSaving && !editingDynamicRegion()) load({ automatic: true }); }, 30000);
})();
