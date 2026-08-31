from __future__ import annotations

"""Chronological market-level split construction for bootstrap research."""

from typing import Sequence

from pydantic import BaseModel, ConfigDict, Field


class SplitError(RuntimeError):
    """Raised when a leakage-safe chronological split cannot be constructed."""


class MarketIndex(BaseModel):
    model_config = ConfigDict(frozen=True)

    market_ticker: str
    split_group_id: str
    first_checkpoint_ts_ns: int = Field(gt=0)


class WalkForwardSplit(BaseModel):
    model_config = ConfigDict(frozen=True)

    train_indices: tuple[int, ...]
    embargo_indices: tuple[int, ...]
    validation_indices: tuple[int, ...]
    lockbox_indices: tuple[int, ...]


def make_walk_forward_splits(
    markets: Sequence[MarketIndex],
    *,
    min_train_markets: int,
    validation_markets: int,
    embargo_markets: int,
) -> list[WalkForwardSplit]:
    """Build expanding chronological development folds with a reserved final lockbox."""
    if min_train_markets <= 0:
        raise SplitError("min_train_markets must be positive")
    if validation_markets <= 0:
        raise SplitError("validation_markets must be positive")
    if embargo_markets < 0:
        raise SplitError("embargo_markets cannot be negative")
    if not markets:
        raise SplitError("at least one market is required")

    group_ids = [market.split_group_id for market in markets]
    if len(set(group_ids)) != len(group_ids):
        raise SplitError("duplicate split group detected")

    timestamps = [market.first_checkpoint_ts_ns for market in markets]
    if any(current <= previous for previous, current in zip(timestamps, timestamps[1:])):
        raise SplitError("markets must be strictly chronological")

    lockbox_start = len(markets) - validation_markets
    if lockbox_start <= 0:
        raise SplitError("insufficient markets after reserving lockbox")
    lockbox_indices = tuple(range(lockbox_start, len(markets)))

    train_end = min_train_markets
    folds: list[WalkForwardSplit] = []
    while train_end < lockbox_start:
        embargo_start = train_end
        embargo_end = embargo_start + embargo_markets
        validation_start = embargo_end
        validation_end = validation_start + validation_markets
        if validation_end > lockbox_start:
            break

        folds.append(
            WalkForwardSplit(
                train_indices=tuple(range(0, train_end)),
                embargo_indices=tuple(range(embargo_start, embargo_end)),
                validation_indices=tuple(range(validation_start, validation_end)),
                lockbox_indices=lockbox_indices,
            )
        )
        train_end = validation_end

    if not folds:
        raise SplitError("insufficient markets for one development fold plus lockbox")
    return folds
