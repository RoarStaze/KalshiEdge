from pathlib import Path

from kalshi_edge.protocol import normalize_ws_message
from kalshi_edge.replay import replay_orderbook
from kalshi_edge.storage import RawSegmentWriter, verify_segment


def test_raw_segment_is_hashed_and_replay_is_deterministic(tmp_path: Path) -> None:
    writer = RawSegmentWriter(tmp_path, source="kalshi_ws", max_events=10, fsync_every=1)
    snapshot = normalize_ws_message(
        {
            "type": "orderbook_snapshot",
            "sid": 2,
            "seq": 1,
            "msg": {
                "market_ticker": "KXBTC15M-X",
                "yes_dollars_fp": [["0.4000", "5.00"]],
                "no_dollars_fp": [["0.5500", "7.00"]],
            },
        },
        receive_ts_ns=1_700_000_000_000_000_000,
    )
    delta = normalize_ws_message(
        {
            "type": "orderbook_delta",
            "sid": 2,
            "seq": 2,
            "msg": {
                "market_ticker": "KXBTC15M-X",
                "price_dollars": "0.4100",
                "delta_fp": "3.00",
                "side": "yes",
                "ts_ms": 1_700_000_001_000,
            },
        },
        receive_ts_ns=1_700_000_001_500_000_000,
    )
    writer.append(snapshot)
    writer.append(delta)
    segment = writer.finalize()

    assert verify_segment(segment.data_path, segment.hash_path)
    first = replay_orderbook(segment.data_path, "KXBTC15M-X")
    second = replay_orderbook(segment.data_path, "KXBTC15M-X")
    assert first.state_hash == second.state_hash
    assert first.best_yes_bid == "0.4100"


def test_replay_fails_closed_on_sequence_gap(tmp_path: Path) -> None:
    import pytest
    from kalshi_edge.replay import ReplayIntegrityError

    writer = RawSegmentWriter(tmp_path, source="kalshi_ws", max_events=10, fsync_every=1)
    writer.append(normalize_ws_message(
        {"type": "orderbook_snapshot", "sid": 2, "seq": 1, "msg": {"market_ticker": "KXBTC15M-GAP", "yes_dollars_fp": [], "no_dollars_fp": []}},
        receive_ts_ns=1_700_000_000_000_000_000,
        connection_id="gap-conn",
    ))
    writer.append(normalize_ws_message(
        {"type": "orderbook_delta", "sid": 2, "seq": 3, "msg": {"market_ticker": "KXBTC15M-GAP", "price_dollars": "0.4", "delta_fp": "1", "side": "yes", "ts_ms": 2}},
        receive_ts_ns=1_700_000_000_000_000_100,
        connection_id="gap-conn",
    ))
    segment = writer.finalize()

    with pytest.raises(ReplayIntegrityError):
        replay_orderbook(segment.data_path, "KXBTC15M-GAP")


def test_replay_sequence_validation_includes_interleaved_markets_on_same_subscription(tmp_path: Path) -> None:
    writer = RawSegmentWriter(tmp_path, source="kalshi_ws", max_events=10, fsync_every=1)
    messages = [
        {"type": "orderbook_snapshot", "sid": 2, "seq": 1, "msg": {"market_ticker": "KXBTC15M-A", "yes_dollars_fp": [["0.4000", "1"]], "no_dollars_fp": [["0.5500", "1"]]}},
        {"type": "orderbook_snapshot", "sid": 2, "seq": 2, "msg": {"market_ticker": "KXBTC15M-B", "yes_dollars_fp": [["0.3000", "1"]], "no_dollars_fp": [["0.6500", "1"]]}},
        {"type": "orderbook_delta", "sid": 2, "seq": 3, "msg": {"market_ticker": "KXBTC15M-A", "price_dollars": "0.4100", "delta_fp": "1", "side": "yes", "ts_ms": 3}},
    ]
    for i, message in enumerate(messages):
        writer.append(normalize_ws_message(message, receive_ts_ns=1_700_000_000_000_000_000 + i, connection_id="shared-sub"))
    segment = writer.finalize()

    result = replay_orderbook(segment.data_path, "KXBTC15M-A")
    assert result.gaps == 0
    assert result.best_yes_bid == "0.4100"


def test_replay_dataset_continues_state_across_segments(tmp_path: Path) -> None:
    from kalshi_edge.replay import replay_dataset

    writer = RawSegmentWriter(tmp_path, source="kalshi_ws", max_events=1, fsync_every=1)
    first = writer.append(normalize_ws_message(
        {"type": "orderbook_snapshot", "sid": 2, "seq": 1, "msg": {"market_ticker": "KXBTC15M-X", "yes_dollars_fp": [["0.4000", "2"]], "no_dollars_fp": [["0.5500", "2"]]}},
        receive_ts_ns=1_700_000_000_000_000_000,
        connection_id="conn-dataset",
    ))
    second = writer.append(normalize_ws_message(
        {"type": "orderbook_delta", "sid": 2, "seq": 2, "msg": {"market_ticker": "KXBTC15M-X", "price_dollars": "0.4200", "delta_fp": "1", "side": "yes", "ts_ms": 2}},
        receive_ts_ns=1_700_000_000_000_000_100,
        connection_id="conn-dataset",
    ))
    assert first is not None and second is not None

    result = replay_dataset(tmp_path, "KXBTC15M-X")
    assert result.gaps == 0
    assert result.best_yes_bid == "0.4200"
