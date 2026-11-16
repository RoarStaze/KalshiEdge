"""No-training policy correction: the 63% displayed probability is the gate.

These are synthetic contract tests, NOT evidence of improved live profitability.
"""
from __future__ import annotations

from kalshi_edge.bootstrap.final_forecast import FinalForecastGate, MINIMUM_FINAL_CONFIDENCE
from kalshi_edge.bootstrap.live import LivePredictionRecord
from kalshi_edge.bootstrap.live_kalshi import LiveMarket
from kalshi_edge.bootstrap.types import FeedQuality
from kalshi_edge.bootstrap.dashboard_ui import PAGE
from kalshi_edge.bootstrap.manual_trade_ui import SCRIPT
from kalshi_edge.bootstrap.site_pages import STYLE

NS = 1_000_000_000
OPEN = 1_900_000_000 * NS
CLOSE = OPEN + 900 * NS
DUE = CLOSE - 480 * NS


def _final(p_yes: float, *, risk: float | None = None):
    market = LiveMarket(
        ticker="KXBTC15M-TEST-63", strike=100_000.0,
        open_ts_ns=OPEN, close_ts_ns=CLOSE,
    )
    prediction = LivePredictionRecord(
        market_ticker=market.ticker, prediction_ts_ns=DUE,
        status="OK", p_yes=p_yes, p_no=1 - p_yes,
        predicted_side="ABOVE" if p_yes >= .5 else "BELOW",
        confidence=min(1., 2 * abs(p_yes - .5)),
        feed_quality=FeedQuality(healthy=True),
        brti_shadow_risk_ratio=risk,
    )
    return FinalForecastGate().evaluate(market, now_ns=DUE, prediction=prediction)


def test_predicted_direction_probability_7838_not_skipped_for_legacy_strength():
    assert MINIMUM_FINAL_CONFIDENCE == .63
    final = _final(.7838, risk=1.0)
    assert final.forecast == "ABOVE"
    assert final.reason is None
    assert final.confidence < .63
    assert final.directional_probability == .7838


def test_at_or_below_63_percent_direction_probability_is_skipped():
    boundary = _final(.63, risk=1.)
    assert boundary.forecast == "SKIP"
    assert boundary.reason == "DIRECTIONAL_PROBABILITY_AT_OR_BELOW_63_PERCENT"
    below = _final(.62)
    assert below.forecast == "SKIP"
    assert _final(.631).forecast == "ABOVE"


def test_below_contract_direction_and_flip_risk_keep_same_protections():
    assert _final(.22, risk=1.0).forecast == "BELOW"
    veto = _final(.7838, risk=.10)
    assert veto.forecast == "SKIP"
    assert veto.reason == "COIN_FLIP_RISK_HIGH"


def test_trade_page_calls_it_directional_probability_not_63_strength():
    assert "63% predicted-direction probability" in PAGE
    assert "63% model strength" not in PAGE
    assert "81.5%" not in PAGE
    assert '<div class="health-row"><span>SKIP threshold</span><strong>63% confidence' not in PAGE


def test_autotrade_history_has_user_friendly_status_labels_preserving_raw_details():
    assert 'function autoHistoryStatusLabel(status)' in SCRIPT
    assert 'case "NOT_SUBMITTED":return "SKIPPED";' in SCRIPT
    assert 'case "FILLED":return "BOUGHT";' in SCRIPT
    assert 'case "PARTIAL":return "PARTIALLY BOUGHT";' in SCRIPT
    assert 'state.textContent=autoHistoryStatusLabel(entry.status);' in SCRIPT
    assert 'const outcome=String(stage.status||"UNKNOWN").replaceAll("_"," ");' in SCRIPT
    assert 'entry.status==="NOT_SUBMITTED"?"No order submitted"' in SCRIPT


def test_trade_panels_use_equal_grid_columns_at_desktop():
    assert '.layout{grid-template-columns:repeat(2,minmax(0,1fr));gap:22px;align-items:stretch}' in STYLE
    assert 'body[data-page="trade"] .layout{flex:1;min-height:0;gap:16px}' in STYLE
