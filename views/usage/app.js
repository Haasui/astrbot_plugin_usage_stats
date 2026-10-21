/* 用量统计 · 视图逻辑
 *
 * 数据来自 /astrbot_plugin_usage_stats/stats?days=N，和 /usage 指令同源。
 * 桥接用官方 window.AstrBotPluginPage，SDK 由 /api/plugin/page/bridge-sdk.js 提供。
 * 本文件不碰样式，全部视觉规则在 style.css。
 */
(function () {
  "use strict";

  var api = window.AstrBotPluginPage || window.AstrBotPluginView;
  // focus 是插件名，focusModel 是模型名。两个都空就是看全部。
  // collapsed 记哪些插件被收起了。不放进 DOM 的话，每次重建行都会弹回来 ——
  // 而每 30 秒就有一次自动刷新
  var state = {
    days: 30,
    focus: "",
    focusModel: "",
    collapsed: {},
    group: "plugin",
    data: null,
    sort: { key: "total", dir: "desc" },
  };

  function $(id) {
    return document.getElementById(id);
  }

  /* ---------- 主题 ----------
     明暗的优先级：宿主经 bridge 告知的 isDark > 宿主自己设的 data-theme > 系统偏好。
     做法参考 astrbot_plugin_livingmemory 的 appearance.js。 */
  var media = window.matchMedia("(prefers-color-scheme: dark)");
  var hostDark = null;   // 来自 bridge.ready() 的上下文
  var weSetTheme = false;

  function applyTheme() {
    if (typeof hostDark === "boolean") {
      weSetTheme = true;
      document.documentElement.setAttribute("data-theme", hostDark ? "dark" : "light");
      return;
    }
    // 进页面时宿主已经设过了，那是它的决定，别抢
    if (!weSetTheme && document.documentElement.hasAttribute("data-theme")) return;
    weSetTheme = true;
    document.documentElement.setAttribute("data-theme", media.matches ? "dark" : "light");
  }
  applyTheme();
  if (media.addEventListener) media.addEventListener("change", applyTheme);

  /* ---------- 时间范围：记在本机，下次进来还是这个范围 ---------- */
  var RANGE_KEY = "astrbot_usage_stats_days";
  var GROUP_KEY = "astrbot_usage_stats_group";

  function saveRange() {
    try { window.localStorage.setItem(RANGE_KEY, String(state.days)); } catch (e) { /* 受限 iframe 会拒绝 */ }
  }

  function syncRange() {
    Array.prototype.forEach.call($("range").children, function (b) {
      b.classList.toggle("on", Number(b.dataset.days) === state.days);
    });
  }

  try {
    var savedDays = window.localStorage.getItem(RANGE_KEY);
  try {
    var savedGroup = window.localStorage.getItem(GROUP_KEY);
    if (savedGroup === "plugin" || savedGroup === "model") state.group = savedGroup;
  } catch (e) { /* 受限 iframe */ }
    if (savedDays !== null && !isNaN(Number(savedDays))) state.days = Number(savedDays);
  } catch (e) { /* 同上 */ }

  function fmt(n) {
    return (n || 0).toLocaleString("en-US");
  }

  function pct(part, whole) {
    if (!whole) return "—";
    return ((part / whole) * 100).toFixed(1) + "%";
  }

  function setStatus(text, kind) {
    $("status-text").textContent = text;
    $("status").className = "status" + (kind ? " is-" + kind : "");
  }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function icon(path) {
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "2");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("class", "caret");
    var p = document.createElementNS("http://www.w3.org/2000/svg", "path");
    p.setAttribute("stroke-linecap", "round");
    p.setAttribute("stroke-linejoin", "round");
    p.setAttribute("d", path);
    svg.appendChild(p);
    return svg;
  }

  function meterCell(value, whole) {
    var td = el("td", "num");
    var meter = el("span", "meter");
    var fill = el("span", "meter-fill");
    fill.style.width = (whole ? Math.round((value / whole) * 100) : 0) + "%";
    meter.appendChild(fill);
    td.appendChild(meter);
    td.appendChild(document.createTextNode(pct(value, whole)));
    return td;
  }

  function showTip(anchor, text) {
    var tip = $("tip");
    tip.textContent = text;
    var box = anchor.getBoundingClientRect();
    tip.style.left = Math.round(box.left + box.width / 2) + "px";
    tip.style.top = Math.round(box.top - 6) + "px";
    tip.classList.add("on");
  }

  function hideTip() {
    $("tip").classList.remove("on");
  }

  var PALETTE = [
    "--series-1", "--series-2", "--series-3",
    "--series-4", "--series-5", "--series-6"
  ];
  var REST = "var(--series-rest)";

  /* 按范围内的总量排名定色：前 6 名各一色，其余统一灰。
     图表和图例共用这一份映射，否则会出现「图上是灰块、图例说它是绿色」。 */
  function rankColors(seriesByPlugin) {
    var totals = {};
    Object.keys(seriesByPlugin || {}).forEach(function (name) {
      totals[name] = (seriesByPlugin[name] || []).reduce(function (sum, p) {
        return sum + (p.total || 0);
      }, 0);
    });
    var ranked = Object.keys(totals).sort(function (a, b) { return totals[b] - totals[a]; });
    var colors = {};
    ranked.forEach(function (name, i) {
      colors[name] = i < PALETTE.length ? "var(" + PALETTE[i] + ")" : REST;
    });
    return { ranked: ranked, totals: totals, colors: colors };
  }

  /* series_by_plugin 摊成「每天 → 当天各插件的用量」，按用量降序。 */
  function stackByDay(seriesByPlugin) {
    var byDay = {};
    Object.keys(seriesByPlugin || {}).forEach(function (name) {
      (seriesByPlugin[name] || []).forEach(function (point) {
        if (!byDay[point.day]) byDay[point.day] = [];
        byDay[point.day].push({ name: name, total: point.total || 0 });
      });
    });
    Object.keys(byDay).forEach(function (day) {
      byDay[day].sort(function (a, b) { return b.total - a.total; });
    });
    return byDay;
  }

  function renderLegend(rank) {
    var box = $("legend");
    box.textContent = "";
    if (!rank) { box.hidden = true; return; }
    var shown = rank.ranked.slice(0, PALETTE.length);
    if (!shown.length) { box.hidden = true; return; }
    box.hidden = false;
    function item(name, value, color, label) {
      var wrap = el("span", "legend-item");
      var sw = el("span", "swatch");
      sw.style.background = color;
      wrap.appendChild(sw);
      wrap.appendChild(document.createTextNode(label || name));
      wrap.appendChild(el("span", "legend-value", value));
      box.appendChild(wrap);
    }
    shown.forEach(function (name) {
      item(name, fmt(rank.totals[name]), rank.colors[name]);
    });
    var rest = rank.ranked.slice(PALETTE.length);
    if (rest.length) {
      var restTotal = rest.reduce(function (sum, n) { return sum + rank.totals[n]; }, 0);
      item(null, fmt(restTotal), REST, "其余 " + rest.length + " 个");
    }
  }

  /* 副标题两段：先说事实，再挂一句环比。环比没有就不占位置。 */
  function setSub(node, text, extra) {
    node.textContent = "";
    if (text) node.appendChild(document.createTextNode(text));
    if (extra) {
      if (text) node.appendChild(document.createTextNode("　"));
      node.appendChild(el("span", "delta", extra));
    }
  }

  function ratio(inputOther, inputCached) {
    var incoming = (inputOther || 0) + (inputCached || 0);
    return incoming ? inputCached / incoming : 0;
  }

  /* 比率型指标的环比要比「率之差」（百分点），不是比命中量之差。 */
  function pointDelta(curRate, prevRate) {
    if (prevRate === null || prevRate === undefined) return "";
    var diff = curRate * 100 - prevRate * 100;
    if (Math.abs(diff) < 0.5) return "与上一周期持平";
    return (diff > 0 ? "↑ " : "↓ ") + Math.abs(diff).toFixed(0) + "pt 较上一周期";
  }

  /* 上一周期为 0 时不给百分比 —— 除零之外，「从 0 涨到 100」说成 +∞% 也没意义。 */
  function delta(cur, prev, unit) {
    if (prev === null || prev === undefined) return "";
    if (!prev) return cur ? "新增" : "";
    var pct = ((cur - prev) / prev) * 100;
    if (Math.abs(pct) < 0.5) return "与上一周期持平";
    return (pct > 0 ? "↑ " : "↓ ") + Math.abs(pct).toFixed(0) + (unit || "%") + " 较上一周期";
  }

  function renderChart(series, label, stack, rank) {
    var host = $("chart");
    host.textContent = "";
    $("chart-focus").textContent = label || "";

    if (!series || !series.length) {
      $("chart-peak").textContent = "";
      $("legend").hidden = true;
      host.appendChild(el("div", "empty", "这个范围内没有记录。"));
      return;
    }

    var max = series.reduce(function (m, s) { return Math.max(m, s.total); }, 0) || 1;
    $("chart-peak").textContent = "峰值 " + fmt(max) + " token / 天";

    var bars = el("div", "bars");
    bars.setAttribute("role", "group");
    bars.setAttribute("aria-label", "每日用量，用左右方向键逐日查看");
    var cols = [];
    series.forEach(function (item, index) {
      var col = el("button", "col");
      col.type = "button";
      // 整张图只留一个 Tab 停留点：30 天就是 30 个停留点的话，
      // 键盘用户想走到下面的表格得先按 30 次 Tab
      col.tabIndex = index === 0 ? 0 : -1;
      col.setAttribute("aria-label", item.day + " " + fmt(item.total) + " token");
      var lines = [item.day + " · " + fmt(item.total) + " token · " + item.calls + " 次"];
      var pieces = stack ? stack[item.day] : null;

      if (pieces && pieces.length) {
        var layers = el("span", "stack");
        layers.style.height = Math.max(2, Math.round((item.total / max) * 100)) + "%";
        pieces.forEach(function (piece) {
          var seg = el("span", "seg-piece");
          seg.style.height = (item.total ? (piece.total / item.total) * 100 : 0) + "%";
          seg.style.background = (rank && rank.colors[piece.name]) || REST;
          layers.appendChild(seg);
        });
        col.appendChild(layers);
        pieces.slice(0, 3).forEach(function (piece) {
          lines.push(piece.name + "　" + fmt(piece.total));
        });
        if (pieces.length > 3) lines.push("其余 " + (pieces.length - 3) + " 个");
      } else {
        var fill = el("span", "fill");
        fill.style.height = Math.max(2, Math.round((item.total / max) * 100)) + "%";
        col.appendChild(fill);
      }

      var text = lines.join("\n");
      var show = function () { showTip(col, text); };
      col.addEventListener("mouseenter", show);
      col.addEventListener("focus", show);
      col.addEventListener("mouseleave", hideTip);
      col.addEventListener("blur", hideTip);
      cols.push(col);
      bars.appendChild(col);
    });

    /* 整张图只留一个 Tab 停留点。30 根柱子各占一个的话，
       键盘用户想走到下面的表格得先按 30 次 Tab。 */
    function focusBar(index) {
      var target = Math.max(0, Math.min(cols.length - 1, index));
      cols.forEach(function (c, i) { c.tabIndex = i === target ? 0 : -1; });
      if (typeof cols[target].focus === "function") cols[target].focus();
    }

    bars.addEventListener("keydown", function (ev) {
      var from = cols.indexOf(ev.target);
      if (from < 0) return;
      var to = null;
      if (ev.key === "ArrowRight" || ev.key === "ArrowDown") to = from + 1;
      else if (ev.key === "ArrowLeft" || ev.key === "ArrowUp") to = from - 1;
      else if (ev.key === "Home") to = 0;
      else if (ev.key === "End") to = cols.length - 1;
      if (to === null) return;
      ev.preventDefault();
      focusBar(to);
    });

    host.appendChild(bars);

    var axis = el("div", "axis");
    var marks = {};
    marks[0] = true;
    marks[Math.floor((series.length - 1) / 2)] = true;
    marks[series.length - 1] = true;
    series.forEach(function (item, i) {
      axis.appendChild(el("span", null, marks[i] ? item.day.slice(5) : ""));
    });
    host.appendChild(axis);
    renderLegend(rank);
  }

  var SORT_KEYS = ["total", "calls", "failed", "images", "cache"];
  var last = { plugins: [], grand: 0 };   // 排序重绘时不必重新取数

  /* 命中率不能把比率相加，但排序要个可比数值，所以用原始计数算。 */
  function cacheRatio(row) {
    var incoming = (row.input_other || 0) + (row.input_cached || 0);
    return incoming ? row.input_cached / incoming : -1;
  }

  function sortValue(row, key) {
    return key === "cache" ? cacheRatio(row) : (row[key] || 0);
  }

  /* 同值时按 token 降序兜底，免得顺序在两次刷新之间乱跳。 */
  function sortRows(rows, sort) {
    var dir = sort.dir === "asc" ? 1 : -1;
    return rows.slice().sort(function (a, b) {
      var diff = sortValue(a, sort.key) - sortValue(b, sort.key);
      if (diff === 0) return (b.total || 0) - (a.total || 0);
      return diff * dir;
    });
  }

  function applySortIndicator() {
    SORT_KEYS.forEach(function (key) {
      var btn = $("sort-" + key);
      var th = $("th-" + key);
      var active = state.sort.key === key;
      if (active) btn.dataset.dir = state.sort.dir;
      else delete btn.dataset.dir;
      if (active) {
        th.setAttribute("aria-sort", state.sort.dir === "asc" ? "ascending" : "descending");
      } else {
        th.removeAttribute("aria-sort");
      }
    });
  }

  /* ---------- 分组 ----------
     表格有两种口径：按插件（子行是模型）/ 按模型（子行是插件）。
     都转成「父行 → 子行」再交给同一个 renderTable，免得写两套渲染。
     子行一律带 plugin/model 两个键 —— 「趋势」下钻要的是这一对。

     数值列走 carry()，列名集中在 NUM_KEYS：加一列只改这一处，不必在四个
     搬运点各补一行。以前是手抄的，output 就这么掉的 —— 接口给了、映射没抄，
     表格一路是 0；而 KPI 读的是接口原样的对象，看着又正常，所以两边对不上
     也很难第一眼看出来。 */
  var NUM_KEYS = ["total", "input_other", "input_cached", "output",
                  "calls", "failed", "images", "cache_reported"];

  function carry(src, extra) {
    var row = { cache_hit: src.cache_hit === undefined ? "—" : src.cache_hit };
    NUM_KEYS.forEach(function (key) { row[key] = src[key] || 0; });
    Object.keys(extra).forEach(function (key) { row[key] = extra[key]; });
    return row;
  }

  function buildGroups(plugins, mode) {
    if (mode !== "model") {
      return plugins.map(function (p) {
        var group = carry(p, { key: p.name, name: p.name, drillable: true });
        group.models = (p.models || []).map(function (m) {
          return carry(m, {
            key: p.name + "|" + m.name,
            name: m.name,
            plugin: p.name,
            model: m.name,
          });
        });
        return group;
      });
    }

    var byModel = {};
    var order = [];
    plugins.forEach(function (p) {
      (p.models || []).forEach(function (m) {
        var g = byModel[m.name];
        if (!g) {
          g = byModel[m.name] = carry({}, {
            key: m.name,
            name: m.name,
            drillable: false,
            models: [],
          });
          order.push(g);
        }
        NUM_KEYS.forEach(function (key) { g[key] += m[key] || 0; });
        g.models.push(carry(m, {
          key: p.name + "|" + m.name,
          name: p.name,
          plugin: p.name,
          model: m.name,
        }));
      });
    });
    // 父行的命中率要用原始计数重算：接口给的是每个插件各自的字符串，加起来没意义
    order.forEach(function (g) {
      var incoming = g.input_other + g.input_cached;
      // 供应商没上报缓存字段时给「—」：0% 会被读成「命中了 0 次」。
      // 有命中数就说明字段在，别拿 cache_reported 把它盖掉 —— 那个标记只看得到
      // 非流式响应的原始 usage，流式的调用它会一路记成「没上报」。
      g.cache_hit = (g.cache_reported <= 0 && g.input_cached <= 0 && g.input_other > 0)
        ? "—"
        : (incoming ? Math.round(g.input_cached / incoming * 100) + "%" : "—");
    });
    return order;
  }

  function renderTable(groups, grand, mode) {
    var body = $("tbody");
    body.textContent = "";
    $("table-note").textContent = groups.length
      ? "共 " + groups.length + " 个" + (mode === "model" ? "模型" : "插件") : "";

    // 必须在提前 return 之前记下来：否则切到空范围后点表头，
    // 排序会用上一个范围的数据把这个表重新画出来，数字还是旧的
    last = { groups: groups, grand: grand, mode: mode };
    applySortIndicator();

    if (!groups.length) {
      $("table").hidden = true;
      $("empty").hidden = false;
      return;
    }
    $("empty").hidden = true;
    $("table").hidden = false;
    groups = sortRows(groups, state.sort);

    groups.forEach(function (plugin) {
      var modelRows = [];
      var focused = plugin.drillable && state.focus === plugin.name && !state.focusModel;
      var tr = el("tr", "row-plugin" + (focused ? " is-focused" : ""));

      var nameCell = el("td");
      var toggle = el("button", "toggle");
      toggle.type = "button";
      var open = !state.collapsed[plugin.key];
      toggle.setAttribute("aria-expanded", String(open));
      toggle.appendChild(icon("M9 6l6 6-6 6"));
      toggle.appendChild(document.createTextNode(plugin.name));
      nameCell.appendChild(toggle);

      // 按模型分组时父行是模型：接口的「趋势」只认 插件|模型 这一对，
      // 单给一个模型没法下钻，所以这一档不给按钮，别让人点了没反应
      if (plugin.drillable && plugin.total > 0) {
        var focus = el("button", "focus-btn" + (focused ? " on" : ""), focused ? "取消" : "趋势");
        focus.type = "button";
        focus.addEventListener("click", function () { toggleFocus(plugin.name, ""); });
        nameCell.appendChild(focus);
      }

      tr.appendChild(nameCell);
      tr.appendChild(el("td", "num", fmt(plugin.total)));
      tr.appendChild(meterCell(plugin.total, grand));
      tr.appendChild(el("td", "num", plugin.cache_hit));
      tr.appendChild(el("td", "num", fmt(plugin.input_other)));
      tr.appendChild(el("td", "num", fmt(plugin.input_cached)));
      tr.appendChild(el("td", "num", fmt(plugin.output || 0)));
      tr.appendChild(el("td", "num", fmt(plugin.calls)));
      tr.appendChild(el("td", "num", fmt(plugin.failed)));
      tr.appendChild(el("td", "num", fmt(plugin.images)));
      body.appendChild(tr);

      sortRows(plugin.models || [], state.sort).forEach(function (model) {
        var mr = el("tr", "row-model");
        mr.hidden = !open;
        var cell = el("td");
        cell.appendChild(el("span", "model-name", model.name));
        var modelFocused = state.focus === model.plugin && state.focusModel === model.model;
        if (model.total > 0) {
          var mfocus = el("button", "focus-btn" + (modelFocused ? " on" : ""), modelFocused ? "取消" : "趋势");
          mfocus.type = "button";
          mfocus.addEventListener("click", function () { toggleFocus(model.plugin, model.model); });
          cell.appendChild(mfocus);
        }
        mr.appendChild(cell);
        mr.appendChild(el("td", "num", fmt(model.total)));
        // 子行占比的分母：按插件时是全局，按模型时是**这一行模型** ——
        // 同一个模型内比才有意义，跨模型比 token 说明不了花销
        mr.appendChild(meterCell(model.total, mode === "model" ? plugin.total : grand));
        mr.appendChild(el("td", "num", model.cache_hit));
        mr.appendChild(el("td", "num", fmt(model.input_other || 0)));
        mr.appendChild(el("td", "num", fmt(model.input_cached || 0)));
        mr.appendChild(el("td", "num", fmt(model.output || 0)));
        mr.appendChild(el("td", "num", fmt(model.calls)));
        mr.appendChild(el("td", "num", fmt(model.failed)));
        mr.appendChild(el("td", "num", fmt(model.images)));
        modelRows.push(mr);
        body.appendChild(mr);
      });

      if (!modelRows.length) {
        toggle.disabled = true;
        return;
      }
      toggle.addEventListener("click", function () {
        var wasOpen = toggle.getAttribute("aria-expanded") === "true";
        toggle.setAttribute("aria-expanded", String(!wasOpen));
        modelRows.forEach(function (row) { row.hidden = wasOpen; });
        if (wasOpen) state.collapsed[plugin.key] = true;
        else delete state.collapsed[plugin.key];
      });
    });
  }

  /* 卡片跟着「趋势」焦点走：点了某个插件，四张卡片就只算它。 */
  function renderKpis(plugins, series, total, previous) {
    var calls = 0, failed = 0, images = 0, other = 0, cached = 0;
    plugins.forEach(function (p) {
      calls += p.calls || 0;
      failed += p.failed || 0;
      images += p.images || 0;
      other += p.input_other || 0;
      cached += p.input_cached || 0;
    });
    var incoming = other + cached;

    $("kpi-total").textContent = fmt(total);
    setSub($("kpi-total-sub"), series.length + " 天有记录",
      previous ? delta(total, previous.tokens) : "");
    $("kpi-calls").textContent = fmt(calls);
    setSub($("kpi-calls-sub"), images ? "含 " + fmt(images) + " 张图" : "",
      previous ? delta(calls, previous.calls) : "");
    $("kpi-failed").textContent = fmt(failed);
    setSub($("kpi-failed-sub"), calls ? (failed / calls * 100).toFixed(1) + "% 失败率" : "",
      previous ? delta(failed, previous.failed) : "");
    $("kpi-failed-card").className = failed > 0 ? "kpi is-warn" : "kpi";
    // 命中率不能把各插件的比率相加，要用原始计数重算
    var out = 0;
    plugins.forEach(function (p) { out += p.output || 0; });
    var grand = incoming + out;

    $("kpi-in").textContent = fmt(other);
    setSub($("kpi-in-sub"), incoming ? "占输入 " + Math.round(other / incoming * 100) + "%" : "",
      previous && previous.input_other ? delta(other, previous.input_other) : "");
    $("kpi-cached").textContent = fmt(cached);
    // 命中率用原始计数重算，不能把各插件各自的百分比相加
    var reported = 0;
    plugins.forEach(function (p) { reported += p.cache_reported || 0; });
    // 同上：有命中数就不算「未上报」
    var cacheKnown = reported > 0 || cached > 0;
    setSub($("kpi-cached-sub"), !cacheKnown && incoming ? "未上报" : (incoming ? "命中 " + (cached / incoming * 100).toFixed(0) + "%" : ""),
      previous ? pointDelta(incoming ? cached / incoming : 0, ratio(previous.input_other, previous.input_cached)) : "");
    $("kpi-out").textContent = fmt(out);
    setSub($("kpi-out-sub"), grand ? "占合计 " + Math.round(out / grand * 100) + "%" : "", "");
  }

  /* 首屏占位。只在 `state.data` 还是空的时候调用。 */
  function renderSkeleton() {
    var host = $("chart");
    host.textContent = "";
    var bars = el("div", "bars");
    [46, 72, 34, 88, 58, 41, 66].forEach(function (h) {
      var col = el("div", "col is-ghost");
      var ph = el("span", "skeleton");
      ph.style.height = h + "%";
      col.appendChild(ph);
      bars.appendChild(col);
    });
    host.appendChild(bars);

    var body = $("tbody");
    body.textContent = "";
    for (var i = 0; i < 4; i++) {
      var tr = el("tr", "skeleton-row");
      for (var c = 0; c < 10; c++) {
        var td = el("td", c === 0 ? "" : "num");
        td.appendChild(el("span", "skeleton skeleton-text"));
        tr.appendChild(td);
      }
      body.appendChild(tr);
    }
    $("table").hidden = false;
    $("empty").hidden = true;
    $("legend").hidden = true;
    $("table-note").textContent = "";
    $("chart-peak").textContent = "";
    $("summary").textContent = "加载中…";
  }

  /* 焦点切换。再点一次同一个按钮就取消，不用另设一个「取消」入口。 */
  function toggleFocus(plugin, model) {
    var same = state.focus === plugin && state.focusModel === (model || "");
    state.focus = same ? "" : plugin;
    state.focusModel = same ? "" : (model || "");
    // 模型级序列要服务端现算，所以焦点一变就得重新取数
    load();
  }

  function render(data) {
    state.data = data;
    var plugins = data.plugins || [];
    var series = data.series || [];
    var grand = data.total_tokens || 0;

    var byPlugin = data.series_by_plugin || {};

    // 下钻目标可能不在这个范围里（换了时间范围、记录被清过）。
    // 这里**不清焦点**：悄悄退回上层会让图换个样子，而用户不知道发生过什么。
    // 保留它，把图显示成空的并写明原因，范围改回去就能接着看。
    var pluginRow = null;
    if (state.focus) {
      pluginRow = plugins.filter(function (p) { return p.name === state.focus; })[0] || null;
    }
    var modelRow = null;
    if (pluginRow && state.focusModel) {
      modelRow = (pluginRow.models || []).filter(function (m) {
        return m.name === state.focusModel;
      })[0] || null;
    }
    var isFocused = Boolean(state.focus);
    var missing = isFocused && (!pluginRow || (state.focusModel && !modelRow));
    var focused = !isFocused
      ? null
      : (missing
        ? []
        : (modelRow ? (data.focus_series || []) : (byPlugin[state.focus] || [])));

    // 只看一处时不堆叠，免得一堆同色小块
    var rank = isFocused ? null : rankColors(byPlugin);
    var stack = isFocused ? null : stackByDay(byPlugin);

    var label = state.focusModel
      ? state.focus + " · " + state.focusModel
      : state.focus;
    if (missing) label += "（这个范围内没有记录）";
    renderChart(focused || series, label, stack, rank);
    // 焦点不在当前行里时，表格里没有「取消」可按，所以标题旁常驻一个
    $("chart-clear").hidden = !isFocused;

    // 焦点在模型上时，卡片只算那一个模型行
    var scoped = missing ? [] : (modelRow ? [modelRow] : (pluginRow ? [pluginRow] : plugins));
    var scopeTotal = missing ? 0 : (modelRow ? modelRow.total : (pluginRow ? pluginRow.total : grand));
    // 只看一处时不给环比：接口不返回单处历史，编不出可比数字
    renderKpis(scoped, focused || series, scopeTotal, isFocused ? null : data.previous);

    var span = series.length
      ? series[0].day.slice(5) + " ~ " + series[series.length - 1].day.slice(5)
      : "这个范围内没有记录";
    $("summary").textContent = isFocused ? label + " · " + span : span;
    setStatus("补丁 " + data.patched + " 处 · 记录 " + data.recorded + " 次");
    renderTable(buildGroups(plugins, state.group), grand, state.group);
  }

  /* 首屏就失败时把骨架收掉 —— 一直转的灰条和「读取失败」自相矛盾。 */
  function clearSkeleton(message) {
    var host = $("chart");
    host.textContent = "";
    host.appendChild(el("div", "empty", message || "取不到数据。"));
    $("tbody").textContent = "";
    $("table").hidden = true;
    $("empty").hidden = true;
    $("legend").hidden = true;
    $("chart-peak").textContent = "";
    $("summary").textContent = "读取失败";
  }

  /* 取数失败重试。GET 是幂等的，重试安全；POST 就不该这么写。 */
  var RETRIES = 2;

  function fetchStats(params, left) {
    return api.apiGet("stats", params).catch(function (err) {
      if (left <= 0) throw err;
      return new Promise(function (resolve) { setTimeout(resolve, 500); })
        .then(function () { return fetchStats(params, left - 1); });
    });
  }

  var loadSeq = 0;   // 只认最后一次请求，防止快发两次时旧数据后到盖掉新数据

  function load() {
    var seq = ++loadSeq;
    setStatus("加载中", "loading");
    hideTip();
    if (!api) {
      setStatus("桥接未加载", "error");
      return Promise.resolve();
    }
    var params = state.days > 0 ? { days: state.days } : {};
    if (state.focus) params.plugin = state.focus;
    if (state.focusModel) params.model = state.focusModel;
    // 只有首屏才铺骨架；之后刷新保留旧数据，免得闪
    if (!state.data) renderSkeleton();
    return api.ready()
      .then(function (ctx) {
        // 宿主会通过上下文告知当前是不是暗色，这比猜系统偏好准
        if (ctx && typeof ctx.isDark === "boolean") {
          hostDark = ctx.isDark;
          applyTheme();
        }
        return fetchStats(params, RETRIES);
      })
      .then(function (data) {
        if (seq !== loadSeq) return;   // 有更新的请求在飞，这次的结果丢掉
        render(data);
      })
      .catch(function (err) {
        if (seq !== loadSeq) return;
        if (!state.data) clearSkeleton("取不到数据：" + err);
        setStatus("读取失败：" + err, "error");
      });
  }

  $("range").addEventListener("click", function (ev) {
    var btn = ev.target.closest("button[data-days]");
    if (!btn) return;
    state.days = Number(btn.dataset.days);
    syncRange();
    saveRange();
    load();
  });

  // 分组切换：只影响表格。图表仍按插件堆叠 —— 接口没有「按模型按天」的序列，
  // 硬切会让表格和图对不上，所以图不动，旁边写明。
  function syncGroup() {
    // 遍历 children 而不是 querySelectorAll：和 syncRange 一致，也免得依赖选择器
    Array.prototype.forEach.call($("group").children, function (btn) {
      btn.classList.toggle("on", btn.dataset.group === state.group);
    });
    $("group-hint").textContent = state.group === "model" ? "图表仍按插件堆叠" : "";
  }

  $("group").addEventListener("click", function (ev) {
    var btn = ev.target.closest("button[data-group]");
    if (!btn || btn.dataset.group === state.group) return;
    state.group = btn.dataset.group;
    try { window.localStorage.setItem(GROUP_KEY, state.group); } catch (e) { /* 受限 iframe */ }
    syncGroup();
    if (state.data) {
      renderTable(
        buildGroups(state.data.plugins || [], state.group),
        state.data.total_tokens || 0,
        state.group
      );
    }
  });

  // 恢复上次选的范围（HTML 里默认标的是「近 30 天」）
  syncRange();
  syncGroup();
  $("refresh").addEventListener("click", load);

  /* ---------- 重置 ----------
     会落盘的销毁操作，所以要两步确认。用按钮自身的状态而不是 window.confirm：
     插件页跑在 iframe 里，浏览器会直接把 confirm 拦掉并返回 false，点了什么
     都不会发生，连报错都没有。 */
  var resetBtn = $("reset-stats");
  var resetArmed = false;
  var resetTimer = 0;
  function disarmReset() {
    resetArmed = false;
    clearTimeout(resetTimer);
    resetBtn.textContent = "重置";
    resetBtn.classList.remove("armed");
  }
  resetBtn.addEventListener("click", function () {
    if (!resetArmed) {
      resetArmed = true;
      resetBtn.textContent = "再点一次确认";
      resetBtn.classList.add("armed");
      clearTimeout(resetTimer);
      resetTimer = setTimeout(disarmReset, 5000);
      return;
    }
    disarmReset();
    if (!api || !api.apiPost) {
      setStatus("桥接不支持写入，无法重置", "error");
      return;
    }
    setStatus("重置中", "loading");
    api.apiPost("reset", {}).then(function (result) {
      var parts = [];
      if (result && result.baseline_plugins) {
        parts.push("历史 " + result.baseline_plugins + " 个插件");
      }
      if (result && result.memory_calls) {
        parts.push("内存 " + result.memory_calls + " 次");
      }
      setStatus(parts.length ? "已重置：" + parts.join("，") : "已重置");
      // 重置后必须重新取数：此刻 state.data 还是旧的，图表不会自己变
      return load();
    }).catch(function (err) {
      setStatus("重置失败：" + (err && err.message ? err.message : err), "error");
    });
  });

  // 标题旁的「取消下钻」：和下钻时点同一个按钮等价
  $("chart-clear").addEventListener("click", function () {
    toggleFocus(state.focus, state.focusModel);
  });

  // 表头排序：点同一列翻转方向，点别的列从降序开始
  SORT_KEYS.forEach(function (key) {
    $("sort-" + key).addEventListener("click", function () {
      if (state.sort.key === key) {
        state.sort.dir = state.sort.dir === "desc" ? "asc" : "desc";
      } else {
        state.sort.key = key;
        state.sort.dir = "desc";
      }
      renderTable(last.groups, last.grand, last.mode);
    });
  });

  /* ---------- 调用日志 ----------
     单独一张卡片，不混进排行。不自动轮询：上千行会把正在读的位置顶走。
     导出在浏览器里做 —— 数据已经在手上，为下载再走一次后端没道理。 */
  var logLines = [];
  var logTail = 200;

  function inOf(line) {
    var m = /(?:^| )in=(\d+)/.exec(line);
    return m ? Number(m[1]) : -1;
  }

  function renderLog(payload) {
    var body = $("log-body");
    var note = $("log-note");
    if (!payload || !payload.enabled) {
      logLines = [];
      body.textContent = "";
      note.textContent = "没开 call_log —— 在插件配置里打开后才有内容";
      return;
    }
    logLines = (payload.lines || []).slice();
    logLines.sort(function (a, b) { return inOf(b) - inOf(a); });
    body.textContent = logLines.length
      ? logLines.join("\n")
      : "还没有记录。日志跟着 60 秒的节拍落盘，等一会儿或点「刷新」。";
    note.textContent = logLines.length ? payload.path + " · 按 in 降序" : payload.path;
  }

  function loadLog() {
    if (!api || !api.apiGet) return;
    api.apiGet("stats", { tail: logTail }).then(renderLog).catch(function (err) {
      $("log-body").textContent = "";
      $("log-note").textContent = "读不到调用日志：" + err;
    });
  }

  function exportLog() {
    if (!logLines.length) {
      $("log-note").textContent = "没有可导出的内容";
      return;
    }
    var stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
    var blob = new Blob([logLines.join("\n") + "\n"], {
      type: "text/plain;charset=utf-8",
    });
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = "usage-calls-" + stamp + ".log";
    a.click();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  $("log-size").addEventListener("click", function (ev) {
    var btn = ev.target.closest("button[data-tail]");
    if (!btn) return;
    logTail = Number(btn.dataset.tail);
    Array.prototype.forEach.call($("log-size").children, function (b) {
      b.classList.toggle("on", b.dataset.tail === btn.dataset.tail);
    });
    loadLog();
  });
  $("log-refresh").addEventListener("click", loadLog);
  $("log-export").addEventListener("click", exportLog);

  /* 标签切换。日志等到真的点进去才取 —— 一页几百行，没必要在首屏背。
     选择记在 localStorage，刷新后还停在原标签。 */
  var TAB_KEY = "astrbot_usage_stats_tab";

  function showTab(name) {
    state.tab = name === "log" ? "log" : "stats";
    $("tab-stats").hidden = state.tab !== "stats";
    $("tab-log").hidden = state.tab !== "log";
    Array.prototype.forEach.call($("tabs").children, function (btn) {
      btn.classList.toggle("on", btn.dataset.tab === state.tab);
    });
    try { window.localStorage.setItem(TAB_KEY, state.tab); } catch (e) { /* 受限 iframe */ }
    if (state.tab === "log" && !logLines.length) loadLog();
  }

  $("tabs").addEventListener("click", function (ev) {
    var btn = ev.target.closest("button[data-tab]");
    if (!btn || btn.dataset.tab === state.tab) return;
    showTab(btn.dataset.tab);
  });

  var savedTab = null;
  try { savedTab = window.localStorage.getItem(TAB_KEY); } catch (e) { /* 受限 iframe */ }
  showTab(savedTab === "log" ? "log" : "stats");

  load();
  setInterval(load, 30000);
})();
