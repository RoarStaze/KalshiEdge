from kalshi_edge.integrity import SequenceStatus, SequenceTracker


def test_sequence_tracker_classifies_contiguous_duplicate_gap_and_out_of_order() -> None:
    tracker = SequenceTracker()
    assert tracker.observe(7, 100) is SequenceStatus.FIRST
    assert tracker.observe(7, 101) is SequenceStatus.CONTIGUOUS
    assert tracker.observe(7, 101) is SequenceStatus.DUPLICATE
    assert tracker.observe(7, 104) is SequenceStatus.GAP
    assert tracker.observe(7, 103) is SequenceStatus.OUT_OF_ORDER
