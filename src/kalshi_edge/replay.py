from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Iterable, Iterator

from .integrity import SequenceStatus, SequenceTracker
from .orderbook import OrderBook
from .rawio import iter_segment_paths


class ReplayIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReplayResult:
    market_ticker: str
    state_hash: str
    best_yes_bid: str | None
    best_yes_ask: str | None
    gaps: int
    duplicates: int
    out_of_order: int


def _events_from_paths(paths: Iterable[Path]) -> Iterator[dict]:
    for path in paths:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                yield json.loads(line)


def _replay_events(events: Iterable[dict], market_ticker: str, *, strict: bool) -> ReplayResult:
    book = OrderBook(market_ticker)
    trackers: dict[str, SequenceTracker] = {}
    gaps = duplicates = out_of_order = 0
    for event in events:
        # Sequence integrity is subscription-stream scoped, not market scoped. Observe
        # every sequenced event first so interleaved markets on one SID don't look gappy.
        sid, seq = event.get("sid"), event.get("seq")
        if sid is not None and seq is not None:
            connection_id = str(event.get("connection_id", "legacy"))
            tracker = trackers.setdefault(connection_id, SequenceTracker())
            status = tracker.observe(int(sid), int(seq))
            gaps += status is SequenceStatus.GAP
            duplicates += status is SequenceStatus.DUPLICATE
            out_of_order += status is SequenceStatus.OUT_OF_ORDER
            if strict and status in {SequenceStatus.GAP, SequenceStatus.DUPLICATE, SequenceStatus.OUT_OF_ORDER}:
                raise ReplayIntegrityError(
                    f"invalid sequence during replay connection={connection_id} sid={sid} seq={seq} status={status.value}"
                )

        if event.get("market_ticker") != market_ticker:
            continue
        payload = event["payload"]
        msg_type = payload.get("type")
        msg = payload.get("msg") or {}
        if msg_type == "orderbook_snapshot":
            book.apply_snapshot(
                yes_dollars_fp=msg.get("yes_dollars_fp", []),
                no_dollars_fp=msg.get("no_dollars_fp", []),
            )
        elif msg_type == "orderbook_delta":
            book.apply_delta(
                side=msg["side"],
                price_dollars=msg["price_dollars"],
                delta_fp=msg["delta_fp"],
            )
    return ReplayResult(
        market_ticker=market_ticker,
        state_hash=book.state_hash(),
        best_yes_bid=None if book.best_yes_bid is None else str(book.best_yes_bid),
        best_yes_ask=None if book.best_yes_ask is None else str(book.best_yes_ask),
        gaps=gaps,
        duplicates=duplicates,
        out_of_order=out_of_order,
    )


def replay_orderbook(path: str | Path, market_ticker: str, *, strict: bool = True) -> ReplayResult:
    return _replay_events(_events_from_paths([Path(path)]), market_ticker, strict=strict)


def replay_dataset(root: str | Path, market_ticker: str, *, strict: bool = True) -> ReplayResult:
    paths = iter_segment_paths(root, source="kalshi_ws")
    if not paths:
        raise FileNotFoundError(f"no kalshi_ws raw segments found under {root}")
    return _replay_events(_events_from_paths(paths), market_ticker, strict=strict)
