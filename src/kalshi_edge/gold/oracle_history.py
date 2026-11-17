"""Offline Pyth Pro one-minute GOLD OHLC evidence importer (no API keys).

Pyth's /v1/{channel}/history requires authenticated access. This module never
contacts that endpoint, reads credentials, or treats a local file as a signed
oracle record. A SHA-256 integrity check is NOT source authentication, and the
OHLC bar timestamp is NOT a verified Kalshi candle-closing boundary.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from .audit import EXPECTED_PYTH_SYMBOL
from .prospective import _checked_gold_root, _read_hash_snapshot, _write_immutable, GoldCaptureError

PYTH_BASE = "https://pyth.dourolabs.app"
DOCUMENTATION = "https://docs.pyth.network/pro/api/rest"
ALLOWED_CHANNELS = frozenset(("real_time", "fixed_rate@200ms"))
MAX_INPUT_BYTES = 16 * 1024 * 1024
MAX_BARS = 100_000


class GoldOracleEvidenceError(ValueError):
    """Reject malformed, forged, or unsupported Pyth price evidence."""


def pyth_history_request(start_ts: int, end_ts: int, *, channel: str = "real_time") -> dict:
    """Build an offline request plan, never an HTTP request or credential lookup."""
    if channel not in ALLOWED_CHANNELS:
        raise GoldOracleEvidenceError("unapproved Pyth channel")
    if (not isinstance(start_ts, int) or isinstance(start_ts, bool)
            or not isinstance(end_ts, int) or isinstance(end_ts, bool)
            or start_ts <= 0 or end_ts <= start_ts
            or end_ts - start_ts > 365 * 86400):
        raise GoldOracleEvidenceError("invalid bounded Pyth history timestamp range")
    return {
        "method": "GET", "host": "pyth.dourolabs.app",
        "path": f"/v1/{channel}/history",
        "params": {
            "symbol": EXPECTED_PYTH_SYMBOL,
            "from": start_ts, "to": end_ts, "resolution": "1",
            "split_adjusted": "false",
        },
        "requires_api_key": True,
        "documentation": DOCUMENTATION,
    }


def _decimal(value: Any, field: str) -> Decimal:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise GoldOracleEvidenceError(f"{field} must be an OHLC numeric JSON value")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise GoldOracleEvidenceError(f"{field} is not numeric") from exc
    if not result.is_finite() or result <= 0:
        raise GoldOracleEvidenceError(f"{field} must be positive and finite")
    return result


def validate_history_payload(
    payload: Any, *, start_ts: int, end_ts: int,
) -> dict[str, int]:
    """Validate exact one-minute shape, bounds and numeric relationships."""
    if not isinstance(payload, dict) or payload.get("s") != "ok":
        raise GoldOracleEvidenceError("Pyth history is missing or returned an error")
    if set(payload) - {"s", "t", "o", "h", "l", "c", "v", "errmsg"}:
        raise GoldOracleEvidenceError("unexpected fields in Pyth history source")
    required = ("t", "o", "h", "l", "c")
    if any(not isinstance(payload.get(key), list) for key in required):
        raise GoldOracleEvidenceError("Pyth OHLC arrays missing")
    bars = payload["t"]
    if len(bars) == 0 or len(bars) > MAX_BARS:
        raise GoldOracleEvidenceError("Pyth bar count outside supported range")
    if any(len(payload[key]) != len(bars) for key in required):
        raise GoldOracleEvidenceError("Pyth OHLC arrays are not aligned")
    if "v" in payload and (not isinstance(payload["v"], list) or len(payload["v"]) != len(bars)):
        raise GoldOracleEvidenceError("Pyth volume array is not aligned")
    previous: int | None = None
    gaps = 0
    for index, ts in enumerate(bars):
        if (not isinstance(ts, int) or isinstance(ts, bool)
                or ts % 60 != 0 or not start_ts <= ts <= end_ts):
            raise GoldOracleEvidenceError("Pyth minute timestamp is invalid or out of bounds")
        if previous is not None:
            if ts <= previous:
                raise GoldOracleEvidenceError("Pyth minute timestamps duplicate or out of order")
            gaps += (ts - previous) // 60 - 1
        previous = ts
        opening = _decimal(payload["o"][index], "open")
        highest = _decimal(payload["h"][index], "high")
        lowest = _decimal(payload["l"][index], "low")
        closing = _decimal(payload["c"][index], "close")
        if highest < max(opening, closing) or lowest > min(opening, closing):
            raise GoldOracleEvidenceError("Pyth OHLC high/low inconsistent with open/close")
        if lowest > highest:
            raise GoldOracleEvidenceError("Pyth high below low")
        if "v" in payload:
            volume = payload["v"][index]
            if not isinstance(volume, (int, float)) or isinstance(volume, bool):
                raise GoldOracleEvidenceError("invalid Pyth volume")
            try:
                amount = Decimal(str(volume))
            except (InvalidOperation, ValueError) as exc:
                raise GoldOracleEvidenceError("invalid Pyth volume") from exc
            if not amount.is_finite() or amount < 0:
                raise GoldOracleEvidenceError("invalid Pyth volume")
    return {"bar_count": len(bars), "gap_minutes": gaps}


def _validate_evidence(doc: dict[str, Any]) -> None:
    if doc.get("artifact_kind") != "unattested_pyth_gold_history_import":
        raise GoldOracleEvidenceError("invalid Pyth import kind")
    req = doc.get("request")
    if not isinstance(req, dict):
        raise GoldOracleEvidenceError("Pyth request metadata missing")
    params = req.get("params")
    if not isinstance(params, dict):
        raise GoldOracleEvidenceError("Pyth history request parameters missing")
    try:
        expected = pyth_history_request(
            params["from"], params["to"],
            channel=req["path"].removeprefix("/v1/").removesuffix("/history"),
        )
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise GoldOracleEvidenceError("invalid Pyth history request metadata") from exc
    if req != expected:
        raise GoldOracleEvidenceError("Pyth source identity, channel or parameters changed")
    raw = doc.get("source_raw_utf8")
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_INPUT_BYTES:
        raise GoldOracleEvidenceError("Pyth source bytes missing or too large")
    if sha256(raw.encode("utf-8")).hexdigest() != doc.get("source_sha256"):
        raise GoldOracleEvidenceError("source byte SHA-256 mismatch")
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise GoldOracleEvidenceError("Pyth source JSON invalid") from exc
    if payload != doc.get("raw_pyth_response"):
        raise GoldOracleEvidenceError("Pyth raw JSON does not match preserved source")
    stats = validate_history_payload(payload, start_ts=params["from"], end_ts=params["to"])
    report = doc.get("report")
    if not isinstance(report, dict) or any((
        report.get("bar_count") != stats["bar_count"],
        report.get("gap_minutes") != stats["gap_minutes"],
        report.get("source_sha256") != doc.get("source_sha256"),
        report.get("settlement_verified") is not False,
        report.get("training_ready") is not False,
        report.get("origin_authenticated") is not False,
        "PYTH_HISTORY_CANDLE_ALIGNMENT_UNVERIFIED" not in report.get("blockers", ()),
        "PYTH_ORIGIN_UNATTESTED" not in report.get("blockers", ()),
    )):
        raise GoldOracleEvidenceError("unattested Pyth provenance or readiness claims are invalid")


def inspect_pyth_history_evidence(path: Path) -> dict[str, Any]:
    """Verify byte consistency and fail-closed provenance of a hashed import."""
    try:
        doc, _ = _read_hash_snapshot(Path(path))
    except (OSError, GoldCaptureError) as exc:
        raise GoldOracleEvidenceError(f"invalid Pyth history SHA-256: {exc}") from exc
    _validate_evidence(doc)
    return doc


def import_pyth_history(
    source_path: Path, output_root: Path, *, channel: str,
    start_ts: int, end_ts: int,
) -> dict[str, Any]:
    """Preserve a supplied Pyth-format OHLC JSON; never claim authenticated data."""
    root = _checked_gold_root(output_root)
    request = pyth_history_request(start_ts, end_ts, channel=channel)
    try:
        raw_bytes = Path(source_path).read_bytes()
    except OSError as exc:
        raise GoldOracleEvidenceError("unable to read Pyth history file") from exc
    if not 1 <= len(raw_bytes) <= MAX_INPUT_BYTES:
        raise GoldOracleEvidenceError("Pyth history input length out of range")
    try:
        raw = raw_bytes.decode("utf-8")
        payload = json.loads(raw)
    except (UnicodeDecodeError, ValueError) as exc:
        raise GoldOracleEvidenceError("Pyth history input must be UTF-8 JSON") from exc
    stats = validate_history_payload(payload, start_ts=start_ts, end_ts=end_ts)
    digest = sha256(raw_bytes).hexdigest()
    report = {
        "series_ticker": "KXGOLD15M",
        "settlement_symbol": EXPECTED_PYTH_SYMBOL,
        "source_sha256": digest,
        **stats,
        "origin_authenticated": False,
        "settlement_verified": False,
        "training_ready": False,
        "blockers": [
            "PYTH_ORIGIN_UNATTESTED",
            "PYTH_HISTORY_CANDLE_ALIGNMENT_UNVERIFIED",
            "CONTRACT_START_END_RULE_MAPPING_UNVERIFIED",
        ],
        "limitations": [
            "Local JSON source cannot prove that it came from authenticated Pyth Pro",
            "Pyth OHLC bar time is not independently mapped to Kalshi's contract close label",
            "The API channel is not established as Kalshi's authoritative settlement channel",
            "No signed Pyth price update or independently checked Kalshi contract rule is supplied",
        ],
    }
    artifact = {
        "schema_version": 1,
        "artifact_kind": "unattested_pyth_gold_history_import",
        "request": request,
        "source_raw_utf8": raw,
        "source_sha256": digest,
        "raw_pyth_response": payload,
        "report": report,
    }
    _validate_evidence(artifact)
    path, artifact_digest = _write_immutable(root, "pyth_history_imports", artifact)
    return {**report, "artifact_path": str(path), "sha256": artifact_digest}
