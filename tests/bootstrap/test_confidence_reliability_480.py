"""Fixed confidence bands: never mix legacy 240s and forward 480s outcomes."""
from __future__ import annotations

from pathlib import Path
import json

import pytest

from kalshi_edge.bootstrap.final_forecast import FinalForecastRecord, FinalForecastWriter
from kalshi_edge.bootstrap.final_history import OfficialSettlement, _save_settlement

NS = 1_000_000_000
OLD = 1_790_000_000 * NS
FUTURE = 1_900_000_000 * NS
HASH = "a" * 64


def market(root: Path, i: int, *, timestamp: int, checkpoint: int,
           confidence: float, p_yes: float,
           forecast: str, actual: str | None, reason: str | None = None):
    ticker=f"KXBTC15M-AUDIT-{i}"
    final = FinalForecastRecord(
        market_ticker=ticker,
        strike=100000,
        scheduled_ts_ns=timestamp + i * 900 * NS,
        prediction_ts_ns=timestamp + i * 900 * NS,
        checkpoint_seconds=checkpoint,
        seconds_remaining=checkpoint,
        forecast=forecast,
        reason=reason if reason is not None else (
            "CONFIDENCE_BELOW_69_PERCENT" if forecast == "SKIP" else None
        ),
        confidence=confidence,
        p_yes=p_yes, p_no=1-p_yes,
        model_hash=HASH,
    )
    close=final.scheduled_ts_ns+checkpoint*NS
    FinalForecastWriter(root).save(final,market_close_ts_ns=close)
    if actual:
        _save_settlement(root,final,OfficialSettlement(
            market_ticker=ticker,market_close_ts_ns=close,
            result="yes" if actual=="ABOVE" else "no",
            actual_side=actual,
            market_status="settled",fetched_ts_ns=close+90*NS))
    return final


def test_confidence_bands_separate_240_from_480_and_skip_from_issued(tmp_path: Path):
    from kalshi_edge.bootstrap.confidence_reliability_480 import evaluate_confidence_reliability

    market(tmp_path,1,timestamp=OLD,checkpoint=240,confidence=.68,p_yes=.84,
           forecast="SKIP",actual="ABOVE")
    market(tmp_path,2,timestamp=OLD,checkpoint=240,confidence=.72,p_yes=.86,
           forecast="ABOVE",actual="BELOW")
    market(tmp_path,3,timestamp=OLD,checkpoint=480,confidence=.65,p_yes=.825,
           forecast="SKIP",actual="BELOW")
    market(tmp_path,4,timestamp=FUTURE,checkpoint=480,confidence=.8,p_yes=.1,
           forecast="BELOW",actual="BELOW")
    market(tmp_path,5,timestamp=FUTURE,checkpoint=480,confidence=.6,p_yes=.8,
           forecast="SKIP",actual="ABOVE")
    market(tmp_path,6,timestamp=FUTURE,checkpoint=480,confidence=.6,p_yes=.8,
           forecast="SKIP",actual=None)

    r=evaluate_confidence_reliability(tmp_path)
    legacy=r["cohorts"]["legacy_240"]
    early=r["cohorts"]["early_480"]
    forward=r["cohorts"]["forward_480"]
    assert legacy["settled_markets"]==2
    assert legacy["issued"]["markets"]==1 and legacy["issued"]["correct"]==0
    assert legacy["skips_with_valid_probabilities"]["markets"]==1
    assert legacy["skips_with_valid_probabilities"]["hypothetically_correct"]==1
    assert early["settled_markets"]==1
    assert early["skips_with_valid_probabilities"]["hypothetically_correct"]==0
    assert forward["settled_markets"]==2
    assert forward["pending_settlements"]==1
    assert forward["issued"]["markets"]==1 and forward["issued"]["correct"]==1
    assert forward["skips_with_valid_probabilities"]["markets"]==1
    assert forward["skips_with_valid_probabilities"]["hypothetically_correct"]==1
    assert forward["bands"]["60%-<69%"]["skipped"]["markets"]==1
    assert legacy["bands"]["69%-<80%"]["issued"]["incorrect"]==1
    assert r["ready_to_promote"] is False


def test_fixed_boundaries_and_small_sample_uncertainty(tmp_path: Path):
    from kalshi_edge.bootstrap.confidence_reliability_480 import (
        confidence_bin, wilson_interval, evaluate_confidence_reliability,
    )
    assert confidence_bin(.6)=="60%-<69%"
    assert confidence_bin(.69)=="69%-<80%"
    assert confidence_bin(.8)=="80%-<90%"
    assert confidence_bin(.9)=="90%-100%"
    assert confidence_bin(1.0)=="90%-100%"
    with pytest.raises(ValueError):
        confidence_bin(1.01)
    assert wilson_interval(0,0)==(None,None)
    lo,hi=wilson_interval(1,1)
    assert 0 < lo < .3 and hi==pytest.approx(1)
    market(tmp_path,1,timestamp=FUTURE,checkpoint=480,confidence=.69,p_yes=.845,
           forecast="ABOVE",actual="ABOVE")
    r=evaluate_confidence_reliability(tmp_path)
    b=r["cohorts"]["forward_480"]["bands"]["69%-<80%"]
    assert b["issued"]["markets"]==1
    assert b["issued"]["wilson_95_lower"]<.3
    assert r["note"].startswith("Read-only")


def test_invalid_settlement_and_duplicate_finals_excluded_readonly(tmp_path: Path):
    from kalshi_edge.bootstrap.confidence_reliability_480 import evaluate_confidence_reliability

    valid=market(tmp_path,1,timestamp=FUTURE,checkpoint=480,confidence=.92,
           p_yes=.96,forecast="ABOVE",actual="ABOVE")
    corrupted=market(tmp_path,2,timestamp=FUTURE,checkpoint=480,confidence=.65,
           p_yes=.825,forecast="SKIP",actual="BELOW")
    mismatch=next((tmp_path/"final_outcomes").rglob(f"{corrupted.market_ticker}.json"))
    raw=json.loads(mismatch.read_text())
    raw["actual_side"]="ABOVE"
    mismatch.write_text(json.dumps(raw))
    duplicate_path=tmp_path/"final_forecasts"/"2099-01-01"/f"{valid.market_ticker}.json"
    duplicate_path.parent.mkdir(parents=True)
    original=next((tmp_path/"final_forecasts").rglob(f"{valid.market_ticker}.json"))
    duplicate_path.write_bytes(original.read_bytes())
    before={str(p.relative_to(tmp_path)):p.read_bytes() for p in tmp_path.rglob("*.json")}
    r=evaluate_confidence_reliability(tmp_path)
    after={str(p.relative_to(tmp_path)):p.read_bytes() for p in tmp_path.rglob("*.json")}
    assert before==after
    assert r["invalid_final_records"]>=2
    assert r["cohorts"]["forward_480"]["settled_markets"]==0
    assert r["cohorts"]["forward_480"]["invalid_settlements"]==1


def test_inconsistent_confidence_probability_not_scored_as_reliable(tmp_path: Path):
    from kalshi_edge.bootstrap.confidence_reliability_480 import evaluate_confidence_reliability
    market(tmp_path,1,timestamp=FUTURE,checkpoint=480,confidence=.69,
           p_yes=.52,forecast="ABOVE",actual="ABOVE")
    r=evaluate_confidence_reliability(tmp_path)
    c=r["cohorts"]["forward_480"]
    assert c["settled_markets"]==1
    assert c["issued"]["markets"]==1
    assert c["invalid_probability_records"]==1
    assert sum(b["total_markets"] for b in c["bands"].values())==0
