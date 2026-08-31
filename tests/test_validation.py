from pathlib import Path

from kalshi_edge.protocol import normalize_ws_message
from kalshi_edge.storage import RawSegmentWriter
from kalshi_edge.validation import verify_dataset


def test_dataset_verification_passes_for_hash_valid_contiguous_stream(tmp_path: Path) -> None:
    writer = RawSegmentWriter(tmp_path, source="kalshi_ws", max_events=10, fsync_every=1)
    writer.append(normalize_ws_message({"type":"orderbook_snapshot","sid":1,"seq":1,"msg":{"market_ticker":"KXBTC15M-X","yes_dollars_fp":[],"no_dollars_fp":[]}}, receive_ts_ns=1_700_000_000_000_000_000))
    writer.append(normalize_ws_message({"type":"orderbook_delta","sid":1,"seq":2,"msg":{"market_ticker":"KXBTC15M-X","side":"yes","price_dollars":"0.4","delta_fp":"1","ts_ms":1700000001000}}, receive_ts_ns=1_700_000_001_000_000_000))
    writer.finalize()
    result = verify_dataset(tmp_path)
    assert result.passed
    assert result.segment_count == 1
    assert result.event_count == 2
    assert result.gaps == 0


def test_dataset_verification_fails_on_sequence_gap(tmp_path: Path) -> None:
    writer = RawSegmentWriter(tmp_path, source="kalshi_ws", max_events=10, fsync_every=1)
    writer.append(normalize_ws_message({"type":"orderbook_snapshot","sid":1,"seq":1,"msg":{"market_ticker":"KXBTC15M-X","yes_dollars_fp":[],"no_dollars_fp":[]}}, receive_ts_ns=1_700_000_000_000_000_000))
    writer.append(normalize_ws_message({"type":"orderbook_delta","sid":1,"seq":3,"msg":{"market_ticker":"KXBTC15M-X","side":"yes","price_dollars":"0.4","delta_fp":"1","ts_ms":1700000001000}}, receive_ts_ns=1_700_000_001_000_000_000))
    writer.finalize()
    result = verify_dataset(tmp_path)
    assert not result.passed
    assert result.gaps == 1


def test_dataset_sequence_scope_resets_for_new_connection(tmp_path: Path) -> None:
    writer = RawSegmentWriter(tmp_path, source="kalshi_ws", max_events=10, fsync_every=1)
    writer.append(normalize_ws_message({"type":"orderbook_snapshot","sid":1,"seq":10,"msg":{"market_ticker":"KXBTC15M-X","yes_dollars_fp":[],"no_dollars_fp":[]}}, receive_ts_ns=1_700_000_000_000_000_000, connection_id="conn-a"))
    writer.append(normalize_ws_message({"type":"orderbook_snapshot","sid":1,"seq":1,"msg":{"market_ticker":"KXBTC15M-X","yes_dollars_fp":[],"no_dollars_fp":[]}}, receive_ts_ns=1_700_000_001_000_000_000, connection_id="conn-b"))
    writer.finalize()
    result = verify_dataset(tmp_path)
    assert result.passed
    assert result.out_of_order == 0


def test_phase1_gate_report_evaluates_machine_checkable_evidence(tmp_path: Path) -> None:
    from kalshi_edge.protocol import normalize_rest_payload, normalize_ws_message
    from kalshi_edge.storage import RawSegmentWriter
    from kalshi_edge.validation import Phase1GateThresholds, evaluate_phase1_gate

    ws = RawSegmentWriter(tmp_path, source="kalshi_ws", max_events=100, fsync_every=1)
    rest = RawSegmentWriter(tmp_path, source="kalshi_rest", max_events=100, fsync_every=1)
    start = 1_700_000_000_000_000_000
    ws.append(normalize_ws_message(
        {"type": "orderbook_snapshot", "sid": 2, "seq": 1, "msg": {"market_ticker": "KXBTC15M-A", "yes_dollars_fp": [], "no_dollars_fp": []}},
        receive_ts_ns=start,
        connection_id="conn-1",
    ))
    ws.append(normalize_ws_message(
        {"type": "cfbenchmarks_value", "sid": 3, "seq": 1, "msg": {"index_id": "BRTI", "data": '{"time":1700000000000,"value":"68000"}', "last_60s_windowed_average_15min": {"value": "68000", "window_size": 60}}},
        receive_ts_ns=start + 3_600_000_000_000,
        connection_id="conn-1",
    ))
    ws.append(normalize_ws_message(
        {"type": "market_lifecycle_v2", "sid": 4, "msg": {"market_ticker": "KXBTC15M-B", "event_type": "created"}},
        receive_ts_ns=start + 3_600_000_000_001,
        connection_id="conn-2",
    ))
    ws.finalize()
    for message_type, payload in [
        ("series_snapshot", {"series": {"ticker": "KXBTC15M"}}),
        ("open_events_snapshot", {"events": []}),
        ("market_settlement_snapshot", {"market": {"ticker": "KXBTC15M-A", "result": "yes"}}),
    ]:
        rest.append(normalize_rest_payload(payload, message_type=message_type, receive_ts_ns=start, request_id="rest-1"))
    rest.finalize()
    from kalshi_edge.protocol import normalize_control_payload
    control = RawSegmentWriter(tmp_path, source="collector_control", max_events=10, fsync_every=1)
    control.append(normalize_control_payload(
        {"project_version": "0.1.0", "git_commit": "abc"},
        message_type="collector_session_snapshot",
        receive_ts_ns=start,
        session_id="conn-1",
    ))
    control.finalize()

    report = evaluate_phase1_gate(
        tmp_path,
        thresholds=Phase1GateThresholds(min_duration_hours=1, min_market_windows=2, min_connections=2),
    )
    assert report.machine_gate_passed
    assert report.unique_market_windows == 2
    assert report.brti_final_minute_events == 1
    assert report.settlement_snapshots == 1
    assert report.session_snapshots == 1
    assert report.known_git_session_snapshots == 1
    assert report.replay_deterministic


def test_dataset_verifier_rejects_event_payload_tampering_even_if_segment_hash_is_rewritten(tmp_path: Path) -> None:
    import hashlib
    import json
    from kalshi_edge.protocol import normalize_ws_message
    from kalshi_edge.storage import RawSegmentWriter
    from kalshi_edge.validation import verify_dataset

    writer = RawSegmentWriter(tmp_path, source="kalshi_ws", max_events=10, fsync_every=1)
    writer.append(normalize_ws_message(
        {"type": "trade", "sid": 11, "seq": 1, "msg": {"market_ticker": "KXBTC15M-X", "yes_price_dollars": "0.5", "ts_ms": 1}},
        receive_ts_ns=1_700_000_000_000_000_000,
        connection_id="tamper",
    ))
    segment = writer.finalize()
    row = json.loads(segment.data_path.read_text())
    row["payload"]["msg"]["yes_price_dollars"] = "0.9"
    rewritten = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    segment.data_path.write_text(rewritten, encoding="utf-8")
    digest = hashlib.sha256(rewritten.encode()).hexdigest()
    segment.hash_path.write_text(f"{digest}  {segment.data_path.name}\n", encoding="utf-8")

    result = verify_dataset(tmp_path)
    assert not result.passed
    assert result.bad_payload_hashes == 1


def test_dataset_verifier_counts_malformed_json_records(tmp_path: Path) -> None:
    import hashlib
    path = tmp_path / "raw/source=kalshi_ws/date=2026-08-31/hour=05/segment-bad.jsonl"
    path.parent.mkdir(parents=True)
    content = "{not-json}\n"
    path.write_text(content, encoding="utf-8")
    digest = hashlib.sha256(content.encode()).hexdigest()
    path.with_suffix(path.suffix + ".sha256").write_text(f"{digest}  {path.name}\n", encoding="utf-8")

    result = verify_dataset(tmp_path)
    assert not result.passed
    assert result.malformed_records == 1
