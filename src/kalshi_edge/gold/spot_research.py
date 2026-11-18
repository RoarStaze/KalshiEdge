"""Offline independent XAUUSD 1-minute research, without Pyth or Kalshi data.

All target labels are derived *only* from a supplied third-party spot-gold
CSV. They do not represent Kalshi results or Pyth settlement outcomes.
The CSV must be legally obtained by the user under an appropriate data license.
This tool does not request any website, feed or API, and cannot place orders.
"""
from __future__ import annotations

import csv
from collections import Counter
from dataclasses import asdict, dataclass
from hashlib import sha256
import math
from pathlib import Path

from .historical_research import (
    ASSUMED_LABEL_DELAY_SECONDS, FEATURE_NAMES, N_HISTORY_BARS, _features,
)
from .prospective import _checked_gold_root, _read_hash_snapshot, _write_immutable, GoldCaptureError

MS_MINUTE = 60_000
MS_ROUND = 900_000
NS_PER_MS = 1_000_000
MAX_CSV_SIZE = 350 * 1024 * 1024
MAX_BARS = 3_500_000
KIND = "independent_xauusd_15m_spot_proxy_v15"
LABEL = "XAUUSD_15M_SPOT_DIRECTION_PROXY_NOT_KALSHI"
SOURCE = "USER_SUPPLIED_XAUUSD_CSV"


class SpotResearchError(ValueError):
    """Reject untrusted input, suspicious timestamps or training claims."""


@dataclass(frozen=True)
class SpotResearchRow:
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


def _load_csv(path: Path) -> tuple[dict[int, tuple[float, float, float, float]], str]:
    path = Path(path)
    if not path.is_file() or not 1 <= path.stat().st_size <= MAX_CSV_SIZE:
        raise SpotResearchError("CSV absent, empty or larger than 350 MiB")
    digest = sha256()
    price_bars: dict[int, tuple[float, float, float, float]] = {}
    # Hash the raw source bytes to bind the provenance of each artifact.
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    previous = None
    try:
        with path.open("r", newline="", encoding="utf-8-sig") as stream:
            reader = csv.DictReader(stream)
            required = {"timestamp", "open", "high", "low", "close"}
            if reader.fieldnames is None or not required.issubset(set(reader.fieldnames)):
                raise SpotResearchError("CSV must have timestamp,open,high,low,close columns")
            for line_no, row in enumerate(reader, start=2):
                if len(price_bars) >= MAX_BARS:
                    raise SpotResearchError("CSV exceeds bounded maximum minute bars")
                ts_str = row.get("timestamp", "")
                if not isinstance(ts_str, str) or not ts_str.isdecimal():
                    raise SpotResearchError(f"invalid UTC Unix millisecond timestamp at CSV line {line_no}")
                ts = int(ts_str)
                if not (946684800000 <= ts <= 4133980800000 and ts % MS_MINUTE == 0):
                    raise SpotResearchError(f"invalid UTC minute-aligned timestamp at CSV line {line_no}")
                if previous is not None and ts <= previous:
                    raise SpotResearchError(f"duplicate or descending gold minute at CSV line {line_no}")
                previous = ts
                try:
                    values = tuple(float(row[key]) for key in ("open","high","low","close"))
                except (TypeError, ValueError, KeyError) as exc:
                    raise SpotResearchError(f"malformed OHLC at CSV line {line_no}") from exc
                opening, high, low, close = values
                if (not all(math.isfinite(v) and v > 0 for v in values)
                        or high < max(opening, close)
                        or low > min(opening, close)):
                    raise SpotResearchError(f"invalid gold OHLC at CSV line {line_no}")
                price_bars[ts] = values
    except (UnicodeDecodeError, csv.Error, OSError) as exc:
        raise SpotResearchError("unable to decode valid UTF-8 spot-gold CSV") from exc
    if not price_bars:
        raise SpotResearchError("spot gold CSV has no minute bars")
    return price_bars, digest.hexdigest()


def _row_for_round(
    start: int, prices: dict[int, tuple[float, float, float, float]],
) -> tuple[SpotResearchRow | None, str | None]:
    """Use only 1m bars known before the seven-minute decision.

    Bars are assumed UTC-minute-*start* timestamps, matching the documented
    dukascopy-node CSV. Decision=start+7m, last feature bar starts=start+5m;
    the bar ends at start+6m (one-minute embargo before decision).
    """
    decision = start + 7*MS_MINUTE
    latest = start + 5*MS_MINUTE
    close = start + MS_ROUND
    history_starts = [latest - j * MS_MINUTE for j in range(N_HISTORY_BARS-1, -1, -1)]
    # Ensure no missing observations from the oldest training feature through
    # settlement. Missing weekends/holidays and market outages are skipped.
    required_start = history_starts[0]
    if any(t not in prices for t in range(required_start, close, MS_MINUTE)):
        return None, "INCOMPLETE_SPOT_WINDOW"
    history = [prices[t] for t in history_starts]
    features, lag_returns = _features(history)
    opening_reference = prices[start-MS_MINUTE][3]
    ending_reference = prices[close-MS_MINUTE][3]
    label = 1 if ending_reference >= opening_reference else 0
    return SpotResearchRow(
        ticker=f"SPOT-XAUUSD-{start}",
        close_ns=close*NS_PER_MS, decision_ns=decision*NS_PER_MS,
        feature_end_ns=(latest+MS_MINUTE)*NS_PER_MS,
        label_available_proxy_ns=(close + ASSUMED_LABEL_DELAY_SECONDS*1000)*NS_PER_MS,
        label=label, source=SOURCE, features=features,
        lag_returns_bps=lag_returns,
    ), None


def build_spot_research(source_csv: Path, output_root: Path) -> dict:
    """Write SHA-256-auditable, nondeployable direction-proxy examples."""
    prices, digest = _load_csv(Path(source_csv))
    starts = sorted(prices)
    earliest = ((starts[0]+MS_ROUND-1)//MS_ROUND)*MS_ROUND
    latest = starts[-1]
    rows: list[SpotResearchRow] = []
    excluded: Counter[str] = Counter()
    for start in range(earliest, latest-MS_ROUND+MS_MINUTE, MS_ROUND):
        row, reason = _row_for_round(start, prices)
        if row is None:
            excluded[reason or "UNSPECIFIED"] += 1
        else:
            rows.append(row)
    report = {
        "candidate_research_rows": len(rows),
        "eligible_training_rows": 0,
        "dataset_ready": False,
        "settlement_oracle_verified": False,
        "label_status": LABEL,
        "source_label": SOURCE,
        "source_market": "INDEPENDENT_XAUUSD_SPOT_NOT_PYTH_OR_KALSHI",
        "excluded": dict(sorted(excluded.items())),
        "source_minute_bars": len(prices),
        "assumed_market_duration_seconds": 900,
        "assumed_prediction_checkpoint_remaining_seconds": 480,
        "assumed_label_delay_hours": 24,
        "model_deployment_allowed": False,
        "assumptions": [
            "INPUT_CSV_TIMESTAMP_IS_UTC_CANDLE_START_MILLISECONDS",
            "SPOT_START_CLOSE_PRIOR_MINUTE_AND_END_CLOSE_FINAL_MINUTE_ARE_PROXIES",
            "PRICE_WINDOW_BOUNDARIES_DIFFER_FROM_PYTH_SETTLEMENT_RULES",
            "NO_KALSHI_TRADE_EXECUTION_OR_RESULT_LABELS",
            "ONE_FULL_MINUTE_PRE_DECISION_FEATURE_EMBARGO",
            "TRAIN_ONLY_CHRONOLOGICAL_EVALUATION",
        ],
    }
    doc = {
        "schema_version": 1,
        "artifact_kind": KIND,
        "feature_names": list(FEATURE_NAMES),
        "source_hashes": {"csv_sha256": digest},
        "research_rows": [asdict(row) for row in rows],
        "report": report,
    }
    path, doc_hash = _write_immutable(
        _checked_gold_root(Path(output_root)), "spot_proxy_research", doc,
    )
    return {**report, "sha256": doc_hash, "artifact_path": str(path)}


def spot_research_rows(doc: dict) -> list[SpotResearchRow]:
    if not isinstance(doc, dict) or not isinstance(doc.get("research_rows"), list):
        raise SpotResearchError("spot research rows missing")
    rows: list[SpotResearchRow] = []
    unique: set[str] = set()
    for raw in doc["research_rows"]:
        try:
            row = SpotResearchRow(**raw)
        except (TypeError, ValueError) as exc:
            raise SpotResearchError("invalid spot research row structure") from exc
        if (not isinstance(row.ticker, str) or not row.ticker.startswith("SPOT-XAUUSD-")
                or row.ticker in unique
                or type(row.close_ns) is not int
                or type(row.decision_ns) is not int
                or type(row.feature_end_ns) is not int
                or type(row.label_available_proxy_ns) is not int
                or row.close_ns - row.decision_ns != 480 * 1_000_000_000
                or row.feature_end_ns > row.decision_ns - 60*1_000_000_000
                or row.label_available_proxy_ns < row.close_ns + 86400*1_000_000_000
                or row.label not in (0, 1) or type(row.label) is not int
                or row.source != SOURCE or row.macro_sentiment is not None
                or row.macro_count != 0
                or len(row.features) != len(FEATURE_NAMES)
                or len(row.lag_returns_bps) != N_HISTORY_BARS - 1
                or not all(type(x) in (int,float) and math.isfinite(x)
                           for x in (*row.features, *row.lag_returns_bps))):
            raise SpotResearchError("invalid spot research row or source provenance")
        unique.add(row.ticker)
        rows.append(row)
    return rows


def inspect_spot_research(artifact_path: Path) -> dict:
    try:
        doc, digest = _read_hash_snapshot(Path(artifact_path))
    except (GoldCaptureError, OSError) as exc:
        raise SpotResearchError("spot research artifact failed integrity validation") from exc
    rep = doc.get("report")
    if (doc.get("artifact_kind") != KIND
            or not isinstance(rep, dict)
            or rep.get("dataset_ready") is not False
            or rep.get("eligible_training_rows") != 0
            or rep.get("settlement_oracle_verified") is not False
            or rep.get("label_status") != LABEL
            or rep.get("source_label") != SOURCE
            or rep.get("model_deployment_allowed") is not False
            or doc.get("feature_names") != list(FEATURE_NAMES)
            or not isinstance(doc.get("source_hashes"), dict)
            or not isinstance(doc["source_hashes"].get("csv_sha256"), str)
            or len(doc["source_hashes"]["csv_sha256"]) != 64):
        raise SpotResearchError("spot dataset metadata falsely implies verified settlement")
    if rep.get("candidate_research_rows") != len(spot_research_rows(doc)):
        raise SpotResearchError("candidate count mismatch")
    return doc
