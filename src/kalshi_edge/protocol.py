from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import base64
import json
from typing import Any


SCHEMA_VERSION = 2


@dataclass(frozen=True)
class RawEvent:
    schema_version: int
    source: str
    connection_id: str
    message_type: str
    receive_ts_ns: int
    source_ts_ms: int | None
    sid: int | None
    seq: int | None
    market_ticker: str | None
    index_id: str | None
    payload_sha256: str
    wire_sha256: str
    wire_encoding: str
    wire_payload: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _canonical_hash(payload: dict[str, Any]) -> str:
    return sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _source_timestamp(payload: dict[str, Any]) -> int | None:
    msg = payload.get("msg") or {}
    source_ts_ms = msg.get("ts_ms")
    if source_ts_ms is None and payload.get("type") == "cfbenchmarks_value":
        raw_data = msg.get("data")
        if isinstance(raw_data, str):
            try:
                source_ts_ms = json.loads(raw_data).get("time")
            except (json.JSONDecodeError, AttributeError):
                source_ts_ms = None
    return source_ts_ms


def _from_payload(
    payload: dict[str, Any],
    *,
    receive_ts_ns: int,
    connection_id: str,
    wire_sha256: str,
    wire_encoding: str,
    wire_payload: str,
) -> RawEvent:
    msg = payload.get("msg") or {}
    return RawEvent(
        schema_version=SCHEMA_VERSION,
        source="kalshi_ws",
        connection_id=connection_id,
        message_type=str(payload.get("type", "unknown")),
        receive_ts_ns=receive_ts_ns,
        source_ts_ms=_source_timestamp(payload),
        sid=payload.get("sid"),
        seq=payload.get("seq"),
        market_ticker=msg.get("market_ticker"),
        index_id=msg.get("index_id"),
        payload_sha256=_canonical_hash(payload),
        wire_sha256=wire_sha256,
        wire_encoding=wire_encoding,
        wire_payload=wire_payload,
        payload=payload,
    )


def normalize_ws_message(payload: dict[str, Any], *, receive_ts_ns: int, connection_id: str = "unknown") -> RawEvent:
    """Normalize an already-parsed message.

    This helper is useful for tests and generated/recovered events. Live collection uses
    ``normalize_ws_frame`` so the exact WebSocket frame is retained.
    """
    canonical = _canonical_json(payload)
    encoded = canonical.encode("utf-8")
    return _from_payload(
        payload,
        receive_ts_ns=receive_ts_ns,
        connection_id=connection_id,
        wire_sha256=sha256(encoded).hexdigest(),
        wire_encoding="canonical-json",
        wire_payload=canonical,
    )


def normalize_ws_frame(raw: str | bytes, *, receive_ts_ns: int, connection_id: str = "unknown") -> RawEvent:
    """Preserve an exact WebSocket frame and parse its structured message when possible."""
    if isinstance(raw, str):
        raw_bytes = raw.encode("utf-8")
        wire_encoding = "utf-8"
        wire_payload = raw
        text = raw
    else:
        raw_bytes = bytes(raw)
        try:
            text = raw_bytes.decode("utf-8")
            wire_encoding = "utf-8"
            wire_payload = text
        except UnicodeDecodeError:
            text = ""
            wire_encoding = "base64"
            wire_payload = base64.b64encode(raw_bytes).decode("ascii")

    try:
        decoded = json.loads(text) if text else None
    except json.JSONDecodeError:
        decoded = None
    if not isinstance(decoded, dict):
        decoded = {"type": "malformed_frame", "msg": {"parseable_json_object": False}}

    return _from_payload(
        decoded,
        receive_ts_ns=receive_ts_ns,
        connection_id=connection_id,
        wire_sha256=sha256(raw_bytes).hexdigest(),
        wire_encoding=wire_encoding,
        wire_payload=wire_payload,
    )


def normalize_rest_payload(
    payload: dict[str, Any],
    *,
    message_type: str,
    receive_ts_ns: int,
    request_id: str,
) -> RawEvent:
    canonical = _canonical_json(payload)
    encoded = canonical.encode("utf-8")
    digest = sha256(encoded).hexdigest()
    return RawEvent(
        schema_version=SCHEMA_VERSION,
        source="kalshi_rest",
        connection_id=request_id,
        message_type=message_type,
        receive_ts_ns=receive_ts_ns,
        source_ts_ms=None,
        sid=None,
        seq=None,
        market_ticker=(payload.get("market") or {}).get("ticker"),
        index_id=None,
        payload_sha256=digest,
        wire_sha256=digest,
        wire_encoding="canonical-json",
        wire_payload=canonical,
        payload=payload,
    )


def normalize_control_payload(
    payload: dict[str, Any],
    *,
    message_type: str,
    receive_ts_ns: int,
    session_id: str,
) -> RawEvent:
    canonical = _canonical_json(payload)
    encoded = canonical.encode("utf-8")
    digest = sha256(encoded).hexdigest()
    return RawEvent(
        schema_version=SCHEMA_VERSION,
        source="collector_control",
        connection_id=session_id,
        message_type=message_type,
        receive_ts_ns=receive_ts_ns,
        source_ts_ms=None,
        sid=None,
        seq=None,
        market_ticker=None,
        index_id=None,
        payload_sha256=digest,
        wire_sha256=digest,
        wire_encoding="canonical-json",
        wire_payload=canonical,
        payload=payload,
    )


def build_subscriptions(market_tickers: list[str]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    next_id = 1
    if market_tickers:
        messages.extend([
            {
                "id": next_id,
                "cmd": "subscribe",
                "params": {"channels": ["orderbook_delta"], "market_tickers": market_tickers},
            },
            {
                "id": next_id + 1,
                "cmd": "subscribe",
                "params": {"channels": ["trade"], "market_tickers": market_tickers},
            },
        ])
        next_id += 2
    messages.extend([
        {
            "id": next_id,
            "cmd": "subscribe",
            "params": {"channels": ["cfbenchmarks_value"], "index_ids": ["BRTI"]},
        },
        {
            "id": next_id + 1,
            "cmd": "subscribe",
            "params": {"channels": ["market_lifecycle_v2"]},
        },
    ])
    return messages
