"""Offline quote economics are hypothetical, NOT executable trading P/L."""
from __future__ import annotations

import math

import pytest

from kalshi_edge.bootstrap.quote_economics import audit_final_quote_economics


def row(ticker, *, p=.9, bid=.60, ask=.70, direction="ABOVE",
        actual="ABOVE", reason=None, settlement="SETTLED", model="v5"):
    return {
        "market_ticker": ticker,
        "market_close": "2026-10-31T18:00:00+00:00",
        "forecast": direction,
        "reason": reason,
        "settlement_status": settlement,
        "actual_side": actual,
        "p_yes": p,
        "p_no": None if p is None else 1-p,
        "kalshi_yes_bid": bid,
        "kalshi_yes_ask": ask,
        "model_version": model,
    }


def test_yes_no_execution_side_uses_ask_not_midpoint_or_yes_ask_for_no():
    report = audit_final_quote_economics([
        row("up", p=.9, bid=.6, ask=.7, direction="ABOVE", actual="ABOVE"),
        row("down", p=.2, bid=.3, ask=.5, direction="BELOW", actual="ABOVE"),
    ])
    a, b = report["opportunities"]
    assert a["chosen_side"] == "ABOVE"
    assert a["quoted_side_ask_dollars"] == "0.700000"
    assert a["model_gross_edge_per_contract"] == "0.200000"
    assert a["hypothetical_gross_settlement_per_contract"] == "0.300000"
    assert b["chosen_side"] == "BELOW"
    assert b["quoted_side_ask_dollars"] == "0.700000"  # 1 - YES bid
    assert b["model_gross_edge_per_contract"] == "0.100000"
    assert b["hypothetical_gross_settlement_per_contract"] == "-0.700000"
    assert report["summary"]["hypothetical_gross_settlement_sum_dollars"] == "-0.400000"
    assert report["summary"]["model_gross_edge_sum_dollars"] == "0.300000"


def test_low_confidence_skip_captured_as_counterfactual_not_executed_trade():
    report = audit_final_quote_economics([
        row("skip", p=.6, bid=.35, ask=.4, direction="SKIP",
            reason="CONFIDENCE_BELOW_69_PERCENT", actual="ABOVE")
    ])
    op = report["opportunities"][0]
    assert op["disposition"] == "SKIPPED_LOW_CONFIDENCE"
    assert op["chosen_side"] == "ABOVE"
    assert op["hypothetical_gross_settlement_per_contract"] == "0.600000"
    assert report["summary"]["settled_directional_quotes"] == 0
    assert report["summary"]["settled_counterfactual_skip_quotes"] == 1
    assert report["summary"]["hypothetical_gross_settlement_sum_dollars"] is None


def test_unhealthy_skip_and_no_quote_do_not_masquerade_as_opportunities():
    report = audit_final_quote_economics([
        row("feed", p=.99, direction="SKIP", reason="STALE_KALSHI_BOOK"),
        row("old", p=.9, bid=None, ask=None),
    ])
    assert report["summary"]["eligible_candidates"] == 1
    assert report["summary"]["valid_quote_candidates"] == 0
    assert report["summary"]["missing_or_invalid_quotes"] == 1
    assert report["summary"]["excluded_unhealthy_skips"] == 1
    assert report["opportunities"] == []


def test_pending_market_has_modeled_edge_but_no_realized_settlement():
    report = audit_final_quote_economics([
        row("pending", settlement="PENDING", actual=None),
    ])
    assert report["summary"]["valid_quote_candidates"] == 1
    assert report["summary"]["pending_settlements"] == 1
    assert report["opportunities"][0]["hypothetical_gross_settlement_per_contract"] is None
    assert report["summary"]["hypothetical_gross_settlement_sum_dollars"] is None


def test_hypothetical_fee_scenario_is_labeled_and_never_claimed_as_paid_fee():
    report = audit_final_quote_economics([
        row("up"),
    ], assumed_fee_per_contract="0.01")
    op = report["opportunities"][0]
    assert op["assumed_fee_per_contract_dollars"] == "0.010000"
    assert op["model_net_edge_per_contract_after_assumed_fee"] == "0.190000"
    assert op["hypothetical_net_settlement_per_contract_after_assumed_fee"] == "0.290000"
    assert report["summary"]["assumed_fee_per_contract_dollars"] == "0.010000"
    assert report["verified_fills"] is False
    assert report["verified_profitability"] is False


def test_duplicate_ticker_excludes_all_versions_of_same_market():
    report = audit_final_quote_economics([
        row("same", p=.99), row("same", p=.01, direction="BELOW"),
        row("unique"),
    ])
    assert report["summary"]["duplicate_records_excluded"] == 2
    assert report["summary"]["valid_quote_candidates"] == 1
    assert [x["ticker"] for x in report["opportunities"]] == ["unique"]


@pytest.mark.parametrize("prob,bid,ask", [
    (math.nan, .6, .7), (None, .6, .7), (True, .6, .7),
    (.9, math.nan, .7), (.9, .8, .7), (.9, -0.1, .8),
    (.9, .6, math.inf), (.9, .2, 1.1),
])
def test_missing_nonfinite_or_crossed_prices_fail_closed(prob,bid,ask):
    report = audit_final_quote_economics([row("x", p=prob, bid=bid, ask=ask)])
    assert report["summary"]["valid_quote_candidates"] == 0


def test_invalid_probability_complement_fail_closed():
    bad = row("bad")
    bad["p_no"] = .5
    assert audit_final_quote_economics([bad])["summary"]["invalid_probabilities"] == 1


@pytest.mark.parametrize("fee", ["-0.01", "NaN", "Infinity", "x", 0.01, True])
def test_invalid_hypothetical_fee_rejected(fee):
    with pytest.raises(ValueError):
        audit_final_quote_economics([row("x")], assumed_fee_per_contract=fee)


def test_report_is_explicitly_not_executable_and_empty_history_has_null_pnl():
    report = audit_final_quote_economics([])
    assert report["summary"]["valid_quote_candidates"] == 0
    assert report["summary"]["hypothetical_gross_settlement_sum_dollars"] is None
    assert report["not_executable"] is True
    assert "not actual" in report["warning"].lower()


def test_read_only_cli_uses_official_settlements_not_unauthenticated_market_guess(tmp_path, monkeypatch, capsys):
    from kalshi_edge.bootstrap.final_forecast import FinalForecastWriter, FinalForecastRecord
    from kalshi_edge.bootstrap.final_history import OfficialSettlement, _settlement_path
    from kalshi_edge.bootstrap.quote_economics import main
    from kalshi_edge.bootstrap.config import BootstrapSettings
    import json
    from pathlib import Path

    root = tmp_path / "bootstrap"
    ns = 1_000_000_000
    scheduled = 1_900_000_000 * ns
    forecast = FinalForecastRecord(
        market_ticker="KXBTC15M-TEST",
        strike=80000,
        scheduled_ts_ns=scheduled,
        prediction_ts_ns=scheduled,
        seconds_remaining=240,
        forecast="ABOVE",
        confidence=.8,
        p_yes=.9,
        p_no=.1,
        directional_probability=.9,
        kalshi_yes_bid=.60,
        kalshi_yes_ask=.70,
    )
    close_ns = scheduled + 240 * ns
    FinalForecastWriter(root).save(forecast, market_close_ts_ns=close_ns)
    outcome = OfficialSettlement(
        market_ticker=forecast.market_ticker,
        market_close_ts_ns=close_ns,
        result="yes",
        actual_side="ABOVE",
        fetched_ts_ns=close_ns + ns,
        market_status="settled",
    )
    path = _settlement_path(root, forecast)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(outcome.model_dump_json(), encoding="utf-8")

    class Settings:
        bootstrap_dir = root

    monkeypatch.setattr("kalshi_edge.bootstrap.config.BootstrapSettings", Settings)
    assert main([]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["summary"]["valid_quote_candidates"] == 1
    assert result["summary"]["settled_directional_quotes"] == 1
    assert result["summary"]["hypothetical_gross_settlement_sum_dollars"] == "0.300000"
