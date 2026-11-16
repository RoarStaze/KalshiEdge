"""Durable, safely classified reasons for final-only AutoTrade NOT_SUBMITTED.

Mocks only; NO Kalshi API calls, credentials, POSTs or real money orders.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from kalshi_edge.trading.armed_autotrade import ArmedAutoTrader
from kalshi_edge.trading.autotrade_execution_audit import (
    classify_auto_order_rejection, read_execution_audit,
)
from kalshi_edge.trading.autotrade_history import AutoTradeHistory
from kalshi_edge.trading.manual_trade import OrderNotSubmitted

TICKER="KXBTC15M-PRESUBMIT-REASON"
NS=1_000_000_000


@pytest.mark.parametrize("raw,expected",[
    ("Fresh AutoTrade quote exceeds available cash","INSUFFICIENT_CASH"),
    ("Fresh AutoTrade quote exceeds 5% cash risk ceiling","LEGACY_CASH_FRACTION_CAP"),
    ("Fresh quote exceeds AutoTrade dollar budget","DOLLAR_BUDGET_EXCEEDED"),
    ("Quote expired; refresh and click again","QUOTE_EXPIRED"),
    ("Market changed; refresh quote","MARKET_CHANGED"),
    ("Market is closing or has no valid close time","MARKET_NOT_OPEN"),
    ("No executable quote","NO_EXECUTABLE_LIQUIDITY"),
    ("Pre-submit account/quote read HTTP 429","FRESH_QUOTE_READ_FAILED"),
    ("Pre-submit validation failed (ValueError)","FRESH_QUOTE_VALIDATION_FAILED"),
    ("Kalshi rejected order (HTTP 422)","EXCHANGE_REJECTED"),
    ("Secret 123_TOKEN999 was invalid","OTHER_DEFINITE_REJECTION"),
])
def test_classification_is_allowlisted_and_never_reproduces_raw_sensitive_text(raw,expected):
    result=classify_auto_order_rejection(raw)
    assert result==expected
    assert "SECRET" not in result
    assert "TOKEN" not in result
    assert "429" not in result
    assert "422" not in result


def _attempt(store: AutoTradeHistory,ticker=TICKER,stage="FINAL_BUY"):
    store.record_attempt(
        ticker=ticker,attempted_at_ns=1_900_000_000*NS,
        outcome="UP",amount="3.00",confidence=.8,
    )
    store.record_stage_attempt(
        ticker=ticker,stage=stage,attempted_at_ns=1_900_000_000*NS,
        outcome="UP",amount="3.00",confidence=.8,
    )


def test_rejection_reason_survives_restart_and_is_in_readonly_execution_audit(tmp_path):
    path=tmp_path/"data"/"autotrade_history.sqlite3"
    store=AutoTradeHistory(path)
    _attempt(store)
    store.record_stage_result(
        ticker=TICKER,stage="FINAL_BUY",status="NOT_SUBMITTED",
        order_id=None,fill_count=None,
        rejection_reason_code="INSUFFICIENT_CASH",
    )
    store.record_result(ticker=TICKER,status="NOT_SUBMITTED")
    restarted=AutoTradeHistory(path)
    row=restarted.recent_stages(TICKER)[0]
    assert row["status"]=="NOT_SUBMITTED"
    assert row["rejection_reason_code"]=="INSUFFICIENT_CASH"
    audit=read_execution_audit(path)
    assert audit["unsubmitted_reason_counts"]=={"INSUFFICIENT_CASH":1}
    assert audit["unsubmitted_stage_attempts"]==1
    assert audit["submitted_stage_attempts"]==1
    assert audit["verified_profit_dollars"] is None
    assert b"SECRET" not in path.read_bytes()


def test_refuse_arbitrary_reason_and_never_mark_fill_with_rejection_code(tmp_path):
    store=AutoTradeHistory(tmp_path/"a.sqlite3")
    _attempt(store)
    with pytest.raises(ValueError):
        store.record_stage_result(
            ticker=TICKER,stage="FINAL_BUY",status="NOT_SUBMITTED",
            order_id=None,fill_count=None,
            rejection_reason_code="SECRET-API-KEY-NO",
        )
    with pytest.raises(ValueError):
        store.record_stage_result(
            ticker=TICKER,stage="FINAL_BUY",status="FILLED",
            order_id="mock",fill_count="0.10",
            rejection_reason_code="INSUFFICIENT_CASH",
        )
    # Neither failed validation changes stage status.
    assert store.recent_stages(TICKER)[0]["status"]=="UNKNOWN"


def test_legacy_auto_trade_stage_migrates_without_erasing_old_fill(tmp_path):
    path=tmp_path/"old.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE auto_trade_stages (
          ticker TEXT NOT NULL, stage TEXT NOT NULL, attempted_at_ns INTEGER NOT NULL,
          outcome TEXT NOT NULL, amount TEXT NOT NULL, confidence REAL NOT NULL,
          status TEXT NOT NULL, order_id TEXT, fill_count TEXT,
          PRIMARY KEY (ticker, stage))""")
        db.execute("""INSERT INTO auto_trade_stages VALUES
          (?,?,?,?,?,?,?,?,?)""",(TICKER,"FINAL_BUY",100,"UP","3.00",.8,
                                   "FILLED","old-fill","0.10"))
    first=AutoTradeHistory(path).recent_stages(TICKER)[0]
    second=AutoTradeHistory(path).recent_stages(TICKER)[0]
    assert first==second
    assert first["rejection_reason_code"] is None
    assert first["order_id"]=="old-fill"
    assert first["status"]=="FILLED"
    report=read_execution_audit(path)
    assert report["unsubmitted_reason_counts"]=={}


def test_older_journal_without_reason_column_remains_readable_without_migration(tmp_path):
    path=tmp_path/"raw-legacy.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE auto_trade_stages (
          ticker TEXT NOT NULL, stage TEXT NOT NULL,
          status TEXT NOT NULL, quoted_unit_price_dollars TEXT)""")
        db.execute("INSERT INTO auto_trade_stages VALUES (?,?,?,?)",
                   (TICKER,"FINAL_BUY","NOT_SUBMITTED",None))
    # Read-only audit must NEVER attempt schema migration.
    result=read_execution_audit(path)
    assert result["unsubmitted_stage_attempts"]==1
    assert result["unsubmitted_reason_counts"]=={"OLDER_JOURNAL_REASON_UNAVAILABLE":1}
    assert result["stages_missing_quote"]==1


def test_live_engine_journals_definite_not_submitted_but_no_unknown_exchange_errors(tmp_path):
    clock=[1_900_000_000*NS]
    close=clock[0]+900*NS
    checkpoint=close-480*NS
    feed={
        "market_ticker":TICKER,
        "market_close_ts_ns":close,
        "checkpoint_ts_ns":checkpoint,
        "final_forecast":None,
    }
    class Exchange:
        def __init__(self):
            self.calls=0
        def preview(self,ticker,action,outcome,units,amount):
            return {
                "ticker":ticker,"action":action,"outcome":outcome,
                "preview_id":"sensitive-not-to-store","count":"1.00",
                "unit_price":"0.7000","fee_estimate":"0.0100",
                "amount_estimate":"0.7100",
            }
        def place(self,preview_id,ticker):
            self.calls+=1
            raise OrderNotSubmitted("Fresh AutoTrade quote exceeds available cash")

    exchange=Exchange()
    journal=tmp_path/"journal.sqlite3"
    bot=ArmedAutoTrader(
        exchange,lambda:feed,now_ns=lambda:clock[0],
        history_path=journal,
    )
    bot.arm("3.00",continuous=True,start_worker=False)
    clock[0]=checkpoint+NS
    feed["final_forecast"]={
        "market_ticker":TICKER,
        "scheduled_ts_ns":checkpoint,
        "forecast":"ABOVE","confidence":.9,
        "directional_probability":.95,
    }
    bot.tick()
    assert bot.status()["last_result"]=="NOT_SUBMITTED"
    assert "available cash" in bot.status()["message"]
    assert exchange.calls==1
    stage=AutoTradeHistory(journal).recent_stages(TICKER)[0]
    assert stage["status"]=="NOT_SUBMITTED"
    assert stage["rejection_reason_code"]=="INSUFFICIENT_CASH"
    # Status text is ephemeral; the safe cause must survive a restart.
    assert read_execution_audit(journal)["unsubmitted_reason_counts"]=={
        "INSUFFICIENT_CASH":1,
    }
    for _ in range(4):
        bot.tick()
    assert exchange.calls==1
    assert b"sensitive-not-to-store" not in journal.read_bytes()


def test_unknown_after_post_never_invents_definite_failure_reason(tmp_path):
    store=AutoTradeHistory(tmp_path/"j.sqlite3")
    _attempt(store)
    store.record_stage_result(
        ticker=TICKER,stage="FINAL_BUY",status="UNKNOWN",
        order_id=None,fill_count=None,
    )
    row=store.recent_stages(TICKER)[0]
    assert row["rejection_reason_code"] is None
    assert read_execution_audit(store.path)["unsubmitted_reason_counts"]=={}
