"""AstrBot assembly tests using an explicit, minimal host stub."""
from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def host_modules():
    modules = {name: ModuleType(name) for name in (
        "astrbot", "astrbot.api", "astrbot.api.event", "astrbot.api.star", "astrbot.api.web",
    )}
    modules["astrbot"].__path__ = []
    modules["astrbot.api"].__path__ = []
    noop = lambda *args, **kwargs: None
    modules["astrbot.api"].AstrBotConfig = dict
    modules["astrbot.api"].logger = SimpleNamespace(info=noop, warning=noop, error=noop)
    decorator = lambda *args, **kwargs: (lambda function: function)
    modules["astrbot.api.event"].AstrMessageEvent = object
    modules["astrbot.api.event"].filter = SimpleNamespace(
        command=decorator, permission_type=decorator, PermissionType=SimpleNamespace(ADMIN="admin"),
    )
    class Star:
        def __init__(self, context):
            self.context = context
    modules["astrbot.api.star"].Star = Star
    modules["astrbot.api.star"].Context = object
    modules["astrbot.api.star"].register = decorator
    modules["astrbot.api.web"].json_response = lambda data: {"http_status": 200, "data": data}
    modules["astrbot.api.web"].error_response = lambda message, status_code=400: {
        "http_status": status_code, "error": message,
    }
    return modules


class FakeEvents:
    def __init__(self):
        self.rows = []
        self.fail_once = False
        self.cleared = False
        self.received_query = None
        self.prices = None
    def append(self, call):
        if self.fail_once and len(self.rows) == 1:
            self.fail_once = False
            raise OSError("injected write failure")
        if call.event_id not in [row.event_id for row in self.rows]:
            self.rows.append(call)
    def prune(self, **kwargs):
        pass
    def dashboard(self, query, aggregates, now=None, covered_ids=None):
        self.received_query = query
        return {"overview": {"calls": len(self.rows)}, "coverage": {}}
    def export(self, query, aggregates, now=None, covered_ids=None):
        self.received_query = query
        return {"csv": "id\n", "returned": 0, "total": 0, "truncated": False}
    def save_prices(self, rows):
        if any(row.get("input", 0) is not None and row.get("input", 0) < 0 for row in rows):
            raise ValueError("price must be nonnegative")
        self.prices = rows
    def clear(self):
        self.rows.clear()
        self.cleared = True
    def close(self):
        pass


class AssemblyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.modules = host_modules()
        cls.host_patch = patch.dict(sys.modules, cls.modules)
        cls.host_patch.start()
        cls.module = importlib.import_module("astrbot_plugin_usage_stats.main")
    @classmethod
    def tearDownClass(cls):
        sys.modules.pop("astrbot_plugin_usage_stats.main", None)
        cls.host_patch.stop()
    def setUp(self):
        self.plugin = self.module.UsageStatsPlugin(SimpleNamespace(), {"save_every": 0})
        self.plugin._events = FakeEvents()
        self.plugin._event_persistent = True
        self.web = self.modules["astrbot.api.web"]
        self.web.request = SimpleNamespace(username="admin", query={})
        self.provider = SimpleNamespace(provider_config={"id": "provider"}, get_model=lambda: "model")
        self.response = SimpleNamespace(role="assistant", usage=SimpleNamespace(
            input_other=10, input_cached=5, output=3,
        ))
    def record(self, meta=None):
        self.plugin._record(self.provider, "text_chat", self.response, {}, 200, meta)
    def test_memory_cap_never_discards_unsaved_calls_after_save_failure(self):
        self.plugin.config["memory_cap"] = 1
        self.record()
        self.record()
        self.assertEqual(len(self.plugin._calls), 2)
        self.assertGreater(self.plugin._save_failures, 0)
    def test_detail_history_remains_opt_in(self):
        self.record()
        self.assertEqual(len(self.plugin._calls), 1)
        self.assertEqual(self.plugin._event_queue, [])
    def test_detail_history_queues_stable_identity_and_timing(self):
        self.plugin.config["detail_history"] = True
        self.record({"result": "success", "ttft_ms": 50})
        call = self.plugin._event_queue[0]
        self.assertTrue(call.event_id)
        self.assertEqual(call.ttft_ms, 50)
        self.assertEqual(call.shape.ms, 200)
        self.assertTrue(self.plugin._flush_event_history())
        self.assertTrue(self.plugin._flush_event_history())
        self.assertEqual(len(self.plugin._events.rows), 1)
    def test_failed_flush_retries_only_remaining_records(self):
        self.plugin.config["detail_history"] = True
        self.record()
        self.record()
        self.plugin._events.fail_once = True
        self.assertFalse(self.plugin._flush_event_history())
        self.assertEqual(len(self.plugin._event_queue), 1)
        self.assertTrue(self.plugin._flush_event_history())
        self.assertEqual(len(self.plugin._events.rows), 2)
        self.assertEqual(self.plugin._event_error, "")
    def test_queue_limit_is_bounded_and_reported(self):
        self.plugin.config.update(detail_history=True, detail_max_events=1)
        self.record()
        self.record()
        self.assertEqual(len(self.plugin._event_queue), 1)
        self.assertEqual(self.plugin._event_dropped, 1)
    def test_canceled_call_is_not_labeled_success(self):
        self.plugin._record(self.provider, "text_chat_stream", None, {}, 20, {"result": "canceled"})
        self.assertEqual(self.plugin._calls[0].result, "canceled")
        self.assertFalse(self.plugin._calls[0].ok)
    def test_error_response_cannot_be_overridden_by_success_metadata(self):
        self.response.role = "err"
        self.record({"result": "success"})
        self.assertEqual(self.plugin._calls[0].result, "failed")
    def test_dashboard_requires_login(self):
        self.web.request.query = {"view": "dashboard"}
        self.web.request.username = None
        result = asyncio.run(self.plugin.api_stats())
        self.assertEqual(result["http_status"], 401)
    def test_dashboard_passes_shared_filters_and_collection_state(self):
        self.web.request.query = {"view": "dashboard", "plugin": "plugin", "model": "model", "result": "failed"}
        result = asyncio.run(self.plugin.api_stats())
        self.assertEqual(result["http_status"], 200)
        self.assertEqual(self.plugin._events.received_query, {"plugin": "plugin", "model": "model", "result": "failed"})
        self.assertFalse(result["data"]["collection"]["detail_enabled"])
    def test_export_uses_same_query_engine(self):
        self.web.request.query = {"view": "export", "model": "model"}
        result = asyncio.run(self.plugin.api_stats())
        self.assertEqual(result["data"]["returned"], 0)
        self.assertEqual(self.plugin._events.received_query, {"model": "model"})
    def test_invalid_dashboard_query_is_an_explicit_error(self):
        self.web.request.query = {"view": "dashboard"}
        self.plugin._events.dashboard = lambda *args: (_ for _ in ()).throw(ValueError("invalid range"))
        result = asyncio.run(self.plugin.api_stats())
        self.assertEqual(result["http_status"], 400)
    def price_request(self, payload, size=2):
        async def body():
            return b"x" * size
        async def json():
            return payload
        self.web.request.body = body
        self.web.request.json = json
        return asyncio.run(self.plugin.api_prices())
    def test_price_save_is_authenticated_validated_and_bounded(self):
        self.web.request.username = None
        self.assertEqual(self.price_request({"prices": []})["http_status"], 401)
        self.web.request.username = "admin"
        self.assertEqual(self.price_request({"prices": []}, 262145)["http_status"], 413)
        self.assertEqual(self.price_request({"prices": "bad"})["http_status"], 400)
        self.assertEqual(self.price_request({"prices": [{"input": -1}]})["http_status"], 400)
        self.assertEqual(self.price_request({"prices": []})["http_status"], 200)
    def test_price_save_never_claims_persistence_in_memory_fallback(self):
        self.plugin._event_persistent = False
        self.assertEqual(self.price_request({"prices": []})["http_status"], 503)
    def test_real_dashboard_preserves_normalized_total_with_cache_writes(self):
        from astrbot_plugin_usage_stats.event_store import EventStore
        self.plugin._events = EventStore()
        self.addCleanup(self.plugin._events.close)
        self.plugin.config["detail_history"] = True
        self.response.raw_completion = {"usage": {"cache_creation_input_tokens": 4}}
        self.record()
        self.web.request.query = {"view": "dashboard", "days": "0"}
        result = asyncio.run(self.plugin.api_stats())
        self.assertEqual(result["http_status"], 200)
        self.assertEqual(result["data"]["overview"]["tokens"], 18)
        self.assertEqual(result["data"]["overview"]["input_other"], 10)
        self.assertEqual(result["data"]["overview"]["calls"], 1)
    def test_raw_detail_without_normalized_usage_does_not_fabricate_zero_total(self):
        from astrbot_plugin_usage_stats.event_store import EventStore
        self.plugin._events = EventStore()
        self.addCleanup(self.plugin._events.close)
        self.plugin.config["detail_history"] = True
        self.response.usage = None
        self.response.raw_completion = {"usage": {"reasoning_tokens": 4}}
        self.record()
        self.web.request.query = {"view": "dashboard", "days": "0"}
        result = asyncio.run(self.plugin.api_stats())
        self.assertEqual(result["http_status"], 200)
        self.assertIsNone(result["data"]["overview"]["tokens"])
    def test_clear_removes_unsaved_details_without_resurrecting_them(self):
        from astrbot_plugin_usage_stats.event_store import EventStore
        self.plugin._events = EventStore()
        self.addCleanup(self.plugin._events.close)
        self.plugin.config["detail_history"] = True
        self.record()
        self.plugin._data_file = Path("unused-snapshot-fixture.json")
        with patch.object(self.module.store, "save", return_value=True):
            self.assertTrue(self.plugin._save())
        self.record()
        self.plugin._flush_event_history()
        async def clear():
            event = SimpleNamespace(plain_result=lambda value: value)
            return [item async for item in self.plugin.usage_clear(event)]
        messages = asyncio.run(clear())
        self.assertTrue(messages)
        self.assertEqual(self.plugin._calls, [])
        self.web.request.query = {"view": "dashboard", "days": "0"}
        result = asyncio.run(self.plugin.api_stats())
        self.assertEqual(result["data"]["overview"]["calls"], 1)
        self.assertEqual(result["data"]["events"]["total"], 1)
    def test_failed_daily_save_recovers_sqlite_details_without_swallowing_legacy(self):
        from astrbot_plugin_usage_stats.event_store import EventStore
        engine = EventStore()
        self.addCleanup(engine.close)
        self.plugin._events = engine
        self.plugin._data_file = Path("unused-crash-fixture.json")
        self.plugin.config["detail_history"] = True
        old = self.module.Call("main_agent", "provider", "model", self.module.time.time() - 10, self.module.Usage(10, 0, 0, 1, usage_reported=1))
        original = self.module.store.snapshot(self.module.fold_by_day_model([old]))
        self.plugin._baseline = original
        self.record()
        with patch.object(self.module.store, "save", return_value=False):
            self.assertFalse(self.plugin._save())
        reopened = self.module.UsageStatsPlugin(SimpleNamespace(), {"save_every": 0, "detail_history": True})
        reopened._baseline = original
        reopened._data_file = Path("unused-crash-fixture.json")
        with patch("astrbot_plugin_usage_stats.event_store.EventStore", return_value=engine), patch.object(Path, "mkdir"):
            reopened._open_event_store()
        self.assertEqual(len(reopened._calls), 1)
        self.assertEqual(reopened._run_calls, 0)
        self.web.request.query = {"view": "dashboard", "days": "0"}
        result = asyncio.run(reopened.api_stats())
        self.assertEqual(result["data"]["overview"]["calls"], 2)
        self.assertEqual(result["data"]["overview"]["tokens"], 28)
        with patch.object(self.module.store, "save", return_value=True) as saved:
            self.assertTrue(reopened._save())
        published = saved.call_args.kwargs["event_checkpoint"]
        self.assertEqual(engine.covered_ids(published), {self.plugin._calls[0].event_id})
        result = asyncio.run(reopened.api_stats())
        self.assertEqual(result["data"]["overview"]["calls"], 2)
        self.assertEqual(result["data"]["overview"]["tokens"], 28)
    def test_detail_retry_after_daily_commit_is_already_covered(self):
        from astrbot_plugin_usage_stats.event_store import EventStore
        engine = EventStore()
        self.addCleanup(engine.close)
        self.plugin._events = engine
        self.plugin._data_file = Path("unused-retry-fixture.json")
        self.plugin.config["detail_history"] = True
        self.record()
        with patch.object(engine, "append", side_effect=OSError("injected detail write failure")), patch.object(self.module.store, "save", return_value=True):
            self.assertTrue(self.plugin._save())
        self.assertEqual(len(self.plugin._event_queue), 1)
        self.web.request.query = {"view": "dashboard", "days": "0"}
        result = asyncio.run(self.plugin.api_stats())
        self.assertEqual(result["data"]["overview"]["calls"], 1)
        self.assertEqual(result["data"]["overview"]["tokens"], 18)
        self.assertEqual(self.plugin._event_queue, [])
    def sync_request(self, payload, size=2):
        async def body():
            return b"x" * size
        async def json():
            return payload
        self.web.request.body = body
        self.web.request.json = json
        return asyncio.run(self.plugin.api_price_sync())
    def test_public_catalog_sync_is_authenticated_and_fixed_source_only(self):
        self.web.request.username = None
        self.assertEqual(self.sync_request({"source": "models.dev"})["http_status"], 401)
        self.web.request.username = "admin"
        self.assertEqual(self.sync_request({"source": "other"})["http_status"], 400)
        self.assertEqual(self.sync_request({"url": "http://localhost"})["http_status"], 400)
        self.assertEqual(self.sync_request({}, 4097)["http_status"], 413)
    def test_public_catalog_sync_updates_real_costs_without_replacing_manual_rates(self):
        from astrbot_plugin_usage_stats.event_store import EventStore
        self.plugin._events = EventStore()
        self.addCleanup(self.plugin._events.close)
        manual = [{"model": "model", "input": 0, "cache_read": 0, "cache_write": 0, "output": 0}]
        self.plugin._events.save_prices(manual)
        public = [{"model": "model", "provider": "", "source_provider": "openai", "input": 2, "output": 8, "cache_read": 1, "cache_write": None}]
        with patch("astrbot_plugin_usage_stats.price_catalog.fetch_catalog", return_value=public):
            result = self.sync_request({"source": "models.dev"})
        self.assertEqual(result["http_status"], 200)
        self.assertEqual(result["data"]["rows"], 1)
        self.record()
        self.web.request.query = {"view": "dashboard", "days": "0"}
        response = asyncio.run(self.plugin.api_stats())["data"]
        self.assertEqual(response["overview"]["estimated_cost"], 0)
        self.assertEqual(response["pricing"]["rows"][0]["price_source"], "manual")
        self.assertEqual(len(response["prices"]), 1)
    def test_catalog_sync_failure_retains_last_known_catalog(self):
        from astrbot_plugin_usage_stats.event_store import EventStore
        self.plugin._events = EventStore()
        self.addCleanup(self.plugin._events.close)
        public = [{"model": "model", "provider": "", "source_provider": "openai", "input": 2, "output": 8}]
        self.plugin._events.save_catalog(public, updated_at=100)
        with patch("astrbot_plugin_usage_stats.price_catalog.fetch_catalog", side_effect=OSError("network unavailable")):
            result = self.sync_request({"source": "models.dev"})
        self.assertEqual(result["http_status"], 502)
        self.web.request.query = {"view": "dashboard", "days": "0"}
        response = asyncio.run(self.plugin.api_stats())["data"]
        self.assertEqual(response["pricing"]["updated_at"], 100)
        self.assertTrue(response["pricing"]["sync_error"])
    def test_previous_period_has_same_natural_day_count(self):
        from datetime import datetime, timedelta, timezone
        now = datetime(2026, 11, 11, 4, tzinfo=timezone.utc)
        days = {}
        for index in range(14):
            day = (now - timedelta(days=index)).date().isoformat()
            days[day] = {"provider|model": {"calls": 1, "failed": 0, "input_other": 1}}
        self.plugin._baseline = {"plugin": days}
        self.web.request.query = {"days": "7"}
        with patch.object(self.module.time, "time", return_value=now.timestamp()):
            result = asyncio.run(self.plugin.api_stats())
        self.assertEqual(result["data"]["previous"]["calls"], 7)
        self.assertEqual(sum(row["calls"] for row in result["data"]["plugins"]), 7)
    def test_reset_clears_details_and_queue_but_not_price_table(self):
        self.plugin.config["detail_history"] = True
        self.record()
        self.plugin._events.prices = [{"model": "model", "input": 1}]
        result = self.plugin._reset_all()
        self.assertTrue(result["details_cleared"])
        self.assertEqual(self.plugin._event_queue, [])
        self.assertTrue(self.plugin._events.cleared)
        self.assertEqual(len(self.plugin._events.prices), 1)


if __name__ == "__main__":
    unittest.main()
