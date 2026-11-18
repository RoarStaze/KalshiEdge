"""V2 market-microstructure signals are causal, bounded, and read-only."""
from __future__ import annotations

from kalshi_edge.bootstrap.reversal_monitor import assess_reversal_risk

NS = 1_000_000_000
TICKER = "KXBTC15M-V2"
START = 1_800_100_000_000_000_000


def row(i, *, p=.78, mid=.72, brti=64060., strike=64000.,
        depth_yes=20., depth_no=20., signed=0., total=0., disagreement=0.):
    return {
        "prediction_ts_ns": START + i * NS,
        "market_ticker": TICKER,
        "status": "OK", "feed_quality": {"healthy": True},
        "p_yes": p, "kalshi_yes_bid": round(mid - .01, 6),
        "kalshi_yes_ask": round(mid + .01, 6),
        "strike": strike, "brti_value": brti,
        "brti_source_ts_ns": START + i * NS,
        "btc_reference": brti, "binance_source_ts_ns": START + i * NS,
        "kalshi_yes_bid_depth": depth_yes, "kalshi_yes_ask_depth": depth_no,
        "kalshi_taker_signed_volume_10s": signed,
        "kalshi_taker_total_volume_10s": total,
        "component_disagreement": disagreement,
        "brti_shadow_risk_ratio": 3.,
        "seconds_remaining": 300.,
    }


def run(rows, *, now_ns=None, ticker=TICKER):
    return assess_reversal_risk(rows, now_ns=now_ns or rows[-1]["prediction_ts_ns"],
                                market_ticker=ticker)


def test_v2_reports_signal_evidence_and_quality_without_claiming_accuracy():
    rows = [row(i) for i in range(35)]
    result = run(rows)
    assert result["level"] == "LOW"
    assert result["flip_alert"] is None
    assert result["signal_quality"] == "FULL"
    assert result["risk_score"] >= 0
    assert result["risk_score"] <= 100
    assert "risk_drivers" in result
    assert "microstructure" in result["evidence_sources"]
    assert result["calibrated_probability"] is None
    assert result["live_orders_enabled"] is False


def test_near_strike_and_disagreement_raise_risk_independently():
    rows = [row(i, brti=64001, disagreement=.42) for i in range(35)]
    result = run(rows)
    assert result["level"] in {"MEDIUM", "HIGH"}
    assert "MODEL_DISAGREEMENT" in result["risk_drivers"]
    assert "NEAR_STRIKE" in result["risk_drivers"]


def test_wide_quote_spread_is_not_misrepresented_as_low_reversal_risk():
    rows = [row(i) for i in range(35)]
    rows[-1]["kalshi_yes_bid"] = .63
    rows[-1]["kalshi_yes_ask"] = .77
    result = run(rows)
    assert result["level"] == "HIGH"
    assert "WIDE_SPREAD" in result["risk_drivers"]
    assert result["flip_alert"] is None


def test_early_down_warning_with_model_quote_brti_and_aggressor_pressure():
    rows = []
    for i in range(35):
        j = max(0, i - 24)
        rows.append(row(i, p=.82 - .012*j, mid=.80 - .009*j,
                        brti=64080-3.2*j,
                        depth_yes=max(2., 25. - j), depth_no=30.+j,
                        signed=-6. if j else 0., total=10. if j else 0.))
    result = run(rows)
    assert rows[-1]["p_yes"] > .5
    assert rows[-1]["kalshi_yes_bid"] > .5
    assert rows[-1]["brti_value"] > rows[-1]["strike"]
    assert result["flip_alert"]["direction"] == "DOWN"
    assert result["flip_alert"]["kind"] == "PRE_FLIP_WARNING"
    assert result["flip_alert"]["current_direction"] == "UP"
    assert "brti" in result["flip_alert"]["supporting_signals"]
    assert "taker_flow" in result["flip_alert"]["supporting_signals"]
    assert result["flip_alert"]["calibrated_probability"] is None


def test_no_warning_on_single_feed_contradiction_or_lookahead():
    rows = [row(i, p=.80 - .008*max(0,i-20), mid=.74,
                brti=64060.-2.*max(0,i-20)) for i in range(35)]
    assert run(rows)["flip_alert"] is None
    # A future-dated record is not admissible, even when its values predict.
    rows[-1]["prediction_ts_ns"] += 5*NS
    assert run(rows, now_ns=START+34*NS)["level"] == "UNAVAILABLE"


def test_extra_signals_are_optional_and_report_partial_quality():
    rows = [row(i) for i in range(35)]
    for item in rows:
        for field in ("brti_value", "brti_source_ts_ns", "strike",
                      "btc_reference", "binance_source_ts_ns",
                      "kalshi_yes_bid_depth", "kalshi_yes_ask_depth",
                      "kalshi_taker_signed_volume_10s",
                      "kalshi_taker_total_volume_10s",
                      "component_disagreement", "brti_shadow_risk_ratio"):
            item.pop(field, None)
    result = run(rows)
    assert result["level"] == "LOW"
    assert result["signal_quality"] == "PARTIAL"
    assert "microstructure" not in result["evidence_sources"]


def test_stale_brti_does_not_count_as_independent_evidence():
    rows = [row(i, brti=64000.5) for i in range(35)]
    for item in rows:
        item["brti_source_ts_ns"] = START
    result = run(rows)
    assert "brti" not in result["evidence_sources"]
    assert "NEAR_STRIKE" not in result["risk_drivers"]
    assert result["signal_quality"] == "PARTIAL"


def test_pre_flip_signal_does_not_survive_crossing():
    rows = []
    for i in range(36):
        j = max(0,i-24)
        rows.append(row(i,p=.82-.03*j,mid=.78-.012*j,
                        brti=64100-4*j,depth_yes=6,depth_no=40))
    before=run(rows[:35])
    after=run(rows)
    assert before["flip_alert"] is None or before["flip_alert"]["direction"] == "DOWN"
    assert after["flip_alert"] is None
