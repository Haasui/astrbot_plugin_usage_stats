"""EventStore regression tests: standard library and in-memory SQLite.

Run: python tests/test_event_store.py
"""
from __future__ import annotations

import csv
import io
import json
import pathlib
import sqlite3
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from astrbot_plugin_usage_stats.event_store import EventStore
from astrbot_plugin_usage_stats.stats import Bucket, Call, Shape, Usage, fold_by_day_model
from astrbot_plugin_usage_stats import store as daily_store

TZ = timezone(timedelta(hours=8))
DAY = datetime(2026, 11, 11, tzinfo=TZ).timestamp()
NOW = DAY + 12 * 3600


def usage(other=10, cached=0, output=5, reported=1, cache=1, reasoning=None, write=None):
    return Usage(input_other=other, input_cached=cached, output=output,
                 cache_reported=cache, reasoning=reasoning, cache_write=write,
                 usage_reported=reported)


def call(identifier="a", timestamp=DAY + 100, plugin="p", provider="vendor", model="m",
         result="success", tokens=None, latency=1000, ttft=None):
    return Call(plugin, provider, model, timestamp, tokens if tokens is not None else usage(),
                ok=result == "success", shape=Shape(ms=latency) if latency is not None else None,
                event_id=identifier, result=result, ttft_ms=ttft)


def aggregate(calls):
    grouped = {}
    for row in calls:
        day = datetime.fromtimestamp(row.timestamp, TZ).date().isoformat()
        key = (row.plugin, day, row.model_key)
        grouped.setdefault(key, []).append(row)
    out = {}
    for (plugin, day, key), rows in grouped.items():
        bucket = Bucket()
        bucket.calls = len(rows)
        bucket.failed = sum(row.result == "failed" for row in rows)
        bucket.canceled = sum(row.result == "canceled" for row in rows)
        values = {name: sum(getattr(row.usage, name) for row in rows) for name in
                  ("input_other", "input_cached", "output", "cache_reported", "usage_reported")}
        optional = {}
        for name in ("reasoning", "cache_write"):
            known = [getattr(row.usage, name) for row in rows if getattr(row.usage, name) is not None]
            optional[name] = sum(known) if known else None
        bucket.usage = usage(values["input_other"], values["input_cached"], values["output"],
                             values["usage_reported"], values["cache_reported"],
                             optional["reasoning"], optional["cache_write"])
        out.setdefault(plugin, {}).setdefault(day, {})[key] = bucket
    return out


class EventStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = EventStore()
        self.addCleanup(self.store.close)

    def dashboard(self, rows=(), query=None, now=NOW):
        return self.store.dashboard(query or {}, aggregate(rows), now=now)

    def test_uncommitted_details_do_not_consume_unrelated_legacy_history(self):
        old = call("old", tokens=usage(10, 0, 0))
        new = call("new", tokens=usage(10, 0, 0))
        self.store.append(new)
        data = self.store.dashboard({"days": "0"}, aggregate([old]), now=NOW, covered_ids=set())
        self.assertEqual(data["overview"]["calls"], 2)
        self.assertEqual(data["overview"]["tokens"], 20)
    def test_checkpoint_proof_preserves_previous_publish_and_recovers_new_rows(self):
        first, second = call("a"), call("b")
        self.store.append(first)
        published = self.store.prepare_checkpoint()
        self.assertEqual(self.store.covered_ids(published), {"a"})
        self.store.append(second)
        pending = self.store.prepare_checkpoint(published)
        self.assertEqual(self.store.covered_ids(published), {"a"})
        self.assertEqual({row.event_id for row in self.store.uncovered(published)}, {"b"})
        self.store.prepare_checkpoint(published)
        self.assertEqual(self.store.covered_ids(published), {"a"})
        latest = self.store.prepare_checkpoint(published)
        self.store.finalize_checkpoint(latest)
        self.assertEqual(self.store.covered_ids(latest), {"a", "b"})
        self.assertEqual(self.store.uncovered(latest), [])
        with self.assertRaises(ValueError):
            self.store.covered_ids(pending)
    def test_nonempty_precheckpoint_database_is_not_silently_recounted(self):
        connection = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
        self.store.append(call())
        self.store._db.backup(connection)
        connection.execute("DROP TABLE event_checkpoints")
        connection.execute("PRAGMA user_version=1")
        with patch("astrbot_plugin_usage_stats.event_store.sqlite3.connect", return_value=connection):
            with self.assertRaisesRegex(ValueError, "no daily inclusion proof"):
                EventStore("legacy-fixture")
    def test_empty_precheckpoint_database_upgrades_without_losing_prices(self):
        connection = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
        self.store.save_prices([{"model": "m", "input": 0}])
        self.store._db.backup(connection)
        connection.execute("DROP TABLE event_checkpoints")
        connection.execute("PRAGMA user_version=1")
        with patch("astrbot_plugin_usage_stats.event_store.sqlite3.connect", return_value=connection):
            reopened = EventStore("legacy-empty-fixture")
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.dashboard({}, {}, now=NOW)["prices"][0]["input"], 0)
    def test_version_two_upgrade_preserves_detail_inclusion_proofs(self):
        self.store.append(call())
        token = self.store.prepare_checkpoint()
        connection = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
        self.store._db.backup(connection)
        connection.execute("DROP TABLE catalog_prices")
        connection.execute("DROP TABLE catalog_state")
        connection.execute("PRAGMA user_version=2")
        with patch("astrbot_plugin_usage_stats.event_store.sqlite3.connect", return_value=connection):
            reopened = EventStore("version-two-fixture")
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.covered_ids(token), {"a"})
        self.assertEqual(reopened.uncovered(token), [])
        data = reopened.dashboard({}, aggregate([call()]), now=NOW, covered_ids=reopened.covered_ids(token))
        self.assertEqual(data["overview"]["calls"], 1)
        self.assertIsNone(data["pricing"]["updated_at"])
    def test_empty_contract_and_null_samples(self):
        data = self.dashboard()
        self.assertEqual(set(data), {"overview", "timeline", "granularity", "analysis", "events",
                                     "options", "prices", "pricing", "coverage", "query"})
        self.assertEqual(data["overview"]["calls"], 0)
        self.assertEqual(data["events"]["total_pages"], 0)
        for key in ("tps", "average_latency_ms", "estimated_cost", "cache_hit_rate", "success_rate"):
            self.assertIsNone(data["overview"][key])
        self.assertEqual(data["query"]["days"], "30")
        self.assertEqual(data["coverage"]["detail_start"], None)
        self.assertLessEqual(len(data["timeline"]), 366)
        json.dumps(data, allow_nan=False)

    def test_idempotent_and_same_batch_not_double_counted(self):
        row = call()
        self.store.append(row)
        self.store.append(row)
        one = self.dashboard([row])
        two = self.dashboard([row])
        self.assertEqual(one, two)
        self.assertEqual(one["overview"]["calls"], 1)
        self.assertEqual(one["overview"]["tokens"], 15)
        self.assertEqual(one["coverage"]["legacy_calls"], 0)
        self.assertEqual(one["coverage"]["detail_calls"], 1)
        self.assertEqual(one["coverage"]["warnings"], [])

    def test_real_bucket_fold_and_json_roundtrip_deduplication(self):
        old = call("old", timestamp=DAY - 86400, tokens=usage(20, 2, 10))
        rows = [call("ok", tokens=usage(10, 3, 5, reasoning=2, write=4)),
                call("cancel", result="canceled", tokens=usage(0, 0, 0, reported=0, cache=0))]
        for row in rows:
            self.store.append(row)
        baseline = daily_store.snapshot(fold_by_day_model([old]))
        memory = daily_store.snapshot(fold_by_day_model(rows))
        buckets = daily_store.as_buckets(daily_store.merge(baseline, memory))
        data = self.store.dashboard({}, buckets, now=NOW)
        self.assertEqual(data["overview"]["calls"], 3)
        self.assertEqual(data["overview"]["tokens"], old.usage.total + sum(row.usage.total for row in rows))
        self.assertEqual(data["overview"]["canceled"], 1)
        self.assertEqual(data["coverage"]["legacy_calls"], 1)
        self.assertFalse(any("differ" in warning for warning in data["coverage"]["warnings"]))

    def test_merge_residual_legacy_and_detail(self):
        old = call("old", timestamp=DAY + 20, tokens=usage(20, 5, 10))
        new = call("new", tokens=usage(3, 1, 2))
        self.store.append(new)
        data = self.dashboard([old, new])
        self.assertEqual(data["overview"]["calls"], 2)
        self.assertEqual(data["overview"]["tokens"], 41)
        self.assertEqual(data["coverage"]["legacy_calls"], 1)
        self.assertEqual(data["events"]["total"], 1)
        self.assertEqual(sum(point["calls"] for point in data["timeline"]), 2)
        self.assertEqual(data["granularity"], "day")
        self.assertTrue(data["coverage"]["warnings"])

    def test_pruning_details_never_loses_daily_totals(self):
        rows = [call(str(i), timestamp=DAY + i + 1) for i in range(4)]
        for row in rows:
            self.store.append(row)
        before = self.dashboard(rows)["overview"]
        self.store.prune(keep_days=0, max_events=1, now=NOW)
        after = self.dashboard(rows)
        self.assertEqual(after["overview"]["calls"], before["calls"])
        self.assertEqual(after["overview"]["tokens"], before["tokens"])
        self.assertEqual(after["coverage"]["legacy_calls"], 3)
        self.assertEqual(after["coverage"]["detail_calls"], 1)
        self.assertEqual(after["events"]["items"][0]["id"], "3")

    def test_age_prune_uses_natural_days_in_utc8(self):
        rows = [call("old", timestamp=DAY - 86400 - 1), call("edge", timestamp=DAY - 86400), call("today")]
        for row in rows:
            self.store.append(row)
        self.store.prune(keep_days=2, now=NOW)
        ids = {row["id"] for row in self.dashboard(rows)["events"]["items"]}
        self.assertEqual(ids, {"edge", "today"})
        self.assertEqual(self.dashboard(rows)["overview"]["calls"], 3)

    def test_result_and_subday_exclude_unrecoverable_legacy(self):
        old, new = call("old"), call("new", result="failed")
        self.store.append(new)
        for query in ({"result": "failed"}, {"start": "2026-11-11T00:01:00+08:00"}):
            with self.subTest(query=query):
                data = self.dashboard([old, new], query)
                self.assertEqual(data["overview"]["calls"], 1)
                self.assertEqual(data["coverage"]["excluded_legacy_calls"], 1)
                self.assertEqual(data["coverage"]["legacy_calls"], 0)
        all_day = self.dashboard([old, new], {"start": "2026-11-11T00:00:00+08:00",
                                                     "end": "2026-11-12T00:00:00+08:00"})
        self.assertEqual(all_day["overview"]["calls"], 2)

    def test_half_open_boundaries_and_timezone_equivalence(self):
        rows = [call("before", DAY - 1), call("start", DAY), call("inside", DAY + 3599), call("end", DAY + 3600)]
        for row in rows:
            self.store.append(row)
        data = self.dashboard(rows, {"start": "2026-11-10T16:00:00Z", "end": "2026-11-11T01:00:00+08:00"})
        self.assertEqual({row["id"] for row in data["events"]["items"]}, {"start", "inside"})
        self.assertEqual(data["overview"]["calls"], 2)
        self.assertEqual(sum(point["calls"] for point in data["timeline"]), 2)
        self.assertEqual(data["timeline"][0]["timestamp"], DAY)
        self.assertEqual(len(data["timeline"]), 2)

    def test_exact_filters_and_time_only_options(self):
        rows = [call("a", plugin="a", provider="x", model="same"),
                call("b", plugin="b", provider="y", model="same", result="failed"),
                call("c", plugin="b", provider="x", model="same-suffix"),
                call("d", timestamp=DAY - 86400, plugin="outside", model="old")]
        for row in rows:
            self.store.append(row)
        data = self.dashboard(rows, {"days": "1", "plugin": "a", "model": "same", "provider": "x", "result": "success"})
        self.assertEqual(data["overview"]["calls"], 1)
        self.assertEqual(data["options"], {"plugins": ["a", "b"], "models": ["same", "same-suffix"], "providers": ["x", "y"]})
        self.assertEqual(self.dashboard(rows, {"model": "x|same"})["overview"]["calls"], 0)

    def test_invalid_queries_raise_value_error(self):
        cases = [{"days": "2"}, {"days": 30}, {"result": "unknown"}, {"group": "provider"},
                 {"speed_mode": "bad"}, {"page": 0}, {"page": 1.5}, {"page_size": -1},
                 {"page": True}, {"plugin": []}, {"start": "not-a-date"},
                 {"start": "2026-11-11T01:00:00"}, {"start": "2026-11-31T01:00:00Z"},
                 {"start": "2026-11-11T00:00:00Z", "end": "2026-11-10T00:00:00Z"}]
        for query in cases:
            with self.subTest(query=query), self.assertRaises(ValueError):
                self.dashboard(query=query)
        self.assertEqual(self.dashboard(query={"page_size": 999})["query"]["page_size"], 200)

    def test_unknown_and_zero_are_distinct(self):
        unknown = call("unknown", tokens=usage(0, 0, 0, reported=0, cache=0), latency=None)
        self.store.append(unknown)
        data = self.dashboard([unknown])
        self.assertIsNone(data["overview"]["tokens"])
        self.assertIsNone(data["events"]["items"][0]["output"])
        zero = call("zero", tokens=usage(0, 0, 0, reasoning=0, write=0), latency=0)
        self.store.append(zero)
        data = self.dashboard([unknown, zero])
        for key in ("tokens", "input_other", "input_cached", "output", "reasoning", "cache_write", "cache_hit_rate", "cache_share"):
            self.assertEqual(data["overview"][key], 0)
        self.assertEqual(data["overview"]["average_latency_ms"], 0)
        self.assertEqual(data["overview"]["latency_samples"], 1)
        self.assertIsNone(data["overview"]["tps"])

    def test_cache_reporting_controls_ratios(self):
        row = call(tokens=usage(10, 0, 5, cache=0))
        self.store.append(row)
        data = self.dashboard([row])
        self.assertIsNone(data["overview"]["cache_hit_rate"])
        self.assertIsNone(data["events"]["items"][0]["input_cached"])
        self.assertEqual(data["overview"]["tokens"], 15)

    def test_reasoning_and_cache_write_not_double_counted(self):
        row = call(tokens=usage(10, 3, 20, reasoning=7, write=5))
        self.store.append(row)
        data = self.dashboard([row])
        self.assertEqual(data["overview"]["tokens"], row.usage.total)
        self.assertEqual(data["overview"]["tokens"], 33)
        self.assertEqual(data["overview"]["input_other"], 10)
        self.assertEqual(data["overview"]["reasoning"], 7)
        self.assertAlmostEqual(data["overview"]["cache_hit_rate"], 3 / 13)
        self.assertAlmostEqual(data["overview"]["cache_share"], 3 / 33)
        self.store.save_prices([{"model": "m", "input": 1, "cache_read": 2, "cache_write": 3, "output": 4}])
        self.assertAlmostEqual(self.dashboard([row])["overview"]["estimated_cost"], (5 + 6 + 15 + 80) / 1_000_000)

    def test_canceled_excluded_from_success_rate_and_weighted_tps(self):
        rows = [call("a", tokens=usage(output=10), latency=1000),
                call("b", tokens=usage(output=90), latency=3000),
                call("c", result="canceled", tokens=usage(output=500), latency=1),
                call("d", result="failed", tokens=usage(output=500), latency=1)]
        for row in rows:
            self.store.append(row)
        data = self.dashboard(rows)
        overview = data["overview"]
        self.assertEqual(overview["tps"], 25)
        self.assertEqual(overview["tps_samples"], 2)
        self.assertAlmostEqual(overview["success_rate"], 2 / 3)
        self.assertEqual(overview["canceled"], 1)
        self.assertEqual(overview["failed"], 1)
        self.assertEqual(overview["latency_samples"], 4)
        self.assertIsNone(next(row for row in data["events"]["items"] if row["id"] == "c")["tps"])

    def test_ttft_tps_rejects_invalid_or_short_generation_window(self):
        rows = [call("valid", tokens=usage(output=10), latency=1000, ttft=900),
                call("floor", tokens=usage(output=10), latency=100, ttft=100),
                call("missing", tokens=usage(output=100), latency=1000),
                call("invalid", tokens=usage(output=100), latency=1000, ttft=2000)]
        for row in rows:
            self.store.append(row)
        data = self.dashboard(rows, {"speed_mode": "ttft"})
        self.assertEqual(data["overview"]["tps_samples"], 1)
        self.assertEqual(data["overview"]["tps"], 100)

    def test_prices_null_missing_zero_and_exact_provider_priority(self):
        rows = [call("a", provider="specific"), call("b", provider="other"), call("c", model="m-extra")]
        for row in rows:
            self.store.append(row)
        self.store.save_prices([{"model": "m", "input": 2, "output": 4},
                                {"model": "m", "provider": "specific", "input": 0, "output": 0}])
        data = self.dashboard(rows)
        priced = {row["id"]: row["estimated_cost"] for row in data["events"]["items"]}
        self.assertEqual(priced["a"], 0)
        self.assertAlmostEqual(priced["b"], 40 / 1_000_000)
        self.assertIsNone(priced["c"])
        self.assertEqual(data["overview"]["priced_calls"], 2)
        self.assertEqual(data["overview"]["unpriced_calls"], 1)
        # A matching exact row with a missing needed rate does not fall through.
        self.store.save_prices([{"model": "m", "input": 1, "output": 1},
                                {"model": "m", "provider": "specific", "input": None, "output": 0}])
        specific = self.dashboard(rows, {"provider": "specific"})
        self.assertIsNone(specific["overview"]["estimated_cost"])

    def test_cache_write_exceeding_normalized_input_is_not_priced(self):
        row = call(tokens=usage(1, 0, 3, write=5))
        self.store.append(row)
        self.store.save_prices([{"model": "m", "input": 1, "output": 1, "cache_write": 1}])
        data = self.dashboard([row])
        self.assertEqual(data["overview"]["tokens"], row.usage.total)
        self.assertIsNone(data["overview"]["estimated_cost"])
        self.assertTrue(any("exceeds" in warning for warning in data["coverage"]["warnings"]))

    def test_missing_nonzero_cache_price_is_unpriced(self):
        row = call(tokens=usage(10, 2, 3, write=4))
        self.store.append(row)
        self.store.save_prices([{"model": "m", "input": 1, "output": 1, "cache_read": 0}])
        self.assertIsNone(self.dashboard([row])["overview"]["estimated_cost"])
        self.store.save_prices([{"model": "m", "input": 1, "output": 1, "cache_read": 0, "cache_write": 0}])
        self.assertEqual(self.dashboard([row])["overview"]["priced_calls"], 1)

    def test_price_validation_atomic_replace_and_clear_preserves_prices(self):
        prices = [{"model": "m", "input": 0}]
        self.store.save_prices(prices)
        initial = self.dashboard()["prices"]
        for rows in ([{"model": "m", "input": -1}], [{"model": "m", "input": float("nan")}],
                     [{"model": "m", "input": True}], [{"model": "m", "multiplier": 2}],
                     [{"model": "m"}, {"model": "m"}]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                self.store.save_prices(rows)
            self.assertEqual(self.dashboard()["prices"], initial)
        self.store.append(call())
        self.store.clear()
        self.assertEqual(self.dashboard()["events"]["total"], 0)
        self.assertEqual(self.dashboard()["prices"], initial)
        self.store.save_prices([])
        self.assertEqual(self.dashboard()["prices"], [])

    def test_pagination_stable_ties_limit_and_out_of_range(self):
        rows = [call(f"{i:03}") for i in range(205)]
        for row in rows:
            self.store.append(row)
        first = self.dashboard(rows, {"page_size": 999})["events"]
        second = self.dashboard(rows, {"page_size": 200, "page": 2})["events"]
        self.assertEqual(len(first["items"]), 200)
        self.assertEqual(first["items"][0]["id"], "204")
        self.assertEqual(len(second["items"]), 5)
        self.assertEqual(first["total_pages"], 2)
        self.assertFalse({row["id"] for row in first["items"]} & {row["id"] for row in second["items"]})
        self.assertEqual(self.dashboard(rows, {"page": 100})["events"]["items"], [])

    def test_export_bom_csv_formula_defense_and_filters_match_page(self):
        rows = [call("=id", plugin=' 中文,"line\nnext', model="@formula"),
                call("b", plugin="other", model="safe", result="failed")]
        for row in rows:
            self.store.append(row)
        query = {"model": "@formula", "page_size": 1, "page": 2}
        result = self.store.export(query, aggregate(rows), now=NOW)
        self.assertTrue(result["csv"].startswith("\ufeff"))
        parsed = list(csv.DictReader(io.StringIO(result["csv"].lstrip("\ufeff"))))
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["id"], "'=id")
        self.assertEqual(parsed[0]["model"], "'@formula")
        self.assertEqual(parsed[0]["plugin"], rows[0].plugin)
        self.assertEqual(result["total"], 1)
        self.assertFalse(result["truncated"])
        self.assertNotIn("session", parsed[0])
        self.assertNotIn("method", parsed[0])
        self.assertEqual(self.dashboard(rows, {"model": "@formula"})["events"]["total"], result["total"])

    def test_export_is_detail_only_and_capped_5000(self):
        rows = [call(str(i), timestamp=DAY + i) for i in range(5002)]
        for row in rows:
            self.store.append(row)
        old = call("legacy", timestamp=DAY - 100)
        result = self.store.export({}, aggregate([old, *rows]), now=NOW)
        self.assertEqual(result["returned"], 5000)
        self.assertEqual(result["total"], 5002)
        self.assertTrue(result["truncated"])
        self.assertEqual(len(list(csv.reader(io.StringIO(result["csv"])))), 5001)

    def test_export_fixes_now_once(self):
        rows = [call("a", timestamp=NOW - 1), call("b", timestamp=NOW)]
        for row in rows:
            self.store.append(row)
        with patch("astrbot_plugin_usage_stats.event_store.time.time", side_effect=[NOW, NOW + 1]) as clock:
            result = self.store.export({}, aggregate(rows))
        self.assertEqual(result["total"], 1)
        self.assertEqual(clock.call_count, 1)

    def test_mismatched_daily_bucket_warns_without_double_counting(self):
        row = call(tokens=usage(20, 0, 5))
        self.store.append(row)
        stale = aggregate([call(tokens=usage(10, 0, 5))])
        data = self.store.dashboard({}, stale, now=NOW)
        self.assertEqual(data["overview"]["calls"], 1)
        self.assertEqual(data["overview"]["tokens"], 25)
        self.assertTrue(any("differ" in warning for warning in data["coverage"]["warnings"]))

    def test_timeline_bounded_and_zero_filled(self):
        rows = [call("ancient", timestamp=DAY - 3000 * 86400), call("today")]
        for row in rows:
            self.store.append(row)
        data = self.dashboard(rows, {"days": "0"})
        self.assertLessEqual(len(data["timeline"]), 366)
        self.assertEqual(sum(point["calls"] for point in data["timeline"]), 2)
        self.assertEqual(sum(point["tokens"] for point in data["timeline"]), 30)
        self.assertTrue(any(point["calls"] == 0 for point in data["timeline"]))
        self.assertTrue(any("coarsened" in warning for warning in data["coverage"]["warnings"]))

    def test_connection_reopen_preserves_rows_prices_and_schema(self):
        # backup restores an actual serialized SQLite database into another
        # in-memory connection, avoiding OS temporary-directory permissions.
        self.store.append(call())
        self.store.save_prices([{"model": "m", "input": 0, "output": 0}])
        connection = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
        self.store._db.backup(connection)
        with patch("astrbot_plugin_usage_stats.event_store.sqlite3.connect", return_value=connection):
            reopened = EventStore("ignored-in-memory-fixture")
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.dashboard({}, aggregate([call()]), now=NOW)["overview"]["priced_calls"], 1)
        self.assertEqual(reopened._db.execute("PRAGMA user_version").fetchone()[0], 3)

    def test_future_schema_rejected_without_overwriting(self):
        connection = sqlite3.connect(":memory:", isolation_level=None)
        connection.execute("PRAGMA user_version=999")
        connection.execute("CREATE TABLE sentinel(value)")
        with patch("astrbot_plugin_usage_stats.event_store.sqlite3.connect", return_value=connection):
            with self.assertRaisesRegex(ValueError, "newer"):
                EventStore()
        # Rejection closes the handle rather than attempting a destructive reset.
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT * FROM sentinel")

    def test_thread_safe_shared_connection(self):
        rows = [call(str(i)) for i in range(100)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(self.store.append, rows * 2))
        self.assertEqual(self.dashboard(rows)["events"]["total"], 100)

    def test_old_positive_tokens_survive_missing_usage_marker(self):
        legacy = call(tokens=usage(20, 0, 5, reported=0, cache=0))
        data = self.dashboard([legacy])
        self.assertEqual(data["overview"]["tokens"], 25)
        self.assertEqual(data["overview"]["input_other"], 20)
        self.assertEqual(data["coverage"]["legacy_calls"], 1)

    def test_partially_reported_legacy_cache_cannot_supply_ratio_denominator(self):
        rows = [call("reported", tokens=usage(10, 5, 3, cache=1)),
                call("unknown", tokens=usage(1000, 0, 4, cache=0))]
        data = self.dashboard(rows)
        self.assertIsNone(data["overview"]["cache_hit_rate"])
        self.assertIsNone(data["overview"]["cache_share"])
        self.assertEqual(data["overview"]["input_cached"], 5)
        self.assertTrue(any("cache" in warning.lower() for warning in data["coverage"]["warnings"]))

    def test_rfc3339_rejects_normalized_invalid_offset_minutes(self):
        for bound in ("2026-11-11T00:00:00+00:60", "2026-11-11T00:00:00-01:99"):
            with self.subTest(bound=bound), self.assertRaises(ValueError):
                self.dashboard(query={"start": bound})

    def test_day_one_at_midnight_is_valid_empty_window(self):
        data = self.dashboard(query={"days": "1"}, now=DAY)
        self.assertEqual(data["overview"]["calls"], 0)
        self.assertEqual(data["overview"]["tokens"], 0)
        self.assertIsNone(data["overview"]["rpm"])
        self.assertEqual(data["timeline"], [])

    def test_connection_never_uses_disk_temporary_storage(self):
        self.assertEqual(self.store._db.execute("PRAGMA temp_store").fetchone()[0], 2)
        with self.assertRaises(ValueError):
            EventStore("")

    def test_failures_propagate_and_secrets_never_stored(self):
        row = call()
        row.session = "PRIVATE_SESSION_SECRET"
        row.error = "PRIVATE_ERROR_SECRET"
        row.body = "PRIVATE_BODY_SECRET"
        self.store.append(row)
        contents = json.dumps(self.dashboard([row])) + self.store.export({}, aggregate([row]), now=NOW)["csv"]
        self.assertNotIn("PRIVATE_", contents)
        columns = {info[1] for info in self.store._db.execute("PRAGMA table_info(events)")}
        self.assertFalse(columns & {"session", "body", "error", "api_key", "method"})
        self.store.close()
        with self.assertRaises(sqlite3.ProgrammingError):
            self.store.append(call("another"))
        self.store.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
