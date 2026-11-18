"""Reconstruct *research-only* gold examples from archived market results + OHLC.

No imported Pyth OHLC is a verified Kalshi settlement label. The market close,
480-second checkpoint, bar boundary and 24-hour label delay are explicitly
ASSUMED FOR EXPLORATORY STUDY, not independently verified by current contract
terms. No past Kalshi bid/ask quotes are used as historical as-of features.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import math
from pathlib import Path
from statistics import pstdev
from typing import Any, Sequence

from .audit import SERIES_TICKER, FINAL_CHECKPOINT_SECONDS
from .candle_probe import _load_inventory, GoldCandleProbeError
from .dataset_readiness import _inventory_records
from .macro_context import asof_macro_features
from .oracle_history import inspect_pyth_history_evidence, GoldOracleEvidenceError
from .prospective import _read_hash_snapshot, _write_immutable, _checked_gold_root, GoldCaptureError

NS = 1_000_000_000
FEATURE_LAG_SECONDS = 60
ASSUMED_LABEL_DELAY_SECONDS = 86400
N_HISTORY_BARS = 21
FEATURE_NAMES = (
    "gold_return_1m_bps", "gold_return_3m_bps", "gold_return_5m_bps",
    "gold_return_10m_bps", "gold_return_20m_bps", "gold_volatility_5m_bps",
    "gold_volatility_20m_bps", "gold_range_5m_bps",
    "gold_ma_5_vs_20_bps", "gold_candle_body_bps", "gold_channel_position_20m",
)


class GoldHistoricalResearchError(ValueError):
    """Historical evidence missing, contradictory, forged or unsuitable."""


@dataclass(frozen=True)
class GoldResearchRow:
    ticker: str
    close_ns: int
    decision_ns: int
    feature_end_ns: int
    label_available_proxy_ns: int
    label: int
    source: str
    features: tuple[float, ...]
    lag_returns_bps: tuple[float, ...]
    macro_sentiment: float | None = None
    macro_count: int = 0


def _utc_ns(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return int(dt.timestamp() * NS) if dt.tzinfo is not None else None


def _read_bars(paths: Sequence[Path]) -> tuple[list[int], list[tuple[float, float, float, float]], dict[str, str]]:
    if not paths:
        raise GoldHistoricalResearchError("supply at least one SHA-256-checked Pyth history artifact")
    bars: dict[int, tuple[float, float, float, float]] = {}
    sources: dict[str, str] = {}
    for path in paths:
        try:
            doc = inspect_pyth_history_evidence(Path(path))
            # Already validated shape, positive prices, sorted unique minute stamps.
            payload = doc["raw_pyth_response"]
        except (GoldOracleEvidenceError, OSError, KeyError, TypeError) as exc:
            raise GoldHistoricalResearchError("invalid/untrusted Pyth history artifact") from exc
        digest = sha256(Path(path).read_bytes()).hexdigest()
        if digest in sources.values():
            raise GoldHistoricalResearchError("duplicate Pyth source artifact")
        sources[str(Path(path).resolve())] = digest
        for t, op, hi, lo, cl in zip(
            payload["t"], payload["o"], payload["h"], payload["l"], payload["c"]
        ):
            values = (float(op), float(hi), float(lo), float(cl))
            if t in bars and bars[t] != values:
                raise GoldHistoricalResearchError("contradictory overlapping Pyth minute bars")
            bars[t] = values
    if not bars:
        raise GoldHistoricalResearchError("no OHLC bars")
    ordered = sorted(bars)
    return ordered, [bars[t] for t in ordered], sources


def _features(history: list[tuple[float, float, float, float]]) -> tuple[tuple[float, ...], tuple[float, ...]]:
    closes = [bar[3] for bar in history]
    returns = [(closes[i]/closes[i - 1]-1) * 10000 for i in range(1, len(closes))]
    latest = closes[-1]
    features = (
        returns[-1],
        (latest/closes[-4]-1)*10000,
        (latest/closes[-6]-1)*10000,
        (latest/closes[-11]-1)*10000,
        (latest/closes[0]-1)*10000,
        pstdev(returns[-5:]), pstdev(returns),
        (max(b[1] for b in history[-5:]) - min(b[2] for b in history[-5:]))/latest*10000,
        (sum(closes[-5:])/5 / (sum(closes[-20:])/20)-1)*10000,
        (history[-1][3]/history[-1][0]-1)*10000,
        ((latest-min(b[2] for b in history)) /
         (max(b[1] for b in history)-min(b[2] for b in history)))
        if max(b[1] for b in history) > min(b[2] for b in history) else .5,
    )
    if not all(math.isfinite(v) and abs(v) < 1e9 for v in features + tuple(returns)):
        raise GoldHistoricalResearchError("invalid market feature magnitude")
    return tuple(features), tuple(returns)


def _sample_market(
    ticker: str, versions: list[dict[str, Any]], stamps: list[int],
    prices: list[tuple[float, float, float, float]], macro_events: Sequence[dict[str, Any]],
) -> tuple[GoldResearchRow | None, str | None]:
    reported = {str(v.get("result", "")).lower().strip() for v in versions
                if str(v.get("result", "")).lower().strip() in {"yes", "no"}}
    if len(reported) > 1:
        return None, "CONFLICTING_KALSHI_RESULT"
    if len(reported) != 1 or not any(str(v.get("status", "")).lower() == "finalized" for v in versions):
        return None, "MISSING_FINALIZED_RESULT"
    close_values = {_utc_ns(v.get("close_time")) for v in versions}
    if len(close_values) != 1 or None in close_values:
        return None, "AMBIGUOUS_MARKET_CLOSE_TIME"
    close_ns = next(iter(close_values))
    decision_ns = close_ns - FINAL_CHECKPOINT_SECONDS * NS
    if decision_ns <= 0:
        return None, "INVALID_DECISION_TIME"
    # Conservative assumed bar-start semantics: t+60s end <= decision-60s.
    latest_start = decision_ns // NS - 120
    index = bisect_right(stamps, latest_start) - 1
    if index < N_HISTORY_BARS - 1:
        return None, "INSUFFICIENT_PRIOR_OHLC"
    history_stamps = stamps[index - N_HISTORY_BARS + 1: index + 1]
    if any(b-a != 60 for a, b in zip(history_stamps, history_stamps[1:])):
        return None, "GAPPED_PRIOR_OHLC"
    history = prices[index - N_HISTORY_BARS + 1: index + 1]
    features, lag_returns = _features(history)
    macro = asof_macro_features(macro_events, decision_ns)
    return GoldResearchRow(
        ticker=ticker, close_ns=close_ns, decision_ns=decision_ns,
        feature_end_ns=(history_stamps[-1] + 60) * NS,
        label_available_proxy_ns=close_ns + ASSUMED_LABEL_DELAY_SECONDS * NS,
        label=1 if "yes" in reported else 0,
        source="UNVERIFIED_PYTH_HISTORICAL_OHLC",
        features=features, lag_returns_bps=lag_returns,
        macro_sentiment=macro["sentiment"], macro_count=macro["count"],
    ), None


def inspect_research_dataset(path: Path) -> dict[str, Any]:
    """Verify immutable content hash and fail-closed research-only claims."""
    try:
        doc, _ = _read_hash_snapshot(Path(path))
    except (GoldCaptureError, OSError) as exc:
        raise GoldHistoricalResearchError("research dataset SHA-256 or JSON invalid") from exc
    report = doc.get("report")
    if (doc.get("artifact_kind") != "unverified_gold_historical_research_v14"
            or not isinstance(report, dict)
            or report.get("series_ticker") != SERIES_TICKER
            or report.get("dataset_ready") is not False
            or report.get("eligible_training_rows") != 0
            or report.get("settlement_oracle_verified") is not False
            or report.get("label_status") != "KALSHI_REPORTED_UNVERIFIED"
            or doc.get("feature_names") != list(FEATURE_NAMES)
            or not isinstance(doc.get("research_rows"), list)
            or report.get("candidate_research_rows") != len(doc["research_rows"])):
        raise GoldHistoricalResearchError("unsupported historical dataset readiness claim")
    extract_research_rows(doc)
    return doc


def extract_research_rows(doc: dict[str, Any]) -> list[GoldResearchRow]:
    if not isinstance(doc, dict) or not isinstance(doc.get("research_rows"), list):
        raise GoldHistoricalResearchError("research rows missing")
    result: list[GoldResearchRow] = []
    seen: set[str] = set()
    for raw in doc["research_rows"]:
        try:
            row = GoldResearchRow(**raw)
        except (TypeError, ValueError) as exc:
            raise GoldHistoricalResearchError("invalid historical research row") from exc
        if (not row.ticker.startswith(SERIES_TICKER+"-") or row.ticker in seen
                or not 0 < row.feature_end_ns <= row.decision_ns - FEATURE_LAG_SECONDS*NS
                or not row.decision_ns < row.close_ns
                or row.label_available_proxy_ns < row.close_ns + ASSUMED_LABEL_DELAY_SECONDS*NS
                or type(row.label) is not int or row.label not in (0,1)
                or row.source != "UNVERIFIED_PYTH_HISTORICAL_OHLC"
                or len(row.features) != len(FEATURE_NAMES)
                or len(row.lag_returns_bps) != N_HISTORY_BARS-1
                or not all(type(x) in (int,float) and math.isfinite(x)
                           for x in (*row.features, *row.lag_returns_bps))
                or (row.macro_sentiment is not None
                    and (not isinstance(row.macro_sentiment, (int,float))
                         or not math.isfinite(row.macro_sentiment)
                         or not -1 <= row.macro_sentiment <= 1))):
            raise GoldHistoricalResearchError("invalid historical research row or leakage")
        seen.add(row.ticker)
        result.append(row)
    return result


def assemble_research_dataset(
    inventory_path: Path, pyth_artifact_paths: Sequence[Path],
    output_root: Path, *, macro_events: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Join previously observed final results with historical gold OHLC.

    Explicitly *does not* establish actual as-of publication of the imported
    OHLC, or authoritative oracle price rounding and contract time alignment.
    """
    root = _checked_gold_root(Path(output_root))
    try:
        inventory, inventory_hash = _load_inventory(Path(inventory_path))
        markets = _inventory_records(inventory)
    except (OSError, GoldCandleProbeError, KeyError, TypeError, ValueError) as exc:
        raise GoldHistoricalResearchError("invalid gold inventory identity or structure") from exc
    stamps, prices, sources = _read_bars(pyth_artifact_paths)
    rows: list[GoldResearchRow] = []
    excluded: Counter[str] = Counter()
    for ticker, versions in sorted(markets.items()):
        if not isinstance(ticker, str) or not ticker.startswith(SERIES_TICKER+"-"):
            raise GoldHistoricalResearchError("cross-series market in gold inventory")
        row, reason = _sample_market(ticker, versions, stamps, prices, macro_events)
        if row is not None:
            rows.append(row)
        else:
            excluded[reason or "UNKNOWN"] += 1
    report = {
        "series_ticker": SERIES_TICKER,
        "candidate_research_rows": len(rows),
        "excluded": dict(sorted(excluded.items())),
        "eligible_training_rows": 0,
        "dataset_ready": False,
        "settlement_oracle_verified": False,
        "point_in_time_publication_verified": False,
        "label_status": "KALSHI_REPORTED_UNVERIFIED",
        "macro_status": "UNVERIFIED_FIRST_OBSERVATION_RESEARCH_ONLY" if macro_events else "NOT_PROVIDED",
        "assumptions": [
            "MARKET_CLOSE_MINUS_480_SECONDS_IS_PROVISIONAL_CHECKPOINT",
            "PYTH_BAR_TIMESTAMP_IS_ASSUMED_MINUTE_START",
            "ONE_FULL_MINUTE_EXTRA_FEATURE_EMBARGO",
            "TRAINING_LABEL_AVAILABLE_24_HOURS_AFTER_MARKET_CLOSE_ASSUMPTION",
            "PYTH_SOURCE_NOT_INDEPENDENTLY_AUTHENTICATED",
            "KALSHI_RESULT_NOT_INDEPENDENTLY_RECONSTRUCTED",
        ],
    }
    doc = {
        "schema_version": 1,
        "artifact_kind": "unverified_gold_historical_research_v14",
        "feature_names": list(FEATURE_NAMES),
        "source_hashes": {"inventory": inventory_hash, "pyth_artifacts": sources},
        "research_rows": [asdict(row) for row in rows],
        "report": report,
    }
    path, digest = _write_immutable(root, "historical_research", doc)
    return {**report, "artifact_path": str(path), "sha256": digest}
