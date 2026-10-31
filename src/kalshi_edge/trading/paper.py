"""Isolated paper trader consuming immutable V5 final forecast JSON files.

No Kalshi order POST endpoint exists in this module. Never backdate entries,
retry uncertain attempts, or substitute live V6/V6.1 forecasts for V5 locks.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import threading
import time

import httpx

from kalshi_edge.bootstrap.final_history import (
    _forecasts, _load_saved_settlement, SettlementIntegrityError
)
from .risk import DAILY_STOP, EntryRejected, PaperPlan, decimal, plan_locked_entry

API_ROOT = "https://external-api.kalshi.com/trade-api/v2"
NS = 1_000_000_000


class PaperLedger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._mutex = threading.Lock()
        with self._open() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS attempts (
                ticker TEXT PRIMARY KEY,
                created_ns INTEGER NOT NULL,
                forecast TEXT NOT NULL,
                status TEXT NOT NULL,
                reason TEXT,
                price TEXT,
                quantity TEXT,
                fee_reserve TEXT,
                debit TEXT,
                expected_profit TEXT,
                model_probability TEXT,
                actual_side TEXT,
                net_profit TEXT,
                settled_ns INTEGER,
                diagnostics_json TEXT
            )""")
            # Existing local installations already have a SQLite ledger.
            columns = {row[1] for row in db.execute("PRAGMA table_info(attempts)")}
            if "diagnostics_json" not in columns:
                db.execute("ALTER TABLE attempts ADD COLUMN diagnostics_json TEXT")

    @contextmanager
    def _open(self):
        connection = sqlite3.connect(str(self.path), timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def reserve(self, ticker: str, forecast: str, now_ns: int) -> bool:
        with self._mutex, self._open() as db:
            result = db.execute(
                "INSERT OR IGNORE INTO attempts "
                "(ticker, created_ns, forecast, status) VALUES (?, ?, ?, ?)",
                (ticker, now_ns, forecast, "PROCESSING"),
            )
            return result.rowcount == 1

    def finish(self, ticker: str, *, status: str, reason: str = "",
               plan: PaperPlan | None = None, diagnostics: dict | None = None) -> None:
        if status not in ("PAPER_OPEN", "SKIP", "ERROR"):
            raise ValueError("invalid attempt state")
        values = plan.as_dict() if plan else {}
        details = None if diagnostics is None else json.dumps(
            diagnostics, sort_keys=True, allow_nan=False, separators=(",", ":")
        )
        with self._mutex, self._open() as db:
            db.execute("""UPDATE attempts
                SET status=?, reason=?, price=?, quantity=?, fee_reserve=?,
                    debit=?, expected_profit=?, model_probability=?, diagnostics_json=?
                WHERE ticker=? AND status='PROCESSING'""", (
                    status, reason, values.get("price"), values.get("count"),
                    values.get("fee_reserve"), values.get("worst_case_debit"),
                    values.get("estimated_net_profit"),
                    values.get("model_win_probability"), details, ticker,
                ))

    def open_positions(self) -> list[dict]:
        with self._mutex, self._open() as db:
            rows = db.execute(
                "SELECT * FROM attempts WHERE status='PAPER_OPEN' "
                "ORDER BY created_ns"
            ).fetchall()
            return [dict(x) for x in rows]

    def settle(self, ticker: str, actual: str, now_ns: int) -> None:
        if actual not in ("ABOVE", "BELOW"):
            raise ValueError("invalid official outcome")
        with self._mutex, self._open() as db:
            row = db.execute(
                "SELECT * FROM attempts WHERE ticker=? AND status='PAPER_OPEN'",
                (ticker,)
            ).fetchone()
            if row is None:
                return
            quantity = decimal(row["quantity"])
            price = decimal(row["price"])
            fee = decimal(row["fee_reserve"])
            net = quantity * ((Decimal("1") if row["forecast"] == actual else Decimal("0")) - price) - fee
            db.execute(
                "UPDATE attempts SET status='SETTLED',actual_side=?,net_profit=?,"
                "settled_ns=? WHERE ticker=? AND status='PAPER_OPEN'",
                (actual, str(net), now_ns, ticker)
            )

    def risk_state(self, now_ns: int) -> dict:
        day = datetime.fromtimestamp(now_ns / NS, timezone.utc).date()
        with self._mutex, self._open() as db:
            rows = [dict(x) for x in db.execute(
                "SELECT * FROM attempts ORDER BY created_ns DESC"
            )]
        losses = Decimal("0")
        open_exposure = Decimal("0")
        streak = 0
        streak_done = False
        for row in rows:
            if row["status"] == "PAPER_OPEN":
                open_exposure += decimal(row["debit"])
            if row["status"] == "SETTLED":
                net = decimal(row["net_profit"])
                settled_day = datetime.fromtimestamp(
                    row["settled_ns"] / NS, timezone.utc
                ).date()
                if settled_day == day and net < 0:
                    losses -= net
                if not streak_done:
                    if net < 0:
                        streak += 1
                    else:
                        streak_done = True
        return {
            "daily_realized_losses": str(losses),
            "open_worst_case": str(open_exposure),
            "consecutive_losses": streak,
            "remaining_daily_risk": str(max(Decimal("0"), DAILY_STOP - losses - open_exposure)),
        }

    def snapshot(self, limit: int = 60) -> list[dict]:
        with self._mutex, self._open() as db:
            rows = [dict(x) for x in db.execute(
                "SELECT * FROM attempts ORDER BY created_ns DESC LIMIT ?",
                (min(max(limit, 1), 200),)
            )]
        for row in rows:
            stored = row.pop("diagnostics_json", None)
            try:
                row["diagnostics"] = json.loads(stored) if stored is not None else None
            except (TypeError, ValueError):
                row["diagnostics"] = None
        return rows


class PublicMarketClient:
    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self.client = httpx.Client(
            base_url=API_ROOT, timeout=3.0, transport=transport,
            follow_redirects=False
        )

    def quote(self, ticker: str) -> tuple[dict, dict]:
        if not ticker.startswith("KXBTC15M-") or not ticker.replace("-", "").isalnum():
            raise EntryRejected("invalid ticker")
        market = self.client.get(f"/markets/{ticker}")
        market.raise_for_status()
        orderbook = self.client.get(f"/markets/{ticker}/orderbook")
        orderbook.raise_for_status()
        return market.json(), orderbook.json()

    def close(self) -> None:
        self.client.close()


class PaperTrader:
    """Thread-safe enable toggle; persisted attempts prevent repeat entry on restart."""
    def __init__(self, bootstrap_root: Path, *, client: PublicMarketClient | None = None):
        self.root = bootstrap_root.resolve()
        self.ledger = PaperLedger(self.root / "trading" / "paper.sqlite3")
        self.market_client = client or PublicMarketClient()
        self._lock = threading.Lock()
        self._enabled = False  # Every restart returns to safe disabled state.

    def enable(self, enabled: bool) -> None:
        with self._lock:
            self._enabled = bool(enabled)

    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    def status(self) -> dict:
        state = self.ledger.risk_state(time.time_ns())
        return {
            "mode": "PAPER_ONLY",
            "live_order_submission": False,
            "fractional_live_eligibility_verified": False,
            "enabled": self.enabled(),
            "max_all_in_per_market": "0.10",
            "daily_loss_limit": str(DAILY_STOP),
            "min_estimated_net_edge": "0.005",
            "loss_policy": "max-stake + daily stop + 3-loss pause; no untested price stop",
            "risk": state,
            "recent_attempts": self.ledger.snapshot(),
        }

    def poll_once(self, now_ns: int | None = None) -> None:
        now_ns = time.time_ns() if now_ns is None else now_ns
        forecasts = _forecasts(self.root)
        indexed = {f.market_ticker: f for f in forecasts}
        # Settlement uses the application's already-saved official Kalshi outcome.
        for position in self.ledger.open_positions():
            forecast = indexed.get(position["ticker"])
            if forecast is None:
                continue
            try:
                settlement = _load_saved_settlement(self.root, forecast)
            except SettlementIntegrityError:
                continue
            if settlement is not None:
                self.ledger.settle(position["ticker"], settlement.actual_side, now_ns)

        if not self.enabled():
            return
        for forecast in forecasts:
            if not (
                forecast.prediction_ts_ns <= now_ns
                <= forecast.prediction_ts_ns + 20 * NS
            ):
                continue
            if not self.ledger.reserve(forecast.market_ticker, forecast.forecast, now_ns):
                continue
            try:
                if forecast.forecast == "SKIP":
                    raise EntryRejected("V5 final forecast SKIP")
                risk = self.ledger.risk_state(now_ns)
                if risk["consecutive_losses"] >= 3:
                    raise EntryRejected("three consecutive paper losses")
                if decimal(risk["remaining_daily_risk"]) < Decimal("0.10"):
                    # Below full market risk cap: fail closed, never increase stake.
                    raise EntryRejected("daily loss circuit breaker")
                market, book = self.market_client.quote(forecast.market_ticker)
                plan = plan_locked_entry(forecast, market, book, time.time_ns())
                self.ledger.finish(forecast.market_ticker, status="PAPER_OPEN", plan=plan)
            except (EntryRejected, httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                diagnostics = exc.diagnostics if isinstance(exc, EntryRejected) else None
                self.ledger.finish(
                    forecast.market_ticker, status="SKIP",
                    reason=str(exc)[:180], diagnostics=diagnostics,
                )
            except Exception:
                # Unknown state is terminal; never blindly resubmit.
                self.ledger.finish(forecast.market_ticker, status="ERROR",
                                   reason="unexpected paper engine failure")

    def run_forever(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.poll_once()
            except Exception:
                # Foreground server keeps running; ledger is fail-closed.
                pass
            stop.wait(1.0)
