"""Regression tests for the existing classifier's causal multi-signal interactions.

Synthetic values test mathematical parity only, not realized Kalshi accuracy.
The active promoted model is unchanged until separate verified retraining.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from kalshi_edge.bootstrap.features import (
    CAUSAL_INTERACTION_FEATURE_NAMES,
    CAUSAL_INTERACTION_POLICY,
    derive_causal_interactions,
)
from kalshi_edge.bootstrap.training import _required_bundle_features, _table_to_rows


def raw_features():
    return {
        "btc_return_15s_available": 1.0,
        "btc_return_60s_available": 1.0,
        "btc_return_15s": -0.002,
        "btc_realized_vol_60s": 0.0004,
        "btc_base_volume_15s": 30.0,
        "btc_base_volume_60s": 90.0,
        "btc_taker_buy_imbalance_15s": -0.4,
        "btc_taker_buy_imbalance_60s": 0.1,
        "btc_normalized_distance": 0.25,
    }


def test_derived_signals_represent_buy_sell_flow_near_strike_reversal_and_volume_burst():
    features = derive_causal_interactions(raw_features())
    assert set(features) == set(CAUSAL_INTERACTION_FEATURE_NAMES)
    assert features["btc_interaction_available"] == 1.
    assert features["btc_signed_taker_volume_60s"] == pytest.approx(9.)
    assert features["btc_flow_imbalance_change_15v60"] == pytest.approx(-.5)
    assert features["btc_volume_burst_15v60"] == pytest.approx(4 * 30 / 90)
    assert features["btc_near_strike_signed_flow_60s"] == pytest.approx(.1 / 1.25)
    assert features["btc_near_strike_reversal_15s"] > 0.


def test_same_raw_features_yield_identical_frozen_historical_and_live_transform():
    old = raw_features()
    changed = dict(old, label_yes=1, future_settlement=99999.)
    assert derive_causal_interactions(old) == derive_causal_interactions(changed)
    assert derive_causal_interactions(old) == derive_causal_interactions(
        {**old, **derive_causal_interactions(old)}
    )
    assert CAUSAL_INTERACTION_POLICY


@pytest.mark.parametrize("change", [
    {"btc_return_15s_available": 0.},
    {"btc_realized_vol_60s": 0.},
    {"btc_base_volume_60s": 0.},
    {"btc_taker_buy_imbalance_60s": float("nan")},
    {"btc_normalized_distance": float("inf")},
    {"btc_taker_buy_imbalance_15s": 2.0},
])
def test_unavailable_inputs_are_explicitly_marked_not_usable(change):
    raw = raw_features()
    raw.update(change)
    fields = derive_causal_interactions(raw)
    assert set(fields) == set(CAUSAL_INTERACTION_FEATURE_NAMES)
    assert fields["btc_interaction_available"] == 0.
    assert all(value == 0. for value in fields.values())


def test_absent_source_columns_do_not_fabricate_derived_features():
    assert derive_causal_interactions({"btc_return_15s": .1}) == {}


def test_existing_verified_parquet_rows_gain_virtual_signals_without_data_rebuild():
    raw = raw_features()
    payload = {
        "market_ticker": ["KXBTC15M-TEST"],
        "market_date": ["2026-11-16"],
        "split_group_id": ["KXBTC15M-TEST"],
        "checkpoint_ts_ns": [1_800_000_000_000_000_000],
        "label_yes": [1],
        "source_ts__binance": [1_800_000_000_000_000_000 - 1_000_000_000],
    }
    payload.update({name: [value] for name, value in raw.items()})
    table = SimpleNamespace(
        to_pydict=lambda: payload,
        column_names=list(payload),
        num_rows=1,
    )
    row, = _table_to_rows(table, tuple(raw))
    assert row.features["btc_signed_taker_volume_60s"] == pytest.approx(9.)
    assert row.source_max_ts_ns["binance"] <= row.checkpoint_ts_ns
    assert row.label_yes == 1


def test_new_bundle_only_requires_derived_features_when_parquet_has_all_prerequisites():
    raw = raw_features()
    requested = (*CAUSAL_INTERACTION_FEATURE_NAMES, "btc_return_15s")
    required = _required_bundle_features(
        requested, (*raw, "btc_close", "strike", "seconds_remaining",
                    "btc_return_5s", "btc_return_5s_available",
                    "kalshi_mid", "kalshi_quote_available",
                    "kalshi_last_trade_yes", "kalshi_trade_available"),
    )
    assert set(CAUSAL_INTERACTION_FEATURE_NAMES).issubset(required)
    with pytest.raises(Exception, match="missing"):
        _required_bundle_features(
            requested,
            ("btc_return_15s", "btc_close", "strike", "seconds_remaining",
             "btc_return_5s", "btc_return_5s_available",
             "kalshi_mid", "kalshi_quote_available",
             "kalshi_last_trade_yes", "kalshi_trade_available"),
        )
