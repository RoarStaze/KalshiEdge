from pathlib import Path

import pytest

from kalshi_edge.collector import MessageProcessor, SequenceGapError, is_target_lifecycle_event
from kalshi_edge.storage import RawSegmentWriter


def test_target_lifecycle_event_recognizes_kxbtc15m_market_creation() -> None:
    payload = {
        "type": "market_lifecycle_v2",
        "sid": 13,
        "msg": {"market_ticker": "KXBTC15M-26AUG311215-15", "event_type": "created"},
    }
    assert is_target_lifecycle_event(payload, "KXBTC15M")


def test_message_processor_fails_closed_on_sequence_gap_after_persisting_raw_event(tmp_path: Path) -> None:
    writer = RawSegmentWriter(tmp_path, source="kalshi_ws", max_events=100, fsync_every=1)
    processor = MessageProcessor(writer)
    processor.process({"type": "orderbook_snapshot", "sid": 2, "seq": 10, "msg": {"market_ticker": "KXBTC15M-X", "yes_dollars_fp": [], "no_dollars_fp": []}}, receive_ts_ns=1)
    with pytest.raises(SequenceGapError):
        processor.process({"type": "orderbook_delta", "sid": 2, "seq": 12, "msg": {"market_ticker": "KXBTC15M-X", "side": "yes", "price_dollars": "0.4", "delta_fp": "1", "ts_ms": 1}}, receive_ts_ns=2)
    segment = writer.finalize()
    assert segment.event_count == 2


def test_target_lifecycle_event_includes_price_level_structure_update() -> None:
    payload = {
        "type": "market_lifecycle_v2",
        "sid": 13,
        "msg": {"market_ticker": "KXBTC15M-26AUG311215-15", "event_type": "price_level_structure_updated"},
    }
    assert is_target_lifecycle_event(payload, "KXBTC15M")


def test_message_processor_fails_closed_on_duplicate_sequence(tmp_path: Path) -> None:
    from kalshi_edge.collector import SequenceIntegrityError

    writer = RawSegmentWriter(tmp_path, source="kalshi_ws", max_events=100, fsync_every=1)
    processor = MessageProcessor(writer, connection_id="conn-a")
    msg = {"type": "trade", "sid": 11, "seq": 10, "msg": {"market_ticker": "KXBTC15M-X", "ts_ms": 1}}
    processor.process(msg, receive_ts_ns=1)
    with pytest.raises(SequenceIntegrityError):
        processor.process(msg, receive_ts_ns=2)
    segment = writer.finalize()
    assert segment.event_count == 2
