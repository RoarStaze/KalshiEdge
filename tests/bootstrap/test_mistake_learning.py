from __future__ import annotations

import json
from pathlib import Path

import pytest

from kalshi_edge.bootstrap.final_forecast import FinalForecastRecord, FinalForecastWriter
from kalshi_edge.bootstrap.final_history import OfficialSettlement, _save_settlement
from kalshi_edge.bootstrap.mistake_learning import (
    MistakePoolError,
    _case_path,
    enroll_settled_forecasts,
    verified_mistake_pool,
)

NS = 1_000_000_000
CLOSE = 1_800_000_900_000_000_000
DUE = CLOSE - 240 * NS


def _market(root: Path, market: str, side: str, actual: str, *,
            confidence: float | None = .96,
            probability: float | None = .98,
            reason: str | None = None,
            late: int = 0,
            save_outcome: bool = True) -> FinalForecastRecord:
    record = FinalForecastRecord(
        market_ticker=market,
        strike=81043.29,
        scheduled_ts_ns=DUE,
        prediction_ts_ns=DUE + late * NS,
        seconds_remaining=240.0 - late,
        forecast=side,
        reason=reason,
        confidence=confidence,
        directional_probability=probability,
        p_yes=probability if side != "BELOW" else 1.0 - (probability or 0.5),
        p_no=1.0 - (probability or 0.5) if side != "BELOW" else probability,
        model_hash="e" * 64,
        model_version="bootstrap-hybrid-v5",
    )
    assert FinalForecastWriter(root).save(record, market_close_ts_ns=CLOSE) is not None
    if save_outcome:
        official = OfficialSettlement(
            market_ticker=market,
            market_close_ts_ns=CLOSE,
            result="yes" if actual == "ABOVE" else "no",
            actual_side=actual,
            fetched_ts_ns=CLOSE + 300 * NS,
            market_status="finalized",
        )
        _save_settlement(root, record, official)
    return record


def test_wrong_final_enrolled_once_and_hash_verified(tmp_path: Path) -> None:
    ticker = "KXBTC15M-ERROR-1"
    _market(tmp_path, ticker, "ABOVE", "BELOW")
    first = enroll_settled_forecasts(tmp_path)
    assert first["new_cases"] == 1
    assert first["eligible"] == 1
    assert first["source_errors"] == 0
    case, path, digest = verified_mistake_pool(tmp_path)[0]
    assert case.reasons == ("INCORRECT_FINAL",)
    assert case.selected_label == "no"
    assert case.model_version == "bootstrap-hybrid-v5"
    assert len(digest) == 64
    assert path == _case_path(tmp_path.resolve(), ticker, CLOSE)
    second = enroll_settled_forecasts(tmp_path)
    assert second["new_cases"] == 0
    assert second["already_enrolled"] == 1
    assert path.read_text().count("\n") == 1


def test_correct_high_confidence_is_not_added(tmp_path: Path) -> None:
    _market(tmp_path, "KXBTC15M-CORRECT-2", "ABOVE", "ABOVE")
    stats = enroll_settled_forecasts(tmp_path)
    assert stats["new_cases"] == 0
    assert stats["correct_high_confidence_excluded"] == 1
    assert verified_mistake_pool(tmp_path) == ()


def test_correct_low_confidence_market_is_enrolled(tmp_path: Path) -> None:
    _market(
        tmp_path, "KXBTC15M-LOW-3", "SKIP", "BELOW",
        confidence=.50, probability=.75,
        reason="CONFIDENCE_BELOW_69_PERCENT",
    )
    stats = enroll_settled_forecasts(tmp_path)
    assert stats["new_cases"] == 1
    case, _, _ = verified_mistake_pool(tmp_path)[0]
    assert case.reasons == ("LOW_CONFIDENCE",)
    assert case.forecast == "SKIP"
    assert case.actual_side == "BELOW"


def test_low_directional_probability_also_eligible(tmp_path: Path) -> None:
    _market(
        tmp_path, "KXBTC15M-LOWPROB-4", "ABOVE", "ABOVE",
        confidence=.75, probability=.68,
    )
    enroll_settled_forecasts(tmp_path)
    case, _, _ = verified_mistake_pool(tmp_path)[0]
    assert "LOW_DIRECTIONAL_PROBABILITY" in case.reasons
    assert "INCORRECT_FINAL" not in case.reasons


def test_pending_outcome_never_enters_training(tmp_path: Path) -> None:
    _market(tmp_path, "KXBTC15M-PENDING-5", "ABOVE", "BELOW", save_outcome=False)
    stats = enroll_settled_forecasts(tmp_path)
    assert stats["awaiting_settlement"] == 1
    assert stats["new_cases"] == 0
    assert verified_mistake_pool(tmp_path) == ()


def test_unhealthy_skip_and_missed_checkpoint_not_added(tmp_path: Path) -> None:
    _market(
        tmp_path, "KXBTC15M-UNHEALTHY-6", "SKIP", "BELOW",
        confidence=.10, probability=.60, reason="STALE_KALSHI_BOOK",
    )
    _market(
        tmp_path, "KXBTC15M-LATE-7", "ABOVE", "BELOW", late=3,
    )
    counts = enroll_settled_forecasts(tmp_path)
    assert counts["new_cases"] == 0
    assert len(verified_mistake_pool(tmp_path)) == 0


def test_source_tampering_rejected_without_relabeling(tmp_path: Path) -> None:
    market = "KXBTC15M-ERR-8"
    forecast = _market(tmp_path, market, "ABOVE", "BELOW")
    assert enroll_settled_forecasts(tmp_path)["new_cases"] == 1
    source = tmp_path / "final_outcomes"
    path = next(source.glob("*/*.json"))
    value = json.loads(path.read_text())
    value["result"] = "yes"
    value["actual_side"] = "ABOVE"
    path.write_text(json.dumps(value))
    with pytest.raises(MistakePoolError, match="source|matches"):
        verified_mistake_pool(tmp_path)
    assert enroll_settled_forecasts(tmp_path)["source_errors"] == 1


def test_mistake_case_tampering_and_duplicate_are_rejected(tmp_path: Path) -> None:
    _market(tmp_path, "KXBTC15M-ERR-9", "ABOVE", "BELOW")
    enroll_settled_forecasts(tmp_path)
    path = next((tmp_path / "mistake_pool").glob("*/*.json"))
    data = json.loads(path.read_text())
    data["actual_side"] = "ABOVE"
    path.write_text(json.dumps(data))
    with pytest.raises(MistakePoolError, match="source|matches"):
        verified_mistake_pool(tmp_path)


def test_pool_loader_does_not_read_excluded_lockbox_cases(tmp_path: Path, monkeypatch) -> None:
    market = "KXBTC15M-LOCKBOX-10"
    _market(tmp_path, market, "ABOVE", "BELOW")
    enroll_settled_forecasts(tmp_path)
    path = next((tmp_path / "mistake_pool").glob("*/*.json"))
    path.write_text("{corrupt json")
    assert verified_mistake_pool(tmp_path, allowed_tickers={"KXBTC15M-ALLOWED-0"}) == ()
    with pytest.raises(MistakePoolError):
        verified_mistake_pool(tmp_path)
