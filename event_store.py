"""SQLite detail history and manual USD prices, independent of AstrBot.

Daily Bucket objects retain totals; detail rows replace their own contributions,
leaving daily legacy history. No sessions, messages, credentials or exception text
are persisted.
"""
from __future__ import annotations

import csv
import io
import json
from uuid import uuid4
import math
import re
import sqlite3
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Mapping

if TYPE_CHECKING:
    from .stats import Call

_TZ = timezone(timedelta(hours=8))
_VERSION = 3
_PRICE_FIELDS = ("input", "cache_read", "cache_write", "output")
_TOKEN_FIELDS = ("input_other", "input_cached", "output", "reasoning", "cache_write")
_COUNTERS = ("calls", "failed", "canceled", "input_other", "input_cached", "output",
             "reasoning", "cache_write", "cache_reported", "usage_reported")
_EVENT_FIELDS = ("id", "timestamp", "plugin", "provider", "model", "result",
                 "input_other", "input_cached", "output", "reasoning", "cache_write",
                 "tokens", "latency_ms", "ttft_ms", "tps", "estimated_cost")
_RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})$")


def _number(value, name: str, *, integer: bool = False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a nonnegative finite number")
    try:
        finite = math.isfinite(value)
    except OverflowError as exc:
        raise ValueError(f"{name} is out of range") from exc
    if not finite or value < 0 or (integer and int(value) != value):
        raise ValueError(f"{name} must be a nonnegative finite number")
    return int(value) if integer else float(value)


def _nullable(value, name: str, *, integer: bool = False):
    return None if value is None else _number(value, name, integer=integer)


def _epoch(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite epoch timestamp")
    try:
        datetime.fromtimestamp(value, _TZ)
    except (ValueError, OverflowError, OSError) as exc:
        raise ValueError(f"invalid {name}") from exc
    return float(value)


def _midnight(epoch: float) -> float:
    return datetime.fromtimestamp(epoch, _TZ).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _parse_time(value, name: str) -> float:
    if not isinstance(value, str) or not _RFC3339.fullmatch(value):
        raise ValueError(f"{name} must be RFC3339 with a timezone")
    if value[-1:].upper() != "Z" and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
        raise ValueError(f"invalid {name} timezone offset")
    try:
        parsed = datetime.fromisoformat(value.upper().replace("Z", "+00:00"))
        return _epoch(parsed.timestamp(), name)
    except (ValueError, OverflowError, OSError) as exc:
        raise ValueError(f"invalid {name}") from exc


def _positive_int(value, name: str) -> int:
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        value = int(value)
    value = _number(value, name, integer=True)
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def _query(query: Mapping, now: float):
    if not isinstance(query, Mapping):
        raise ValueError("query must be a mapping")
    days, result = query.get("days", "30"), query.get("result", "all")
    group, speed = query.get("group", "model"), query.get("speed_mode", "total")
    if days not in ("1", "7", "30", "0"):
        raise ValueError("days must be 1, 7, 30 or 0")
    if result not in ("all", "success", "failed", "canceled"):
        raise ValueError("invalid result")
    if group not in ("model", "plugin") or speed not in ("total", "ttft"):
        raise ValueError("invalid group or speed_mode")
    explicit_start, explicit_end = query.get("start") is not None, query.get("end") is not None
    start = (_parse_time(query["start"], "start") if explicit_start else
             None if days == "0" else _midnight(now) - (int(days) - 1) * 86400)
    end = _parse_time(query["end"], "end") if explicit_end else now
    if start is not None and (start > end or (start == end and (explicit_start or explicit_end))):
        raise ValueError("start must precede end")
    out = {"days": days, "result": result, "group": group,
           "page": _positive_int(query.get("page", 1), "page"),
           "page_size": min(200, _positive_int(query.get("page_size", 50), "page_size")),
           "speed_mode": speed}
    for name in ("plugin", "provider", "model"):
        text = query.get(name, "")
        if not isinstance(text, str):
            raise ValueError(f"{name} must be a string")
        out[name] = text
    # Omitted bounds stay omitted: inferred times echoed as explicit would
    # exclude daily legacy history on a subsequent otherwise identical request.
    if explicit_start:
        out["start"] = datetime.fromtimestamp(start, _TZ).isoformat()
    if explicit_end:
        out["end"] = datetime.fromtimestamp(end, _TZ).isoformat()
    return out, start, end, explicit_start, explicit_end


def _matches(row: dict, query: dict) -> bool:
    return all(not query[key] or row[key] == query[key] for key in ("plugin", "provider", "model"))


def _sum_known(rows, key: str):
    values = [row[key] for row in rows if row.get(key) is not None]
    return sum(values) if values else None


def _tps(row: dict, mode: str):
    duration = row.get("latency_ms")
    if row.get("result") != "success" or not row.get("output") or duration is None or duration <= 0:
        return None, None
    if mode == "ttft":
        first = row.get("ttft_ms")
        if first is None or first <= 0 or first >= duration or duration - first < 100:
            return None, None
        duration -= first
    return row["output"] * 1000 / duration, duration


def _cost(row: dict, prices: dict):
    price = prices.get((row["provider"], row["model"]))
    if price is None:
        price = prices.get(("", row["model"]))
    if price is None or not row.get("usage_reported"):
        return None
    total = 0.0
    price_counts = dict(row)
    price_counts["input_other"] = max(0, row["input_other"] - (row.get("cache_write") or 0))
    for token, field in (("input_other", "input"), ("input_cached", "cache_read"),
                         ("cache_write", "cache_write"), ("output", "output")):
        count = price_counts.get(token)
        if count:
            rate = price[field]
            if rate is None:
                return None
            total += count * rate / 1_000_000
    return total


class ReconciliationRequired(ValueError):
    requires_reconciliation = True


class EventStore:
    """Short, locked transactions; errors deliberately reach the caller.

    clear() preserves prices. keep_days=0 disables age pruning; max_events=0
    removes all details. Caller-owned daily aggregates are never modified.
    """

    def __init__(self, path: Path | str = ":memory:"):
        if not isinstance(path, (Path, str)) or not str(path):
            raise ValueError("path must name a database or :memory:")
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._closed = False
        try:
            self._db.execute("PRAGMA busy_timeout=5000")
            self._db.execute("PRAGMA temp_store=MEMORY")
            self._migrate()
        except BaseException:
            self._db.close()
            raise

    def _migrate(self):
        self._db.execute("BEGIN IMMEDIATE")
        try:
            version = self._db.execute("PRAGMA user_version").fetchone()[0]
            if version > _VERSION:
                raise ValueError("event history schema is newer than this plugin")
            if version == 1 and self._db.execute("SELECT COUNT(*) FROM events").fetchone()[0]:
                raise ReconciliationRequired("Existing v1 detail records have no daily inclusion proof; preserve and reconcile the database before importing details")
            if version == 0:
                existing = self._db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
                if existing:
                    raise ValueError("unversioned event history schema is not recognized")
                self._db.execute("""CREATE TABLE events (
                    event_id TEXT PRIMARY KEY NOT NULL, timestamp REAL NOT NULL,
                    plugin TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
                    result TEXT NOT NULL CHECK(result IN ('success','failed','canceled')),
                    input_other INTEGER NOT NULL, input_cached INTEGER NOT NULL,
                    output INTEGER NOT NULL, reasoning INTEGER, cache_write INTEGER,
                    cache_reported INTEGER NOT NULL, usage_reported INTEGER NOT NULL,
                    latency_ms REAL, ttft_ms REAL)""")
                self._db.execute("CREATE INDEX events_time ON events(timestamp DESC,event_id DESC)")
                self._db.execute("""CREATE TABLE prices (
                    provider TEXT NOT NULL, model TEXT NOT NULL,
                    input REAL, cache_read REAL, cache_write REAL, output REAL,
                    PRIMARY KEY(provider,model))""")
                self._db.execute(f"PRAGMA user_version={_VERSION}")
            if version < 2:
                self._db.execute("CREATE TABLE IF NOT EXISTS event_checkpoints (token TEXT PRIMARY KEY NOT NULL, event_ids TEXT NOT NULL)")
                self._db.execute(f"PRAGMA user_version={_VERSION}")
            if version < 3:
                self._db.execute("CREATE TABLE IF NOT EXISTS catalog_prices (model TEXT PRIMARY KEY NOT NULL, input REAL, cache_read REAL, cache_write REAL, output REAL, source_provider TEXT NOT NULL)")
                self._db.execute("CREATE TABLE IF NOT EXISTS catalog_state (id INTEGER PRIMARY KEY CHECK(id=1), source TEXT NOT NULL, updated_at REAL NOT NULL)")
                self._db.execute(f"PRAGMA user_version={_VERSION}")
            catalog_columns = {row[1] for row in self._db.execute("PRAGMA table_info(catalog_prices)")}
            state_columns = {row[1] for row in self._db.execute("PRAGMA table_info(catalog_state)")}
            if not {"model", "source_provider", *_PRICE_FIELDS} <= catalog_columns or not {"id", "source", "updated_at"} <= state_columns:
                raise ValueError("invalid price catalog schema")
            checkpoint_columns = {row[1] for row in self._db.execute("PRAGMA table_info(event_checkpoints)")}
            if not {"token", "event_ids"} <= checkpoint_columns:
                raise ValueError("invalid event checkpoint schema")
            columns = {row[1] for row in self._db.execute("PRAGMA table_info(events)")}
            required = {"event_id", "timestamp", "plugin", "provider", "model", "result",
                        "input_other", "input_cached", "output", "reasoning", "cache_write",
                        "cache_reported", "usage_reported", "latency_ms", "ttft_ms"}
            price_columns = {row[1] for row in self._db.execute("PRAGMA table_info(prices)")}
            if not required <= columns or not {"provider", "model", *_PRICE_FIELDS} <= price_columns:
                raise ValueError("invalid event history schema")
            self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK")
            raise

    def _mutate(self, operation):
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                operation()
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def append(self, call: Call) -> None:
        event_id = getattr(call, "event_id", None)
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("call.event_id must be a stable nonempty string")
        result = getattr(call, "result", None) or ("success" if call.ok else "failed")
        if result not in ("success", "failed", "canceled"):
            raise ValueError("invalid call result")
        usage = call.usage
        counts = [_number(getattr(usage, name), name, integer=True)
                  for name in ("input_other", "input_cached", "output")]
        optional = [_nullable(getattr(usage, name, None), name, integer=True)
                    for name in ("reasoning", "cache_write")]
        latency = _nullable(getattr(call.shape, "ms", None), "latency_ms")
        first = _nullable(getattr(call, "ttft_ms", None), "ttft_ms")
        text = [call.plugin, call.provider_id, call.model]
        if not all(isinstance(value, str) for value in text):
            raise ValueError("plugin, provider and model must be strings")
        values = [event_id, _epoch(call.timestamp, "timestamp"), *text, result,
                  *counts, *optional, int(bool(usage.cache_reported)),
                  int(bool(getattr(usage, "usage_reported", True))), latency, first]
        self._mutate(lambda: self._db.execute(
            "INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(event_id) DO NOTHING", values))

    def prune(self, keep_days: int = 60, max_events: int = 20000, now: float | None = None) -> None:
        keep_days = _number(keep_days, "keep_days", integer=True)
        max_events = _number(max_events, "max_events", integer=True)
        now = _epoch(time.time() if now is None else now, "now")

        def operation():
            if keep_days:
                cutoff = _midnight(now) - (keep_days - 1) * 86400
                self._db.execute("DELETE FROM events WHERE timestamp < ?", (cutoff,))
            self._db.execute("DELETE FROM events WHERE event_id IN "
                             "(SELECT event_id FROM events ORDER BY timestamp DESC,event_id DESC LIMIT -1 OFFSET ?)",
                             (max_events,))
        self._mutate(operation)

    def covered_ids(self, token: str) -> set[str]:
        """Resolve the exact detail batch atomically named by the JSON snapshot."""
        if not token:
            return set()
        with self._lock:
            row = self._db.execute("SELECT event_ids FROM event_checkpoints WHERE token = ?", (token,)).fetchone()
        if row is None:
            raise ValueError("daily history references an unavailable event checkpoint")
        values = json.loads(row[0])
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise ValueError("invalid event checkpoint contents")
        return set(values)

    def prepare_checkpoint(self, protected_token: str = "", included_ids=()) -> str:
        """Persist inclusion proof before the caller atomically publishes its JSON."""
        token = str(uuid4())
        included = set(included_ids)
        if any(not isinstance(value, str) or not value for value in included):
            raise ValueError("included event IDs must be nonempty strings")
        def operation():
            ids = sorted(included | {row[0] for row in self._db.execute("SELECT event_id FROM events")})
            self._db.execute("DELETE FROM event_checkpoints WHERE token != ?", (protected_token,))
            self._db.execute("INSERT INTO event_checkpoints VALUES (?,?)", (token, json.dumps(ids)))
        self._mutate(operation)
        return token

    def finalize_checkpoint(self, token: str) -> None:
        """Release superseded proofs only after the JSON publish succeeds."""
        self._mutate(lambda: self._db.execute("DELETE FROM event_checkpoints WHERE token != ?", (token,)))

    def uncovered(self, token: str):
        """Recover committed details absent from the last durable daily snapshot."""
        from .stats import Call, Shape, Usage
        covered = self.covered_ids(token)
        events, _, _ = self._snapshot()
        return [Call(
            plugin=row["plugin"], provider_id=row["provider"], model=row["model"],
            timestamp=row["timestamp"], usage=Usage(
                row["input_other"], row["input_cached"], row["output"], row["cache_reported"],
                reasoning=row["reasoning"], cache_write=row["cache_write"], usage_reported=row["usage_reported"]),
            ok=row["result"] == "success", result=row["result"], event_id=row["event_id"],
            ttft_ms=row["ttft_ms"], shape=Shape(ms=row["latency_ms"]) if row["latency_ms"] is not None else None,
        ) for row in events if row["event_id"] not in covered]

    def discard(self, event_ids) -> None:
        """Remove a selected unsaved batch without touching retained history or prices."""
        ids = list(event_ids)
        if any(not isinstance(value, str) or not value for value in ids):
            raise ValueError("event IDs must be nonempty strings")
        if ids:
            self._mutate(lambda: self._db.executemany(
                "DELETE FROM events WHERE event_id = ?", ((value,) for value in ids)))

    def clear(self) -> None:
        self._mutate(lambda: self._db.execute("DELETE FROM events"))

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._db.close()
                self._closed = True

    def save_prices(self, rows: list[dict]) -> None:
        if not isinstance(rows, list):
            raise ValueError("prices must be a list")
        clean, seen = [], set()
        for row in rows:
            if not isinstance(row, dict) or set(row) - {"model", "provider", *_PRICE_FIELDS}:
                raise ValueError("invalid price row fields")
            model, provider = row.get("model"), row.get("provider", "")
            if not isinstance(model, str) or not model or not isinstance(provider, str):
                raise ValueError("price model/provider must be strings; model cannot be empty")
            if (provider, model) in seen:
                raise ValueError("duplicate price model/provider")
            seen.add((provider, model))
            clean.append((provider, model, *[_nullable(row.get(key), key) for key in _PRICE_FIELDS]))

        def operation():
            self._db.execute("DELETE FROM prices")
            self._db.executemany("INSERT INTO prices VALUES (?,?,?,?,?,?)", clean)
        self._mutate(operation)

    def save_catalog(self, rows: list[dict], updated_at: float | None = None) -> None:
        """Atomically replace public reference prices without changing manual overrides."""
        if not isinstance(rows, list) or not rows or len(rows) > 20000:
            raise ValueError("price catalog must contain 1 through 20000 rows")
        stamp = _epoch(time.time() if updated_at is None else updated_at, "updated_at")
        clean, seen = [], set()
        allowed = {"model", "provider", "source_provider", *_PRICE_FIELDS}
        for row in rows:
            if not isinstance(row, dict) or set(row) - allowed:
                raise ValueError("invalid catalog fields")
            model, origin = row.get("model"), row.get("source_provider")
            if not isinstance(model, str) or not model.strip() or len(model) > 512 or row.get("provider", "") != "":
                raise ValueError("invalid reference model identity")
            if not isinstance(origin, str) or not origin.strip() or len(origin) > 512 or model in seen:
                raise ValueError("invalid or duplicate catalog model")
            seen.add(model)
            values = {key: _nullable(row.get(key), key) for key in _PRICE_FIELDS}
            if values["input"] is None or values["output"] is None:
                raise ValueError("reference input and output rates are required")
            clean.append((model, *[values[key] for key in _PRICE_FIELDS], origin))
        def operation():
            self._db.execute("DELETE FROM catalog_prices")
            self._db.executemany("INSERT INTO catalog_prices VALUES (?,?,?,?,?,?)", clean)
            self._db.execute("INSERT INTO catalog_state VALUES (1,?,?) ON CONFLICT(id) DO UPDATE SET source=excluded.source,updated_at=excluded.updated_at", ("models.dev", stamp))
        self._mutate(operation)

    @staticmethod
    def _pricing(rows: list[dict], prices: list[dict], overview: dict, catalog_info: dict, query: dict) -> dict:
        price_map = {(price["provider"], price["model"]): price for price in prices}
        grouped = {}
        for row in rows:
            identity = (row["provider"], row["model"])
            target = grouped.setdefault(identity, {"provider": identity[0], "model": identity[1], "calls": 0,
                "tokens": None, "estimated_cost": None, "priced_calls": 0, "unpriced_calls": 0})
            target["calls"] += row["calls"]
            if row["tokens"] is not None:
                target["tokens"] = (target["tokens"] or 0) + row["tokens"]
            if row["estimated_cost"] is not None:
                target["estimated_cost"] = (target["estimated_cost"] or 0) + row["estimated_cost"]
                target["priced_calls"] += row["calls"]
            else:
                target["unpriced_calls"] += row["calls"]
        used_models = {model for _, model in grouped}
        for identity, price in price_map.items():
            if query["model"] and identity[1] != query["model"]:
                continue
            if query["provider"] and identity[0] not in ("", query["provider"]):
                continue
            if identity in grouped or (identity[0] == "" and identity[1] in used_models):
                continue
            grouped[identity] = {"provider": identity[0], "model": identity[1], "calls": 0, "tokens": 0,
                "estimated_cost": 0, "priced_calls": 0, "unpriced_calls": 0}
        for identity, row in grouped.items():
            price = price_map.get(identity)
            if price is None:
                price = price_map.get(("", row["model"]))
            row["price"] = {key: price[key] for key in _PRICE_FIELDS} if price is not None else None
            row["price_source"] = price["price_source"] if price is not None else None
            row["source_provider"] = price.get("source_provider") if price is not None else None
        result = sorted(grouped.values(), key=lambda row: (-row["calls"], row["model"], row["provider"]))
        return {"total_cost": overview["estimated_cost"] if overview["calls"] else 0,
            "priced_calls": overview["priced_calls"], "unpriced_calls": overview["unpriced_calls"],
            "available_prices": len({price["model"] for price in prices if any(price[key] is not None for key in _PRICE_FIELDS)}),
            "updated_at": catalog_info["updated_at"], "source": "models.dev", "rows": result}

    def _snapshot(self):
        # Read prices and events under a single consistent SQLite snapshot.
        with self._lock:
            self._db.execute("BEGIN")
            try:
                events = [dict(row) for row in self._db.execute(
                    "SELECT * FROM events ORDER BY timestamp DESC,event_id DESC")]
                manual = [dict(row) for row in self._db.execute("SELECT * FROM prices ORDER BY provider,model")]
                catalog = [dict(row) for row in self._db.execute("SELECT * FROM catalog_prices ORDER BY model")]
                state = self._db.execute("SELECT source,updated_at FROM catalog_state WHERE id=1").fetchone()
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        effective = {(row["model"]): {**row, "provider": "", "price_source": "models.dev"} for row in catalog}
        prices = list(effective.values())
        for row in manual:
            if not row["provider"]:
                prices = [price for price in prices if not (price["provider"] == "" and price["model"] == row["model"])]
            prices.append({**row, "price_source": "manual", "source_provider": None})
        info = {"source": "models.dev", "updated_at": state["updated_at"] if state else None}
        return events, prices, info

    @staticmethod
    def _merge(events: list[dict], aggregates: dict, warnings: set[str], covered_ids=None):
        contributions = defaultdict(lambda: defaultdict(int))
        for row in events:
            if covered_ids is not None and row["event_id"] not in covered_ids:
                continue
            day = datetime.fromtimestamp(row["timestamp"], _TZ).date().isoformat()
            cell = contributions[(row["plugin"], day, row["provider"], row["model"])]
            cell["calls"] += 1
            cell["failed"] += row["result"] == "failed"
            cell["canceled"] += row["result"] == "canceled"
            for name in _COUNTERS[3:]:
                cell[name] += row.get(name) or 0
        legacy, visited = [], set()
        for plugin, days in aggregates.items():
            for day, models in days.items():
                try:
                    date = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=_TZ)
                except (TypeError, ValueError) as exc:
                    raise ValueError("invalid aggregate day") from exc
                for model_key, bucket in models.items():
                    provider, sep, model = model_key.partition("|")
                    key = (plugin, day, provider, model if sep else "")
                    visited.add(key)
                    detail = contributions[key]
                    usage = bucket.usage  # Actual Bucket objects, never raw JSON.
                    raw = {"calls": bucket.calls, "failed": bucket.failed,
                           "canceled": getattr(bucket, "canceled", 0),
                           "input_other": usage.input_other, "input_cached": usage.input_cached,
                           "output": usage.output, "reasoning": getattr(usage, "reasoning", None),
                           "cache_write": getattr(usage, "cache_write", None),
                           "cache_reported": usage.cache_reported,
                           "usage_reported": getattr(usage, "usage_reported", bucket.calls)}
                    residual = {}
                    for name, value in raw.items():
                        if value is None:
                            residual[name] = None
                        else:
                            difference = value - detail[name]
                            if difference < 0:
                                warnings.add("Daily aggregates and detail contributions differ; negative residuals were clamped.")
                            residual[name] = max(0, difference)
                    if not residual["calls"]:
                        if any(residual[name] for name in ("input_other", "input_cached", "output", "reasoning", "cache_write")):
                            warnings.add("Daily aggregates contain token residuals without calls; totals retain those residuals.")
                        else:
                            continue
                    residual.update(plugin=plugin, provider=key[2], model=key[3],
                                    timestamp=date.timestamp(), legacy=True)
                    residual["success"] = max(0, residual["calls"] - residual["failed"] - residual["canceled"])
                    legacy.append(residual)
        if any(key not in visited for key in contributions):
            warnings.add("Some details have no matching daily aggregate; their contributions are included once.")
        return legacy

    @staticmethod
    def _prepare(row: dict, price_map: dict, mode: str) -> dict:
        row = dict(row)
        # Old JSON has no usage_reported counter. Positive retained counters are
        # still known history; do not hide them when details are pruned/restarted.
        if row.get("legacy") and not row["usage_reported"] and any(row[key] for key in ("input_other", "input_cached", "output")):
            row["usage_reported"] = 1
        if not row.get("legacy"):
            row["id"] = row.pop("event_id")
            row["calls"] = 1
            for result in ("success", "failed", "canceled"):
                row[result] = int(row["result"] == result)
        # Follow Usage.total: reasoning is contained in output and cache writes
        # annotate the existing uncached-input column, not additional tokens.
        row["tokens"] = (row["input_other"] + row["input_cached"] + row["output"]
                         if row["usage_reported"] else None)
        row["_write_exceeds_input"] = (row["cache_write"] or 0) > row["input_other"]
        row["estimated_cost"] = None if row["_write_exceeds_input"] else _cost(row, price_map)
        if not row["usage_reported"]:
            for key in ("input_other", "input_cached", "output"):
                row[key] = None
        elif not row["cache_reported"]:
            row["input_cached"] = None
        row["tps"], row["_tps_duration"] = _tps(row, mode)
        return row

    def _select(self, query: Mapping, aggregates: dict, now: float | None, covered_ids=None):
        now = _epoch(time.time() if now is None else now, "now")
        normalized, start, end, explicit_start, explicit_end = _query(query, now)
        events, prices, catalog_info = self._snapshot()
        warnings: set[str] = set()
        legacy = self._merge(events, aggregates, warnings, covered_ids)
        price_map = {(row["provider"], row["model"]): row for row in prices}
        options = {name + "s": set() for name in ("plugin", "model", "provider")}
        selected, details, excluded = [], [], 0
        for raw in events:
            if not ((start is None or raw["timestamp"] >= start) and raw["timestamp"] < end):
                continue
            for name in ("plugin", "model", "provider"):
                options[name + "s"].add(raw[name])
            if _matches(raw, normalized) and (normalized["result"] == "all" or raw["result"] == normalized["result"]):
                row = self._prepare(raw, price_map, normalized["speed_mode"])
                selected.append(row)
                details.append(row)
        for raw in legacy:
            day_start, day_end = raw["timestamp"], raw["timestamp"] + 86400
            if (start is not None and day_end <= start) or day_start >= end:
                continue
            for name in ("plugin", "model", "provider"):
                options[name + "s"].add(raw[name])
            if not _matches(raw, normalized):
                continue
            partial = ((explicit_start and start is not None and start > day_start) or
                       (explicit_end and end < day_end))
            if normalized["result"] != "all" or partial:
                excluded += raw["calls"]
                continue
            selected.append(self._prepare(raw, price_map, normalized["speed_mode"]))
        if excluded:
            warnings.add("Legacy daily calls excluded: result or partial-day filters cannot recover their exact tokens or times.")
        legacy_calls = sum(row["calls"] for row in selected if row.get("legacy"))
        if legacy_calls:
            warnings.add("Legacy history has daily granularity; latency, TTFT and TPS use detail samples only.")
        if any(row["_write_exceeds_input"] for row in selected):
            warnings.add("Cache-write metadata exceeds normalized input; totals retain Usage.total and affected calls are not priced.")
        if any(not row["usage_reported"] for row in selected):
            warnings.add("Some calls did not report usage; token and price sums cover reported samples only.")
        if any(row.get("input_cached") is None for row in selected):
            warnings.add("Some calls did not report cache usage; cache ratios use reported samples only.")
        return normalized, start, end, selected, details, prices, options, {
            "legacy_calls": legacy_calls, "detail_calls": len(details), "excluded_legacy_calls": excluded,
            "detail_start": min((row["timestamp"] for row in events), default=None), "warnings": warnings}, catalog_info

    def dashboard(self, query: Mapping, aggregates: dict, now: float | None = None, covered_ids=None) -> dict:
        query, start, end, rows, details, prices, options, coverage, catalog_info = self._select(query, aggregates, now, covered_ids)
        counts = {key: sum(row[key] for row in rows) for key in ("calls", "success", "failed", "canceled")}
        overview = {**counts, **{key: _sum_known(rows, key) for key in ("tokens", *_TOKEN_FIELDS)}}
        if not rows:
            overview.update(tokens=0, input_other=0, input_cached=0, output=0)
        cache_rows = [row for row in rows if row.get("cache_reported") and row.get("usage_reported")
                      and (not row.get("legacy") or row["cache_reported"] == row["calls"])]
        if any(row.get("legacy") and 0 < row["cache_reported"] < row["calls"] for row in rows):
            coverage["warnings"].add("Partially cache-reported legacy buckets cannot supply exact cache-ratio denominators; ratios use detail or fully reported daily samples only.")
        cache = sum(row["input_cached"] for row in cache_rows)
        incoming = sum(row["input_other"] + row["input_cached"] for row in cache_rows)
        composition = sum(row["tokens"] for row in cache_rows)
        valid_speed = [row for row in details if row["_tps_duration"] is not None]
        latencies = [row["latency_ms"] for row in details if row["latency_ms"] is not None]
        priced = [row for row in rows if row["estimated_cost"] is not None]
        window_start = start if start is not None else min((row["timestamp"] for row in rows), default=None)
        overview.update(
            cache_hit_rate=min(1.0, cache / incoming) if incoming else 0.0 if cache_rows else None,
            cache_share=min(1.0, cache / composition) if composition else 0.0 if cache_rows else None,
            success_rate=counts["success"] / (counts["success"] + counts["failed"]) if counts["success"] + counts["failed"] else None,
            tps=sum(row["output"] for row in valid_speed) * 1000 / sum(row["_tps_duration"] for row in valid_speed) if valid_speed else None,
            tps_samples=len(valid_speed), rpm=counts["calls"] / max(1.0, (end - window_start) / 60) if window_start is not None and end > window_start else None,
            average_latency_ms=sum(latencies) / len(latencies) if latencies else None,
            latency_samples=len(latencies), estimated_cost=sum(row["estimated_cost"] for row in priced) if priced else None,
            priced_calls=sum(row["calls"] for row in priced),
            unpriced_calls=sum(row["calls"] for row in rows if row["estimated_cost"] is None))
        analysis = {}
        for name in ("plugin", "model", "provider"):
            cells = {}
            for row in rows:
                key = row[name]
                cell = cells.setdefault(key, {"key": key, "label": key, "calls": 0, "tokens": None, "failed": 0})
                cell["calls"] += row["calls"]
                cell["failed"] += row["failed"]
                if row["tokens"] is not None:
                    cell["tokens"] = (cell["tokens"] or 0) + row["tokens"]
            analysis[name + "s"] = sorted(cells.values(), key=lambda cell: (-(cell["tokens"] or 0), cell["key"]))
        timeline, granularity = self._timeline(rows, start, end, coverage["warnings"])
        total, offset = len(details), (query["page"] - 1) * query["page_size"]
        items = [{key: row[key] for key in _EVENT_FIELDS} for row in details[offset:offset + query["page_size"]]]
        coverage["warnings"] = sorted(coverage["warnings"])
        return {"overview": overview, "timeline": timeline, "granularity": granularity,
                "analysis": analysis, "events": {"items": items, "page": query["page"],
                "page_size": query["page_size"], "total": total, "total_pages": math.ceil(total / query["page_size"])},
                "options": {key: sorted(values) for key, values in options.items()},
                "prices": [{key: row[key] for key in ("model", "provider", *_PRICE_FIELDS)} for row in prices if row["price_source"] == "manual"],
                "pricing": self._pricing(rows, prices, overview, catalog_info, query), "coverage": coverage, "query": query}

    @staticmethod
    def _timeline(rows: list[dict], start: float | None, end: float, warnings: set[str]):
        if start is None:
            start = min((row["timestamp"] for row in rows), default=end)
        span = max(0, end - start)
        if any(row.get("legacy") for row in rows) or span > 7 * 86400:
            granularity, step = "day", 86400
        elif span > 86400:
            granularity, step = "hour", 3600
        else:
            granularity, step = "30m", 1800
        lower = math.floor((start + 28800) / step) * step - 28800
        points = max(0, math.ceil((end - lower) / step))
        if points > 366:
            factor = math.ceil(points / 366)
            step *= factor
            warnings.add(f"Trend coarsened to {factor} {granularity} intervals per point (maximum 366 points).")
            points = math.ceil((end - lower) / step)
        bins = {lower + i * step: {"timestamp": lower + i * step,
                "label": datetime.fromtimestamp(lower + i * step, _TZ).strftime("%Y-%m-%d" if granularity == "day" else "%m-%d %H:%M"),
                "calls": 0, "tokens": 0, "models": {}, "plugins": {}, "_unknown": False}
                for i in range(points)}
        for row in rows:
            stamp = lower + math.floor((row["timestamp"] - lower) / step) * step
            if stamp not in bins:
                continue
            cell = bins[stamp]
            cell["calls"] += row["calls"]
            if row["tokens"] is None:
                cell["_unknown"] = True
            else:
                cell["tokens"] += row["tokens"]
            for name in ("model", "plugin"):
                key = row[name]
                child = cell[name + "s"].setdefault(key, {"key": key, "label": key, "tokens": None})
                if row["tokens"] is not None:
                    child["tokens"] = (child["tokens"] or 0) + row["tokens"]
        for cell in bins.values():
            if cell.pop("_unknown") and not any(child["tokens"] is not None for child in cell["models"].values()):
                cell["tokens"] = None
            for name in ("models", "plugins"):
                cell[name] = sorted(cell[name].values(), key=lambda child: (-(child["tokens"] or 0), child["key"]))
        return list(bins.values()), granularity

    def export(self, query: Mapping, aggregates: dict, now: float | None = None, covered_ids=None) -> dict:
        # _select fixes now once and reads rows/prices in one SQLite snapshot.
        _, _, _, _, details, _, _, _, _ = self._select(query, aggregates, now, covered_ids)
        stream = io.StringIO(newline="")
        stream.write("\ufeff")
        writer = csv.writer(stream)
        writer.writerow(_EVENT_FIELDS)
        for row in details[:5000]:
            values = []
            for key in _EVENT_FIELDS:
                value = row[key]
                if isinstance(value, str) and (value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n"))):
                    value = "'" + value
                values.append("" if value is None else value)
            writer.writerow(values)
        return {"csv": stream.getvalue(), "returned": min(5000, len(details)),
                "total": len(details), "truncated": len(details) > 5000}
