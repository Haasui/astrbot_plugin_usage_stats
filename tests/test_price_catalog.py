"""Offline standard-library tests: python -m unittest discover -s tests -p test_price_catalog.py."""

from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import price_catalog as catalog


def model(model_id="exact-model", input_price=1, output_price=2, **cache):
    return {"id": model_id, "cost": {"input": input_price,
                                     "output": output_price, **cache}}


def provider(*models):
    return {"models": {item["id"]: item for item in models}}


class ParseCatalogTests(unittest.TestCase):
    def test_real_nested_schema_units_and_output_contract(self):
        # Shape and fields observed at https://models.dev/api.json.
        # Pricing there is already USD per million tokens.
        payload = {"openai": {
            "id": "openai", "env": ["OPENAI_API_KEY"], "name": "OpenAI",
            "models": {"gpt-4o-mini": {
                "id": "gpt-4o-mini", "name": "GPT-4o mini", "family": "gpt-mini",
                "limit": {"context": 128000, "output": 16384},
                "cost": {"input": 0.15, "output": 0.6, "cache_read": 0.075},
            }},
        }}
        self.assertEqual(catalog.parse_catalog(payload), [{
            "model": "gpt-4o-mini", "provider": "", "input": 0.15,
            "output": 0.6, "cache_read": 0.075, "cache_write": None,
            "source_provider": "openai",
        }])

    def test_zero_is_not_missing_and_numbers_become_floats(self):
        rows = catalog.parse_catalog({"openai": provider(
            model("zero", 0, 0, cache_read=0, cache_write=0), model("unknown"))})
        self.assertEqual([row["model"] for row in rows], ["unknown", "zero"])
        self.assertIsNone(rows[0]["cache_read"])
        self.assertIsNone(rows[0]["cache_write"])
        for field in ("input", "output", "cache_read", "cache_write"):
            self.assertEqual(rows[1][field], 0.0)
            self.assertIsInstance(rows[1][field], float)

    def test_missing_input_or_output_excludes_model(self):
        payload = {"seller": {"models": {
            "no-cost": {"id": "no-cost"},
            "input-only": {"cost": {"input": 0}},
            "output-only": {"cost": {"output": 0}},
            "null-cost": {"cost": None},
            "list-cost": {"cost": [1, 2]},
        }}}
        self.assertEqual(catalog.parse_catalog(payload), [])

    def test_invalid_required_prices_are_rejected(self):
        invalid = [True, False, None, "1.2", -1, float("nan"),
                   float("inf"), -float("inf"), [], {}, 10 ** 400]
        for value in invalid:
            for field in ("input", "output"):
                with self.subTest(value=repr(value), field=field):
                    item = model()
                    item["cost"][field] = value
                    self.assertEqual(catalog.parse_catalog({"seller": provider(item)}), [])

    def test_invalid_optional_prices_remain_unknown(self):
        for value in [True, "0", -1, float("nan"), float("inf"), None]:
            with self.subTest(value=repr(value)):
                item = model(cache_read=value, cache_write=value)
                row, = catalog.parse_catalog({"seller": provider(item)})
                self.assertIsNone(row["cache_read"])
                self.assertIsNone(row["cache_write"])

    def test_original_provider_beats_cheaper_reseller(self):
        payload = {
            "reseller": provider(model("gpt-4o-mini", 0.001, 0.002)),
            "openai": provider(model("gpt-4o-mini", 0.15, 0.6)),
        }
        row, = catalog.parse_catalog(payload)
        self.assertEqual(row["source_provider"], "openai")
        self.assertEqual((row["input"], row["output"]), (0.15, 0.6))
        self.assertEqual(row["provider"], "")

    def test_explicit_original_source_ids_are_supported(self):
        for source in ["openai", "anthropic", "google", "deepseek", "xai",
                       "mistral", "cohere", "moonshotai", "zhipuai", "zai",
                       "minimax", "alibaba", "qwen"]:
            with self.subTest(source=source):
                row, = catalog.parse_catalog({
                    "aaa-reseller": provider(model(input_price=0.001)),
                    source: provider(model(input_price=4)),
                })
                self.assertEqual(row["source_provider"], source)
                self.assertEqual(row["input"], 4.0)

    def test_identical_resellers_merge_with_stable_source(self):
        a = provider(model(cache_read=0.25))
        z = provider(model(cache_read=0.25))
        first = catalog.parse_catalog({"z-seller": z, "a-seller": a})
        second = catalog.parse_catalog({"a-seller": a, "z-seller": z})
        self.assertEqual(first, second)
        self.assertEqual(first[0]["source_provider"], "a-seller")

    def test_conflicting_resellers_skip_instead_of_choosing_cheapest(self):
        self.assertEqual(catalog.parse_catalog({
            "a": provider(model(input_price=0.001)),
            "z": provider(model(input_price=4)),
        }), [])

    def test_conflicting_originals_skip_without_priority_order(self):
        payload = {"alibaba": provider(model("same", 1, 2)),
                   "qwen": provider(model("same", 2, 2)),
                   "seller": provider(model("same", 1, 2))}
        self.assertEqual(catalog.parse_catalog(payload), [])
        self.assertEqual(catalog.parse_catalog(dict(reversed(list(payload.items())))), [])

    def test_identical_originals_merge_stably(self):
        row, = catalog.parse_catalog({
            "qwen": provider(model()), "alibaba": provider(model()),
            "a-seller": provider(model(input_price=0)),
        })
        self.assertEqual(row["source_provider"], "alibaba")

    def test_cache_price_disagreement_is_ambiguous(self):
        for a, b in [(None, 0), (1, 2)]:
            with self.subTest(a=a, b=b):
                self.assertEqual(catalog.parse_catalog({
                    "a": provider(model(cache_read=a)),
                    "b": provider(model(cache_read=b)),
                }), [])
        self.assertEqual(catalog.parse_catalog({
            "zai": provider(model(cache_write=0)),
            "zhipuai": provider(model(cache_write=1)),
        }), [])

    def test_exact_ids_no_dates_removed_namespace_stripped_or_fuzzy_matching(self):
        ids = ["claude-sonnet-4-20250514", "claude-sonnet-4", "openai/gpt-4o",
               "GPT-4o", "gpt-4o", " gpt-4o "]
        payload = {"seller": provider(*(model(name) for name in ids))}
        rows = catalog.parse_catalog(payload)
        self.assertEqual([row["model"] for row in rows], sorted(ids))
        self.assertNotIn("claude-sonnet", [row["model"] for row in rows])

    def test_model_id_wins_without_emitting_key_alias(self):
        row, = catalog.parse_catalog({"seller": {"models": {
            "incorrect-key": model("exact-id-20260101"),
        }}})
        self.assertEqual(row["model"], "exact-id-20260101")

    def test_missing_or_empty_model_id_uses_key(self):
        rows = catalog.parse_catalog({"seller": {"models": {
            "fallback-a": {"cost": {"input": 1, "output": 2}},
            "fallback-b": model(""),
        }}})
        self.assertEqual([row["model"] for row in rows], ["fallback-a", "fallback-b"])

    def test_non_string_or_whitespace_id_is_not_aliased(self):
        for value in [False, 123, [], {}, "   "]:
            with self.subTest(value=value):
                self.assertEqual(catalog.parse_catalog({"seller": {"models": {
                    "fallback": model(value),
                }}}), [])

    def test_duplicate_ids_with_conflicting_costs_are_not_aliased(self):
        self.assertEqual(catalog.parse_catalog({"openai": {"models": {
            "key-a": model("same", 1, 2), "key-b": model("same", 2, 2),
        }}}), [])

    def test_embedded_provider_id_cannot_impersonate_original(self):
        payload = {"a-reseller": {"id": "openai", **provider(model())},
                   "z-reseller": provider(model(input_price=3))}
        self.assertEqual(catalog.parse_catalog(payload), [])

    def test_malformed_nested_entries_are_ignored(self):
        self.assertEqual(catalog.parse_catalog({
            "null": None, "list-models": {"models": []}, "missing": {},
            "bad-model": {"models": {"null": None, "list": [], "empty": {}}},
        }), [])
        for value in [None, [], "{}", 0]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                catalog.parse_catalog(value)

    def test_count_limits_fail_explicitly_and_count_unpriced_entries(self):
        with patch.object(catalog, "MAX_MODEL_ENTRIES", 2):
            with self.assertRaisesRegex(ValueError, "model count"):
                catalog.parse_catalog({"a": {"models": {"a": {}, "b": {}}},
                                       "b": {"models": {"c": {}}}})
        with patch.object(catalog, "MAX_PROVIDERS", 1):
            with self.assertRaisesRegex(ValueError, "provider count"):
                catalog.parse_catalog({"a": {}, "b": {}})

    def test_model_and_source_name_limits(self):
        long_name = "x" * (catalog.MAX_NAME_LENGTH + 1)
        for payload in [{"seller": provider(model(long_name))},
                        {long_name: provider(model())}]:
            with self.assertRaisesRegex(ValueError, "name length"):
                catalog.parse_catalog(payload)


class FakeResponse(io.BytesIO):
    def __init__(self, body=b"{}", *, status=200, headers=None, url=catalog.SOURCE_URL):
        super().__init__(body)
        self.status = status
        self.headers = headers or {}
        self.url = url
        self.read_sizes = []
        self.bytes_read = 0

    def geturl(self):
        return self.url

    def read(self, size=-1):
        self.read_sizes.append(size)
        chunk = super().read(size)
        self.bytes_read += len(chunk)
        return chunk


class FetchCatalogTests(unittest.TestCase):
    def fetch_response(self, response):
        opener = Mock()
        opener.open.return_value = response
        with patch.object(catalog, "build_opener", return_value=opener) as factory:
            result = catalog.fetch_catalog()
        return result, opener, factory

    def test_fixed_url_timeout_headers_no_payload_and_no_environment_proxy(self):
        payload = {"openai": provider(model("gpt-4o-mini", 0.15, 0.6))}
        response = FakeResponse(json.dumps(payload).encode())
        rows, opener, factory = self.fetch_response(response)
        self.assertEqual(rows, catalog.parse_catalog(payload))
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://models.dev/api.json")
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)
        self.assertEqual(opener.open.call_args.kwargs, {"timeout": 12})
        self.assertEqual({name.lower() for name, _ in request.header_items()},
                         {"accept", "accept-encoding", "user-agent"})
        proxy, redirect = factory.call_args.args
        self.assertIsInstance(proxy, ProxyHandler)
        self.assertEqual(proxy.proxies, {})
        self.assertIsInstance(redirect, catalog._RejectRedirects)
        self.assertTrue(response.closed)
        self.assertTrue(all(0 < n <= 65536 for n in response.read_sizes))
        self.assertEqual(catalog.MAX_RESPONSE_BYTES, 16 * 1024 * 1024)

    def test_timeout_and_network_failure_propagate(self):
        for error in [TimeoutError("timed out"), URLError("unavailable")]:
            with self.subTest(error=error):
                opener = Mock()
                opener.open.side_effect = error
                with patch.object(catalog, "build_opener", return_value=opener):
                    with self.assertRaises(type(error)) as caught:
                        catalog.fetch_catalog()
                self.assertIs(caught.exception, error)
                self.assertEqual(opener.open.call_args.kwargs["timeout"], 12)

    def test_http_error_propagates(self):
        error = HTTPError(catalog.SOURCE_URL, 503, "Unavailable", {}, None)
        self.addCleanup(error.close)
        opener = Mock()
        opener.open.side_effect = error
        with patch.object(catalog, "build_opener", return_value=opener):
            with self.assertRaises(HTTPError) as caught:
                catalog.fetch_catalog()
        self.assertEqual(caught.exception.code, 503)

    def test_non_success_and_redirect_status_are_rejected(self):
        for status in [204, 301, 302, 303, 307, 308, 404, 500]:
            with self.subTest(status=status):
                response = FakeResponse(status=status)
                with self.assertRaises(HTTPError) as caught:
                    self.fetch_response(response)
                self.addCleanup(caught.exception.close)
                self.assertEqual(caught.exception.code, status)
                self.assertEqual(response.bytes_read, 0)
                self.assertTrue(response.closed)

    def test_redirect_handler_refuses_every_target_before_following(self):
        handler = catalog._RejectRedirects()
        for code in [301, 302, 303, 307, 308]:
            for target in [catalog.SOURCE_URL, "https://evil.invalid/stolen", "http://models.dev/api.json"]:
                with self.subTest(code=code, target=target):
                    with self.assertRaises(HTTPError) as caught:
                        handler.redirect_request(Request(catalog.SOURCE_URL), None,
                                                 code, "redirect", {}, target)
                    self.addCleanup(caught.exception.close)

    def test_arbitrary_user_url_and_wrong_response_url_are_rejected(self):
        for url in ["http://models.dev/api.json", "https://evil.invalid/api.json",
                    "https://models.dev.evil.invalid/api.json",
                    "https://user:password@models.dev/api.json",
                    "https://models.dev/other", "https://models.dev/api.json?token=secret"]:
            with self.subTest(url=url):
                with patch.object(catalog, "SOURCE_URL", url), patch.object(catalog, "build_opener") as factory:
                    with self.assertRaises(ValueError):
                        catalog.fetch_catalog()
                    factory.assert_not_called()
        response = FakeResponse(url="https://evil.invalid/api.json")
        with self.assertRaises(ValueError):
            self.fetch_response(response)
        self.assertEqual(response.bytes_read, 0)

    def test_content_length_over_limit_rejected_without_read(self):
        response = FakeResponse(headers={"Content-Length": str(catalog.MAX_RESPONSE_BYTES + 1)})
        with self.assertRaisesRegex(ValueError, "16 MiB"):
            self.fetch_response(response)
        self.assertEqual(response.read_sizes, [])
        self.assertTrue(response.closed)

    def test_stream_limit_with_missing_false_or_invalid_content_length(self):
        for headers in [{}, {"Content-Length": "1"}, {"Content-Length": "invalid"}]:
            response = FakeResponse(b" " * 65, headers=headers)
            with self.subTest(headers=headers), patch.object(catalog, "MAX_RESPONSE_BYTES", 64):
                with self.assertRaisesRegex(ValueError, "16 MiB"):
                    self.fetch_response(response)
            self.assertEqual(response.bytes_read, 65)
            self.assertEqual(response.read_sizes, [64, 1])
            self.assertTrue(response.closed)

    def test_exact_size_body_is_accepted(self):
        response = FakeResponse(b"{}" + b" " * 62)
        with patch.object(catalog, "MAX_RESPONSE_BYTES", 64):
            rows, _, _ = self.fetch_response(response)
        self.assertEqual(rows, [])
        self.assertEqual(response.bytes_read, 64)
        self.assertEqual(response.read_sizes, [64, 1])

    def test_json_decode_and_invalid_root_errors_propagate(self):
        for body, error in [(b"{broken", json.JSONDecodeError),
                            (b"", json.JSONDecodeError), (b"\xff", UnicodeDecodeError),
                            (b"[]", ValueError)]:
            with self.subTest(body=body):
                response = FakeResponse(body)
                with self.assertRaises(error):
                    self.fetch_response(response)
                self.assertTrue(response.closed)

    def test_timeout_during_stream_propagates_and_closes_response(self):
        response = FakeResponse()
        response.read = Mock(side_effect=TimeoutError("stream timed out"))
        with self.assertRaises(TimeoutError):
            self.fetch_response(response)
        self.assertTrue(response.closed)


if __name__ == "__main__":
    unittest.main()
