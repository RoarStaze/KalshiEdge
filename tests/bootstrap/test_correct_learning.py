from __future__ import annotations

import json
from pathlib import Path

import pytest

from kalshi_edge.bootstrap.correct_learning import (
    CorrectCase, enroll_correct_forecasts, verified_correct_pool,
)
from kalshi_edge.bootstrap.final_forecast import FinalForecastRecord, FinalForecastWriter
from kalshi_edge.bootstrap.final_history import OfficialSettlement, _save_settlement
from kalshi_edge.bootstrap.mistake_learning import MistakePoolError, verified_mistake_pool

NS = 1_000_000_000
CLOSE = 1_800_000_900_000_000_000
DUE = CLOSE - 240 * NS


def _record(root: Path, ticker: str, *, side="ABOVE", result="yes",
            confidence=.95, probability=.975, reason=None, ontime=True):
    record = FinalForecastRecord(
        market_ticker=ticker, strike=81043.29, scheduled_ts_ns=DUE,
        prediction_ts_ns=DUE if ontime else DUE + 3 * NS,
        seconds_remaining=240 if ontime else 237,
        forecast=side, reason=reason,
        confidence=confidence, directional_probability=probability,
        p_yes=probability, p_no=1-probability,
        model_hash="f" * 64, model_version="bootstrap-hybrid-v5",
    )
    FinalForecastWriter(root).save(record, market_close_ts_ns=CLOSE)
    _save_settlement(root, record, OfficialSettlement(
        market_ticker=ticker, market_close_ts_ns=CLOSE,
        result=result, actual_side="ABOVE" if result == "yes" else "BELOW",
        fetched_ts_ns=CLOSE + 90 * NS, market_status="finalized",
    ))
    return record


def test_refresher_only_enrolls_settled_correct_confident(tmp_path):
    ticker = "KXBTC15M-CORRECT-100"
    _record(tmp_path, ticker)
    result = enroll_correct_forecasts(tmp_path)
    assert result["new_cases"] == 1
    assert result["eligible"] == 1
    case, path, digest = verified_correct_pool(tmp_path)[0]
    assert case.forecast == "ABOVE"
    assert case.selected_label == "yes"
    assert case.model_version == "bootstrap-hybrid-v5"
    assert path.parent.parent.name == "correct_pool"
    assert len(digest) == 64
    assert enroll_correct_forecasts(tmp_path)["already_enrolled"] == 1
    assert verified_mistake_pool(tmp_path) == ()


def test_wrong_and_skips_never_enter_correct_refresher(tmp_path):
    _record(tmp_path, "KXBTC15M-WRONG-101", result="no")
    _record(tmp_path, "KXBTC15M-SKIP-102", side="SKIP", reason="CONFIDENCE_BELOW_69_PERCENT",
            confidence=.40, probability=.70)
    assert enroll_correct_forecasts(tmp_path)["new_cases"] == 0
    assert verified_correct_pool(tmp_path) == ()


def test_low_confidence_or_late_correct_do_not_enter(tmp_path):
    _record(tmp_path, "KXBTC15M-LOW-103", confidence=.68, probability=.84)
    _record(tmp_path, "KXBTC15M-LOWPROB-104", confidence=.80, probability=.68)
    _record(tmp_path, "KXBTC15M-LATE-105", ontime=False)
    assert enroll_correct_forecasts(tmp_path)["new_cases"] == 0


def test_refresher_source_tamper_is_detected(tmp_path):
    _record(tmp_path, "KXBTC15M-CORRECT-106")
    assert enroll_correct_forecasts(tmp_path)["new_cases"] == 1
    source = next((tmp_path / "final_outcomes").glob("*/*.json"))
    values = json.loads(source.read_text())
    values["result"] = "no"
    values["actual_side"] = "BELOW"
    source.write_text(json.dumps(values))
    with pytest.raises(MistakePoolError):
        verified_correct_pool(tmp_path)
    assert enroll_correct_forecasts(tmp_path)["errors"] == 1


def test_correct_pool_skips_unselected_holdout_case_without_reading_it(tmp_path):
    _record(tmp_path, "KXBTC15M-HOLDOUT-107")
    enroll_correct_forecasts(tmp_path)
    pool_file = next((tmp_path / "correct_pool").glob("*/*.json"))
    pool_file.write_text("{not-valid-json")
    assert verified_correct_pool(tmp_path, allowed_tickers={"KXBTC15M-OTHER"}) == ()
    with pytest.raises(MistakePoolError):
        verified_correct_pool(tmp_path)


def test_correct_case_requires_source_provenance_and_never_invents_a_label(tmp_path):
    _record(tmp_path, "KXBTC15M-PROVENANCE-108")
    enroll_correct_forecasts(tmp_path)
    entry = next((tmp_path / "correct_pool").glob("*/*.json"))
    payload = json.loads(entry.read_text())
    payload["selected_label"] = "no"
    entry.write_text(json.dumps(payload))
    with pytest.raises(MistakePoolError):
        verified_correct_pool(tmp_path)
