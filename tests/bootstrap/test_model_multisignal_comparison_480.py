"""Read-only same-market model vs fixed/no-training multi-signal experiment.

Fixtures are synthetic tests of accounting, NOT verified predictive improvement.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from kalshi_edge.bootstrap.signal_fusion_480 import _Example, build_signal_vector
from kalshi_edge.bootstrap.model_multisignal_comparison_480 import (
    THRESHOLDS_PERCENT,
    EXPERIMENT_POLICY,
    FIXED_BLEND,
    experimental_multisignal_probability,
    evaluate_paired_comparison,
    main,
)

MODEL = "a" * 64
START = 1_900_000_000_000_000_000


def _features(**update):
    src = {
        "brti_distance_to_strike_bps": 7.0,
        "brti_binance_gap_bps": 1.0,
        "btc_return_15s": 0.001,
        "btc_return_15s_available": 1.0,
        "btc_return_60s": 0.0015,
        "btc_return_60s_available": 1.0,
        "btc_taker_buy_imbalance_60s": .55,
        "btc_base_volume_60s": 42.0,
        "kalshi_yes_taker_imbalance_60s": .25,
    }
    return {**src, **update}


def _vector(base=.76, risk=1.0, **signal_changes):
    result = build_signal_vector(base, _features(**signal_changes), risk_z=risk)
    assert result is not None
    return result


def _rows():
    probabilities = [.60, .69, .74, .79, .81, .90]
    outcomes = [1, 0, 1, 0, 1, 1]
    risks = [1., 1., 1., 1., .1, 1.]
    records = []
    for i, (p, outcome, risk) in enumerate(zip(probabilities, outcomes, risks)):
        records.append(_Example(
            ticker=f"KXBTC15M-TEST-{i}",
            checkpoint_ts_ns=START + i * 900_000_000_000,
            fetched_ts_ns=START + i * 900_000_000_000 + 600_000_000_000,
            probability=p,
            risk_z=risk,
            vector=_vector(p, risk),
            actual_yes=outcome,
        ))
    return records


def _sources(monkeypatch, rows):
    import kalshi_edge.bootstrap.model_multisignal_comparison_480 as module
    monkeypatch.setattr(module, "_verified_examples", lambda root, sha: (
        rows, {"same_model_finals": len(rows), "eligible": len(rows), "missing_or_invalid": 0}
    ))
    monkeypatch.setattr(module, "_forecasts", lambda root: [
        SimpleNamespace(market_ticker=r.ticker, reason=None) for r in rows
    ])
    return module


def test_experiment_predeclared_and_no_fit_or_live_gate_changes():
    assert THRESHOLDS_PERCENT == (63, 68, 70, 73, 75, 78, 80, 82)
    assert EXPERIMENT_POLICY.startswith("read-only-no-fit-")
    assert sum(FIXED_BLEND.values()) == pytest.approx(1.)
    assert FIXED_BLEND["base_model"] > .5


def test_multisignal_uses_brti_flip_candle_and_buy_sell_flow_without_labels():
    p = .76
    positive = experimental_multisignal_probability(p, _vector(p))
    assert 0 < positive < 1
    assert positive == experimental_multisignal_probability(p, _vector(p))
    opposite_brti = experimental_multisignal_probability(
        p, _vector(p, brti_distance_to_strike_bps=-7.)
    )
    assert opposite_brti < positive
    low_flip_risk = experimental_multisignal_probability(
        p, _vector(p, risk=.1)
    )
    assert low_flip_risk != pytest.approx(positive)
    bearish_flow = experimental_multisignal_probability(
        p, _vector(p, btc_taker_buy_imbalance_60s=-.8,
                   kalshi_yes_taker_imbalance_60s=-.65)
    )
    assert bearish_flow < positive
    bearish_candles = experimental_multisignal_probability(
        p, _vector(p, btc_return_15s=-.004, btc_return_60s=-.005)
    )
    assert bearish_candles < positive
    strong_basis_divergence = experimental_multisignal_probability(
        p, _vector(p, brti_binance_gap_bps=100.)
    )
    assert strong_basis_divergence != pytest.approx(positive)


@pytest.mark.parametrize("bad", [
    (float("nan"), _vector()),
    (-.1, _vector()),
    (.5, (1., 2.)),
    (.7, tuple(float("inf") for _ in range(10))),
])
def test_unusable_signal_vector_refuses_instead_of_fabricating(bad):
    with pytest.raises(ValueError):
        experimental_multisignal_probability(*bad)


def test_paired_sweep_same_checkpoint_cohort_both_predictors_all_gates(monkeypatch, tmp_path):
    _sources(monkeypatch, _rows())
    report = evaluate_paired_comparison(tmp_path, model_hash=MODEL)
    assert report["status"] == "DESCRIPTIVE_ONLY"
    assert report["matched_settled_markets"] == 6
    assert report["model_retraining"] is False
    assert report["live_gate_changed"] is False
    assert report["trades_executed"] is False
    assert report["predictors"]["model"]["count"] == 6
    assert report["predictors"]["multisignal"]["count"] == 6
    assert math.isfinite(report["predictors"]["model"]["brier"])
    assert math.isfinite(report["predictors"]["multisignal"]["brier"])
    assert math.isfinite(report["predictors"]["multisignal"]["log_loss"])
    assert len(report["thresholds"]) == 8
    for row in report["thresholds"]:
        assert row["threshold_percent"] in THRESHOLDS_PERCENT
        model = row["model"]
        signals = row["multisignal"]
        for item in (model, signals):
            assert item["qualified"] + item["skipped"] == 6
            assert item["correct"] + item["incorrect"] == item["qualified"]
            assert item["accuracy"] is None or 0 <= item["accuracy"] <= 1
            assert 0 <= item["coverage"] <= 1
        assert row["delta_multisignal_minus_model"]["qualified"] == (
            signals["qualified"] - model["qualified"]
        )
        assert row["delta_multisignal_minus_model"]["correct"] == (
            signals["correct"] - model["correct"]
        )
        assert row["delta_multisignal_minus_model"]["skipped"] == (
            signals["skipped"] - model["skipped"]
        )
    q = {r["threshold_percent"]: r["model"] for r in report["thresholds"]}
    assert q[63]["qualified"] == 4
    assert q[78]["qualified"] == 2
    assert q[82]["qualified"] == 1
    assert q[63]["skipped"] == 2  # .60 gate + .81 near-strike flip veto
    assert q[63]["correct"] == 2
    # The recorded high flip-risk market must not count as eligible even if the
    # candidate's directional probability changes.
    assert report["excluded"]["invalid_or_missing_signal"] == 0


def test_incremental_accuracy_uses_just_next_added_probability_band(monkeypatch, tmp_path):
    _sources(monkeypatch, _rows())
    report = evaluate_paired_comparison(tmp_path, model_hash=MODEL)
    rows = {row["threshold_percent"]: row for row in report["thresholds"]}
    # For model 78% -> 75% no new market; strict >75 excludes equality .75.
    assert rows[75]["model"]["incremental_qualified"] == 0
    assert rows[75]["model"]["incremental_accuracy"] is None
    # Model 70% -> 68% adds .69, which was incorrect.
    assert rows[68]["model"]["incremental_qualified"] == 1
    assert rows[68]["model"]["incremental_correct"] == 0
    assert rows[68]["model"]["incremental_accuracy"] == 0.
    assert rows[82]["model"]["incremental_qualified"] is None
    assert rows[82]["multisignal"]["incremental_qualified"] is None


def test_non_probability_skip_is_excluded_not_reinterpreted(monkeypatch, tmp_path):
    rows = _rows()
    module = _sources(monkeypatch, rows)
    monkeypatch.setattr(module, "_forecasts", lambda root: [
        SimpleNamespace(market_ticker=r.ticker,
                        reason="STALE_BRTI" if r == rows[0] else None)
        for r in rows
    ])
    report = evaluate_paired_comparison(tmp_path, model_hash=MODEL)
    assert report["matched_settled_markets"] == 5
    assert report["excluded"]["non_probability_skip"] == 1
    assert report["predictors"]["model"]["count"] == 5


def test_missing_evidence_not_false_accuracy(monkeypatch, tmp_path):
    _sources(monkeypatch, [])
    result = evaluate_paired_comparison(tmp_path, model_hash=MODEL)
    assert result["status"] == "NO_VERIFIED_PAIRED_EVIDENCE"
    assert result["matched_settled_markets"] == 0
    assert result["predictors"]["model"]["brier"] is None
    assert all(x["model"]["accuracy"] is None for x in result["thresholds"])


def test_cli_read_only_text_and_json_and_explicit_model_filter(monkeypatch, tmp_path, capsys):
    _sources(monkeypatch, _rows())
    before = list(tmp_path.rglob("*"))
    args = ["--root", str(tmp_path), "--model-hash", MODEL]
    assert main(args) == 0
    printed = capsys.readouterr().out
    assert "MODEL vs MULTISIGNAL" in printed
    assert "63%" in printed and "82%" in printed
    assert "Brier" in printed and "DESCRIPTIVE ONLY" in printed
    assert main(args + ["--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["matched_settled_markets"] == 6
    assert result["policy"] == EXPERIMENT_POLICY
    assert list(tmp_path.rglob("*")) == before
