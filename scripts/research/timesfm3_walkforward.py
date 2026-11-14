#!/usr/bin/env python3
"""Research-only 480s TimesFM3 vs chronologically REFITTED KalshiEdge pipeline.

This evaluates full KalshiEdge architecture re-trained on earlier history,
NOT existing promoted Model #1 weights. The currently unused promotion
lockbox and KalshiEdge live/trading state are never used or modified.

Requires appropriately licensed, non-commercial, non-production TimesFM3
weights for separate inference. See docs/TimesFM3_WALKFORWARD.md.
"""
from __future__ import annotations

import argparse
import base64
import dataclasses
import hashlib
import importlib.util
import json
import math
import pickle
import random
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Sequence

NS = 1_000_000_000
MODEL = "google/timesfm-3.0-pytorch"
RESEARCH_KIND = "TIMESFM3_CHRONOLOGICAL_REFIT"
OUTPUT_DIR = Path("data/bootstrap/training_runs/research/timesfm3_walkforward")


def _quickcheck():
    spec = importlib.util.spec_from_file_location(
        "timesfm3_quickcheck_research",
        Path(__file__).with_name("timesfm3_quickcheck.py"),
    )
    if spec is None or spec.loader is None:
        raise ValueError("existing TimesFM3 quickcheck is missing")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def chronological_folds(
    ordered_groups: Sequence[tuple[str, int, int]],
    *, train_markets: int, calibration_markets: int, test_markets: int,
    folds: int, embargo_markets: int = 1,
) -> list[dict[str, Any]]:
    """Expanding market-disjoint historical folds, determined without labels.

    Groups are the ORIGINAL available-development universe (not the fresh
    held-out lockbox). Each fold refits the production training pipeline,
    calibrates only on an intermediate chronological block, embargoes one
    whole market and scores on the next strictly later test block.
    """
    params = (train_markets, calibration_markets, test_markets, folds)
    if any(type(v) is not int or v < 1 for v in params):
        raise ValueError("train, calibration, test sizes and folds must be positive integers")
    if type(embargo_markets) is not int or embargo_markets < 1:
        raise ValueError("at least one whole-market embargo is required")
    groups = tuple(ordered_groups)
    if len(set(x[0] for x in groups)) != len(groups):
        raise ValueError("duplicate split market identity")
    for before, after in zip(groups, groups[1:]):
        if before[1] >= after[1] or before[2] >= after[1] or before[1] > before[2]:
            raise ValueError("market chronology is overlapping or out of order")
    if groups and groups[-1][1] > groups[-1][2]:
        raise ValueError("invalid final market interval")
    segment = calibration_markets + embargo_markets + test_markets
    total = train_markets + folds * segment
    if len(groups) < total:
        raise ValueError(f"insufficient historical development markets: need {total}, found {len(groups)}")
    selected = groups[-total:]
    plans: list[dict[str, Any]] = []
    for fold in range(folds):
        train_end = train_markets + fold * segment
        training = selected[:train_end]
        calibration = selected[train_end:train_end + calibration_markets]
        embargo = selected[train_end + calibration_markets:
                           train_end + calibration_markets + embargo_markets]
        test = selected[train_end + calibration_markets + embargo_markets:
                        train_end + segment]
        plan = {
            "fold": fold,
            "train": tuple(item[0] for item in training),
            "calibration": tuple(item[0] for item in calibration),
            "embargo": tuple(item[0] for item in embargo),
            "test": tuple(item[0] for item in test),
            "train_market_count": len(training),
            "test_market_count": len(test),
            "train_start_ns": training[0][1],
            "train_end_ns": training[-1][2],
            "calibration_start_ns": calibration[0][1],
            "calibration_end_ns": calibration[-1][2],
            "test_start_ns": test[0][1],
            "test_end_ns": test[-1][2],
        }
        if not (plan["train_end_ns"] < plan["calibration_start_ns"]
                < plan["calibration_end_ns"] < plan["test_start_ns"]
                <= plan["test_end_ns"]):
            raise ValueError("fold chronology overlaps train, calibration or test")
        plans.append(plan)
    return plans


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("invalid research JSON payload")
    return value


def _save(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".partial")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    temp.replace(path)


def _sha_payload(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, separators=(",", ":"), sort_keys=True).encode()).hexdigest()


def _current_git_sha() -> str:
    repo = Path(__file__).resolve().parents[2]
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    if len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha.lower()):
        raise ValueError("cannot establish research code revision")
    return sha.lower()


def _load_test_rows(dataset_path: Path, feature_names: tuple[str, ...], tickers: Sequence[str]):
    """Materialize ONLY 480-second rows for the predeclared test tickers."""
    import pyarrow.parquet as pq
    from kalshi_edge.bootstrap import training
    table = pq.read_table(dataset_path, columns=[
        "market_ticker", "market_date", "split_group_id", "checkpoint_ts_ns",
        "label_yes", *feature_names,
    ], filters=[
        ("market_ticker", "in", list(tickers)),
        ("seconds_remaining", "==", 480.0),
    ])
    rows = training._table_to_rows(table, feature_names)
    result = {}
    for row in rows:
        if row.market_ticker in result:
            raise ValueError(f"duplicate 480s historical row {row.market_ticker}")
        if row.market_ticker not in tickers:
            raise ValueError("unexpected out-of-fold market returned from parquet")
        result[row.market_ticker] = row
    if set(result) != set(tickers):
        raise ValueError(f"unavailable 480-second test observations: {len(set(tickers)-set(result))}")
    return [result[ticker] for ticker in tickers]


def _run_one_fold(
    *, root: Path, settings, verified_context, plan: dict[str, Any],
    git_sha: str, quickcheck,
) -> dict[str, Any]:
    from kalshi_edge.bootstrap import artifact, training
    from kalshi_edge.bootstrap.labels import normalize_market_label
    from kalshi_edge.bootstrap.provenance import verify_artifact

    boundaries = artifact.SplitBoundaries(
        train_start_ts_ns=plan["train_start_ns"],
        train_end_ts_ns=plan["train_end_ns"],
        calibration_start_ts_ns=plan["calibration_start_ns"],
        calibration_end_ts_ns=plan["calibration_end_ns"],
        lockbox_start_ts_ns=plan["test_start_ns"],
        lockbox_end_ts_ns=plan["test_end_ns"],
    )
    research_plan = training.PartitionPlan(
        development_groups=plan["train"],
        calibration_groups=plan["calibration"],
        lockbox_groups=plan["test"],
        boundaries=boundaries,
    )
    context = dataclasses.replace(verified_context, plan=research_plan)
    fold_number = plan["fold"] + 1

    print(f"Fold {fold_number}: fit {len(plan['train'])} historical markets, "
          f"calibrate {len(plan['calibration'])}, hold out {len(plan['test'])}", flush=True)

    def progress(done: int, total: int, message: str):
        # Verifiable completed training operations, no invented time ETA.
        if done != getattr(progress, "last", None):
            print(f"Fold {fold_number}: {done}/{total} training operations — {message}", flush=True)
            progress.last = done

    bundle = training.build_experiment_bundle(
        root, settings, git_sha=git_sha,
        research_context=context, progress=progress,
    )
    # Bundle exists only in memory. Never write to the model or default dirs.
    sealed = bundle.model_copy(update={"bundle_sha256": artifact.bundle_sha256(bundle)})
    pipeline = pickle.loads(artifact.serialized_pipeline_bytes(sealed))
    if not isinstance(pipeline, training.FittedBootstrapPipeline):
        raise ValueError("refitted pipeline failed integrity/type check")
    rows = _load_test_rows(verified_context.dataset_path, verified_context.feature_names, plan["test"])
    predicted = pipeline.predict(rows)["candidate"]
    records = []
    skipped = []
    for row, p in zip(rows, predicted, strict=True):
        ticker = row.market_ticker
        if not plan["test_start_ns"] <= row.checkpoint_ts_ns <= plan["test_end_ns"]:
            raise ValueError("out-of-window market checkpoint detected")
        market = root / "raw" / "kalshi" / "markets" / f"{ticker}.json"
        manifest = root / "manifests" / "kalshi" / "markets" / f"{ticker}.json.manifest.json"
        if not verify_artifact(market, manifest):
            raise ValueError(f"official Kalshi outcome manifest not verified: {ticker}")
        label = normalize_market_label(_json(market))
        if (label.close_ts_ns-row.checkpoint_ts_ns != 480*NS
                or row.label_yes != int(label.result == "yes")
                or not math.isclose(label.strike, row.features["strike"], abs_tol=1e-8, rel_tol=0.0)):
            raise ValueError(f"official Kalshi strike/outcome mismatch for {ticker}")
        try:
            series = quickcheck._causal_context_for_market(
                root, settings.binance_symbol, row.checkpoint_ts_ns//NS, 256)
        except (FileNotFoundError, ValueError, OSError):
            skipped.append(ticker)
            continue
        if not math.isfinite(float(p)) or not 0 <= p <= 1:
            raise ValueError("refit produced an invalid 480s probability")
        records.append({
            "ticker": ticker,
            "fold": plan["fold"],
            "market_date": row.market_date or datetime.fromtimestamp(
                row.checkpoint_ts_ns/NS, timezone.utc).date().isoformat(),
            "checkpoint_ts_ns": row.checkpoint_ts_ns,
            "label_yes": row.label_yes,
            "strike": label.strike,
            "strike_type": label.strike_type,
            "context": series,
            "kalshiedge_refit_p_yes": float(p),
        })
    if len(records) < max(8, int(len(rows) * .8)):
        raise ValueError(
            f"Fold {fold_number} only has {len(records)}/{len(rows)} complete causal BTC contexts; "
            "do not cherry-pick available test markets for an accuracy claim"
        )
    print(f"Fold {fold_number}: {len(records)}/{len(rows)} matched 480s records; "
          f"{len(skipped)} missing BTC contexts", flush=True)
    return {
        "fold": plan["fold"],
        "plan": plan,
        "model_bundle_sha256": sealed.bundle_sha256,
        "records": records,
        "excluded_missing_btc_context": skipped,
    }


def run_prepare(
    *, root: Path, out: Path, train_markets: int = 1200,
    calibration_markets: int = 200, test_markets: int = 256,
    folds: int = 3, embargo_markets: int = 1,
) -> dict[str, Any]:
    """Refit full pipeline on earlier markets, predict later groups, store no live model."""
    from kalshi_edge.bootstrap import training
    from kalshi_edge.bootstrap.config import BootstrapSettings
    root = root.resolve()
    base = training._verified_context(root)
    metadata = training._metadata_groups(base.dataset_path)
    allowed = set(base.plan.development_groups)
    available = [group for group in metadata if group[0] in allowed
                 and group[2] < base.plan.boundaries.calibration_start_ts_ns]
    plans = chronological_folds(
        available, train_markets=train_markets,
        calibration_markets=calibration_markets, test_markets=test_markets,
        folds=folds, embargo_markets=embargo_markets,
    )
    git_sha = _current_git_sha()
    settings = BootstrapSettings(bootstrap_dir=root)
    quick = _quickcheck()
    # Three verified daily archives at most, rather than repeatedly reading
    # the same 86,400-row Parquet for every 15-minute market in a day.
    quick._verified_day_close_map = lru_cache(maxsize=3)(quick._verified_day_close_map)

    signature = _sha_payload({
        "dataset": base.input_hashes["derived/features.parquet"],
        "code_sha": git_sha, "plans": plans, "btc_symbol": settings.binance_symbol,
        "calibration": "full-pipeline", "horizon_seconds": 480,
        "timesfm_context": 256,
    })
    out = out.resolve()
    all_results = []
    for plan in plans:
        checkpoint = out.parent / "fold_checkpoints" / f"fold_{plan['fold'] + 1:02d}.json"
        if checkpoint.exists():
            saved = _json(checkpoint)
            if (saved.get("signature") != signature
                    or _sha_payload(saved.get("plan")) != _sha_payload(plan)):
                raise ValueError(
                    f"saved research fold checkpoint has incompatible data/code/plan: {checkpoint}; "
                    "use a different --out directory rather than reusing stale predictions"
                )
            result = saved
            print(f"Fold {plan['fold']+1}: resumed verified matching research checkpoint", flush=True)
        else:
            result = _run_one_fold(
                root=root, settings=settings, verified_context=base,
                plan=plan, git_sha=git_sha, quickcheck=quick,
            )
            result["signature"] = signature
            _save(checkpoint, result)
        all_results.append(result)

    records = [rec for fold in all_results for rec in fold["records"]]
    if len({r["ticker"] for r in records}) != len(records):
        raise ValueError("duplicate test market across folds")
    payload = {
        "experiment_kind": RESEARCH_KIND,
        "model_name": "KalshiEdge full-pipeline refit",
        "target_seconds": 480,
        "timesfm_checkpoint": MODEL,
        "timesfm_pretrained_frozen": True,
        "model1_deployed_weights_compared": False,
        "sealed_current_lockbox_opened": False,
        "dataset_sha256": base.input_hashes["derived/features.parquet"],
        "git_sha": git_sha,
        "research_signature": signature,
        "folds": [{
            **fold["plan"],
            "model_bundle_sha256": fold["model_bundle_sha256"],
            "matched_markets": len(fold["records"]),
            "excluded_missing_btc_context": len(fold["excluded_missing_btc_context"]),
        } for fold in all_results],
        "records": records,
    }
    payload["input_sha256"] = _sha_payload(payload)
    _save(out, payload)
    return {
        "status": "CHRONOLOGICAL_REFIT_PREPARED",
        "folds": len(plans), "market_count": len(records),
        "requested_test_markets": test_markets * folds,
        "output": str(out), "sealed_current_lockbox_opened": False,
        "deployed_model1_weight_comparison": False,
        "ready_for_timesfm3_inference": True,
    }


def _accuracy(y: Sequence[int], decisions: Sequence[int]) -> float:
    return sum(int(a == b) for a, b in zip(y, decisions, strict=True)) / len(y)


def _date_block_ci(records: Sequence[dict[str, Any]], diffs: Sequence[int],
                   reps: int = 2000, seed: int = 73115) -> dict[str, float | int]:
    if type(reps) is not int or reps < 20:
        raise ValueError("at least 20 paired day bootstrap samples required")
    by_day: dict[str, list[int]] = defaultdict(list)
    for record, value in zip(records, diffs, strict=True):
        by_day[record["market_date"]].append(value)
    days = sorted(by_day)
    if not days:
        raise ValueError("no chronological market-date groups")
    rng = random.Random(seed)
    values = []
    for _ in range(reps):
        sampled = rng.choices(days, k=len(days))
        block = [x for day in sampled for x in by_day[day]]
        values.append(100 * sum(block) / len(block))
    values.sort()
    return {
        "low_pp": values[int(.025 * (reps-1))],
        "high_pp": values[int(.975 * (reps-1))],
        "resamples": reps,
        "day_blocks": len(days),
    }


def score_walkforward(
    prepared: dict[str, Any], timesfm: dict[str, Any],
    *, bootstrap_reps: int = 2000,
) -> dict[str, Any]:
    """Paired chronological held-out results, never a live promotion decision."""
    if prepared.get("experiment_kind") != RESEARCH_KIND or prepared.get("sealed_current_lockbox_opened") is not False:
        raise ValueError("non-chronological or protected current lockbox research input")
    if prepared.get("target_seconds") != 480 or prepared.get("model1_deployed_weights_compared") is not False:
        raise ValueError("invalid historical 480s refit provenance")
    if timesfm.get("checkpoint") != MODEL or timesfm.get("input_sha256") != prepared.get("input_sha256"):
        raise ValueError("TimesFM3 forecast input hash/checkpoint mismatch")
    recs = prepared.get("records")
    outputs = timesfm.get("results")
    folds = prepared.get("folds")
    if not isinstance(recs, list) or not recs or not isinstance(outputs, list) or not isinstance(folds, list) or not folds:
        raise ValueError("missing records or fold definitions")
    if len(outputs) != len(recs):
        raise ValueError("TimesFM3 predictions missing for some test markets")
    by_ticker = {r["ticker"]: r for r in recs}
    predicted = {r["ticker"]: r for r in outputs}
    if (len(by_ticker) != len(recs) or len(predicted) != len(outputs)
            or set(by_ticker) != set(predicted)):
        raise ValueError("duplicate, missing or extra TimesFM3 market ticker")
    allowed: dict[str, dict[str, Any]] = {}
    for index, fold in enumerate(folds):
        if fold.get("fold") != index or not (
            fold["train_end_ns"] < fold["calibration_start_ns"]
            <= fold["calibration_end_ns"] < fold["test_start_ns"]
            <= fold["test_end_ns"]
        ):
            raise ValueError("historical fold chronology violates research protocol")
        for ticker in fold["test"]:
            if ticker in allowed:
                raise ValueError("test market repeated across chronological folds")
            allowed[ticker] = fold
    if set(allowed) != set(by_ticker):
        raise ValueError("fold test membership does not equal matched market set")

    model_correct = tfm_correct = persistence_correct = 0
    only_model = only_tfm = 0
    brier = loss = 0.0
    qualifies = qualified_correct = 0
    diffs: list[int] = []
    fold_out = []
    details: list[tuple[dict[str, Any], int, int, int]] = []
    quick = _quickcheck()
    for rec in recs:
        ticker = rec["ticker"]
        fold = allowed[ticker]
        ts = int(rec["checkpoint_ts_ns"])
        if rec["fold"] != fold["fold"] or not fold["test_start_ns"] <= ts <= fold["test_end_ns"]:
            raise ValueError("test record outside its predeclared chronological fold")
        label = rec["label_yes"]
        p = float(rec["kalshiedge_refit_p_yes"])
        price = float(predicted[ticker]["forecast_price"])
        if type(label) is not int or label not in (0, 1) or not math.isfinite(p) or not 0 <= p <= 1:
            raise ValueError("invalid held-out label or refitted probability")
        mid = 1 if p >= .5 else 0
        tfm = quick._validated_price_result(
            price, strike=float(rec["strike"]), strike_type=rec["strike_type"])
        naive = quick._validated_price_result(
            float(rec["context"][-1]), strike=float(rec["strike"]),
            strike_type=rec["strike_type"])
        cm, ct, cn = (int(mid == label), int(tfm == label), int(naive == label))
        model_correct += cm
        tfm_correct += ct
        persistence_correct += cn
        only_model += int(cm == 1 and ct == 0)
        only_tfm += int(cm == 0 and ct == 1)
        diffs.append(ct - cm)
        brier += (p - label) ** 2
        c = min(1-1e-12, max(1e-12, p))
        loss -= label * math.log(c) + (1-label) * math.log1p(-c)
        if 2*abs(p-.5) >= .69:
            qualifies += 1
            qualified_correct += cm
        details.append((rec, cm, ct, cn))
    n = len(recs)
    for fold in folds:
        sample = [rec for rec, *_ in details if rec["fold"] == fold["fold"]]
        cm = [model_ok for rec, model_ok, *_ in details if rec["fold"] == fold["fold"]]
        ct = [tfm_ok for rec, _, tfm_ok, _ in details if rec["fold"] == fold["fold"]]
        if not sample or len(sample) < max(8, int(.8 * fold["test_market_count"])):
            raise ValueError("fold has insufficient complete matched test observations")
        fold_out.append({
            "fold": fold["fold"]+1, "markets": len(sample),
            "train_markets": fold["train_market_count"],
            "kalshiedge_refit_accuracy": sum(cm)/len(cm),
            "timesfm3_accuracy": sum(ct)/len(ct),
            "accuracy_delta_pp": 100 * (sum(ct)-sum(cm))/len(cm),
        })
    from scipy.stats import binomtest
    discordant = only_model + only_tfm
    exact_p = float(binomtest(min(only_tfm, only_model), discordant, .5).pvalue) if discordant else 1.0
    return {
        "experiment_kind": RESEARCH_KIND,
        "model_name": "KalshiEdge full-pipeline refit",
        "model1_deployed_weights_compared": False,
        "markets": n,
        "fold_count": len(folds),
        "kalshiedge_refit_accuracy": model_correct/n,
        "kalshiedge_refit_brier": brier/n,
        "kalshiedge_refit_log_loss": loss/n,
        "kalshiedge_refit_69_gate_qualified": qualifies,
        "kalshiedge_refit_69_gate_coverage": qualifies/n,
        "kalshiedge_refit_69_gate_accuracy": qualified_correct/qualifies if qualifies else None,
        "timesfm3_accuracy": tfm_correct/n,
        "timesfm3_brier": None,
        "timesfm3_log_loss": None,
        "timesfm3_69_gate_qualified": None,
        "timesfm3_69_gate_coverage": None,
        "persistence_accuracy": persistence_correct/n,
        "accuracy_delta_percentage_points": 100*(tfm_correct-model_correct)/n,
        "timesfm3_only_correct": only_tfm,
        "kalshiedge_refit_only_correct": only_model,
        "mcnemar_exact_two_sided_p": exact_p,
        "paired_date_block_ci_95": _date_block_ci(recs,diffs,reps=bootstrap_reps),
        "per_fold": fold_out,
        "promotion_eligible": False,
        "winner": None,
        "limitations": (
            "Full KalshiEdge pipeline was refitted for each chronological period, "
            "not the deployed Model #1 weights. These pre-existing historical "
            "development windows were not a newly sealed prospective evaluation. "
            "TimesFM3 pretraining series overlap cannot be exhaustively verified; "
            "Binance BTCUSDT is not Kalshi's BRTI settlement reference. "
            "TimesFM3 point forecast is not a calibrated ABOVE/BELOW probability, "
            "so its Brier/log loss/69% confidence coverage cannot be compared. "
            "Market-level McNemar assumes independence, while the paired date-block "
            "bootstrap accounts partially for autocorrelation. No forecast "
            "superiority is established for future market deployment. "
            "No production use of noncommercial TimesFM3 weights or their outputs."
        ),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare", help="fit full KalshiEdge pipeline in chronological historical folds")
    prep.add_argument("--root", type=Path, default=Path("data/bootstrap"))
    prep.add_argument("--out", type=Path, default=OUTPUT_DIR/"prepared.json")
    prep.add_argument("--folds", type=int, default=3)
    prep.add_argument("--train-markets", type=int, default=1200)
    prep.add_argument("--calibration-markets", type=int, default=200)
    prep.add_argument("--test-markets", type=int, default=256)
    prep.add_argument("--embargo-markets", type=int, default=1)
    infer = sub.add_parser("infer", help="zero-shot TimesFM3 in separate research-only Python environment")
    infer.add_argument("--prepared", type=Path, default=OUTPUT_DIR/"prepared.json")
    infer.add_argument("--out", type=Path, default=OUTPUT_DIR/"timesfm3.json")
    infer.add_argument("--batch-size", type=int, default=1)
    infer.add_argument("--accept-noncommercial-license", action="store_true")
    score = sub.add_parser("score", help="paired chronological analysis; no automatic model promotion")
    score.add_argument("--prepared", type=Path, default=OUTPUT_DIR/"prepared.json")
    score.add_argument("--timesfm3", type=Path, default=OUTPUT_DIR/"timesfm3.json")
    score.add_argument("--out", type=Path, default=OUTPUT_DIR/"score.json")
    args = parser.parse_args(argv)
    if args.command == "prepare":
        result = run_prepare(
            root=args.root, out=args.out, folds=args.folds,
            train_markets=args.train_markets,
            calibration_markets=args.calibration_markets,
            test_markets=args.test_markets, embargo_markets=args.embargo_markets,
        )
    elif args.command == "infer":
        quick = _quickcheck()
        result = quick.run_infer(
            args.prepared, args.out,
            accepted_noncommercial_license=args.accept_noncommercial_license,
            batch_size=args.batch_size,
        )
    else:
        result = score_walkforward(_json(args.prepared), _json(args.timesfm3))
        _save(args.out,result)
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
