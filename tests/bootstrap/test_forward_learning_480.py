"""Offline tests for 480s forward training from immutable, settled snapshots.

Neither fixtures nor the candidate evaluator may authorize live AutoTrade.
"""
from __future__ import annotations

from kalshi_edge.bootstrap.types import FeatureRow
from kalshi_edge.bootstrap import forward_learning_480

NS = 1_000_000_000
BASE = 1_900_000_000 * NS


def sample(i: int, *, healthy: bool = True) -> FeatureRow:
    yes = (i % 2) == 0
    ts = BASE + i * 900 * NS
    midpoint = .67 if yes else .33
    features = {
        "seconds_remaining": 480.0,
        "btc_normalized_distance": 2.0 if yes else -2.0,
        "btc_realized_vol_60s": .0003,
        "btc_realized_vol_300s": .0004,
        "btc_return_60s": .001 if yes else -.001,
        "btc_return_60s_available": 1.,
        "kalshi_quote_available": 1.,
        "kalshi_yes_bid": midpoint - .02,
        "kalshi_yes_ask": midpoint + .02,
        "kalshi_mid": midpoint,
        "kalshi_spread": .04,
        "kalshi_quote_staleness_seconds": 2.,
    }
    if not healthy:
        features["kalshi_quote_available"] = 0.
    return FeatureRow(
        market_ticker=f"KXBTC15M-TEST-FORWARD-{i}",
        split_group_id=f"KXBTC15M-TEST-FORWARD-{i}",
        checkpoint_ts_ns=ts,
        label_yes=int(yes),
        features=features,
        source_max_ts_ns={"binance": ts-NS, "kalshi_candles": ts-2*NS},
    )


def incumbent(rows, *, skip_all=False):
    return {
        row.market_ticker: {
            "market_ticker": row.market_ticker,
            "p_yes": .70 if row.label_yes else .30,
            "forecast": "SKIP" if skip_all else (
                "ABOVE" if row.label_yes else "BELOW"
            ),
            "coin_flip_assessment": "LOW",
        }
        for row in rows
    }


def test_forward_readiness_is_independent_of_legacy_parquet_472_count():
    rows = [sample(i) for i in range(75)]
    result = forward_learning_480.assess_forward_training_readiness(
        rows, audit={"settled_480s": 75, "missing_snapshot_records": 7},
    )
    assert result["eligible_480_markets"] == 75
    assert result["required_markets"] == 120
    assert result["ready"] is False
    assert result["progress_percent"] < 100
    assert result["missing_snapshot_records"] == 7
    assert result["reason"]
    assert result["live_model_changed"] is False


def test_120_eligible_chronological_both_class_markets_make_candidate_ready():
    rows = [sample(i) for i in range(120)]
    readiness = forward_learning_480.assess_forward_training_readiness(rows)
    assert readiness["ready"] is True
    assert readiness["progress_percent"] == 100
    train, valid = forward_learning_480.partition_forward_training(rows)
    assert len(train) >= 80
    assert len(valid) >= 40
    assert train[-1].checkpoint_ts_ns < valid[0].checkpoint_ts_ns
    assert len(set(r.market_ticker for r in train + valid)) == 120


def test_unhealthy_future_or_duplicate_rows_never_unblock_training():
    rows = [sample(i) for i in range(119)]
    rows += [sample(118)]  # Duplicate ticker, not 120 independent markets
    rows += [sample(120, healthy=False)]
    rows += [sample(121).model_copy(update={
        "source_max_ts_ns": {"binance": BASE+122*900*NS,
                             "kalshi_candles": BASE+121*900*NS-NS},
    })]
    readiness = forward_learning_480.assess_forward_training_readiness(rows)
    assert not readiness["ready"]
    assert readiness["eligible_480_markets"] == 118
    assert readiness["exclusions"].get("duplicate_rows") == 2


def test_challenger_is_fitted_only_on_earlier_markets_and_never_promoted():
    rows = [sample(i) for i in range(140)]
    forecasts = incumbent(rows, skip_all=True)
    outcome = forward_learning_480.evaluate_forward_candidate(
        rows, forecasts, frozen_at_ns=BASE+140*900*NS,
    )
    assert outcome["status"] in ("VALIDATION_JOINT_GAIN", "NO_VALIDATED_JOINT_GAIN")
    assert outcome["training_markets"] >= 80
    assert outcome["validation_markets"] >= 40
    assert outcome["candidate_69_gate"]["settled_markets"] == outcome["validation_markets"]
    assert outcome["incumbent_69_gate"]["qualified"] == 0
    assert outcome["candidate_probability_metrics"]["markets"] == outcome["validation_markets"]
    assert outcome["ready_to_promote"] is False
    assert outcome["live_model_changed"] is False
    assert outcome["auto_trade_changed"] is False
    assert outcome["next_required_evidence"]


def test_missing_incumbent_probability_never_counts_as_paired_improvement():
    rows = [sample(i) for i in range(120)]
    results = forward_learning_480.evaluate_forward_candidate(
        rows, {}, frozen_at_ns=BASE+120*900*NS,
    )
    assert results["status"] == "INSUFFICIENT_PAIRED_VALIDATION"
    assert results["matched_validation_markets"] == 0
    assert results["ready_to_promote"] is False


def test_readiness_legacy_zero_case_explanation_is_actionable(tmp_path, monkeypatch):
    from kalshi_edge.bootstrap import training_cycle
    from types import SimpleNamespace
    root = tmp_path
    (root/"derived").mkdir()
    (root/"models").mkdir()
    (root/"derived"/"features.parquet").write_bytes(b"not-read-in-mock")
    (root/"models"/"default.json").write_text("{}")
    monkeypatch.setattr(training_cycle.training, "_previous_lockbox_end_ts_ns", lambda _: 13000)
    groups = [(f"KXBTC15M-HIST-{i}", i*100+1, i*100+20) for i in range(120)]
    groups += [(f"KXBTC15M-FRESH-{i}", 20000+i*100, 20020+i*100) for i in range(472)]
    monkeypatch.setattr(training_cycle.training, "_metadata_groups", lambda _: groups)
    monkeypatch.setattr(training_cycle, "_learning_pool", lambda *_a, **_k: ())
    result = training_cycle.inspect_training_readiness(root)
    assert result["progress_percent"] == 40
    assert result["new_evaluation_markets"] == 472
    assert result["eligible_markets"] == 0
    assert "settled" in result["reason"].lower()
    assert "features" in result["reason"].lower()
    assert "480" in result["reason"]


def test_train_page_exposes_prospective_progress_separately():
    from kalshi_edge.bootstrap.dashboard_ui import PAGE
    assert 'id="forward-480-progress"' in PAGE
    assert 'id="forward-480-eligible"' in PAGE
    assert 'info.forward_480' in PAGE
