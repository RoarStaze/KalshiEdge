"""Pre-flip warnings must anticipate an approaching model-side reversal.

Tests are causal synthetic scenarios, NOT evidence of live predictive accuracy.
They require a warning *before* the model crosses 50%, and no retrospective
"flip already happened" alert after the threshold was crossed.
"""
from __future__ import annotations

from kalshi_edge.bootstrap.reversal_monitor import assess_reversal_risk

NS = 1_000_000_000
START = 1_800_100_000_000_000_000
TICKER = "KXBTC15M-PRE-FLIP"


def series(probs, mids):
    assert len(probs) == len(mids)
    return [
        {"prediction_ts_ns": START + i * NS,
         "market_ticker": TICKER,
         "status": "OK", "feed_quality": {"healthy": True},
         "p_yes": float(p), "kalshi_yes_bid": float(m) - .01,
         "kalshi_yes_ask": float(m) + .01}
        for i, (p, m) in enumerate(zip(probs, mids))
    ]


def assess(rows):
    return assess_reversal_risk(rows, now_ns=rows[-1]["prediction_ts_ns"],
                                market_ticker=TICKER)["flip_alert"]


def test_warns_down_before_live_model_has_flipped_down():
    p = [.82] * 10 + [.80, .75, .70, .64, .61, .59, .57, .54]
    q = [.78] * 10 + [.76, .73, .70, .67, .64, .62, .60, .58]
    rows = series(p, q)
    assert rows[-1]["p_yes"] > .5  # Forecast still UP, not after-the-fact.
    warning = assess(rows)
    assert warning is not None
    assert warning["direction"] == "DOWN"
    assert warning["current_direction"] == "UP"
    assert warning["kind"] == "PRE_FLIP_WARNING"
    assert warning["detected_ts_ns"] == rows[-1]["prediction_ts_ns"]


def test_warns_up_before_live_model_has_flipped_up():
    p = [.18] * 10 + [.20, .25, .30, .36, .39, .41, .43, .46]
    q = [.22] * 10 + [.24, .27, .30, .33, .36, .38, .40, .42]
    rows = series(p, q)
    assert rows[-1]["p_yes"] < .5
    warning = assess(rows)
    assert warning is not None
    assert warning["direction"] == "UP"
    assert warning["current_direction"] == "DOWN"
    assert warning["kind"] == "PRE_FLIP_WARNING"


def test_after_crossing_does_not_report_retrospective_flip():
    p = [.82] * 10 + [.80, .75, .70, .64, .61, .59, .57, .54, .48, .42, .35]
    q = [.78] * 10 + [.76, .73, .70, .67, .64, .62, .60, .58, .54, .50, .46]
    rows = series(p, q)
    assert assess(rows[:18])["direction"] == "DOWN"  # Early warning was active.
    assert assess(rows) is None  # DOWN is already live; no stale warning.


def test_slope_only_or_quote_only_does_not_produce_warning():
    p = [.82] * 10 + [.80, .75, .70, .64, .61, .59, .57, .54]
    q = [.78] * 10 + [.76, .73, .70, .67, .64, .62, .60, .58]
    assert assess(series(p, [.7] * len(p))) is None
    assert assess(series([.56] * len(q), q)) is None


def test_bounce_back_clear_and_stale_evidence_never_warns():
    p = [.82] * 10 + [.80, .75, .70, .64, .61, .59, .57, .54]
    q = [.78] * 10 + [.76, .73, .70, .67, .64, .62, .60, .58]
    rows = series(p, q)
    assert assess(rows)["direction"] == "DOWN"
    rows[-1] = dict(rows[-1], p_yes=.72, kalshi_yes_bid=.70,
                    kalshi_yes_ask=.72)
    assert assess(rows) is None
    rows = series(p, q)
    assert assess_reversal_risk(rows, now_ns=rows[-1]["prediction_ts_ns"] + 7 * NS,
                                market_ticker=TICKER)["flip_alert"] is None


def test_flip_ui_only_says_target_up_or_down_not_confirmed_past_direction():
    from kalshi_edge.bootstrap.dashboard_ui import PAGE
    assert 'flip.textContent=alert.direction;' in PAGE
    assert 'alert.kind==="PRE_FLIP_WARNING"' in PAGE
    assert '["UP","DOWN"].includes(alert.direction)' in PAGE
    assert 'Model direction switched with Kalshi quote confirmation' not in PAGE
    assert 'UP_TO_DOWN' not in PAGE
    assert 'DOWN_TO_UP' not in PAGE
