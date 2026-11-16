/* Pure dashboard contract and arithmetic checks; node tests/dashboard_smoke.mjs. */
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
const source = fs.readFileSync(new URL("../views/usage/dashboard.js", import.meta.url), "utf8");
const context = vm.createContext({});
vm.runInContext(source, context);
const H = context.UsageDashboard;
assert.equal(H.fmt(null), "—"); assert.equal(H.fmt(undefined), "—"); assert.equal(H.fmt(NaN), "—");
assert.equal(H.fmt(0), "0"); assert.equal(H.percent(null), "—"); assert.equal(H.percent(0), "0%");
assert.equal(H.money(null), "—"); assert.equal(H.money(0), "$0"); assert.equal(H.money(0.000001), "$0.000001");
for (const value of ["", "  ", null, undefined]) assert.equal(H.price(value), null);
for (const value of ["0", "0.25", "1e2", ".5"]) assert.equal(H.price(value), Number(value));
for (const value of ["-1", "Infinity", "NaN", "1oops", "0x10", "1e999"]) assert.throws(() => H.price(value));
const query = H.query({ days: 0, model: "raw|model", page_size: 999, page: 2, result: "canceled" });
assert.equal(query.view, "dashboard"); assert.equal(query.days, 0); assert.equal(query.page_size, 200);
assert.equal(query.speed_mode, "total"); assert.equal(H.query({ speed_mode: "ttft" }).speed_mode, "ttft");
assert.equal(query.model, "raw|model"); assert.equal(query.result, "canceled"); assert.equal(query.page, 2);
for (const days of [0, 1, 7, 30]) assert.equal(H.query({ days }).days, days);
const custom = H.query({ custom: true, start: "2026-10-15T01:02", end: "2026-10-15T02:03" }, "export");
assert.equal(custom.view, "export"); assert.equal(custom.days, 0); assert.equal(custom.start, new Date("2026-10-15T01:02").toISOString()); assert.match(custom.end, /Z$/);
for (const state of [{ custom: true }, { custom: true, start: "bad", end: "bad" }, { custom: true, start: "2026-10-15", end: "2026-10-14" }, { custom: true, start: "2026-10-15", end: "2026-10-15" }]) assert.throws(() => H.query(state));
const timeline = [
  { label: "big", calls: 2, tokens: 1000000, models: [{ key: "__proto__", label: "safe", tokens: 700000 }, { key: "b", tokens: 300000 }], plugins: [{ key: "p", tokens: 1000000 }] },
  { label: "tiny", calls: 1, tokens: 1, models: [{ key: "__proto__", tokens: 1 }], plugins: [{ key: "q", tokens: 1 }] },
  { label: "zero", calls: 0, tokens: 0, models: [] }
];
const stack = H.stacks(timeline, "model", new Set());
assert.equal(stack.max, 1000000); assert.equal(stack.points[1].total / stack.max, 0.000001); assert.equal(stack.points[2].segments.length, 0);
for (const point of stack.points) {
  assert.equal(point.segments.reduce((sum, s) => sum + s.value, 0), point.total);
  let offset = 0; for (const segment of point.segments) { assert.equal(segment.offset, offset); offset += segment.value; }
}
assert.equal(stack.series[0].key, "__proto__"); assert.equal(stack.series[0].label, "__proto__");
const hidden = H.stacks(timeline, "model", new Set(["__proto__"])); assert.equal(hidden.points[0].total, 300000); assert.equal(hidden.points[1].total, 0);
assert.equal(H.stacks(timeline, "plugin").series[0].key, "p");
const unattributed = H.stacks([{ tokens: 10, plugins: [{ key: "p", tokens: 4 }] }], "plugin"); assert.equal(unattributed.points[0].total, 10);
assert.equal(H.stacks([], "model").max, 0);
const many = [{ tokens: 36, calls: 8, models: Array.from({ length: 8 }, (_, i) => ({ key: "model-" + i, tokens: i + 1 })) }];
const top = H.stacks(many, "model"); assert.equal(top.series.length, 7); assert.equal(top.points[0].total, 36);
assert.match(top.series[6].label, /其他/); assert.equal(top.series[6].total, 3);
assert.equal(H.stacks(many, "model", new Set([top.series[6].key])).points[0].total, 33);
const rows = [{ key: "a", tokens: 5, calls: 1 }, { key: "b", tokens: 2, calls: 8 }, { key: "c", tokens: null, calls: null }];
assert.equal(H.sorted(rows, "calls", "desc")[0].key, "b"); assert.equal(H.sorted(rows, "calls", "asc")[0].key, "a"); assert.equal(rows[0].key, "a");
const components = H.components({ input_other: 2, input_cached: 3, output: 5, reasoning: 4, cache_write: 1 });
assert.equal(components.reduce((sum, p) => sum + p.value, 0), 10); assert.equal(components.length, 3);

assert.equal(H.quote(null), "—"); assert.equal(H.quote(0), "$0.0000");
assert.notEqual(H.quote(.00000001), "$0.0000"); assert.notEqual(H.money(.000000001), "$0");
assert.equal(H.manualPrice([{model:"m",provider:"",input:1},{model:"m",provider:"p",input:2}],"m","p").input,2);
const fallback = H.pricing({analysis:{models:[{key:"used",calls:3,tokens:50}]},prices:[],overview:{estimated_cost:null,priced_calls:0,unpriced_calls:3}});
assert.equal(fallback.supported,false); assert.equal(fallback.rows[0].model,"used"); assert.equal(fallback.rows[0].estimated_cost,null);
const sourcePricing = H.pricing({pricing:{total_cost:0,priced_calls:3,unpriced_calls:0,available_prices:120,updated_at:123,rows:[{model:"catalog",calls:0,tokens:0,estimated_cost:0,price:{input:0}}]}});
assert.equal(sourcePricing.supported,true); assert.equal(sourcePricing.total_cost,0); assert.equal(sourcePricing.updated_at,123);
const page = H.pricePage(Array.from({length:123},(_,i)=>({model:"m"+i})),"",2,50);
assert.equal(page.rows.length,50); assert.equal(page.rows[0].model,"m50"); assert.equal(page.total,123);
assert.equal(H.pricePage([{model:"ABC",provider:"p"},{model:"other"}],"abc",8,50).rows[0].model,"ABC");
assert.equal(H.pricePage([],"",1,50).start,0);
console.log("Dashboard helper smoke: query/timezones, zero/null, price validation, true-scale stacks, safe keys, sorting and non-overlapping donut passed.");
