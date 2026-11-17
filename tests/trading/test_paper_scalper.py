"""Isolated deterministic paper scalping: no live trading or private API."""

from decimal import Decimal
from pathlib import Path

import pytest

from kalshi_edge.trading.paper_scalper import PaperScalpTrader


NS = 1_000_000_000
TICKER = "KXBTC15M-TEST"


def book(yes, no, depth="60"):
    return {"orderbook_fp": {
        "yes_dollars": [[yes, depth]],
        "no_dollars": [[no, depth]],
    }}


class Quotes:
    def __init__(self):
        self.next_book = book("0.74", "0.24")
        self.calls = []
        self.private_orders = 0

    def quote(self, ticker):
        self.calls.append(ticker)
        return {"market": {"ticker": ticker, "status": "active"}}, self.next_book

    def send_order(self, *a, **kw):
        self.private_orders += 1
        raise AssertionError("PAPER MODE MUST NEVER SEND REAL ORDERS")

    def close(self):
        pass


class Live:
    def __init__(self):
        self.t = 1_900_000_000 * NS
        self.close = self.t + 720 * NS
        self.feed_age_seconds = 0
        self.live_status = "LIVE"
        self.ticker = TICKER
        self.final_probability = 0.98
        self.final_side = "ABOVE"

    def __call__(self):
        return {
            "status": self.live_status,
            "market_ticker": self.ticker,
            "latest_prediction": {
                "status": "OK",
                "market_ticker": self.ticker,
                "prediction_ts_ns": self.t - self.feed_age_seconds * NS,
            },
            "market_close_ts_ns": self.close,
            "checkpoint_ts_ns": self.close - 480 * NS,
            "final_forecast": {
                "market_ticker": self.ticker,
                "scheduled_ts_ns": self.close - 480 * NS,
                "forecast": self.final_side,
                "directional_probability": self.final_probability,
            },
        }

    def advance(self, seconds):
        self.t += seconds * NS


def make(tmp_path, *, live=None, client=None, capital="1000"):
    live = live or Live()
    client = client or Quotes()
    engine = PaperScalpTrader(
        tmp_path, live, client=client, now_ns=lambda: live.t,
        initial_bankroll=Decimal(capital),
    )
    return engine, live, client


def enter(engine, live, client):
    for yes, no in [("0.74", "0.24"), ("0.77", "0.21"), ("0.80", "0.18")]:
        client.next_book = book(yes, no)
        engine.tick()
        live.advance(3)


def test_requires_arm_but_not_eight_minute_final_or_model_probability(tmp_path):
    engine, live, client = make(tmp_path)
    enter(engine, live, client)
    assert not engine.status()["recent_cycles"]
    assert not client.calls
    with pytest.raises(ValueError):
        engine.arm("55")  # above 5% of the paper bankroll
    engine.arm("50")
    live.final_side = "BELOW"  # explicitly opposite the UP book movement
    live.final_probability = .01  # not a signal input for paper scalping
    state = live()
    assert live.close - live.t > 480 * NS
    assert state["final_forecast"]["directional_probability"] == .01
    # The independent paper trader can start and enter with >8 min remaining.
    enter(engine, live, client)
    assert engine.status()["open_position"]["side"] == "UP"
    assert client.private_orders == 0


def test_paper_buy_profit_exit_and_repeat_in_same_market(tmp_path):
    engine, live, client = make(tmp_path)
    engine.arm("50")
    enter(engine, live, client)
    first = engine.status()["open_position"]
    assert first and first["side"] == "UP"
    assert first["status"] == "OPEN"
    assert Decimal(first["entry_price"]) == Decimal(".83")  # 1c pessimistic slip
    client.next_book = book(".95", ".03")
    engine.tick()
    state = engine.status()
    assert state["open_position"] is None
    assert state["recent_cycles"][0]["exit_reason"] == "TAKE_PROFIT_SIGNAL"
    assert Decimal(state["recent_cycles"][0]["paper_net_pnl"]) > 0
    assert Decimal(state["recent_cycles"][0]["exit_price"]) == Decimal("0.94")
    assert client.private_orders == 0
    # Cooldown followed by a *fresh* independent 3-quote signal permits reentry.
    live.advance(12)
    enter(engine, live, client)
    assert len(engine.status()["recent_cycles"]) == 2
    assert engine.status()["open_position"] is not None


def test_loss_stop_and_pause_keep_monitoring_open_position(tmp_path):
    engine, live, client = make(tmp_path)
    engine.arm("50")
    enter(engine, live, client)
    assert engine.status()["open_position"]
    engine.pause()
    client.next_book = book(".55", ".42")
    engine.tick()
    st = engine.status()
    assert not st["enabled"]
    assert st["open_position"] is None
    assert st["recent_cycles"][0]["exit_reason"] == "STOP_LOSS_SIGNAL"
    assert Decimal(st["recent_cycles"][0]["paper_net_pnl"]) < 0
    assert client.private_orders == 0


def test_restart_does_not_rearm_but_keeps_protecting_open_paper_position(tmp_path):
    engine, live, client = make(tmp_path)
    engine.arm("50")
    enter(engine, live, client)
    before = engine.status()["open_position"]
    restarted, _, _ = make(tmp_path, live=live, client=client)
    assert restarted.status()["enabled"] is False
    assert restarted.status()["open_position"]["id"] == before["id"]
    client.next_book = book(".95", ".03")
    restarted.tick()
    assert restarted.status()["open_position"] is None
    assert restarted.status()["recent_cycles"][0]["exit_reason"] == "TAKE_PROFIT_SIGNAL"
    assert not restarted.status()["enabled"]


def test_insufficient_depth_keeps_position_and_marks_risk(tmp_path):
    engine, live, client = make(tmp_path)
    engine.arm("50")
    enter(engine, live, client)
    client.next_book = book(".55", ".42", depth=".01")
    engine.tick()
    st = engine.status()
    assert st["open_position"] is not None
    assert st["last_signal"] == "EXIT_BLOCKED_LIQUIDITY"
    assert st["recent_cycles"][0]["status"] == "OPEN"
    assert st["open_position"]["paper_net_pnl"] is None


def test_market_rollover_never_fabricates_sale_or_settlement(tmp_path):
    engine, live, client = make(tmp_path)
    engine.arm("50")
    enter(engine, live, client)
    live.t = live.close + 1
    live.ticker = "KXBTC15M-NEXT"
    engine.tick()
    assert engine.status()["open_position"] is not None
    assert engine.status()["last_signal"] == "AWAITING_OFFICIAL_SETTLEMENT"
    assert client.private_orders == 0
    # Only an explicitly supplied *verified* result may conclude the paper
    # cashflow. The caller must validate that result's market identity.
    engine.tick(settlement=(TICKER, "no"))
    ended = engine.status()
    assert ended["open_position"] is None
    assert ended["recent_cycles"][0]["exit_reason"] == "OFFICIAL_SETTLEMENT"
    assert Decimal(ended["recent_cycles"][0]["paper_net_pnl"]) < 0


def test_same_market_cap_and_durable_budget_limit(tmp_path):
    engine, live, client = make(tmp_path)
    engine.arm("50")
    cap=engine.status()["max_cycles_per_market"]
    assert cap >= 3
    for cycle in range(cap):
        enter(engine, live, client)
        assert engine.status()["open_position"] is not None, cycle
        client.next_book = book(".95", ".03")
        engine.tick()
        assert engine.status()["open_position"] is None
        live.advance(12)
    assert len(engine.status()["recent_cycles"]) == cap
    enter(engine, live, client)
    assert engine.status()["open_position"] is None
    assert engine.status()["last_signal"]=="MARKET_CYCLE_LIMIT"


def test_down_side_uses_no_bid_and_separate_simulated_accounting(tmp_path):
    engine, live, client = make(tmp_path)
    live.final_side = "BELOW"
    engine.arm("50")
    for yes, no in [(".24", ".74"), (".21", ".77"), (".18", ".80")]:
        client.next_book = book(yes, no)
        engine.tick()
        live.advance(3)
    opened=engine.status()["open_position"]
    assert opened and opened["side"]=="DOWN"
    assert Decimal(opened["entry_price"]) == Decimal("0.83")
    client.next_book=book(".03", ".95")
    engine.tick()
    assert engine.status()["recent_cycles"][0]["exit_reason"]=="TAKE_PROFIT_SIGNAL"
    assert Decimal(engine.status()["recent_cycles"][0]["paper_net_pnl"])>0


def test_paper_only_status_and_fail_closed_on_malformed_public_data(tmp_path):
    engine, live, client = make(tmp_path)
    assert engine.status()["live_order_submission"] is False
    assert engine.status()["mode"] == "PAPER_SCALP_SIMULATED"
    engine.arm("50")
    client.next_book = {"orderbook_fp": {"yes_dollars": [], "no_dollars": []}}
    engine.tick()
    assert engine.status()["open_position"] is None
    assert client.private_orders == 0


def test_three_loss_circuit_breaker_resets_on_new_utc_day(tmp_path):
    engine, live, _ = make(tmp_path)
    yesterday = live.t - 86_400 * NS
    for i in range(3):
        when = yesterday + i * 10 * NS
        engine.ledger.insert(
            ticker=f"KXBTC15M-LOSS{i}", side="UP",
            quantity=Decimal("1"), price=Decimal(".50"),
            fee=Decimal(".01"), paid=Decimal(".51"),
            now_ns=when, close_ns=when + 600 * NS,
        )
        row = engine.ledger.open_position()
        engine.ledger.close(
            row["id"], price=Decimal(".10"), fee=Decimal(".01"),
            now_ns=when + NS, reason="STOP_LOSS_SIGNAL",
            pnl=Decimal("-.42"),
        )
    risk = engine._risk(live.t)
    assert risk["daily_losses"] == Decimal("0")
    assert risk["loss_streak"] == 0


def test_down_paper_entry_ignores_opposite_final_model_direction(tmp_path):
    engine,live,client=make(tmp_path)
    live.final_side="ABOVE"
    live.final_probability=.999
    engine.arm("50")
    for yes,no in [(".24",".74"),(".21",".77"),(".18",".80")]:
        client.next_book=book(yes,no)
        engine.tick()
        live.advance(3)
    assert engine.status()["open_position"]["side"]=="DOWN"
    assert client.private_orders==0


def test_stale_predictor_market_identity_fails_closed_without_new_paper_buy(tmp_path):
    engine,live,client=make(tmp_path)
    engine.arm("50")
    live.feed_age_seconds=60
    enter(engine,live,client)
    assert engine.status()["open_position"] is None
    assert engine.status()["last_signal"]=="WAITING_FOR_FRESH_LIVE_MARKET"
    assert client.calls==[]
    live.feed_age_seconds=0
    enter(engine,live,client)
    assert engine.status()["open_position"] is not None


def test_degraded_live_market_blocks_entries_without_blocking_open_exit(tmp_path):
    engine,live,client=make(tmp_path)
    engine.arm("50")
    live.live_status="DEGRADED"
    enter(engine,live,client)
    assert engine.status()["open_position"] is None
    live.live_status="LIVE"
    enter(engine,live,client)
    assert engine.status()["open_position"] is not None
    live.live_status="DEGRADED"
    client.next_book=book(".95",".03")
    engine.tick()
    assert engine.status()["open_position"] is None
    assert engine.status()["recent_cycles"][0]["exit_reason"]=="TAKE_PROFIT_SIGNAL"


def test_both_sides_scanned_with_one_public_book_request_per_tick(tmp_path):
    engine,live,client=make(tmp_path)
    engine.arm("50")
    client.next_book=book(".45",".45")
    for _ in range(3):
        engine.tick()
        live.advance(3)
    assert len(client.calls)==3
    assert engine.status()["open_position"] is None
    assert engine.status()["entry_policy"]=="INTRAROUND_UP_DOWN_BOOK_MOMENTUM"
    assert engine.status()["paper_minimum_net_profit_dollars"]=="0.01"
    assert engine.status()["live_order_submission"] is False


def test_paper_small_gain_does_not_exit_far_below_loss_budget(tmp_path):
    engine,live,client=make(tmp_path)
    engine.arm("50")
    enter(engine,live,client)
    position=engine.status()["open_position"]
    entry_paid=Decimal(position["total_paid"])
    # A small net gain must no longer immediately close a position exposed
    # to an 8%-of-debit stop. An asymmetric 1-cent profit / 8-cent stop
    # requires over 88% successful exits before overshoot and settlement.
    client.next_book=book(".87",".11")
    engine.tick()
    mid=engine.status()
    assert mid["open_position"] is not None
    assert mid["last_signal"]=="HOLD"
    # A large enough *executable* favorable move may satisfy the higher,
    # fee-adjusted reward target; never claim a guaranteed profit.
    client.next_book=book(".95",".03")
    engine.tick()
    after=engine.status()
    assert after["open_position"] is None
    row=after["recent_cycles"][0]
    assert row["exit_reason"]=="TAKE_PROFIT_SIGNAL"
    assert Decimal(row["paper_net_pnl"])>=max(Decimal(".01"),entry_paid*Decimal(".08"))


def test_missing_model_probability_and_no_final_record_still_allow_paper_momentum(tmp_path):
    engine,live,client=make(tmp_path)
    engine.arm("50")
    live.final_probability=None
    enter(engine,live,client)
    assert engine.status()["open_position"]["side"]=="UP"
    assert client.private_orders==0


def test_paper_dynamic_reward_target_is_not_tiny_relative_to_stop_budget():
    from kalshi_edge.trading.paper_scalper import (
        paper_profit_target, paper_stop_budget,
    )
    for debit in (Decimal(".70"),Decimal("1"),Decimal("5"),Decimal("50")):
        stop=paper_stop_budget(debit)
        target=paper_profit_target(debit)
        assert target >= stop
        assert target > Decimal(".01")
        assert target >= stop*Decimal("1.25")


def test_paper_skips_high_price_momentum_when_reward_ceiling_is_insufficient(tmp_path):
    engine,live,client=make(tmp_path)
    engine.arm("50")
    # This high-price UP breakout still meets the three-tick momentum
    # criterion, but there is insufficient room below the $1 binary ceiling
    # to compensate for the modeled stop budget.
    for yes,no in ((".88",".10"),(".90",".08"),(".92",".06")):
        client.next_book=book(yes,no)
        engine.tick()
        live.advance(3)
    state=engine.status()
    assert state["open_position"] is None
    assert state["last_signal"]=="PAPER_MAX_REWARD_BELOW_REQUIRED_TARGET"
    assert client.private_orders==0


def test_paper_status_discloses_the_reward_to_risk_rule(tmp_path):
    engine,_,client=make(tmp_path)
    status=engine.status()
    assert status["live_order_submission"] is False
    assert status["paper_minimum_reward_to_stop_ratio"]=="1.25"
    assert status["paper_profit_target_rule"]=="MAX_1_CENT_OR_1_25X_NOMINAL_STOP"
    assert client.private_orders==0


def test_new_paper_entry_persists_reward_target_for_restart(tmp_path):
    engine,live,client=make(tmp_path)
    engine.arm("50")
    enter(engine,live,client)
    old=engine.status()["open_position"]
    assert old is not None
    assert Decimal(old["profit_target"]) >= Decimal(old["total_paid"]) * Decimal(".08") * Decimal("1.25")
    restarted,_,_=make(tmp_path,live=live,client=client)
    new=restarted.status()["open_position"]
    assert new["id"]==old["id"]
    assert new["profit_target"]==old["profit_target"]
    assert not restarted.status()["enabled"]


def test_legacy_open_paper_entry_keeps_original_one_cent_target(tmp_path):
    # Existing saved paper inventories must not acquire a larger new exit
    # target mid-trade during a code update.
    engine,live,client=make(tmp_path)
    engine.ledger.insert(
        ticker=TICKER,side="UP",quantity=Decimal("1"),
        price=Decimal(".80"),fee=Decimal(".02"),paid=Decimal(".82"),
        now_ns=live.t-10*NS,close_ns=live.close,
    )
    row=engine.status()["open_position"]
    assert row is not None and row["profit_target"] is None
    client.next_book=book(".87",".11")
    engine.tick()
    result=engine.status()
    assert result["open_position"] is None
    assert result["recent_cycles"][0]["exit_reason"]=="TAKE_PROFIT_SIGNAL"
    assert Decimal(result["recent_cycles"][0]["paper_net_pnl"])>=Decimal(".01")


def test_legacy_sqlite_paper_database_migrates_without_erasing_open_inventory(tmp_path):
    import sqlite3
    database=tmp_path/"trading"/"paper_scalp.sqlite3"
    database.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(database) as db:
        db.execute("""CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)""")
        db.execute("INSERT INTO settings(key,value) VALUES('initial_bankroll','1000')")
        db.execute("""CREATE TABLE cycles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            side TEXT NOT NULL,
            status TEXT NOT NULL,
            quantity TEXT NOT NULL,
            entry_price TEXT NOT NULL,
            entry_fee TEXT NOT NULL,
            total_paid TEXT NOT NULL,
            entered_at_ns INTEGER NOT NULL,
            close_ns INTEGER NOT NULL,
            peak_net TEXT NOT NULL DEFAULT '0',
            exit_price TEXT,
            exit_fee TEXT,
            exit_ns INTEGER,
            exit_reason TEXT,
            paper_net_pnl TEXT
        )""")
        db.execute("""INSERT INTO cycles(
            ticker,side,status,quantity,entry_price,entry_fee,total_paid,
            entered_at_ns,close_ns,peak_net) VALUES(
            ?,?,'OPEN',?,?,?,?,?,?,'0')""",(
            TICKER,"UP","1",".8",".02",".82",
            1_900_000_000*NS-10*NS,1_900_000_720*NS,
        ))
    engine,live,client=make(tmp_path)
    opened=engine.status()["open_position"]
    assert opened["status"]=="OPEN"
    assert opened["profit_target"] is None
    assert opened["side"]=="UP"
    assert not engine.status()["enabled"]
    client.next_book=book(".87",".11")
    engine.tick()
    assert engine.status()["open_position"] is None
    assert engine.status()["recent_cycles"][0]["exit_reason"]=="TAKE_PROFIT_SIGNAL"
