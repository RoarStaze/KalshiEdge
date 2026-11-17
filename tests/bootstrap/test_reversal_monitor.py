"""Read-only intramarket reversal diagnostics and flip alerts."""
from kalshi_edge.bootstrap.reversal_monitor import assess_reversal_risk

NS = 1_000_000_000
TICKER = "KXBTC15M-TEST"
START = 1_800_000_000_000_000_000


def sample(i, p=.78, mid=.72, *, ticker=TICKER):
    return {
        "prediction_ts_ns": START + i * NS,
        "market_ticker": ticker,
        "status": "OK",
        "feed_quality": {"healthy": True},
        "p_yes": p,
        "kalshi_yes_bid": mid - .01,
        "kalshi_yes_ask": mid + .01,
    }


def test_steady_live_signal_is_low_and_has_no_flip_alert():
    series = [sample(i) for i in range(18)]
    result = assess_reversal_risk(series, now_ns=series[-1]["prediction_ts_ns"], market_ticker=TICKER)
    assert result["level"] == "LOW"
    assert result["flip_alert"] is None
    assert result["source"] == "LIVE_MODEL_AND_KALSHI_QUOTE"
    assert result["live_orders_enabled"] is False


def test_adverse_book_and_model_drift_raise_high_reversal_risk():
    series = [sample(i, p=.82 - .009 * i, mid=.79 - .006 * i) for i in range(16)]
    result = assess_reversal_risk(series, now_ns=series[-1]["prediction_ts_ns"], market_ticker=TICKER)
    assert result["level"] == "HIGH"
    assert result["flip_alert"] is None


def test_persistent_up_to_down_transition_emits_timed_potential_flip_alert():
    probabilities = [.8] * 12 + [.72, .64, .56, .49, .42, .37, .33, .31]
    series = [sample(i, p=p, mid=.75 - .018 * max(0, i - 11)) for i, p in enumerate(probabilities)]
    result = assess_reversal_risk(series, now_ns=series[-1]["prediction_ts_ns"], market_ticker=TICKER)
    assert result["level"] == "HIGH"
    assert result["flip_alert"]["direction"] == "UP_TO_DOWN"
    assert result["flip_alert"]["detected_ts_ns"] <= series[-1]["prediction_ts_ns"]
    assert result["flip_alert"]["age_seconds"] <= 5


def test_short_bounce_and_unconfirmed_midpoint_crossing_do_not_trigger_flip():
    probs = [.75] * 12 + [.51, .49, .52, .53, .58, .62]
    series = [sample(i, p=p, mid=.7) for i, p in enumerate(probs)]
    result = assess_reversal_risk(series, now_ns=series[-1]["prediction_ts_ns"], market_ticker=TICKER)
    assert result["flip_alert"] is None


def test_no_data_stale_quote_degraded_and_mixed_market_fail_closed():
    series = [sample(i) for i in range(18)]
    now = series[-1]["prediction_ts_ns"]
    assert assess_reversal_risk(series[:3], now_ns=series[2]["prediction_ts_ns"], market_ticker=TICKER)["level"] == "UNAVAILABLE"
    assert assess_reversal_risk(series, now_ns=now + 7 * NS, market_ticker=TICKER)["level"] == "UNAVAILABLE"
    degraded = [dict(s) for s in series]
    degraded[-1]["feed_quality"] = {"healthy": False}
    assert assess_reversal_risk(degraded, now_ns=now, market_ticker=TICKER)["level"] == "UNAVAILABLE"
    missing = [dict(s) for s in series]
    missing[-1]["kalshi_yes_bid"] = None
    assert assess_reversal_risk(missing, now_ns=now, market_ticker=TICKER)["level"] == "UNAVAILABLE"
    wrong = [sample(i, ticker="KXBTC15M-OTHER") for i in range(18)]
    assert assess_reversal_risk(wrong, now_ns=now, market_ticker=TICKER)["level"] == "UNAVAILABLE"


def test_flip_alert_expires_and_does_not_leak_across_rounds():
    probs = [.8] * 12 + [.70, .60, .40, .35, .30]
    series = [sample(i, p=p, mid=.7 - .012 * max(0, i - 11)) for i, p in enumerate(probs)]
    extension = [sample(i, p=.3, mid=.4) for i in range(17, 47)]
    result = assess_reversal_risk(series + extension, now_ns=extension[-1]["prediction_ts_ns"], market_ticker=TICKER)
    assert result["flip_alert"] is None
    assert assess_reversal_risk(series, now_ns=series[-1]["prediction_ts_ns"], market_ticker="KXBTC15M-OTHER")["flip_alert"] is None
