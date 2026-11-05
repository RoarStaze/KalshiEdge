"""Profit Intelligence must distinguish forecast skill from executed returns.

Pure offline fixtures: no exchange calls, credentials or live orders.
"""
from __future__ import annotations

from decimal import Decimal

from kalshi_edge.trading.profit_intelligence import build_profit_intelligence


def _forecast(ticker, p_yes, actual, *, forecast="ABOVE", status="SETTLED",
              bid=0.65, ask=0.68, checkpoint=480):
    return {
        "market_ticker": ticker,
        "checkpoint_seconds": checkpoint,
        "forecast": forecast,
        "settlement_status": status,
        "actual_side": actual,
        "p_yes": p_yes,
        "p_no": 1 - p_yes,
        "kalshi_yes_bid": bid,
        "kalshi_yes_ask": ask,
        "coin_flip_assessment": "LOW",
    }


def test_empty_history_never_invents_profit_or_model_skill():
    result = build_profit_intelligence([], [])
    assert result["forecast"]["settled_markets"] == 0
    assert result["forecast"]["brier_score"] is None
    assert result["forecast"]["model_accuracy"] is None
    assert result["execution"]["verified_net_pnl_dollars"] is None
    assert result["execution"]["net_per_observed_hour_dollars"] is None
    assert result["ready_to_change_live_model"] is False


def test_all_verified_outcomes_including_coinflip_skips_score_model_probability():
    f = [
        _forecast("KXBTC15M-A", .80, "ABOVE"),
        _forecast("KXBTC15M-B", .25, "ABOVE", forecast="BELOW"),
        _forecast("KXBTC15M-C", .60, "BELOW", forecast="SKIP"),
        _forecast("KXBTC15M-D", .99, None, status="PENDING"),
        _forecast("KXBTC15M-E", .80, "ABOVE", checkpoint=240),
    ]
    f[2]["coin_flip_assessment"] = "HIGH"
    report = build_profit_intelligence(f, [])
    model = report["forecast"]
    assert model["settled_markets"] == 3
    assert model["issued_predictions"] == 2
    assert model["correct_predictions"] == 1
    assert model["coin_flip_high_settled"] == 1
    assert model["model_accuracy"] == .5
    assert abs(model["brier_score"] - ((.2 ** 2 + .75 ** 2 + .6 ** 2) / 3)) < 1e-10
    assert model["eligible_quote_comparisons"] == 2
    assert report["ready_to_change_live_model"] is False


def test_invalid_duplicate_or_inconsistent_data_cannot_improve_metrics():
    same = _forecast("KXBTC15M-DUP", .99, "ABOVE")
    wrong = _forecast("KXBTC15M-WRONG", .99, "BELOW")
    wrong["p_no"] = .50
    other = _forecast("KXBTC15M-OTHER", .75, "BELOW")
    other["kalshi_yes_bid"] = .95
    other["kalshi_yes_ask"] = .70
    result = build_profit_intelligence([same, same, wrong, other], [])
    assert result["forecast"]["duplicate_tickers_excluded"] == 2
    assert result["forecast"]["settled_markets"] == 1
    assert result["forecast"]["eligible_quote_comparisons"] == 0


def test_verified_execution_profit_and_no_fills_are_separate():
    entries = [
        {"ticker": "KXBTC15M-A", "status": "FILLED",
         "pnl_status": "SETTLED", "realized_pnl_dollars": "0.0375",
         "attempted_at_ns": 1_900_000_000_000_000_000},
        {"ticker": "KXBTC15M-B", "status": "NO_FILL",
         "pnl_status": "UNAVAILABLE", "realized_pnl_dollars": None,
         "attempted_at_ns": 1_900_004_000_000_000_000},
        {"ticker": "KXBTC15M-C", "status": "FILLED",
         "pnl_status": "PENDING", "realized_pnl_dollars": None,
         "attempted_at_ns": 1_900_008_000_000_000_000},
    ]
    result = build_profit_intelligence([], entries)
    execution = result["execution"]
    assert Decimal(execution["verified_net_pnl_dollars"]) == Decimal("0.0375")
    assert execution["verified_settled_trades"] == 1
    assert execution["no_fill_trades"] == 1
    assert execution["filled_unverified_trades"] == 1
    assert execution["net_per_observed_hour_dollars"] is None
    assert result["ready_to_change_live_model"] is False


def test_trailing_window_does_not_claim_hourly_rate_without_one_hour_and_complete_fills():
    entries = [
        {"ticker": "KXBTC15M-A", "status": "FILLED",
         "pnl_status": "SETTLED", "realized_pnl_dollars": "0.04",
         "attempted_at_ns": 1_900_000_000_000_000_000},
        {"ticker": "KXBTC15M-B", "status": "FILLED",
         "pnl_status": "SETTLED", "realized_pnl_dollars": "-0.02",
         "attempted_at_ns": 1_900_003_600_000_000_000},
    ]
    result = build_profit_intelligence([], entries)
    execution = result["execution"]
    assert Decimal(execution["verified_net_pnl_dollars"]) == Decimal("0.02")
    assert Decimal(execution["net_per_observed_hour_dollars"]) == Decimal("0.02")
    assert execution["observed_span_hours"] == 1.0
    assert result["ready_to_change_live_model"] is False
