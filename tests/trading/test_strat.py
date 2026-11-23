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
    series = quotes(list(map(D, ["0.64", "0.69", "0.69", "0.69", "0.69"])))
    signal = evaluate_up_momentum(series, 80, holding=True)
    assert signal.action == "SELL"
    assert signal.reason == "UP_MOMENTUM_FLAT_CONFIRMED"
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


def test_paper_cycle_sells_only_at_positive_net_slowdown_and_durable_ledger(tmp_path: Path):
    bids = list(map(D, [
        "0.64", "0.64", "0.65", "0.66", "0.68",
        "0.78", "0.78", "0.78", "0.78",
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
    assert history[0]["exit_reason"] == "UP_MOMENTUM_SLOWED_PROFIT"
    assert D(history[0]["paper_net_pnl"]) > 0  # real after-cost paper profit
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
    down = quotes(list(map(D, ["0.64", "0.69", "0.69", "0.69", "0.69"])))
    outcome = evaluate_side_momentum(down, 80, side="DOWN", holding=True)
    assert outcome.action == "SELL"
    assert outcome.reason == "DOWN_MOMENTUM_FLAT_CONFIRMED"
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


def test_bidirectional_trader_paper_takes_positive_net_down_profit(tmp_path):
    values = list(map(D, [
        "0.64", "0.64", "0.65", "0.66", "0.68",
        "0.78", "0.78", "0.78", "0.78",
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
    assert history[0]["exit_reason"] == "DOWN_MOMENTUM_SLOWED_PROFIT"
    assert client.calls == len(values)
    assert D(history[0]["paper_net_pnl"]) > D("0")
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
        "0.78", "0.78", "0.78", "0.78",
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


def test_a_single_flat_quote_is_not_a_confirmed_momentum_exit():
    """One unchanged REST response is not evidence that a burst ended."""
    stall = quotes(list(map(D, [
        "0.64", "0.64", "0.65", "0.68", "0.68"
    ])))
    for side in ("UP", "DOWN"):
        signal = evaluate_side_momentum(stall, 90, side=side, holding=True)
        assert signal.action == "HOLD"
        assert signal.reason == f"{side}_FLAT_QUOTE_UNCONFIRMED"


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


def test_same_market_four_positive_profit_gated_cycles_remain_allowed(tmp_path):
    import sqlite3
    now = [BASE]
    close = BASE + 700 * NS
    bids = list(map(D, [
        "0.64", "0.64", "0.65", "0.66", "0.68",
        "0.78", "0.78", "0.78", "0.78",
    ]))
    # Repeat profitable price bursts with cooldown to trigger another entry.
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
    assert len(history) == 4
    assert all(r["status"] == "CLOSED" and D(r["paper_net_pnl"]) > 0
               for r in history)
    assert trader.status()["market_loss_streak"] == 0
    assert trader.status()["entry_block_reason"] is None
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


def test_global_loss_streak_does_not_block_new_paper_market(tmp_path):
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
    position = trader.ledger.open_position()
    assert position is not None and position["ticker"] == ticker
    assert position["side"] == "UP"
    assert len(trader.ledger.recent()) == 4
    assert trader.status()["global_loss_streak"] == 3  # retained as audit only
    assert trader.status()["entry_block_reason"] == "POSITION_OPEN"
    assert "global_loss_circuit_active" not in trader.status()
    assert trader.status()["quote_tape_samples"] == 5
    tape = StratQuoteTape(tmp_path/"trading"/"strat_quote_tape.sqlite3")
    assert tape.summary()["markets"] == 1
    assert tape.recent(1)[0]["up_signal"] == "NEW_FAST_UP_MOMENTUM"
    # A new UTC session starts a fresh informational loss streak.
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


def test_quote_tape_and_informational_loss_streak_are_visible_in_dashboard():
    from kalshi_edge.bootstrap.strat_ui import render_strat_page
    html = render_strat_page()
    assert "Captured book snapshots" in html
    assert "Informational daily loss streak" in html
    assert "quote_tape_samples" in html
    assert "global_loss_streak" in html



def test_market_close_gate_uses_post_response_quote_receipt_time(tmp_path):
    """Network request time must not turn 1:59 into a legal 2:00 entry."""
    start = [BASE]
    close = BASE + 124*NS

    def live():
        return {
            "market_ticker": TICKER, "market_close_ts_ns": close,
            "latest_prediction": {
                "market_ticker": TICKER, "prediction_ts_ns": start[0],
            },
        }

    class SlowClient(FakeMarketClient):
        def quote(self, ticker):
            market, book = super().quote(ticker)
            start[0] += NS  # REST response took one second
            return market, book

    prices = [
        (bid, bid+D("0.02")) for bid in map(D, [
            "0.64", "0.64", "0.65", "0.66", "0.68"
        ])
    ]
    trader = StratPaperTrader(
        tmp_path, live, client=SlowClient(prices), clock=lambda: start[0],
    )
    trader.arm("1.00")
    for i in range(5):
        start[0] = BASE+i*NS
        trader.tick(BASE+i*NS)
    assert trader.ledger.open_position() is None
    assert trader.ledger.recent() == []
    captured = trader.tape.recent(1)[0]
    assert captured["observed_ns"] == BASE+5*NS
    assert captured["remaining_seconds"] == 119.0
    assert captured["up_signal"] == "OUTSIDE_2_MINUTE_BUY_WINDOW"
    trader.close()



def test_daily_paper_loss_is_informational_and_does_not_block_new_entries(tmp_path):
    """Paper research should retain the $2 loss statistic without halting."""
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

    sequence = [D(x) for x in ("0.64", "0.64", "0.65", "0.66", "0.68")]
    trader = StratPaperTrader(
        tmp_path, live, client=FakeMarketClient(
            [(bid, bid + D("0.02")) for bid in sequence]),
        clock=lambda: now[0],
    )
    for n in range(3):
        stamp = BASE-(30-n)*NS
        trader.ledger.insert(
            ticker=f"KXBTC15M-PREV{n}", side="UP", quantity=D("1"),
            price=D("0.70"), fee=D("0.02"), paid=D("0.72"),
            now_ns=stamp, close_ns=close,
        )
        position = trader.ledger.open_position()
        trader.ledger.close(
            position["id"], price=D("0"), fee=D("0"),
            now_ns=stamp+NS, reason="OFFICIAL_SETTLEMENT",
            pnl=D("-0.72"), settled=True,
        )
    trader.arm("1.00")
    for i in range(5):
        now[0] = BASE + i * NS
        trader.tick(now[0])
    assert trader.ledger.open_position() is not None
    assert D(trader.status()["daily_paper_loss_dollars"]) >= D("2")
    assert trader.status()["entry_block_reason"] == "POSITION_OPEN"
    assert trader.status()["quote_tape_samples"] == 5
    trader.close()


def test_dashboard_shows_entry_eligibility_separate_from_momentum():
    from kalshi_edge.bootstrap.strat_ui import render_strat_page
    page = render_strat_page()
    assert 'id="entryBlock"' in page
    assert 's.entry_block_reason' in page
    assert "Informational daily loss streak" in page
    assert "GLOBAL_LOSS_CIRCUIT_ACTIVE" not in page
    assert "s.global_loss_circuit_active" not in page



def test_three_consecutive_flat_updates_confirm_slowdown_both_sides():
    for side in ("UP", "DOWN"):
        # Exactly three unchanged bid observations after a clear rise.
        series = quotes(list(map(D, [
            "0.64", "0.68", "0.68", "0.68", "0.68"
        ])))
        signal = evaluate_side_momentum(
            series, 90, side=side, holding=True
        )
        assert signal.action == "SELL"
        assert signal.reason == f"{side}_MOMENTUM_FLAT_CONFIRMED"


def test_single_flat_rest_poll_and_longer_negative_slowdown_hold(tmp_path):
    now = [BASE]
    close = BASE + 600*NS
    bids = list(map(D, [
        "0.64", "0.64", "0.65", "0.66", "0.68",
        "0.68",  # first flat sample: hold, not an automatic 3c loss
        "0.68", "0.68",  # sustained flat, exit after confirmation
    ]))

    def live():
        return {
            "market_ticker": TICKER, "market_close_ts_ns": close,
            "latest_prediction": {
                "market_ticker": TICKER, "prediction_ts_ns": now[0],
            }
        }

    trader = StratPaperTrader(
        tmp_path, live,
        client=FakeMarketClient([(bid, bid+D("0.02")) for bid in bids]),
        clock=lambda: now[0],
    )
    trader.arm("1.00")
    for i in range(6):
        now[0] = BASE + i*NS
        trader.tick(now[0])
    opening = trader.ledger.open_position()
    assert opening is not None and opening["status"] == "OPEN"
    assert trader.status()["last_signal"] == "UP_FLAT_QUOTE_UNCONFIRMED"
    for i in range(6, len(bids)):
        now[0] = BASE + i*NS
        trader.tick(now[0])
    assert trader.ledger.open_position() is not None
    history = trader.ledger.recent()
    assert history[0]["status"] == "OPEN"
    assert trader.status()["last_signal"] == "MOMENTUM_SLOWED_BUT_NOT_PROFITABLE"
    assert D(trader.status()["projected_exit_net_pnl_dollars"]) < 0
    trader.close()


def test_quote_replay_is_cost_aware_and_respects_market_time(tmp_path):
    from kalshi_edge.trading.strat_tape import StratQuoteTape
    from kalshi_edge.trading.strat_replay import analyze_causal_markouts
    tape = StratQuoteTape(tmp_path / "quotes.sqlite3")
    ticker = TICKER
    close = BASE+600*NS
    for idx, bid in enumerate(map(D, [
        "0.68", "0.68", "0.68", "0.68", "0.68",
        "0.69", "0.70", "0.73", "0.74", "0.75", "0.76",
    ])):
        t = BASE+idx*NS
        up = UpQuote(ticker, t, bid, bid+D("0.02"), D("100"), D("100"))
        down = UpQuote(ticker, t, D("0.25"), D("0.27"), D("100"), D("100"))
        tape.append(
            observed_ns=t, ticker=ticker, close_ns=close,
            remaining_seconds=(close-t)/NS,
            quotes={"UP":up,"DOWN":down},
            signals={
                "UP":"NEW_FAST_UP_MOMENTUM" if idx==0 else "NO_NEW_ACCELERATION",
                "DOWN":"WAIT"
            },position_side=None,
        )
    # 5s and 10s marks are executable hypothetical exits, not mid-price.
    summary = analyze_causal_markouts(tape.path, horizons=(5,10))
    assert summary["candidate_count"] == 1
    five = summary["horizons"]["5s"]
    ten = summary["horizons"]["10s"]
    assert five["observed"] == 1 and ten["observed"] == 1
    assert five["net_profitable"] == 0
    assert ten["net_profitable"] == 1
    assert five["mean_net_cents_per_contract"] < 0
    assert ten["mean_net_cents_per_contract"] > 0
    assert summary["interpretation"] == "DESCRIPTIVE_ONLY_NO_VERIFIED_EDGE"

    # A second tape with no follow-up cannot use the next ticker's quotes.
    sparse = StratQuoteTape(tmp_path / "sparse.sqlite3")
    t = BASE
    q_up = UpQuote(TICKER, t, D("0.68"), D("0.70"), D("100"), D("100"))
    q_down = UpQuote(TICKER, t, D("0.28"), D("0.30"), D("100"), D("100"))
    sparse.append(observed_ns=t, ticker=TICKER, close_ns=close,
                  remaining_seconds=600.0, quotes={"UP":q_up,"DOWN":q_down},
                  signals={"UP":"NEW_FAST_UP_MOMENTUM","DOWN":"WAIT"},
                  position_side=None)
    no_future = analyze_causal_markouts(sparse.path, horizons=(5,))
    assert no_future["candidate_count"] == 1
    assert no_future["horizons"]["5s"]["observed"] == 0
    assert no_future["horizons"]["5s"]["missing_followup"] == 1


def test_strat_dashboard_can_show_on_demand_causal_markout_audit():
    from kalshi_edge.bootstrap.strat_ui import render_strat_page
    page = render_strat_page()
    assert "Analyze captured momentum" in page
    assert "/api/strat/replay" in page
    assert "NO VERIFIED EDGE" in page
    assert 'id="replayResult"' in page



def test_entry_restriction_reports_clock_then_price_not_old_loss_streak(tmp_path):
    now = [BASE]
    close = BASE + 600 * NS
    def live():
        return {
            "market_ticker": TICKER, "market_close_ts_ns": close,
            "latest_prediction": {
                "market_ticker": TICKER, "prediction_ts_ns": now[0]
            },
        }
    high = [(D("0.96"), D("0.98"))] * 5
    trader = StratPaperTrader(
        tmp_path, live, client=FakeMarketClient(high),
        clock=lambda: now[0],
    )
    for n in range(3):
        stamp = BASE - (30-n)*NS
        trader.ledger.insert(
            ticker=TICKER, side="UP", quantity=D("1"), price=D("0.70"),
            fee=D("0.02"), paid=D("0.72"), now_ns=stamp, close_ns=close,
        )
        prev = trader.ledger.open_position()
        trader.ledger.close(
            prev["id"], price=D("0.65"), fee=D("0.02"),
            now_ns=stamp + NS, reason="UP_MOMENTUM_SLOWED",
            pnl=D("-0.09"),
        )
    trader.arm("1.00")
    for i in range(5):
        now[0] = BASE+i*NS
        trader.tick(now[0])
    assert trader.status()["market_loss_streak"] == 3
    assert trader.status()["entry_block_reason"] == "BOTH_SIDE_ASKS_OUTSIDE_65_85"
    close = BASE + 60*NS
    assert trader.status()["entry_block_reason"] == "OUTSIDE_2_MINUTE_BUY_WINDOW"
    trader.close()


def test_dashboard_no_longer_describes_paper_loss_circuits_as_entry_stops():
    from kalshi_edge.bootstrap.strat_ui import render_strat_page
    page = render_strat_page()
    assert "Loss streak this market" in page
    assert "Informational daily loss" in page
    assert "No paper-loss circuit" in page
    assert "THREE_SAME_MARKET_PAPER_LOSSES" not in page
    assert "DAILY_PAPER_LOSS_CIRCUIT" not in page



def test_prior_completed_markets_negative_edge_blocks_only_losing_side(tmp_path):
    """A sufficient past CLOSED-market sample blocks one side, never uses current."""
    from kalshi_edge.trading.strat_tape import StratQuoteTape
    from kalshi_edge.trading.strat_evidence import evaluate_historical_entry_evidence
    tape = StratQuoteTape(tmp_path / "quotes.sqlite3")
    for market_index in range(10):
        ticker = f"KXBTC15M-OLD{market_index}"
        start = BASE + market_index * 900 * NS
        close = start + 600 * NS
        for event_index in range(6):
            t = start + (10 + event_index * 35) * NS
            for when, bid, marker in [
                (t, D("0.68"), "NEW_FAST_UP_MOMENTUM"),
                (t + 20 * NS, D("0.68"), "NO_NEW_ACCELERATION"),
            ]:
                up = UpQuote(ticker, when, bid, bid + D("0.02"),
                             D("100"), D("100"))
                down = UpQuote(ticker, when, D("0.28"), D("0.30"),
                               D("100"), D("100"))
                tape.append(
                    ticker=ticker, observed_ns=when, close_ns=close,
                    remaining_seconds=(close - when) / NS,
                    quotes={"UP": up, "DOWN": down},
                    signals={"UP": marker, "DOWN": "WAIT"},
                    position_side=None,
                )
    audit_time = BASE + 10 * 900 * NS
    result = evaluate_historical_entry_evidence(tape.path, audit_time)
    up, down = result["sides"]["UP"], result["sides"]["DOWN"]
    assert up["status"] == "NEGATIVE_HISTORICAL_TAKER_MARKOUT"
    assert up["entry_veto"] is True
    assert up["observed"] == 60
    assert up["market_count"] == 10
    assert up["mean_net_cents_per_contract"] < 0
    assert down["status"] == "INSUFFICIENT_HISTORY"
    assert down["entry_veto"] is False
    # Prior-to-close knowledge cannot veto a current/unfinished market.
    premature = evaluate_historical_entry_evidence(
        tape.path, BASE + 9 * 900 * NS + 300 * NS,
    )
    assert premature["sides"]["UP"]["market_count"] == 9
    assert result["interpretation"] == "DESCRIPTIVE_ONLY_NO_VERIFIED_EDGE"


def test_own_paper_trade_signal_is_vetoed_after_negative_prior_market_evidence(tmp_path):
    from kalshi_edge.trading.strat_tape import StratQuoteTape
    ticker = "KXBTC15M-NEWLIVE"
    previous = StratQuoteTape(tmp_path / "trading" / "strat_quote_tape.sqlite3")
    for i in range(10):
        old = f"KXBTC15M-OLD{i}"
        start = BASE + i * 900 * NS
        close = start + 600 * NS
        for j in range(6):
            t = start + (20 + j*35)*NS
            for when, sig in [(t, "NEW_FAST_UP_MOMENTUM"),
                              (t+20*NS, "WAIT")]:
                up = UpQuote(old, when, D("0.68"), D("0.70"), D("100"), D("100"))
                down = UpQuote(old, when, D("0.28"), D("0.30"),
                               D("100"), D("100"))
                previous.append(
                    ticker=old, observed_ns=when, close_ns=close,
                    remaining_seconds=(close-when)/NS,
                    quotes={"UP":up,"DOWN":down},
                    signals={"UP":sig,"DOWN":"WAIT"}, position_side=None,
                )
    now = [BASE + 10*900*NS]
    close = now[0] + 600*NS
    def live():
        return {
            "market_ticker": ticker, "market_close_ts_ns": close,
            "latest_prediction": {
                "market_ticker": ticker, "prediction_ts_ns": now[0]
            }
        }
    client = FakeMarketClient([
        (bid, bid+D("0.02")) for bid in map(D, [
            "0.64","0.64","0.65","0.66","0.68"
        ])
    ])
    trader = StratPaperTrader(
        tmp_path, live, client=client, clock=lambda: now[0],
    )
    trader.arm("1.00")
    for i in range(5):
        now[0] = BASE + 10*900*NS + i*NS
        trader.tick(now[0])
    assert trader.ledger.open_position() is None
    assert trader.status()["last_signal"] == "NEGATIVE_HISTORICAL_UP_TAKER_MARKOUT"
    assert trader.status()["entry_evidence"]["sides"]["UP"]["entry_veto"]
    assert trader.status()["quote_tape_samples"] == 125
    assert trader.status()["enabled"] is True
    assert trader.status()["paper_exploration_override"] is False
    assert trader.status()["entry_block_reason"] == "NEGATIVE_HISTORICAL_UP_TAKER_MARKOUT"
    trader.close()

    # Explicit, PAPER-ONLY exploration override: underlying evidence remains
    # negative and visible, but no longer vetoes the same entry.
    now[0] = BASE + 10*900*NS
    experimental = StratPaperTrader(
        tmp_path, live,
        client=FakeMarketClient([
            (bid, bid+D("0.02")) for bid in map(D, [
                "0.64", "0.64", "0.65", "0.66", "0.68"
            ])
        ]),
        clock=lambda: now[0],
    )
    assert experimental.status()["paper_exploration_override"] is False
    experimental.arm("1.00", exploratory_ignore_negative_evidence=True)
    assert experimental.status()["paper_exploration_override"] is True
    assert experimental.status()["live_orders_enabled"] is False
    for i in range(5):
        now[0] = BASE + 10*900*NS + i*NS
        experimental.tick(now[0])
    position = experimental.ledger.open_position()
    assert position is not None and position["side"] == "UP"
    assert experimental.status()["last_signal"] == "PAPER_BOUGHT_UP_FAST_MOMENTUM"
    assert experimental.status()["entry_evidence"]["sides"]["UP"]["entry_veto"] is True
    experimental.pause()
    assert experimental.status()["paper_exploration_override"] is False
    experimental.close()

    restart = StratPaperTrader(
        tmp_path, live, client=FakeMarketClient([]), clock=lambda: now[0],
    )
    assert restart.status()["paper_exploration_override"] is False
    assert restart.status()["enabled"] is False
    assert restart.ledger.open_position() is not None
    restart.close()


def test_paper_exploration_override_requires_actual_bool(tmp_path):
    import pytest
    trader = StratPaperTrader(tmp_path, lambda: {}, client=FakeMarketClient([]))
    for invalid in ("yes", "true", 1, None, [], {}):
        with pytest.raises(ValueError):
            trader.arm("1.00", exploratory_ignore_negative_evidence=invalid)
    assert trader.status()["paper_exploration_override"] is False
    trader.close()


def test_paper_exploration_keeps_original_time_and_price_rules(tmp_path):
    now = [BASE]
    close = BASE + 600*NS
    def live():
        return {
            "market_ticker": TICKER, "market_close_ts_ns": close,
            "latest_prediction": {
                "market_ticker": TICKER, "prediction_ts_ns": now[0]
            }
        }
    # Same prices as user's no-trade screenshot: neither ask qualifies.
    trader = StratPaperTrader(
        tmp_path, live,
        client=FakeMarketClient([(D("0.54"), D("0.56"))] * 5),
        clock=lambda: now[0],
    )
    trader.arm("1.00", exploratory_ignore_negative_evidence=True)
    for i in range(5):
        now[0] = BASE+i*NS
        trader.tick(now[0])
    assert trader.ledger.open_position() is None
    assert trader.status()["entry_block_reason"] == "BOTH_SIDE_ASKS_OUTSIDE_65_85"
    close = BASE + 60*NS
    assert trader.status()["entry_block_reason"] == "OUTSIDE_2_MINUTE_BUY_WINDOW"
    trader.close()


def test_exploratory_checkbox_and_active_state_are_explained():
    from kalshi_edge.bootstrap.strat_ui import render_strat_page
    html = render_strat_page()
    assert 'id="paperExplore"' in html
    assert 'exploratory_ignore_negative_evidence' in html
    assert "Paper exploration" in html
    assert "Historical taker evidence" in html
    assert "No verified profit edge" in html


def test_maker_entry_scenario_is_labelled_optimistic_and_unverified(tmp_path):
    from kalshi_edge.trading.strat_tape import StratQuoteTape
    from kalshi_edge.trading.strat_replay import analyze_causal_markouts
    tape = StratQuoteTape(tmp_path / "tape.sqlite3")
    ticker = TICKER
    close = BASE + 600*NS
    for seconds, bid, signal in [
        (0, D("0.68"), "NEW_FAST_UP_MOMENTUM"),
        (20, D("0.74"), "WAIT"),
    ]:
        when = BASE+seconds*NS
        tape.append(
            ticker=ticker, observed_ns=when, close_ns=close,
            remaining_seconds=(close-when)/NS,
            quotes={
                "UP": UpQuote(ticker,when,bid,bid+D("0.02"),D("100"),D("100")),
                "DOWN": UpQuote(ticker,when,D("0.25"),D("0.27"),
                                D("100"),D("100")),
            },
            signals={"UP":signal,"DOWN":"WAIT"},position_side=None,
        )
    report = analyze_causal_markouts(tape.path, horizons=(20,))
    comparison = report["execution_scenarios"]["20s"]
    assert comparison["optimistic_maker_entry_assumed_fills"] == 1
    assert comparison["optimistic_maker_entry_mean_net_cents"] > 0
    assert comparison["optimistic_maker_entry_is_verified"] is False
    assert "NOT VERIFIED" in comparison["maker_label"]
    assert report["horizons"]["20s"]["mean_net_cents_per_contract"] < 0


def test_dashboard_exposes_evidence_gate_and_unverified_maker_comparison():
    from kalshi_edge.bootstrap.strat_ui import render_strat_page
    html = render_strat_page()
    assert 'id="evidenceGate"' in html
    assert "Negative historical taker markout" in html
    assert "Maker entry (unverified)" in html
    assert "entry_evidence" in html



def test_slowdown_exit_requires_positive_net_after_fees_and_slippage(tmp_path):
    now = [BASE]
    close = BASE + 600*NS
    # Entry after bid 0.68, then flat at 0.72: still negative after full
    # round trip. At 0.78, a new confirmed slowdown can exit profitably.
    values = list(map(D, [
        "0.64", "0.64", "0.65", "0.66", "0.68",
        "0.72", "0.72", "0.72", "0.72",
        "0.76", "0.78", "0.78", "0.78", "0.78",
    ]))
    def live():
        return {
            "market_ticker": TICKER, "market_close_ts_ns": close,
            "latest_prediction": {
                "market_ticker": TICKER, "prediction_ts_ns": now[0]
            }
        }
    trader = StratPaperTrader(
        tmp_path, live,
        client=FakeMarketClient([(x, x+D("0.02")) for x in values]),
        clock=lambda: now[0],
    )
    trader.arm("1.00")
    for i in range(9):
        now[0] = BASE+i*NS
        trader.tick(now[0])
    assert trader.ledger.open_position() is not None
    assert trader.status()["last_signal"] == "MOMENTUM_SLOWED_BUT_NOT_PROFITABLE"
    assert D(trader.status()["projected_exit_net_pnl_dollars"]) < 0
    for i in range(9, len(values)):
        now[0] = BASE+i*NS
        trader.tick(now[0])
    closed = trader.ledger.recent()[0]
    assert closed["status"] == "CLOSED"
    assert closed["side"] == "UP"
    assert closed["exit_reason"] == "UP_MOMENTUM_SLOWED_PROFIT"
    assert D(closed["paper_net_pnl"]) > 0
    assert trader.status()["open_position"] is None
    trader.close()


def test_unprofitable_up_slowdown_stays_open_and_can_lose_at_verified_settlement(tmp_path):
    now = [BASE]
    close = BASE + 600*NS
    values = list(map(D, [
        "0.64", "0.64", "0.65", "0.66", "0.68",
        "0.68", "0.68", "0.68",
    ]))
    def live():
        return {
            "market_ticker": TICKER, "market_close_ts_ns": close,
            "latest_prediction": {
                "market_ticker": TICKER, "prediction_ts_ns": now[0]
            }
        }
    trader = StratPaperTrader(
        tmp_path, live,
        client=FakeMarketClient([(x, x+D("0.02")) for x in values]),
        clock=lambda: now[0],
        settlement_reader=lambda ticker: "no",
    )
    trader.arm("1.00")
    for i in range(len(values)):
        now[0] = BASE+i*NS
        trader.tick(now[0])
    assert trader.ledger.open_position() is not None
    assert trader.status()["last_signal"] == "MOMENTUM_SLOWED_BUT_NOT_PROFITABLE"
    assert trader.status()["projected_exit_net_pnl_dollars"].startswith("-")
    now[0] = close+NS
    trader.tick(now[0])
    row = trader.ledger.recent()[0]
    assert row["status"] == "SETTLED"
    assert row["exit_reason"] == "OFFICIAL_SETTLEMENT"
    assert D(row["paper_net_pnl"]) == -D(row["total_paid"])
    assert trader.ledger.open_position() is None
    trader.close()


def test_down_slowdown_never_closes_at_loss(tmp_path):
    now = [BASE]
    close = BASE+600*NS
    values = list(map(D, [
        "0.64", "0.64", "0.65", "0.66", "0.68",
        "0.68", "0.68", "0.68",
    ]))
    def live():
        return {
            "market_ticker": TICKER, "market_close_ts_ns": close,
            "latest_prediction": {
                "market_ticker": TICKER, "prediction_ts_ns": now[0]
            }
        }
    client = FakeDownClient([(x,x+D("0.02")) for x in values])
    trader = StratPaperTrader(tmp_path, live, client=client, clock=lambda: now[0])
    trader.arm("1.00")
    for i in range(len(values)):
        now[0] = BASE+i*NS
        trader.tick(now[0])
    assert trader.ledger.open_position() is not None
    assert trader.status()["last_signal"] == "MOMENTUM_SLOWED_BUT_NOT_PROFITABLE"
    assert D(trader.status()["projected_exit_net_pnl_dollars"]) < 0
    trader.close()


def test_profitable_held_price_without_slowdown_does_not_trigger_sale(tmp_path):
    now = [BASE]
    close = BASE+600*NS
    # Profitable bid appears while still increasing; sell only after slowdown.
    values = list(map(D, [
        "0.64", "0.64", "0.65", "0.66", "0.68",
        "0.75", "0.82", "0.89",  # uniform 7c/tick rise; no slowdown
    ]))
    def live():
        return {
            "market_ticker": TICKER, "market_close_ts_ns": close,
            "latest_prediction": {
                "market_ticker": TICKER, "prediction_ts_ns": now[0]
            }
        }
    trader = StratPaperTrader(
        tmp_path, live, client=FakeMarketClient([
            (x,x+D("0.02")) for x in values
        ]), clock=lambda: now[0],
    )
    trader.arm("1.00")
    for i in range(len(values)):
        now[0] = BASE+i*NS
        trader.tick(now[0])
    assert trader.ledger.open_position() is not None
    assert D(trader.status()["projected_exit_net_pnl_dollars"]) > 0
    assert trader.status()["last_signal"] in ("UP_MOMENTUM_ACTIVE", "UP_LATEST_RISE_SLOWED")
    # Still no SELL because profitable price alone isn't slowdown.
    assert trader.ledger.recent()[0]["status"] == "OPEN"
    trader.close()


def test_profit_only_slowdown_rule_visible_on_strat_page():
    from kalshi_edge.bootstrap.strat_ui import render_strat_page
    page = render_strat_page()
    assert "Positive net P&amp;L" in page
    assert 'id="projectedExitPnl"' in page
    assert "profit only" in page.lower()
    assert "settlement" in page.lower()



def test_projected_profit_does_not_survive_missing_or_expired_quotes(tmp_path):
    now = [BASE]
    close = BASE + 600*NS
    values = list(map(D, [
        "0.64", "0.64", "0.65", "0.66", "0.68", "0.80",
    ]))
    def live():
        return {
            "market_ticker": TICKER, "market_close_ts_ns": close,
            "latest_prediction": {
                "market_ticker": TICKER, "prediction_ts_ns": now[0],
            }
        }
    trader = StratPaperTrader(
        tmp_path, live,
        client=FakeMarketClient([(x,x+D("0.02")) for x in values]),
        clock=lambda: now[0],
        settlement_reader=lambda _: None,
    )
    trader.arm("1.00")
    for i in range(len(values)):
        now[0] = BASE+i*NS
        trader.tick(now[0])
    assert D(trader.status()["projected_exit_net_pnl_dollars"]) > 0
    assert trader.ledger.open_position() is not None
    now[0] = BASE+len(values)*NS
    trader.tick(now[0])  # exhausted live quote feed
    assert trader.status()["projected_exit_net_pnl_dollars"] is None
    now[0] = close + NS
    trader.tick(now[0])  # no official result; do not infer a settlement
    assert trader.status()["projected_exit_net_pnl_dollars"] is None
    assert trader.status()["last_signal"] == "WAITING_FOR_OFFICIAL_SETTLEMENT"
    assert trader.ledger.open_position() is not None
    trader.close()
