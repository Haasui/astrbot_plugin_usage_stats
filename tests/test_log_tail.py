"""Bounded binary tail reader and call-log response tests."""
from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_usage_stats.log_tail import append_log_batch, parse_tail_request, read_call_log, read_tail


class LimitedReader(io.BytesIO):
    def read(self, size=-1):
        assert size >= 0, "unbounded reads are forbidden"
        return super().read(size)


class FailingWriter(io.BytesIO):
    def __init__(self, rollback_fails):
        super().__init__(b"before\n")
        self.rollback_fails = rollback_fails
    def write(self, data):
        super().write(data[:3])
        raise OSError("write failed")
    def truncate(self, size=None):
        if self.rollback_fails:
            raise OSError("rollback failed")
        return super().truncate(size)


class TailReaderTests(unittest.TestCase):
    def test_tail_parameter_validation(self):
        self.assertEqual(parse_tail_request(None, 5000), (False, 0))
        self.assertEqual(parse_tail_request("0", 5000), (False, 0))
        self.assertEqual(parse_tail_request("50000", 5000), (False, 5000))
        self.assertEqual(parse_tail_request("-1", 5000), (True, 0))
        self.assertEqual(parse_tail_request("many", 5000), (True, 0))

    def test_write_failure_rolls_back(self):
        stream = FailingWriter(False)
        self.assertEqual(append_log_batch(stream, ["row"]), (False, True))
        self.assertEqual(stream.getvalue(), b"before\n")

    def test_failed_rollback_reports_uncertain_append(self):
        stream = FailingWriter(True)
        self.assertEqual(append_log_batch(stream, ["row"]), (False, False))
        self.assertEqual(stream.getvalue(), b"before\nrow")
    def test_empty_and_small_file(self):
        self.assertEqual(read_tail(LimitedReader(b""), 5, 100), ([], False, 0))
        self.assertEqual(read_tail(LimitedReader(b"a\nb\n"), 5, 100)[:2], (["a", "b"], False))

    def test_line_cap_and_unterminated_last_record(self):
        self.assertEqual(read_tail(LimitedReader(b"a\nb\nc"), 2, 100)[:2], (["b", "c"], True))

    def test_byte_cap_discards_partial_first_record(self):
        lines, truncated, scanned = read_tail(LimitedReader(b"old\nnew\n"), 10, 5)
        self.assertEqual(lines, ["new"])
        self.assertTrue(truncated)
        self.assertLessEqual(scanned, 5)

    def test_exact_byte_limit_and_single_oversized_record(self):
        self.assertEqual(read_tail(LimitedReader(b"a\nb\n"), 5, 4)[:2], (["a", "b"], False))
        lines, truncated, scanned = read_tail(LimitedReader(b"x" * 20), 5, 8)
        self.assertEqual(lines, [])
        self.assertTrue(truncated)
        self.assertEqual(scanned, 8)

    def test_crlf_and_multibyte_utf8(self):
        self.assertEqual(read_tail(LimitedReader("旧\r\n新🙂\r\n".encode()), 2, 100)[:2], (["旧", "新🙂"], False))
        lines, truncated, _ = read_tail(LimitedReader("🙂\n".encode()), 2, 2)
        self.assertEqual(lines, [])
        self.assertTrue(truncated)

    def test_api_states_disabled_empty_ready_truncated_partial_and_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "calls.log"
            self.assertEqual(read_call_log(path, False, True, 2, 100)["state"], "disabled")
            self.assertEqual(read_call_log(path, True, True, 2, 100)["state"], "empty")
            path.write_bytes(b"a\nb\nc\n")
            ready = read_call_log(path, True, True, 3, 100)
            self.assertEqual((ready["state"], ready["lines"]), ("ready", ["a", "b", "c"]))
            self.assertEqual(read_call_log(path, True, True, 2, 100)["state"], "truncated")
            partial = read_call_log(path, True, False, 2, 100)
            self.assertEqual((partial["state"], partial["error_code"]), ("partial", "flush_failed"))
            path.unlink()
            path.mkdir()
            failed = read_call_log(path, True, True, 2, 100)
            self.assertEqual((failed["state"], failed["error_code"]), ("unavailable", "read_failed"))
        self.assertEqual(read_call_log(None, True, True, 2, 100)["error_code"], "path_unavailable")


if __name__ == "__main__":
    unittest.main()
