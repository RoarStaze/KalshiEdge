"""Same-market prospective 480s shadow scoring, test-first and read-only."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from kalshi_edge.bootstrap.final_forecast import FinalForecastRecord, FinalForecastWriter
from kalshi_edge.bootstrap.final_history import OfficialSettlement, _save_settlement
from kalshi_edge.bootstrap.frozen_specialist_480 import (
    FILENAME, FEATURES, MODEL_NAME, _hash, save_frozen_specialist,
)
from kalshi_edge.bootstrap.shadow_live_480 import Shadow480Record, Shadow480Writer, _digest
from kalshi_edge.bootstrap.prospective_shadow_480 import evaluate_prospective_shadow

NS = 1_000_000_000
BASE = 1_900_000_000 * NS
FROZEN = BASE - 900 * NS
HASH = "b" * 64


def _make_freeze(root: Path):
    model = dict(model=MODEL_NAME, feature_names=list(FEATURES),
                 dataset_sha256="a" * 64, fit_markets=100,
                 trained_through_ns=FROZEN - 900 * NS,
                 frozen_at_ns=FROZEN, mean=[0.0] * 6,
                 scale=[1.0] * 6, coefficients=[0.0] * 6, intercept=0.0)
    model["sha256"] = _hash(model)
    save_frozen_specialist(root / FILENAME, model)
    return model


def _make_market(root: Path, i: int, *,
                 incumbent: float | None, candidate: float | None,
                 actual: str | None, include_shadow=True, candidate_skip=False):
    scheduled = BASE + i * 900 * NS
    close = scheduled + 480 * NS
    ticker = f"KXBTC15M-28NOV02{i:04d}-00"
    confidence = None if incumbent is None else 2 * abs(incumbent - .5)
    forecast = ("SKIP" if confidence is None or confidence < .69
                else "ABOVE" if incumbent >= .5 else "BELOW")
    final = FinalForecastRecord(
        market_ticker=ticker, strike=100_000.0,
        scheduled_ts_ns=scheduled, prediction_ts_ns=scheduled,
        seconds_remaining=480, checkpoint_seconds=480,
        forecast=forecast, reason="CONFIDENCE_BELOW_69_PERCENT" if forecast=="SKIP" else None,
        confidence=confidence, p_yes=incumbent,
        p_no=None if incumbent is None else 1-incumbent,
        model_hash=HASH,
    )
    FinalForecastWriter(root).save(final, market_close_ts_ns=close)
    if include_shadow:
        raw = dict(market_ticker=ticker, scheduled_ts_ns=scheduled,
                   prediction_ts_ns=scheduled, market_close_ts_ns=close,
                   frozen_at_ns=FROZEN, frozen_model_sha256=_make_hash(root),
                   incumbent_model_hash=HASH, incumbent_p_yes=incumbent,
                   status="SKIP" if candidate_skip else "OK",
                   reason="SHADOW_FEATURE_ROW_UNAVAILABLE" if candidate_skip else None,
                   p_yes=None if candidate_skip else candidate,
                   gate_qualified=None if candidate_skip else
                   2 * abs(candidate - .5) >= .69)
        Shadow480Writer(root).save(
            Shadow480Record(**raw, record_sha256=_digest(raw)),
        )
    if actual is not None:
        _save_settlement(root,final,OfficialSettlement(
            market_ticker=ticker, market_close_ts_ns=close,
            result="yes" if actual=="ABOVE" else "no",
            actual_side=actual,
            source="kalshi-public-market-api",
            market_status="settled", fetched_ts_ns=close+60*NS,
        ))
    return ticker


def _make_hash(root: Path):
    from kalshi_edge.bootstrap.frozen_specialist_480 import load_frozen_specialist
    return load_frozen_specialist(root/FILENAME)["sha256"]


def test_scores_same_two_settled_markets_at_fixed_gate(tmp_path: Path):
    _make_freeze(tmp_path)
    _make_market(tmp_path, 1, incumbent=.79, candidate=.88, actual="ABOVE")
    _make_market(tmp_path, 2, incumbent=.27, candidate=.26, actual="BELOW")
    r = evaluate_prospective_shadow(tmp_path)
    assert r["status"] == "INSUFFICIENT_SETTLED_MARKETS"
    assert r["settled_market_count"] == 2
    assert r["pending_settlements"] == 0
    assert r["incumbent"]["qualified"] == 0
    assert r["specialist"]["qualified"] == 1
    assert r["specialist"]["correct"] == 1
    assert r["specialist"]["coverage"] == .5
    assert r["specialist"]["qualified_accuracy"] == 1.0
    assert r["paired_probability_markets"] == 2
    assert r["paired_gate_cohorts"]["candidate_only"]["markets"] == 1
    assert r["paired_gate_cohorts"]["neither_qualified"] == 1
    assert r["ready_to_promote"] is False


def test_missing_and_unhealthy_shadow_count_as_skips_not_deleted(tmp_path: Path):
    _make_freeze(tmp_path)
    _make_market(tmp_path, 1, incumbent=.79, candidate=.88, actual="ABOVE")
    _make_market(tmp_path, 2, incumbent=.91, candidate=None, actual="BELOW",
                 candidate_skip=True)
    _make_market(tmp_path, 3, incumbent=.93, candidate=None, actual="ABOVE",
                 include_shadow=False)
    _make_market(tmp_path, 4, incumbent=.92, candidate=.95, actual=None)
    r = evaluate_prospective_shadow(tmp_path)
    assert r["settled_market_count"] == 3
    assert r["pending_settlements"] == 1
    assert r["missing_shadow_records"] == 1
    assert r["shadow_status_skips"] == 1
    assert r["specialist"]["qualified"] == 1
    assert r["specialist"]["coverage"] == pytest.approx(1/3)
    assert r["incumbent"]["qualified"] == 2
    assert r["incumbent"]["correct"] == 1
    assert r["incumbent"]["coverage"] == pytest.approx(2/3)
    assert r["ready_to_promote"] is False
    # Snapshot-matched coverage is separate from conservative all-market
    # coverage. A valid status=SKIP remains in this cohort.
    matched = r["matched_snapshot_cohort"]
    assert matched["settled_markets"] == 2
    assert matched["incumbent"]["qualified"] == 1
    assert matched["incumbent"]["correct"] == 0
    assert matched["incumbent"]["coverage"] == pytest.approx(.5)
    assert matched["specialist"]["qualified"] == 1
    assert matched["specialist"]["correct"] == 1
    assert matched["specialist"]["coverage"] == pytest.approx(.5)
    assert matched["explicit_shadow_skips"] == 1
    assert r["missing_shadow_market_tickers"] == ["KXBTC15M-28NOV020003-00"]


def test_tampered_shadow_and_bad_official_settlement_fail_closed(tmp_path: Path):
    _make_freeze(tmp_path)
    ticker_a = _make_market(tmp_path, 1, incumbent=.9, candidate=.95, actual="ABOVE")
    ticker_b = _make_market(tmp_path, 2, incumbent=.88, candidate=.96, actual="BELOW")
    for file in (tmp_path / "research" / "480_specialist" / "live_shadow").rglob(f"{ticker_a}.json"):
        content=json.loads(file.read_text(encoding="utf-8"))
        content["p_yes"] = .02
        file.write_text(json.dumps(content),encoding="utf-8")
    for file in (tmp_path/"final_outcomes").rglob(f"{ticker_b}.json"):
        content=json.loads(file.read_text(encoding="utf-8"))
        content["actual_side"]="ABOVE"
        file.write_text(json.dumps(content),encoding="utf-8")
    r=evaluate_prospective_shadow(tmp_path)
    assert r["settled_market_count"] == 1
    assert r["invalid_shadow_records"] == 1
    assert r["invalid_settlements"] == 1
    assert r["specialist"]["qualified"] == 0
    assert r["incumbent"]["qualified"] == 1
    assert r["ready_to_promote"] is False


def test_rejects_wrong_frozen_identity_in_otherwise_integrity_valid_snapshot(tmp_path: Path):
    _make_freeze(tmp_path)
    ticker=_make_market(tmp_path, 1, incumbent=.9, candidate=.95, actual="ABOVE")
    files=list((tmp_path / "research" / "480_specialist" / "live_shadow").rglob(f"{ticker}.json"))
    raw=json.loads(files[0].read_text(encoding="utf-8"))
    raw["frozen_model_sha256"]="f"*64
    raw["record_sha256"]=_digest({k:v for k,v in raw.items() if k!="record_sha256"})
    files[0].write_text(json.dumps(raw),encoding="utf-8")
    r=evaluate_prospective_shadow(tmp_path)
    assert r["invalid_shadow_records"]==1
    assert r["specialist"]["qualified"]==0


def test_pre_freeze_final_or_legacy_240_never_enters_prospective_sample(tmp_path: Path):
    _make_freeze(tmp_path)
    _make_market(tmp_path, 1, incumbent=.9, candidate=.95, actual="ABOVE")
    old = FinalForecastRecord(
        market_ticker="KXBTC15M-OLD", strike=100_000,
        scheduled_ts_ns=FROZEN-900*NS, prediction_ts_ns=FROZEN-900*NS,
        checkpoint_seconds=480, seconds_remaining=480, forecast="SKIP",
    )
    FinalForecastWriter(tmp_path).save(old,market_close_ts_ns=old.scheduled_ts_ns+480*NS)
    legacy = FinalForecastRecord(
        market_ticker="KXBTC15M-LEGACY", strike=100_000,
        scheduled_ts_ns=BASE+6000*NS,prediction_ts_ns=BASE+6000*NS,
        checkpoint_seconds=240,seconds_remaining=240, forecast="SKIP",
    )
    FinalForecastWriter(tmp_path).save(legacy,market_close_ts_ns=legacy.scheduled_ts_ns+240*NS)
    r=evaluate_prospective_shadow(tmp_path)
    assert r["final_480_after_freeze"]==1
    assert r["settled_market_count"]==1


def test_readonly_evaluation_does_not_create_files(tmp_path: Path):
    _make_freeze(tmp_path)
    _make_market(tmp_path, 1, incumbent=.79, candidate=.88, actual="ABOVE")
    before=sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*") if p.is_file())
    evaluate_prospective_shadow(tmp_path)
    after=sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*") if p.is_file())
    assert before==after


def test_rescue_policy_adds_high_confidence_wins_without_overriding_original(tmp_path: Path):
    from kalshi_edge.bootstrap.rescue_policy_480 import evaluate_rescue_policy

    _make_freeze(tmp_path)
    _make_market(tmp_path, 1, incumbent=.79, candidate=.88, actual="ABOVE")
    _make_market(tmp_path, 2, incumbent=.27, candidate=.26, actual="BELOW")
    # Original remains authoritative even if specialist strongly disagrees.
    _make_market(tmp_path, 3, incumbent=.94, candidate=.03, actual="ABOVE")
    r=evaluate_rescue_policy(tmp_path)
    assert r["status"]=="INSUFFICIENT_SETTLED_MARKETS"
    assert r["settled_market_count"]==3
    assert r["incumbent"]["qualified"]==1
    assert r["incumbent"]["correct"]==1
    assert r["rescue_hybrid"]["qualified"]==2
    assert r["rescue_hybrid"]["correct"]==2
    assert r["rescue_hybrid"]["coverage"]==pytest.approx(2/3)
    assert r["rescue_hybrid"]["qualified_accuracy"]==1.0
    assert r["rescues"]["markets"]==1
    assert r["rescues"]["correct"]==1
    assert r["rescues"]["wrong"]==0
    assert r["net_new_correct_forecasts"]==1
    assert r["ready_to_promote"] is False


def test_rescue_policy_includes_losses_and_missing_or_explicit_skip(tmp_path: Path):
    from kalshi_edge.bootstrap.rescue_policy_480 import evaluate_rescue_policy

    _make_freeze(tmp_path)
    _make_market(tmp_path, 1, incumbent=.79, candidate=.88, actual="ABOVE")
    _make_market(tmp_path, 2, incumbent=.79, candidate=.96, actual="BELOW")
    _make_market(tmp_path, 3, incumbent=.91, candidate=.96, actual="ABOVE",
                 include_shadow=False)
    _make_market(tmp_path, 4, incumbent=.79, candidate=None, actual="ABOVE",
                 candidate_skip=True)
    _make_market(tmp_path, 5, incumbent=.79, candidate=.96, actual=None)
    r=evaluate_rescue_policy(tmp_path)
    assert r["settled_market_count"]==4
    assert r["pending_settlements"]==1
    assert r["missing_shadow_records"]==1
    assert r["explicit_shadow_skips"]==1
    assert r["rescues"]["markets"]==2
    assert r["rescues"]["correct"]==1
    assert r["rescues"]["wrong"]==1
    assert r["incumbent"]["qualified"]==1
    assert r["rescue_hybrid"]["qualified"]==3
    assert r["rescue_hybrid"]["correct"]==2
    assert r["rescue_hybrid"]["qualified_accuracy"]==pytest.approx(2/3)
    assert r["confidence_unchanged"]==.69
    assert r["ready_to_promote"] is False


def test_rescue_policy_cannot_override_original_or_rescue_other_skip_reasons(tmp_path: Path):
    from kalshi_edge.bootstrap.rescue_policy_480 import evaluate_rescue_policy

    _make_freeze(tmp_path)
    ticker=_make_market(tmp_path, 1, incumbent=.79,candidate=.97,actual="ABOVE")
    forecast_file=next((tmp_path/"final_forecasts").rglob(f"{ticker}.json"))
    raw=json.loads(forecast_file.read_text(encoding="utf-8"))
    raw["reason"]="MISSED_480S_CHECKPOINT"
    forecast_file.write_text(json.dumps(raw),encoding="utf-8")
    r=evaluate_rescue_policy(tmp_path)
    assert r["incumbent"]["qualified"]==0
    assert r["rescue_hybrid"]["qualified"]==0
    assert r["rescues"]["markets"]==0


def test_rescue_policy_fails_closed_on_invalid_shadow_and_does_not_write(tmp_path: Path):
    from kalshi_edge.bootstrap.rescue_policy_480 import evaluate_rescue_policy

    _make_freeze(tmp_path)
    ticker=_make_market(tmp_path, 1, incumbent=.79,candidate=.97,actual="ABOVE")
    shadow_file=next((tmp_path/"research"/"480_specialist"/"live_shadow").rglob(f"{ticker}.json"))
    raw=json.loads(shadow_file.read_text(encoding="utf-8"))
    raw["p_yes"]=.01
    shadow_file.write_text(json.dumps(raw),encoding="utf-8")
    before=sorted((p.relative_to(tmp_path),p.read_bytes()) for p in tmp_path.rglob("*") if p.is_file())
    r=evaluate_rescue_policy(tmp_path)
    after=sorted((p.relative_to(tmp_path),p.read_bytes()) for p in tmp_path.rglob("*") if p.is_file())
    assert before==after
    assert r["invalid_shadow_records"]==1
    assert r["rescues"]["markets"]==0
    assert r["rescue_hybrid"]["qualified"]==0


def test_rescue_policy_rejects_any_promotion_and_no_live_or_trading_imports(tmp_path: Path):
    import inspect
    from kalshi_edge.bootstrap.rescue_policy_480 import evaluate_rescue_policy

    _make_freeze(tmp_path)
    r=evaluate_rescue_policy(tmp_path, minimum_settled=1)
    assert r["ready_to_promote"] is False
    assert r["settled_market_count"]==0
    assert r["rescue_hybrid"]["qualified_accuracy"] is None
    assert r["rescue_hybrid"]["coverage"] is None
    source=inspect.getsource(evaluate_rescue_policy)
    assert "place_order" not in source
    assert "autotrade" not in source.lower()


def test_rescue_policy_forward_validation_excludes_pre_registered_wins(
    tmp_path: Path, monkeypatch,
):
    from kalshi_edge.bootstrap import rescue_policy_480 as policy

    _make_freeze(tmp_path)
    _make_market(tmp_path, 1, incumbent=.79, candidate=.88, actual="ABOVE")
    _make_market(tmp_path, 2, incumbent=.79, candidate=.94, actual="BELOW")
    # Simulate selecting policy AFTER seeing market #1. An unbiased
    # forward report must not use that already-seen successful rescue.
    monkeypatch.setattr(policy, "POLICY_REGISTERED_AT_NS", BASE + 900*NS)
    r=policy.evaluate_rescue_policy(tmp_path)
    fwd=r["forward_validation"]
    assert fwd["settled_market_count"]==1
    assert fwd["rescues"]["markets"]==1
    assert fwd["rescues"]["correct"]==0
    assert fwd["rescues"]["wrong"]==1
    assert fwd["rescue_hybrid"]["qualified"]==1
    assert fwd["rescue_hybrid"]["correct"]==0
    assert r["rescues"]["markets"]==2
    assert r["rescues"]["correct"]==1
    assert r["ready_to_promote"] is False
