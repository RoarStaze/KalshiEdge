from __future__ import annotations

import pytest

from kalshi_edge.bootstrap import splits


NS = 1_000_000_000


def _markets(count: int = 14) -> tuple[splits.MarketIndex, ...]:
    base = 1_800_000_000_000_000_000
    return tuple(
        splits.MarketIndex(
            market_ticker=f"KXBTC15M-{index:02d}",
            split_group_id=f"KXBTC15M-{index:02d}",
            first_checkpoint_ts_ns=base + index * 900 * NS,
        )
        for index in range(count)
    )


def test_walk_forward_splits_are_expanding_chronological_and_embargoed() -> None:
    result = splits.make_walk_forward_splits(
        _markets(),
        min_train_markets=4,
        validation_markets=2,
        embargo_markets=1,
    )

    assert len(result) >= 2
    first, second = result[0], result[1]
    assert first.train_indices == (0, 1, 2, 3)
    assert first.embargo_indices == (4,)
    assert first.validation_indices == (5, 6)
    assert second.train_indices[: len(first.train_indices)] == first.train_indices
    assert len(second.train_indices) > len(first.train_indices)
    assert max(first.train_indices) < min(first.embargo_indices) < min(first.validation_indices)


def test_final_block_is_reserved_as_untouched_lockbox_for_every_fold() -> None:
    markets = _markets()
    result = splits.make_walk_forward_splits(
        markets,
        min_train_markets=4,
        validation_markets=2,
        embargo_markets=1,
    )
    expected_lockbox = (12, 13)

    assert result
    for fold in result:
        assert fold.lockbox_indices == expected_lockbox
        development = set(fold.train_indices) | set(fold.embargo_indices) | set(fold.validation_indices)
        assert development.isdisjoint(expected_lockbox)


def test_market_groups_are_exclusive_within_and_across_fold_roles() -> None:
    markets = _markets()
    result = splits.make_walk_forward_splits(
        markets,
        min_train_markets=4,
        validation_markets=2,
        embargo_markets=1,
    )

    for fold in result:
        role_sets = [
            {markets[index].split_group_id for index in fold.train_indices},
            {markets[index].split_group_id for index in fold.embargo_indices},
            {markets[index].split_group_id for index in fold.validation_indices},
            {markets[index].split_group_id for index in fold.lockbox_indices},
        ]
        assert sum(len(group_set) for group_set in role_sets) == len(set().union(*role_sets))


def test_duplicate_split_group_is_rejected_before_splitting() -> None:
    markets = list(_markets())
    markets[5] = markets[5].model_copy(update={"split_group_id": markets[4].split_group_id})

    with pytest.raises(splits.SplitError, match="duplicate split group"):
        splits.make_walk_forward_splits(
            markets,
            min_train_markets=4,
            validation_markets=2,
            embargo_markets=1,
        )


def test_nonchronological_market_index_is_rejected() -> None:
    markets = list(_markets())
    markets[5], markets[6] = markets[6], markets[5]

    with pytest.raises(splits.SplitError, match="chronological"):
        splits.make_walk_forward_splits(
            markets,
            min_train_markets=4,
            validation_markets=2,
            embargo_markets=1,
        )
