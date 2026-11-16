"""Synthetic TDD fixtures: strict point-in-time BRTI/Binance attribution.

Neither historical performance uplift nor trading profitability is asserted.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from kalshi_edge.bootstrap.final_forecast import FinalForecastRecord, FinalForecastWriter
from kalshi_edge.bootstrap.final_history import OfficialSettlement, _save_settlement
from kalshi_edge.bootstrap.forward_feature_evidence_480 import (
    Forward480FeatureWriter, build_forward_snapshot,
)
from kalshi_edge.bootstrap.types import FeatureRow

from kalshi_edge.bootstrap.reference_conditioned_480 import (
    analyze_reference_disagreement, main,
)

NS = 1_000_000_000
BASE = 1_900_000_000 * NS
MODEL = "a" * 64


def case(
    root: Path, index: int, *, p: float = .9, actual_yes: bool = True,
    risk: float = .8, issued: bool = True, reason: str | None = None,
    brti_bps: float = 5., btc: float = 100_001.,
    stale: bool = False, bad_gap: bool = False, snapshot: bool = True,
    model_hash: str = MODEL, checkpoint: int = 480,
) -> None:
    tick = f"KXBTC15M-REF-{index:04d}"
    when = BASE + index * 900 * NS
    close = when + checkpoint * NS
    final = FinalForecastRecord(
        market_ticker=tick, strike=100_000., scheduled_ts_ns=when,
        checkpoint_seconds=checkpoint, prediction_ts_ns=when,
        seconds_remaining=float(checkpoint), forecast=(
            ("ABOVE" if p >= .5 else "BELOW") if issued else "SKIP"
        ), reason=reason, p_yes=p, p_no=1.-p,
        directional_probability=max(p, 1.-p), confidence=2*abs(p-.5),
        coin_flip_risk_z=risk, coin_flip_assessment=(
            "HIGH" if risk <= .2 else "LOW"
        ), model_hash=model_hash,
    )
    written = FinalForecastWriter(root).save(final, market_close_ts_ns=close)
    assert written is not None
    if snapshot and checkpoint == 480:
        gap = brti_bps - ((btc - 100_000.) / 100_000. * 10_000.)
        row = FeatureRow(
            market_ticker=tick, checkpoint_ts_ns=when, label_yes=0,
            features={
                "seconds_remaining":480.,
                "brti_distance_to_strike_bps":brti_bps,
                "brti_binance_gap_bps":gap + (50. if bad_gap else 0.),
                "btc_close":btc,
            },
            source_max_ts_ns={
                "brti":when - NS,
                "binance":when - (6 if stale else 1)*NS,
            },
        )
        Forward480FeatureWriter(root).save(
            build_forward_snapshot(final, written, row)
        )
    _save_settlement(root, final, OfficialSettlement(
        market_ticker=tick, market_close_ts_ns=close,
        result="yes" if actual_yes else "no",
        actual_side="ABOVE" if actual_yes else "BELOW",
        source="kalshi-public-market-api",
        fetched_ts_ns=close + 60*NS, market_status="settled",
    ))


def populated(root: Path) -> None:
    case(root, 1, p=.9, risk=.1, issued=False, reason="COIN_FLIP_RISK_HIGH",
         actual_yes=True, btc=99_999.)  # correct, disagrees, vetoed
    case(root, 2, p=.8, risk=.4, actual_yes=True, btc=99_999.)  # correct, disagrees
    case(root, 3, p=.8, risk=.4, actual_yes=False, btc=100_001.)  # wrong, agrees
    case(root, 4, p=.75, risk=.8, issued=False, reason="CONFIDENCE_BELOW_69_PERCENT",
         actual_yes=True, btc=99_999.)  # correct, historical SKIP, 63% replay eligible
    case(root, 5, p=.9, risk=1.2, actual_yes=True, btc=100_001.)  # correct, agrees
    case(root, 6, p=.9, risk=1.2, actual_yes=False, btc=99_999.)  # wrong, disagrees
    case(root, 7, p=.62, risk=.8, issued=False,
         reason="DIRECTIONAL_PROBABILITY_AT_OR_BELOW_63_PERCENT",
         actual_yes=True, btc=100_001.)  # below 63% current gate
    case(root, 8, p=.85, risk=.8, actual_yes=False, btc=100_001.)  # wrong, agrees
    case(root, 9, p=.63, risk=.8, issued=False,
         reason="DIRECTIONAL_PROBABILITY_AT_OR_BELOW_63_PERCENT",
         actual_yes=True, btc=100_001.)  # exactly at strict threshold
    case(root, 10, p=.9, risk=.8, btc=99_999., stale=True)
    case(root, 11, p=.9, risk=.8, btc=99_999., bad_gap=True)
    case(root, 12, p=.9, risk=.8, btc=99_999., snapshot=False)
    case(root, 13, p=.9, risk=.8, btc=99_999., model_hash="b"*64)
    case(root, 14, p=.9, risk=.8, btc=99_999., checkpoint=240)


def test_risk_conditioned_reference_error_tables_and_exclusions(tmp_path):
    populated(tmp_path)
    report = analyze_reference_disagreement(tmp_path, model_hash=MODEL)
    assert report["status"] == "DESCRIPTIVE_ONLY"
    assert report["markets_with_alignment"] == 9
    assert report["excluded"]["stale_feed"] == 1
    assert report["excluded"]["inconsistent_reference_basis"] == 1
    assert report["excluded"]["missing_or_invalid_snapshot"] == 1
    assert report["excluded"]["different_model"] == 1
    assert report["excluded"]["non_480_checkpoint"] == 1
    all_rows = report["cohorts"]["all_healthy"]
    assert all_rows["overall"]["disagree"]["total"] == 4
    assert all_rows["overall"]["disagree"]["wrong"] == 1
    assert all_rows["overall"]["agree"]["total"] == 5
    assert all_rows["overall"]["agree"]["wrong"] == 2
    assert all_rows["risk_strata"]["at_or_below_0_20"]["disagree"]["total"] == 1
    assert all_rows["risk_strata"]["above_0_20_to_0_50"]["agree"]["wrong"] == 1
    assert all_rows["risk_strata"]["above_1_00"]["disagree"]["wrong"] == 1
    assert all_rows["risk_strata"]["unavailable"]["agree"]["error_rate"] is None


def test_actual_issued_and_current_gate_replay_are_not_conflated(tmp_path):
    populated(tmp_path)
    report = analyze_reference_disagreement(tmp_path, model_hash=MODEL)
    issued = report["cohorts"]["actual_issued"]
    replay = report["cohorts"]["live_63_replay"]
    assert issued["matched"] == 5
    assert replay["matched"] == 6
    assert replay["overall"]["disagree"]["total"] == 3
    assert replay["overall"]["disagree"]["wrong"] == 1
    assert replay["overall"]["agree"]["total"] == 3
    assert replay["overall"]["agree"]["wrong"] == 2
    assert report["policy_replay"]["eligible"] == 6
    assert report["policy_replay"]["replayed_historical_skips"] == 1
    assert report["policy_replay"]["rejected_for_near_strike_veto"] == 1
    assert report["policy_replay"]["rejected_for_probability_gate"] == 2


def test_hypothetical_disagreement_veto_discloses_correct_forecasts_lost(tmp_path):
    populated(tmp_path)
    report = analyze_reference_disagreement(tmp_path, model_hash=MODEL)
    veto = report["hypothetical_disagreement_veto"]
    assert veto["live_gate_before"]["qualified"] == 6
    assert veto["live_gate_before"]["correct"] == 3
    assert veto["live_gate_after"]["qualified"] == 3
    assert veto["live_gate_after"]["correct"] == 1
    assert veto["correct_forecasts_removed"] == 2
    assert veto["incorrect_forecasts_avoided"] == 1
    assert veto["trades_executed"] is False
    assert report["research_only"]["live_policy_changed"] is False


def test_missing_references_do_not_imply_agreement_or_sample(tmp_path):
    report = analyze_reference_disagreement(tmp_path, model_hash=MODEL)
    assert report["status"] == "NO_VERIFIED_REFERENCE_COHORT"
    assert report["markets_with_alignment"] == 0
    assert report["cohorts"]["all_healthy"]["overall"]["agree"]["error_rate"] is None
    assert report["hypothetical_disagreement_veto"]["live_gate_before"]["accuracy"] is None


def test_cli_json_does_not_write_or_trade(tmp_path, capsys):
    populated(tmp_path)
    before = {p.relative_to(tmp_path).as_posix(): p.read_bytes()
              for p in tmp_path.rglob("*.json")}
    argv = ["--root", str(tmp_path), "--model-hash", MODEL]
    assert main(argv) == 0
    output = capsys.readouterr().out
    assert "RISK-CONDITIONED" in output
    assert "CORRECT forecasts removed" in output
    assert "NO LIVE POLICY CHANGE" in output
    assert main(argv + ["--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["markets_with_alignment"] == 9
    assert data["cohorts"]["actual_issued"]["matched"] == 5
    assert {p.relative_to(tmp_path).as_posix(): p.read_bytes()
            for p in tmp_path.rglob("*.json")} == before
