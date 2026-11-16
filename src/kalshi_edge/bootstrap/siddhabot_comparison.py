"""Read-only, prospective KXBTC15M KalshiEdge vs SiddhaBot benchmark.

Scores BOTH probabilities on exactly the same official Kalshi settlements,
using the common >63% directional-probability gate at 480s remaining.
Never backfills unavailable SiddhaBot predictions or uses settled labels
for inference. Original KalshiEdge final-gate results are kept separately.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
from typing import Any, Sequence

from .final_forecast import FINAL_CHECKPOINT_SECONDS

NS = 1_000_000_000
GATE = 0.63
MAX_CAPTURE_OFFSET_NS = 5 * NS
SAFE_TICKER = re.compile(r"^KXBTC15M-[A-Z0-9-]+$")
HEX_SHA = re.compile(r"^[0-9a-f]{40,64}$")
BENCHMARK_VERSION = "siddhabot-prospective-480-v1"


def _probability(value: object) -> float | None:
    if type(value) not in (float, int):
        return None
    number = float(value)
    return number if math.isfinite(number) and 0 <= number <= 1 else None


def _score(probabilities: Sequence[float | None], labels: Sequence[int]) -> dict[str, Any]:
    if len(probabilities) != len(labels):
        raise ValueError("unaligned benchmark labels and predictions")
    count = len(labels)
    called = [(p, label) for p, label in zip(probabilities, labels)
              if p is not None and max(p, 1.0 - p) > GATE]
    correct = sum(int((p >= 0.5) == bool(label)) for p, label in called)
    return {
        "markets": count,
        "qualified": len(called),
        "correct": correct,
        "incorrect": len(called) - correct,
        "skipped": count - len(called),
        "accuracy": correct / len(called) if called else None,
        "coverage": len(called) / count if count else None,
        "probabilities_available": sum(p is not None for p in probabilities),
    }


def _locked_score(decisions: Sequence[str], labels: Sequence[int]) -> dict[str, Any]:
    if len(decisions) != len(labels):
        raise ValueError("unaligned locked forecasts and labels")
    count = len(labels)
    called = [(side, label) for side, label in zip(decisions, labels)
              if side in ("ABOVE", "BELOW")]
    correct = sum(int((side == "ABOVE") == bool(label)) for side, label in called)
    return {
        "markets": count,
        "qualified": len(called),
        "correct": correct,
        "incorrect": len(called) - correct,
        "skipped": count - len(called),
        "accuracy": correct / len(called) if called else None,
        "coverage": len(called) / count if count else None,
    }


def _capture_path(root: Path, ticker: str, close_ns: int) -> Path:
    day = datetime.fromtimestamp(close_ns / NS, timezone.utc).date().isoformat()
    return root / "benchmarks" / "siddhabot" / "predictions" / day / f"{ticker}.json"


def _read_capture(root: Path, final: Any) -> tuple[float | None, str]:
    ticker = final.market_ticker
    if not SAFE_TICKER.fullmatch(ticker):
        return None, "INVALID_TICKER"
    close_ns = final.scheduled_ts_ns + FINAL_CHECKPOINT_SECONDS * NS
    path = _capture_path(root, ticker, close_ns)
    if not path.is_file():
        return None, "NOT_CAPTURED"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if type(payload) is not dict:
            return None, "INVALID_CAPTURE"
        before = payload.get("started_ts_ns")
        after = payload.get("finished_ts_ns")
        probability = _probability(payload.get("p_yes"))
        if (payload.get("benchmark_version") != BENCHMARK_VERSION
                or payload.get("ticker") != ticker
                or payload.get("close_ts_ns") != close_ns
                or type(before) is not int or type(after) is not int
                or before > after or before <= 0 or after >= close_ns
                or max(abs(close_ns - before - 480 * NS),
                       abs(close_ns - after - 480 * NS)) > MAX_CAPTURE_OFFSET_NS
                or probability is None
                or not isinstance(payload.get("source_commit"), str)
                or not HEX_SHA.fullmatch(payload["source_commit"])
                or not isinstance(payload.get("model_sha256"), str)
                or len(payload["model_sha256"]) != 64
                or not HEX_SHA.fullmatch(payload["model_sha256"])):
            return None, "INVALID_CAPTURE"
    except (OSError, ValueError, OverflowError):
        return None, "INVALID_CAPTURE"
    return probability, "CAPTURED"


def compare(root: Path) -> dict[str, Any]:
    """Compare only the KalshiEdge journal's legitimately settled 480s markets.

    The capture-start boundary deliberately excludes all earlier markets:
    missing pre-install SiddhaBot predictions must never count as evidence.
    The same eligible market denominator is used for both systems.
    """
    from .final_history import _forecasts, _load_saved_settlement, SettlementIntegrityError

    root = root.resolve()
    start_file = root / "benchmarks" / "siddhabot" / "capture-start.json"
    result: dict[str, Any] = {
        "benchmark": BENCHMARK_VERSION,
        "status": "NOT_STARTED",
        "checkpoint_seconds": FINAL_CHECKPOINT_SECONDS,
        "common_directional_gate_strictly_greater_than": GATE,
        "capture_tolerance_seconds": MAX_CAPTURE_OFFSET_NS / NS,
    }
    if not start_file.is_file():
        return result
    try:
        session = json.loads(start_file.read_text(encoding="utf-8"))
        start_ns = session["started_ts_ns"]
        if type(start_ns) is not int or start_ns <= 0:
            raise ValueError("invalid benchmark start")
    except (OSError, ValueError, TypeError, KeyError):
        result["status"] = "INVALID_START_MARKER"
        return result

    result["started_ts_ns"] = start_ns
    finals = [
        final for final in _forecasts(root)
        if final.checkpoint_seconds == FINAL_CHECKPOINT_SECONDS
        and final.scheduled_ts_ns >= start_ns
    ]
    edge_probs: list[float | None] = []
    siddha_probs: list[float | None] = []
    locked_sides: list[str] = []
    labels: list[int] = []
    failures: dict[str, int] = {}
    source_models: set[str] = set()
    source_commits: set[str] = set()
    unsettled = 0
    for final in finals:
        try:
            settlement = _load_saved_settlement(root, final)
        except SettlementIntegrityError:
            failures["INVALID_OFFICIAL_SETTLEMENT"] = failures.get("INVALID_OFFICIAL_SETTLEMENT", 0) + 1
            continue
        if settlement is None:
            unsettled += 1
            continue
        label = 1 if settlement.result == "yes" else 0
        p, capture_status = _read_capture(root, final)
        failures[capture_status] = failures.get(capture_status, 0) + 1
        if capture_status == "CAPTURED":
            path = _capture_path(root, final.market_ticker, settlement.market_close_ts_ns)
            payload = json.loads(path.read_text(encoding="utf-8"))
            source_models.add(payload["model_sha256"])
            source_commits.add(payload["source_commit"])
        labels.append(label)
        edge_probs.append(_probability(final.p_yes))
        siddha_probs.append(p)
        locked_sides.append(final.forecast)

    result.update(
        status="READY" if labels else "NO_SETTLED_PAIRED_PERIOD_MARKETS",
        total_final_forecasts_in_period=len(finals),
        pending_official_settlements=unsettled,
        scored_officially_settled_markets=len(labels),
        siddhabot_capture_status=failures,
        distinct_siddhabot_artifacts=len(source_models),
        distinct_siddhabot_source_commits=len(source_commits),
        kalshiedge_common_gate=_score(edge_probs, labels),
        siddhabot_common_gate=_score(siddha_probs, labels),
        kalshiedge_actual_locked_policy=_locked_score(locked_sides, labels),
    )
    paired = [(e, s, y) for e, s, y in zip(edge_probs, siddha_probs, labels)
              if e is not None and s is not None]
    result["paired_probability_markets"] = len(paired)
    result["paired_brier"] = {
        "kalshiedge": sum((e - y) ** 2 for e, _, y in paired) / len(paired) if paired else None,
        "siddhabot": sum((s - y) ** 2 for _, s, y in paired) / len(paired) if paired else None,
    }
    if len(source_models) > 1 or len(source_commits) > 1:
        result["warning"] = "SiddhaBot source/model versions changed during the scored period"
    if labels and not paired:
        result["status"] = "NO_PAIRED_PROBABILITIES"
        result["limitation"] = "No contemporaneous settled markets with both probabilities; comparison is not yet meaningful"
    return result


def _pct(value: float | None) -> str:
    return f"{value * 100:.2f}%" if value is not None else "N/A"


def format_report(result: dict[str, Any]) -> str:
    lines = [
        "KXBTC15M 480s PROSPECTIVE COMPARISON (same official settlements)",
        f"Status: {result['status']}; common gate: >{int(GATE * 100)}% directional probability",
    ]
    if "kalshiedge_common_gate" not in result:
        return "\n".join(lines + ["Start capture first; no comparable results exist yet."])
    edge = result["kalshiedge_common_gate"]
    siddha = result["siddhabot_common_gate"]
    lines.extend([
        f"Officially settled markets in capture period: {result['scored_officially_settled_markets']}",
        f"Paired probabilities available: {result['paired_probability_markets']}",
        "",
        f"{'Metric':<22} {'KalshiEdge':>14} {'SiddhaBot':>14}",
        "-" * 53,
        f"{'Accuracy':<22} {_pct(edge['accuracy']):>14} {_pct(siddha['accuracy']):>14}",
        f"{'Coverage':<22} {_pct(edge['coverage']):>14} {_pct(siddha['coverage']):>14}",
        f"{'Correct':<22} {edge['correct']:>14} {siddha['correct']:>14}",
        f"{'Incorrect':<22} {edge['incorrect']:>14} {siddha['incorrect']:>14}",
        f"{'Qualified':<22} {edge['qualified']:>14} {siddha['qualified']:>14}",
        f"{'Skipped / unavailable':<22} {edge['skipped']:>14} {siddha['skipped']:>14}",
        f"{'Predictions available':<22} {edge['probabilities_available']:>14} {siddha['probabilities_available']:>14}",
        "",
        "KalshiEdge original locked-policy accuracy/coverage: "
        + _pct(result["kalshiedge_actual_locked_policy"]["accuracy"]) + " / "
        + _pct(result["kalshiedge_actual_locked_policy"]["coverage"]),
        "Paired Brier (lower better): "
        + "KalshiEdge=" + (f"{result['paired_brier']['kalshiedge']:.5f}" if result['paired_brier']['kalshiedge'] is not None else "N/A")
        + ", SiddhaBot=" + (f"{result['paired_brier']['siddhabot']:.5f}" if result['paired_brier']['siddhabot'] is not None else "N/A"),
        "SiddhaBot missing captures count as SKIPs; no synthetic predictions.",
        "The common gate is a RESEARCH comparison, not either bot's trade execution policy.",
    ])
    if result.get("limitation"):
        lines.append("LIMITATION: " + result["limitation"])
    if result.get("warning"):
        lines.append("WARNING: " + result["warning"])
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    from .config import BootstrapSettings
    parser = argparse.ArgumentParser(description="Read-only prospective KalshiEdge vs SiddhaBot comparison")
    parser.add_argument("--root", type=Path, default=BootstrapSettings().bootstrap_dir)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable comparison")
    args = parser.parse_args(argv)
    result = compare(args.root)
    print(json.dumps(result, indent=2, sort_keys=True) if args.json else format_report(result))
    return 0 if result["status"] == "READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
