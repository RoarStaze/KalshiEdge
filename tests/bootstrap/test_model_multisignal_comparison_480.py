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
    assert "MODEL vs SIGNALS ONLY vs COMBINED" in printed
    assert "63%" in printed and "82%" in printed
    assert "Brier" in printed and "DESCRIPTIVE ONLY" in printed
    assert main(args + ["--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["matched_settled_markets"] == 6
    assert result["policy"] == EXPERIMENT_POLICY
    assert list(tmp_path.rglob("*")) == before


def test_real_hash_checked_forecast_snapshot_and_settlement_read_without_rewrite(tmp_path):
    from kalshi_edge.bootstrap.final_forecast import FinalForecastRecord, FinalForecastWriter
    from kalshi_edge.bootstrap.final_history import OfficialSettlement, _save_settlement
    from kalshi_edge.bootstrap.forward_feature_evidence_480 import (
        Forward480FeatureWriter, build_forward_snapshot,
    )
    from kalshi_edge.bootstrap.types import FeatureRow
    ts = START
    market = "KXBTC15M-TEST-INTEGRATION-00"
    final = FinalForecastRecord(
        market_ticker=market, strike=65000.,
        scheduled_ts_ns=ts, prediction_ts_ns=ts,
        checkpoint_seconds=480, seconds_remaining=480.,
        forecast="ABOVE", reason=None,
        confidence=.48, directional_probability=.74,
        p_yes=.74, p_no=.26,
        coin_flip_risk_z=1., coin_flip_assessment="LOW",
        model_hash=MODEL,
    )
    close = ts + 480_000_000_000
    forecast_path = FinalForecastWriter(tmp_path).save(final, market_close_ts_ns=close)
    assert forecast_path is not None
    feat = FeatureRow(
        market_ticker=market, checkpoint_ts_ns=ts, label_yes=0,
        features=_features(),
        source_max_ts_ns={"binance": ts-1_000_000_000, "brti": ts-1_000_000_000},
    )
    snapshot = build_forward_snapshot(final, forecast_path, feat)
    saved_path = Forward480FeatureWriter(tmp_path).save(snapshot)
    assert saved_path
    _save_settlement(tmp_path, final, OfficialSettlement(
        market_ticker=market, market_close_ts_ns=close,
        result="yes", actual_side="ABOVE",
        source="kalshi-public-market-api", market_status="settled",
        fetched_ts_ns=close + 60_000_000_000,
    ))
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    report = evaluate_paired_comparison(tmp_path, model_hash=MODEL)
    assert report["matched_settled_markets"] == 1
    assert report["predictors"]["model"]["brier"] == pytest.approx((.74-1)**2)
    assert {p: p.read_bytes() for p in tmp_path.rglob("*.json")} == before
    # Tampering with a saved feature invalidates the entire corresponding pair.
    contents = json.loads(saved_path.read_text())
    contents["features"]["btc_return_15s"] = -.005
    saved_path.write_text(json.dumps(contents))
    damaged = evaluate_paired_comparison(tmp_path, model_hash=MODEL)
    assert damaged["matched_settled_markets"] == 0
    assert damaged["excluded"]["missing_or_invalid_signal_or_settlement"] == 1


def test_standalone_signals_ignore_model_probability_and_blend_is_same_as_previous():
    from kalshi_edge.bootstrap.model_multisignal_comparison_480 import (
        experimental_signal_only_probability,
    )
    # Only the base_logit feature changes. Signal-only forecast must not
    # inadvertently inherit the model's predicted direction or confidence.
    signal_a=experimental_signal_only_probability(_vector(.25))
    signal_b=experimental_signal_only_probability(_vector(.75))
    assert signal_a == pytest.approx(signal_b, abs=1e-14)
    assert .0 < signal_a < 1.
    p=.76
    combined=experimental_multisignal_probability(p,_vector(p))
    assert combined == pytest.approx(
        FIXED_BLEND["base_model"]*p+
        (1-FIXED_BLEND["base_model"])*signal_a,
        abs=1e-14,
    )
    # No trained/fitted weights; preserve the original 75/15/6/4 blend.
    assert FIXED_BLEND == {
        "base_model":.75, "brti_reference":.15,
        "candle_reversal":.06,"signed_market_flow":.04,
    }


def test_three_predictors_evaluated_on_exactly_same_markets_all_eight_gates(monkeypatch,tmp_path):
    _sources(monkeypatch,_rows())
    report=evaluate_paired_comparison(tmp_path,model_hash=MODEL)
    assert report["matched_settled_markets"]==6
    assert report["predictors"]["signal_only"]["count"]==6
    assert report["predictors"]["model"]["count"]==6
    assert report["predictors"]["multisignal"]["count"]==6
    assert len(report["thresholds"])==8
    for row in report["thresholds"]:
        assert set(("model","signal_only","multisignal")).issubset(row)
        for name in ("model","signal_only","multisignal"):
            r=row[name]
            assert r["qualified"]+r["skipped"]==6
            assert r["correct"]+r["incorrect"]==r["qualified"]
            assert r["incremental_qualified"] is None or r["incremental_qualified"]>=0
        delta=row["delta_multisignal_minus_signal_only"]
        assert delta["qualified"]==row["multisignal"]["qualified"]-row["signal_only"]["qualified"]
        assert delta["correct"]==row["multisignal"]["correct"]-row["signal_only"]["correct"]
        assert delta["skipped"]==row["multisignal"]["skipped"]-row["signal_only"]["skipped"]
    assert report["predictors"]["signal_only"]["brier"] is not None
    assert report["direction_disagreements_pairs"]["model_vs_multisignal"]==report["direction_disagreements"]
    assert report["direction_disagreements_pairs"]["model_vs_signal_only"]>=0
    assert report["model_retraining"] is False
    assert report["live_gate_changed"] is False
    assert report["trades_executed"] is False


def test_standalone_signal_reacts_to_signed_brti_flow_and_candle_changes():
    from kalshi_edge.bootstrap.model_multisignal_comparison_480 import (
        experimental_signal_only_probability as signal,
    )
    x=signal(_vector(.75))
    assert signal(_vector(.75,brti_distance_to_strike_bps=-7.)) < x
    assert signal(_vector(.75,btc_return_15s=-.005,btc_return_60s=-.004)) < x
    assert signal(_vector(.75,btc_taker_buy_imbalance_60s=-.95,
                          kalshi_yes_taker_imbalance_60s=-.8)) < x


def test_three_way_text_json_and_no_model_mutation(monkeypatch,tmp_path,capsys):
    _sources(monkeypatch,_rows())
    args=["--root",str(tmp_path),"--model-hash",MODEL]
    assert main(args)==0
    table=capsys.readouterr().out
    assert "MODEL vs SIGNALS ONLY vs COMBINED" in table
    data_lines=[line.split() for line in table.splitlines() if line.lstrip().startswith(("63%","68%","70%","73%","75%","78%","80%","82%"))]
    assert sum(fields[1]=="signal_only" for fields in data_lines)==8
    assert sum(fields[1]=="multisignal" for fields in data_lines)==8
    assert sum(fields[1]=="model" for fields in data_lines)==8
    assert main(args+["--json"])==0
    j=json.loads(capsys.readouterr().out)
    assert "signal_only" in j["predictors"]
    assert "multisignal" in j["predictors"]
    assert "model" in j["predictors"]
    assert all("delta_multisignal_minus_signal_only" in row for row in j["thresholds"])
    assert list(tmp_path.rglob("*"))==[]


def test_missing_evidence_reports_all_three_without_inventing_accuracy(monkeypatch,tmp_path):
    _sources(monkeypatch,[])
    report=evaluate_paired_comparison(tmp_path,model_hash=MODEL)
    assert report["status"]=="NO_VERIFIED_PAIRED_EVIDENCE"
    for name in ("model","signal_only","multisignal"):
        assert report["predictors"][name]["brier"] is None
        assert all(row[name]["accuracy"] is None for row in report["thresholds"])
