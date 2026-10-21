import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// 脚本住在 <插件>/tools/ 下，按自身位置定位插件目录，不依赖当前工作目录。
// 用法：在插件根目录跑 node tools/preview.mjs，产物在 <插件>/_preview/（不入库）。
// 不能用 new URL(...).pathname：它把空格编码成 %20，目录名带空格时直接找不到文件。
const HERE = path.dirname(fileURLToPath(import.meta.url));
const PLUGIN = path.resolve(HERE, "..");
const OUT = path.join(PLUGIN, "_preview");
fs.mkdirSync(OUT, { recursive: true });

// --- 造一份像样的样例：3 个插件、10 天，总量和冒烟测试里那组对得上 ---
const days = [];
for (let i = 9; i >= 0; i -= 1) {
  const d = new Date(Date.UTC(2026, 9, 16) - i * 86400000);
  days.push(d.toISOString().slice(0, 10));
}
const shape = [
  { name: "astrbot_plugin_angel_heart", weights: [0.5, 0.42, 0.6, 0.38, 0.55, 0.47, 0.62, 0.44, 0.51, 0.56] },
  { name: "main_agent", weights: [0.3, 0.4, 0.25, 0.44, 0.3, 0.36, 0.22, 0.4, 0.33, 0.28] },
  { name: "astrbot_plugin_image_gate", weights: [0.12, 0.1, 0.08, 0.14, 0.1, 0.12, 0.09, 0.1, 0.13, 0.11] },
];
const DAILY = 260000;
const series = days.map((day) => ({ day, calls: 0, total: 0 }));
const seriesByPlugin = {};
const plugins = shape.map(({ name, weights }) => ({
  name, total: 0, calls: 0, failed: 0, images: 0,
  cache_hit: "—", input_other: 0, input_cached: 0, output: 0, cache_reported: 0, models: [],
}));
shape.forEach((entry, si) => {
  seriesByPlugin[entry.name] = [];
  entry.weights.forEach((w, di) => {
    const total = Math.round(DAILY * w);
    const calls = 3 + ((si * 7 + di * 3) % 9);
    seriesByPlugin[entry.name].push({ day: days[di], calls, total });
    series[di].calls += calls;
    series[di].total += total;
    const p = plugins[si];
    p.total += total;
    p.calls += calls;
  });
});
// 模型：各插件给自己加一两个模型行
const modelNames = [
  ["openai/deepseek-ai/DeepSeek-R1-0528-Qwen3-8B", "openai/DeepSeek-V4-Flash-0731"],
  ["openai/DeepSeek-V4-Flash-0731"],
  ["openai/glm-4v-flash"],
];
plugins.forEach((p, i) => {
  const split = i === 1 ? [1] : [0.62, 0.38];
  modelNames[i].forEach((name, k) => {
    const frac = split[k] ?? 0.38;
    const total = Math.round(p.total * frac);
    const incoming = Math.round(total * 1.4);
    const cached = Math.round(incoming * (i === 1 ? 0.8 : 0.15));
    const modelCalls = Math.round(p.calls * frac);
    // 输出单独给一栏：以前样例里没有它，预览图上的「输出」永远是 0，
    // 恰好把「表格没搬 output」这个真 bug 盖住了
    const output = Math.max(1, Math.round(total * (i === 1 ? 0.06 : 0.035)));
    p.models.push({
      name, total, calls: modelCalls, failed: i === 2 ? 2 : 0,
      images: i === 2 ? 4 : 0,
      cache_hit: Math.round((cached / incoming) * 100) + "%",
      input_other: incoming - cached, input_cached: cached,
      output, cache_reported: modelCalls,
    });
  });
  const incoming = p.models.reduce((s, m) => s + m.input_other + m.input_cached, 0);
  const cached = p.models.reduce((s, m) => s + m.input_cached, 0);
  p.input_other = incoming - cached;
  p.input_cached = cached;
  p.output = p.models.reduce((s, m) => s + m.output, 0);
  p.cache_reported = p.models.reduce((s, m) => s + m.cache_reported, 0);
  p.cache_hit = incoming ? Math.round((cached / incoming) * 100) + "%" : "—";
  p.failed = i === 2 ? 2 : 0;
  p.images = i === 2 ? 4 : 0;
  p.models.sort((a, b) => b.total - a.total);
});
plugins.sort((a, b) => b.total - a.total);
const grand = plugins.reduce((s, p) => s + p.total, 0);

const SAMPLE = {
  total_tokens: grand,
  patched: 4,
  recorded: series.reduce((s, p) => s + p.calls, 0),
  range_days: 30,
  generated_at: "20:11:00",
  series,
  series_by_plugin: seriesByPlugin,
  focus_series: seriesByPlugin["main_agent"],
  previous: { tokens: Math.round(grand * 0.78), calls: 40, failed: 0, images: 0,
              input_other: 100000, input_cached: 60000, output: Math.round(grand * 0.012) },
  plugins,
};

// --- 真 index.html，只把 bridge SDK 那行换成桩 ---
const real = fs.readFileSync(path.join(PLUGIN, "views/usage/index.html"), "utf8");
const swapped = real
  .replace('<script src="/api/plugin/page/bridge-sdk.js"></script>', '<script src="./stub.js"></script>')
  .replace('href="./style.css"', 'href="../views/usage/style.css"')
  .replace('src="./app.js"', 'src="../views/usage/app.js"');
// 保真：预览页与真 index.html 只能差在下面这几行。差得更多就说明两者开始分家，
// 预览就不再代表真实视图了 —— 那种情况下给人看预览是误导。
const before = real.split("\n");
const after = swapped.split("\n");
if (before.length !== after.length) throw new Error("预览页的行数变了，替换不该增删行");
const changed = before
  .map((line, i) => (line === after[i] ? null : i + 1))
  .filter((x) => x !== null);
if (changed.length !== 3) throw new Error("预期只改 3 行，实际改了 " + changed.length + " 行：第 " + changed.join("/") + " 行");
console.log("与真 index.html 的差异：只改了第 " + changed.join(" 和第 ") + " 行");

const stub = "window.AstrBotPluginPage = {\n"
  + "  ready: async () => ({ isDark: new URLSearchParams(location.search).has('dark') }),\n"
  + "  apiGet: async () => ({\n"
  + JSON.stringify(SAMPLE, null, 2).split("\n").map((l) => "    " + l).join("\n")
  + "\n  }),\n};\n";
fs.writeFileSync(path.join(OUT, "stub.js"), stub, "utf8");
fs.writeFileSync(path.join(OUT, "index.html"), swapped, "utf8");
console.log("样例：", plugins.length, "个插件 /", days.length, "天 / 合计", grand.toLocaleString("en-US"));
console.log("预览写入", OUT);