"""Bounded binary tail reader for newline-delimited call logs."""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO


def parse_tail_request(value, max_lines):
    """Return (invalid, bounded tail); zero means aggregate-stats request."""
    if value in (None, "", 0, "0"):
        return False, 0
    try:
        tail = int(value)
    except (TypeError, ValueError):
        return True, 0
    if tail <= 0:
        return True, 0
    return False, min(tail, max_lines)


def read_tail(stream: BinaryIO, max_lines: int, max_bytes: int) -> tuple[list[str], bool, int]:
    """Read at most max_bytes from the end and return complete decoded lines.

    The byte budget includes all chunks read. A partial first line is discarded.
    """
    if max_lines < 0 or max_bytes < 0:
        raise ValueError("limits must be non-negative")
    if max_lines == 0 or max_bytes == 0:
        return [], False, 0

    stream.seek(0, 2)
    end = stream.tell()
    position = end
    chunks: list[bytes] = []
    scanned = 0
    newline_count = 0
    budget_hit = False
    chunk_size = 64 * 1024
    while position > 0 and newline_count <= max_lines and scanned < max_bytes:
        amount = min(chunk_size, position, max_bytes - scanned)
        position -= amount
        stream.seek(position)
        data = stream.read(amount)
        if not data:
            break
        chunks.append(data)
        scanned += len(data)
        newline_count += data.count(b"\n")
        if scanned >= max_bytes and position > 0 and newline_count <= max_lines:
            budget_hit = True
            break

    raw = b"".join(reversed(chunks))
    truncated = position > 0 and (budget_hit or newline_count > max_lines)
    # A trailing delimiter terminates the final record; it is not another record.
    if raw.endswith(b"\n"):
        raw = raw[:-1]
    records = raw.split(b"\n") if raw else []
    if position > 0 and records:
        # The first record may start mid-line at either limit.
        records = records[1:]
    elif position > 0 and not records:
        truncated = True
    if len(records) > max_lines:
        records = records[-max_lines:]
        truncated = True
    # CRLF is kept compatible with splitlines() behavior.
    lines = [line.removesuffix(b"\r").decode("utf-8", errors="replace") for line in records]
    return lines, truncated, scanned


def append_log_batch(stream: BinaryIO, lines: list[str]) -> tuple[bool, bool]:
    """Append a batch; return (write_ok, rollback_ok) after write failure."""
    stream.seek(0, 2)
    original_size = stream.tell()
    data = ("\n".join(lines) + "\n").encode("utf-8")
    try:
        stream.write(data)
        stream.flush()
        return True, True
    except OSError:
        try:
            stream.truncate(original_size)
            stream.flush()
            return False, True
        except OSError:
            return False, False


def read_call_log(path: Path | None, enabled: bool, flush_ok: bool, max_lines: int, max_bytes: int) -> dict:
    """Build a safe, stable API payload for a call-log tail request."""
    payload = {"enabled": bool(enabled), "path": str(path) if path else "", "lines": [], "state": "ready", "returned": 0, "truncated": False, "error_code": ""}
    if not enabled:
        payload["state"] = "disabled"
        return payload
    if path is None:
        payload.update(state="unavailable", error_code="path_unavailable")
        return payload
    try:
        with path.open("rb") as stream:
            lines, truncated, _ = read_tail(stream, max_lines, max_bytes)
        payload.update(lines=lines, returned=len(lines), truncated=truncated, state="truncated" if truncated else ("ready" if lines else "empty"))
    except FileNotFoundError:
        payload["state"] = "empty"
    except OSError:
        payload.update(state="unavailable", error_code="read_failed")
    if not flush_ok and payload["state"] != "unavailable":
        payload.update(state="partial", error_code="flush_failed")
    return payload
