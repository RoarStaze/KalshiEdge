"""KalshiEdge Profit Intelligence: honest, read-only forecast/execution scorecards.

This module never sends orders, promotes models, modifies AutoTrade stakes, or
treats quotes as fills. It separates all settled 480s model probabilities
(including SKIPs) from the authenticated fee-inclusive AutoTrade ledger.
"""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal, InvalidOperation
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from .autotrade_scorecard import summarize_autotrade_performance


def _prob(value: Any) -> float | None:
    if type(value) not in (str, float, int, Decimal):
        return None
    try:
        x = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return x if math.isfinite(x) and 0 <= x <= 1 else None


def _decimal(value: Any) -> Decimal | None:
    if type(value) not in (str, int, Decimal):
        return None
    try:
        x = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return x if x.is_finite() else None


def _dollars(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.000001")), "f")


def _forecast_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    tickers = [
        row.get("market_ticker") for row in rows
        if isinstance(row, Mapping) and isinstance(row.get("market_ticker"), str)
        and row["market_ticker"]
    ]
    duplicates = Counter(tickers)
    duplicate_rows = sum(count for count in duplicates.values() if count > 1)
    settled = issued = correct = coinflip = quoted = 0
    loss_sum = log_sum = baseline_loss_sum = 0.0
    baseline_n = 0
    gross_model_edge = Decimal("0")
    gross_hypothetical = Decimal("0")
    groups: dict[str, list[float]] = {}

    for row in rows:
        if not isinstance(row, Mapping):
            continue
        ticker = row.get("market_ticker")
        if (not isinstance(ticker, str) or not ticker
                or duplicates[ticker] > 1
                or row.get("checkpoint_seconds") != 480
                or row.get("settlement_status") != "SETTLED"
                or row.get("actual_side") not in ("ABOVE", "BELOW")
                or row.get("forecast") not in ("ABOVE", "BELOW", "SKIP")):
            continue
        yes, no = _prob(row.get("p_yes")), _prob(row.get("p_no"))
        if yes is None or no is None or abs(yes + no - 1) > 1e-6:
            continue
        actual = 1 if row["actual_side"] == "ABOVE" else 0
        is_up = yes >= 0.5
        implied_side = "ABOVE" if is_up else "BELOW"
        if row["forecast"] != "SKIP" and row["forecast"] != implied_side:
            continue

        settled += 1
        loss_sum += (yes - actual) ** 2
        log_sum -= math.log(max(1e-12, yes if actual else 1 - yes))
        prediction_correct = implied_side == row["actual_side"]
        if row["coin_flip_assessment"] if "coin_flip_assessment" in row else None:
            if row.get("coin_flip_assessment") == "HIGH":
                coinflip += 1
        if row["forecast"] != "SKIP":
            issued += 1
            correct += int(prediction_correct)

        side_p = max(yes, no)
        bucket = min(9, int(side_p * 10))
        key = f"{bucket * 10}-{(bucket + 1) * 10}%"
        if key not in groups:
            groups[key] = [0.0, 0.0, 0.0]
        groups[key][0] += 1
        groups[key][1] += side_p
        groups[key][2] += float(prediction_correct)

        bid, ask = _prob(row.get("kalshi_yes_bid")), _prob(row.get("kalshi_yes_ask"))
        if bid is not None and ask is not None and bid <= ask:
            midpoint = (bid + ask) / 2
            baseline_loss_sum += (midpoint - actual) ** 2
            baseline_n += 1
            if row["forecast"] != "SKIP":
                side_ask = ask if is_up else 1 - bid
                gross_model_edge += Decimal(str(side_p)) - Decimal(str(side_ask))
                gross_hypothetical += (
                    Decimal("1") if prediction_correct else Decimal("0")
                ) - Decimal(str(side_ask))
                quoted += 1

    bands = [
        {
            "band": key, "markets": int(values[0]),
            "average_model_win_probability": round(values[1] / values[0], 6),
            "observed_accuracy": round(values[2] / values[0], 6),
        }
        for key, values in sorted(groups.items())
    ]
    ece = (
        sum(abs(b["average_model_win_probability"] - b["observed_accuracy"])
            * b["markets"] for b in bands) / settled if settled else None
    )
    return {
        "scope": "ALL_VERIFIED_SETTLED_480S_PROBABILITIES_INCLUDING_SKIPS",
        "settled_markets": settled,
        "issued_predictions": issued,
        "correct_predictions": correct,
        "model_accuracy": correct / issued if issued else None,
        "coin_flip_high_settled": coinflip,
        "brier_score": loss_sum / settled if settled else None,
        "log_loss": log_sum / settled if settled else None,
        "expected_calibration_error": ece,
        "calibration_bands": bands,
        "market_midpoint_brier": baseline_loss_sum / baseline_n if baseline_n else None,
        "market_midpoint_matched_count": baseline_n,
        "duplicate_tickers_excluded": duplicate_rows,
        "eligible_quote_comparisons": quoted,
        "model_gross_edge_sum_per_one_contract": (
            _dollars(gross_model_edge) if quoted else None
        ),
        "hypothetical_gross_quote_settlement_sum_per_one_contract": (
            _dollars(gross_hypothetical) if quoted else None
        ),
        "quote_warning": (
            "Quote economics assume one contract at recorded checkpoint ask, "
            "before fees; neither a historical fill nor executable P/L. "
            "Midpoint Brier uses only markets with a valid two-sided quote."
        ),
    }


def _execution_metrics(entries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    score = summarize_autotrade_performance(entries)
    pnl = _decimal(score["net_pnl_dollars"])
    timestamps = [
        row["attempted_at_ns"] for row in entries if isinstance(row, Mapping)
        and type(row.get("attempted_at_ns")) is int
        and row["attempted_at_ns"] > 0
    ]
    observed_span = (
        (max(timestamps) - min(timestamps)) / 3_600_000_000_000
        if len(timestamps) >= 2 else 0.0
    )
    rate = None
    # Even a reconciled small sample does not establish repeatable profitability.
    # A rate is merely realized net dollars divided by observable wall-clock span,
    # NOT continuous runtime/market opportunity time or an earnings prediction.
    if (pnl is not None and score["is_complete_for_filled_markets"]
            and score["verified_settled_markets"] >= 2
            and observed_span >= 1.0
            and len(timestamps) == len(entries)):
        rate = _dollars(pnl / Decimal(str(observed_span)))

    return {
        "scope": "RECENT_AUTOTRADE_JOURNAL_ONLY",
        "verified_net_pnl_dollars": score["net_pnl_dollars"],
        "verified_settled_trades": score["verified_settled_markets"],
        "filled_unverified_trades": score["unverified_filled_markets"],
        "no_fill_trades": score["no_fill_markets"],
        "duplicate_records_excluded": score["duplicate_records_excluded"],
        "observed_span_hours": round(observed_span, 6) if timestamps else None,
        "net_per_observed_hour_dollars": rate,
        "verified_settled_pnl_complete": score["is_complete_for_filled_markets"],
        "warning": (
            "Only authenticated, reconciled, settled AutoTrade P/L counts. "
            "Observed wall-clock rate is descriptive, not a forward profit rate; "
            "pending trades and missing timestamps block rate calculations."
        ),
    }


def build_profit_intelligence(
    forecasts: Sequence[Mapping[str, Any]],
    entries: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Join two independently scoped evidence sets without equating quotes and fills."""
    if not isinstance(forecasts, (tuple, list)) or not isinstance(entries, (tuple, list)):
        raise TypeError("Expected forecast and AutoTrade history sequences")
    return {
        "report": "KALSHIEDGE_PROFIT_INTELLIGENCE_V1",
        "status": "EVIDENCE_ONLY",
        "forecast": _forecast_metrics(forecasts),
        "execution": _execution_metrics(entries),
        "ready_to_change_live_model": False,
        "promotion_requirement": (
            "Freeze a candidate and show improved fee-inclusive realized net "
            "value on new independent chronological markets, with calibrated "
            "probabilities, matching quote/size/fills, drawdown and uncertainty. "
            "Historical reports alone never authorize changing the live model."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    from kalshi_edge.bootstrap.config import BootstrapSettings
    from kalshi_edge.bootstrap.final_history import read_final_history
    from .autotrade_history import AutoTradeHistory
    from .autotrade_pnl import enrich_history

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=BootstrapSettings().bootstrap_dir)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    history_file = root / "data" / "autotrade_history.sqlite3"
    # Offline command never connects to an exchange, so unresolved P/L remains unknown.
    if history_file.is_file():
        journal = AutoTradeHistory(history_file)
        entries = journal.recent(limit=100)
        for row in entries:
            row["stages"] = journal.recent_stages(row["ticker"])
        entries = enrich_history(entries, client=None)
    else:
        entries = []
    forecast_rows = read_final_history(root)["forecasts"]
    print(json.dumps(build_profit_intelligence(forecast_rows, entries),
                     indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
