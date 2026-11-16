/* Original dashboard algorithms; no upstream code or third-party dependencies. */
(function (root) {
  "use strict";
  function known(value) { return typeof value === "number" && Number.isFinite(value); }
  function fmt(value, digits) {
    return known(value) ? value.toLocaleString("en-US", { maximumFractionDigits: digits === undefined ? 2 : digits }) : "—";
  }
  function percent(value) { return known(value) ? fmt(value * 100, 1) + "%" : "—"; }
  function money(value) { return known(value) ? "$" + (value > 0 && value < .000001 ? value.toPrecision(2) : fmt(value, 6)) : "—"; }
  function query(state, view) {
    var q = { view: view || "dashboard", days: [1, 7, 30, 0].indexOf(state.days) >= 0 ? state.days : 30,
      plugin: state.plugin || "", model: state.model || "", provider: state.provider || "",
      result: state.result || "all", group: state.group === "model" ? "model" : "plugin",
      page: Math.max(1, Math.floor(state.page || 1)), page_size: Math.min(200, Math.max(1, Math.floor(state.page_size || 50))),
      speed_mode: state.speed_mode === "ttft" ? "ttft" : "total" };
    if (state.custom) {
      var start = new Date(state.start), end = new Date(state.end);
      if (!state.start || !state.end || !Number.isFinite(start.getTime()) || !Number.isFinite(end.getTime()) || start >= end)
        throw new Error("请选择有效的开始、结束时间，结束时间必须晚于开始时间。");
      q.days = 0; q.start = start.toISOString(); q.end = end.toISOString();
    }
    return q;
  }
  function stacks(timeline, group, hidden) {
    var field = group === "model" ? "models" : "plugins", totals = new Map(), labels = new Map();
    var points = (timeline || []).map(function (point) {
      var values = new Map();
      (point[field] || []).forEach(function (item) {
        var key = String(item.key), value = known(item.tokens) ? Math.max(0, item.tokens) : 0;
        values.set(key, (values.get(key) || 0) + value); labels.set(key, item.label || key);
        totals.set(key, (totals.get(key) || 0) + value);
      });
      // The unsplit total remains visible if the server cannot attribute a bucket.
      var sum = Array.from(values.values()).reduce(function (a, b) { return a + b; }, 0);
      if (known(point.tokens) && point.tokens > sum) {
        var rest = "\u0000unattributed"; values.set(rest, point.tokens - sum); labels.set(rest, "未分类");
        totals.set(rest, (totals.get(rest) || 0) + point.tokens - sum);
      }
      return { label: point.label || "", timestamp: point.timestamp, calls: point.calls, values: values };
    });
    var ranked = Array.from(totals.keys()).sort(function (a, b) { return totals.get(b) - totals.get(a) || a.localeCompare(b); });
    if (ranked.length > 6) {
      var remainder = ranked.slice(6), otherKey = "\u0000other";
      labels.set(otherKey, "其他（" + remainder.length + " 项）");
      totals.set(otherKey, remainder.reduce(function (sum, key) { return sum + totals.get(key); }, 0));
      points.forEach(function (point) {
        var rest = remainder.reduce(function (sum, key) { return sum + (point.values.get(key) || 0); }, 0);
        point.values.set(otherKey, rest);
      });
      ranked = ranked.slice(0, 6).concat([otherKey]);
    }
    var series = ranked.map(function (key, index) { return { key: key, label: labels.get(key), total: totals.get(key), index: index }; });
    var max = 0;
    points.forEach(function (point) {
      point.total = 0; point.segments = [];
      series.forEach(function (item) {
        if (hidden && hidden.has(item.key)) return;
        var value = point.values.get(item.key) || 0;
        if (value > 0) point.segments.push({ key: item.key, label: item.label, value: value, offset: point.total, index: item.index });
        point.total += value;
      });
      max = Math.max(max, point.total);
    });
    return { points: points, series: series, max: max };
  }
  function sorted(rows, key, direction) {
    return (rows || []).slice().sort(function (a, b) {
      var av = a[key], bv = b[key];
      if (!known(av) && !known(bv)) return String(a.label || a.key).localeCompare(String(b.label || b.key));
      if (!known(av)) return 1; if (!known(bv)) return -1;
      return (direction === "asc" ? av - bv : bv - av) || (b.tokens || 0) - (a.tokens || 0);
    });
  }
  function price(value) {
    if (value === null || value === undefined || String(value).trim() === "") return null;
    var text = String(value).trim();
    if (!/^(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(text)) throw new Error("价格必须是有限且不小于 0 的数字；未知价格请留空。");
    var n = Number(text);
    if (!Number.isFinite(n) || n < 0) throw new Error("价格必须是有限且不小于 0 的数字；未知价格请留空。");
    return n;
  }
  function components(overview) {
    return [{ key: "input_other", label: "输入（未缓存）" }, { key: "input_cached", label: "缓存读取" }, { key: "output", label: "输出" }]
      .map(function (part, index) { return { key: part.key, label: part.label, value: overview[part.key], index: index }; });
  }
  function manualPrice(prices, model, provider) {
    return (prices || []).find(function (p) { return p.model === model && (p.provider || "") === (provider || ""); })
      || (prices || []).find(function (p) { return p.model === model && !(p.provider || ""); }) || null;
  }
  function quote(value) { return known(value) ? "$" + (value > 0 && value < .0001 ? value.toPrecision(2) : value.toFixed(4)) : "—"; }
  function pricing(data) {
    data = data || {};
    var o = data.overview || {}, p = data.pricing, manuals = Array.isArray(data.prices) ? data.prices : [];
    var models = ((data.analysis || {}).models || []), valid = p && Array.isArray(p.rows);
    if (valid && !p.rows.length && models.length) valid = false;
    var rows;
    if (valid) rows = p.rows;
    else {
      var seen = new Set();
      rows = models.filter(function (row) { return typeof row.key === "string" && row.key; }).map(function (row) {
        seen.add(JSON.stringify(["", row.key])); var manual = manualPrice(manuals, row.key, "");
        return { model: row.key, provider: "", calls: row.calls, tokens: row.tokens, estimated_cost: null,
          priced_calls: null, unpriced_calls: null, price: manual, price_source: manual ? "manual" : null, source_provider: null };
      });
      manuals.forEach(function (manual) {
        var key = JSON.stringify([manual.provider || "", manual.model]); if (seen.has(key)) return;
        seen.add(key); rows.push({ model: manual.model, provider: manual.provider || "", calls: models.some(function (r) { return r.key === manual.model; }) ? null : 0,
          tokens: null, estimated_cost: null, priced_calls: null, unpriced_calls: null, price: manual, price_source: "manual", source_provider: null });
      });
    }
    return { supported: !!valid, rows: rows.filter(function (row) { return row && typeof row.model === "string" && row.model; }),
      total_cost: valid ? (known(p.total_cost) ? p.total_cost : null) : (known(o.estimated_cost) ? o.estimated_cost : null),
      priced_calls: valid ? p.priced_calls : o.priced_calls, unpriced_calls: valid ? p.unpriced_calls : o.unpriced_calls,
      available_prices: valid ? p.available_prices : manuals.length, updated_at: valid && known(p.updated_at) ? p.updated_at : null,
      source: "models.dev" };
  }
  function pricePage(rows, search, page, size) {
    var needle = String(search || "").trim().toLowerCase();
    var filtered = (rows || []).filter(function (row) { return !needle || [row.model, row.provider, row.source_provider].join(" ").toLowerCase().indexOf(needle) >= 0; });
    size = Math.max(1, Math.min(100, Math.floor(size || 50)));
    var pages = Math.max(1, Math.ceil(filtered.length / size)); page = Math.max(1, Math.min(pages, Math.floor(page || 1)));
    return { rows: filtered.slice((page - 1) * size, page * size), page: page, pages: pages, total: filtered.length, start: filtered.length ? (page - 1) * size + 1 : 0, end: Math.min(page * size, filtered.length) };
  }
  root.UsageDashboard = { known: known, fmt: fmt, percent: percent, money: money, query: query, stacks: stacks, sorted: sorted, price: price, components: components, manualPrice: manualPrice, quote: quote, pricing: pricing, pricePage: pricePage };
})(typeof window !== "undefined" ? window : globalThis);
