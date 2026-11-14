#!/usr/bin/env python3
"""Research-only: train a settlement-probability head on frozen TimesFM-3 outputs.

Downloaded TimesFM-3 weights are non-commercial/non-production. No weights are
fine-tuned, no model bundle is promoted, and no live Trade/AutoTrade code imports
this script. See docs/TimesFM3_MARKET_ADAPTER.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Sequence

MODEL = "google/timesfm-3.0-pytorch"
KIND = "TIMESFM3_SETTLEMENT_ADAPTER_RESEARCH"
DIRECTORY = Path("training_runs/research/timesfm3_market_adapter")
CHECKPOINT_SECONDS = 480
CONTEXT_STEPS = 256
PREDICT_STEPS = 97
MINIMUM_MARKETS = 300


def _digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("research JSON must be an object")
    return value


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _validated_prepared(prepared: dict[str, Any]) -> list[dict[str, Any]]:
    if prepared.get("experiment_kind") != KIND or prepared.get("market_scope") != "DEVELOPMENT_ONLY":
        raise ValueError("invalid research-only development dataset")
    if prepared.get("sealed_current_lockbox_opened") is not False:
        raise ValueError("protected current lockbox may not be inspected")
    if prepared.get("model_checkpoint") != MODEL:
        raise ValueError("TimesFM3 checkpoint identity mismatch")
    hash_value = prepared.get("input_sha256")
    if not isinstance(hash_value, str) or hash_value != _digest({
        key: value for key, value in prepared.items() if key != "input_sha256"
    }):
        raise ValueError("prepared research input hash mismatch")
    records = prepared.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("prepared research has no verified markets")
    tickers: set[str] = set()
    previous = -1
    for rec in records:
        if not isinstance(rec, dict) or not isinstance(rec.get("ticker"), str):
            raise ValueError("invalid prepared market record")
        ticker = rec["ticker"]
        ts = rec.get("checkpoint_ts_ns")
        if not ticker or ticker in tickers:
            raise ValueError("duplicate prepared market ticker")
        if type(ts) is not int or ts <= previous:
            raise ValueError("prepared markets must be strictly chronological")
        tickers.add(ticker)
        previous = ts
        if type(rec.get("label_yes")) is not int or rec["label_yes"] not in (0, 1):
            raise ValueError("invalid official settled label")
        if rec.get("strike_type") not in ("greater", "greater_or_equal"):
            raise ValueError("invalid original Kalshi strike condition")
        if type(rec.get("context")) is not list or len(rec["context"]) != CONTEXT_STEPS:
            raise ValueError("invalid causal TimesFM context")
    return records


def select_development_markets(
    ordered_groups: Sequence[tuple[str, int, int]], development_tickers: set[str]
) -> tuple[str, ...]:
    """Select from verified development group IDs only, never current lockbox."""
    seen: set[str] = set()
    last = -1
    chosen: list[str] = []
    for ticker, first, end in ordered_groups:
        if ticker in seen:
            raise ValueError("duplicate historical market identity")
        if first <= last or first > end:
            raise ValueError("invalid chronological market intervals")
        seen.add(ticker)
        last = end
        if ticker in development_tickers:
            chosen.append(ticker)
    if not set(chosen).issubset(development_tickers):
        raise ValueError("non-development market requested")
    return tuple(chosen)


def chronological_partitions(
    ordered_tickers: Sequence[str], *, minimum: int = MINIMUM_MARKETS,
    embargo: int = 2,
) -> dict[str, tuple[str, ...]]:
    """Frozen 70/15/15 development-only fit/calibration/evaluation with embargo."""
    n = len(ordered_tickers)
    if n < minimum or minimum < 10 or embargo < 1:
        raise ValueError(f"at least {minimum} verified historical markets required")
    if len(set(ordered_tickers)) != n:
        raise ValueError("duplicate market in time split")
    fit_end = int(n * .70)
    calibration_end = int(n * .85)
    output = {
        "fit": tuple(ordered_tickers[:fit_end]),
        "calibration": tuple(ordered_tickers[fit_end + embargo:calibration_end]),
        "evaluation": tuple(ordered_tickers[calibration_end + embargo:]),
    }
    if any(len(output[name]) < 5 for name in output):
        raise ValueError("at least five markets required in each segment")
    return output


def adapter_features(record: dict[str, Any], predicted_price: float) -> tuple[float, ...]:
    """Only contemporaneous strike, causal Binance context and frozen prediction.

    Labels, market dates and settlement prices are intentionally never used.
    """
    strike = float(record["strike"])
    values = record["context"]
    if not isinstance(values, (list, tuple)) or len(values) < 32:
        raise ValueError("causal BTC context is incomplete")
    prices = [float(x) for x in values]
    if (not math.isfinite(strike) or strike <= 0
            or not math.isfinite(predicted_price) or predicted_price <= 0
            or any(not math.isfinite(p) or p <= 0 for p in prices)):
        raise ValueError("invalid causal market price")
    last = prices[-1]
    returns = [math.log(a/b) for a, b in zip(prices[-33:-1], prices[-32:])]
    vol = math.sqrt(sum(ret*ret for ret in returns) / len(returns))
    return (
        math.log(predicted_price / strike),
        math.log(last / strike),
        math.log(predicted_price / last),
        vol,
    )


def _direction(price: float, record: dict[str, Any]) -> int:
    strike = float(record["strike"])
    if record["strike_type"] == "greater":
        return int(price > strike)
    if record["strike_type"] == "greater_or_equal":
        return int(price >= strike)
    raise ValueError("unknown Kalshi strike type")


def _matched_predictions(prepared: dict[str, Any], timesfm: dict[str, Any]):
    records = _validated_prepared(prepared)
    if timesfm.get("checkpoint") != MODEL or timesfm.get("input_sha256") != prepared["input_sha256"]:
        raise ValueError("TimesFM3 prediction input hash or model checkpoint mismatch")
    results = timesfm.get("results")
    if not isinstance(results, list) or len(results) != len(records):
        raise ValueError("missing or extra TimesFM3 forecasts for prepared markets")
    by_ticker: dict[str, float] = {}
    for item in results:
        if not isinstance(item, dict) or not isinstance(item.get("ticker"), str):
            raise ValueError("malformed TimesFM3 forecast")
        ticker = item["ticker"]
        if ticker in by_ticker:
            raise ValueError("duplicate TimesFM3 forecast ticker")
        value = item.get("forecast_price")
        if type(value) not in (float, int) or not math.isfinite(value) or value <= 0:
            raise ValueError("invalid TimesFM3 terminal price")
        by_ticker[ticker] = float(value)
    if set(by_ticker) != {r["ticker"] for r in records}:
        raise ValueError("missing or extra TimesFM3 forecast ticker")
    return records, by_ticker


def _metrics(labels, probabilities):
    import numpy as np
    y = np.asarray(labels, dtype=np.float64)
    p = np.asarray(probabilities, dtype=np.float64)
    if not len(y) or len(y) != len(p) or np.any(~np.isfinite(p)) or np.any((p < 0) | (p > 1)):
        raise ValueError("invalid forecast probability cohort")
    clipped = np.clip(p, 1e-12, 1-1e-12)
    confident = 2 * np.abs(p - .5) >= .69
    pred = p >= .5
    return {
        "markets": int(len(y)),
        "accuracy": float(np.mean(pred == y)),
        "brier": float(np.mean((p-y)**2)),
        "log_loss": float(np.mean(-(y * np.log(clipped) + (1-y) * np.log1p(-clipped)))),
        "qualified": int(np.sum(confident)),
        "coverage": float(np.mean(confident)),
        "qualified_accuracy": float(np.mean((pred == y)[confident])) if np.any(confident) else None,
    }


def train_adapter(prepared: dict[str, Any], timesfm: dict[str, Any], *,
                  minimum: int = MINIMUM_MARKETS) -> dict[str, Any]:
    """Fixed-parameter chronological training; never touches promotion lockbox."""
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    records, prices = _matched_predictions(prepared, timesfm)
    tickers = [rec["ticker"] for rec in records]
    parts = chronological_partitions(tickers, minimum=minimum)
    positions = {ticker: i for i, ticker in enumerate(tickers)}
    x = np.asarray([adapter_features(rec, prices[rec["ticker"]]) for rec in records])
    y = np.asarray([rec["label_yes"] for rec in records])
    indices = {name: [positions[t] for t in ids] for name, ids in parts.items()}
    train, calibration, evaluation = (indices[name] for name in ("fit", "calibration", "evaluation"))
    if any(len(set(int(y[i]) for i in ids)) != 2 for ids in (train, calibration, evaluation)):
        raise ValueError("all chronological segments must include both settled outcome classes")
    # All thresholds and regularization constants are fixed before final evaluation.
    estimator = make_pipeline(StandardScaler(), LogisticRegression(
        C=.1, solver="lbfgs", max_iter=1000, random_state=73115,
    ))
    estimator.fit(x[train], y[train])
    raw_cal = estimator.predict_proba(x[calibration])[:, 1]
    raw_cal = np.clip(raw_cal, 1e-6, 1-1e-6)
    cal_logits = np.log(raw_cal / (1-raw_cal)).reshape(-1, 1)
    calibrator = LogisticRegression(C=1.0, solver="lbfgs", max_iter=1000, random_state=73115)
    calibrator.fit(cal_logits, y[calibration])
    raw_eval = np.clip(estimator.predict_proba(x[evaluation])[:, 1], 1e-6, 1-1e-6)
    logit_eval = np.log(raw_eval / (1-raw_eval)).reshape(-1, 1)
    probabilities = calibrator.predict_proba(logit_eval)[:, 1]
    labels = y[evaluation]
    metric = _metrics(labels, probabilities)
    frozen_correct = sum(
        _direction(prices[records[i]["ticker"]], records[i]) == int(y[i])
        for i in evaluation
    ) / len(evaluation)
    persistence_correct = sum(
        _direction(float(records[i]["context"][-1]), records[i]) == int(y[i])
        for i in evaluation
    ) / len(evaluation)
    scale = estimator.named_steps["standardscaler"]
    logistic = estimator.named_steps["logisticregression"]
    report: dict[str, Any] = {
        "experiment_kind": KIND,
        "model_checkpoint": MODEL,
        "input_sha256": prepared["input_sha256"],
        "dataset_sha256": prepared["dataset_sha256"],
        "market_scope": "DEVELOPMENT_ONLY",
        "market_count": len(records),
        "training_markets": len(train),
        "calibration_markets": len(calibration),
        "evaluation_markets": len(evaluation),
        "embargo_markets_per_boundary": 2,
        "adapter_accuracy": metric["accuracy"],
        "adapter_brier": metric["brier"],
        "adapter_log_loss": metric["log_loss"],
        "adapter_69_gate_qualified": metric["qualified"],
        "adapter_69_gate_coverage": metric["coverage"],
        "adapter_69_gate_accuracy": metric["qualified_accuracy"],
        "frozen_timesfm3_point_accuracy": frozen_correct,
        "last_completed_btc_price_accuracy": persistence_correct,
        "adapter_parameters": {
            "features": ["log_forecast_over_strike", "log_last_over_strike",
                         "log_forecast_over_last", "realized_vol_32_returns"],
            "regularization_C": .1,
            "fit_markets": len(train),
            "means": [float(v) for v in scale.mean_],
            "scales": [float(v) for v in scale.scale_],
            "coefficients": [float(v) for v in logistic.coef_[0]],
            "intercept": float(logistic.intercept_[0]),
            "calibration_coef": float(calibrator.coef_[0][0]),
            "calibration_intercept": float(calibrator.intercept_[0]),
        },
        "timesfm3_weights_finetuned": False,
        "promotion_eligible": False,
        "live_model_changed": False,
        "trade_pnl_dollars": None,
        "winner": None,
        "limitations": (
            "Historical development-only research, not a new sealed prospective lockbox. "
            "Only a supervised settlement head is trained, not TimesFM3 weights. "
            "Binance BTCUSDT is not the Kalshi settlement index or settlement average. "
            "Frozen TimesFM3 pretrained training-data overlap is unknown. "
            "Downloaded TimesFM3 weights/outputs are non-commercial and must not "
            "be used in live trading; no executable quote, fee, fill or P&L evaluation."
        ),
    }
    return report


def _quickcheck():
    import importlib.util
    path = Path(__file__).with_name("timesfm3_quickcheck.py")
    spec = importlib.util.spec_from_file_location("timesfm3_adapter_quickcheck", path)
    if spec is None or spec.loader is None:
        raise ValueError("TimesFM3 verified historical context helper missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_prepare(root: Path, out: Path) -> dict[str, Any]:
    """Use verified development group membership; never ask parquet for lockbox rows."""
    from kalshi_edge.bootstrap import training
    from kalshi_edge.bootstrap.config import BootstrapSettings
    from kalshi_edge.bootstrap.labels import normalize_market_label
    from kalshi_edge.bootstrap.provenance import verify_artifact
    import pyarrow.parquet as pq

    root = root.resolve()
    context = training._verified_context(root)
    groups = training._metadata_groups(context.dataset_path)
    tickers = select_development_markets(groups, set(context.plan.development_groups))
    if len(tickers) < MINIMUM_MARKETS:
        raise ValueError("not enough development markets; do not use protected lockbox")
    # Only need the exact checkpoint features. Never materialize calibration
    # or current untouched lockbox forecasts/labels in a research process.
    table = pq.read_table(context.dataset_path, columns=[
        "market_ticker", "checkpoint_ts_ns", "label_yes", "strike", "seconds_remaining",
    ], filters=[
        ("market_ticker", "in", list(tickers)),
        ("seconds_remaining", "==", 480.0),
    ])
    candidates = {}
    permitted = set(tickers)
    for row in table.to_pylist():
        ticker = row["market_ticker"]
        if ticker not in permitted:
            continue
        if ticker in candidates:
            raise ValueError(f"duplicate original 480s checkpoint: {ticker}")
        candidates[ticker] = row
    quick = _quickcheck()
    quick._verified_day_close_map = lru_cache(maxsize=3)(quick._verified_day_close_map)
    settings = BootstrapSettings(bootstrap_dir=root)
    records: list[dict[str, Any]] = []
    excluded: list[str] = []
    for count, ticker in enumerate(tickers, 1):
        row = candidates.get(ticker)
        if row is None:
            excluded.append(ticker)
            continue
        checkpoint = int(row["checkpoint_ts_ns"])
        market = root / "raw" / "kalshi" / "markets" / f"{ticker}.json"
        manifest = root / "manifests" / "kalshi" / "markets" / f"{ticker}.json.manifest.json"
        if not verify_artifact(market, manifest):
            raise ValueError(f"official Kalshi settlement manifest invalid: {ticker}")
        label = normalize_market_label(_read(market))
        if (label.ticker != ticker or label.close_ts_ns-checkpoint != 480*1_000_000_000
                or int(label.result == "yes") != row["label_yes"]
                or not math.isclose(float(row["strike"]), label.strike, abs_tol=1e-8, rel_tol=0)):
            raise ValueError(f"official Kalshi 480s settlement/strike mismatch: {ticker}")
        try:
            series = quick._causal_context_for_market(
                root, settings.binance_symbol, checkpoint // 1_000_000_000, CONTEXT_STEPS
            )
        except FileNotFoundError:
            # Missing local daily archive is an unavailable market, not
            # a synthetic backfilled training example.
            excluded.append(ticker)
            continue
        except ValueError as exc:
            # A missing five-second bar is an availability exclusion.
            # Manifest checksum/source-provenance errors must FAIL CLOSED.
            if "missing" not in str(exc).lower():
                raise
            excluded.append(ticker)
            continue
        records.append({
            "ticker": ticker,
            "market_date": quick.datetime.fromtimestamp(
                checkpoint / 1_000_000_000, quick.timezone.utc
            ).date().isoformat(),
            "checkpoint_ts_ns": checkpoint,
            "label_yes": int(row["label_yes"]),
            "strike": float(label.strike),
            "strike_type": label.strike_type,
            "context": series,
        })
        if count % 100 == 0 or count == len(tickers):
            print(f"TimesFM3 adapter source: {count}/{len(tickers)} checked, "
                  f"{len(records)} usable", flush=True)
    if len(records) < MINIMUM_MARKETS or len(records) < int(.90*len(tickers)):
        raise ValueError(
            f"insufficient causal Binance context coverage: {len(records)}/{len(tickers)}; "
            "do not silently cherry-pick a sparse market subset"
        )
    payload = {
        "experiment_kind": KIND,
        "market_scope": "DEVELOPMENT_ONLY",
        "sealed_current_lockbox_opened": False,
        "model_checkpoint": MODEL,
        "dataset_sha256": context.input_hashes["derived/features.parquet"],
        "context_steps": CONTEXT_STEPS,
        "predict_steps": PREDICT_STEPS,
        "source_markets": len(tickers),
        "excluded_missing_context": len(excluded),
        "records": records,
    }
    payload["input_sha256"] = _digest(payload)
    _validated_prepared(payload)
    if out.exists():
        existing = _read(out)
        if existing.get("input_sha256") != payload["input_sha256"]:
            raise ValueError("existing prepared research file differs; choose a new output directory")
        return {"status": "PREPARED_ALREADY_IDENTICAL", "markets": len(records),
                "output": str(out), "protected_lockbox_opened": False}
    _write(out, payload)
    return {"status": "PREPARED", "markets": len(records),
            "excluded_missing_context": len(excluded),
            "output": str(out), "protected_lockbox_opened": False}


def _read_results(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    if not lines:
        raise ValueError("empty TimesFM3 inference journal")
    header = json.loads(lines[0])
    if not isinstance(header, dict) or "header" not in header:
        raise ValueError("TimesFM3 inference journal header missing")
    results = [json.loads(line) for line in lines[1:]]
    return {**header["header"], "results": results}


def run_infer(prepared_path: Path, output: Path, *,
              accepted_noncommercial_license: bool, batch_size: int = 1) -> dict[str, Any]:
    """Resumable research-only GPU inference; never saves model weights."""
    if not accepted_noncommercial_license:
        raise ValueError(
            "explicit non-commercial license acknowledgement required; "
            "do not use self-hosted TimesFM3 for production or real-money trading"
        )
    prepared = _read(prepared_path)
    records = _validated_prepared(prepared)
    if type(batch_size) is not int or not 1 <= batch_size <= 4:
        raise ValueError("research GPU batch size must be 1-4")
    checkpoint = {"checkpoint": MODEL, "input_sha256": prepared["input_sha256"]}
    if output.exists():
        recorded = _read_results(output)
        if recorded["checkpoint"] != MODEL or recorded["input_sha256"] != checkpoint["input_sha256"]:
            raise ValueError("inference journal model or source hash mismatch")
        previous = recorded["results"]
        if len(previous) > len(records):
            raise ValueError("inference journal contains extra rows")
        for rec, result in zip(records, previous):
            if (rec["ticker"] != result.get("ticker") or
                    type(result.get("forecast_price")) not in (float, int) or
                    not math.isfinite(result["forecast_price"]) or
                    result["forecast_price"] <= 0):
                raise ValueError("inference journal has invalid or out-of-order forecast")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps({"header": checkpoint}, sort_keys=True)+"\n")
            handle.flush()
            os.fsync(handle.fileno())
        previous = []
    completed = len(previous)
    if completed == len(records):
        return {"status": "INFERENCE_COMPLETE", "count": completed,
                "output": str(output), "resumed": True}
    import numpy as np
    import torch
    from timesfm3 import TimesFM3Evaluator, ModelConfig
    if not torch.cuda.is_available():
        raise ValueError("CUDA PyTorch unavailable in isolated TimesFM3 research environment")
    forecaster = TimesFM3Evaluator(ModelConfig(
        checkpoint_path=MODEL, per_core_batch_size=batch_size, device="cuda",
    ))
    for start in range(completed, len(records), batch_size):
        batch = records[start:start+batch_size]
        contexts = [np.asarray(rec["context"], dtype=np.float32) for rec in batch]
        outputs = list(forecaster.predict_batch(
            contexts, horizon=PREDICT_STEPS,
            return_quantiles=False, use_symmetric_averaging=False,
        ))
        if len(outputs) != len(batch):
            raise ValueError("TimesFM3 returned an incomplete research prediction batch")
        lines = []
        for rec, predicted in zip(batch, outputs):
            price = float(np.asarray(predicted.forecast).reshape(-1)[-1])
            if not math.isfinite(price) or price <= 0:
                raise ValueError("TimesFM3 produced invalid terminal BTC price")
            lines.append(json.dumps({"ticker": rec["ticker"], "forecast_price": price}))
        with output.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines)+"\n")
            handle.flush()
            os.fsync(handle.fileno())
        print(f"TimesFM3 research inference: {min(start+len(batch),len(records))}/"
              f"{len(records)} markets", flush=True)
    return {"status": "INFERENCE_COMPLETE", "count": len(records),
            "output": str(output), "resumed": bool(previous)}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/bootstrap"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("prepare", help="build verified development-only 31k causal inputs")
    infer = commands.add_parser("infer", help="frozen TimesFM3 GPU inference, resumable")
    infer.add_argument("--batch-size", type=int, default=1)
    infer.add_argument("--accept-noncommercial-license", action="store_true")
    commands.add_parser("fit", help="train only a frozen-TimesFM3 settlement probability head")
    args = parser.parse_args(argv)
    location = args.root.resolve() / DIRECTORY
    prepared_path = location / "prepared.json"
    inference_path = location / "timesfm3.jsonl"
    report_path = location / "adapter_report.json"
    if args.command == "prepare":
        result = run_prepare(args.root, prepared_path)
    elif args.command == "infer":
        result = run_infer(prepared_path, inference_path,
                           accepted_noncommercial_license=args.accept_noncommercial_license,
                           batch_size=args.batch_size)
    else:
        report = train_adapter(_read(prepared_path), _read_results(inference_path))
        _write(report_path, report)
        result = {"status": "RESEARCH_ADAPTER_TRAINED",
                  "markets": report["market_count"], "evaluation_markets": report["evaluation_markets"],
                  "adapter_accuracy": report["adapter_accuracy"],
                  "timesfm3_accuracy": report["frozen_timesfm3_point_accuracy"],
                  "report_path": str(report_path),
                  "timesfm3_weights_finetuned": False, "live_model_changed": False}
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
