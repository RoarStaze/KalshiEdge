"""Offline quoted economics for KXBTC15M final forecasts.

This is NOT an execution simulator. A best ask can be stale or lack size;
no price/fee in this report is verified as an executed AutoTrade fill.
Forecasted gross value and hypothetical settlement are deliberately separate
from the exchange-reconciled AutoTrade P/L ledger.
"""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal, InvalidOperation
import json
from typing import Any, Mapping, Sequence

CENT = Decimal("0.000001")
ZERO = Decimal("0")
ONE = Decimal("1")
TOLERANCE = Decimal("0.000001")


def _decimal(value: Any) -> Decimal | None:
    if type(value) not in (int, float, str):
        return None
    try:
        num = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return num if num.is_finite() else None


def _fmt(value: Decimal) -> str:
    return format(value.quantize(CENT), "f")


def _fee(value: str) -> Decimal:
    if type(value) is not str:
        raise ValueError("Hypothetical fee must be a dollar string")
    result = _decimal(value)
    if result is None or result < ZERO or result > ONE:
        raise ValueError("Hypothetical fee per contract must be $0 through $1")
    return result


def _probability(row: Mapping[str, Any]) -> Decimal | None:
    yes = _decimal(row.get("p_yes"))
    no = _decimal(row.get("p_no"))
    if (yes is None or no is None or not ZERO <= yes <= ONE
            or not ZERO <= no <= ONE or abs(yes + no - ONE) > TOLERANCE):
        return None
    return yes


def _quote(row: Mapping[str, Any]) -> tuple[Decimal, Decimal] | None:
    yes_bid = _decimal(row.get("kalshi_yes_bid"))
    yes_ask = _decimal(row.get("kalshi_yes_ask"))
    if (yes_bid is None or yes_ask is None or not ZERO <= yes_bid <= yes_ask <= ONE):
        return None
    return yes_bid, yes_ask


def audit_final_quote_economics(
    forecasts: Sequence[Mapping[str, Any]],
    *,
    assumed_fee_per_contract: str = "0",
) -> dict[str, Any]:
    """Descriptive, one-hypothetical-contract market quote analysis.

    Only a directional immutable final decision or a specific
    low-confidence SKIP is a candidate. For ABOVE, quoted side ask is YES ask.
    For BELOW, quoted side ask is 1 - YES bid. Fees/slippage/depth/filled
    quantity are UNKNOWN unless the caller explicitly adds a fee scenario,
    which remains an *assumption*, not an exchange receipt.
    """
    hypothetical_fee = _fee(assumed_fee_per_contract)
    if not isinstance(forecasts, (list, tuple)):
        raise TypeError("forecast history must be a sequence of records")

    tickers = [
        row.get("market_ticker") for row in forecasts
        if isinstance(row, Mapping)
        and isinstance(row.get("market_ticker"), str)
        and row["market_ticker"]
    ]
    counts = Counter(tickers)
    duplicates = sum(n for n in counts.values() if n > 1)
    candidates = invalid_probability = invalid_quote = excluded_skip = 0
    pending = invalid_outcome = 0
    settled_signal_count = settled_skips = 0
    signal_realized_sum = signal_model_gross_sum = ZERO
    signal_model_net_sum = signal_hypothetical_net_sum = ZERO
    opportunities: list[dict[str, Any]] = []

    for row in forecasts:
        if not isinstance(row, Mapping):
            continue
        ticker = row.get("market_ticker")
        if not isinstance(ticker, str) or not ticker or counts[ticker] > 1:
            continue
        forecast = row.get("forecast")
        is_signal = forecast in ("ABOVE", "BELOW")
        is_low_confidence_skip = (
            forecast == "SKIP"
            and row.get("reason") == "CONFIDENCE_BELOW_69_PERCENT"
        )
        if not is_signal and not is_low_confidence_skip:
            if forecast == "SKIP":
                excluded_skip += 1
            continue
        candidates += 1

        p_yes = _probability(row)
        if p_yes is None:
            invalid_probability += 1
            continue
        quote = _quote(row)
        if quote is None:
            invalid_quote += 1
            continue

        yes_bid, yes_ask = quote
        side = forecast if is_signal else (
            "ABOVE" if p_yes >= Decimal("0.5") else "BELOW"
        )
        p_win = p_yes if side == "ABOVE" else ONE - p_yes
        quoted_ask = yes_ask if side == "ABOVE" else ONE - yes_bid
        model_gross_edge = p_win - quoted_ask
        model_net_edge = model_gross_edge - hypothetical_fee

        settled = row.get("settlement_status") == "SETTLED"
        actual = row.get("actual_side")
        valid_settlement = settled and actual in ("ABOVE", "BELOW")
        hypothetical_gross = None
        hypothetical_net = None
        if valid_settlement:
            hypothetical_gross = (
                ONE if actual == side else ZERO
            ) - quoted_ask
            hypothetical_net = hypothetical_gross - hypothetical_fee
            if is_signal:
                settled_signal_count += 1
                signal_realized_sum += hypothetical_gross
                signal_model_gross_sum += model_gross_edge
                signal_model_net_sum += model_net_edge
                signal_hypothetical_net_sum += hypothetical_net
            else:
                settled_skips += 1
        elif settled:
            invalid_outcome += 1
        else:
            pending += 1

        opportunities.append({
            "ticker": ticker,
            "model_version": row.get("model_version"),
            "disposition": "FORECASTED" if is_signal else "SKIPPED_LOW_CONFIDENCE",
            "chosen_side": side,
            "model_win_probability": _fmt(p_win),
            "kalshi_yes_bid_dollars": _fmt(yes_bid),
            "kalshi_yes_ask_dollars": _fmt(yes_ask),
            "quoted_side_ask_dollars": _fmt(quoted_ask),
            "model_gross_edge_per_contract": _fmt(model_gross_edge),
            "assumed_fee_per_contract_dollars": _fmt(hypothetical_fee),
            "model_net_edge_per_contract_after_assumed_fee": _fmt(model_net_edge),
            "officially_settled": valid_settlement,
            "hypothetical_gross_settlement_per_contract": (
                _fmt(hypothetical_gross)
                if hypothetical_gross is not None else None
            ),
            "hypothetical_net_settlement_per_contract_after_assumed_fee": (
                _fmt(hypothetical_net)
                if hypothetical_net is not None else None
            ),
        })

    verified_signals = settled_signal_count
    return {
        "report": "KXBTC15M_READ_ONLY_FINAL_QUOTE_ECONOMICS_V1",
        "scope": "IMMUTABLE_FINAL_CHECKPOINT_QUOTES_ONLY",
        "not_executable": True,
        "verified_fills": False,
        "verified_profitability": False,
        "summary": {
            "rows_examined": len(forecasts),
            "duplicate_records_excluded": duplicates,
            "eligible_candidates": candidates,
            "valid_quote_candidates": len(opportunities),
            "invalid_probabilities": invalid_probability,
            "missing_or_invalid_quotes": invalid_quote,
            "excluded_unhealthy_skips": excluded_skip,
            "pending_settlements": pending,
            "invalid_settlement_outcomes": invalid_outcome,
            "settled_directional_quotes": settled_signal_count,
            "settled_counterfactual_skip_quotes": settled_skips,
            "assumed_fee_per_contract_dollars": _fmt(hypothetical_fee),
            "model_gross_edge_sum_dollars": (
                _fmt(signal_model_gross_sum) if verified_signals else None
            ),
            "model_net_edge_sum_after_assumed_fee_dollars": (
                _fmt(signal_model_net_sum) if verified_signals else None
            ),
            "hypothetical_gross_settlement_sum_dollars": (
                _fmt(signal_realized_sum) if verified_signals else None
            ),
            "hypothetical_net_settlement_sum_after_assumed_fee_dollars": (
                _fmt(signal_hypothetical_net_sum) if verified_signals else None
            ),
            "sum_scope": (
                "ONE HYPOTHETICAL CONTRACT PER SETTLED DIRECTIONAL FORECAST;"
                " NOT VERIFIED ORDER FILLS, REALIZED P/L, OR A TRADABLE PORTFOLIO"
            ),
        },
        "opportunities": opportunities,
        "warning": (
            "This is not actual trading profit, not verified fills, not a "
            "depth-aware execution backtest and not net performance. "
            "Quoted side asks were observed at the immutable forecast checkpoint "
            "but may not have been executable at the recorded size or later time. "
            "Default fee=0 gives gross, optimistically cost-free results; "
            "any nonzero fee is explicitly assumed, never verified. "
            "SKIP forecasts are counterfactuals. Never select a model, "
            "increase stakes or loosen a trading gate based on these "
            "retrospective selected-market results."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    from .config import BootstrapSettings
    from .final_history import read_final_history

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--assumed-fee-per-contract", default="0",
        help="Hypothetical fixed fee PER contract in dollars; not actual Kalshi fee.",
    )
    parser.add_argument(
        "--recent", type=int, default=20,
        help="Number of record details to print; summary always uses all rows.",
    )
    args = parser.parse_args(argv)
    if args.recent < 0:
        parser.error("--recent must be nonnegative")
    report = audit_final_quote_economics(
        read_final_history(BootstrapSettings().bootstrap_dir)["forecasts"],
        assumed_fee_per_contract=args.assumed_fee_per_contract,
    )
    count = len(report["opportunities"])
    report["opportunities"] = report["opportunities"][:args.recent]
    report["detail_rows_displayed"] = len(report["opportunities"])
    report["total_valid_quote_opportunities"] = count
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
