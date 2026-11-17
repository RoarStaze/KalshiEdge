"""Bounded, restart-safe read-only KXGOLD15M observation collection.

Every cycle and each microstructure sample is immutable and content-hashed.
A successful HTTP receipt is NOT an exchange publication timestamp, and
neither Kalshi quotes nor order books prove Pyth settlement labels.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import os
from pathlib import Path
import re
import time
from typing import Any, Callable, Iterator
from uuid import uuid4

import httpx

from .audit import REST_BASE, SERIES_TICKER, EXPECTED_PYTH_SYMBOL
from .prospective import (
    GoldCaptureError, _checked_gold_root, _read_hash_snapshot, _utc,
    _write_immutable, capture_gold_quotes, inspect_gold_quote_snapshot,
)

_TICKER = re.compile(r"^KXGOLD15M-[A-Za-z0-9-]+$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_INTERVAL_MIN = 10
_INTERVAL_MAX = 3600


class GoldContinuousError(ValueError):
    """Invalid microstructure, collector evidence, or concurrent collector."""


def _number(value: Any, field: str, *, minimum: Decimal, maximum: Decimal | None) -> Decimal:
    if not isinstance(value, str) or not value:
        raise GoldContinuousError(f"invalid {field}: expected fixed-point string")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise GoldContinuousError(f"invalid {field}") from exc
    if not number.is_finite() or number < minimum or (maximum is not None and number > maximum):
        raise GoldContinuousError(f"invalid {field}: nonfinite or outside range")
    return number


def _book(payload: Any) -> None:
    if not isinstance(payload, dict) or not isinstance(payload.get("orderbook_fp"), dict):
        raise GoldContinuousError("orderbook_fp is missing")
    book = payload["orderbook_fp"]
    for side in ("yes_dollars", "no_dollars"):
        levels = book.get(side)
        if not isinstance(levels, list) or len(levels) > 100:
            raise GoldContinuousError("invalid orderbook levels")
        for level in levels:
            if not isinstance(level, list) or len(level) != 2:
                raise GoldContinuousError("invalid orderbook level")
            _number(level[0], "orderbook price", minimum=Decimal("0"), maximum=Decimal("1"))
            _number(level[1], "orderbook quantity", minimum=Decimal("0"), maximum=None)


def _trades(payload: Any, ticker: str, limit: int) -> bool:
    if not isinstance(payload, dict) or not isinstance(payload.get("trades"), list):
        raise GoldContinuousError("trades array is missing")
    rows = payload["trades"]
    if len(rows) > limit:
        raise GoldContinuousError("trades endpoint returned more rows than requested")
    seen: set[str] = set()
    for trade in rows:
        if not isinstance(trade, dict) or trade.get("ticker") != ticker:
            raise GoldContinuousError("trade ticker does not match requested gold market")
        trade_id = trade.get("trade_id")
        if not isinstance(trade_id, str) or not trade_id or trade_id in seen:
            raise GoldContinuousError("trade ID missing or repeated")
        seen.add(trade_id)
        _number(trade.get("count_fp"), "trade quantity", minimum=Decimal("0"), maximum=None)
        _number(trade.get("yes_price_dollars"), "trade price",
                minimum=Decimal("0"), maximum=Decimal("1"))
        try:
            _utc(trade.get("created_time"))
        except GoldCaptureError as exc:
            raise GoldContinuousError("trade timestamp is invalid") from exc
    cursor = payload.get("cursor")
    if cursor is not None and not isinstance(cursor, str):
        raise GoldContinuousError("trades cursor is invalid")
    return bool(cursor)


def _get_timed(
    client: httpx.Client, path: str, *, params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    started = time.time_ns()
    response = client.get(REST_BASE + path, params=params)
    received = time.time_ns()
    if received < started:
        raise GoldContinuousError("system clock moved backward during request")
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise GoldContinuousError("non-object microstructure response")
    return {
        "request_path": path, "request_params": params or {},
        "request_started_ns": started, "response_received_ns": received,
        "server_date_header": response.headers.get("Date"),
        "response": payload,
    }


def capture_gold_microstructure(
    output_root: Path, quote_path: Path, *, client: httpx.Client | None = None,
    max_markets: int = 2, depth: int = 10, trade_limit: int = 100,
) -> dict[str, Any]:
    """Collect one bounded public order book and trades page per listed gold market."""
    if not 1 <= max_markets <= 8 or not 1 <= depth <= 100 or not 1 <= trade_limit <= 1000:
        raise ValueError("microstructure limits outside supported range")
    root = _checked_gold_root(output_root)
    quote = Path(quote_path).resolve()
    if quote.parent != (root / "prospective_quotes").resolve():
        raise GoldContinuousError("microstructure requires an in-root quote artifact")
    source = inspect_gold_quote_snapshot(quote)
    quote_sha = sha256(quote.read_bytes()).hexdigest()
    tickers = sorted({
        item["ticker"]
        for page in source["pages"]
        for item in page["response"]["markets"]
    })
    own = client is None
    client = client if client is not None else httpx.Client(timeout=20.0, trust_env=False)
    records: list[dict[str, Any]] = []
    try:
        for ticker in tickers[:max_markets]:
            if not _TICKER.fullmatch(ticker):
                raise GoldContinuousError("invalid market ticker for orderbook")
            orderbook = _get_timed(client, f"/markets/{ticker}/orderbook", params={"depth": depth})
            _book(orderbook["response"])
            trades = _get_timed(
                client, "/markets/trades",
                params={"ticker": ticker, "limit": trade_limit},
            )
            trades["pagination_capped"] = _trades(trades["response"], ticker, trade_limit)
            records.append({"ticker": ticker, "orderbook": orderbook, "trades": trades})
    finally:
        if own:
            client.close()
    report = {
        "series_ticker": SERIES_TICKER,
        "settlement_symbol": EXPECTED_PYTH_SYMBOL,
        "listed_market_count": len(tickers),
        "captured_market_count": len(records),
        "markets_capped": len(tickers) > max_markets,
        "trade_pages_capped": sum(1 for row in records if row["trades"]["pagination_capped"]),
        "point_in_time_publication_verified": False,
        "settlement_oracle_verified": False,
        "ready_for_training": False,
        "ready_for_live_trading": False,
        "blockers": [
            "KALSHI_RECEIPT_NOT_EXCHANGE_PUBLICATION",
            "KALSHI_QUOTES_NOT_PYTH_INDEX",
            "ORDERBOOK_AND_TRADES_NONATOMIC",
            "PYTH_SETTLEMENT_UNVERIFIED",
            "CONTRACT_CANDLE_ALIGNMENT_UNVERIFIED",
        ],
    }
    doc = {
        "schema_version": 1, "artifact_kind": "gold_microstructure_v13",
        "source_api": REST_BASE, "source_quote_artifact": str(quote),
        "quote_sha256": quote_sha,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "records": records, "report": report,
    }
    _validate_microstructure(doc)
    path, digest = _write_immutable(root, "gold_microstructure", doc)
    return {**report, "artifact_path": str(path), "sha256": digest}


def _validate_microstructure(doc: dict[str, Any]) -> None:
    report = doc.get("report")
    if (doc.get("artifact_kind") != "gold_microstructure_v13"
            or doc.get("source_api") != REST_BASE
            or not isinstance(doc.get("quote_sha256"), str)
            or not _HASH.fullmatch(doc["quote_sha256"])
            or not isinstance(report, dict)
            or report.get("series_ticker") != SERIES_TICKER
            or report.get("settlement_symbol") != EXPECTED_PYTH_SYMBOL
            or report.get("point_in_time_publication_verified") is not False
            or report.get("settlement_oracle_verified") is not False
            or report.get("ready_for_training") is not False
            or report.get("ready_for_live_trading") is not False
            or not isinstance(doc.get("records"), list)):
        raise GoldContinuousError("unsupported microstructure evidence claims")
    seen: set[str] = set()
    for record in doc["records"]:
        if not isinstance(record, dict):
            raise GoldContinuousError("microstructure record is not an object")
        ticker = record.get("ticker")
        if not isinstance(ticker, str) or not _TICKER.fullmatch(ticker) or ticker in seen:
            raise GoldContinuousError("invalid or duplicate gold microstructure ticker")
        seen.add(ticker)
        for name in ("orderbook", "trades"):
            payload = record.get(name)
            if not isinstance(payload, dict):
                raise GoldContinuousError("microstructure response missing")
            started, received = payload.get("request_started_ns"), payload.get("response_received_ns")
            if (not isinstance(started, int) or isinstance(started, bool) or started <= 0
                    or not isinstance(received, int) or isinstance(received, bool)
                    or received < started):
                raise GoldContinuousError("microstructure receipt timestamps invalid")
        if record["orderbook"].get("request_path") != f"/markets/{ticker}/orderbook":
            raise GoldContinuousError("orderbook request identity mismatch")
        if record["trades"].get("request_path") != "/markets/trades":
            raise GoldContinuousError("trade request identity mismatch")
        if record["trades"].get("request_params", {}).get("ticker") != ticker:
            raise GoldContinuousError("trade request ticker mismatch")
        limit = record["trades"].get("request_params", {}).get("limit")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
            raise GoldContinuousError("trade page limit invalid")
        _book(record["orderbook"].get("response"))
        capped = _trades(record["trades"].get("response"), ticker, limit)
        if record["trades"].get("pagination_capped") is not capped:
            raise GoldContinuousError("trade pagination completeness mismatch")
    if (report.get("captured_market_count") != len(seen)
            or report.get("trade_pages_capped") != sum(
                bool(x["trades"]["pagination_capped"]) for x in doc["records"]
            )
            or not isinstance(report.get("listed_market_count"), int)
            or report["listed_market_count"] < len(seen)
            or report.get("markets_capped") is not (report["listed_market_count"] > len(seen))):
        raise GoldContinuousError("microstructure market accounting mismatch")


def inspect_gold_microstructure_snapshot(path: Path) -> dict[str, Any]:
    """Inspect SHA integrity and internal non-promotional evidence invariants."""
    try:
        doc, _ = _read_hash_snapshot(path)
        _validate_microstructure(doc)
    except (GoldCaptureError, OSError, KeyError, TypeError) as exc:
        raise GoldContinuousError(f"invalid microstructure SHA-256 or evidence: {exc}") from exc
    return doc


@contextmanager
def _collector_lock(root: Path) -> Iterator[None]:
    """OS-held advisory lock; crash automatically releases it on Windows/Linux."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / "collector.lock"
    with path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise GoldContinuousError("another gold collector already holds the lock") from exc
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise GoldContinuousError("another gold collector already holds the lock") from exc
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _event_check(event: dict[str, Any]) -> None:
    if (event.get("artifact_kind") != "gold_collector_cycle_v13"
            or event.get("series_ticker") != SERIES_TICKER
            or event.get("settlement_symbol") != EXPECTED_PYTH_SYMBOL
            or event.get("status") not in ("CAPTURED", "FAILED")
            or event.get("ready_for_training") is not False
            or event.get("dataset_ready") is not False
            or event.get("settlement_verified") is not False
            or not isinstance(event.get("session_id"), str)
            or not re.fullmatch(r"[0-9a-f]{32}", event["session_id"])
            or not isinstance(event.get("cycle"), int) or event["cycle"] < 1
            or not isinstance(event.get("previous_event_sha256"), (str, type(None)))):
        raise GoldContinuousError("collector event claims are invalid")
    previous = event["previous_event_sha256"]
    if previous is not None and not _HASH.fullmatch(previous):
        raise GoldContinuousError("invalid previous event digest")
    if event["status"] == "CAPTURED":
        if (event.get("failure_code") is not None
                or not event.get("quote_artifact")
                or not event.get("microstructure_artifact")):
            raise GoldContinuousError("captured cycle is missing evidence")
    elif not isinstance(event.get("failure_code"), str) or not event["failure_code"]:
        raise GoldContinuousError("failed cycle has no reason")


def inspect_gold_collector_status(output_root: Path, *, limit: int = 20_000) -> dict[str, Any]:
    """Read immutable events; never infer a verified settlement or training row."""
    if not 1 <= limit <= 100_000:
        raise ValueError("status event limit outside supported range")
    folder = _checked_gold_root(output_root) / "collector_events"
    paths = sorted(folder.glob("*.json")) if folder.exists() else []
    selected = paths[-limit:]
    success = failed = 0
    latest_quote = None
    latest_micro = None
    sessions: set[str] = set()
    last_per_session: dict[str, tuple[int, str]] = {}
    truncated = len(selected) < len(paths)
    for path in selected:
        try:
            event, digest = _read_hash_snapshot(path)
            _event_check(event)
        except (GoldCaptureError, OSError, TypeError, KeyError) as exc:
            raise GoldContinuousError(f"collector SHA-256 or evidence invalid: {exc}") from exc
        session = event["session_id"]
        prior = last_per_session.get(session)
        if prior is None:
            if event["cycle"] == 1:
                if event["previous_event_sha256"] is not None:
                    raise GoldContinuousError("collector first cycle chain must start empty")
            elif not truncated:
                raise GoldContinuousError("collector cycle chain has missing first event")
        elif (event["cycle"] != prior[0] + 1
                or event["previous_event_sha256"] != prior[1]):
            raise GoldContinuousError("collector cycle chain is missing or reordered")
        last_per_session[session] = (event["cycle"], digest)
        sessions.add(session)
        if event["status"] == "CAPTURED":
            success += 1
        else:
            failed += 1
        if event.get("quote_artifact"):
            latest_quote = event["quote_artifact"]
        if event.get("microstructure_artifact"):
            latest_micro = event["microstructure_artifact"]
    return {
        "series_ticker": SERIES_TICKER,
        "cycles": len(selected), "successful_cycles": success, "failed_cycles": failed,
        "sessions": len(sessions), "events_truncated": len(selected) < len(paths),
        "latest_quote_path": latest_quote, "latest_microstructure_path": latest_micro,
        "dataset_ready": False, "settlement_verified": False,
        "notes": "Only authenticated Pyth source and confirmed contract terms can establish settlement labels",
    }


def run_continuous_gold_collection(
    output_root: Path, *, interval_seconds: int = 30,
    max_cycles: int | None = None, max_markets: int = 2,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Poll safely until Ctrl+C or max_cycles; preserve each successful/failed cycle."""
    if not isinstance(interval_seconds, int) or not _INTERVAL_MIN <= interval_seconds <= _INTERVAL_MAX:
        raise ValueError("interval_seconds must be between 10 and 3600")
    if max_cycles is not None and (not isinstance(max_cycles, int) or max_cycles < 1):
        raise ValueError("max_cycles must be a positive integer")
    if not isinstance(max_markets, int) or not 1 <= max_markets <= 8:
        raise ValueError("max_markets must be between 1 and 8")
    root = _checked_gold_root(output_root)
    pause = sleep if sleep is not None else time.sleep
    own = client is None
    client = client if client is not None else httpx.Client(timeout=20.0, trust_env=False)
    session = uuid4().hex
    previous = None
    cycles = successes = failures = 0
    consecutive_failures = 0
    stopped = "BOUNDED_COMPLETE"
    try:
        with _collector_lock(root):
            try:
                while max_cycles is None or cycles < max_cycles:
                    cycles += 1
                    quote_path = None
                    micro_path = None
                    status = "CAPTURED"
                    code = None
                    retry_after_seconds = 0
                    started = time.time_ns()
                    try:
                        quote = capture_gold_quotes(root, client=client)
                        quote_path = quote["artifact_path"]
                        micro = capture_gold_microstructure(
                            root, Path(quote_path), client=client, max_markets=max_markets,
                        )
                        micro_path = micro["artifact_path"]
                        successes += 1
                        consecutive_failures = 0
                    except (httpx.HTTPError, ValueError, OSError) as exc:
                        status = "FAILED"
                        failures += 1
                        consecutive_failures = min(consecutive_failures + 1, 4)
                        if isinstance(exc, httpx.HTTPStatusError):
                            code = f"HTTP_{exc.response.status_code}"
                            header = exc.response.headers.get("Retry-After", "")
                            if header.isdecimal():
                                retry_after_seconds = min(300, int(header))
                        elif isinstance(exc, httpx.TimeoutException):
                            code = "HTTP_TIMEOUT"
                        elif isinstance(exc, httpx.RequestError):
                            code = "HTTP_REQUEST_ERROR"
                        else:
                            code = type(exc).__name__
                    event = {
                        "schema_version": 1, "artifact_kind": "gold_collector_cycle_v13",
                        "session_id": session, "cycle": cycles,
                        "series_ticker": SERIES_TICKER,
                        "settlement_symbol": EXPECTED_PYTH_SYMBOL,
                        "started_ns": started, "completed_ns": time.time_ns(),
                        "status": status, "failure_code": code,
                        "quote_artifact": quote_path,
                        "microstructure_artifact": micro_path,
                        "previous_event_sha256": previous,
                        "dataset_ready": False, "settlement_verified": False,
                        "ready_for_training": False,
                    }
                    _event_check(event)
                    path, digest = _write_immutable(root, "collector_events", event)
                    previous = digest
                    if on_event is not None:
                        on_event({**event, "event_path": str(path), "event_sha256": digest})
                    if max_cycles is not None and cycles >= max_cycles:
                        break
                    # All rate errors and other retrieval failures back off rather than spin.
                    delay = max(
                        min(300, interval_seconds * (2 ** consecutive_failures)),
                        retry_after_seconds,
                    )
                    pause(delay)
            except KeyboardInterrupt:
                stopped = "INTERRUPTED"
    finally:
        if own:
            client.close()
    return {
        "series_ticker": SERIES_TICKER, "session_id": session,
        "cycles": cycles, "successful_cycles": successes, "failed_cycles": failures,
        "status": stopped, "dataset_ready": False, "settlement_verified": False,
    }
