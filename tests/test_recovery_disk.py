"""Real-file recovery tests for the shared JSON/SQLite publication proof."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from astrbot_plugin_usage_stats import store
from astrbot_plugin_usage_stats.event_store import EventStore
from astrbot_plugin_usage_stats.stats import Call, Usage, fold_by_day_model


class DiskRecoveryTests(unittest.TestCase):
    def test_sqlite_commit_then_failed_json_publish_recovers_exactly_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "usage.json"
            now = time.time()
            old = Call("plugin", "provider", "model", now - 2, Usage(1000, 0, 0, 1, usage_reported=1))
            new = Call("plugin", "provider", "model", now - 1, Usage(10, 0, 0, 1, usage_reported=1))
            original = store.snapshot(fold_by_day_model([old]))
            self.assertTrue(store.save(path, original))
            database = root / "events.db"
            engine = EventStore(database)
            try:
                engine.append(new)
                pending = engine.prepare_checkpoint()
                combined = store.merge(original, store.snapshot(fold_by_day_model([new])))
                with patch("astrbot_plugin_usage_stats.store.os.replace", side_effect=OSError("injected publish failure")):
                    self.assertFalse(store.save(path, combined, event_checkpoint=pending))
            finally:
                engine.close()
            self.assertEqual(store.load(path), original)
            self.assertEqual(store.load_checkpoint(path), "")
            engine = EventStore(database)
            try:
                recovered = engine.uncovered(store.load_checkpoint(path))
                self.assertEqual([row.event_id for row in recovered], [new.event_id])
                combined = store.merge(store.load(path), store.snapshot(fold_by_day_model(recovered)))
                data = engine.dashboard({"days": "0"}, store.as_buckets(combined), now=now, covered_ids={row.event_id for row in recovered})
                self.assertEqual((data["overview"]["calls"], data["overview"]["tokens"]), (2, 1010))
                published = engine.prepare_checkpoint()
                self.assertTrue(store.save(path, combined, event_checkpoint=published))
                engine.finalize_checkpoint(published)
            finally:
                engine.close()
            engine = EventStore(database)
            try:
                published = store.load_checkpoint(path)
                self.assertEqual(engine.uncovered(published), [])
                for _ in range(2):
                    data = engine.dashboard({"days": "0"}, store.as_buckets(store.load(path)), now=now, covered_ids=engine.covered_ids(published))
                    self.assertEqual((data["overview"]["calls"], data["overview"]["tokens"]), (2, 1010))
            finally:
                engine.close()


if __name__ == "__main__":
    unittest.main()
