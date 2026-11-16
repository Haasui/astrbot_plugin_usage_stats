// Launch the real dashboard against synthetic, in-memory data only.
// Usage: node tools/preview.mjs [--port 8766]
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
const script = fileURLToPath(new URL("./preview_server.py", import.meta.url));
const child = spawn(process.env.PYTHON || "python", [script, ...process.argv.slice(2)], { stdio: "inherit", windowsHide: true });
child.on("error", (error) => { console.error("Cannot start Python preview:", error.message); process.exitCode = 1; });
child.on("exit", (code) => { process.exitCode = code ?? 1; });
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => child.kill(signal));
