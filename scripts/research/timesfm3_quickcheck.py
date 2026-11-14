#!/usr/bin/env python3
"""Isolated, non-deployable TimesFM3-vs-incumbent 480s research pilot.

Usage: see docs/TimesFM3_QUICKCHECK.md.

PREPARE: run with KalshiEdge research Python; read only verified development
markets strictly beyond the CURRENT incumbent's calibration training interval.
Never load reserved future lockbox rows. Inference sees no labels.
INFER: run with separate TimesFM3 Python environment (no KalshiEdge imports).
SCORE: run with ordinary Python; no candidate is trained, promoted or traded.

Pretrained TimesFM3 weights are restricted to non-commercial/non-production
use by their license. The user must confirm that this experiment complies.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Sequence

NS = 1_000_000_000
CHECKPOINT_SECONDS = 480
RESAMPLE_SECONDS = 5
CONTEXT_GUARD_SECONDS = 5
PREDICT_STEPS = (CHECKPOINT_SECONDS + CONTEXT_GUARD_SECONDS) // RESAMPLE_SECONDS  # 97
MODEL_ID = "google/timesfm-3.0-pytorch"
OUTPUT_DIR = Path("data/bootstrap/training_runs/research/timesfm3")


def sample_spaced(items: Sequence[str], *, limit: int) -> tuple[str, ...]:
    """Uniformly spaced deterministic historical samples; no replacement."""
    if type(limit) is not int or limit < 1:
        raise ValueError("sample limit must be positive")
    values = tuple(items)
    if len(values) <= limit:
        return values
    if limit == 1:
        return (values[-1],)
    indices = [index * (len(values) - 1) // (limit - 1) for index in range(limit)]
    return tuple(values[index] for index in indices)


def causal_five_second_context(
    completed_seconds_to_close: dict[int, float], *,
    checkpoint_second: int, steps: int = 256,
) -> list[float]:
    """No partial bars, filling missing timestamps, or observations at cutoff."""
    if type(checkpoint_second) is not int or type(steps) is not int or steps < 8:
        raise ValueError("invalid 5-second context configuration")
    last = checkpoint_second - CONTEXT_GUARD_SECONDS
    timestamps = range(last - (steps - 1) * RESAMPLE_SECONDS,
                       last + RESAMPLE_SECONDS, RESAMPLE_SECONDS)
    try:
        values = [float(completed_seconds_to_close[ts]) for ts in timestamps]
    except KeyError as exc:
        raise ValueError("missing complete historical 5-second context") from exc
    if not all(math.isfinite(v) and v > 0 for v in values):
        raise ValueError("context includes nonpositive or nonfinite prices")
    return values


def _validated_price_result(
    predicted_price: float, *, strike: float, strike_type: str,
) -> int:
    if not math.isfinite(predicted_price) or not math.isfinite(strike) or strike <= 0:
        raise ValueError("nonfinite BTC forecast or strike")
    if strike_type == "greater":
        return int(predicted_price > strike)
    if strike_type == "greater_or_equal":
        return int(predicted_price >= strike)
    raise ValueError("unsupported Kalshi strike convention")


def compare(prepared: dict[str, Any], prediction: dict[str, Any]) -> dict[str, Any]:
    """Matched historical development comparison; never a promotion decision."""
    records = prepared.get("records")
    outputs = prediction.get("results")
    if not isinstance(records, list) or not records or not isinstance(outputs, list):
        raise ValueError("missing matched forecast records")
    if len(records) != len(outputs):
        raise ValueError("TimesFM3 outputs are incomplete")
    reference = {r["ticker"]: r for r in records}
    forecasts = {r["ticker"]: r for r in outputs}
    if len(reference) != len(records) or len(forecasts) != len(outputs) or reference.keys() != forecasts.keys():
        raise ValueError("market identity mismatch or duplicate results")
    if "input_sha256" in prediction and prediction["input_sha256"] != prepared.get("input_sha256"):
        raise ValueError("TimesFM3 results do not match the prepared input hash")
    if prediction.get("checkpoint", MODEL_ID) != MODEL_ID:
        raise ValueError("wrong TimesFM3 checkpoint")

    incumbent_correct = timesfm_correct = naive_correct = 0
    model_only = timesfm_only = both_wrong = both_right = 0
    qualified = qualified_correct = 0
    for rec in records:
        label = rec["label_yes"]
        p = float(rec["incumbent_p_yes"])
        price = float(forecasts[rec["ticker"]]["forecast_price"])
        strike = float(rec["strike"])
        strike_type = rec["strike_type"]
        if type(label) is not int or label not in (0, 1) or not math.isfinite(p) or not 0 <= p <= 1:
            raise ValueError("invalid verified outcome or incumbent probability")
        incumbent_direction = int(p >= .5)
        timesfm_direction = _validated_price_result(price, strike=strike, strike_type=strike_type)
        last_known_price = float(rec["context"][-1])
        naive_direction = _validated_price_result(last_known_price, strike=strike, strike_type=strike_type)
        correct_inc = incumbent_direction == label
        correct_tfm = timesfm_direction == label
        incumbent_correct += int(correct_inc)
        timesfm_correct += int(correct_tfm)
        naive_correct += int(naive_direction == label)
        both_right += int(correct_inc and correct_tfm)
        model_only += int(correct_inc and not correct_tfm)
        timesfm_only += int(correct_tfm and not correct_inc)
        both_wrong += int(not correct_inc and not correct_tfm)
        if 2 * abs(p - .5) >= .69:
            qualified += 1
            qualified_correct += int(correct_inc)
    n = len(records)

    # Two-sided exact McNemar test, paired by identical historical market.
    n_disagree = timesfm_only + model_only
    p_value = (
        min(1., 2 * sum(math.comb(n_disagree, k) for k in range(min(timesfm_only, model_only) + 1))
            / (1 << n_disagree)) if n_disagree else 1.
    )
    return {
        "experiment_kind": "NON_DEPLOYABLE_EXPLORATORY",
        "count": n,
        "incumbent_bundle_sha": prepared.get("incumbent_bundle_sha"),
        "timesfm_checkpoint": MODEL_ID,
        "incumbent_accuracy": incumbent_correct / n,
        "timesfm3_accuracy": timesfm_correct / n,
        "persistence_accuracy": naive_correct / n,
        "accuracy_delta_percentage_points": 100 * (timesfm_correct - incumbent_correct) / n,
        "both_correct": both_right,
        "incumbent_only_correct": model_only,
        "timesfm3_only_correct": timesfm_only,
        "both_wrong": both_wrong,
        "mcnemar_exact_two_sided_p": p_value,
        "incumbent_69_strength_qualified": qualified,
        "incumbent_69_strength_accuracy": qualified_correct / qualified if qualified else None,
        "confidence_gated_timesfm3_accuracy": None,
        "brier_timesfm3": None,
        "promotion_eligible": False,
        "limitations": (
            "Exploratory pre-existing development markets, not fresh untouched "
            "evaluation. TimesFM3 price forecasts use Binance BTCUSDT, not Kalshi "
            "BRTI reference; differences in exchange index can change strike "
            "classification. Only raw directional accuracy is comparable; no "
            "TimesFM3 calibrated probabilities, proper score or comparable 69% "
            "confidence gate. No deployment or profitability conclusion."
        ),
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """All output goes to an independent research directory, never model pointers."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temp.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("research JSON is not an object")
    return value


def _verified_day_close_map(root: Path, symbol: str, day: date) -> dict[int, float]:
    """Verify normalized and raw input hashes before reading completed seconds."""
    from kalshi_edge.bootstrap import dataset
    import pyarrow.parquet as pq

    stem = f"{symbol.upper()}-1s-{day.isoformat()}"
    normalized = root / "normalized" / "binance" / "1s" / f"{stem}.parquet"
    manifest = root / "manifests" / "binance_normalized" / "1s" / f"{stem}.parquet.manifest.json"
    info = dataset._require_verified(normalized, manifest, label=f"TimesFM3 normalized Binance {day}")
    source = info.get("metadata") or {}
    raw_relative = source.get("source_raw_path")
    raw_sha = source.get("source_raw_sha256")
    if not isinstance(raw_relative, str) or not isinstance(raw_sha, str):
        raise ValueError("normalized Binance source manifest lacks raw integrity proof")
    raw = (root / raw_relative).resolve()
    raw.relative_to((root / "raw").resolve())
    raw_manifest = dataset._manifest_for_raw(root, raw)
    verified_raw = dataset._require_verified(raw, raw_manifest, label=f"TimesFM3 raw Binance {day}")
    if verified_raw.get("sha256") != raw_sha:
        raise ValueError("normalized Binance history has inconsistent SHA source identity")
    table = pq.read_table(normalized, columns=["ts_ns", "close"]).to_pydict()
    result: dict[int, float] = {}
    for ts, close in zip(table["ts_ns"], table["close"]):
        end_sec = int(ts) // NS + 1
        if end_sec in result:
            raise ValueError("duplicate completed Binance second")
        result[end_sec] = float(close)
    return result


def _causal_context_for_market(root: Path, symbol: str, checkpoint_second: int, steps: int) -> list[float]:
    last = checkpoint_second - CONTEXT_GUARD_SECONDS
    first = last - (steps - 1) * RESAMPLE_SECONDS
    begin = datetime.fromtimestamp(first, timezone.utc).date()
    end = datetime.fromtimestamp(last, timezone.utc).date()
    days = [begin] if begin == end else [begin, end]
    seconds: dict[int, float] = {}
    for day in days:
        values = _verified_day_close_map(root, symbol, day)
        seconds.update({ts: v for ts, v in values.items() if first <= ts <= last})
    return causal_five_second_context(seconds, checkpoint_second=checkpoint_second, steps=steps)


def run_prepare(
    *, root: Path, out: Path, markets: int = 32, context_steps: int = 256,
) -> dict[str, Any]:
    """Only previously exposed development markets; never read sealed lockbox labels."""
    from kalshi_edge.bootstrap import training, live
    from kalshi_edge.bootstrap.config import BootstrapSettings
    from kalshi_edge.bootstrap.labels import normalize_market_label
    from kalshi_edge.bootstrap.provenance import verify_artifact
    import pyarrow.parquet as pq

    if markets < 8 or markets > 256 or context_steps < 64 or context_steps > 2048:
        raise ValueError("research sample requires 8-256 markets, 64-2048 context steps")
    context = training._verified_context(root)
    incumbent_bundle, pipeline = live.load_default_model(root)
    training_end = incumbent_bundle.boundaries.calibration_end_ts_ns
    calibration_start = context.plan.boundaries.calibration_start_ts_ns
    group_set = set(context.plan.development_groups)
    eligible = [
        ticker for ticker, first, last in training._metadata_groups(context.dataset_path)
        if ticker in group_set and first > training_end and last < calibration_start
    ]
    if len(eligible) < 8:
        raise ValueError(
            "No safe out-of-incumbent-fitting historical development cohort; "
            "do not use fresh sealed lockbox for this exploratory test."
        )
    candidates = sample_spaced(eligible, limit=min(len(eligible), markets))
    columns = ["market_ticker", "market_date", "split_group_id", "checkpoint_ts_ns",
               "label_yes", *context.feature_names]
    parquet = pq.read_table(
        context.dataset_path, columns=columns, filters=[
            ("market_ticker", "in", list(candidates)),
            ("seconds_remaining", "==", float(CHECKPOINT_SECONDS)),
            ("checkpoint_ts_ns", "<", calibration_start),
        ],
    )
    rows = {row.market_ticker: row for row in training._table_to_rows(parquet, context.feature_names)}
    settings = BootstrapSettings(bootstrap_dir=root)
    selected: list[Any] = []
    records: list[dict[str, Any]] = []
    excluded: list[str] = []
    for ticker in candidates:
        if len(records) >= markets:
            break
        row = rows.get(ticker)
        if row is None or row.checkpoint_ts_ns % NS or row.checkpoint_ts_ns <= training_end:
            excluded.append(ticker)
            continue
        market_path = root / "raw" / "kalshi" / "markets" / f"{ticker}.json"
        manifest = root / "manifests" / "kalshi" / "markets" / f"{ticker}.json.manifest.json"
        if not verify_artifact(market_path, manifest):
            raise ValueError(f"official market provenance mismatch: {ticker}")
        label = normalize_market_label(_read_json(market_path))
        if (label.close_ts_ns - row.checkpoint_ts_ns != CHECKPOINT_SECONDS * NS
                or row.label_yes != int(label.result == "yes")
                or not math.isclose(row.features["strike"], label.strike, rel_tol=0.0, abs_tol=1e-8)):
            raise ValueError(f"480s checkpoint does not match official outcome and strike for {ticker}")
        try:
            series = _causal_context_for_market(
                root, settings.binance_symbol, row.checkpoint_ts_ns // NS, context_steps,
            )
        except (FileNotFoundError, ValueError, OSError):
            excluded.append(ticker)
            continue
        records.append({
            "ticker": ticker, "label_yes": row.label_yes,
            "strike": label.strike, "strike_type": label.strike_type,
            "checkpoint_ts_ns": row.checkpoint_ts_ns,
            "context": series,
        })
        selected.append(row)
    if len(records) < 8:
        raise ValueError(f"only {len(records)} complete causal research markets; need at least 8")
    prediction = pipeline.predict(selected)["candidate"]
    if len(prediction) != len(records):
        raise ValueError("incumbent probability alignment mismatch")
    for item, p in zip(records, prediction):
        if not math.isfinite(float(p)) or not 0 <= p <= 1:
            raise ValueError("incumbent prediction invalid")
        item["incumbent_p_yes"] = float(p)

    payload: dict[str, Any] = {
        "experiment_kind": "NON_DEPLOYABLE_EXPLORATORY",
        "incumbent_bundle_sha": incumbent_bundle.bundle_sha256,
        "timesfm_checkpoint": MODEL_ID,
        "checkpoint_seconds": CHECKPOINT_SECONDS,
        "cadence_seconds": RESAMPLE_SECONDS,
        "guard_seconds": CONTEXT_GUARD_SECONDS,
        "predict_steps": PREDICT_STEPS,
        "context_steps": context_steps,
        "excluded_missing_context_count": len(excluded),
        "records": records,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["input_sha256"] = hashlib.sha256(raw).hexdigest()
    _write_json(out, payload)
    return {"status": "PREPARED", "market_count": len(records),
            "excluded": len(excluded), "output": str(out),
            "out_of_fit_groups_only": True, "sealed_lockbox_opened": False}


def run_infer(
    prepared: Path, output: Path, *, accepted_noncommercial_license: bool,
    batch_size: int = 2,
) -> dict[str, Any]:
    """Separate Python environment with ONLY torch/timesfm, not app dependencies."""
    if not accepted_noncommercial_license:
        raise ValueError(
            "license acknowledgement required: TimesFM3 downloadable weights are "
            "restricted to non-commercial non-production use; do not use if this "
            "research is not permitted by the TimesFM3 license."
        )
    import numpy as np
    import torch
    from timesfm3 import TimesFM3Evaluator, ModelConfig

    if not torch.cuda.is_available():
        raise ValueError("TimesFM3 research GPU unavailable; install CUDA-enabled PyTorch in its isolated environment")
    payload = _read_json(prepared)
    if payload.get("experiment_kind") != "NON_DEPLOYABLE_EXPLORATORY":
        raise ValueError("cannot infer from a non-research input")
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("empty prepared sample")
    batch_size = max(1, min(int(batch_size), 4))
    forecaster = TimesFM3Evaluator(ModelConfig(
        checkpoint_path=MODEL_ID,
        per_core_batch_size=batch_size,
        device="cuda",
    ))
    results: list[dict[str, Any]] = []
    for start in range(0, len(records), batch_size):
        batch = records[start:start + batch_size]
        ts = [np.asarray(record["context"], dtype=np.float32) for record in batch]
        outputs = list(forecaster.predict_batch(
            ts, horizon=int(payload["predict_steps"]),
            return_quantiles=False, use_symmetric_averaging=False,
        ))
        if len(outputs) != len(batch):
            raise ValueError("TimesFM3 returned incomplete batch")
        for record, pred in zip(batch, outputs):
            forecast = float(np.asarray(pred.forecast).reshape(-1)[-1])
            if not math.isfinite(forecast) or forecast <= 0:
                raise ValueError("TimesFM3 predicted an invalid BTC price")
            results.append({"ticker": record["ticker"], "forecast_price": forecast})
        print(f"TimesFM3 research: {len(results)}/{len(records)} markets", flush=True)
    output_payload = {
        "checkpoint": MODEL_ID,
        "input_sha256": payload.get("input_sha256"),
        "results": results,
        "note": "Research only; downloaded model weights may not be used in production.",
    }
    _write_json(output, output_payload)
    return {"status": "INFERENCE_COMPLETE", "count": len(results), "output": str(output)}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare", help="read-only verified local development sample")
    prepare.add_argument("--root", type=Path, default=Path("data/bootstrap"))
    prepare.add_argument("--markets", type=int, default=32)
    prepare.add_argument("--context-steps", type=int, default=256)
    prepare.add_argument("--out", type=Path, default=OUTPUT_DIR / "prepared.json")
    infer = sub.add_parser("infer", help="run zero-shot TimesFM3 in a separate CUDA/PyTorch Python environment")
    infer.add_argument("--prepared", type=Path, default=OUTPUT_DIR / "prepared.json")
    infer.add_argument("--out", type=Path, default=OUTPUT_DIR / "timesfm3.json")
    infer.add_argument("--batch-size", type=int, default=2)
    infer.add_argument("--accept-noncommercial-license", action="store_true")
    score = sub.add_parser("score", help="matched read-only directional comparison")
    score.add_argument("--prepared", type=Path, default=OUTPUT_DIR / "prepared.json")
    score.add_argument("--timesfm3", type=Path, default=OUTPUT_DIR / "timesfm3.json")
    args = ap.parse_args(argv)
    if args.command == "prepare":
        result = run_prepare(root=args.root.resolve(), out=args.out.resolve(),
                             markets=args.markets, context_steps=args.context_steps)
    elif args.command == "infer":
        result = run_infer(args.prepared, args.out,
                           accepted_noncommercial_license=args.accept_noncommercial_license,
                           batch_size=args.batch_size)
    else:
        result = compare(_read_json(args.prepared), _read_json(args.timesfm3))
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
