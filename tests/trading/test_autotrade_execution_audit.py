"""Risk-neutral decision-time quote evidence for real AutoTrade stage journal.

All exchanges are in-memory fakes; no test submits a real order.
"""
from __future__ import annotations

import json
import sqlite3
from decimal import Decimal

import pytest

from kalshi_edge.trading.autotrade_execution_audit import (
    sanitize_quote_snapshot, read_execution_audit,
)
from kalshi_edge.trading.autotrade_history import AutoTradeHistory
from kalshi_edge.trading.armed_autotrade import ArmedAutoTrader


NS = 1_000_000_000
TICKER = "KXBTC15M-EXECAUDIT"


def quote():
    return {
        "ticker": TICKER, "action": "BUY", "outcome": "UP",
        "preview_id": "sensitive-preview-token-never-store",
        "api_key": "SECRET-NEVER-STORE",
        "unit_price": "0.4000", "count": "0.25",
        "fee_estimate": "0.006000", "amount_estimate": "0.1060",
    }


def metadata():
    return {
        "approved": True, "reason": "APPROVED",
        "model_win_probability": "0.950000",
        "expected_net_dollars": "0.124000",
    }


def test_sanitized_quote_captures_only_allowlisted_numbers_and_never_preview_id():
    item = sanitize_quote_snapshot(
        quote(), metadata(), preview_received_at_ns=1_900_000_000 * NS,
    )
    assert item == {
        "preview_received_at_ns": 1_900_000_000 * NS,
        "quoted_unit_price_dollars": "0.4000",
        "quoted_count_fp": "0.25",
        "quoted_fee_dollars": "0.006000",
        "quoted_total_dollars": "0.1060",
        "model_win_probability": "0.950000",
        "model_expected_net_dollars": "0.124000",
        "quote_gate": "APPROVED",
    }
    assert "SECRET" not in repr(item) and "sensitive" not in repr(item)


@pytest.mark.parametrize("field,bad", [
    ("unit_price", float("nan")), ("unit_price", "1.10"),
    ("count", 0), ("count", "Infinity"),
    ("fee_estimate", "-0.01"), ("amount_estimate", 0),
    ("unit_price", True), ("unit_price", None),
])
def test_bad_quotes_fail_closed_in_audit_without_crashing_trading(field,bad):
    bad_quote = quote()
    bad_quote[field] = bad
    assert sanitize_quote_snapshot(
        bad_quote, metadata(), preview_received_at_ns=1_900_000_000 * NS,
    ) is None


def test_original_quote_survives_restart_and_is_not_modified_by_execution_result(tmp_path):
    path = tmp_path / "auto.sqlite3"
    journal = AutoTradeHistory(path)
    snap = sanitize_quote_snapshot(
        quote(), metadata(), preview_received_at_ns=1_900_000_000 * NS,
    )
    journal.record_attempt(ticker=TICKER, attempted_at_ns=11, outcome="UP",
                           amount="0.11", confidence=.9)
    journal.record_stage_attempt(ticker=TICKER, stage="FINAL_BUY",
                                 attempted_at_ns=12, outcome="UP",
                                 amount="0.11", confidence=.9, quote=snap)
    journal.record_stage_result(ticker=TICKER, stage="FINAL_BUY", status="FILLED",
                                order_id="filled-order", fill_count="0.25")
    saved = AutoTradeHistory(path).recent_stages(TICKER)[0]
    assert saved["quoted_unit_price_dollars"] == "0.4000"
    assert saved["quoted_fee_dollars"] == "0.006000"
    assert saved["preview_received_at_ns"] == 1_900_000_000 * NS
    assert saved["quote_gate"] == "APPROVED"
    assert saved["order_id"] == "filled-order"
    raw = path.read_bytes()
    assert b"SECRET-NEVER-STORE" not in raw
    assert b"sensitive-preview-token-never-store" not in raw


def test_old_journal_migrates_in_place_and_old_orders_remain_intact(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE auto_trade_stages (
          ticker TEXT NOT NULL, stage TEXT NOT NULL, attempted_at_ns INTEGER NOT NULL,
          outcome TEXT NOT NULL, amount TEXT NOT NULL, confidence REAL NOT NULL,
          status TEXT NOT NULL, order_id TEXT, fill_count TEXT,
          PRIMARY KEY (ticker, stage))""")
        db.execute("""INSERT INTO auto_trade_stages VALUES
          (?,?,?,?,?,?,?,?,?)""", (TICKER, "FINAL_BUY", 100, "UP",
                                 "0.10", .90, "FILLED", "legacy", "0.25"))
    journal = AutoTradeHistory(path)
    old = journal.recent_stages(TICKER)[0]
    assert old["order_id"] == "legacy"
    assert old["quoted_unit_price_dollars"] is None
    assert old["quote_gate"] is None
    assert old["status"] == "FILLED"
    assert AutoTradeHistory(path).recent_stages(TICKER)[0] == old


def test_quote_rejection_counts_with_no_order_journal_and_no_secrets(tmp_path):
    journal = AutoTradeHistory(tmp_path / "rejected.sqlite3")
    snap = sanitize_quote_snapshot(
        quote(), {"reason": "NON_POSITIVE_EDGE", "approved": False},
        preview_received_at_ns=100,
    )
    journal.record_quote_rejection(ticker=TICKER, stage="EARLY_BUY",
                                   reason="NON_POSITIVE_EDGE",
                                   observed_at_ns=100, quote=snap)
    journal.record_quote_rejection(ticker=TICKER, stage="EARLY_BUY",
                                   reason="NON_POSITIVE_EDGE",
                                   observed_at_ns=200, quote=snap)
    assert journal.recent() == []
    rows = journal.recent_quote_rejections()
    assert len(rows) == 1
    assert rows[0]["observations"] == 2
    assert rows[0]["first_observed_at_ns"] == 100
    assert rows[0]["last_observed_at_ns"] == 200
    assert rows[0]["quoted_unit_price_dollars"] == "0.4000"
    assert "sensitive" not in str(rows)
    report = read_execution_audit(journal.path)
    assert report["rejection_observations"] == 2
    assert report["rejection_reasons"] == {"NON_POSITIVE_EDGE": 2}
    assert report["submitted_stage_attempts"] == 0
    assert report["verified_profit_dollars"] is None


def test_live_engine_records_approved_quote_atomically_in_order_stage(tmp_path):
    now = 1_900_000_000 * NS
    close = now + 900 * NS
    feed = {
        "market_ticker": TICKER,
        "market_close_ts_ns": close,
        "checkpoint_ts_ns": close - 240 * NS,
        "final_forecast": None,
        "status": "LIVE",
    }

    class Manual:
        orders = []
        def preview(self, *args):
            return quote()
        def place(self, preview_id, ticker):
            self.orders.append((preview_id,ticker))
            return {"order_id":"exec-1","status":"FILLED","fill_count":"0.25"}

    fake = Manual()
    journal_path = tmp_path / "execution.sqlite3"
    trader = ArmedAutoTrader(fake, lambda: feed, now_ns=lambda: now,
                             history_path=journal_path)
    trader.arm("0.25", start_worker=False)
    now = close - 239 * NS
    feed["final_forecast"] = {
        "market_ticker":TICKER,
        "scheduled_ts_ns":close-240*NS,
        "forecast":"ABOVE", "confidence":0.9,
    }
    trader.tick()
    assert fake.orders == [("sensitive-preview-token-never-store", TICKER)]
    saved = AutoTradeHistory(journal_path).recent_stages(TICKER)[0]
    assert saved["quoted_unit_price_dollars"] == "0.4000"
    assert saved["quoted_total_dollars"] == "0.1060"
    assert saved["model_expected_net_dollars"] == "0.124000"
    assert saved["quote_gate"] == "APPROVED"
    assert b"sensitive-preview-token-never-store" not in journal_path.read_bytes()


def test_live_engine_records_rejected_value_gate_without_pretending_trade(tmp_path):
    now = 1_900_000_000 * NS
    close = now + 900 * NS
    feed = {
        "market_ticker":TICKER, "market_close_ts_ns":close,
        "checkpoint_ts_ns":close-240*NS, "status":"LIVE",
        "final_forecast": None,
    }

    class Manual:
        orders = []
        def preview(self,*args):
            return {**quote(), "unit_price":"0.9900",
                    "amount_estimate":"0.2500"}
        def place(self,*args):
            self.orders.append(args)
            raise AssertionError("Uneconomic quote must not trade")

    fake = Manual()
    journal_path = tmp_path / "rejection.sqlite3"
    trader = ArmedAutoTrader(fake, lambda:feed, now_ns=lambda:now,
                             history_path=journal_path)
    trader.arm("0.25", start_worker=False)
    now = close - 239 * NS
    feed["final_forecast"] = {
        "market_ticker":TICKER,
        "scheduled_ts_ns":close-240*NS,
        "forecast":"ABOVE","confidence":.9,
    }
    trader.tick()
    assert fake.orders == []
    assert AutoTradeHistory(journal_path).recent_stages(TICKER) == []
    rejections = AutoTradeHistory(journal_path).recent_quote_rejections()
    assert rejections[0]["reason"] == "NON_POSITIVE_EDGE"
    assert rejections[0]["quoted_unit_price_dollars"] == "0.9900"
    assert trader.status()["last_result"] == "SKIPPED"


def test_execution_audit_does_not_invent_prices_for_legacy_stages(tmp_path):
    journal = AutoTradeHistory(tmp_path / "legacy.sqlite3")
    journal.record_stage_attempt(ticker=TICKER, stage="FINAL_BUY",
                                 attempted_at_ns=100, outcome="UP",
                                 amount="1", confidence=.9)
    report = read_execution_audit(journal.path)
    assert report["submitted_stage_attempts"] == 1
    assert report["stages_with_quoted_price"] == 0
    assert report["stages_missing_quote"] == 1
    assert report["verified_profit_dollars"] is None
