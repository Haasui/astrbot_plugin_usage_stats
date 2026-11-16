"""Pricing statistics must remain visible without a hand-entered price table."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from astrbot_plugin_usage_stats.event_store import EventStore
from astrbot_plugin_usage_stats.stats import Call, Usage, fold_by_day_model

NOW = datetime(2026, 11, 11, 12, tzinfo=timezone(timedelta(hours=8))).timestamp()

class PricingIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.store = EventStore()
        self.addCleanup(self.store.close)
        self.call = Call("plugin", "local-provider", "model-a", NOW - 1, Usage(100, 0, 50, 1, usage_reported=1))
        self.store.append(self.call)
        self.aggregates = fold_by_day_model([self.call])
    def data(self, query=None):
        return self.store.dashboard(query or {"days": "0"}, self.aggregates, now=NOW)
    def catalog(self, input=2, output=8):
        return [{"model": "model-a", "provider": "", "input": input, "output": output, "cache_read": None, "cache_write": None, "source_provider": "native"}]
    def test_used_model_appears_even_when_prices_have_never_been_configured(self):
        data = self.data()
        self.assertEqual(data["prices"], [])
        row = data["pricing"]["rows"][0]
        self.assertEqual((row["model"], row["provider"], row["calls"], row["tokens"]), ("model-a", "local-provider", 1, 150))
        self.assertIsNone(row["price"])
        self.assertIsNone(row["estimated_cost"])
    def test_synced_reference_prices_flow_into_overview_rows_and_details(self):
        self.store.save_catalog(self.catalog(), updated_at=NOW)
        data = self.data()
        self.assertEqual(data["prices"], [])
        self.assertAlmostEqual(data["overview"]["estimated_cost"], .0006)
        row = data["pricing"]["rows"][0]
        self.assertAlmostEqual(row["estimated_cost"], .0006)
        self.assertEqual(row["price_source"], "models.dev")
        self.assertAlmostEqual(data["events"]["items"][0]["estimated_cost"], .0006)
        self.assertEqual(data["pricing"]["updated_at"], NOW)
    def test_manual_prices_survive_catalog_refresh_and_take_precedence(self):
        self.store.save_catalog(self.catalog(), updated_at=NOW)
        self.store.save_prices([{"model": "model-a", "provider": "local-provider", "input": 0, "output": 0}])
        self.store.save_catalog(self.catalog(10, 20), updated_at=NOW + 1)
        data = self.data()
        self.assertEqual(data["overview"]["estimated_cost"], 0)
        self.assertEqual(data["pricing"]["rows"][0]["price_source"], "manual")
        self.assertEqual(len(data["prices"]), 1)
        self.store.save_prices([])
        self.assertAlmostEqual(self.data()["overview"]["estimated_cost"], .002)
    def test_catalog_models_without_usage_are_visible_with_zero_calls(self):
        self.store.save_catalog(self.catalog() + [{**self.catalog()[0], "model": "model-b"}], updated_at=NOW)
        rows = self.data()["pricing"]["rows"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["model"], "model-a")
        self.assertEqual((rows[1]["calls"], rows[1]["tokens"], rows[1]["estimated_cost"]), (0, 0, 0))
    def test_global_model_filter_also_limits_price_directory_rows(self):
        self.store.save_catalog(self.catalog() + [{**self.catalog()[0], "model": "model-b"}], updated_at=NOW)
        data = self.data({"days": "0", "model": "model-a"})
        self.assertEqual([row["model"] for row in data["pricing"]["rows"]], ["model-a"])
    def test_catalog_invalid_refresh_is_atomic(self):
        self.store.save_catalog(self.catalog(), updated_at=NOW)
        with self.assertRaises(ValueError):
            self.store.save_catalog(self.catalog(input=-1), updated_at=NOW + 1)
        self.assertEqual(self.data()["pricing"]["updated_at"], NOW)
        self.assertAlmostEqual(self.data()["overview"]["estimated_cost"], .0006)

if __name__ == "__main__":
    unittest.main()
