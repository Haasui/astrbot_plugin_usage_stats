"""Canceled calls retain a separate count through legacy aggregation."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from astrbot_plugin_usage_stats import store
from astrbot_plugin_usage_stats.stats import Call, Usage, fold_by_day_model, fold_by_model, fold_by_model_across, add_into


class CancellationCountsTests(unittest.TestCase):
    def calls(self):
        return [Call("plugin", "provider", "model", 1794370000, Usage(1, 0, 1), ok=result == "success", result=result)
                for result in ("success", "failed", "canceled")]
    def check_bucket(self, bucket):
        self.assertEqual(bucket.calls, 3)
        self.assertEqual(bucket.failed, 1)
        self.assertEqual(getattr(bucket, "canceled", 0), 1)
        self.assertEqual(bucket.usage.total, 6)
    def test_cancel_is_separate_from_failure(self):
        self.check_bucket(fold_by_model(self.calls())["plugin"]["provider|model"])
    def test_cancel_survives_snapshot_and_all_fold_paths(self):
        snapshot = store.snapshot(fold_by_day_model(self.calls()))
        nested = store.as_buckets(snapshot)
        view, _, _ = store.window_view(nested)
        self.check_bucket(view["plugin"]["provider|model"])
        self.check_bucket(store.by_plugin_model(nested)["plugin"]["provider|model"])
        self.check_bucket(fold_by_model_across(view)[0][1])
        target = {}
        add_into(target, view)
        self.check_bucket(target["plugin"]["provider|model"])
        merged = store.as_buckets(store.merge(snapshot, snapshot))
        aggregate = store.aggregate(store.window_view(merged)[0])
        self.assertEqual((aggregate["calls"], aggregate["failed"], aggregate["canceled"]), (6, 2, 2))
        self.assertEqual(aggregate["tokens"], 12)


if __name__ == "__main__":
    unittest.main()
