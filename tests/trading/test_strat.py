"""Regression tests for the isolated, paper-only KXBTC15M UP Strat."""
from __future__ import annotations

from decimal import Decimal as D
from pathlib import Path

from kalshi_edge.trading.strat_policy import (
    UpQuote, evaluate_up_momentum,
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


def test_entry_requires_7_minutes_and_65_85_and_new_acceleration():
    series = quotes(list(map(D, ["0.64", "0.64", "0.65", "0.66", "0.68"])))
    assert evaluate_up_momentum(series, 420, holding=False).action == "BUY"
    assert evaluate_up_momentum(series, 419.999, holding=False).action == "WAIT"
    assert evaluate_up_momentum(series, 901, holding=False).action == "WAIT"
    assert evaluate_up_momentum(
        quotes([q.bid - D("0.06") for q in series]), 420, holding=False
    ).reason == "UP_ASK_OUTSIDE_65_85"
    assert evaluate_up_momentum(
        quotes([q.bid + D("0.18") for q in series]), 420, holding=False
    ).reason == "UP_ASK_OUTSIDE_65_85"
    assert evaluate_up_momentum(
        quotes(list(map(D, ["0.64", "0.65", "0.66", "0.67", "0.68"]))),
        420, holding=False,
    ).action == "WAIT"


def test_exit_remains_enabled_after_seven_minute_cutoff():
    series = quotes(list(map(D, ["0.64", "0.66", "0.69", "0.69", "0.69"])))
    signal = evaluate_up_momentum(series, 200, holding=True)
    assert signal.action == "SELL"
    assert signal.reason == "UP_MOMENTUM_DECELERATED"
    assert evaluate_up_momentum(
        quotes(list(map(D, ["0.69", "0.69", "0.68", "0.67", "0.66"]))),
        100, holding=True,
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
