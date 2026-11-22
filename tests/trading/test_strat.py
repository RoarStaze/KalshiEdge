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
