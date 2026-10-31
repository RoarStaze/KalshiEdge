"""Local durable journal for final-forecast AutoTrade order attempts.

One database row per KXBTC15M ticker. Inserting before the exchange POST
provides an at-most-one-attempt guard across normal restarts. A row still
marked ATTEMPTED after a crash must be treated as UNKNOWN, never as a fill.
This is not a reconciliation service or a guarantee about external execution.
"""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
from contextlib import closing

from .autotrade_execution_audit import QUOTE_FIELDS


_VALID_STATUS = frozenset(("ATTEMPTED", "FILLED", "PARTIAL", "NO_FILL", "UNKNOWN"))
_FIELDS = (
    "ticker", "attempted_at_ns", "outcome", "amount",
    "confidence", "status", "order_id", "fill_count",
)


class AutoTradeHistory:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=5.0)) as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS auto_trade_attempts (
                    ticker TEXT PRIMARY KEY,
                    attempted_at_ns INTEGER NOT NULL,
                    outcome TEXT NOT NULL CHECK(outcome IN ('UP', 'DOWN')),
                    amount TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    status TEXT NOT NULL,
                    order_id TEXT,
                    fill_count TEXT
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS auto_trade_stages (
                    ticker TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    attempted_at_ns INTEGER NOT NULL,
                    outcome TEXT NOT NULL,
                    amount TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    status TEXT NOT NULL,
                    order_id TEXT,
                    fill_count TEXT,
                    PRIMARY KEY (ticker, stage)
                )
            """)
            # Non-destructive migration for journals created before quote capture.
            # Keep existing stage primary keys and retry/UNKNOWN protections.
            existing = {row[1] for row in db.execute(
                "PRAGMA table_info(auto_trade_stages)"
            )}
            for name in QUOTE_FIELDS:
                if name not in existing:
                    kind = "INTEGER" if name == "preview_received_at_ns" else "TEXT"
                    db.execute(f"ALTER TABLE auto_trade_stages ADD COLUMN {name} {kind}")
            db.execute("""
                CREATE TABLE IF NOT EXISTS auto_trade_quote_rejections (
                    ticker TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    first_observed_at_ns INTEGER NOT NULL,
                    last_observed_at_ns INTEGER NOT NULL,
                    observations INTEGER NOT NULL,
                    quoted_unit_price_dollars TEXT,
                    quoted_count_fp TEXT,
                    quoted_fee_dollars TEXT,
                    quoted_total_dollars TEXT,
                    model_expected_net_dollars TEXT,
                    PRIMARY KEY (ticker, stage, reason)
                )
            """)
            db.commit()
        if os.name != "nt":
            os.chmod(self.path, 0o600)

    def has_attempt(self, ticker: str) -> bool:
        with closing(sqlite3.connect(self.path, timeout=5.0)) as db:
            return db.execute(
                "SELECT 1 FROM auto_trade_attempts WHERE ticker=?",
                (ticker,),
            ).fetchone() is not None

    def record_attempt(self, *, ticker: str, attempted_at_ns: int,
                       outcome: str, amount: str, confidence: float) -> None:
        # Single unique-key commit BEFORE the real exchange request.
        # SQLite keeps duplicate attempts from being inserted after restart.
        with closing(sqlite3.connect(self.path, timeout=5.0)) as db:
            with db:
                db.execute("""
                    INSERT INTO auto_trade_attempts (
                      ticker, attempted_at_ns, outcome, amount, confidence, status
                    ) VALUES (?, ?, ?, ?, ?, 'ATTEMPTED')
                """, (ticker, attempted_at_ns, outcome, amount, confidence))

    def record_result(self, *, ticker: str, status: str,
                      order_id: str | None = None,
                      fill_count: str | None = None) -> None:
        if status not in _VALID_STATUS or status == "ATTEMPTED":
            raise ValueError("Unexpected AutoTrade execution status")
        with closing(sqlite3.connect(self.path, timeout=5.0)) as db:
            with db:
                cursor = db.execute("""
                    UPDATE auto_trade_attempts
                    SET status=?, order_id=?, fill_count=?
                    WHERE ticker=?
                """, (status, order_id, fill_count, ticker))
                if cursor.rowcount != 1:
                    raise ValueError("Cannot update missing AutoTrade attempt")

    def record_stage_attempt(self, *, ticker: str, stage: str,
                             attempted_at_ns: int, outcome: str, amount: str,
                             confidence: float,
                             quote: dict | None = None) -> None:
        allowed = ("EARLY_BUY", "EARLY_BUY_2", "REVERSAL_SELL",
                   "FINAL_BUY", "FINAL_BUY_2")
        if stage not in allowed or outcome not in ("UP", "DOWN"):
            raise ValueError("Invalid AutoTrade order stage")
        snapshot = quote if isinstance(quote, dict) else {}
        values = tuple(snapshot.get(key) for key in QUOTE_FIELDS)
        # Quote fields are written in the SAME atomic pre-POST stage transaction.
        # No second disk write is added to the accepted-order critical path.
        fields = ",".join(QUOTE_FIELDS)
        placeholders = ",".join("?" for _ in QUOTE_FIELDS)
        with closing(sqlite3.connect(self.path, timeout=5.0)) as db:
            with db:
                db.execute(f"""
                    INSERT INTO auto_trade_stages
                    (ticker,stage,attempted_at_ns,outcome,amount,confidence,status,{fields})
                    VALUES (?,?,?,?,?,?,'ATTEMPTED',{placeholders})
                """, (ticker,stage,attempted_at_ns,outcome,amount,confidence,*values))

    def record_quote_rejection(self, *, ticker: str, stage: str,
                               reason: str, observed_at_ns: int,
                               quote: dict | None) -> None:
        """Aggregate rejected value-gate checks without creating an order attempt."""
        if stage not in ("EARLY_BUY", "EARLY_BUY_2", "FINAL_BUY", "FINAL_BUY_2"):
            raise ValueError("Invalid rejected BUY stage")
        if reason not in ("NON_POSITIVE_EDGE", "INSUFFICIENT_EDGE", "INVALID_QUOTE"):
            raise ValueError("Invalid quote rejection reason")
        snapshot = quote if isinstance(quote, dict) else {}
        values = tuple(snapshot.get(key) for key in (
            "quoted_unit_price_dollars", "quoted_count_fp",
            "quoted_fee_dollars", "quoted_total_dollars",
            "model_expected_net_dollars",
        ))
        with closing(sqlite3.connect(self.path, timeout=5.0)) as db:
            with db:
                db.execute("""
                    INSERT INTO auto_trade_quote_rejections (
                        ticker, stage, reason, first_observed_at_ns,
                        last_observed_at_ns, observations,
                        quoted_unit_price_dollars, quoted_count_fp,
                        quoted_fee_dollars, quoted_total_dollars,
                        model_expected_net_dollars
                    ) VALUES (?,?,?,?,?,1,?,?,?,?,?)
                    ON CONFLICT(ticker,stage,reason) DO UPDATE SET
                        last_observed_at_ns=excluded.last_observed_at_ns,
                        observations=observations+1,
                        quoted_unit_price_dollars=excluded.quoted_unit_price_dollars,
                        quoted_count_fp=excluded.quoted_count_fp,
                        quoted_fee_dollars=excluded.quoted_fee_dollars,
                        quoted_total_dollars=excluded.quoted_total_dollars,
                        model_expected_net_dollars=excluded.model_expected_net_dollars
                """, (ticker,stage,reason,observed_at_ns,observed_at_ns,*values))

    def recent_quote_rejections(self, limit: int = 100) -> list[dict]:
        limit = max(1, min(int(limit), 1000))
        with closing(sqlite3.connect(self.path, timeout=5.0)) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("""
                SELECT ticker,stage,reason,first_observed_at_ns,last_observed_at_ns,
                       observations,quoted_unit_price_dollars,quoted_count_fp,
                       quoted_fee_dollars,quoted_total_dollars,model_expected_net_dollars
                FROM auto_trade_quote_rejections
                ORDER BY last_observed_at_ns DESC LIMIT ?
            """, (limit,)).fetchall()
        return [dict(row) for row in rows]

    def record_stage_result(self, *, ticker: str, stage: str,
                            status: str, order_id: str | None,
                            fill_count: str | None) -> None:
        if status not in ("FILLED", "PARTIAL", "NO_FILL", "UNKNOWN"):
            raise ValueError("Invalid exchange execution status")
        with closing(sqlite3.connect(self.path, timeout=5.0)) as db:
            with db:
                row = db.execute("""
                    UPDATE auto_trade_stages SET status=?, order_id=?, fill_count=?
                    WHERE ticker=? AND stage=? AND status='ATTEMPTED'
                """, (status,order_id,fill_count,ticker,stage))
                if row.rowcount != 1:
                    raise ValueError("Stage already finalized or missing")

    def recent_stages(self, ticker: str) -> list[dict]:
        with closing(sqlite3.connect(self.path, timeout=5.0)) as db:
            rows = db.execute("""
                SELECT ticker,stage,attempted_at_ns,outcome,amount,confidence,
                       status,order_id,fill_count,
                       preview_received_at_ns,quoted_unit_price_dollars,
                       quoted_count_fp,quoted_fee_dollars,quoted_total_dollars,
                       model_win_probability,model_expected_net_dollars,quote_gate
                FROM auto_trade_stages
                WHERE ticker=? ORDER BY attempted_at_ns,
                    CASE stage WHEN 'EARLY_BUY' THEN 0
                    WHEN 'EARLY_BUY_2' THEN 1
                    WHEN 'REVERSAL_SELL' THEN 2
                    WHEN 'FINAL_BUY' THEN 3 ELSE 4 END
            """, (ticker,)).fetchall()
        names = ("ticker","stage","attempted_at_ns","outcome","amount",
                 "confidence","status","order_id","fill_count",*QUOTE_FIELDS)
        return [{**dict(zip(names,r)),
                 "status":"UNKNOWN" if r[6]=="ATTEMPTED" else r[6]} for r in rows]

    def recent(self, limit: int = 25) -> list[dict]:
        limit = max(1, min(int(limit), 100))
        with closing(sqlite3.connect(self.path, timeout=5.0)) as db:
            rows = db.execute("""
                SELECT ticker, attempted_at_ns, outcome, amount, confidence,
                       status, order_id, fill_count
                FROM auto_trade_attempts
                ORDER BY attempted_at_ns DESC, ticker DESC LIMIT ?
            """, (limit,)).fetchall()
        entries = []
        for row in rows:
            item = dict(zip(_FIELDS, row))
            # The exchange may have received the POST before process failure.
            if item["status"] == "ATTEMPTED":
                item["status"] = "UNKNOWN"
            entries.append(item)
        return entries
