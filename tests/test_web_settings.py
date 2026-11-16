"""Web settings use the host configuration saver, not a detached dict copy."""
from __future__ import annotations
import asyncio
import copy
import importlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_integration import host_modules, FakeEvents

class HostConfig(dict):
    def __init__(self, values=None, path=None):
        super().__init__(values or {})
        self.path = path
        self.saved = copy.deepcopy(dict(self))
        self.calls = 0
        self.fail = False
    def save_config(self, replace_config=None):
        self.update(copy.deepcopy(replace_config or {}))
        self.calls += 1
        if self.fail:
            raise OSError("injected host config failure")
        if self.path is not None:
            self.path.write_text(json.dumps(self), encoding="utf-8")
        self.saved = copy.deepcopy(dict(self))

class AsyncHostConfig(HostConfig):
    commit = True
    async def save_config_async(self, replace_config=None):
        if not self.commit:
            self.update(copy.deepcopy(replace_config or {}))
            return False
        self.save_config(replace_config=replace_config)
        return True

class WebSettingsTests(unittest.TestCase):
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
        self.host = HostConfig({"enabled": False, "detail_history": False, "keep_days": 60, "detail_max_events": 20000, "refresh_interval": 0, "other": "preserved"})
        self.make_plugin(self.host)
    def make_plugin(self, host):
        self.plugin = self.module.UsageStatsPlugin(SimpleNamespace(), host)
        self.plugin._events = FakeEvents()
        self.plugin._event_persistent = True
        self.web = self.modules["astrbot.api.web"]
        self.web.request = SimpleNamespace(username="admin", query={"view": "dashboard"})
    def values(self, **kwargs):
        values = {key: self.plugin._settings_snapshot()[key] for key in ("enabled", "detail_history", "keep_days", "detail_max_events")}
        values.update(kwargs)
        return values
    def request(self, values=None, revision=None, size=2):
        payload = {"settings": values if values is not None else self.values()}
        if revision is not None:
            payload["revision"] = revision
        async def body(): return b"x" * size
        async def json_body(): return payload
        self.web.request.body = body
        self.web.request.json = json_body
        return asyncio.run(self.plugin.api_settings())
    def test_retains_real_host_handle_and_get_returns_actual_values(self):
        self.assertIs(self.plugin._config_handle, self.host)
        self.host["advanced"] = {"keep_days": 90}
        self.make_plugin(self.host)
        data = asyncio.run(self.plugin.api_stats())["data"]
        self.assertTrue(data["settings"]["writable"])
        self.assertEqual(data["settings"]["keep_days"], 90)
    def test_synchronous_host_save_persists_and_survives_plugin_recreation(self):
        values = self.values(detail_history=True, keep_days=30, detail_max_events=123)
        result = self.request(values)
        self.assertEqual(result["http_status"], 200)
        self.assertTrue(result["data"]["saved"])
        self.assertTrue(result["data"]["applied"])
        self.assertEqual(self.host.saved["detail_max_events"], 123)
        self.assertEqual(self.host.saved["other"], "preserved")
        self.assertEqual(self.host.calls, 1)
        self.make_plugin(HostConfig(self.host.saved))
        self.assertEqual(self.plugin._settings_snapshot()["keep_days"], 30)
        self.assertTrue(self.plugin._settings_snapshot()["detail_history"])
    def test_async_host_save_is_awaited(self):
        host = AsyncHostConfig(dict(self.host))
        self.make_plugin(host)
        result = self.request(self.values(keep_days=7))
        self.assertTrue(result["data"]["saved"])
        self.assertEqual(host.saved["keep_days"], 7)
    def test_failed_save_restores_host_and_keeps_runtime_unchanged(self):
        self.host["advanced"] = {"keep_days": 90, "unrelated": "kept"}
        self.make_plugin(self.host)
        previous = copy.deepcopy(dict(self.host))
        initial_revision = self.plugin._settings_revision
        self.host.fail = True
        result = self.request(self.values(keep_days=3, detail_history=True))
        self.assertEqual(result["http_status"], 503)
        self.assertEqual(dict(self.host), previous)
        self.assertEqual(self.plugin._settings_snapshot()["keep_days"], 90)
        self.assertFalse(self.plugin._settings_snapshot()["detail_history"])
        self.assertEqual(self.plugin._settings_revision, initial_revision)
    def test_async_uncommitted_snapshot_is_not_reported_saved(self):
        host = AsyncHostConfig(dict(self.host))
        host.commit = False
        self.make_plugin(host)
        result = self.request(self.values(keep_days=10))
        self.assertEqual(result["http_status"], 503)
        self.assertEqual(host["keep_days"], 60)
        self.assertEqual(self.plugin._settings_snapshot()["keep_days"], 60)
    def test_async_saver_without_commit_confirmation_is_rejected(self):
        async def unconfirmed(replace_config=None):
            self.host.update(copy.deepcopy(replace_config or {}))
            return None
        self.host.save_config_async = unconfirmed
        result = self.request(self.values(keep_days=4))
        self.assertEqual(result["http_status"], 503)
        self.assertEqual(self.host["keep_days"], 60)
        self.assertEqual(self.plugin._settings_snapshot()["keep_days"], 60)
    def test_advanced_override_is_updated_without_losing_other_fields(self):
        self.host["advanced"] = {"keep_days": 90, "other_advanced": {"a": 1}}
        self.make_plugin(self.host)
        result = self.request(self.values(keep_days=14))
        self.assertEqual(result["http_status"], 200)
        self.assertEqual(self.host.saved["advanced"]["keep_days"], 14)
        self.assertEqual(self.host.saved["advanced"]["other_advanced"], {"a": 1})
        self.assertEqual(self.plugin._settings_snapshot()["keep_days"], 14)
    def test_authentication_validation_and_body_limit_precede_saving(self):
        self.web.request.username = None
        self.assertEqual(self.request()["http_status"], 401)
        self.web.request.username = "admin"
        for key, value in (("enabled", 1), ("detail_history", "true"), ("keep_days", -1), ("keep_days", 3651), ("keep_days", True), ("detail_max_events", 0), ("detail_max_events", 200001)):
            self.assertEqual(self.request(self.values(**{key: value}))["http_status"], 400)
        self.assertEqual(self.request({"enabled": False})["http_status"], 400)
        self.assertEqual(self.request(self.values(), size=4097)["http_status"], 413)
        self.assertEqual(self.host.calls, 0)
    def test_stale_revision_is_rejected(self):
        revision = self.plugin._settings_revision
        self.assertTrue(self.request(self.values(keep_days=7), revision=revision)["data"]["saved"])
        self.assertEqual(self.request(self.values(keep_days=30), revision=revision)["http_status"], 409)
        self.assertEqual(self.host.saved["keep_days"], 7)
    def test_plugin_restart_uses_new_revision_token(self):
        with patch.object(self.module.secrets, "randbits", side_effect=[100, 200]):
            self.make_plugin(self.host)
            revision = self.plugin._settings_revision
            self.make_plugin(self.host)
        self.assertEqual(self.request(self.values(keep_days=7), revision=revision)["http_status"], 409)
        self.assertEqual(self.host.calls, 0)
    def test_missing_host_saver_never_claims_in_memory_change_is_saved(self):
        self.make_plugin(dict(self.host))
        self.assertFalse(self.plugin._settings_snapshot()["writable"])
        self.assertEqual(self.request()["http_status"], 501)
    def test_runtime_failure_distinguishes_saved_from_applied(self):
        with patch.object(self.plugin, "_hook_providers", side_effect=OSError("runtime unavailable")):
            result = self.request(self.values(enabled=True))
        self.assertTrue(result["data"]["saved"])
        self.assertFalse(result["data"]["applied"])
        self.assertTrue(result["data"]["reload_required"])
        self.assertTrue(self.host.saved["enabled"])
    def test_enable_installs_and_disable_restores_without_reloading(self):
        with patch.object(self.plugin, "_hook_providers", return_value=["Example.text_chat"]) as hook:
            self.assertTrue(self.request(self.values(enabled=True))["data"]["applied"])
            self.assertEqual(hook.call_count, 1)
        with patch.object(self.plugin._registry, "restore", return_value=[]) as restored:
            self.assertTrue(self.request(self.values(enabled=False))["data"]["applied"])
            self.assertEqual(restored.call_count, 1)
        self.assertIsNone(self.plugin._refresh_task)
    def test_method_list_is_returned_from_actual_runtime_labels(self):
        self.plugin._patched = ["Example.text_chat", "Example.text_chat_stream"]
        data = asyncio.run(self.plugin.api_stats())["data"]
        self.assertEqual(data["collection"]["wrapped_methods"], self.plugin._patched)
        self.assertEqual(data["collection"]["patched_count"], 2)
    def test_host_file_is_saved_and_reloaded(self):
        from uuid import uuid4
        root = Path(__file__).resolve().parents[1] / ".test-tmp"
        root.mkdir(exist_ok=True)
        path = root / ("plugin-settings-" + uuid4().hex + ".json")
        try:
            host = HostConfig(dict(self.host), path=path)
            self.make_plugin(host)
            result = self.request(self.values(keep_days=2))
            self.assertEqual(result["http_status"], 200)
            self.assertTrue(result["data"]["saved"])
            reloaded = HostConfig(json.loads(path.read_text(encoding="utf-8")))
            self.make_plugin(reloaded)
            self.assertEqual(self.plugin._settings_snapshot()["keep_days"], 2)
        finally:
            path.unlink(missing_ok=True)

if __name__ == "__main__":
    unittest.main()
