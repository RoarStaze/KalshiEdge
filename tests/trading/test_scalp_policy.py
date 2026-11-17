"""Research-only scalping policy: executable quotes, fee-inclusive exits.

These tests never access Kalshi credentials or submit any orders. A paper
signal is NOT a validated or executable trading advantage.
"""
from decimal import Decimal

import pytest

from kalshi_edge.trading.scalp_policy import (
    ScalpPosition, entry_signal, intraround_entry_signal, exit_signal, quote_from_kalshi, readiness,
)


def book(yes, no, *, yes_qty="12.00", no_qty="12.00"):
    return {"orderbook_fp": {
        "yes_dollars": [[yes, yes_qty]],
        "no_dollars": [[no, no_qty]],
    }}


def q(side, yes, no, timestamp_ns, **kw):
    return quote_from_kalshi(book(yes, no, **kw), side, timestamp_ns=timestamp_ns)


def test_bid_ask_are_executable_and_binary_complements_on_both_sides():
    up=q("UP", "0.4700", "0.4900", 10_000_000_000)
    down=q("DOWN", "0.4700", "0.4900", 10_000_000_000)
    assert (up.bid, up.ask, up.spread)==(
        Decimal(".47"), Decimal(".51"), Decimal(".04"))
    assert (down.bid, down.ask, down.spread)==(
        Decimal(".49"), Decimal(".53"), Decimal(".04"))


@pytest.mark.parametrize("item",[
    book("0.60", "0.41"), # crossed/inverted
    book("0.00", "0.50"),
    {"orderbook_fp": {"yes_dollars": [], "no_dollars": []}},
    book("NaN", "0.45"),
])
def test_invalid_or_nonexecutable_books_fail_closed(item):
    with pytest.raises(ValueError):
        quote_from_kalshi(item, "UP", timestamp_ns=10_000_000_000)


def test_no_entry_when_terminal_model_probability_or_momentum_is_missing():
    ticks=[q("UP","0.40","0.58",1_000_000_000),
           q("UP","0.41","0.57",3_000_000_000),
           q("UP","0.43","0.55",6_000_000_000)]
    assert entry_signal(ticks, "0.69", "1.00", 200).action=="SKIP"
    assert entry_signal(ticks[:1], "0.90", "1.00", 200).action=="SKIP"


def test_entry_requires_realistic_roundtrip_cost_and_bid_depth():
    flat=[q("UP","0.44","0.55",1_000_000_000),
          q("UP","0.45","0.54",3_000_000_000),
          q("UP","0.46","0.53",6_000_000_000)]
    decision=entry_signal(flat, "0.92", "1.00", 240)
    assert decision.action=="SKIP" # cannot pretend 1-cent move covers fees
    thin=[q("UP","0.40","0.58",1_000_000_000),
          q("UP","0.42","0.56",3_000_000_000),
          q("UP","0.44","0.54",6_000_000_000, yes_qty="0.01")]
    assert entry_signal(thin, "0.93", "1.00", 240).action=="SKIP"


def test_candidate_entry_is_only_a_research_signal_never_order_authorization():
    ticks=[q("UP","0.74","0.24",1_000_000_000,yes_qty="50",no_qty="50"),
           q("UP","0.77","0.21",3_000_000_000,yes_qty="50",no_qty="50"),
           q("UP","0.80","0.18",6_000_000_000,yes_qty="50",no_qty="50")]
    e=entry_signal(ticks, "0.98", "30.00", 300)
    assert e.action=="RESEARCH_BUY_SIGNAL"
    assert not e.live_eligible


def test_exit_marks_actual_liquidation_not_midpoint_or_unrealized_payout():
    position=ScalpPosition(side="DOWN", quantity=Decimal("2"),
        total_paid=Decimal("1.21"), entered_at_ns=1_000_000_000)
    mark=q("DOWN", "0.48", "0.49", 10_000_000_000)
    decision=exit_signal(position, mark, now_ns=10_000_000_000,
                         seconds_remaining=180)
    assert decision.mark_net_dollars < Decimal("0")
    assert decision.action=="STOP_LOSS_SIGNAL"
    assert not decision.live_eligible


def test_profit_exit_uses_bid_after_fee_not_last_trade_price():
    position=ScalpPosition(side="UP", quantity=Decimal("2.00"),
        total_paid=Decimal("0.82"), entered_at_ns=1_000_000_000)
    quote=q("UP", "0.53", "0.44", 15_000_000_000)
    decision=exit_signal(position, quote, now_ns=15_000_000_000,
                         seconds_remaining=180)
    assert decision.action=="TAKE_PROFIT_SIGNAL"
    assert decision.mark_net_dollars > Decimal("0")
    assert not decision.live_eligible


def test_stop_and_timeout_cannot_claim_liquidation_without_depth():
    position=ScalpPosition(side="UP", quantity=Decimal("2.00"),
        total_paid=Decimal("1.42"), entered_at_ns=1_000_000_000)
    thin=q("UP", "0.30", "0.65", 160_000_000_000, yes_qty="0.10")
    result=exit_signal(position, thin, now_ns=160_000_000_000,
                       seconds_remaining=20)
    assert result.action=="EXIT_BLOCKED_LIQUIDITY"
    assert not result.live_eligible


def test_restart_safe_readiness_does_not_enable_live_scalping():
    report=readiness()
    assert report["live_orders_enabled"] is False
    assert report["status"]=="RESEARCH_ONLY"
    assert report["assumes_fills"] is False
    assert report["requires_exchange_fill_backtest"] is True


def test_existing_autotrade_exposes_research_gate_without_live_execution():
    from kalshi_edge.trading.armed_autotrade import ArmedAutoTrader
    engine=ArmedAutoTrader(None, lambda: {})
    status=engine.status()
    assert status["state"]=="IDLE"
    assert status["scalp_research"]["status"]=="RESEARCH_ONLY"
    assert status["scalp_research"]["live_orders_enabled"] is False


def test_entry_costs_must_not_trigger_its_own_stop_loss():
    # A one-share round trip near 50c has an immediate fee+spread drag
    # larger than the proposed loss budget. No research BUY can be emitted.
    ticks=[q("UP","0.40","0.58",1_000_000_000),
           q("UP","0.42","0.56",3_000_000_000),
           q("UP","0.44","0.54",6_000_000_000)]
    d=entry_signal(ticks, "0.93", "1.00", 300)
    assert d.action=="SKIP"
    assert d.reason=="STOP_ALREADY_BREACHED_BY_COSTS"


def test_stale_liquidation_quote_cannot_trigger_executable_stop():
    position=ScalpPosition(side="UP",quantity=Decimal("2"),
        total_paid=Decimal("1.10"),entered_at_ns=1_000_000_000)
    old=q("UP","0.45","0.53",10_000_000_000)
    d=exit_signal(position,old,now_ns=20_000_000_000,
                  seconds_remaining=120)
    assert d.action=="EXIT_BLOCKED_STALE_QUOTE"


def test_trailing_and_time_exits_are_liquidation_signals_not_orders():
    mark=q("UP","0.50","0.48",160_000_000_000)
    pos=ScalpPosition(side="UP",quantity=Decimal("2"),
        total_paid=Decimal("0.95"),entered_at_ns=1_000_000_000,
        peak_net_dollars=Decimal("0.10"))
    trailing=exit_signal(pos,mark,now_ns=160_000_000_000,
                         seconds_remaining=180)
    assert trailing.action=="TRAILING_STOP_SIGNAL"
    pos2=ScalpPosition(side="UP",quantity=Decimal("2"),
        total_paid=Decimal("0.95"),entered_at_ns=150_000_000_000)
    clock=exit_signal(pos2,mark,now_ns=160_000_000_000,
                       seconds_remaining=30)
    assert clock.action=="TIME_EXIT_SIGNAL"
    assert not trailing.live_eligible and not clock.live_eligible


def test_independent_intraround_signal_up_before_final_without_model_probability():
    # 12m left, no final-forecast input exists; the paper strategy reacts to
    # sustained executable bid and ask momentum only.
    ticks=[q("UP",".74",".24",1_000_000_000,yes_qty="50",no_qty="50"),
           q("UP",".77",".21",4_000_000_000,yes_qty="50",no_qty="50"),
           q("UP",".80",".18",7_000_000_000,yes_qty="50",no_qty="50")]
    s=intraround_entry_signal(ticks, "30", 720)
    assert s.action=="RESEARCH_BUY_SIGNAL"
    assert not s.live_eligible
    assert "MOMENTUM" in s.reason


def test_independent_intraround_signal_down_from_opposite_price_movement():
    ticks=[q("DOWN",".24",".74",1_000_000_000,yes_qty="50",no_qty="50"),
           q("DOWN",".21",".77",4_000_000_000,yes_qty="50",no_qty="50"),
           q("DOWN",".18",".80",7_000_000_000,yes_qty="50",no_qty="50")]
    s=intraround_entry_signal(ticks, "30", 720)
    assert s.action=="RESEARCH_BUY_SIGNAL"
    assert not s.live_eligible


def test_intraround_signal_rejects_spread_narrowing_without_true_direction():
    # Bid rising while the ask falls is a tightening spread, not a signed
    # direction move. Both YES and NO must fail.
    for side in ("UP","DOWN"):
        ticks=[q(side,".45",".45",1_000_000_000),
               q(side,".46",".46",4_000_000_000),
               q(side,".47",".47",7_000_000_000)]
        assert intraround_entry_signal(ticks, "2", 720).action=="SKIP"


def test_intraround_signal_blocks_subsecond_or_stale_history_and_close_runway():
    ticks=[q("UP",".74",".24",1_000_000_000),
           q("UP",".77",".21",1_500_000_000),
           q("UP",".80",".18",1_900_000_000)]
    assert intraround_entry_signal(ticks, "2", 720).action=="SKIP"
    assert intraround_entry_signal(ticks, "2", 40).action=="SKIP"
    assert intraround_entry_signal(ticks, "2", 1100).action=="SKIP"


def test_intraround_fees_and_slippage_must_fit_stop_budget():
    ticks=[q("UP",".74",".24",1_000_000_000,yes_qty="50",no_qty="50"),
           q("UP",".77",".21",4_000_000_000,yes_qty="50",no_qty="50"),
           q("UP",".80",".18",7_000_000_000,yes_qty="50",no_qty="50")]
    one=intraround_entry_signal(ticks, "1", 720)
    assert one.action=="SKIP"
    assert one.reason=="STOP_ALREADY_BREACHED_BY_SIMULATED_COSTS"
    depth=[ticks[0],ticks[1],q("UP",".80",".18",7_000_000_000,
                              yes_qty="0.01",no_qty="50")]
    assert intraround_entry_signal(depth, "30", 720).action=="SKIP"


def test_paper_one_cent_net_profit_exits_even_if_legacy_five_percent_not_hit():
    pos=ScalpPosition(side="UP",quantity=Decimal("1"),
                      total_paid=Decimal(".82"),entered_at_ns=1_000_000_000)
    quote=q("UP",".85",".14",10_000_000_000)
    old=exit_signal(pos,quote,now_ns=10_000_000_000,seconds_remaining=300)
    paper=exit_signal(pos,quote,now_ns=10_000_000_000,
                      seconds_remaining=300,
                      paper_minimum_net_profit=Decimal(".01"))
    assert old.action=="HOLD"
    assert paper.mark_net_dollars >= Decimal(".01")
    assert paper.action=="TAKE_PROFIT_SIGNAL"
    assert not paper.live_eligible
