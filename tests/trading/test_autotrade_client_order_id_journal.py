"""AutoTrade client IDs must survive an ambiguous exchange submission.

All exchange operations are fake. No live credentials or real orders.
"""
from pathlib import Path
import sqlite3
import uuid
from types import SimpleNamespace

from kalshi_edge.trading.armed_autotrade import ArmedAutoTrader
from kalshi_edge.trading.autotrade_history import AutoTradeHistory
from kalshi_edge.trading.manual_trade import ManualTrader

NS = 1_000_000_000
TICKER = "KXBTC15M-DURABLE-ID"


def test_client_order_id_is_fsynced_in_stage_before_exchange_post(tmp_path):
    journal = tmp_path / "auto.sqlite3"
    seen = []

    class LostResponseExchange:
        def send_order(self, request):
            with sqlite3.connect(journal) as db:
                stored = db.execute(
                    "SELECT client_order_id, status FROM auto_trade_stages "
                    "WHERE ticker=? AND stage='FINAL_BUY'",
                    (TICKER,),
                ).fetchone()
            seen.append((dict(request), stored))
            raise OSError("Exchange may have accepted POST; reply was lost")

    exchange = LostResponseExchange()
    manual = ManualTrader(
        tmp_path, vault=SimpleNamespace(), client=exchange,
        market_client=SimpleNamespace(),
    )
    price = {
        "ticker": TICKER, "action": "BUY", "outcome": "UP",
        "units": "DOLLARS", "side": "bid", "price": "0.6000",
        "unit_price": "0.6000", "count": "0.10",
        "fee_estimate": "0.002000", "amount_estimate": "0.0620",
        "available_balance": "10.00", "position_yes": "0.00",
    }
    manual._prepare = lambda *args, fresh_account=False, **kwargs: dict(price)
    clock = [1_900_000_000 * NS]
    close = clock[0] + 900 * NS
    checkpoint = close - 480 * NS
    feed = {
        "market_ticker": TICKER,
        "market_close_ts_ns": close,
        "checkpoint_ts_ns": checkpoint,
        "final_forecast": None,
    }
    engine = ArmedAutoTrader(
        manual, lambda: feed, now_ns=lambda: clock[0], history_path=journal,
    )
    engine.arm("0.10", continuous=True, start_worker=False)
    clock[0] = checkpoint + NS
    feed["final_forecast"] = {
        "market_ticker": TICKER, "scheduled_ts_ns": checkpoint,
        "forecast": "ABOVE", "confidence": 0.98,
    }
    engine.tick()

    assert len(seen) == 1
    request, persisted = seen[0]
    assert persisted is not None and persisted[1] == "ATTEMPTED"
    assert persisted[0] == request["client_order_id"]
    assert str(uuid.UUID(persisted[0])) == persisted[0]
    assert engine.status()["state"] == "UNKNOWN"
    assert AutoTradeHistory(journal).recent_stages(TICKER)[0]["client_order_id"] == persisted[0]
    assert AutoTradeHistory(journal).has_attempt(TICKER)
    engine.tick()
    assert len(seen) == 1


def test_old_autotrade_stage_journals_migrate_without_losing_rows(tmp_path):
    journal = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(journal) as db:
        db.execute(
            "CREATE TABLE auto_trade_stages (ticker TEXT, stage TEXT, "
            "attempted_at_ns INTEGER, outcome TEXT, amount TEXT, "
            "confidence REAL, status TEXT, order_id TEXT, fill_count TEXT, "
            "PRIMARY KEY(ticker, stage))"
        )
        db.execute(
            "INSERT INTO auto_trade_stages VALUES "
            "(?, 'FINAL_BUY', 123, 'UP', '0.10', 0.98, 'UNKNOWN', NULL, NULL)",
            (TICKER,),
        )

    store = AutoTradeHistory(journal)
    stages = store.recent_stages(TICKER)
    assert len(stages) == 1
    assert stages[0]["status"] == "UNKNOWN"
    assert stages[0]["client_order_id"] is None
