/* 视图冒烟测试：用最小 DOM 桩在 Node 里真的执行一次 app.js。
 *
 *     node tests/view_smoke.mjs
 *
 * 这不是浏览器测试，也不验证样式。它能抓到的是：函数名写错、属性不存在、
 * 字段填错、id 与 DOM 对不上这类运行时错误 —— 这些在浏览器里往往也是静默失效。
 * 需要 Node（开发时用，进不了插件包）。
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// 三个视图文件就在本测试的上一级
const BASE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "views", "usage");
const html = fs.readFileSync(BASE + "/index.html", "utf8");
const src = fs.readFileSync(BASE + "/app.js", "utf8");
const IDS = [...html.matchAll(/id="([^"]+)"/g)].map((m) => m[1]);

function makeNode(tag) {
  const node = {
    tagName: tag,
    children: [],
    _text: "",
    _cls: "",
    style: {},
    dataset: {},
    hidden: false,
    disabled: false,
    type: "",
    _ls: {},
    get textContent() { return this._text; },
    set textContent(v) { this._text = String(v); this.children = []; },
    get className() { return this._cls; },
    set className(v) { this._cls = String(v); },
    appendChild(c) { this.children.push(c); return c; },
    append(...cs) { cs.forEach((c) => this.children.push(c)); },
    addEventListener(type, fn) { (this._ls[type] = this._ls[type] || []).push(fn); },
    setAttribute(k, v) { this[k] = v; },
    removeAttribute(k) { delete this[k]; },
    getAttribute(k) { return this[k] === undefined ? null : this[k]; },
    hasAttribute(k) { return this[k] !== undefined; },
    classList: { add() {}, remove() {}, toggle() {} },
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 10, height: 10 }),
    closest: () => null,
  };
  return node;
}

/* 触发节点上登记过的监听器。桩不做事件冒泡，够用。 */
function fire(node, type, ev) {
  (node._ls[type] || []).forEach((fn) => fn(ev || {}));
}

const registry = new Map();
function byId(id) {
  if (!registry.has(id)) registry.set(id, makeNode("div"));
  return registry.get(id);
}
for (const id of IDS) byId(id);

// 分段控件要有 4 个带 data-days 的子按钮，syncRange 才跑得动
const range = byId("range");
range.children = ["1", "7", "30", "0"].map((d) => {
  const b = makeNode("button");
  b.dataset.days = d;
  return b;
});

// 分组切换同理：两个带 data-group 的按钮，syncGroup 才跑得动
const groupSeg = byId("group");
groupSeg.children = ["plugin", "model"].map((g) => {
  const b = makeNode("button");
  b.dataset.group = g;
  return b;
});

const store = new Map();
const sample = {
  total_tokens: 259212, patched: 4, recorded: 35, range_days: 30, generated_at: "20:11:00",
  series: [
    { day: "2026-10-15", calls: 5, total: 40000 },
    { day: "2026-10-16", calls: 30, total: 219212 },
  ],
  series_by_plugin: {
    astrbot_plugin_angel_heart: [
      { day: "2026-10-15", calls: 2, total: 30000 },
      { day: "2026-10-16", calls: 27, total: 114038 },
    ],
    main_agent: [
      { day: "2026-10-15", calls: 2, total: 10000 },    // 必须和 series 的当天合计对得上，
      { day: "2026-10-16", calls: 3, total: 105174 },    // 否则堆叠柱上方会空一块
    ],
  },
  focus_series: [{ day: "2026-10-16", calls: 29, total: 144038 }],
  previous: { tokens: 200000, calls: 50, failed: 0, images: 0,
              input_other: 100000, input_cached: 100000, output: 0 },
  plugins: [
    { name: "astrbot_plugin_angel_heart", total: 144038, calls: 29, failed: 0, images: 0,
      cache_hit: "15%", input_other: 122432, input_cached: 21606, output: 240,
      cache_reported: 29,
      models: [{ name: "openai|R1", total: 144038, calls: 29, failed: 0, images: 0,
                 cache_hit: "15%", output: 240 }] },
    { name: "main_agent", total: 115174, calls: 40, failed: 1, images: 2,
      cache_hit: "80%", input_other: 23035, input_cached: 92139, output: 1000,
      cache_reported: 40, models: [] },
  ],
};

let lastParams = null;
let payload = sample;   // 换掉它就能模拟「这个范围没有记录」
let defer = null;       // 设成数组就进入挂起模式，用来造请求竞态
const posted = [];      // POST 出去的请求：「重置」按钮必须真的发请求

const win = {
  matchMedia: () => ({ matches: false, addEventListener() {} }),
  localStorage: {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
  },
  AstrBotPluginPage: {
    ready: async () => ({ isDark: true }),
    apiGet: (path, params) => {
      lastParams = params;
      if (defer) return new Promise((resolve) => { defer.push({ resolve, params }); });
      return Promise.resolve(payload);
    },
    apiPost: (path, body) => {
      posted.push({ path, body });
      return Promise.resolve({ baseline_plugins: 3, memory_calls: 7, saved: true });
    },
  },
};
const doc = {
  documentElement: makeNode("html"),
  getElementById: byId,
  createElement: makeNode,
  createElementNS: (_ns, tag) => makeNode(tag),
  createTextNode: (t) => ({ tagName: "#text", textContent: t }),
};

new Function("window", "document", "setInterval", src)(win, doc, () => 0);

/* ---- 首屏：数据还没回来，应该铺着骨架而不是空白 ---- */
const skeletonRows = byId("tbody").children.filter((r) => r.className === "skeleton-row").length;
const skeletonOk = skeletonRows === 4 && byId("table").hidden === false;
console.log((skeletonOk ? "  ok   " : "  FAIL ") + "首屏骨架行数 = " + skeletonRows + "，表格可见 = " + (byId("table").hidden === false));
let bad = skeletonOk ? 0 : 1;

await new Promise((r) => setTimeout(r, 30));

const checks = [
  ["kpi-total", "259,212"],
  ["kpi-calls", "69"],
  ["kpi-failed", "1"],
  ["kpi-in", "145,467"],     // 259212 - 113745
  ["kpi-cached", "113,745"],
  ["kpi-out", "1,240"],      // 240 + 1000
];
for (const [id, want] of checks) {
  const got = byId(id).textContent;
  const ok = got === want;
  if (!ok) bad++;
  console.log((ok ? "  ok   " : "  FAIL ") + id + " = " + JSON.stringify(got) + (ok ? "" : "  期望 " + JSON.stringify(want)));
}
/* ---- 分组控件：默认按插件，提示为空 ---- */
const groupHintOk = byId("group-hint").textContent === "";
const groupNoteOk = byId("table-note").textContent === "共 2 个插件";
console.log((groupHintOk && groupNoteOk ? "  ok   " : "  FAIL ")
  + "默认按插件分组（提示「" + byId("group-hint").textContent + "」，表注「" + byId("table-note").textContent + "」）");
if (!(groupHintOk && groupNoteOk)) bad++;

const rows = byId("tbody").children.length;
console.log((rows >= 3 ? "  ok   " : "  FAIL ") + "表格行数 = " + rows + "（期望 >= 3：两个插件 + 一个模型）");
if (rows < 3) bad++;
const legend = byId("legend");
const legendOk = legend.hidden === false && legend.children.length >= 2;
console.log((legendOk ? "  ok   " : "  FAIL ") + "图例 hidden=" + legend.hidden + " 条目=" + legend.children.length);
if (!legendOk) bad++;

/* ---- 输出列：接口给了 output，表格就得把它显示出来 ----
   0.2.3 的真 bug：buildGroups 归一化时手抄字段、漏了 output，表格那格一路是 0。
   KPI 读的是接口原样的对象，所以上面那组 KPI 断言全绿 —— 光看数字对不上，
   得直接查单元格。 */
function nameOf(row) {
  const first = row.children[0];
  if (row.className === "row-model") return first.children[0].textContent;
  return first.children[0].children[1].textContent;
}
function outCell(name) {
  const row = byId("tbody").children.filter((r) => nameOf(r) === name)[0];
  return row ? row.children[6].textContent : "(没有这一行)";
}
const outChecks = [
  ["astrbot_plugin_angel_heart", "240"],
  ["main_agent", "1,000"],
  ["openai|R1", "240"],        // 模型子行
];
for (const [name, want] of outChecks) {
  const got = outCell(name);
  const ok = got === want;
  if (!ok) bad++;
  console.log((ok ? "  ok   " : "  FAIL ") + "「" + name + "」的输出列 = " + JSON.stringify(got) + (ok ? "" : "  期望 " + JSON.stringify(want)));
}

/* 按模型分组：父行是模型、子行是插件，两边的输出同样要带过去 */
const modelGroupBtn = groupSeg.children[1];
fire(byId("group"), "click", { target: { closest: () => modelGroupBtn } });
const modelModeOut = [outCell("openai|R1"), outCell("astrbot_plugin_angel_heart")];
const modelModeOk = modelModeOut[0] === "240" && modelModeOut[1] === "240";
if (!modelModeOk) bad++;
console.log((modelModeOk ? "  ok   " : "  FAIL ") + "按模型分组的输出列 = " + JSON.stringify(modelModeOut) + "（父行/子行都应为 240）");
fire(byId("group"), "click", { target: { closest: () => groupSeg.children[0] } });   // 切回按插件，后面的断言按这个顺序写

/* ---- 图表的算术：高度按比例、堆叠段正好铺满 ---- */
const barCols = byId("chart").children[0].children;
const heights = barCols.map((c) => parseFloat(c.children[0].style.height));
const peak = Math.max(...sample.series.map((d) => d.total));
const peakIndex = sample.series.findIndex((d) => d.total === peak);
const heightOk = heights[peakIndex] === 100;
console.log((heightOk ? "  ok   " : "  FAIL ") + "最高的柱子 = " + heights[peakIndex] + "%（应为 100）");
if (!heightOk) bad++;
const otherIndex = peakIndex === 0 ? 1 : 0;
const wantHeight = Math.max(2, Math.round((sample.series[otherIndex].total / peak) * 100));
const propOk = heights[otherIndex] === wantHeight;
console.log((propOk ? "  ok   " : "  FAIL ") + "另一根按比例 = " + heights[otherIndex] + "%（应为 " + wantHeight + "%）");
if (!propOk) bad++;
const stackSums = barCols.map((c) =>
  c.children[0].children.reduce((sum, seg) => sum + parseFloat(seg.style.height), 0));
const stackOk = stackSums.every((sum) => Math.abs(sum - 100) < 0.01);
console.log((stackOk ? "  ok   " : "  FAIL ") + "每天堆叠段之和 = " + stackSums.map((s) => s.toFixed(2)).join(" / ") + "（应为 100）");
if (!stackOk) bad++;
const meterWidths = byId("tbody").children
  .filter((r) => r.className === "row-plugin")
  .map((r) => parseFloat(r.children[2].children[0].children[0].style.width));
const meterSum = meterWidths.reduce((a, b) => a + b, 0);
const meterOk = Math.abs(meterSum - 100) <= 1;
console.log((meterOk ? "  ok   " : "  FAIL ") + "插件占比条之和 = " + meterSum + "%（约 100）");
if (!meterOk) bad++;
let ceiling = 0;
let nestedOk = true;
byId("tbody").children.forEach((row) => {
  const width = parseFloat(row.children[2].children[0].children[0].style.width);
  if (row.className === "row-plugin") ceiling = width;
  else if (row.className === "row-model" && width > ceiling) nestedOk = false;
});
console.log((nestedOk ? "  ok   " : "  FAIL ") + "模型行的占比条不超过它的插件行");
if (!nestedOk) bad++;
/* ---- 环比文案 ---- */
function subText(id) {
  var node = byId(id);
  return node.children.map(function (c) { return c.textContent; }).join("");
}
const deltaChecks = [
  ["kpi-total-sub", "↑ 30% 较上一周期"],   // 259212 vs 200000
  ["kpi-calls-sub", "↑ 38% 较上一周期"],   // 69 vs 50
  ["kpi-failed-sub", "新增"],              // 1 vs 0：上一周期是 0，不给百分比
  ["kpi-cached-sub", "↓ 6pt 较上一周期"],   // 43.9% vs 50.0%
];
for (const [id, want] of deltaChecks) {
  const text = subText(id);
  const ok = text.indexOf(want) >= 0;
  if (!ok) bad++;
  console.log((ok ? "  ok   " : "  FAIL ") + id + " 含 " + JSON.stringify(want) + "  实际 " + JSON.stringify(text));
}

const status = byId("status-text").textContent;
const statusOk = /补丁 4 处/.test(status);
console.log((statusOk ? "  ok   " : "  FAIL ") + "状态 = " + JSON.stringify(status));
if (!statusOk) bad++;
console.log((doc.documentElement.theme === undefined ? "  ok   " : "  FAIL ") + "主题属性 = " + JSON.stringify(doc.documentElement["data-theme"]));
const themeOk = doc.documentElement["data-theme"] === "dark";   // 宿主说 isDark=true
if (!themeOk) bad++;
/* ---- 图表的 roving tabindex ---- */
function cols() { return byId("chart").children[0].children; }
function tabbables() { return cols().filter((c) => c.tabIndex === 0); }
let rovingOk = tabbables().length === 1 && cols()[0].tabIndex === 0;
console.log((rovingOk ? "  ok   " : "  FAIL ") + "整张图 Tab 停留点 = " + tabbables().length + "（应为 1）");
if (!rovingOk) bad++;
fire(byId("chart").children[0], "keydown", { target: cols()[0], key: "ArrowRight", preventDefault() {} });
rovingOk = cols()[0].tabIndex === -1 && cols()[1].tabIndex === 0;
console.log((rovingOk ? "  ok   " : "  FAIL ") + "按右方向键后停在第二根柱子");
if (!rovingOk) bad++;
fire(byId("chart").children[0], "keydown", { target: cols()[1], key: "End", preventDefault() {} });
// 样例只有 2 根柱子，「最后一根」就是 index 1，所以这里查第一根已交停留点
rovingOk = cols()[cols().length - 1].tabIndex === 0 && cols()[0].tabIndex === -1;
console.log((rovingOk ? "  ok   " : "  FAIL ") + "按 End 跳到最后一根");
if (!rovingOk) bad++;
fire(byId("chart").children[0], "keydown", { target: cols()[cols().length - 1], key: "ArrowRight", preventDefault() {} });
rovingOk = cols()[cols().length - 1].tabIndex === 0;
console.log((rovingOk ? "  ok   " : "  FAIL ") + "到末尾再按右键不会越界");
if (!rovingOk) bad++;

/* ---- 排序：点表头应该真的改变行序 ---- */
function firstName() {
  var row = byId("tbody").children[0];
  return row.children[0].children[0].children[1].textContent;
}
const before = firstName();
fire(byId("sort-calls"), "click");
const after = firstName();
const sortOk = before === "astrbot_plugin_angel_heart" && after === "main_agent";
console.log((sortOk ? "  ok   " : "  FAIL ") + '按调用排序：' + before + " → " + after);
if (!sortOk) bad++;
const aria = byId("th-calls")["aria-sort"];
const ariaOk = aria === "descending";
console.log((ariaOk ? "  ok   " : "  FAIL ") + "aria-sort = " + JSON.stringify(aria));
if (!ariaOk) bad++;
// 再点一次翻成升序
fire(byId("sort-calls"), "click");
const ascOk = firstName() === "astrbot_plugin_angel_heart" && byId("th-calls")["aria-sort"] === "ascending";
console.log((ascOk ? "  ok   " : "  FAIL ") + "再点一次翻成升序，首行 = " + firstName());
if (!ascOk) bad++;

/* ---- 收起后刷新，不该自己弹回来 ---- */
function modelRows() {
  return byId("tbody").children.filter((r) => r.className === "row-model");
}
const firstToggle = byId("tbody").children[0].children[0].children[0];   // tr > td > button.toggle
fire(firstToggle, "click");
let hiddenModels = modelRows().filter((r) => r.hidden === true).length;
const collapseOk = firstToggle.getAttribute("aria-expanded") === "false" && hiddenModels === 1;
console.log((collapseOk ? "  ok   " : "  FAIL ") + "点插件名收起：aria-expanded=false，隐藏 " + hiddenModels + " 行模型");
if (!collapseOk) bad++;
fire(byId("refresh"), "click");
await new Promise((r) => setTimeout(r, 10));
hiddenModels = modelRows().filter((r) => r.hidden === true).length;
const persisted = hiddenModels === 1 && byId("tbody").children[0].children[0].children[0].getAttribute("aria-expanded") === "false";
console.log((persisted ? "  ok   " : "  FAIL ") + "刷新后收起状态还在（隐藏 " + hiddenModels + " 行）");
if (!persisted) bad++;
fire(byId("tbody").children[0].children[0].children[0], "click");   // 展开回去，免得挡住后面的断言

/* ---- 模型级下钻：点模型行的「趋势」应把 plugin/model 发给服务端 ---- */
function modelRowButton() {
  for (const row of byId("tbody").children) {
    if (row.className && row.className.indexOf("row-model") >= 0) {
      return row.children[0].children[1];
    }
  }
  return null;
}
const drill = modelRowButton();
const hasDrill = Boolean(drill) && drill.className.indexOf("focus-btn") >= 0;
console.log((hasDrill ? "  ok   " : "  FAIL ") + "模型行有「趋势」按钮: " + (hasDrill ? "有" : "没有"));
if (!hasDrill) bad++;
if (hasDrill) {
  fire(drill, "click");
  await new Promise((r) => setTimeout(r, 10));
  const drillOk = lastParams && lastParams.plugin === "astrbot_plugin_angel_heart" && lastParams.model === "openai|R1";
  console.log((drillOk ? "  ok   " : "  FAIL ") + "下钻参数 = " + JSON.stringify(lastParams));
  if (!drillOk) bad++;
  const summary = byId("summary").textContent;
  const labelOk = summary.indexOf("astrbot_plugin_angel_heart · openai|R1") === 0;
  console.log((labelOk ? "  ok   " : "  FAIL ") + "摘要标出下钻目标 = " + JSON.stringify(summary));
  if (!labelOk) bad++;
}

/* ---- 下钻目标在这个范围里消失：不许静默降级 ---- */
const clearBtn = byId("chart-clear");
const clearShown = clearBtn.hidden === false;
console.log((clearShown ? "  ok   " : "  FAIL ") + "下钻时标题旁出现「取消下钻」");
if (!clearShown) bad++;

// 换成「插件还在、它那个模型在这个范围里没有记录」的数据
const noModel = Object.assign({}, sample, {
  plugins: sample.plugins.map((p) =>
    p.name === "astrbot_plugin_angel_heart" ? Object.assign({}, p, { models: [] }) : p),
});
payload = noModel;
fire(byId("refresh"), "click");
await new Promise((r) => setTimeout(r, 10));
const focusTitle = byId("chart-focus").textContent;
const keptFocus = focusTitle.indexOf("openai|R1") >= 0 && focusTitle.indexOf("没有记录") >= 0;
console.log((keptFocus ? "  ok   " : "  FAIL ") + "模型没记录时保留下钻并写明原因：" + JSON.stringify(focusTitle));
if (!keptFocus) bad++;

fire(clearBtn, "click");
await new Promise((r) => setTimeout(r, 10));
const clearedOk = !lastParams.plugin && !lastParams.model && byId("chart-clear").hidden === true;
console.log((clearedOk ? "  ok   " : "  FAIL ") + "标题旁的取消能清掉下钻，参数 = " + JSON.stringify(lastParams));
if (!clearedOk) bad++;
payload = sample;
fire(byId("refresh"), "click");
await new Promise((r) => setTimeout(r, 10));

/* ---- 切到空范围后点排序，不该把上一个范围的表复活 ---- */
const emptyPayload = { total_tokens: 0, patched: 4, recorded: 0, series: [], series_by_plugin: {}, plugins: [] };
payload = emptyPayload;
fire(byId("refresh"), "click");
await new Promise((r) => setTimeout(r, 10));
const emptyShown = byId("table").hidden === true && byId("empty").hidden === false;
console.log((emptyShown ? "  ok   " : "  FAIL ") + "空范围：表格收起、空态显示");
if (!emptyShown) bad++;
fire(byId("sort-calls"), "click");
const notRevived = byId("table").hidden === true && byId("tbody").children.length === 0;
console.log((notRevived ? "  ok   " : "  FAIL ") + "空范围下点表头没有复活旧表");
if (!notRevived) bad++;

/* ---- 请求竞态：先发的后到，不该盖掉后发的 ---- */
payload = sample;
const newer = Object.assign({}, sample, { total_tokens: 111111 });
defer = [];
fire(byId("refresh"), "click");
fire(byId("refresh"), "click");
await new Promise((r) => setTimeout(r, 5));
const pending = defer.slice();
defer = null;
pending[1].resolve(newer);          // 后发的先回
await new Promise((r) => setTimeout(r, 10));
pending[0].resolve(sample);         // 先发的后回（旧数据）
await new Promise((r) => setTimeout(r, 10));
const raceOk = byId("kpi-total").textContent === "111,111";
console.log((raceOk ? "  ok   " : "  FAIL ") + "竞态：旧响应没盖掉新响应，当前 = " + byId("kpi-total").textContent);
if (!raceOk) bad++;

/* ---- 起一个全新的实例（自己的 DOM 桩），用来单独验一件事 ---- */
function bootWith(apiGetImpl) {
  const reg = new Map();
  const get = (id) => {
    if (!reg.has(id)) reg.set(id, makeNode("div"));
    return reg.get(id);
  };
  for (const id of IDS) get(id);
  const range = get("range");
  range.children = ["1", "7", "30", "0"].map((d) => {
    const node = makeNode("button");
    node.dataset.days = d;
    return node;
  });
  const doc = {
    documentElement: makeNode("html"),
    getElementById: get,
    createElement: makeNode,
    createElementNS: (_ns, tag) => makeNode(tag),
    createTextNode: (value) => ({ tagName: "#text", textContent: value }),
  };
  const win = {
    matchMedia: () => ({ matches: false, addEventListener() {} }),
    localStorage: { getItem: () => null, setItem() {} },
    AstrBotPluginPage: {
      ready: async () => ({ isDark: false }),
      apiGet: apiGetImpl,
    },
  };
  new Function("window", "document", "setInterval", src)(win, doc, () => 0);
  return { byId: get };
}

/* ---- 首屏就失败：骨架要收掉，不能一直转 ---- */
const realTimeout = globalThis.setTimeout;
globalThis.setTimeout = (fn) => realTimeout(fn, 0);   // 把重试的 500ms 压掉，测试不必真等
const brokenRun = bootWith(() => Promise.reject(new Error("bridge down")));
await new Promise((r) => realTimeout(r, 60));
globalThis.setTimeout = realTimeout;
const chartText = brokenRun.byId("chart").children.map((c) => c.textContent).join("");
const skeletonGone = chartText.indexOf("取不到数据") >= 0 && brokenRun.byId("table").hidden === true;
console.log((skeletonGone ? "  ok   " : "  FAIL ") + "首屏失败：骨架收掉并写明原因（" + chartText.slice(0, 24) + "）");
if (!skeletonGone) bad++;

/* ---- 极小的值也要看得见：2% 地板 ---- */
const tiny = {
  total_tokens: 1000001, patched: 4, recorded: 2, range_days: 30, generated_at: "00:00",
  series: [
    { day: "2026-10-15", calls: 1, total: 1000000 },
    { day: "2026-10-16", calls: 1, total: 1 },        // 千分之一
  ],
  series_by_plugin: {
    big: [{ day: "2026-10-15", calls: 1, total: 1000000 }, { day: "2026-10-16", calls: 1, total: 1 }],
  },
  plugins: [
    { name: "big", total: 1000001, calls: 2, failed: 0, images: 0, cache_hit: "—",
      input_other: 0, input_cached: 0, models: [] },
  ],
};
const tinyRun = bootWith(() => Promise.resolve(tiny));
await new Promise((r) => setTimeout(r, 30));
const tinyHeights = tinyRun.byId("chart").children[0].children.map((c) => parseFloat(c.children[0].style.height));
const floorOk = tinyHeights[0] === 100 && tinyHeights[1] === 2;
console.log((floorOk ? "  ok   " : "  FAIL ") + "千分之一的那天仍有 2% 高度 = " + tinyHeights.join(" / ") + "（应为 100 / 2）");
if (!floorOk) bad++;

/* ---- 有命中数就不许显示「—」 ----
   流式响应扒不到 raw_completion，cache_reported 会一路记成 0。命中率以前只看
   这个标记，于是整列「—」，而旁边的「缓存」列明明有数字。 */
const hitsNoFlag = {
  total_tokens: 259212, patched: 4, recorded: 10, range_days: 30, generated_at: "00:00",
  series: [{ day: "2026-10-15", calls: 10, total: 259212 }],
  series_by_plugin: { p: [{ day: "2026-10-15", calls: 10, total: 259212 }] },
  plugins: [
    { name: "p", total: 259212, calls: 10, failed: 0, images: 0, cache_hit: "—",
      input_other: 122432, input_cached: 136780, output: 0, cache_reported: 0,
      models: [{ name: "m", total: 259212, calls: 10, failed: 0, images: 0,
                 cache_hit: "—", input_other: 122432, input_cached: 136780,
                 output: 0, cache_reported: 0 }] },
  ],
};
const hitsRun = bootWith(() => Promise.resolve(hitsNoFlag));
await new Promise((r) => setTimeout(r, 30));
const hitsSub = hitsRun.byId("kpi-cached-sub").children.map((c) => c.textContent).join("");
const hitsKpiOk = hitsSub.indexOf("未上报") < 0 && hitsSub.indexOf("命中 53%") >= 0;
if (!hitsKpiOk) bad++;
console.log((hitsKpiOk ? "  ok   " : "  FAIL ") + "有命中数时 KPI 算命中率 = " + JSON.stringify(hitsSub));
// 按模型分组时父行的命中率是前端用原始计数重算的 —— 这条路径以前直接给「—」
fire(hitsRun.byId("group"), "click", { target: { closest: () => ({ dataset: { group: "model" } }) } });
const hitsCell = hitsRun.byId("tbody").children[0].children[3].textContent;
const hitsCellOk = hitsCell === "53%";
if (!hitsCellOk) bad++;
console.log((hitsCellOk ? "  ok   " : "  FAIL ") + "按模型分组时的命中率 = " + JSON.stringify(hitsCell) + "（应为 53%）");

/* 反向：命中数 0 且没人上报 → 仍是「未上报」，别把这条老规矩改没了 */
const noHits = Object.assign({}, hitsNoFlag, {
  plugins: [{ name: "p", total: 122432, calls: 10, failed: 0, images: 0, cache_hit: "—",
              input_other: 122432, input_cached: 0, output: 0, cache_reported: 0, models: [] }],
});
const noHitsRun = bootWith(() => Promise.resolve(noHits));
await new Promise((r) => setTimeout(r, 30));
const noHitsSub = noHitsRun.byId("kpi-cached-sub").children.map((c) => c.textContent).join("");
const noHitsOk = noHitsSub.indexOf("未上报") >= 0;
if (!noHitsOk) bad++;
console.log((noHitsOk ? "  ok   " : "  FAIL ") + "没人上报缓存时仍是「未上报」= " + JSON.stringify(noHitsSub));

/* ---- 重置：两步确认，且第二次点击必须真的发 POST ----
   以前这里用 window.confirm，而插件页跑在 iframe 里，浏览器会直接拦掉模态框
   并返回 false —— 点了什么都不会发生。所以确认只能做在按钮自己身上。 */
const resetBtn = byId("reset-stats");
posted.length = 0;
fire(resetBtn, "click");
const armedOk = posted.length === 0 && resetBtn.textContent === "再点一次确认";
if (!armedOk) bad++;
console.log((armedOk ? "  ok   " : "  FAIL ")
  + "第一次点重置只进入待确认（POST 发了 " + posted.length + " 次，文字「" + resetBtn.textContent + "」）");
fire(resetBtn, "click");
await new Promise((r) => setTimeout(r, 20));
const postOk = posted.length === 1 && posted[0].path === "reset";
if (!postOk) bad++;
console.log((postOk ? "  ok   " : "  FAIL ") + "第二次点重置发出 " + JSON.stringify(posted.map((p) => p.path)));
const backOk = resetBtn.textContent === "重置" && !resetBtn.className.includes("armed");
if (!backOk) bad++;
console.log((backOk ? "  ok   " : "  FAIL ") + "重置后按钮复原为「" + resetBtn.textContent + "」");
// 重置之后要重新取数：此刻 state.data 还是旧的，不重取图表不会变
const reloadOk = lastParams !== null;
if (!reloadOk) bad++;
console.log((reloadOk ? "  ok   " : "  FAIL ") + "重置后重新取数（最后参数 " + JSON.stringify(lastParams) + "）");

console.log(bad === 0 ? "\n视图冒烟测试全部通过" : "\n有 " + bad + " 项没过");
process.exit(bad === 0 ? 0 : 1);