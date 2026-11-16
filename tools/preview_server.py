"""Serve the real usage view with synthetic, in-memory analytics only.

Run: python tools/preview_server.py --port 8766
This is a development preview, not an AstrBot server or a user-data migration.
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
from astrbot_plugin_usage_stats.event_store import EventStore
from astrbot_plugin_usage_stats.price_catalog import fetch_catalog
from astrbot_plugin_usage_stats.stats import Bucket, Call, Shape, Usage, day_key, fold_by_day_model

LOCK = threading.RLock()
EVENTS = EventStore()
CALLS = []
NOW = time.time()
for index in range(240):
    stamp = NOW - 120 - index * 3600
    cached = 2500 + index * 31
    item = Call(
        plugin=("main_agent", "image_assistant", "daily_digest")[index % 3],
        provider_id=("openai", "compatible")[index % 2],
        model=("gpt-4o-mini", "claude-sonnet-5", "deepseek-chat")[index % 3],
        timestamp=stamp, usage=Usage(600 + index * 7, cached, 90 + index % 70, 1,
                                  reasoning=20 if index % 2 else None, cache_write=None, usage_reported=1),
        ok=index % 31 != 0, shape=Shape(ms=1200 + index * 10),
        result="failed" if index % 31 == 0 else "canceled" if index % 53 == 0 else "success",
        ttft_ms=150 + index,
    )
    CALLS.append(item)
    EVENTS.append(item)
EVENTS.save_prices([])

BRIDGE = r'''window.AstrBotPluginPage = {
 ready: async () => ({isDark:new URLSearchParams(location.search).has("dark")}),
 apiGet: async (name, params) => {
  const response=await fetch("/_api/"+name+"?"+new URLSearchParams(params||{}));
  const data=await response.json(); if(!response.ok) throw new Error(data.error||"Preview request failed"); return data;
 },
 apiPost: async (name, body) => {
  const response=await fetch("/_api/"+name,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body||{})});
  const data=await response.json(); if(!response.ok) throw new Error(data.error||"Preview request failed"); return data;
 }
};'''


class Handler(BaseHTTPRequestHandler):
    def send(self, body, content_type="application/json; charset=utf-8", status=200):
        if not isinstance(body, (str, bytes)):
            body = json.dumps(body, ensure_ascii=False, allow_nan=False)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/_preview_info":
            return self.send({"synthetic_only": True, "name": "usage-statistics-preview"})
        if url.path == "/api/plugin/page/bridge-sdk.js":
            return self.send(BRIDGE, "text/javascript; charset=utf-8")
        if url.path == "/_api/stats":
            query = {key: values[-1] for key, values in parse_qs(url.query).items()}
            if query.get("tail"):
                return self.send({"enabled": False, "state": "disabled", "lines": [], "returned": 0, "truncated": False, "error_code": ""})
            try:
                with LOCK:
                    aggregates = fold_by_day_model(CALLS)
                    old_day = day_key(NOW - 15 * 86400)
                    if CALLS:
                        aggregates.setdefault("legacy_plugin", {})[old_day] = {"compatible|gpt-4o-mini": Bucket(calls=12, usage=Usage(18000, 40000, 800, 12, usage_reported=12))}
                    if query.get("view") == "export":
                        result = EVENTS.export(query, aggregates)
                    else:
                        result = EVENTS.dashboard(query, aggregates)
                        result["settings"] = {"enabled": True, "detail_history": True, "keep_days": 60, "detail_max_events": 20000, "writable": False, "revision": 0, "reload_required": False, "runtime_error": "开发预览没有AstrBot配置句柄，不会写入真实配置。"}
                        result["collection"] = {"plugin_enabled": True, "wrapped_methods": ["PreviewBase.text_chat", "PreviewBase.text_chat_stream", "PreviewProvider.text_chat", "PreviewProvider.text_chat_stream"], "patched_count": 4, "recorded_count": len(CALLS), "detail_enabled": True, "detail_available": True, "detail_error": "", "pending": 0, "dropped": 0}
                return self.send(result)
            except (ValueError, TypeError) as exc:
                return self.send({"error": str(exc)}, status=400)
        asset = "index.html" if url.path == "/" else url.path.lstrip("/")
        if asset not in ("index.html", "app.js", "dashboard.js", "style.css"):
            return self.send({"error": "Not found"}, status=404)
        types = {"html": "text/html", "js": "text/javascript", "css": "text/css"}
        return self.send((ROOT / "views" / "usage" / asset).read_bytes(), types[asset.rsplit(".", 1)[1]] + "; charset=utf-8")
    def do_POST(self):
        path = urlsplit(self.path).path
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 262144:
                return self.send({"error": "Body exceeds limit"}, status=413)
            payload = json.loads(self.rfile.read(length) or b"{}")
            if path == "/_api/price_sync":
                if not isinstance(payload, dict) or set(payload) - {"source"} or payload.get("source", "models.dev") != "models.dev":
                    return self.send({"error": "Only models.dev is supported"}, status=400)
                try:
                    rows = fetch_catalog()
                    stamp = time.time()
                    with LOCK:
                        EVENTS.save_catalog(rows, updated_at=stamp)
                    return self.send({"synced": True, "source": "models.dev", "rows": len(rows), "updated_at": stamp})
                except Exception as exc:
                    return self.send({"error": str(exc)}, status=502)
            with LOCK:
                if path == "/_api/prices":
                    EVENTS.save_prices(payload["prices"])
                elif path == "/_api/reset":
                    CALLS.clear()
                    EVENTS.clear()
                else:
                    return self.send({"error": "Not found"}, status=404)
            return self.send({"saved": True, "note": "仅清理本预览内存样本"})
        except (ValueError, KeyError, TypeError) as exc:
            return self.send({"error": str(exc)}, status=400)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Synthetic-only preview: http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        EVENTS.close()
