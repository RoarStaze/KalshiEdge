"""Read-only full local AutoTrade journal and Kalshi settlement reconciliation.

No authenticated API calls are made without the explicit --live flag.
Never exposes credentials, submits, changes, retries, or cancels an order.
Historical Kalshi settlements can be archived out of the live API, and those
markets remain UNVERIFIED rather than being assigned an invented P/L.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from typing import Any

from .autotrade_history import _FIELDS
from .autotrade_pnl import enrich_history
from .autotrade_scorecard import summarize_autotrade_performance


def _load_journal_readonly(path: Path) -> list[dict]:
    """Load all attempts and stages from SQLite without schema migrations."""
    if not path.is_file():
        return []
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5.0)) as db:
        db.row_factory = sqlite3.Row
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        if "auto_trade_attempts" not in tables:
            raise ValueError("Missing AutoTrade attempts table")
        attempts = db.execute("""
            SELECT ticker, attempted_at_ns, outcome, amount, confidence,
                   status, order_id, fill_count
            FROM auto_trade_attempts
            ORDER BY attempted_at_ns DESC, ticker DESC
        """).fetchall()
        stages = []
        if "auto_trade_stages" in tables:
            stages = db.execute("""
                SELECT * FROM auto_trade_stages
                ORDER BY attempted_at_ns,
                    CASE stage WHEN 'EARLY_BUY' THEN 0
                    WHEN 'EARLY_BUY_2' THEN 1
                    WHEN 'REVERSAL_SELL' THEN 2
                    WHEN 'FINAL_BUY' THEN 3 ELSE 4 END
            """).fetchall()
    by_ticker: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for stage in stages:
        item = dict(stage)
        if item.get("status") == "ATTEMPTED":
            item["status"] = "UNKNOWN"
        by_ticker[item["ticker"]].append(item)
    results = []
    for raw in attempts:
        row = dict(zip(_FIELDS, raw))
        if row["status"] == "ATTEMPTED":
            row["status"] = "UNKNOWN"
        row["stages"] = by_ticker.get(row["ticker"], [])
        results.append(row)
    # An orphan stage is a data-integrity defect, not harmless accounting noise.
    if any(ticker not in {r["ticker"] for r in results} for ticker in by_ticker):
        raise ValueError("Orphan exchange stage has no parent journal attempt")
    return results


def audit_autotrade_profit(root: Path, *, client=None) -> dict[str, Any]:
    """Audit *every* local market, preserving unknown and archived outcomes."""
    root = Path(root).resolve()
    entries = _load_journal_readonly(
        root / "data" / "autotrade_history.sqlite3"
    )
    # The strict API variant requires both settled position quantities.
    reconciled = enrich_history(
        entries, client=client, require_settlement_counts=True
    )
    score = summarize_autotrade_performance(reconciled)
    verified = score["verified_settled_markets"]
    no_fill = score["no_fill_markets"]
    complete = (
        bool(entries) and verified + no_fill == len(entries)
        and score["unverified_filled_markets"] == 0
        and score["other_unverified_markets"] == 0
        and score["duplicate_records_excluded"] == 0
    )
    return {
        "report": "KALSHIEDGE_FULL_AUTOTRADE_NET_PROFIT_AUDIT_V1",
        "scope": (
            "LOCAL_AUTOTRADE_JOURNAL_WITH_LIVE_SETTLEMENT_EVIDENCE"
            if client is not None else "LOCAL_AUTOTRADE_JOURNAL_ONLY"
        ),
        "status": "COMPLETE_FOR_LOCAL_JOURNAL" if complete else "PARTIAL_OR_UNVERIFIED",
        "journal_market_count": len(entries),
        "verified_settled_markets": verified,
        "unverified_filled_markets": score["unverified_filled_markets"],
        "other_unverified_markets": score["other_unverified_markets"],
        "no_fill_markets": no_fill,
        "duplicate_records_excluded": score["duplicate_records_excluded"],
        "verified_net_pnl_dollars": score["net_pnl_dollars"],
        "verified_gains_dollars": score["gross_gains_dollars"],
        "verified_losses_dollars": score["gross_losses_dollars"],
        "complete_for_journal_markets": complete,
        "live_exchange_checked": client is not None,
        "limitations": (
            "Only the available LOCAL AutoTrade journal is in scope; it is not "
            "all-account or lifetime profitability. Strict reconciliation "
            "requires journaled fill quantities, all account fills for the "
            "market, live YES/NO settlement holdings and actual fill fees. "
            "Kalshi archives older settlements separately, without identical "
            "settlement fields, so archived records remain unverified. "
            "MIXED, UNKNOWN, incomplete pagination and missing records never "
            "contribute to verified net P/L. No future profitability implied."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    from kalshi_edge.bootstrap.config import BootstrapSettings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path,
                        default=BootstrapSettings().bootstrap_dir)
    parser.add_argument(
        "--live", action="store_true",
        help="Explicitly allow signed GET settlement/fill reads only (never trade)",
    )
    args = parser.parse_args(argv)

    # Default mode does not load secrets and does not contact Kalshi.
    if not args.live:
        report = audit_autotrade_profit(args.root)
    else:
        from .manual_trade import KalshiOrderClient
        from .vault import CredentialVault

        source = KalshiOrderClient(CredentialVault())
        try:
            # Expose only two allowed GETs to the auditing function.
            class ReadOnlyAccount:
                def get(self, path, *, params=None):
                    if path not in ("/portfolio/settlements", "/portfolio/fills"):
                        raise ValueError("Non-audit account access blocked")
                    return source.get(path, params=params)

            report = audit_autotrade_profit(args.root, client=ReadOnlyAccount())
        finally:
            source.close()
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
