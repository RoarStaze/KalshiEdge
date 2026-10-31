"""Read-only health diagnostics for immutable final KXBTC15M checkpoints.

No trading API access, forecast recalculation, model update, or file mutation.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Sequence

from .config import BootstrapSettings
from .final_forecast import (
    FinalForecastRecord,
    MAX_CHECKPOINT_LATENESS_NS,
    NS,
)


def _two_sided_quote(record: FinalForecastRecord) -> bool:
    bid, ask = record.kalshi_yes_bid, record.kalshi_yes_ask
    return (type(bid) in (int, float) and type(ask) in (int, float)
            and math.isfinite(bid) and math.isfinite(ask)
            and 0 <= bid <= ask <= 1)


def summarize_checkpoint_health(
    records: Sequence[FinalForecastRecord],
    *, window: int = 96,
) -> dict:
    """Summarize last N unique archived market locks, including SKIPs.

    The recorded decision timestamp is not a verified process latency
    measurement; it is the time the inference tick *started*. Thus an
    apparently on-time record cannot prove a final write met the deadline.
    """
    if type(window) is not int or window < 1:
        raise ValueError("window must be a positive integer")
    if any(not isinstance(rec, FinalForecastRecord) for rec in records):
        raise ValueError("expected validated final forecast records")

    counts = Counter(rec.market_ticker for rec in records)
    duplicates = sum(count for count in counts.values() if count > 1)
    unique = [r for r in records if counts[r.market_ticker] == 1]
    ordered = sorted(unique, key=lambda x: (x.scheduled_ts_ns, x.market_ticker))
    recent = ordered[-window:]

    reasons = Counter(
        r.reason or "UNKNOWN_SKIP"
        for r in recent
        if r.forecast == "SKIP"
    )
    quotes = sum(_two_sided_quote(r) for r in recent)
    timely = sum(
        0 <= r.prediction_ts_ns - r.scheduled_ts_ns <= MAX_CHECKPOINT_LATENESS_NS
        for r in recent
    )
    directional = sum(r.forecast in ("ABOVE", "BELOW") for r in recent)
    latest = []
    for record in reversed(recent[-8:]):
        delay_ms = (record.prediction_ts_ns - record.scheduled_ts_ns) / 1_000_000
        latest.append({
            "ticker": record.market_ticker,
            "forecast": record.forecast,
            "reason": record.reason,
            "checkpoint_time_utc": datetime.fromtimestamp(
                record.scheduled_ts_ns / NS, tz=timezone.utc,
            ).isoformat(),
            "prediction_tick_delay_ms": round(delay_ms, 3),
            "yes_bid": record.kalshi_yes_bid if _two_sided_quote(record) else None,
            "yes_ask": record.kalshi_yes_ask if _two_sided_quote(record) else None,
        })

    total = len(recent)
    return {
        "report": "KXBTC15M_IMMUTABLE_FINAL_CHECKPOINT_HEALTH_V1",
        "scope": "RECORDED_FINAL_FORECASTS_ONLY",
        "markets_examined": total,
        "lookback_limit": window,
        "duplicate_records_excluded": duplicates,
        "directional_forecasts": directional,
        "skipped_markets": total - directional,
        "missed_checkpoint": reasons.get("MISSED_240S_CHECKPOINT", 0),
        "low_confidence_skips": reasons.get("CONFIDENCE_BELOW_69_PERCENT", 0),
        "feed_or_integrity_skips": sum(
            count for reason, count in reasons.items()
            if reason not in (
                "MISSED_240S_CHECKPOINT", "CONFIDENCE_BELOW_69_PERCENT",
            )
        ),
        "on_time_checkpoints": timely,
        "valid_two_sided_quotes": quotes,
        "quotes_missing_or_invalid": total - quotes,
        "quote_coverage": quotes / total if total else None,
        "skip_reasons": dict(sorted(reasons.items())),
        "last_markets": latest,
        "warning": (
            "Read-only history of RECORDED forecasts, not a complete market "
            "opportunity census, executable-price or profitable-trade audit. "
            "Tick timestamps do not prove actual wall-clock final-write time. "
            "Never infer a missing market was a successful forecast."
        ),
    }


def read_checkpoint_health(root: Path, *, window: int = 96) -> dict:
    """Read all locally archived immutable decisions, tolerating damaged files."""
    root = Path(root).resolve()
    archived = root / "final_forecasts"
    records = []
    invalid = 0
    for path in archived.glob("*/*.json"):
        try:
            records.append(
                FinalForecastRecord.model_validate_json(
                    path.read_text(encoding="utf-8"),
                )
            )
        except (OSError, ValueError):
            invalid += 1
    report = summarize_checkpoint_health(records, window=window)
    report["invalid_files"] = invalid
    return report


def main() -> int:
    report = read_checkpoint_health(BootstrapSettings().bootstrap_dir)
    print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
