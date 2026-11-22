"""Regression tests for the isolated, paper-only KXBTC15M UP Strat."""
from __future__ import annotations

from decimal import Decimal as D
from pathlib import Path

from kalshi_edge.trading.strat_policy import (
    UpQuote, evaluate_up_momentum, evaluate_side_momentum,
)
from kalshi_edge.trading.strat_paper import StratPaperTrader

NS = 1_000_000_000
BASE = 1_800_000_000 * NS
TICKER = "KXBTC15M-TEST123"


def quotes(bids, asks=None, *, base=BASE, ticker=TICKER):
    if asks is None:
        asks = [v + D("0.02") for v in bids]
    return [
        UpQuote(ticker, base + index * NS, bid, ask,
                D("100"), D("100"))
        for index, (bid, ask) in enumerate(zip(bids, asks))
    ]


def test_entry_requires_2_minutes_and_65_85_and_new_acceleration():
    series = quotes(list(map(D, ["0.64", "0.64", "0.65", "0.66", "0.68"])))
    assert evaluate_up_momentum(series, 120, holding=False).action == "BUY"
    assert evaluate_up_momentum(series, 119.999, holding=False).action == "WAIT"
    assert evaluate_up_momentum(series, 901, holding=False).action == "WAIT"
    assert evaluate_up_momentum(
        quotes([q.bid - D("0.06") for q in series]), 120, holding=False
    ).reason == "UP_ASK_OUTSIDE_65_85"
    assert evaluate_up_momentum(
        quotes([q.bid + D("0.18") for q in series]), 120, holding=False
    ).reason == "UP_ASK_OUTSIDE_65_85"
    assert evaluate_up_momentum(
        quotes(list(map(D, ["0.64", "0.65", "0.66", "0.67", "0.68"]))),
        120, holding=False,
    ).action == "WAIT"


def test_exit_remains_enabled_after_two_minute_cutoff():
    series = quotes(list(map(D, ["0.64", "0.66", "0.69", "0.69", "0.69"])))
    signal = evaluate_up_momentum(series, 80, holding=True)
    assert signal.action == "SELL"
    assert signal.reason == "UP_MOMENTUM_DECELERATED"
    assert evaluate_up_momentum(
        quotes(list(map(D, ["0.69", "0.69", "0.68", "0.67", "0.66"]))),
        60, holding=True,
    ).action == "SELL"


def test_stale_gapped_or_other_ticker_samples_fail_closed():
    base = quotes(list(map(D, ["0.64", "0.64", "0.65", "0.66", "0.68"])))
    assert evaluate_up_momentum(base[:4], 600, holding=False).action == "WAIT"
    assert evaluate_up_momentum(
        [*base[:4], UpQuote("KXBTC15M-OTHER", base[-1].observed_ns,
                           D("0.68"), D("0.70"), D("100"), D("100"))],
        600, holding=False,
    ).action == "WAIT"
    shifted = [UpQuote(x.ticker, x.observed_ns + i * 5 * NS,
                       x.bid, x.ask, x.bid_depth, x.ask_depth)
               for i, x in enumerate(base)]
    assert evaluate_up_momentum(shifted, 600, holding=False).action == "WAIT"


class FakeMarketClient:
    def __init__(self, prices):
        self.prices = iter(prices)
        self.closed = False

    def quote(self, ticker):
        bid, ask = next(self.prices)
        market = {"market": {"ticker": ticker, "status": "active"}}
        book = {"orderbook_fp": {
            "yes_dollars": [[str(bid), "100"]],
            "no_dollars": [[str(D("1") - ask), "100"]],
        }}
        return market, book

    def close(self):
        self.closed = True


def test_paper_cycle_uses_both_legs_and_durable_ledger(tmp_path: Path):
    bids = list(map(D, [
        "0.64", "0.64", "0.65", "0.66", "0.68",
        "0.68", "0.68", "0.68",
    ]))
    asks = [bid + D("0.02") for bid in bids]
    now = [BASE]
    close = BASE + 600 * NS

    def live():
        return {
            "market_ticker": TICKER,
            "market_close_ts_ns": close,
            "latest_prediction": {
                "market_ticker": TICKER,
                "prediction_ts_ns": now[0],
            },
        }

    client = FakeMarketClient(zip(bids, asks))
    trader = StratPaperTrader(tmp_path, live, client=client, clock=lambda: now[0])
    assert trader.status()["enabled"] is False
    trader.arm("1.00")
    for i in range(len(bids)):
        now[0] = BASE + i * NS
        trader.tick(now[0])
    history = trader.ledger.recent()
    assert len(history) == 1
    assert history[0]["status"] == "CLOSED"
    assert history[0]["side"] == "UP"
    assert history[0]["exit_reason"] == "UP_MOMENTUM_SLOWED"
    assert D(history[0]["paper_net_pnl"]) < 0  # drag is not fabricated away
    trader.close()
    assert client.closed is True

    # Restart must not carry an armed session; ledger survives independently.
    reboot = StratPaperTrader(
        tmp_path, live, client=FakeMarketClient([]), clock=lambda: now[0])
    assert reboot.status()["enabled"] is False
    assert len(reboot.ledger.recent()) == 1
    reboot.close()


def test_budget_rejects_oversize_or_malformed(tmp_path: Path):
    trader = StratPaperTrader(
        tmp_path, lambda: {}, client=FakeMarketClient([]))
    for amount in ("-1", "0.09", "5.01", "NaN", "1.001"):
        import pytest
        with pytest.raises(ValueError):
            trader.arm(amount)
    trader.close()



def test_down_entry_obeys_identical_time_price_acceleration_rules():
    # These are executable DOWN bids/asks, not inverted BTC spot returns.
    down = quotes(list(map(D, ["0.64", "0.64", "0.65", "0.66", "0.68"])))
    assert evaluate_side_momentum(down, 120, side="DOWN", holding=False).action == "BUY"
    assert evaluate_side_momentum(down, 119.999, side="DOWN", holding=False).action == "WAIT"
    assert evaluate_side_momentum(
        quotes([q.bid - D("0.06") for q in down]), 600, side="DOWN",
        holding=False).action == "WAIT"
    assert evaluate_side_momentum(
        quotes([q.bid + D("0.18") for q in down]), 600, side="DOWN",
        holding=False).action == "WAIT"
    assert evaluate_side_momentum(down, 600, side="SIDEWAYS", holding=False).action == "WAIT"


def test_down_exit_when_down_bid_slows_even_after_buy_cutoff():
    down = quotes(list(map(D, ["0.64", "0.66", "0.69", "0.69", "0.69"])))
    outcome = evaluate_side_momentum(down, 80, side="DOWN", holding=True)
    assert outcome.action == "SELL"
    assert outcome.reason == "DOWN_MOMENTUM_DECELERATED"
    down_reversal = quotes(list(map(D, ["0.69", "0.69", "0.68", "0.67", "0.66"])))
    assert evaluate_side_momentum(
        down_reversal, 60, side="DOWN", holding=True
    ).reason == "DOWN_BID_REVERSAL"


class FakeDownClient:
    def __init__(self, down_quotes):
        self.values = iter(down_quotes)
        self.calls = 0

    def quote(self, ticker):
        self.calls += 1
        bid, ask = next(self.values)
        return {"market": {"ticker": ticker, "status": "active"}}, {
            "orderbook_fp": {
                "yes_dollars": [[str(D("1") - ask), "100"]],
                "no_dollars": [[str(bid), "100"]],
            }
        }

    def close(self):
        pass


def test_bidirectional_trader_paper_buys_and_sells_down(tmp_path):
    values = list(map(D, [
        "0.64", "0.64", "0.65", "0.66", "0.68",
        "0.68", "0.68", "0.68",
    ]))
    client = FakeDownClient([(x, x + D("0.02")) for x in values])
    now = [BASE]
    close = BASE + 600 * NS

    def live():
        return {
            "market_ticker": TICKER, "market_close_ts_ns": close,
            "latest_prediction": {
                "market_ticker": TICKER, "prediction_ts_ns": now[0],
            }
        }

    trader = StratPaperTrader(tmp_path, live, client=client, clock=lambda: now[0])
    assert trader.status()["enabled"] is False
    trader.arm("1.00")
    for i in range(len(values)):
        now[0] = BASE + i * NS
        trader.tick(now[0])
    history = trader.ledger.recent()
    assert len(history) == 1
    assert history[0]["side"] == "DOWN"
    assert history[0]["status"] == "CLOSED"
    assert history[0]["exit_reason"] == "DOWN_MOMENTUM_SLOWED"
    assert client.calls == len(values)
    assert D(history[0]["paper_net_pnl"]) < D("0")
    assert trader.status()["down_ask_cents"] is not None
    assert trader.status()["open_position"] is None
    trader.close()


def test_down_official_settlement_and_single_inventory(tmp_path):
    values = list(map(D, ["0.64", "0.64", "0.65", "0.66", "0.68"]))
    client = FakeDownClient([(x, x + D("0.02")) for x in values])
    now = [BASE]
    close = BASE + 600 * NS

    def live():
        return {
            "market_ticker": TICKER, "market_close_ts_ns": close,
            "latest_prediction": {
                "market_ticker": TICKER, "prediction_ts_ns": now[0],
            }
        }

    trader = StratPaperTrader(
        tmp_path, live, client=client, clock=lambda: now[0],
        settlement_reader=lambda ticker: "no",
    )
    trader.arm("1.00")
    for i in range(len(values)):
        now[0] = BASE + i * NS
        trader.tick(now[0])
    open_pos = trader.ledger.open_position()
    assert open_pos is not None and open_pos["side"] == "DOWN"
    assert len(trader.ledger.recent()) == 1
    now[0] = close + 1
    trader.tick(now[0])
    result = trader.ledger.recent()[0]
    assert result["status"] == "SETTLED"
    assert D(result["paper_net_pnl"]) == D(result["quantity"]) - D(result["total_paid"])
    assert trader.ledger.open_position() is None
    trader.close()



def test_one_inventory_can_switch_from_up_to_down_after_prior_exit(tmp_path):
    """Alternating momentum is allowed, simultaneous UP and DOWN is not."""
    up = list(map(D, [
        "0.64", "0.64", "0.65", "0.66", "0.68",
        "0.68", "0.68", "0.68",
    ]))
    down = list(map(D, [
        "0.64", "0.64", "0.64", "0.64", "0.64",
        "0.64", "0.64", "0.64", "0.64", "0.64",
        "0.64", "0.64", "0.64", "0.65", "0.66", "0.68",
    ]))
    prices = [("UP", value) for value in up] + [
        ("DOWN", value) for value in down
    ]

    class Feed:
        def __init__(self):
            self.records = iter(prices)

        def quote(self, ticker):
            side, bid = next(self.records)
            ask = bid + D("0.02")
            if side == "UP":
                yes_bid, no_bid = bid, D("1") - ask
            else:
                yes_bid, no_bid = D("1") - ask, bid
            return {"market": {"ticker": ticker, "status": "open"}}, {
                "orderbook_fp": {
                    "yes_dollars": [[str(yes_bid), "100"]],
                    "no_dollars": [[str(no_bid), "100"]],
                }
            }

        def close(self):
            pass

    now = [BASE]
    close = BASE + 600 * NS

    def live():
        return {
            "market_ticker": TICKER,
            "market_close_ts_ns": close,
            "latest_prediction": {
                "market_ticker": TICKER, "prediction_ts_ns": now[0],
            }
        }

    trader = StratPaperTrader(tmp_path, live, client=Feed(), clock=lambda: now[0])
    trader.arm("1.00")
    for index in range(len(prices)):
        now[0] = BASE + index * NS
        trader.tick(now[0])
        assert len([r for r in trader.ledger.recent()
                    if r["status"] == "OPEN"]) <= 1
    history = trader.ledger.recent()
    assert len(history) == 2
    assert history[0]["side"] == "DOWN" and history[0]["status"] == "OPEN"
    assert history[1]["side"] == "UP" and history[1]["status"] == "CLOSED"
    assert history[0]["entered_at_ns"] > history[1]["exit_ns"]
    trader.close()


def test_strat_page_shows_both_sides_and_two_minute_buy_gate():
    from kalshi_edge.bootstrap.strat_ui import render_strat_page
    html = render_strat_page()
    assert "UP / DOWN Momentum" in html
    assert "Executable DOWN ask" in html
    assert 'id="downSpeed"' in html
    assert "≥ 2:00" in html
    assert "sec>=120" in html
    assert 'r.side,r.status' in html
    assert "≥ 7:00" not in html



def test_entry_rejects_last_snapshot_stall_after_earlier_burst():
    """An old burst must not be treated as an active new impulse."""
    stalled = quotes(list(map(D, [
        "0.64", "0.64", "0.65", "0.68", "0.68"
    ])))
    for side in ("UP", "DOWN"):
        signal = evaluate_side_momentum(
            stalled, 500, side=side, holding=False
        )
        assert signal.action == "WAIT"
        assert signal.reason == "LATEST_TICK_NOT_RISING"


def test_exit_on_immediate_deceleration_without_2_second_average_lag():
    """The newest bid stall should trigger before the whole window reverses."""
    stall = quotes(list(map(D, [
        "0.64", "0.64", "0.65", "0.68", "0.68"
    ])))
    for side in ("UP", "DOWN"):
        signal = evaluate_side_momentum(stall, 90, side=side, holding=True)
        assert signal.action == "SELL"
        assert signal.reason == f"{side}_LATEST_BID_STALLED"


def test_fee_model_has_no_unsupported_extra_one_cent_per_leg():
    from kalshi_edge.trading.strat_paper import standard_taker_fee_estimate
    from kalshi_edge.trading.risk import taker_fee_reserve
    price, count = D("0.66"), D("1.45")
    cost = price * count
    raw = D("0.07") * price * (D("1") - price) * count
    expected = (cost + raw).quantize(D("0.0001"), rounding="ROUND_CEILING") - cost
    actual = standard_taker_fee_estimate(price, count)
    assert actual == expected
    assert actual < taker_fee_reserve(price, count)
    assert standard_taker_fee_estimate(D("0.70"), D("0")) == D("0")


def test_same_market_three_losses_block_fourth_entry(tmp_path):
    import sqlite3
    now = [BASE]
    close = BASE + 700 * NS
    bids = list(map(D, [
        "0.64", "0.64", "0.65", "0.66", "0.68",
        "0.68", "0.68", "0.68",
    ]))
    # Repeat enough price bursts with cooldown to trigger another entry.
    stream = []
    for _ in range(4):
        stream.extend((bid, bid + D("0.02")) for bid in bids)
        stream.extend((D("0.64"), D("0.66")) for _ in range(8))

    def live():
        return {
            "market_ticker": TICKER, "market_close_ts_ns": close,
            "latest_prediction": {
                "market_ticker": TICKER, "prediction_ts_ns": now[0],
            }
        }

    class Feed:
        def __init__(self):
            self.rows = iter(stream)
        def quote(self, ticker):
            bid, ask = next(self.rows)
            return {"market": {"ticker": ticker, "status": "active"}}, {
                "orderbook_fp": {
                    "yes_dollars": [[str(bid), "100"]],
                    "no_dollars": [[str(D("1") - ask), "100"]],
                }
            }
        def close(self):
            pass

    trader = StratPaperTrader(tmp_path, live, client=Feed(), clock=lambda: now[0])
    trader.arm("1.00")
    for i in range(len(stream)):
        now[0] = BASE + i * NS
        trader.tick(now[0])
    history = trader.ledger.recent()
    assert len(history) == 3
    assert all(r["status"] == "CLOSED" and D(r["paper_net_pnl"]) < 0
               for r in history)
    assert trader.status()["market_loss_streak"] == 3
    assert trader.status()["last_signal"] == "THREE_SAME_MARKET_PAPER_LOSSES"
    trader.close()


def test_strat_dashboard_shows_fee_and_price_components():
    from kalshi_edge.bootstrap.strat_ui import render_strat_page
    page = render_strat_page()
    assert "Fee estimate" in page
    assert "Gross price P&amp;L" in page
    assert "Break-even move" in page



def test_quote_tape_preserves_two_sided_causal_observations(tmp_path):
    from kalshi_edge.trading.strat_tape import StratQuoteTape
    path = tmp_path / "strat_quotes.sqlite3"
    tape = StratQuoteTape(path)
    up, down = (quotes([D("0.68")])[0],
                quotes([D("0.28")], asks=[D("0.30")])[0])
    tape.append(
        observed_ns=BASE, ticker=TICKER, close_ns=BASE + 600*NS,
        remaining_seconds=600.0,
        quotes={"UP": up, "DOWN": down},
        signals={"UP": "NEW_FAST_UP_MOMENTUM", "DOWN": "WAIT"},
        position_side=None,
    )
    tape.append(
        observed_ns=BASE, ticker=TICKER, close_ns=BASE + 600*NS,
        remaining_seconds=600.0,
        quotes={"UP": up, "DOWN": down},
        signals={"UP": "CHANGED", "DOWN": "CHANGED"},
        position_side=None,
    )
    assert tape.summary()["snapshots"] == 1
    assert tape.summary()["markets"] == 1
    tape_reopened = StratQuoteTape(path)
    rows = tape_reopened.recent(3)
    assert len(rows) == 1
    row = rows[0]
    assert row["up_bid"] == "0.68"
    assert row["up_ask"] == "0.70"
    assert row["down_bid"] == "0.28"
    assert row["down_ask"] == "0.30"
    assert row["up_signal"] == "NEW_FAST_UP_MOMENTUM"
    assert row["remaining_seconds"] == 600.0


def test_global_consecutive_loss_brake_persists_across_tickers(tmp_path):
    from kalshi_edge.trading.strat_tape import StratQuoteTape
    now = [BASE]
    ticker = "KXBTC15M-NEWMARKET"
    end = BASE + 650 * NS

    def live():
        return {
            "market_ticker": ticker, "market_close_ts_ns": end,
            "latest_prediction": {
                "market_ticker": ticker, "prediction_ts_ns": now[0],
            }
        }

    bids = list(map(D, ["0.64", "0.64", "0.65", "0.66", "0.68"]))
    trader = StratPaperTrader(
        tmp_path, live, client=FakeMarketClient(
            [(bid, bid+D("0.02")) for bid in bids]),
        clock=lambda: now[0],
    )
    for n in range(3):
        opened_at = BASE - (30-n)*NS
        trader.ledger.insert(
            ticker=f"KXBTC15M-PREV{n}", side="UP", quantity=D("1"),
            price=D("0.70"), fee=D("0.02"), paid=D("0.72"),
            now_ns=opened_at, close_ns=BASE + 600*NS,
        )
        row = trader.ledger.open_position()
        trader.ledger.close(
            row["id"], price=D("0.65"), fee=D("0.02"),
            now_ns=opened_at+NS, reason="UP_MOMENTUM_SLOWED",
            pnl=D("-0.09"),
        )
    assert trader.status()["global_loss_streak"] == 3
    trader.arm("1.00")
    for i in range(5):
        now[0] = BASE + i*NS
        trader.tick(now[0])
    assert trader.ledger.open_position() is None
    assert len(trader.ledger.recent()) == 3
    assert trader.status()["last_signal"] == "GLOBAL_LOSS_CIRCUIT_ACTIVE"
    assert trader.status()["quote_tape_samples"] == 5
    tape = StratQuoteTape(tmp_path/"trading"/"strat_quote_tape.sqlite3")
    assert tape.summary()["markets"] == 1
    assert tape.recent(1)[0]["up_signal"] == "NEW_FAST_UP_MOMENTUM"
    # A new UTC session starts a fresh global entry-risk streak.
    assert trader._global_loss_streak(BASE + 24*3600*NS) == 0
    trader.close()


def test_no_entry_on_quote_recorder_failure(tmp_path):
    now = [BASE]
    close = BASE+600*NS

    def live():
        return {
            "market_ticker": TICKER, "market_close_ts_ns": close,
            "latest_prediction": {
                "market_ticker": TICKER, "prediction_ts_ns": now[0],
            },
        }

    trader = StratPaperTrader(
        tmp_path, live, client=FakeMarketClient(
            [(bid, bid+D("0.02")) for bid in map(D, [
                "0.64", "0.64", "0.65", "0.66", "0.68"
            ])]),
        clock=lambda: now[0],
    )
    trader.arm("1.00")
    def fail(*args, **kwargs):
        raise OSError("Disk full")
    trader.tape.append = fail
    for i in range(5):
        now[0] = BASE+i*NS
        trader.tick(now[0])
    assert trader.ledger.open_position() is None
    assert trader.status()["last_signal"] == "QUOTE_TAPE_WRITE_FAILED"
    trader.close()


def test_quote_tape_and_global_circuit_are_visible_in_dashboard():
    from kalshi_edge.bootstrap.strat_ui import render_strat_page
    html = render_strat_page()
    assert "Captured book snapshots" in html
    assert "Cross-market loss streak" in html
    assert "quote_tape_samples" in html
    assert "global_loss_streak" in html
