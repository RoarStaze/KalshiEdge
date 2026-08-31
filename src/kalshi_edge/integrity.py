from __future__ import annotations

from enum import Enum


class SequenceStatus(str, Enum):
    FIRST = "first"
    CONTIGUOUS = "contiguous"
    DUPLICATE = "duplicate"
    GAP = "gap"
    OUT_OF_ORDER = "out_of_order"


class SequenceTracker:
    def __init__(self) -> None:
        self._last: dict[int, int] = {}

    def observe(self, sid: int, seq: int) -> SequenceStatus:
        previous = self._last.get(sid)
        if previous is None:
            self._last[sid] = seq
            return SequenceStatus.FIRST
        if seq == previous:
            return SequenceStatus.DUPLICATE
        if seq < previous:
            return SequenceStatus.OUT_OF_ORDER
        self._last[sid] = seq
        if seq == previous + 1:
            return SequenceStatus.CONTIGUOUS
        return SequenceStatus.GAP
