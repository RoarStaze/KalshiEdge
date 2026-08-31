from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import base64
import json
from pathlib import Path
from typing import Any

from .integrity import SequenceStatus, SequenceTracker
from .orderbook import OrderBook
from .protocol import SCHEMA_VERSION
from .rawio import iter_raw_events, iter_segment_paths
from .storage import verify_segment


@dataclass(frozen=True)
class DatasetVerification:
    passed: bool
    segment_count: int
    event_count: int
    bad_hashes: int
    bad_payload_hashes: int
    bad_wire_hashes: int
    malformed_records: int
    unsupported_schema_versions: int
    gaps: int
    duplicates: int
    out_of_order: int

    def to_dict(self) -> dict[str, int | bool]:
        return asdict(self)


@dataclass(frozen=True)
class Phase1GateThresholds:
    min_duration_hours: float = 24.0
    min_market_windows: int = 90
    min_connections: int = 2


@dataclass(frozen=True)
class Phase1GateReport:
    machine_gate_passed: bool
    dataset_integrity_passed: bool
    duration_hours: float
    unique_market_windows: int
    connection_count: int
    brti_events: int
    brti_final_minute_events: int
    lifecycle_events: int
    series_snapshots: int
    open_events_snapshots: int
    settlement_snapshots: int
    session_snapshots: int
    known_git_session_snapshots: int
    replay_deterministic: bool
    replay_fingerprint: str
    manual_gate_items_remaining: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["manual_gate_items_remaining"] = list(self.manual_gate_items_remaining)
        return result


def _canonical_payload_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(canonical.encode("utf-8")).hexdigest()


def _wire_hash(event: dict[str, Any]) -> str | None:
    encoding = event.get("wire_encoding")
    wire_payload = event.get("wire_payload")
    if not isinstance(wire_payload, str):
        return None
    try:
        if encoding in {"utf-8", "canonical-json"}:
            data = wire_payload.encode("utf-8")
        elif encoding == "base64":
            data = base64.b64decode(wire_payload, validate=True)
        else:
            return None
    except (ValueError, UnicodeError):
        return None
    return sha256(data).hexdigest()


def verify_dataset(root: str | Path) -> DatasetVerification:
    root_path = Path(root)
    trackers: dict[str, SequenceTracker] = {}
    segment_count = event_count = bad_hashes = 0
    bad_payload_hashes = bad_wire_hashes = malformed_records = unsupported_schema_versions = 0
    gaps = duplicates = out_of_order = 0
    for data_path in iter_segment_paths(root_path):
        segment_count += 1
        hash_path = data_path.with_suffix(data_path.suffix + ".sha256")
        if not hash_path.exists() or not verify_segment(data_path, hash_path):
            bad_hashes += 1
        with data_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                event_count += 1
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    malformed_records += 1
                    continue
                if not isinstance(event, dict):
                    malformed_records += 1
                    continue
                version = event.get("schema_version")
                if version != SCHEMA_VERSION:
                    unsupported_schema_versions += 1
                payload = event.get("payload")
                if not isinstance(payload, dict) or event.get("payload_sha256") != _canonical_payload_hash(payload):
                    bad_payload_hashes += 1
                if version == SCHEMA_VERSION:
                    computed_wire_hash = _wire_hash(event)
                    if computed_wire_hash is None or event.get("wire_sha256") != computed_wire_hash:
                        bad_wire_hashes += 1
                sid, seq = event.get("sid"), event.get("seq")
                if sid is None or seq is None:
                    continue
                try:
                    sid_int, seq_int = int(sid), int(seq)
                except (TypeError, ValueError):
                    malformed_records += 1
                    continue
                connection_id = str(event.get("connection_id", "legacy"))
                tracker = trackers.setdefault(connection_id, SequenceTracker())
                status = tracker.observe(sid_int, seq_int)
                gaps += status is SequenceStatus.GAP
                duplicates += status is SequenceStatus.DUPLICATE
                out_of_order += status is SequenceStatus.OUT_OF_ORDER
    passed = (
        segment_count > 0
        and bad_hashes == bad_payload_hashes == bad_wire_hashes == malformed_records == unsupported_schema_versions == 0
        and gaps == duplicates == out_of_order == 0
    )
    return DatasetVerification(
        passed=passed,
        segment_count=segment_count,
        event_count=event_count,
        bad_hashes=bad_hashes,
        bad_payload_hashes=bad_payload_hashes,
        bad_wire_hashes=bad_wire_hashes,
        malformed_records=malformed_records,
        unsupported_schema_versions=unsupported_schema_versions,
        gaps=gaps,
        duplicates=duplicates,
        out_of_order=out_of_order,
    )


def _replay_fingerprint(root: str | Path) -> str:
    books: dict[str, OrderBook] = {}
    for event in iter_raw_events(root, source="kalshi_ws", skip_malformed=True):
        ticker = event.get("market_ticker")
        if not ticker:
            continue
        payload = event.get("payload") or {}
        msg = payload.get("msg") or {}
        msg_type = payload.get("type")
        if msg_type not in {"orderbook_snapshot", "orderbook_delta"}:
            continue
        book = books.setdefault(str(ticker), OrderBook(str(ticker)))
        if msg_type == "orderbook_snapshot":
            book.apply_snapshot(
                yes_dollars_fp=msg.get("yes_dollars_fp", []),
                no_dollars_fp=msg.get("no_dollars_fp", []),
            )
        else:
            book.apply_delta(side=msg["side"], price_dollars=msg["price_dollars"], delta_fp=msg["delta_fp"])
    states = {ticker: book.state_hash() for ticker, book in sorted(books.items())}
    canonical = json.dumps(states, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


def evaluate_phase1_gate(
    root: str | Path,
    *,
    thresholds: Phase1GateThresholds | None = None,
    series_ticker: str = "KXBTC15M",
) -> Phase1GateReport:
    limits = thresholds or Phase1GateThresholds()
    integrity = verify_dataset(root)
    min_ts: int | None = None
    max_ts: int | None = None
    markets: set[str] = set()
    connections: set[str] = set()
    brti_events = brti_final_minute_events = lifecycle_events = 0
    series_snapshots = open_events_snapshots = settlement_snapshots = session_snapshots = known_git_session_snapshots = 0

    for event in iter_raw_events(root, skip_malformed=True):
        receive_ts = event.get("receive_ts_ns")
        if isinstance(receive_ts, int):
            min_ts = receive_ts if min_ts is None else min(min_ts, receive_ts)
            max_ts = receive_ts if max_ts is None else max(max_ts, receive_ts)
        source = event.get("source")
        connection_id = event.get("connection_id")
        if source == "kalshi_ws" and connection_id and connection_id != "unknown":
            connections.add(str(connection_id))
        ticker = event.get("market_ticker")
        if isinstance(ticker, str) and ticker.startswith(f"{series_ticker}-"):
            markets.add(ticker)
        message_type = event.get("message_type")
        payload = event.get("payload") or {}
        msg = payload.get("msg") or {}
        if message_type == "cfbenchmarks_value" and msg.get("index_id") == "BRTI":
            brti_events += 1
            if msg.get("last_60s_windowed_average_15min") is not None:
                brti_final_minute_events += 1
        elif message_type == "market_lifecycle_v2":
            lifecycle_ticker = msg.get("market_ticker")
            if isinstance(lifecycle_ticker, str) and lifecycle_ticker.startswith(f"{series_ticker}-"):
                markets.add(lifecycle_ticker)
                lifecycle_events += 1
        elif message_type == "series_snapshot":
            series_snapshots += 1
        elif message_type == "open_events_snapshot":
            open_events_snapshots += 1
            for event_item in payload.get("events", []):
                if event_item.get("series_ticker") != series_ticker:
                    continue
                for market in event_item.get("markets", []):
                    mt = market.get("ticker")
                    if mt:
                        markets.add(str(mt))
        elif message_type == "collector_session_snapshot":
            session_snapshots += 1
            git_commit = payload.get("git_commit")
            if isinstance(git_commit, str) and git_commit and git_commit != "unknown":
                known_git_session_snapshots += 1
        elif message_type == "market_settlement_snapshot":
            settlement_snapshots += 1
            mt = (payload.get("market") or {}).get("ticker")
            if mt:
                markets.add(str(mt))

    duration_hours = 0.0 if min_ts is None or max_ts is None else (max_ts - min_ts) / 3_600_000_000_000
    first_fingerprint = _replay_fingerprint(root)
    second_fingerprint = _replay_fingerprint(root)
    replay_deterministic = first_fingerprint == second_fingerprint
    machine_gate_passed = all([
        integrity.passed,
        duration_hours >= limits.min_duration_hours,
        len(markets) >= limits.min_market_windows,
        len(connections) >= limits.min_connections,
        brti_events > 0,
        brti_final_minute_events > 0,
        lifecycle_events > 0,
        series_snapshots > 0,
        open_events_snapshots > 0,
        settlement_snapshots > 0,
        session_snapshots > 0,
        known_git_session_snapshots == session_snapshots,
        replay_deterministic,
    ])
    return Phase1GateReport(
        machine_gate_passed=machine_gate_passed,
        dataset_integrity_passed=integrity.passed,
        duration_hours=duration_hours,
        unique_market_windows=len(markets),
        connection_count=len(connections),
        brti_events=brti_events,
        brti_final_minute_events=brti_final_minute_events,
        lifecycle_events=lifecycle_events,
        series_snapshots=series_snapshots,
        open_events_snapshots=open_events_snapshots,
        settlement_snapshots=settlement_snapshots,
        session_snapshots=session_snapshots,
        known_git_session_snapshots=known_git_session_snapshots,
        replay_deterministic=replay_deterministic,
        replay_fingerprint=first_fingerprint,
        manual_gate_items_remaining=(
            "confirm a clean reconnect/resubscription cycle was operationally reconciled",
            "confirm collector restart did not mutate previously finalized segments",
        ),
    )
