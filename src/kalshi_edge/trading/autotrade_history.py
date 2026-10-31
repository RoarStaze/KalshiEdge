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
