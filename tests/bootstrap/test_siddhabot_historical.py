"""No fabricated scores, no future data, and genuine same-market denominators."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import hashlib

import numpy as np
import pytest

from kalshi_edge.bootstrap import siddhabot_historical as historic

NS = 1_000_000_000


def fixture_ticks():
    checkpoint = int(datetime(2026, 11, 15, 12, 7, tzinfo=timezone.utc).timestamp()*NS)
    ts = np.arange(checkpoint-4*3600*NS, checkpoint, NS, dtype=np.int64)
    n = len(ts)
    close = 100000 + .08*np.arange(n) + 12*np.sin(np.arange(n)/1000)
    ticks = {
        "ts_ns": ts, "high": close + .11, "low": close - .11,
        "close": close, "base_volume": np.full(n, .3),
    }
    final = SimpleNamespace(
        market_ticker="KXBTC15M-26NOV151215-15",
        scheduled_ts_ns=checkpoint, prediction_ts_ns=checkpoint,
        checkpoint_seconds=480, strike=100000.,
        kalshi_yes_bid=.42, kalshi_yes_ask=.48,
    )
    return final, ticks


def test_23_inputs_are_causal_and_finite():
    final, ticks = fixture_ticks()
    result = historic.build_proxy_features(final, ticks, settlement_bias=.55)
    assert result is not None
    assert set(result) == set(historic.SIDDHA_COLUMNS)
    assert result["seconds_remaining"] == 480
    assert result["yes_mid"] == pytest.approx(.45)
    assert result["spread"] == pytest.approx(.06)
    assert 0 < result["gbm_prob"] < 1
    assert all(np.isfinite(v) for v in result.values())
    future = {
        name: np.concatenate([values, np.array([final.prediction_ts_ns + NS if name == "ts_ns" else 1e9])])
        for name, values in ticks.items()
    }
    assert historic.build_proxy_features(final, future, settlement_bias=.55) == result
    final.prediction_ts_ns += NS//2
    assert historic.build_proxy_features(final, ticks, settlement_bias=.55) is not None


def test_missing_second_or_quote_is_unscorable_not_imputed():
    final,ticks=fixture_ticks()
    assert historic.build_proxy_features(final,{name: values[1:] for name, values in ticks.items()},settlement_bias=.5) is None
    final.kalshi_yes_bid = None
    assert historic.build_proxy_features(final,ticks,settlement_bias=.5) is None


def test_same_market_accuracy_coverage_and_brier():
    rows=[(.8,.7,1,"ABOVE"),(.2,.6,0,"BELOW"),
          (.51,.1,1,"SKIP"),(.64,.63,0,"ABOVE")]
    report=historic.evaluate_paired(rows)
    assert report["markets"] == 4
    assert report["kalshiedge_common_gate"]["qualified"] == 3
    assert report["kalshiedge_common_gate"]["correct"] == 2
    assert report["siddhabot_common_gate"]["qualified"] == 2
    assert report["siddhabot_common_gate"]["correct"] == 1
    assert report["siddhabot_common_gate"]["coverage"] == .5
    assert report["paired_brier"]["kalshiedge"] == pytest.approx(
        sum((p-y)**2 for p,_,y,_ in rows)/4
    )


def test_git_blob_validation():
    data=b"abc"
    assert historic.git_blob_sha1(data) == hashlib.sha1(b"blob 3\x00abc").hexdigest()


def test_settlement_bias_excludes_current_and_future():
    now=2000*NS
    assert historic.trailing_settlement_bias(
        [(now-900*NS,1), (now-30*NS,0)], now
    ) is None
    past=[(now-(i+2)*900*NS,i%2) for i in range(20)]
    assert historic.trailing_settlement_bias(past,now) == .5
