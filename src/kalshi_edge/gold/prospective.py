"""Prospective KXGOLD15M public quote receipts, not verified training features.

Kalshi market listings are cached and can be delayed; a local receipt timestamp
proves only that the payload had arrived at this machine by that time. It does
not prove exchange publication time, execution, or the official Pyth oracle.
No credentials, orders, training or historical backfilled features are used.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import time
from typing import Any

import httpx

from .audit import REST_BASE, SERIES_TICKER, EXPECTED_PYTH_SYMBOL, FINAL_CHECKPOINT_SECONDS, validate_gold_series

_TICKER = re.compile(r"^KXGOLD15M-[A-Za-z0-9-]+$")
_SHA_NAME = re.compile(r"^\d+-([0-9a-f]{16})\.json$")


class GoldCaptureError(ValueError):
    """Reject invalid prospective gold quote evidence."""


def _utc(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise GoldCaptureError("invalid market close_time")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GoldCaptureError("invalid market close_time") from exc
    if parsed.tzinfo is None:
        raise GoldCaptureError("market close_time is missing UTC offset")
    return parsed.astimezone(timezone.utc)


def _decimal(value: Any, field: str, *, minimum: Decimal, maximum: Decimal | None) -> Decimal:
    if not isinstance(value, str) or not value:
        raise GoldCaptureError(f"invalid {field}: expected a decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise GoldCaptureError(f"invalid {field}") from exc
    if (not parsed.is_finite() or parsed < minimum
            or (maximum is not None and parsed > maximum)):
        raise GoldCaptureError(f"invalid {field}: outside expected range")
    return parsed


def _validate_market(record: Any) -> None:
    if not isinstance(record, dict):
        raise GoldCaptureError("market is not an object")
    ticker = record.get("ticker")
    if not isinstance(ticker, str) or not _TICKER.fullmatch(ticker):
        raise GoldCaptureError("unexpected gold ticker")
    if record.get("status") not in ("active", "open"):
        raise GoldCaptureError("not an open market")
    if record.get("result") not in ("", None):
        raise GoldCaptureError("live quote has a finalized result")
    _utc(record.get("close_time"))
    bid = _decimal(record.get("yes_bid_dollars"), "yes_bid_dollars",
                   minimum=Decimal("0"), maximum=Decimal("1"))
    ask = _decimal(record.get("yes_ask_dollars"), "yes_ask_dollars",
                   minimum=Decimal("0"), maximum=Decimal("1"))
    if bid > ask:
        raise GoldCaptureError("crossed yes bid/ask quote")
    _decimal(record.get("last_price_dollars"), "last_price_dollars",
             minimum=Decimal("0"), maximum=Decimal("1"))
    _decimal(record.get("volume_fp"), "volume_fp",
             minimum=Decimal("0"), maximum=None)


def _canonical(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _checked_gold_root(output_root: Path) -> Path:
    root = Path(output_root).resolve()
    parts = tuple(p.lower() for p in root.parts)
    if any(parts[i:i + 2] == ("data", "raw") for i in range(len(parts) - 1)):
        raise ValueError("gold research cannot write inside canonical Phase 1 data/raw")
    return root


def _write_immutable(root: Path, subdirectory: str, doc: dict[str, Any]) -> tuple[Path, str]:
    data = _canonical(doc)
    digest = sha256(data).hexdigest()
    folder = _checked_gold_root(root) / subdirectory
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{time.time_ns()}-{digest[:16]}.json"
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return path, digest


def _read_hash_snapshot(path: Path) -> tuple[dict[str, Any], str]:
    p = Path(path)
    matched = _SHA_NAME.fullmatch(p.name)
    if matched is None:
        raise GoldCaptureError("snapshot filename must contain SHA-256 prefix")
    data = p.read_bytes()
    digest = sha256(data).hexdigest()
    if digest[:16] != matched.group(1):
        raise GoldCaptureError("snapshot SHA-256 mismatch")
    try:
        document = json.loads(data)
    except (UnicodeDecodeError, ValueError) as exc:
        raise GoldCaptureError("snapshot JSON is invalid") from exc
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise GoldCaptureError("unsupported snapshot schema")
    return document, digest


def inspect_gold_quote_snapshot(path: Path) -> dict[str, Any]:
    """Verify integrity and internal structure, not authenticity of the exchange."""
    document, _ = _read_hash_snapshot(path)
    try:
        validate_gold_series(document["series"])
    except (ValueError, TypeError, KeyError) as exc:
        raise GoldCaptureError("quote series identity mismatch") from exc
    report = document.get("report")
    if not isinstance(report, dict) or (
        report.get("series_ticker") != SERIES_TICKER
        or report.get("settlement_symbol") != EXPECTED_PYTH_SYMBOL
        or report.get("ready_for_training") is not False
        or report.get("settlement_oracle_verified") is not False
        or report.get("point_in_time_publication_verified") is not False
    ):
        raise GoldCaptureError("quote snapshot readiness fields are invalid")
    pages = document.get("pages")
    if not isinstance(pages, list):
        raise GoldCaptureError("invalid quote snapshot pages")
    seen: set[str] = set()
    for page in pages:
        if not isinstance(page, dict) or not isinstance(page.get("response"), dict):
            raise GoldCaptureError("invalid quote snapshot page")
        start, received = page.get("request_started_ns"), page.get("response_received_ns")
        if any(not isinstance(v, int) or isinstance(v, bool) or v <= 0 for v in (start, received)):
            raise GoldCaptureError("invalid quote request timestamps")
        if start > received:
            raise GoldCaptureError("request receipt precedes request start")
        params = page.get("params")
        if not isinstance(params, dict) or params.get("series_ticker") != SERIES_TICKER:
            raise GoldCaptureError("quote request series mismatch")
        records = page["response"].get("markets")
        if not isinstance(records, list):
            raise GoldCaptureError("quote markets array missing")
        for item in records:
            _validate_market(item)
            if item["ticker"] in seen:
                raise GoldCaptureError("duplicate gold market across quote pages")
            seen.add(item["ticker"])
    return document


def capture_gold_quotes(
    output_root: Path, *, client: httpx.Client | None = None,
    page_size: int = 100, max_pages: int = 2,
) -> dict[str, Any]:
    """Capture bounded GET /markets?status=open responses with local receipt times."""
    if not 1 <= page_size <= 1000 or not 1 <= max_pages <= 20:
        raise ValueError("quote page_size must be 1..1000 and max_pages 1..20")
    root = _checked_gold_root(output_root)
    own = client is None
    client = client or httpx.Client(timeout=20.0)
    pages: list[dict[str, Any]] = []
    seen_tickers: set[str] = set()
    seen_cursors: set[str] = set()
    cursor: str | None = None
    try:
        series_response = client.get(f"{REST_BASE}/series/{SERIES_TICKER}")
        series_received_ns = time.time_ns()
        series_response.raise_for_status()
        series = series_response.json()
        if not isinstance(series, dict):
            raise GoldCaptureError("gold series must be an object")
        try:
            validate_gold_series(series)
        except ValueError as exc:
            raise GoldCaptureError("unexpected Pyth symbol or series identity") from exc
        for _ in range(max_pages):
            params: dict[str, Any] = {
                "series_ticker": SERIES_TICKER,
                "status": "open", "limit": page_size,
            }
            if cursor is not None:
                params["cursor"] = cursor
            start = time.time_ns()
            response = client.get(f"{REST_BASE}/markets", params=params)
            received = time.time_ns()
            if received < start:
                raise GoldCaptureError("local clock moved backward during capture")
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("markets"), list):
                raise GoldCaptureError("invalid public live markets response")
            for item in payload["markets"]:
                _validate_market(item)
                if item["ticker"] in seen_tickers:
                    raise GoldCaptureError("repeated ticker across market pages")
                seen_tickers.add(item["ticker"])
            pages.append({
                "request_path": "/markets", "params": params,
                "request_started_ns": start, "response_received_ns": received,
                "server_date_header": response.headers.get("Date"),
                "response": payload,
            })
            next_cursor = payload.get("cursor")
            if next_cursor is None or next_cursor == "":
                cursor = None
                break
            if not isinstance(next_cursor, str) or next_cursor in seen_cursors:
                raise GoldCaptureError("invalid or repeated markets cursor")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
    finally:
        if own:
            client.close()

    candidate_pre_checkpoint = 0
    for page in pages:
        for item in page["response"]["markets"]:
            checkpoint_ns = int(_utc(item["close_time"]).timestamp() * 1_000_000_000) - (
                FINAL_CHECKPOINT_SECONDS * 1_000_000_000)
            if page["response_received_ns"] <= checkpoint_ns:
                candidate_pre_checkpoint += 1

    report = {
        "series_ticker": SERIES_TICKER, "settlement_symbol": EXPECTED_PYTH_SYMBOL,
        "market_records": len(seen_tickers), "page_count": len(pages),
        "pagination_capped": cursor is not None,
        "candidate_pre_checkpoint_receipts": candidate_pre_checkpoint,
        "checkpoint_seconds_hypothetical": FINAL_CHECKPOINT_SECONDS,
        "point_in_time_publication_verified": False,
        "settlement_oracle_verified": False, "ready_for_training": False,
        "ready_for_live_trading": False,
        "limitations": [
            "Local receipt time is not an exchange publication timestamp",
            "GET /markets listings are cached and prices may lag",
            "API close_time minus 480 seconds is not a validated rule checkpoint",
            "No independently recorded Pyth settlement observations or labels",
            "Top-of-book quotes do not establish executable fill size or net edge",
        ],
    }
    snapshot = {
        "schema_version": 1, "series": series, "series_received_ns": series_received_ns,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_url": REST_BASE, "pages": pages, "report": report,
    }
    target, digest = _write_immutable(root, "prospective_quotes", snapshot)
    return {**report, "sha256": digest, "artifact_path": str(target)}
