"""Append-only, causal public order-book evidence for the Strat experiment.

One record per verified market snapshot, not one per side. Neither hypothetical
fills nor trade history are inferred from the data; subsequent replay can pair
the exact contemporaneous two-sided quotes with recorded signal decisions.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import math
import sqlite3
import threading

from .strat_policy import UpQuote


class StratQuoteTape:
    """Persistent two-sided market observations, separate from the P&L ledger."""

    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS quotes (
                ticker TEXT NOT NULL,
                observed_ns INTEGER NOT NULL,
                close_ns INTEGER NOT NULL,
                remaining_seconds REAL NOT NULL,
                up_bid TEXT NOT NULL,
                up_ask TEXT NOT NULL,
                up_bid_depth TEXT NOT NULL,
                up_ask_depth TEXT NOT NULL,
                down_bid TEXT NOT NULL,
                down_ask TEXT NOT NULL,
                down_bid_depth TEXT NOT NULL,
                down_ask_depth TEXT NOT NULL,
                up_signal TEXT NOT NULL,
                down_signal TEXT NOT NULL,
                position_side TEXT,
                PRIMARY KEY(ticker, observed_ns)
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_strat_quote_time "
                       "ON quotes(observed_ns)")
            self._count = int(db.execute("SELECT COUNT(*) FROM quotes").fetchone()[0])
            self._tickers = {
                row[0] for row in db.execute("SELECT DISTINCT ticker FROM quotes")
            }

    @contextmanager
    def _db(self):
        db = sqlite3.connect(str(self.path), timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def append(self, *, observed_ns: int, ticker: str, close_ns: int,
               remaining_seconds: float, quotes: dict[str, UpQuote],
               signals: dict[str, str], position_side: str | None) -> None:
        if (type(observed_ns) is not int or observed_ns <= 0
                or type(close_ns) is not int or close_ns <= observed_ns
                or not isinstance(remaining_seconds, (float, int))
                or not math.isfinite(remaining_seconds)
                or not 0 < remaining_seconds <= 900
                or position_side not in (None, "UP", "DOWN")
                or not isinstance(signals, dict)
                or set(signals) != {"UP", "DOWN"}
                or not all(isinstance(signals[s], str)
                           and 0 < len(signals[s]) <= 120 for s in ("UP", "DOWN"))
                or not isinstance(quotes, dict)
                or set(quotes) != {"UP", "DOWN"}):
            raise ValueError("Invalid Strat observation metadata")
        for side in ("UP", "DOWN"):
            q = quotes[side]
            if (not isinstance(q, UpQuote) or not q.valid()
                    or q.ticker != ticker or q.observed_ns != observed_ns):
                raise ValueError("Unverified or mismatched side quote")

        up, down = quotes["UP"], quotes["DOWN"]
        values = (
            ticker, observed_ns, close_ns, float(remaining_seconds),
            str(up.bid), str(up.ask), str(up.bid_depth), str(up.ask_depth),
            str(down.bid), str(down.ask), str(down.bid_depth), str(down.ask_depth),
            signals["UP"], signals["DOWN"], position_side,
        )
        with self._lock, self._db() as db:
            inserted = db.execute("""INSERT OR IGNORE INTO quotes (
                ticker, observed_ns, close_ns, remaining_seconds,
                up_bid, up_ask, up_bid_depth, up_ask_depth,
                down_bid, down_ask, down_bid_depth, down_ask_depth,
                up_signal, down_signal, position_side
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", values)
            if inserted.rowcount == 1:
                self._count += 1
                self._tickers.add(ticker)

    def summary(self) -> dict:
        with self._lock:
            return {
                "snapshots": self._count,
                "markets": len(self._tickers),
                "path": str(self.path),
            }

    def recent(self, limit: int = 10) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Invalid quote tape limit")
        with self._lock, self._db() as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(
                "SELECT * FROM quotes ORDER BY observed_ns DESC LIMIT ?", (limit,)
            )]
