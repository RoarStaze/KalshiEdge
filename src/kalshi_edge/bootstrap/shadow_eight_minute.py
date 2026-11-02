"""Retrospective 480-second shadow from actual, timestamped 1Hz observations.

This does not reconstruct an immutable 480s forecast. It matches past 240s
market settlements against the EARLIEST archived live prediction at the 480s
checkpoint. Nothing can overwrite a final forecast or execute an order.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import argparse
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from .final_forecast import FINAL_CHECKPOINT_SECONDS, LEGACY_FINAL_CHECKPOINT_SECONDS, MAX_CHECKPOINT_LATENESS_NS, MINIMUM_FINAL_CONFIDENCE, NS
from .forward_monitor import evaluate_forward_reliability
from .live import LivePredictionRecord


def _close_ns(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return round(parsed.timestamp() * NS)
    except (ValueError, TypeError, OverflowError):
        return None


def _valid_p(record: LivePredictionRecord) -> bool:
    p, no, confidence = record.p_yes, record.p_no, record.confidence
    if not all(type(x) in (int, float) and math.isfinite(x) for x in (p, no, confidence)):
        return False
    if not (0 <= p <= 1 and 0 <= no <= 1
            and 0 <= confidence <= 1 and abs(p + no - 1) <= 1e-6):
        return False
    side = "ABOVE" if p >= .5 else "BELOW"
    return (record.predicted_side == side
            and abs(confidence - 2 * abs(p - .5)) <= .005)


def _report(
    source: Path,
    settled_rows: Sequence[Mapping[str, Any]],
    *, max_markets: int,
    min_sample: int,
    include_replay_rows: bool = False,
) -> dict[str, Any]:
    # Only old 240s records with verifiably official settled labels. New 480s
    # records already have direct immutable evidence in checkpoint_confidence.
    candidates: list[tuple[int, str, Mapping[str, Any]]] = []
    for row in settled_rows:
        if not isinstance(row, Mapping):
            continue
        if row.get("checkpoint_seconds", LEGACY_FINAL_CHECKPOINT_SECONDS) != LEGACY_FINAL_CHECKPOINT_SECONDS:
            continue
        if (row.get("settlement_status") != "SETTLED"
                or row.get("actual_side") not in ("ABOVE", "BELOW")):
            continue
        ticker = row.get("market_ticker")
        close = _close_ns(row.get("market_close"))
        if not isinstance(ticker, str) or not ticker or close is None:
            continue
        candidates.append((close, ticker, row))

    by_ticker = Counter(ticker for _close, ticker, _row in candidates)
    duplicates = sum(n for n in by_ticker.values() if n > 1)
    selected = sorted(
        (item for item in candidates if by_ticker[item[1]] == 1),
        key=lambda item: (item[0], item[1]),
        reverse=True,
    )[:max_markets]
    targets = {
        ticker: (close - FINAL_CHECKPOINT_SECONDS * NS, close, original)
        for close, ticker, original in selected
    }

    days = {
        datetime.fromtimestamp(due / NS, timezone.utc).date().isoformat()
        for due, _close, _original in targets.values()
    }

    earliest: dict[str, tuple[int, LivePredictionRecord]] = {}
    ambiguous: set[str] = set()
    malformed_candidate_lines = 0
    for day in sorted(days):
        path = source / "predictions" / day / "predictions.jsonl"
        if not path.is_file():
            continue
        try:
            with path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    try:
                        obj = json.loads(line)
                    except (ValueError, UnicodeDecodeError):
                        malformed_candidate_lines += 1
                        continue
                    if not isinstance(obj, dict):
                        continue
                    ticker = obj.get("market_ticker")
                    if ticker not in targets:
                        continue
                    ts = obj.get("prediction_ts_ns")
                    if type(ts) is not int:
                        malformed_candidate_lines += 1
                        continue
                    due, close, _old = targets[ticker]
                    if not (due <= ts <= due + MAX_CHECKPOINT_LATENESS_NS and ts < close):
                        continue
                    try:
                        live = LivePredictionRecord.model_validate(obj)
                    except ValueError:
                        malformed_candidate_lines += 1
                        continue
                    earlier = earliest.get(ticker)
                    if earlier is None or ts < earlier[0]:
                        earliest[ticker] = (ts, live)
                        ambiguous.discard(ticker)
                    elif ts == earlier[0]:
                        ambiguous.add(ticker)
        except OSError:
            # Missing/unreadable journals are never converted into invented
            # 480s decisions or healthy model predictions.
            malformed_candidate_lines += 1

    projected = []
    missing = unhealthy = low = directional = 0
    reasons = Counter()
    for close, ticker, old in selected:
        hit = earliest.get(ticker)
        if hit is None:
            missing += 1
            continue
        if ticker in ambiguous:
            reasons["AMBIGUOUS_CHECKPOINT_OBSERVATIONS"] += 1
            continue
        ts, live = hit
        due = close - FINAL_CHECKPOINT_SECONDS * NS
        seconds_remaining = (close - ts) / NS
        if (live.seconds_remaining is None
                or abs(live.seconds_remaining - seconds_remaining) > .01
                or live.market_ticker != ticker
                or not live.feed_quality.healthy
                or live.status != "OK"
                or not _valid_p(live)):
            unhealthy += 1
            reasons[live.reason or "INVALID_OR_UNHEALTHY_CHECKPOINT_OBSERVATION"] += 1
            continue

        assert live.p_yes is not None and live.p_no is not None
        assert live.confidence is not None
        if live.confidence < MINIMUM_FINAL_CONFIDENCE:
            forecast = "SKIP"
            reason = "CONFIDENCE_BELOW_69_PERCENT"
            low += 1
        else:
            forecast = live.predicted_side
            reason = None
            directional += 1

        projected.append({
            "market_ticker": ticker,
            "market_close": old["market_close"],
            "checkpoint_seconds": FINAL_CHECKPOINT_SECONDS,
            "forecast": forecast,
            "reason": reason,
            "settlement_status": "SETTLED",
            "actual_side": old["actual_side"],
            "p_yes": live.p_yes,
            "p_no": live.p_no,
            "confidence": live.confidence,
            "model_version": live.model_version or "UNKNOWN",
            "model_hash": live.model_hash,
            # Original same-tick observations for research-only challenger.
            # Never reconstruct a later BRTI value or a new market quote.
            "strike": live.strike,
            "brti_value": live.brti_value,
            "brti_source_ts_ns": live.brti_source_ts_ns,
            "kalshi_yes_bid": live.kalshi_yes_bid,
            "kalshi_yes_ask": live.kalshi_yes_ask,
            "source": "ARCHIVED_CONTINUOUS_PREDICTIONS_NOT_FINAL_LOCKS",
            "recorded_prediction_ts_ns": ts,
            "scheduled_checkpoint_ts_ns": due,
        })
    reliable = evaluate_forward_reliability(projected, min_sample=min_sample)
    with_tick = len(earliest)
    return {
        **({"replay_rows": sorted(projected, key=lambda row: (row["market_close"], row["market_ticker"]))}
           if include_replay_rows else {}),
        "report": "KXBTC15M_LEGACY_480S_REPLAY_V1",
        "source": "ARCHIVED_CONTINUOUS_PREDICTIONS_NOT_FINAL_LOCKS",
        "status": "RETROSPECTIVE_ONLY" if reliable["markets_scored"]
        else "NO_VALID_480S_REPLAY_EVIDENCE",
        "selected_legacy_settled_markets": len(selected),
        "duplicate_legacy_labels_excluded": duplicates,
        "markets_with_on_time_observation": with_tick,
        "markets_without_on_time_observation": missing,
        "ambiguous_earliest_observations": len(ambiguous),
        "unhealthy_or_invalid_observations": unhealthy,
        "hypothetical_480_directional_decisions": directional,
        "hypothetical_480_low_confidence_skips": low,
        "replay_skip_reasons": dict(sorted(reasons.items())),
        "malformed_json_or_prediction_lines": malformed_candidate_lines,
        "reliability": reliable,
        "live_orders_submitted": False,
        "independent_validation": False,
        "warning": (
            "This is not an immutable historical final forecast, not an executed "
            "AutoTrade, and not independent validation. It uses recorded live "
            "predictions ONLY at the original 480s observation window paired "
            "with official later settlement. An absent or unhealthy first "
            "observation stays absent, never replaced with a later winning "
            "prediction. The replay may suffer archival coverage and selection "
            "bias; it cannot justify increasing model confidence, changing "
            "thresholds, or claiming profitable trading."
        ),
    }


def replay_eight_minute_history(
    root: Path,
    history: Sequence[Mapping[str, Any]],
    *, max_markets: int = 100,
    min_sample: int = 100,
    include_replay_rows: bool = False,
) -> dict[str, Any]:
    if type(max_markets) is not int or not 1 <= max_markets <= 2000:
        raise ValueError("max_markets must be a positive integer <=2000")
    if type(min_sample) is not int or min_sample < 1:
        raise ValueError("min_sample must be a positive integer")
    if type(include_replay_rows) is not bool:
        raise ValueError("include_replay_rows must be boolean")
    return _report(Path(root), history, max_markets=max_markets, min_sample=min_sample,
                   include_replay_rows=include_replay_rows)


def main(argv: list[str] | None = None) -> int:
    from .config import BootstrapSettings
    from .final_history import read_final_history

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--last-markets", type=int, default=100)
    args = parser.parse_args(argv)
    root = BootstrapSettings().bootstrap_dir
    result = replay_eight_minute_history(
        root, read_final_history(root)["forecasts"], max_markets=args.last_markets,
    )
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
