/* Dependency-free runtime regression tests. Run: node tests/view_smoke.mjs.
 * The upgraded UI uses the dashboard contract; protections from the legacy smoke
 * remain: output propagation, grouping, sorting/empty state, drill filters, races,
 * chart keyboard access and arithmetic, themes, reset confirmation, bounded logs.
 * This DOM stub does not validate browser layout. */
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
const base = new URL("../views/usage/", import.meta.url);
const html = fs.readFileSync(new URL("index.html", base), "utf8");
const helper = fs.readFileSync(new URL("dashboard.js", base), "utf8");
const source = fs.readFileSync(new URL("app.js", base), "utf8");
function node(tag) {
  const n = { tagName: tag, children: [], parentNode: null, dataset: {}, style: {}, attrs: {}, listeners: {}, value: "", checked: false, disabled: false, hidden: false, tabIndex: -1, _text: "", _cls: "",
    get textContent() { return this._text + this.children.map(c => c.textContent).join(""); },
    set textContent(text) { this._text = String(text); this.children.forEach(c => c.parentNode = null); this.children = []; },
    get className() { return this._cls; }, set className(value) { this._cls = value; },
    appendChild(c) { c.parentNode = this; this.children.push(c); return c; },
    remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(c => c !== this); this.parentNode = null; },
    addEventListener(event, fn) { (this.listeners[event] ||= []).push(fn); },
    setAttribute(key, value) { this.attrs[key] = String(value); if (key === "tabindex") this.tabIndex = Number(value); if (key.startsWith("data-")) this.dataset[key.slice(5)] = String(value); },
    getAttribute(key) { return this.attrs[key] ?? null; }, removeAttribute(key) { delete this.attrs[key]; }, hasAttribute(key) { return key in this.attrs; },
    closest(selector) { const m = /^button(?:\[data-([a-z-]+)\])?$/.exec(selector); if (m && this.tagName === "button" && (!m[1] || m[1] in this.dataset)) return this; return this.parentNode?.closest(selector) || null; },
    focus() { this.focused = true; fire(this, "focus"); }, click() { fire(this, "click"); }
  };
  n.classList = { add(c) { if (!n._cls.split(" ").includes(c)) n._cls += " " + c; }, remove(c) { n._cls = n._cls.split(" ").filter(x => x !== c).join(" "); }, toggle(c, on) { if (on) this.add(c); else this.remove(c); } };
  return n;
}
function fire(n, type, event = {}) { for (const fn of n.listeners[type] || []) fn({ target: n, preventDefault() {}, ...event }); }
function parse() {
  const registry = new Map(), root = node("root"), stack = [root];
  for (const match of html.matchAll(/<\/?([a-zA-Z][\w-]*)([^>]*)>/g)) {
    const [text, tag, attr] = match;
    if (text.startsWith("</")) { if (stack.at(-1).tagName === tag) stack.pop(); continue; }
    const n = node(tag);
    for (const a of attr.matchAll(/([\w-]+)(?:="([^"]*)")?/g)) {
      const key = a[1], value = a[2] ?? ""; n.setAttribute(key, value);
      if (key === "id") registry.set(value, n); if (key === "value") n.value = value;
      if (key === "hidden") n.hidden = true; if (key === "disabled") n.disabled = true;
    }
    stack.at(-1).appendChild(n);
    if (!["meta", "link", "input", "br", "hr"].includes(tag) && !text.endsWith("/>")) stack.push(n);
  }
  return registry;
}
const sample = {
  overview: { calls: 69, success: 67, failed: 1, canceled: 1, tokens: 259212, input_other: 145467, input_cached: 112505, output: 1240, reasoning: 240, cache_write: 50,
    cache_hit_rate: 0.436, cache_share: 0.434, success_rate: 67 / 69, tps: 24, tps_samples: 4, rpm: 2, average_latency_ms: 1200, latency_samples: 4, estimated_cost: 0, priced_calls: 2, unpriced_calls: 3 },
  timeline: [
    { timestamp: 1792022400, label: "10-15", calls: 5, tokens: 40000, models: [{ key: "raw|R1", label: "R1", tokens: 30000 }, { key: "other", tokens: 10000 }], plugins: [{ key: "angel", tokens: 30000 }, { key: "main_agent", tokens: 10000 }] },
    { timestamp: 1792108800, label: "10-16", calls: 64, tokens: 219212, models: [{ key: "raw|R1", label: "R1", tokens: 114038 }, { key: "other", tokens: 105174 }], plugins: [{ key: "angel", tokens: 114038 }, { key: "main_agent", tokens: 105174 }] }
  ], granularity: "day",
  analysis: { plugins: [{ key: "angel", label: "Angel", calls: 29, tokens: 144038, failed: 0 }, { key: "main_agent", calls: 40, tokens: 115174, failed: 1 }],
    models: [{ key: "raw|R1", label: "R1", calls: 29, tokens: 144038, failed: 0 }], providers: [{ key: "openai", calls: 69, tokens: 259212, failed: 1 }] },
  events: { items: [{ id: "a", timestamp: 1792022400, plugin: "angel", provider: "openai", model: "raw|R1", result: "success", input_other: 1, input_cached: 0, output: 240, reasoning: null, cache_write: 0, tokens: 241, latency_ms: 0, ttft_ms: null, tps: null, estimated_cost: 0 }], page: 1, page_size: 50, total: 70, total_pages: 2 },
  options: { plugins: ["angel", "main_agent"], models: ["raw|R1", "other"], providers: ["openai"] },
  prices: [{ model: "raw|R1", provider: "", input: null, cache_read: 0, cache_write: null, output: 2 }],
  pricing: {total_cost:0,priced_calls:40,unpriced_calls:29,available_prices:85,updated_at:1792022400,source:"models.dev",rows:[
    {model:"raw|R1",provider:"openai",calls:29,tokens:144038,estimated_cost:null,priced_calls:0,unpriced_calls:29,price:{input:null,cache_read:0,cache_write:null,output:2},price_source:"manual",source_provider:null},
    {model:"other",provider:"openai",calls:40,tokens:115174,estimated_cost:0,priced_calls:40,unpriced_calls:0,price:{input:0,output:0,cache_read:0,cache_write:0},price_source:"models.dev",source_provider:"openai"},
    {model:"catalog-unused",provider:"",calls:0,tokens:0,estimated_cost:0,priced_calls:0,unpriced_calls:0,price:{input:2,output:10,cache_read:.2,cache_write:2.5},price_source:"models.dev",source_provider:"vendor"}
  ]},
  coverage: { legacy_calls: 60, detail_calls: 9, excluded_legacy_calls: 0, detail_start: null, warnings: ["部分旧数据不含明细"] },
  settings: { enabled: false, detail_history: true, keep_days: 13, detail_max_events: 37, writable: true, revision: 7, reload_required: false, runtime_error: "" },
  collection: { detail_enabled: false, detail_available: true, detail_error: "", patched_count: 4, pending: 2, dropped: 1, wrapped_methods: ["LiveProvider.text_chat", "LiveProvider.text_chat_stream"] }
};
const clone = value => structuredClone(value);
function boot({ get, post, dark = true, alias = false } = {}) {
  const registry = parse(), intervals = [], timers = [], gets = [], posts = [], downloads = [], store = new Map();
  const byId = id => { assert.ok(registry.has(id), "unknown DOM id: " + id); return registry.get(id); };
  const doc = { documentElement: node("html"), hidden: false, getElementById: byId, createElement: node, createElementNS: (_ns, tag) => node(tag), createTextNode: text => { const n = node("#text"); n.textContent = text; return n; } };
  const bridge = { ready: async () => ({ isDark: dark }), apiGet: async (path, params) => { gets.push({ path, params: clone(params) }); return get ? get(path, params) : clone(sample); },
    apiPost: async (path, body) => { posts.push({ path, body: clone(body) }); return post ? post(path, body) : { saved: true, baseline_plugins: 3, memory_calls: 7 }; } };
  const win = { matchMedia: () => ({ matches: false, addEventListener() {} }), localStorage: { getItem: key => store.get(key) ?? null, setItem: (key, value) => store.set(key, String(value)) }, [alias ? "AstrBotPluginView" : "AstrBotPluginPage"]: bridge };
  const context = vm.createContext({ window: win, document: doc, Blob, URL: { createObjectURL: blob => { downloads.push(blob); return "blob:test"; }, revokeObjectURL() {} },
    setInterval: fn => intervals.push(fn), setTimeout: (fn, delay) => { timers.push({ fn, delay }); if (delay <= 500) queueMicrotask(fn); return timers.length; }, clearTimeout: id => { if (timers[id - 1]) timers[id - 1].canceled = true; } });
  vm.runInContext(helper, context); vm.runInContext(source, context);
  return { byId, doc, gets, posts, downloads, intervals, timers, bridge, tab(name) { fire(byId("tabs"), "click", { target: byId("tab-" + name + "-btn") }); }, filter(key, value) { byId("filter-" + key).value = value; fire(byId("filter-" + key), "change"); } };
}
const flush = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
const app = boot();
assert.equal(app.byId("dashboard").getAttribute("aria-busy"), "true");
assert.match(app.byId("status-text").textContent, /加载/);
await flush();
for (const [id, value] of [["kpi-calls", "69"], ["kpi-total", "259,212"], ["kpi-tps", "24"], ["kpi-cost", "$0"], ["kpi-cache", "43.6%"]]) assert.equal(app.byId(id).textContent, value);
assert.match(app.byId("token-parts").textContent, /输出1,240/); // output survives all projections
assert.match(app.byId("events-body").textContent, /240/);
assert.equal(app.doc.documentElement.getAttribute("data-theme"), "dark");
assert.equal(app.byId("dashboard").getAttribute("aria-busy"), "false");
assert.equal(app.gets[0].path, "stats"); assert.equal(app.gets[0].params.view, "dashboard"); assert.equal(app.gets[0].params.days, 30);
assert.equal(app.byId("range").value, "30");
assert.equal(app.byId("group").children[1].getAttribute("aria-pressed"), "true");
assert.equal(app.byId("legend").children.length, 2);
const canvas = app.byId("chart").children[0], bars = canvas.children.filter(n => n.getAttribute("class") === "chart-bar");
assert.equal(bars.filter(n => n.tabIndex === 0).length, 1);
fire(canvas, "keydown", { target: bars[0], key: "ArrowRight" }); assert.equal(bars[0].tabIndex, -1); assert.equal(bars[1].tabIndex, 0);
fire(canvas, "keydown", { target: bars[1], key: "End" }); assert.equal(bars.at(-1).tabIndex, 0);
fire(canvas, "keydown", { target: bars.at(-1), key: "ArrowRight" }); assert.equal(bars.at(-1).tabIndex, 0);
const peakRects = bars[1].children.filter(n => n.tagName === "rect" && n.getAttribute("height") !== "237");
assert.ok(Math.abs(peakRects.reduce((sum, r) => sum + Number(r.getAttribute("height")), 0) - 237) < 1e-9);
const beforeLegend = app.gets.length; fire(app.byId("legend").children[0], "click");
assert.equal(app.gets.length, beforeLegend); assert.equal(app.byId("kpi-total").textContent, "259,212"); assert.equal(app.byId("legend").children[0].getAttribute("aria-pressed"), "false");
fire(app.byId("sort-calls"), "click"); assert.equal(app.byId("tbody").children[0].children[0].textContent, "main_agent"); assert.equal(app.byId("th-calls").getAttribute("aria-sort"), "descending");
fire(app.byId("sort-calls"), "click"); assert.equal(app.byId("tbody").children[0].children[0].textContent, "Angel"); assert.equal(app.byId("th-calls").getAttribute("aria-sort"), "ascending");
fire(app.byId("tbody").children[0].children[0].children[0], "click"); await flush(); assert.equal(app.gets.at(-1).params.plugin, "angel");
app.byId("analysis-group").value = "models"; fire(app.byId("analysis-group"), "change");
fire(app.byId("tbody").children[0].children[0].children[0], "click"); await flush(); assert.equal(app.gets.at(-1).params.model, "raw|R1"); assert.equal(app.gets.at(-1).params.plugin, "angel");
// Group switch affects chart and request query, but not numerical KPI definitions.
fire(app.byId("group"), "click", { target: app.byId("group").children[0] }); await flush(); assert.equal(app.gets.at(-1).params.group, "model"); assert.match(app.byId("legend").textContent, /R1/);
assert.equal(app.byId("kpi-total").textContent, "259,212");
// Paging and unified filters reset the page for every view.
fire(app.byId("page-next"), "click"); await flush(); assert.equal(app.gets.at(-1).params.page, 2);
app.filter("provider", "openai"); await flush(); assert.equal(app.gets.at(-1).params.page, 1);
app.filter("result", "canceled"); await flush(); assert.equal(app.gets.at(-1).params.result, "canceled");
app.byId("page-size").value = "200"; fire(app.byId("page-size"), "change"); await flush(); assert.equal(app.gets.at(-1).params.page_size, 200);
// Column visibility and width are usable without drag, and survive refresh.
const controls = app.byId("event-columns").children;
const outputControl = controls.find(c => c.children[0].textContent === "输出");
outputControl.children[1].value = "222"; fire(outputControl.children[1], "change");
let outputHeader = app.byId("events-head").children[0].children.find(c => c.textContent === "输出"); assert.equal(outputHeader.style.width, "222px");
outputControl.children[0].children[0].checked = false; fire(outputControl.children[0].children[0], "change"); assert.ok(!app.byId("events-head").textContent.includes("输出"));
fire(app.byId("refresh"), "click"); await flush(); assert.ok(!app.byId("events-head").textContent.includes("输出"));
// Tabs expose selected state, roving focus and all six panels.
fire(app.byId("tabs"), "keydown", { target: app.byId("tab-stats-btn"), key: "ArrowRight" });
assert.equal(app.byId("tab-analysis-btn").getAttribute("aria-selected"), "true"); assert.equal(app.byId("tab-stats-btn").tabIndex, -1); assert.equal(app.byId("tab-analysis").hidden, false); assert.equal(app.byId("tab-analysis-btn").focused, true);
fire(app.byId("tabs"), "keydown", { target: app.byId("tab-analysis-btn"), key: "End" }); assert.equal(app.byId("tab-manage").hidden, false);
fire(app.byId("tabs"), "keydown", { target: app.byId("tab-manage-btn"), key: "Home" }); assert.equal(app.byId("tab-stats").hidden, false);
// Custom local datetime inputs are serialized as timezone-qualified ISO, end excluded.
app.byId("range").value="custom"; fire(app.byId("range"), "change"); await flush(); assert.equal(app.byId("custom-range").hidden,false);
fire(app.byId("apply-range"),"click"); await flush(); assert.match(app.byId("status-text").textContent,/有效/);
app.byId("start").value = "2026-10-15T10:30"; app.byId("end").value = "2026-10-15T12:00"; fire(app.byId("apply-range"), "click"); await flush();
assert.equal(app.gets.at(-1).params.start, new Date("2026-10-15T10:30").toISOString()); assert.match(app.gets.at(-1).params.end, /Z$/); assert.equal(app.gets.at(-1).params.days, 0);
app.byId("range").value="0"; fire(app.byId("range"), "change"); await flush(); assert.equal(app.gets.at(-1).params.days, 0); assert.equal(app.gets.at(-1).params.start, undefined);
// Automatic refresh pauses for hidden documents and never polls logs.
const beforeHidden = app.gets.length; app.doc.hidden = true; app.intervals[0](); await flush(); assert.equal(app.gets.length, beforeHidden);
app.doc.hidden = false; app.intervals[0](); await flush(); assert.equal(app.gets.length, beforeHidden + 1);
// Price statistics start as read-only rows; only explicit edits produce manual writes.
app.tab("prices"); assert.equal(app.byId("price-editor").hidden,true); assert.doesNotMatch(app.byId("price-status").textContent,/未保存/);
assert.equal(app.byId("price-total").textContent,"$0"); assert.ok(app.byId("price-summary").textContent.includes("40 / 69"));
assert.equal(app.byId("prices-body").children.length,3);
const beforeSearch = app.gets.length; app.byId("price-search").value="other"; fire(app.byId("price-search"),"input");
assert.equal(app.byId("prices-body").children.length,1); assert.equal(app.gets.length,beforeSearch);
app.byId("price-search").value=""; fire(app.byId("price-search"),"input");
fire(app.byId("prices-body").children[0].children[8].children[0],"click");
assert.equal(app.byId("price-editor").hidden,false); assert.equal(app.byId("price-edit-input").value,""); assert.equal(app.byId("price-edit-cache_read").value,"0");
assert.doesNotMatch(app.byId("price-edit-status").textContent,/未保存/);
const priceInput=app.byId("price-edit-input"); priceInput.value="-1"; fire(priceInput,"input"); fire(app.byId("price-save"),"click"); await flush();
assert.equal(app.posts.length,0); assert.match(app.byId("price-edit-status").textContent,/价格/);
fire(app.byId("refresh"),"click"); await flush(); assert.equal(app.byId("price-edit-input").value,"-1");
priceInput.value=""; fire(priceInput,"input"); fire(app.byId("price-save"),"click"); await flush();
assert.equal(app.posts.at(-1).path,"prices"); assert.equal(app.posts.at(-1).body.prices.length,1);
assert.equal(app.posts.at(-1).body.prices[0].input,null); assert.equal(app.posts.at(-1).body.prices[0].cache_read,0); assert.equal(app.posts.at(-1).body.prices[0].model,"raw|R1");
assert.match(app.byId("collection-status").textContent,/未启用/); assert.match(app.byId("coverage").textContent,/详细历史起点—/); assert.match(app.byId("coverage-warnings").textContent,/旧数据/);
// Edits made while saving remain visible through both a save response and refresh.
let savedPriceSample=clone(sample),finishPriceSave;
const priceRace=boot({get:()=>clone(savedPriceSample),post:()=>new Promise(resolve=>{finishPriceSave=resolve;})}); await flush();
fire(priceRace.byId("prices-body").children[0].children[8].children[0],"click");
const racePriceInput=priceRace.byId("price-edit-input"); racePriceInput.value="4"; fire(racePriceInput,"input"); fire(priceRace.byId("price-save"),"click"); await flush();
racePriceInput.value="5"; fire(racePriceInput,"input"); savedPriceSample.prices[0].input=4; finishPriceSave({saved:true}); await flush();
fire(priceRace.byId("refresh"),"click"); await flush(); assert.equal(priceRace.byId("price-edit-input").value,"5");
// Catalog rows stay read-only until edited; public sync never POSTs the catalog as manual rows.
const beforeSync=app.posts.length; fire(app.byId("price-sync"),"click"); await flush();
assert.equal(app.posts.at(-1).path,"price_sync"); assert.equal(app.posts.at(-1).body.source,"models.dev"); assert.equal(app.posts.length,beforeSync+1);
assert.match(app.byId("price-status").textContent,/未确认|失败/); assert.equal(app.byId("price-sync").disabled,false);
const synced=boot({post:async(path)=>path==="price_sync"?{synced:true,rows:85,updated_at:1792022400,source:"models.dev"}:{saved:true}}); await flush();
fire(synced.byId("price-sync"),"click"); await flush(); assert.match(synced.byId("price-status").textContent,/已更新/);
const offline=boot({post:async()=>{throw new Error("offline");}}); await flush(); fire(offline.byId("price-sync"),"click"); await flush(); assert.match(offline.byId("price-status").textContent,/更新失败/); assert.equal(offline.byId("price-sync").disabled,false);
const noManualSample=clone(sample); noManualSample.prices=[]; noManualSample.pricing.rows[0].price=null; noManualSample.pricing.rows[0].price_source=null;
const noManual=boot({get:()=>clone(noManualSample)}); await flush(); assert.match(noManual.byId("prices-body").textContent,/raw\|R1/); assert.doesNotMatch(noManual.byId("price-status").textContent,/未保存/);
const legacyPriceSample=clone(noManualSample); delete legacyPriceSample.pricing;
const legacyPrices=boot({get:()=>clone(legacyPriceSample)}); await flush(); assert.equal(legacyPrices.byId("price-compat").hidden,false); assert.match(legacyPrices.byId("prices-body").textContent,/raw\|R1/);
const largePriceSample=clone(sample); largePriceSample.pricing.rows=Array.from({length:123},(_,i)=>({model:"catalog-"+i,provider:"",calls:0,tokens:0,estimated_cost:0,price:null,price_source:null}));
const largePrices=boot({get:()=>clone(largePriceSample)}); await flush(); assert.equal(largePrices.byId("prices-body").children.length,50); fire(largePrices.byId("price-next"),"click"); assert.equal(largePrices.byId("prices-body").children.length,50); assert.match(largePrices.byId("price-page-note").textContent,/51-100/);
// Manual add alone does not claim unsaved modifications; cancel does not POST.
const beforeAdd=app.posts.length; fire(app.byId("price-add"),"click"); assert.doesNotMatch(app.byId("price-edit-status").textContent,/未保存/); fire(app.byId("price-cancel"),"click"); assert.equal(app.posts.length,beforeAdd);

// Editing a directory row creates one manual override, never imports the whole catalog.
fire(app.byId("prices-body").children[1].children[8].children[0],"click"); fire(app.byId("price-save"),"click"); await flush();
assert.equal(app.posts.at(-1).body.prices.length,2); assert.ok(app.posts.at(-1).body.prices.some(p=>p.model==="other")); assert.ok(!app.posts.at(-1).body.prices.some(p=>p.model==="catalog-unused"));
// Removing the persisted override works even if the unsaved draft is invalid, and restores catalog display.
const restoredSample=clone(sample);
const restore=boot({get:()=>clone(restoredSample),post:async(path,body)=>{ if(path==="prices") {restoredSample.prices=clone(body.prices);restoredSample.pricing.rows[0].price_source="models.dev";restoredSample.pricing.rows[0].price={input:1,output:2,cache_read:.1,cache_write:null};} return {saved:true}; }}); await flush();
fire(restore.byId("prices-body").children[0].children[8].children[0],"click"); restore.byId("price-edit-input").value="-9"; fire(restore.byId("price-edit-input"),"input"); fire(restore.byId("price-remove"),"click"); await flush();
assert.equal(restore.posts.at(-1).body.prices.length,0); assert.match(restore.byId("prices-body").children[0].textContent,/Models.dev/); assert.equal(restore.byId("price-editor").hidden,true);
// Public refresh preserves an active manual draft and only writes the sync endpoint.
const draftSync=boot({post:async()=>({synced:true,rows:85,updated_at:1792022400,source:"models.dev"})}); await flush();
fire(draftSync.byId("prices-body").children[0].children[8].children[0],"click"); draftSync.byId("price-edit-input").value="7"; fire(draftSync.byId("price-edit-input"),"input"); fire(draftSync.byId("price-sync"),"click"); await flush();
assert.equal(draftSync.byId("price-edit-input").value,"7"); assert.equal(draftSync.byId("price-editor").hidden,false); assert.ok(draftSync.posts.every(p=>p.path==="price_sync"));
const partialReset=boot({post:async()=>({saved:false,note:"disk full"})}); await flush(); partialReset.tab("manage"); fire(partialReset.byId("reset-stats"),"click"); fire(partialReset.byId("reset-stats"),"click"); await flush(); assert.match(partialReset.byId("reset-status").textContent,/重置失败/); assert.doesNotMatch(partialReset.byId("reset-status").textContent,/已重置/);
// Destructive reset: first click arms only, cancel/Escape/expiry never POST.
app.tab("manage"); const countBeforeReset = app.posts.length;
fire(app.byId("reset-stats"), "click"); assert.equal(app.posts.length, countBeforeReset); assert.equal(app.byId("reset-stats").textContent, "再点一次确认");
fire(app.byId("reset-cancel"), "click"); assert.equal(app.byId("reset-stats").textContent, "重置");
fire(app.byId("reset-stats"), "click"); fire(app.byId("reset-stats"), "keydown", { key: "Escape" }); assert.equal(app.posts.length, countBeforeReset);
fire(app.byId("reset-stats"), "click"); app.timers.filter(t => t.delay === 5000 && !t.canceled).at(-1).fn(); assert.equal(app.byId("reset-stats").textContent, "重置");
fire(app.byId("reset-stats"), "click"); fire(app.byId("reset-stats"), "click"); await flush(); assert.equal(app.posts.at(-1).path, "reset"); assert.equal(app.posts.length, countBeforeReset + 1); assert.equal(app.byId("reset-stats").textContent, "重置");
assert.equal(app.gets.at(-1).params.view, "dashboard");
// Empty/race/error protections: no stale rows resurrect on sort, latest request wins.
const emptySample = clone(sample); emptySample.analysis.plugins = []; emptySample.timeline = [];
const empty = boot({ get: () => emptySample }); await flush(); assert.equal(empty.byId("table").hidden, true); assert.equal(empty.byId("empty").hidden, false); fire(empty.byId("sort-calls"), "click"); assert.equal(empty.byId("tbody").children.length, 0);
const pending = []; const race = boot({ get: () => new Promise((resolve, reject) => pending.push({ resolve, reject })) }); await flush();
fire(race.byId("refresh"), "click"); await flush(); assert.equal(pending.length, 2); assert.match(race.byId("status-text").textContent, /加载/);
const newer = clone(sample); newer.overview.tokens = 111111; pending[1].resolve(newer); await flush(); pending[0].resolve(sample); await flush(); assert.equal(race.byId("kpi-total").textContent, "111,111");
const broken = boot({ get: () => Promise.reject(new Error("bridge down")), alias: true }); await flush(); assert.match(broken.byId("chart").textContent, /取不到数据/); assert.equal(broken.byId("table").hidden, true); assert.match(broken.byId("status-text").textContent, /读取失败/);
const unknownSample = clone(sample); Object.assign(unknownSample.overview, { tps: null, success_rate: null, cache_hit_rate: null, estimated_cost: null });
const unknown = boot({ get: () => unknownSample, dark: false }); await flush(); for (const id of ["kpi-tps", "kpi-success", "kpi-cache", "kpi-cost"]) assert.equal(unknown.byId(id).textContent, "—"); assert.equal(unknown.doc.documentElement.getAttribute("data-theme"), "light");
const zeroSample = clone(sample); Object.assign(zeroSample.overview, { tps: 0, success_rate: 0, cache_hit_rate: 0, estimated_cost: 0 }); const zero = boot({ get: () => zeroSample }); await flush(); assert.equal(zero.byId("kpi-tps").textContent, "0"); assert.equal(zero.byId("kpi-cache").textContent, "0%");
// Missing selected options stay explicit, never silently clear an active scope.
const absentSample = clone(sample); absentSample.options.models = []; const absent = boot({ get: () => absentSample }); await flush(); absent.filter("model", "raw|R1"); await flush(); assert.equal(absent.byId("filter-model").value, "raw|R1"); assert.match(absent.byId("filter-model").textContent, /当前时间范围无记录/);
// Tiny bars retain actual proportional height, and zero bars have no painted segment.
const tinySample = clone(sample); tinySample.timeline = [{ label: "big", tokens: 1000000, plugins: [{ key: "p", tokens: 1000000 }] }, { label: "tiny", tokens: 1, plugins: [{ key: "p", tokens: 1 }] }, { label: "zero", tokens: 0, plugins: [] }];
const tiny = boot({ get: () => tinySample }); await flush(); const tinyBars = tiny.byId("chart").children[0].children.filter(n => n.getAttribute("class") === "chart-bar");
assert.equal(Number(tinyBars[0].children[1].getAttribute("height")), 237); assert.equal(Number(tinyBars[1].children[1].getAttribute("height")), 0.000237); assert.equal(tinyBars[2].children.length, 2);
// Export uses stats with identical global filters and discloses bounded/truncated history.
const exporter = boot({ get: (_path, q) => q.view === "export" ? { csv: "model,tokens\nraw,1\n", returned: 5000, total: 6000, truncated: true } : clone(sample) }); await flush(); exporter.filter("model", "raw|R1"); await flush(); fire(exporter.byId("export-events"), "click"); await flush();
assert.equal(exporter.gets.at(-1).path, "stats"); assert.equal(exporter.gets.at(-1).params.view, "export"); assert.equal(exporter.gets.at(-1).params.model, "raw|R1"); assert.equal(exporter.downloads.length, 1); assert.match(exporter.byId("export-status").textContent, /已截断/); assert.match(exporter.byId("export-status").textContent, /不含旧汇总/);
const exportPending = []; const exportRace = boot({ get: (_path, q) => q.view === "export" ? new Promise(resolve => exportPending.push(resolve)) : clone(sample) }); await flush(); fire(exportRace.byId("export-events"), "click"); await flush(); exportRace.filter("model", "other"); await flush(); exportPending[0]({ csv: "stale", returned: 1, total: 1 }); await flush(); assert.equal(exportRace.downloads.length, 0);
// Bounded log states/privacy and input-token order are preserved, with independent generation guards.
const log = boot({ get: (_path, q) => q.tail ? { enabled: true, state: "truncated", returned: 4, truncated: true, lines: ["in=1", "in=4"] } : clone(sample) }); await flush(); log.tab("log"); await flush();
assert.equal(log.gets.at(-1).path, "stats"); assert.equal(log.gets.at(-1).params.tail, 200); assert.equal(Object.keys(log.gets.at(-1).params).length, 1); assert.equal(log.byId("log-body").textContent, "in=4\nin=1"); assert.match(log.byId("log-status").textContent, /最近 4 条/); assert.equal(log.byId("log-export").disabled, false); assert.equal(log.byId("log-refresh").disabled, false);
const beforeLogInterval = log.gets.length; log.intervals[0](); await flush(); assert.equal(log.gets.length, beforeLogInterval);
fire(log.byId("log-export"), "click"); assert.equal(log.downloads.length, 1);
const logPending = []; const logRace = boot({ get: (_path, q) => q.tail ? new Promise(resolve => logPending.push(resolve)) : clone(sample) }); await flush(); logRace.tab("log");
fire(logRace.byId("log-size"), "click", { target: logRace.byId("log-size").children[2] }); assert.equal(logRace.gets.at(-1).params.tail, 5000);
logPending[1]({ state: "ready", returned: 1, lines: ["newest"] }); await flush(); logPending[0]({ state: "ready", returned: 1, lines: ["stale"] }); await flush(); assert.equal(logRace.byId("log-body").textContent, "newest");
for (const [state, text] of [["disabled", "未启用"], ["empty", "暂无"], ["unavailable", "无法读取"], ["invalid_request", "无效"], ["partial", "尚未写入"]]) {
  const run = boot({ get: (_path, q) => q.tail ? { state, returned: 0, lines: [], error_code: "read_failed" } : clone(sample) }); await flush(); run.tab("log"); await flush(); assert.match(run.byId("log-status").textContent, new RegExp(text)); assert.equal(run.byId("log-export").disabled, true);
}
const logFail = boot({ get: (_path, q) => q.tail ? Promise.reject(new Error("bad")) : clone(sample) }); await flush(); logFail.tab("log"); await flush(); assert.match(logFail.byId("log-status").textContent, /读取失败/); assert.equal(logFail.byId("log-refresh").disabled, false);
const injection = clone(sample); injection.analysis.plugins[0].label = '<img src=x onerror="boom">'; injection.coverage.warnings = ['<script>boom</script>']; const safe = boot({ get: () => injection }); await flush(); assert.match(safe.byId("tbody").textContent, /<img/); assert.equal(safe.byId("tbody").children[0].children[0].children[0].children.length, 0);
console.log("View smoke: six tabs, unified filters, chart/keyboard/arithmetic, sorting, details/columns/pagination, prices, coverage, export, themes, reset safeguards and bounded-log regressions passed.");

// Feed an actual HTTP query response into the UI renderer when a synthetic fixture is supplied.
if (process.env.ASTRBOT_USAGE_FIXTURE) {
  const actual = JSON.parse(fs.readFileSync(process.env.ASTRBOT_USAGE_FIXTURE, "utf8"));
  const live = boot({ get: () => clone(actual) }); await flush(); live.tab("prices"); await flush();
  assert.equal(live.byId("price-compat").hidden, true);
  assert.equal(live.byId("prices-body").children.length, Math.min(50, actual.pricing.rows.length));
  const first = actual.pricing.rows[0], displayed = live.byId("prices-body").children[0];
  assert.match(displayed.children[0].textContent, new RegExp(first.model.replace(/[.*+?^${}()|[]\]/g, "\$&")));
  assert.equal(displayed.children[1].textContent, first.calls.toLocaleString("en-US"));
  assert.equal(live.byId("price-total").textContent, "$" + actual.pricing.total_cost.toLocaleString("en-US", { maximumFractionDigits: 6 }));
  assert.match(live.byId("status-text").textContent, /采集中/);
  assert.doesNotMatch(live.byId("price-status").textContent, /未保存/);
  console.log("Actual HTTP dashboard-to-UI pricing projection passed.");
}
const pausedSample=clone(sample); pausedSample.collection.plugin_enabled=false;
const paused=boot({get:()=>clone(pausedSample)}); await flush(); assert.match(paused.byId("status-text").textContent,/采集未启用/);
const waitingSample=clone(sample); waitingSample.collection.plugin_enabled=true; waitingSample.collection.patched_count=0;
const waiting=boot({get:()=>clone(waitingSample)}); await flush(); assert.match(waiting.byId("status-text").textContent,/等待采集就绪/);

// Collection settings display actual values and remain scoped to their own endpoint.
const settingsControls = boot(); await flush(); settingsControls.tab("manage");
assert.equal(settingsControls.byId("settings-enabled").checked, false);
assert.equal(settingsControls.byId("settings-detail-history").checked, true);
assert.equal(settingsControls.byId("settings-keep-days").value, "13");
assert.equal(settingsControls.byId("settings-max-events").value, "37");
assert.match(settingsControls.byId("settings-detail-hint").textContent, /启用用量采集/);
assert.equal(settingsControls.byId("runtime-diagnostics").hasAttribute("open"), false);
assert.match(settingsControls.byId("wrapped-methods").textContent, /LiveProvider.text_chat/);
assert.doesNotMatch(settingsControls.byId("coverage").textContent, /Provider.*补丁|包装方法数|等待写入|队列/);
assert.doesNotMatch(settingsControls.byId("status").getAttribute("title"), /Provider|补丁|包装|计数/);

function settingsType(app, id, value) { app.byId(id).value = String(value); fire(app.byId(id), "input"); }
function settingsToggle(app, id, value) { app.byId(id).checked = value; fire(app.byId(id), "change"); }
function settingsAck(server, body, applied = true, reload = false) {
  server.settings = { ...server.settings, ...clone(body.settings), revision: server.settings.revision + 1, reload_required: reload };
  return { saved: true, applied, reload_required: reload, settings: clone(server.settings) };
}

// Missing or unwritable configuration never falls back to a pretend in-memory save.
const noSettingsData = clone(sample); delete noSettingsData.settings;
const noSettingsUI = boot({ get: () => clone(noSettingsData) }); await flush();
assert.equal(noSettingsUI.byId("settings-save").disabled, true); assert.equal(noSettingsUI.byId("settings-keep-days").disabled, true);
assert.match(noSettingsUI.byId("settings-runtime").textContent, /后端.*更新/);
fire(noSettingsUI.byId("settings-save"), "click"); await flush(); assert.equal(noSettingsUI.posts.length, 0);
const lockedSettingsData = clone(sample); Object.assign(lockedSettingsData.settings, { enabled: true, keep_days: 0, detail_max_events: 1, writable: false });
const lockedSettingsUI = boot({ get: () => clone(lockedSettingsData) }); await flush();
assert.equal(lockedSettingsUI.byId("settings-enabled").checked, true); assert.equal(lockedSettingsUI.byId("settings-keep-days").value, "0");
assert.equal(lockedSettingsUI.byId("settings-max-events").value, "1"); assert.match(lockedSettingsUI.byId("settings-runtime").textContent, /没有配置持久化接口/);
fire(lockedSettingsUI.byId("settings-save"), "click"); await flush(); assert.equal(lockedSettingsUI.posts.length, 0);

// A save contains exactly the four editable fields and the draft's original revision.
const settingsServer = clone(sample);
const editableSettingsUI = boot({ get: () => clone(settingsServer), post: async (path, body) => {
  assert.equal(path, "settings"); return settingsAck(settingsServer, body);
} }); await flush();
settingsType(editableSettingsUI, "settings-keep-days", 17);
fire(editableSettingsUI.byId("settings-save"), "click"); await flush();
const settingsBody = editableSettingsUI.posts[0].body;
assert.deepEqual(Object.keys(settingsBody).sort(), ["revision", "settings"]);
assert.deepEqual(Object.keys(settingsBody.settings).sort(), ["detail_history", "detail_max_events", "enabled", "keep_days"]);
assert.deepEqual(settingsBody, { settings: { enabled: false, detail_history: true, keep_days: 17, detail_max_events: 37 }, revision: 7 });
assert.equal(editableSettingsUI.byId("settings-enabled").checked, false);
assert.match(editableSettingsUI.byId("settings-status").textContent, /已保存并生效/);
assert.equal(editableSettingsUI.byId("settings-save").disabled, true);

// Client range validation rejects blanks, negatives, fractions, and out-of-range values before POST.
const invalidSettingsUI = boot(); await flush();
for (const value of ["", "-1", "3651", "1.5", "1e1"]) {
  settingsType(invalidSettingsUI, "settings-keep-days", value); fire(invalidSettingsUI.byId("settings-save"), "click"); await flush();
  assert.equal(invalidSettingsUI.posts.length, 0); assert.match(invalidSettingsUI.byId("settings-status").textContent, /保留天数/);
}
settingsType(invalidSettingsUI, "settings-keep-days", 0);
for (const value of ["", "0", "200001", "1.5"]) {
  settingsType(invalidSettingsUI, "settings-max-events", value); fire(invalidSettingsUI.byId("settings-save"), "click"); await flush();
  assert.equal(invalidSettingsUI.posts.length, 0); assert.match(invalidSettingsUI.byId("settings-status").textContent, /明细数量上限/);
}

// Background loads preserve dirty drafts and do not silently rebase a conflicting revision.
const conflictSettingsData = clone(sample);
const conflictSettingsUI = boot({ get: () => clone(conflictSettingsData), post: async () => { throw new Error("revision 冲突，请读取服务器值"); } }); await flush();
settingsType(conflictSettingsUI, "settings-keep-days", 18);
Object.assign(conflictSettingsData.settings, { keep_days: 42, revision: 8 });
fire(conflictSettingsUI.byId("refresh"), "click"); await flush(); assert.equal(conflictSettingsUI.byId("settings-keep-days").value, "18");
fire(conflictSettingsUI.byId("settings-save"), "click"); await flush();
assert.equal(conflictSettingsUI.posts[0].body.revision, 7); assert.equal(conflictSettingsUI.byId("settings-keep-days").value, "18");
assert.match(conflictSettingsUI.byId("settings-status").textContent, /草稿已保留/);
Object.assign(conflictSettingsData.settings, { keep_days: 43, revision: 9 });
fire(conflictSettingsUI.byId("settings-revert"), "click"); await flush();
assert.equal(conflictSettingsUI.byId("settings-keep-days").value, "43"); assert.match(conflictSettingsUI.byId("settings-status").textContent, /已还原服务器设置/);
settingsType(conflictSettingsUI, "settings-keep-days", 44); fire(conflictSettingsUI.byId("settings-save"), "click"); await flush();
assert.equal(conflictSettingsUI.posts.at(-1).body.revision, 9);

// Confirmed persistence is distinct from successful live application.
const reloadSettingsData = clone(sample);
const reloadSettingsUI = boot({ get: () => clone(reloadSettingsData), post: async (_path, body) => settingsAck(reloadSettingsData, body, false, true) }); await flush();
settingsToggle(reloadSettingsUI, "settings-enabled", true); fire(reloadSettingsUI.byId("settings-save"), "click"); await flush();
assert.match(reloadSettingsUI.byId("settings-status").textContent, /已保存，需要重载插件生效/);
assert.doesNotMatch(reloadSettingsUI.byId("settings-status").textContent, /已保存并生效/);
for (const reply of [{}, { saved: false }, { saved: true, applied: true, reload_required: false }]) {
  const unconfirmed = boot({ post: async () => clone(reply) }); await flush(); settingsType(unconfirmed, "settings-keep-days", 22);
  fire(unconfirmed.byId("settings-save"), "click"); await flush();
  assert.equal(unconfirmed.byId("settings-keep-days").value, "22"); assert.match(unconfirmed.byId("settings-status").textContent, /未能确认保存/);
  assert.doesNotMatch(unconfirmed.byId("settings-status").textContent, /^已保存|已保存并生效/);
}

// An edit made during a save survives the receipt and rebases to that acknowledged revision.
const inFlightSettingsData = clone(sample), settingsResolvers = [];
const inFlightSettingsUI = boot({ get: () => clone(inFlightSettingsData), post: (_path, body) => new Promise(resolve => settingsResolvers.push({ resolve, body })) }); await flush();
settingsType(inFlightSettingsUI, "settings-keep-days", 30); fire(inFlightSettingsUI.byId("settings-save"), "click"); await flush();
assert.equal(inFlightSettingsUI.byId("settings-keep-days").disabled, false);
settingsType(inFlightSettingsUI, "settings-keep-days", 31); fire(inFlightSettingsUI.byId("refresh"), "click"); await flush();
assert.equal(inFlightSettingsUI.byId("settings-keep-days").value, "31");
settingsResolvers[0].resolve(settingsAck(inFlightSettingsData, settingsResolvers[0].body)); await flush();
assert.equal(inFlightSettingsUI.byId("settings-keep-days").value, "31"); assert.match(inFlightSettingsUI.byId("settings-status").textContent, /草稿仍有未保存/);
fire(inFlightSettingsUI.byId("settings-save"), "click"); await flush(); assert.equal(inFlightSettingsUI.posts[1].body.revision, 8);
settingsResolvers[1].resolve(settingsAck(inFlightSettingsData, settingsResolvers[1].body)); await flush();
assert.equal(inFlightSettingsUI.byId("settings-keep-days").value, "31"); assert.equal(inFlightSettingsUI.byId("settings-save").disabled, true);

// A directory sync's subsequent dashboard refresh cannot replace an edited settings draft.
const syncSettingsData = clone(sample), syncSettingsResolvers = [];
const syncSettingsUI = boot({ get: () => clone(syncSettingsData), post: () => new Promise(resolve => syncSettingsResolvers.push(resolve)) }); await flush();
syncSettingsUI.tab("prices"); fire(syncSettingsUI.byId("price-sync"), "click"); await flush(); syncSettingsUI.tab("manage");
settingsType(syncSettingsUI, "settings-keep-days", 25); Object.assign(syncSettingsData.settings, { keep_days: 99, revision: 8 });
syncSettingsResolvers[0]({ synced: true, rows: 85, source: "models.dev", updated_at: 1792022401 }); await flush();
assert.equal(syncSettingsUI.byId("settings-keep-days").value, "25");
const omittedMethodsData = clone(sample); delete omittedMethodsData.collection.wrapped_methods;
const omittedMethodsUI = boot({ get: () => clone(omittedMethodsData) }); await flush(); assert.match(omittedMethodsUI.byId("wrapped-methods").textContent, /未返回/);
console.log("Editable collection settings: actual values, range guards, persistence receipts, dirty/conflict/in-flight protection and closed runtime diagnostics passed.");

// A dashboard GET begun before a settings save must not restore the older revision afterward.
const staleSettingsData=clone(sample), staleSettingsQueue=[]; let staleSettingsReads=0;
const staleSettingsUI=boot({get:()=>{staleSettingsReads++; if(staleSettingsReads===2)return new Promise(resolve=>staleSettingsQueue.push(resolve)); return clone(staleSettingsData);},post:async(_path,body)=>settingsAck(staleSettingsData,body)}); await flush();
settingsType(staleSettingsUI,"settings-keep-days",28); fire(staleSettingsUI.byId("refresh"),"click"); await flush();
const staleSettingsResponse=clone(staleSettingsData); fire(staleSettingsUI.byId("settings-save"),"click"); await flush();
staleSettingsQueue[0](staleSettingsResponse); await flush(); assert.equal(staleSettingsUI.byId("settings-keep-days").value,"28");
settingsType(staleSettingsUI,"settings-keep-days",29); fire(staleSettingsUI.byId("settings-save"),"click"); await flush(); assert.equal(staleSettingsUI.posts.at(-1).body.revision,8);

// Revert is an explicit fresh read, and an edit made during that read remains a draft.
const revertingData=clone(sample), revertingQueue=[]; let revertingReads=0;
const revertingUI=boot({get:()=>{revertingReads++; if(revertingReads===2)return new Promise(resolve=>revertingQueue.push(resolve)); return clone(revertingData);}}); await flush();
settingsType(revertingUI,"settings-keep-days",20); fire(revertingUI.byId("settings-revert"),"click"); await flush();
settingsType(revertingUI,"settings-keep-days",21); Object.assign(revertingData.settings,{keep_days:77,revision:8}); revertingQueue[0](clone(revertingData)); await flush();
assert.equal(revertingUI.byId("settings-keep-days").value,"21"); assert.match(revertingUI.byId("settings-status").textContent,/草稿仍保留/);
fire(revertingUI.byId("settings-revert"),"click"); await flush(); assert.equal(revertingUI.byId("settings-keep-days").value,"77");

// Existing out-of-range integers are shown as-is so users can correct them, never replaced by defaults.
const legacyLimitsData=clone(sample); Object.assign(legacyLimitsData.settings,{keep_days:7300,detail_max_events:250000});
const legacyLimitsUI=boot({get:()=>clone(legacyLimitsData),post:async(_path,body)=>settingsAck(legacyLimitsData,body)}); await flush();
assert.equal(legacyLimitsUI.byId("settings-keep-days").value,"7300"); assert.equal(legacyLimitsUI.byId("settings-max-events").value,"250000");
assert.match(legacyLimitsUI.byId("settings-runtime").textContent,/超出编辑范围/); assert.equal(legacyLimitsUI.byId("settings-keep-days").disabled,false);
settingsType(legacyLimitsUI,"settings-keep-days",3650); settingsType(legacyLimitsUI,"settings-max-events",200000); fire(legacyLimitsUI.byId("settings-save"),"click"); await flush();
assert.equal(legacyLimitsUI.posts.length,1); assert.equal(legacyLimitsUI.posts[0].body.settings.keep_days,3650);
const malformedMethodsData=clone(sample); malformedMethodsData.collection.wrapped_methods=[null,42,{}];
const malformedMethodsUI=boot({get:()=>clone(malformedMethodsData)}); await flush(); assert.match(malformedMethodsUI.byId("wrapped-methods").textContent,/未返回有效/);
console.log("Settings stale-response, restore-edit, legacy-value and exact-method boundaries passed.");
