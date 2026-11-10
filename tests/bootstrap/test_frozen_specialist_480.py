"""Red/green checks for an immutable development-only 480s specialist JSON freeze."""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from kalshi_edge.bootstrap.frozen_specialist_480 import (
    fit_frozen_specialist,
    load_frozen_specialist,
    save_frozen_specialist,
    score_frozen_specialist,
)
from kalshi_edge.bootstrap.specialist_480 import FEATURES
from kalshi_edge.bootstrap.types import FeatureRow

NS = 1_000_000_000
BASE = 1_900_000_000 * NS


def make_row(i: int, *, positive=None, feature_overrides=None):
    yes = i % 2 == 0 if positive is None else bool(positive)
    timestamp = BASE + i * 900 * NS
    midpoint = .73 if yes else .27
    f = {
        "seconds_remaining": 480.0,
        "btc_normalized_distance": 3.0 if yes else -3.0,
        "btc_realized_vol_60s": .0003,
        "btc_realized_vol_300s": .0004,
        "btc_return_60s": .001 if yes else -.001,
        "btc_return_60s_available": 1.0,
        "kalshi_quote_available": 1.0,
        "kalshi_yes_bid": midpoint - .025,
        "kalshi_yes_ask": midpoint + .025,
        "kalshi_mid": midpoint,
        "kalshi_spread": .05,
        "kalshi_quote_staleness_seconds": 35.0,
    }
    if feature_overrides:
        f.update(feature_overrides)
    return FeatureRow(
        market_ticker=f"KXBTC15M-FREEZE-{i}",
        split_group_id=f"KXBTC15M-FREEZE-{i}",
        checkpoint_ts_ns=timestamp,
        label_yes=int(yes),
        features=f,
        source_max_ts_ns={
            "binance": timestamp - NS,
            "kalshi_candles": timestamp - 2 * NS,
        },
    )



def _training_rows():
    return [make_row(i) for i in range(100)]


def _frozen():
    return fit_frozen_specialist(
        _training_rows(), dataset_sha256="a" * 64,
        frozen_at_ns=BASE + 101 * 900 * NS,
    )


def test_frozen_numeric_weights_match_original_fixed_pipeline_not_a_refit():
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    training = _training_rows()
    fitted = make_pipeline(
        StandardScaler(),
        LogisticRegression(C=.15, solver="lbfgs", max_iter=1000, random_state=0),
    )
    fitted.fit([[row.features[n] for n in FEATURES] for row in training],
               [row.label_yes for row in training])
    frozen = _frozen()
    assert frozen["model"] == "KXBTC15M_FROZEN_480S_LOGISTIC_V1"
    assert frozen["feature_names"] == list(FEATURES)
    assert frozen["fit_markets"] == 100
    assert frozen["dataset_sha256"] == "a" * 64
    assert frozen["trained_through_ns"] == training[-1].checkpoint_ts_ns
    for i in (110, 111, 120):
        row = make_row(i)
        actual = score_frozen_specialist(frozen, row)
        expected = float(fitted.predict_proba([[row.features[n] for n in FEATURES]])[0][1])
        assert actual == pytest.approx(expected, abs=1e-12)
    assert frozen["frozen_at_ns"] > frozen["trained_through_ns"]


def test_freeze_does_not_require_reserved_calibration_or_lockbox_rows():
    spec = _frozen()
    assert "calibration" not in spec
    assert "lockbox" not in spec
    assert set(spec) == {"model", "feature_names", "dataset_sha256", "fit_markets",
                         "trained_through_ns", "frozen_at_ns", "mean", "scale",
                         "coefficients", "intercept", "sha256"}


def test_readonly_json_round_trip_and_tampering_refusal(tmp_path: Path):
    path = tmp_path / "frozen.json"
    saved = save_frozen_specialist(path, _frozen())
    assert saved == path
    reloaded = load_frozen_specialist(path)
    assert reloaded == _frozen()
    with pytest.raises(FileExistsError):
        save_frozen_specialist(path, _frozen())
    payload = json.loads(path.read_text("utf-8"))
    payload["intercept"] += .5
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="hash|integrity"):
        load_frozen_specialist(path)


@pytest.mark.parametrize("i,overrides", [
    (102, {"kalshi_quote_available":0.0}),
    (102, {"kalshi_yes_bid":.90,"kalshi_yes_ask":.10}),
    (102, {"kalshi_quote_staleness_seconds":121.0}),
    (102, {"btc_return_60s_available":0.0}),
    (102, {"btc_realized_vol_300s":0.0}),
    (102, {"btc_normalized_distance":math.inf}),
    (102, {"seconds_remaining":240.0}),
])
def test_refuse_uncausal_or_unusable_future_shadow(i, overrides):
    row = make_row(i, feature_overrides=overrides)
    with pytest.raises(ValueError):
        score_frozen_specialist(_frozen(), row)


def test_reject_beforedated_or_at_freeze_observations_and_future_sources():
    spec = _frozen()
    for i in (99, 101):
        with pytest.raises(ValueError, match="freeze|later"):
            score_frozen_specialist(spec, make_row(i))
    row = make_row(105)
    with pytest.raises(ValueError):
        score_frozen_specialist(spec, row.model_copy(update={
            "source_max_ts_ns":{"binance":row.checkpoint_ts_ns+1,
                                "kalshi_candles":row.checkpoint_ts_ns-NS}
        }))


def test_fitting_invalid_or_one_sided_training_is_refused():
    with pytest.raises(ValueError):
        fit_frozen_specialist(_training_rows()[:79], dataset_sha256="a"*64,
                              frozen_at_ns=BASE+101*900*NS)
    all_yes = [make_row(i, positive=True) for i in range(100)]
    with pytest.raises(ValueError):
        fit_frozen_specialist(all_yes, dataset_sha256="a"*64,
                              frozen_at_ns=BASE+101*900*NS)
    with pytest.raises(ValueError):
        fit_frozen_specialist(_training_rows(), dataset_sha256="x",
                              frozen_at_ns=BASE+101*900*NS)
    with pytest.raises(ValueError):
        fit_frozen_specialist(_training_rows(), dataset_sha256="a"*64,
                              frozen_at_ns=BASE)


def test_corrupted_frozen_model_structure_cannot_score():
    spec = _frozen()
    spec["coefficients"][0] = float("nan")
    with pytest.raises(ValueError):
        score_frozen_specialist(spec, make_row(110))


def test_frozen_shadow_scores_immutable_480s_locks_up_to_two_seconds_late():
    frozen = _frozen()
    late_but_valid = make_row(120, feature_overrides={"seconds_remaining": 478.1})
    score = score_frozen_specialist(frozen, late_but_valid)
    assert 0 <= score <= 1
    with pytest.raises(ValueError):
        score_frozen_specialist(frozen, make_row(121, feature_overrides={
            "seconds_remaining": 480.1,
        }))
