/* Usage dashboard. All remote text is rendered with textContent; all GETs use stats. */
(function () {
  "use strict";
  var api = window.AstrBotPluginPage || window.AstrBotPluginView;
  var H = window.UsageDashboard;
  var state = { days: 30, custom: false, start: "", end: "", plugin: "", model: "", provider: "", result: "all",
    group: "plugin", page: 1, page_size: 50, speed_mode: "total", tab: "stats", data: null,
    analysisGroup: "plugins", sort: { key: "tokens", dir: "desc" }, hiddenSeries: new Set() };
  var loadSeq = 0, exportSeq = 0, pricesDirty = false, priceSaving = false, priceVersion = 0;
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
  function dateText(seconds) { return H.known(seconds) ? new Date(seconds * 1000).toLocaleString() : "—"; }
  function palette(index) { return "var(--series-" + (index % 6 + 1) + ")"; }
  function renderKpis(o) {
    $("kpi-calls").textContent = H.fmt(o.calls);
    $("kpi-calls-sub").textContent = "成功 " + H.fmt(o.success) + " · 失败 " + H.fmt(o.failed) + " · 取消 " + H.fmt(o.canceled);
    $("kpi-tps").textContent = H.fmt(o.tps);
    $("kpi-tps-sub").textContent = "Token / 秒 · " + H.fmt(o.tps_samples) + " 个有效样本";
    $("kpi-total").textContent = H.fmt(o.tokens);
    $("kpi-success").textContent = H.percent(o.success_rate);
    $("kpi-success-sub").textContent = "成功 " + H.fmt(o.success) + " / 已完成 " + H.fmt(H.known(o.success) && H.known(o.failed) ? o.success + o.failed : null) + "（不含取消）";
    $("kpi-cache").textContent = H.percent(o.cache_hit_rate);
    $("kpi-cache-sub").textContent = "缓存 Token 占总量 " + H.percent(o.cache_share);
    $("kpi-cost").textContent = H.money(o.estimated_cost);
    $("kpi-cost-sub").textContent = "已计价 " + H.fmt(o.priced_calls) + " · 未计价 " + H.fmt(o.unpriced_calls) + " · 非账单";
  }
  function renderChart() {
    var host = $("chart"); host.textContent = "";
    var chart = H.stacks(state.data.timeline, state.group, state.hiddenSeries);
    var legend = $("legend"); legend.textContent = "";
    chart.series.forEach(function (item) {
      var b = el("button"), sw = el("span", "swatch"); b.type = "button";
      sw.style.background = palette(item.index); b.appendChild(sw); b.appendChild(document.createTextNode(item.label + " · " + H.fmt(item.total)));
      b.setAttribute("aria-pressed", String(!state.hiddenSeries.has(item.key)));
      b.setAttribute("aria-label", "切换图表系列 " + item.label);
      b.addEventListener("click", function () {
        if (state.hiddenSeries.has(item.key)) state.hiddenSeries.delete(item.key); else state.hiddenSeries.add(item.key);
        renderChart();
        var replacement = legend.children[item.index]; if (replacement) replacement.focus();
      }); legend.appendChild(b);
    });
    $("chart-detail").textContent = "";
    $("chart-peak").textContent = "粒度：" + ({ day: "天", hour: "小时", "30m": "30 分钟" }[state.data.granularity] || "—") + " · 可见峰值 " + H.fmt(chart.max) + " Token";
    if (!chart.points.length || chart.max === 0) {
      host.appendChild(el("div", "empty", chart.series.length && state.hiddenSeries.size ? "当前可见系列无用量；可用图例恢复。" : "这个范围内没有 Token 用量。")); return;
    }
    var width = Math.max(760, chart.points.length * 18 + 90), height = 300, left = 68, top = 18, bottom = 45, plotHeight = height - top - bottom, plotWidth = width - left - 15;
    var canvas = svg("svg", { viewBox: "0 0 " + width + " " + height, class: "chart-svg", role: "group", "aria-label": "Token 堆叠柱图，左右方向键查看时间桶" });
    // Each tick and segment uses the same linear scale. Tiny values stay tiny, not a minimum-height floor.
    for (var tick = 0; tick <= 4; tick++) {
      var y = top + plotHeight * tick / 4;
      canvas.appendChild(svg("line", { x1: left, x2: width - 15, y1: y, y2: y, class: "chart-grid" }));
      canvas.appendChild(svg("text", { x: left - 8, y: y + 4, "text-anchor": "end" }, H.fmt(chart.max * (4 - tick) / 4, 2)));
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
      if (index === 0 || index === chart.points.length - 1 || index === Math.floor(chart.points.length / 2)) {
        canvas.appendChild(svg("text", { x: left + slot * (index + .5), y: height - 16, "text-anchor": "middle" }, point.label));
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
      var canvas = svg("svg", { viewBox: "0 0 160 160", role: "img", "aria-label": parts.map(function (p) { return p.label + " " + H.fmt(p.value); }).join("，") });
      var offset = 0, circumference = 2 * Math.PI * 57;
      parts.forEach(function (p) {
        canvas.appendChild(svg("circle", { cx: 80, cy: 80, r: 57, fill: "none", stroke: palette(p.index), "stroke-width": 18,
          "stroke-dasharray": p.value / total * circumference + " " + circumference,
          "stroke-dashoffset": -offset / total * circumference, transform: "rotate(-90 80 80)" })); offset += p.value;
      });
      canvas.appendChild(svg("text", { x: 80, y: 77, "text-anchor": "middle" }, "Token"));
      canvas.appendChild(svg("text", { x: 80, y: 99, "text-anchor": "middle" }, H.fmt(total, 0))); host.appendChild(canvas);
    } else host.appendChild(el("p", "muted", allKnown ? "暂无 Token 用量" : "分量数据不完整"));
    parts.forEach(function (p) { metric(list, p.label, H.fmt(p.value), palette(p.index)); });
    metric(list, "思考 Token（原始参考项）", H.fmt(o.reasoning)); metric(list, "缓存写入（原始参考项）", H.fmt(o.cache_write));
    var performance = $("performance"); performance.textContent = "";
    metric(performance, "平均耗时", H.known(o.average_latency_ms) ? H.fmt(o.average_latency_ms) + " ms" : "—");
    metric(performance, "耗时样本", H.fmt(o.latency_samples)); metric(performance, "RPM", H.fmt(o.rpm));
    metric(performance, "成功 / 失败 / 取消", H.fmt(o.success) + " / " + H.fmt(o.failed) + " / " + H.fmt(o.canceled));
    var priced = H.known(o.priced_calls) && H.known(o.unpriced_calls) ? o.priced_calls + o.unpriced_calls : 0;
    metric(performance, "计价覆盖（当前范围）", priced ? H.percent(o.priced_calls / priced) : "—");
    metric(performance, "可计价 / 未计价", H.fmt(o.priced_calls) + " / " + H.fmt(o.unpriced_calls));
  }
  function renderAnalysis() {
    var rows = ((state.data || {}).analysis || {})[state.analysisGroup] || [], body = $("tbody"); body.textContent = "";
    ["tokens", "calls", "failed"].forEach(function (key) {
      if (state.sort.key === key) $("th-" + key).setAttribute("aria-sort", state.sort.dir === "asc" ? "ascending" : "descending");
      else $("th-" + key).removeAttribute("aria-sort");
    });
    $("table").hidden = !rows.length; $("table-scroll").hidden = !rows.length; $("empty").hidden = Boolean(rows.length);
    $("table-note").textContent = "共 " + rows.length + " 项";
    H.sorted(rows, state.sort.key, state.sort.dir).forEach(function (row) {
      var tr = el("tr"), name = el("td"), b = el("button", "name-button", row.label || row.key); b.type = "button";
      b.setAttribute("aria-label", "筛选 " + (row.label || row.key));
      b.addEventListener("click", function () { var field = { plugins: "plugin", models: "model", providers: "provider" }[state.analysisGroup]; state[field] = row.key; $("filter-" + field).value = row.key; changed(); });
      name.appendChild(b); tr.appendChild(name);
      ["tokens", "calls", "failed"].forEach(function (key) { tr.appendChild(el("td", "num", H.fmt(row[key]))); }); body.appendChild(tr);
    });
  }
  var columns = [
    ["timestamp", "时间", 180], ["plugin", "来源插件", 180], ["provider", "Provider", 140], ["model", "模型", 180], ["result", "结果", 90],
    ["tokens", "Token", 110], ["input_other", "输入", 110], ["input_cached", "缓存读取", 110], ["output", "输出", 110],
    ["reasoning", "推理", 100], ["cache_write", "缓存写入", 110], ["latency_ms", "耗时 ms", 110], ["ttft_ms", "TTFT ms", 110],
    ["tps", "TPS", 100], ["estimated_cost", "估价 USD", 130], ["id", "记录 ID", 180]
  ].map(function (c) { return { key: c[0], label: c[1], width: c[2], visible: ["reasoning", "cache_write", "ttft_ms", "id"].indexOf(c[0]) < 0 }; });
  function eventValue(item, key) {
    if (key === "timestamp") return dateText(item[key]);
    if (key === "estimated_cost") return H.money(item[key]);
    if (key === "result") return ({ success: "成功", failed: "失败", canceled: "取消" })[item[key]] || item[key] || "—";
    if (["id", "plugin", "provider", "model"].indexOf(key) >= 0) return item[key] === null || item[key] === undefined || item[key] === "" ? "—" : String(item[key]);
    return H.fmt(item[key]);
  }
  function renderEvents() {
    var events = (state.data || {}).events || {}, items = events.items || [], head = $("events-head"), body = $("events-body"), visible = columns.filter(function (c) { return c.visible; });
    head.textContent = ""; body.textContent = ""; var tr = el("tr");
    visible.forEach(function (c) { var th = el("th", "", c.label); th.setAttribute("scope", "col"); th.style.width = c.width + "px"; tr.appendChild(th); }); head.appendChild(tr);
    items.forEach(function (item) { var row = el("tr"); visible.forEach(function (c) { row.appendChild(el("td", "", eventValue(item, c.key))); }); body.appendChild(row); });
    if (!items.length) { var empty = el("td", "empty", "这个范围内没有详细记录。旧汇总不提供逐请求明细。"); empty.setAttribute("colspan", String(visible.length || 1)); var er = el("tr"); er.appendChild(empty); body.appendChild(er); }
    var page = events.page || state.page, pages = events.total_pages || 0;
    if (H.known(events.page) && events.page >= 1) state.page = Math.floor(events.page);
    $("page-note").textContent = "第 " + page + " / " + Math.max(1, pages) + " 页 · 共 " + H.fmt(events.total) + " 条";
    $("page-prev").disabled = page <= 1; $("page-next").disabled = !pages || page >= pages;
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
      ["Legacy daily calls excluded", "旧日汇总无法精确匹配结果或子日时间筛选，相关调用已排除，数量见上方。"],
      ["Legacy history has daily granularity", "旧历史仅有日粒度；延迟、首 Token 时间与 TPS 只覆盖有效明细样本。"],
      ["Cache-write metadata exceeds normalized input", "缓存写入参考值超过归一化输入；总量保持原口径，相关调用不估价。"],
      ["Some calls did not report usage", "部分调用未提供完整用量；Token 与估价仅汇总已上报样本。"],
      ["Some calls did not report cache usage", "部分调用未上报缓存字段；缓存比率仅使用明确上报的样本。"],
      ["Partially cache-reported legacy buckets", "旧日桶仅部分调用上报缓存，无法还原精确分母；缓存比率只使用明细或完整上报日桶。"]
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
    metric(list, "旧汇总请求", H.fmt(c.legacy_calls)); metric(list, "详细历史请求", H.fmt(c.detail_calls)); metric(list, "筛选排除的旧汇总请求", H.fmt(c.excluded_legacy_calls)); metric(list, "详细历史起点", dateText(c.detail_start));
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
    $("coverage-warnings").textContent = ""; (c.warnings || []).forEach(function (message) { $("coverage-warnings").appendChild(el("li", "", coverageMessage(String(message)))); });
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
    $("settings-detail-hint").textContent = needed ? "启用用量采集后，才会产生新的调用明细。" : "";
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
    if (record.reload_required) notes.push("服务器设置已保存，需要重载插件生效。");
    if (record.runtime_error) notes.push("运行状态：" + record.runtime_error);
    $("settings-runtime").textContent = notes.join(" ");
    $("settings-runtime").classList.toggle("is-error", !record.writable || !!record.runtime_error);
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
    settingsMessage("有未保存的采集设置修改。"); settingsControls();
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
      var message = !result.applied || result.reload_required || record.reload_required ? "已保存，需要重载插件生效。"
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
  function priceKey(value) { return JSON.stringify([value.provider || "", value.model]); }
  function copyManual(value) {
    var result = { model: value.model, provider: value.provider || "" };
    ["input", "cache_read", "cache_write", "output"].forEach(function (field) { result[field] = H.known(value[field]) ? value[field] : null; });
    return result;
  }
  function closePriceEditor() {
    priceDraft = null; pricesDirty = false; $("price-editor").hidden = true; $("price-edit-status").textContent = "";
  }
  function editPrice(row) {
    if (priceSaving) { $("price-status").textContent = "价格正在保存，请稍后编辑其他条目。"; return; }
    if (priceDraft && pricesDirty) { $("price-status").textContent = "请先保存或取消当前价格编辑。"; return; }
    var manual = row ? H.manualPrice(manualPrices, row.model, row.provider) : null;
    var initial = manual || (row ? Object.assign({ model: row.model, provider: row.provider || "" }, row.price || {}) : {});
    priceDraft = { originalKey: manual ? priceKey(manual) : null };
    priceVersion++; pricesDirty = false;
    priceFields.forEach(function (field) { $("price-edit-" + field).value = initial[field] === null || initial[field] === undefined ? "" : String(initial[field]); });
    $("price-editor-title").textContent = manual ? "编辑手工价格覆盖" : row ? "为模型设置手工覆盖" : "手动添加模型价格";
    $("price-remove").disabled = !manual; $("price-editor").hidden = false;
    $("price-status").textContent = ""; $("price-edit-status").textContent = ""; $("price-edit-model").focus();
  }
  function renderPriceRows() {
    var view = priceView || { rows: [] }, page = H.pricePage(view.rows, priceSearch, pricePage, 50);
    pricePage = page.page; var body = $("prices-body"); body.textContent = "";
    page.rows.forEach(function (row) {
      var tr = el("tr"), model = el("td"), name = el("span", "price-model-name", row.model);
      name.setAttribute("title", row.model); model.appendChild(name);
      var source = row.price_source === "manual" ? "手工覆盖" : row.price_source === "models.dev" ? "Models.dev" : "未匹配价格";
      var subtitle = (row.provider || "通用") + " · " + source + (row.source_provider ? " / " + row.source_provider : "");
      var note = el("small", "price-model-note", subtitle); note.setAttribute("title", subtitle); model.appendChild(note); tr.appendChild(model);
      tr.appendChild(el("td", "num", H.fmt(row.calls, 0))); tr.appendChild(el("td", "num", H.fmt(row.tokens, 0)));
      var cost = el("td", "num price-cost", H.money(row.estimated_cost));
      cost.setAttribute("title", "已计价 " + H.fmt(row.priced_calls, 0) + " / " + H.fmt(row.calls, 0) + " 次调用；缺价部分不计入估算");
      tr.appendChild(cost);
      ["input", "output", "cache_read", "cache_write"].forEach(function (field) { tr.appendChild(el("td", "num", H.quote(row.price && row.price[field]))); });
      var action = el("td"), edit = el("button", "price-edit-button", "✎"); edit.type = "button"; edit.setAttribute("aria-label", "编辑价格 " + row.model); edit.setAttribute("title", "编辑手工覆盖");
      edit.addEventListener("click", function () { editPrice(row); }); action.appendChild(edit); tr.appendChild(action); body.appendChild(tr);
    });
    if (!page.rows.length) { var tr = el("tr"), cell = el("td", "empty", priceSearch ? "没有匹配的模型。" : "尚无模型记录或价格目录。可点击更新价格，或手动添加。"); cell.setAttribute("colspan", "9"); tr.appendChild(cell); body.appendChild(tr); }
    $("price-page-note").textContent = page.start + "-" + page.end + " / " + page.total + " 项";
    $("price-prev").disabled = page.page <= 1; $("price-next").disabled = page.page >= page.pages;
    $("price-pager").hidden = page.total <= 50;
  }
  function renderPrices(data) {
    if (Array.isArray(data.prices)) manualPrices = data.prices.map(copyManual);
    priceView = H.pricing(data);
    $("price-total").textContent = H.money(priceView.total_cost);
    var total = H.known(priceView.priced_calls) && H.known(priceView.unpriced_calls) ? priceView.priced_calls + priceView.unpriced_calls : null;
    $("price-summary").textContent = "已计价 " + H.fmt(priceView.priced_calls, 0) + " / " + H.fmt(total, 0) + " 次调用 · 可用 " + H.fmt(priceView.available_prices, 0) + " 项模型价格";
    $("price-source-state").textContent = H.known(priceView.updated_at) ? "Models.dev · 最近同步 " + dateText(priceView.updated_at) + " · USD / 百万 Token，参考估价并非账单" : "Models.dev 尚未同步，点击更新价格；已有模型用量仍会列出，缺价显示 —。";
    if (data.pricing && data.pricing.sync_error) $("price-source-state").textContent += " · " + data.pricing.sync_error;
    $("price-compat").hidden = priceView.supported;
    $("price-compat").textContent = priceView.supported ? "" : "后端未提供完整模型价格统计，当前展示已有模型与手工价格；逐模型费用未知，请更新后端。";
    renderPriceRows();
  }
  function renderOptions(options) {
    ["model", "plugin", "provider"].forEach(function (key) {
      var select = $("filter-" + key), values = options[key + "s"] || [], label = { model: "全部模型", plugin: "全部插件", provider: "全部 Provider" }[key];
      select.textContent = ""; var all = el("option", "", label); all.value = ""; select.appendChild(all);
      // Only backend time-window candidates become options; a selected missing value is explicitly retained.
      values.forEach(function (value) { var option = el("option", "", value); option.value = value; select.appendChild(option); });
      if (state[key] && values.indexOf(state[key]) < 0) { var absent = el("option", "", state[key] + "（当前时间范围无记录）"); absent.value = state[key]; select.appendChild(absent); }
      select.value = state[key];
    });
  }
  function render(data) {
    state.data = data;
    renderKpis(data.overview || {}); renderChart(); renderDistribution(data.overview || {}); renderAnalysis(); renderEvents(); renderCoverage(data); renderSettings(data.settings); renderPrices(data); renderOptions(data.options || {});
    $("summary").textContent = (state.custom ? $("start").value + " 至 " + $("end").value + "（结束不含）" : state.days ? "近 " + state.days + " 天" : "全部时间") + " · " + [state.plugin, state.model, state.provider, state.result === "all" ? "" : state.result].filter(Boolean).join(" / ");
  }
  function fetchStats(params, retries, seq) {
    return api.apiGet("stats", params).catch(function (error) {
      if (seq !== loadSeq || retries <= 0) throw error;
      return new Promise(function (resolve) { setTimeout(resolve, 500); }).then(function () {
        if (seq !== loadSeq) throw error; return fetchStats(params, retries - 1, seq);
      });
    });
  }
  function load() {
    var seq = ++loadSeq, params;
    try { params = H.query(state); } catch (error) { setStatus(error.message, "error"); $("filter-note").textContent = error.message; $("filter-note").className = "filter-error"; $("dashboard").setAttribute("aria-busy", "false"); return Promise.resolve(); }
    setStatus("加载中…", "loading"); $("dashboard").setAttribute("aria-busy", "true");
    if (!api) { setStatus("桥接未加载，请从 AstrBot 插件页面打开。", "error"); $("dashboard").setAttribute("aria-busy", "false"); return Promise.resolve(); }
    return api.ready().then(function (ctx) {
      if (ctx && typeof ctx.isDark === "boolean") { hostDark = ctx.isDark; applyTheme(); }
      return fetchStats(params, 2, seq);
    }).then(function (data) {
      if (seq !== loadSeq) return;
      if (!data || !data.overview) throw new Error("统计响应缺少 dashboard overview，请更新后端。");
      render(data);
      var collection = data.collection || {}, paused = collection.plugin_enabled === false || collection.patched_count === 0;
      setStatus(collection.plugin_enabled === false ? "采集未启用" : collection.patched_count === 0 ? "等待采集就绪" : collection.plugin_enabled === true ? "采集中" : "已更新", paused ? "paused" : "");
      $("status").setAttribute("title", "数据读取时间 · " + new Date().toLocaleTimeString());
    }).catch(function (error) {
      if (seq !== loadSeq) return;
      setStatus("读取失败：" + (error.message || error), "error");
      if (!state.data) { $("chart").textContent = "取不到数据，请点击刷新重试。"; $("tbody").textContent = ""; $("table").hidden = true; $("table-scroll").hidden = true; }
    }).then(function () { if (seq === loadSeq) $("dashboard").setAttribute("aria-busy", "false"); });
  }
  function changed() {
    state.page = 1; pricePage = 1; state.hiddenSeries.clear(); ++exportSeq; $("export-events").disabled = false;
    $("filter-note").className = "sr-only"; $("export-status").textContent = ""; $("filter-note").textContent = "筛选应用到所有统计视图；调用日志仍按最近条数读取。";
    return load();
  }
  $("range").addEventListener("change", function () {
    state.custom = $("range").value === "custom"; $("custom-range").hidden = !state.custom;
    if (!state.custom) { state.days = Number($("range").value); storageSet(RANGE_KEY, state.days); changed(); }
  });
  $("apply-range").addEventListener("click", function () { state.start = $("start").value; state.end = $("end").value; changed(); });
  ["model", "plugin", "provider", "result"].forEach(function (key) { $("filter-" + key).addEventListener("change", function () { state[key] = $("filter-" + key).value; changed(); }); });
  $("clear-filters").addEventListener("click", function () { ["model", "plugin", "provider", "result"].forEach(function (key) { state[key] = key === "result" ? "all" : ""; $("filter-" + key).value = state[key]; }); changed(); });
  $("group").addEventListener("click", function (ev) { var b = ev.target.closest("button[data-group]"); if (!b) return; state.group = b.dataset.group; storageSet(GROUP_KEY, state.group); syncSeg("group", "group", state.group); changed(); });
  $("analysis-group").addEventListener("change", function () { state.analysisGroup = $("analysis-group").value; renderAnalysis(); });
  ["tokens", "calls", "failed"].forEach(function (key) { $("sort-" + key).addEventListener("click", function () { state.sort.dir = state.sort.key === key && state.sort.dir === "desc" ? "asc" : "desc"; state.sort.key = key; renderAnalysis(); }); });
  $("page-prev").addEventListener("click", function () { if (state.page > 1) { state.page--; load(); } });
  $("page-next").addEventListener("click", function () { var events = (state.data || {}).events || {}; if (state.page < events.total_pages) { state.page++; load(); } });
  $("page-size").addEventListener("change", function () { state.page_size = Math.min(200, Number($("page-size").value) || 50); changed(); });
  function download(content, type, name) {
    var url = URL.createObjectURL(new Blob([content], { type: type })), a = el("a"); a.href = url; a.download = name;
    a.click(); setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }
  $("export-events").addEventListener("click", function () {
    var query, seq = ++exportSeq;
    try { query = H.query(state, "export"); } catch (error) { $("export-status").textContent = error.message; return; }
    if (!api) return;
    $("export-events").disabled = true; $("export-status").textContent = "正在导出详细历史，最多 5000 行，不含旧汇总…";
    api.apiGet("stats", query).then(function (result) {
      if (seq !== exportSeq) return;
      if (!result || typeof result.csv !== "string") throw new Error("导出响应无效");
      download(result.csv, "text/csv;charset=utf-8", "usage-details-" + new Date().toISOString().slice(0, 10) + ".csv");
      $("export-status").textContent = "已导出 " + H.fmt(result.returned) + " / " + H.fmt(result.total) + " 条详细记录" + (result.truncated ? "（已截断）" : "") + "。最多 5000 行，不含旧汇总。";
    }).catch(function (error) { if (seq === exportSeq) $("export-status").textContent = "导出失败：" + (error.message || error); })
      .then(function () { if (seq === exportSeq) $("export-events").disabled = false; });
  });
  $("price-add").addEventListener("click", function () { editPrice(null); });
  $("price-cancel").addEventListener("click", function () { if (!priceSaving) { closePriceEditor(); $("price-status").textContent = ""; } });
  priceFields.forEach(function (field) { $("price-edit-" + field).addEventListener("input", function () { priceVersion++; pricesDirty = true; $("price-edit-status").textContent = "有未保存的价格修改。"; }); });
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
      $("price-status").textContent = remove ? "手工覆盖已删除，将使用公共目录的匹配价格。" : "手工价格已保存。";
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
      .then(function () { priceSyncing = false; $("price-sync").disabled = false; $("price-sync").textContent = "更新价格"; });
  });
  var resetBtn = $("reset-stats"), resetArmed = false, resetTimer = 0, resetting = false;
  function disarmReset() { resetArmed = false; clearTimeout(resetTimer); resetBtn.textContent = "重置"; resetBtn.classList.remove("armed"); $("reset-cancel").hidden = true; }
  $("reset-cancel").addEventListener("click", function () { disarmReset(); $("reset-status").textContent = "已取消重置。"; resetBtn.focus(); });
  resetBtn.addEventListener("keydown", function (ev) { if (ev.key === "Escape") { disarmReset(); $("reset-status").textContent = "已取消重置。"; } });
  resetBtn.addEventListener("click", function () {
    if (resetting) return;
    if (!resetArmed) {
      resetArmed = true; resetBtn.textContent = "再点一次确认"; resetBtn.classList.add("armed"); $("reset-cancel").hidden = false;
      $("reset-status").textContent = "警告：将永久清空全部统计。5 秒内再次点击确认，或取消。";
      resetTimer = setTimeout(function () { disarmReset(); $("reset-status").textContent = "确认已超时，未重置数据。"; }, 5000); return;
    }
    disarmReset();
    if (!api || !api.apiPost) { $("reset-status").textContent = "桥接不支持写入，无法重置。"; return; }
    resetting = true; resetBtn.disabled = true; ++loadSeq; ++exportSeq; ++logGeneration;
    $("export-events").disabled = false; $("export-status").textContent = "";
    logLines = []; logReady = false; $("log-body").textContent = ""; $("log-export").disabled = true;
    $("log-note").textContent = ""; $("log-status").textContent = "日志视图已清空；可手动重新读取调用日志。";
    $("reset-status").textContent = "重置中…";
    api.apiPost("reset", {}).then(function (result) {
      if (!result || result.saved !== true) throw new Error(result && result.note || "部分数据未能重置，请检查存储后重试。");
      $("reset-status").textContent = "已重置。历史插件 " + H.fmt(result && result.baseline_plugins) + "；内存调用 " + H.fmt(result && result.memory_calls) + "。";
      return load();
    }).catch(function (error) { $("reset-status").textContent = "重置失败：" + (error.message || error); })
      .then(function () { resetting = false; resetBtn.disabled = false; $("log-refresh").disabled = false; });
  });
  var logLines = [];
  var logTail = 200;
  var logGeneration = 0;
  var logReady = false;

  function inOf(line) {
    var m = /(?:^| )in=(\d+)/.exec(line);
    return m ? Number(m[1]) : -1;
  }

  function renderLog(payload, generation) {
    if (generation !== logGeneration) return;
    var body = $("log-body");
    var note = $("log-note");
    note.textContent = "";
    var status = $("log-status");
    var exportButton = $("log-export");
    var state = payload && payload.state;
    logLines = [];
    logReady = false;
    body.textContent = "";
    exportButton.disabled = true;
    if (!payload || !Array.isArray(payload.lines) || !state) {
      note.textContent = "";
      status.textContent = "调用日志响应无效，请重试。";
      return;
    }
    if (state === "disabled") status.textContent = "调用日志未启用。可在插件配置中启用；此功能默认关闭。";
    else if (state === "empty") status.textContent = "暂无已落盘的调用记录，可手动刷新。";
    else if (state === "unavailable") status.textContent = payload.error_code === "path_unavailable" ? "调用日志数据目录不可用，请检查插件数据目录。" : "调用日志暂时无法读取，请稍后重试。";
    else if (state === "invalid_request") status.textContent = "读取条数无效，请重新选择后重试。";
    else if (state === "partial") status.textContent = "已显示已落盘记录；最新一批可能尚未写入。";
    else if (state === "truncated") status.textContent = "仅显示最近 " + (payload.returned || 0) + " 条；结果受读取上限限制。";
    else if (state === "ready") status.textContent = "显示 " + (payload.returned || 0) + " 条调用记录。";
    else { status.textContent = "调用日志状态未知，请重试。"; return; }
    if (["ready", "truncated", "partial"].indexOf(state) < 0) return;
    logLines = payload.lines.slice();
    logLines.sort(function (a, b) { return inOf(b) - inOf(a); });
    body.textContent = logLines.join("\n");
    logReady = logLines.length > 0;
    exportButton.disabled = !logReady;
    note.textContent = logReady ? "按输入 token 降序" : "";
  }

  function loadLog() {
    var generation = ++logGeneration;
    if (!api || !api.apiGet) {
      logLines = []; logReady = false; $("log-body").textContent = "";
      $("log-export").disabled = true; $("log-status").textContent = "日志接口不可用，请刷新页面。";
      return;
    }
    $("log-status").textContent = "正在读取调用日志。";
    $("log-refresh").disabled = true;
    api.apiGet("stats", { tail: logTail }).then(function (payload) {
      renderLog(payload, generation);
    }).catch(function () {
      if (generation !== logGeneration) return;
      logLines = []; logReady = false; $("log-body").textContent = "";
      $("log-note").textContent = ""; $("log-export").disabled = true;
      $("log-status").textContent = "调用日志读取失败，请重试。";
    }).then(function () {
      if (generation === logGeneration) $("log-refresh").disabled = false;
    });
  }

  function exportLog() {
    if (!logReady || !logLines.length) return;
    var stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
    var blob = new Blob([logLines.join("\n") + "\n"], { type: "text/plain;charset=utf-8" });
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = "usage-calls-" + stamp + ".log";
    a.click();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    $("log-status").textContent = "已导出 " + logLines.length + " 条调用记录。";
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


  var tabNames = ["stats", "analysis", "events", "prices", "log", "manage"];
  function showTab(name) {
    state.tab = tabNames.indexOf(name) >= 0 ? name : "stats";
    tabNames.forEach(function (tab) { $("tab-" + tab).hidden = state.tab !== tab; });
    Array.prototype.forEach.call($("tabs").children, function (b) { var selected = b.dataset.tab === state.tab; b.setAttribute("aria-selected", String(selected)); b.tabIndex = selected ? 0 : -1; b.classList.toggle("on", selected); });
    storageSet(TAB_KEY, state.tab);
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
  $("refresh").addEventListener("click", load);
  $("range").value = String(state.days); syncSeg("group", "group", state.group); showTab(storageGet(TAB_KEY));
  load();
  setInterval(function () { if (!document.hidden && state.tab !== "log" && !resetting && !priceSaving) load(); }, 30000);
})();
