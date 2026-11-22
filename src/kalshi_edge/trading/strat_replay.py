"""Read-only causal markout audit of recorded Strat order-book entry signals.

This is NOT a strategy backtest, forecast, execution simulator, or permission
to trade. It does not assume any unobserved maker fill or future event.
Each result is a descriptive, timestamp-forward taker liquidation markout.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
import sqlite3

D = Decimal
NS = 1_000_000_000
SLIPPAGE = D("0.01")
MAX_CANDIDATES = 5000
REQUIRED_CANDIDATES = 30
FOLLOWUP_TOLERANCE_NS = 3 * NS


def analyze_causal_markouts(
    tape_path: Path, *, horizons: tuple[int, ...] = (2, 5, 10, 20),
) -> dict:
    """Check only FUTURE executable bids, for each previously observed BUY.

    Uses one contract to display comparable net cents across prices. A future
    quote in a subsequent ticker or after market close cannot count. Missing
    observations are counted, never assumed to be wins or losses.
    """
    if (not isinstance(horizons, tuple) or not horizons
            or any(type(x) is not int or x < 1 or x > 120 for x in horizons)
            or len(set(horizons)) != len(horizons)):
        raise ValueError("Invalid Strat replay horizon")
    path = Path(tape_path).resolve()
    result = {
        "kind": "CAUSAL_QUOTE_MARKOUT_AUDIT",
        "interpretation": "DESCRIPTIVE_ONLY_NO_VERIFIED_EDGE",
        "minimum_candidates_for_review": REQUIRED_CANDIDATES,
        "candidate_count": 0,
        "capped_at": MAX_CANDIDATES,
        "truncated": False,
        "horizons": {
            f"{h}s": {
                "observed": 0, "net_profitable": 0,
                "missing_followup": 0,
                "mean_net_cents_per_contract": None,
            } for h in horizons
        },
        "warning": (
            "Descriptive forward quote markouts only; signals are correlated. "
            "Single-contract top-of-book taker fills with 1c simulated slippage "
            "and assumed standard taker fees are not verified executions. "
            "Not a calibrated model, walk-forward validation, trading edge, "
            "or evidence that a maker order would fill."
        ),
    }
    if not path.is_file():
        result["status"] = "NO_CAPTURED_QUOTE_TAPE"
        return result

    # Import here to keep the paper engine's startup independent of replay.
    from .strat_paper import standard_taker_fee_estimate
    with sqlite3.connect(str(path), timeout=5) as db:
        db.row_factory = sqlite3.Row
        source = db.execute(
            """SELECT ticker, observed_ns, close_ns, remaining_seconds,
                      up_ask, down_ask, up_signal, down_signal, position_side
               FROM quotes
               WHERE up_signal='NEW_FAST_UP_MOMENTUM'
                  OR down_signal='NEW_FAST_DOWN_MOMENTUM'
               ORDER BY observed_ns ASC LIMIT ?""",
            (MAX_CANDIDATES + 1,),
        ).fetchall()
        if len(source) > MAX_CANDIDATES:
            result["truncated"] = True
            source = source[:MAX_CANDIDATES]
        last_event_ns: dict[tuple[str, str], int] = {}
        totals = {h: D("0") for h in horizons}
        for event in source:
            if (event["position_side"] is not None
                    or event["remaining_seconds"] < 120
                    or event["close_ns"] <= event["observed_ns"]):
                continue
            for side in ("UP", "DOWN"):
                if event[f"{side.lower()}_signal"] != f"NEW_FAST_{side}_MOMENTUM":
                    continue
                try:
                    ask = D(event[f"{side.lower()}_ask"])
                except (InvalidOperation, TypeError):
                    continue
                entry = ask + SLIPPAGE
                if not D("0.65") <= ask <= D("0.85") or entry > D("0.85"):
                    continue
                key = (event["ticker"], side)
                t = int(event["observed_ns"])
                if t - last_event_ns.get(key, -10**30) < 5 * NS:
                    continue
                last_event_ns[key] = t
                result["candidate_count"] += 1
                for h in horizons:
                    row = result["horizons"][f"{h}s"]
                    target = t + h * NS
                    if target >= event["close_ns"]:
                        row["missing_followup"] += 1
                        continue
                    followup = db.execute(
                        """SELECT observed_ns, up_bid, down_bid FROM quotes
                           WHERE ticker=? AND observed_ns>=? AND observed_ns<=?
                             AND observed_ns<?
                           ORDER BY observed_ns LIMIT 1""",
                        (event["ticker"], target,
                         target + FOLLOWUP_TOLERANCE_NS,
                         event["close_ns"]),
                    ).fetchone()
                    if followup is None:
                        row["missing_followup"] += 1
                        continue
                    try:
                        sale = D(followup[f"{side.lower()}_bid"]) - SLIPPAGE
                        if not D("0") < sale < D("1"):
                            raise ValueError("bad future bid")
                        net = (sale - entry
                               - standard_taker_fee_estimate(entry, D("1"))
                               - standard_taker_fee_estimate(sale, D("1")))
                    except (InvalidOperation, TypeError, ValueError):
                        row["missing_followup"] += 1
                        continue
                    row["observed"] += 1
                    if net > 0:
                        row["net_profitable"] += 1
                    totals[h] += net
        for h in horizons:
            row = result["horizons"][f"{h}s"]
            if row["observed"]:
                row["mean_net_cents_per_contract"] = float(
                    (totals[h] * 100 / row["observed"]).quantize(D("0.0001"))
                )
    result["status"] = (
        "INSUFFICIENT_CANDIDATES" if result["candidate_count"] < REQUIRED_CANDIDATES
        else "DESCRIPTIVE_SAMPLE_AVAILABLE"
    )
    return result
