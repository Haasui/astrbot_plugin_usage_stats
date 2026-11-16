"""Models.dev generic reference prices in USD per million tokens.

Independent of AstrBot. Run fetch_catalog in asyncio.to_thread from async
callers; let failures preserve the old catalog. Reference prices are not a
provider billing guarantee.
"""

from __future__ import annotations

import json
import math
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

SOURCE_URL = "https://models.dev/api.json"
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_MODEL_ENTRIES = 100_000
MAX_PROVIDERS = 1024
MAX_NAME_LENGTH = 512
FETCH_TIMEOUT = 12
_READ_CHUNK = 64 * 1024

# Exact source IDs only. There is no model-family inference or vendor ranking.
_ORIGINAL_PROVIDERS = frozenset({
    "openai", "anthropic", "google", "deepseek", "xai", "mistral", "cohere",
    "moonshotai", "zhipuai", "zai", "minimax", "alibaba", "qwen",
    "ai21", "amazon", "baidu", "bytedance", "meta", "nvidia", "perplexity",
    "stepfun", "tencent", "xiaomi", "inception", "writer",
})


def _name(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    if len(value) > MAX_NAME_LENGTH:
        raise ValueError("Models.dev identifier exceeds name length limit")
    return value


def _price(value: object) -> float | None:
    # JSON numbers only: bool is an int subclass but is not a price.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def parse_catalog(payload: dict) -> list[dict]:
    """Parse provider.models dictionaries without normalizing model IDs.

    A nonempty model.id wins over its dictionary key, including when they
    differ; the key is never emitted as a second alias. Missing/empty IDs use
    the key. Known original sources win over resellers. Conflicting original
    sources, or conflicting resellers with no original source, are omitted.
    Cache prices participate in equality; missing and explicit zero differ.
    Invalid optional cache prices are treated as unknown (None).
    """
    if not isinstance(payload, dict):
        raise ValueError("Models.dev catalog must be a provider dictionary")
    if len(payload) > MAX_PROVIDERS:
        raise ValueError("Models.dev catalog exceeds provider count limit")

    candidates: dict[str, list[dict]] = {}
    entries = 0
    for provider_key, provider in payload.items():
        if not isinstance(provider, dict):
            continue
        models = provider.get("models")
        if not isinstance(models, dict):
            continue
        entries += len(models)
        if entries > MAX_MODEL_ENTRIES:
            raise ValueError("Models.dev catalog exceeds model count limit")
        source = _name(provider_key)
        if source is None:
            continue
        # The top-level key is the source identity. A mismatched embedded
        # provider.id must not impersonate a trusted source.
        for model_key, model in models.items():
            if not isinstance(model, dict):
                continue
            raw_id = model.get("id")
            if raw_id is None or raw_id == "":
                raw_id = model_key
            model_id = _name(raw_id)
            if model_id is None:
                continue
            cost = model.get("cost")
            if not isinstance(cost, dict):
                continue
            input_price = _price(cost.get("input"))
            output_price = _price(cost.get("output"))
            if input_price is None or output_price is None:
                continue
            candidates.setdefault(model_id, []).append({
                "model": model_id,
                "provider": "",
                "input": input_price,
                "output": output_price,
                "cache_read": _price(cost.get("cache_read")),
                "cache_write": _price(cost.get("cache_write")),
                "source_provider": source,
            })

    result = []
    for model_id in sorted(candidates):
        rows = candidates[model_id]
        originals = [row for row in rows
                     if row["source_provider"] in _ORIGINAL_PROVIDERS]
        considered = originals or rows
        prices = {(row["input"], row["output"], row["cache_read"],
                   row["cache_write"]) for row in considered}
        if len(prices) == 1:
            result.append(min(considered, key=lambda row: row["source_provider"]))
    return result


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, "Models.dev redirect refused", headers, fp)


def _validate_url(url: str) -> None:
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != "models.dev"
            or url != "https://models.dev/api.json"):
        raise ValueError("Models.dev fetch requires the fixed HTTPS catalog URL")


def fetch_catalog() -> list[dict]:
    """Fetch only SOURCE_URL with a 12-second socket timeout and bounded reads.

    No local configuration, usage, credentials or environment proxy settings
    are sent. Redirects are rejected before following them. Network/HTTP/JSON
    failures propagate to the caller. At the byte limit a one-byte probe is
    needed to distinguish an exact-size body from an oversized response;
    that byte is never appended to the bounded catalog buffer.
    """
    _validate_url(SOURCE_URL)
    opener = build_opener(ProxyHandler({}), _RejectRedirects())
    request = Request(SOURCE_URL, headers={
        "Accept": "application/json", "Accept-Encoding": "identity",
        "User-Agent": "usage-stats-price-catalog/1",
    })
    with opener.open(request, timeout=FETCH_TIMEOUT) as response:
        _validate_url(response.geturl())
        if response.status != 200:
            raise HTTPError(SOURCE_URL, response.status,
                            "Models.dev HTTP request failed", response.headers, None)
        length = response.headers.get("Content-Length")
        if length is not None:
            try:
                declared = int(length)
            except (ValueError, TypeError):
                declared = None
            if declared is not None and declared > MAX_RESPONSE_BYTES:
                raise ValueError("Models.dev response exceeds 16 MiB limit")
        body = bytearray()
        while len(body) < MAX_RESPONSE_BYTES:
            chunk = response.read(min(_READ_CHUNK, MAX_RESPONSE_BYTES - len(body)))
            if not chunk:
                break
            if len(chunk) > MAX_RESPONSE_BYTES - len(body):
                raise ValueError("Models.dev response exceeds 16 MiB limit")
            body.extend(chunk)
        if len(body) == MAX_RESPONSE_BYTES and response.read(1):
            raise ValueError("Models.dev response exceeds 16 MiB limit")
    return parse_catalog(json.loads(body))
