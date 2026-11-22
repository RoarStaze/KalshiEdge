"""Conservative, descriptive entry veto for demonstrably adverse *past* taker signals.

No threshold is fitted here. Only historical *closed* market quotes are
eligible; active market snapshots, later outcomes and unfillable maker
scenarios cannot drive a live/paper decision. Negative-evidence vetoes
reduce exposure; they do not certify a positive edge on unvetoed trades.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
import sqlite3

D = Decimal
NS = 1_000_000_000
HORIZON_NS = 20 * NS
JITTER_NS = 3 * NS
MAX_EVENTS = 5000
MIN_MARKETS = 10
MIN_OBSERVED = 60
MIN_COVERAGE = D("0.80")
MAX_WIN_RATE = D("0.30")
LOSS_THRESHOLD = D("-0.02")
SLIPPAGE = D("0.01")


def _empty() -> dict:
    return {
        "status": "INSUFFICIENT_HISTORY",
        "entry_veto": False,
        "observed": 0, "candidates": 0,
        "missing_followup": 0, "market_count": 0,
        "profitable": 0, "mean_net_cents_per_contract": None,
        "win_rate": None,
    }


def evaluate_historical_entry_evidence(tape_path: Path, now_ns: int) -> dict:
    """Veto proven-adverse taker momentum only, using strictly PAST markets.

    Requires 10 distinct closed markets, 60 matched observations, 80%
    forward quote coverage and <30% positive net observations alongside
    <-2c mean net return at 20s. These are safety heuristics; correlated
    observations and fees assumptions preclude significance/edge claims.
    """
    if type(now_ns) is not int or now_ns <= 0:
        raise ValueError("Invalid evidence cutoff timestamp")
    result = {
        "kind": "PAST_CLOSED_MARKET_TAKER_MARKOUT_VETO",
        "interpretation": "DESCRIPTIVE_ONLY_NO_VERIFIED_EDGE",
        "as_of_ns": now_ns,
        "horizon_seconds": 20,
        "min_closed_markets": MIN_MARKETS,
        "min_observed": MIN_OBSERVED,
        "sides": {"UP": _empty(), "DOWN": _empty()},
        "warning": (
            "Only previously CLOSED markets. Negative-evidence veto is a "
            "conservative heuristic, not a profitable entry selector or "
            "walk-forward proven edge. Missing quotes are not filled in."
        ),
    }
    path = Path(tape_path).resolve()
    if not path.is_file():
        return result
    from .strat_paper import standard_taker_fee_estimate
    with sqlite3.connect(str(path), timeout=5) as db:
        db.row_factory = sqlite3.Row
        source = db.execute(
            """SELECT ticker, observed_ns, close_ns, remaining_seconds,
                      up_ask, down_ask, up_signal, down_signal, position_side
               FROM quotes
               WHERE close_ns < ?
                 AND (up_signal='NEW_FAST_UP_MOMENTUM'
                   OR down_signal='NEW_FAST_DOWN_MOMENTUM')
               ORDER BY observed_ns ASC LIMIT ?""",
            (now_ns, MAX_EVENTS+1),
        ).fetchall()
        if len(source) > MAX_EVENTS:
            result["truncated"] = True
            # When the universe exceeds capacity, do not apply the veto on
            # a potentially cherry-picked/truncated historical subset.
            return result
        result["truncated"] = False
        sums = {"UP": D("0"), "DOWN": D("0")}
        market_ids: dict[str, set[str]] = {"UP": set(), "DOWN": set()}
        last_seen: dict[tuple[str, str], int] = {}
        for row in source:
            if (row["position_side"] is not None
                    or row["remaining_seconds"] < 120):
                continue
            for side in ("UP", "DOWN"):
                if row[f"{side.lower()}_signal"] != f"NEW_FAST_{side}_MOMENTUM":
                    continue
                t = int(row["observed_ns"])
                if t - last_seen.get((row["ticker"], side), -10**30) < 5*NS:
                    continue
                try:
                    ask = D(row[f"{side.lower()}_ask"])
                    entry = ask + SLIPPAGE
                except (TypeError, InvalidOperation):
                    continue
                if not (D("0.65") <= ask <= D("0.85") and
                        D("0") < entry <= D("0.85")):
                    continue
                last_seen[(row["ticker"], side)] = t
                out = result["sides"][side]
                out["candidates"] += 1
                target = t + HORIZON_NS
                if target >= row["close_ns"]:
                    out["missing_followup"] += 1
                    continue
                quote = db.execute(
                    """SELECT up_bid, down_bid FROM quotes
                       WHERE ticker=? AND observed_ns>=? AND observed_ns<=?
                         AND observed_ns<?
                       ORDER BY observed_ns ASC LIMIT 1""",
                    (row["ticker"], target, target+JITTER_NS, row["close_ns"]),
                ).fetchone()
                if quote is None:
                    out["missing_followup"] += 1
                    continue
                try:
                    sale = D(quote[f"{side.lower()}_bid"]) - SLIPPAGE
                    if not D("0") < sale < D("1"):
                        raise ValueError("no executable future bid")
                    net = (sale - entry
                           - standard_taker_fee_estimate(entry, D("1"))
                           - standard_taker_fee_estimate(sale, D("1")))
                except (TypeError, InvalidOperation, ValueError):
                    out["missing_followup"] += 1
                    continue
                out["observed"] += 1
                out["profitable"] += int(net > 0)
                sums[side] += net
                market_ids[side].add(row["ticker"])
        for side in ("UP", "DOWN"):
            out = result["sides"][side]
            out["market_count"] = len(market_ids[side])
            if out["observed"]:
                out["mean_net_cents_per_contract"] = float(
                    (100 * sums[side] / out["observed"]).quantize(D("0.0001"))
                )
                out["win_rate"] = round(out["profitable"] / out["observed"], 4)
            if (out["observed"] >= MIN_OBSERVED
                    and out["market_count"] >= MIN_MARKETS
                    and D(out["observed"]) / max(out["candidates"], 1)
                        >= MIN_COVERAGE
                    and sums[side] / out["observed"] <= LOSS_THRESHOLD
                    and D(out["profitable"]) / out["observed"] <= MAX_WIN_RATE):
                out["status"] = "NEGATIVE_HISTORICAL_TAKER_MARKOUT"
                out["entry_veto"] = True
            elif (out["observed"] >= MIN_OBSERVED
                  and out["market_count"] >= MIN_MARKETS):
                out["status"] = "NONNEGATIVE_NOT_VALIDATED"
    return result
