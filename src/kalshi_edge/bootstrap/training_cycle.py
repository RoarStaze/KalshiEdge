from __future__ import annotations

"""Four-round 3:1 retraining cycle with verifiable readiness and manual launch.

The three error-focused rounds and one correct-refresher round are counted only
after *successful independent lockbox evaluation*, regardless of promotion.
No live forecast, dataset or untouched lockbox is modified by status reads.
"""

import json
import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import artifact, training
from .correct_learning import verified_correct_pool
from .mistake_learning import verified_mistake_pool

LOG = logging.getLogger(__name__)
TARGET_CASE_MARKETS = 12
TARGET_NEW_MARKETS = 20
CYCLE = ("mistake", "mistake", "mistake", "correct")
CACHE_SECONDS = 30


class CycleError(RuntimeError):
    """Training cycle cannot safely continue."""


WORKER_STAGE_PROGRESS = {
    "STARTING": 0,
    "BUILDING_DATASET": 8,
    "VERIFYING_DATASET": 20,
    "VERIFYING": 20,
    "SPLITTING": 30,
    "FITTING": 48,
    "TRAINING": 48,
    "SCORING": 67,
    "EVALUATING": 67,
    "COMPARING": 83,
    "PROMOTION_GATE": 94,
    "COMPLETE": 100,
}
FORWARD_MODE = "forward_480"
BASELINE_MODE = "baseline"
MIN_RETRAIN_LOCKBOX_MARKETS = 80
ACTIVE_WORKER_STAGES = frozenset({
    "STARTING", "BUILDING_DATASET", "VERIFYING_DATASET",
    "VERIFYING", "SPLITTING", "FITTING", "TRAINING",
    "SCORING", "EVALUATING", "COMPARING", "PROMOTION_GATE",
})



def _select_training_mode(
    legacy: dict, forward: dict, baseline: dict | None = None,
) -> str | None:
    """Prefer existing deployable training before a research-only challenger."""
    if legacy.get("ready"):
        return legacy.get("mode") if legacy.get("mode") in ("mistake", "correct") else None
    if baseline is not None and baseline.get("ready"):
        return BASELINE_MODE
    return FORWARD_MODE if forward.get("ready") else None


def inspect_baseline_training_readiness(root: Path) -> dict[str, Any]:
    """Require signed source data and unused later 480s lockbox opportunity.

    The standard pipeline fits a complete, compatible bootstrap-hybrid model.
    It can be promoted only via the independent evaluation + strict 480s gates.
    """
    result: dict[str, Any] = {
        "mode": BASELINE_MODE, "ready": False,
        "status": "UNAVAILABLE", "fresh_evaluation_markets": 0,
        "required_fresh_markets": MIN_RETRAIN_LOCKBOX_MARKETS,
        "reason": "Verified full-model retraining inputs unavailable",
        "live_model_changed": False,
    }
    root = root.resolve()
    if not (root / "models" / "default.json").is_file():
        result["reason"] = "A promoted current model is required for same-market improvement comparison."
        return result
    try:
        context = training._verified_context(root)
    except Exception as exc:
        result["reason"] = f"Verified historical dataset not ready: {type(exc).__name__}: {exc}"
        return result
    plan = context.plan
    count = len(plan.lockbox_groups)
    result["fresh_evaluation_markets"] = count
    if count < MIN_RETRAIN_LOCKBOX_MARKETS:
        result["reason"] = (
            f"Only {count}/{MIN_RETRAIN_LOCKBOX_MARKETS} unused fresh whole markets "
            "available for independent 480s lockbox. Keep collecting/rebuilding verified data."
        )
        return result
    if len(plan.development_groups) < 80 or len(plan.calibration_groups) < 20:
        result["reason"] = (
            "Verified dataset needs at least 80 historical development and "
            "20 calibration market groups before full-model training."
        )
        return result
    result.update(
        ready=True, status="READY_FOR_FULL_MODEL_TRAINING",
        reason=(
            f"{count} unused verified lockbox markets available. "
            "Train full model, compare current and candidate at the 480s checkpoint, "
            "and promote only if both accuracy and coverage improve with stable scores."
        ),
    )
    return result





def _atomic_state(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("x", encoding="utf-8", newline="\n") as out:
            json.dump(data, out, sort_keys=True, separators=(",", ":"))
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _state_path(root: Path) -> Path:
    return root / "training_runs" / "current.json"


def _run_state(root: Path) -> dict[str, Any] | None:
    path = _state_path(root)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("run-state JSON must be an object")
        return payload
    except (OSError, ValueError) as exc:
        raise CycleError("training run-state corrupted; manual inspection required") from exc


def _completed_learning_rounds(root: Path) -> list[dict[str, Any]]:
    """Derive immutable cycle history from evaluated experiment hashes, not counters."""

    root = root.resolve()
    previous: list[dict[str, Any]] = []
    for report_path in sorted((root / "reports").glob("evaluation-*.json")):
        digest = report_path.stem.removeprefix("evaluation-")
        experiment_path = root / "models" / "experiments" / f"{digest}.json"
        if not experiment_path.is_file():
            # Legacy evaluations can precede learning-cycle infrastructure.
            continue
        try:
            bundle = artifact.load_model_bundle(experiment_path)
            source_kinds = {
                relative.split("/", 1)[0]
                for relative in bundle.input_hashes
                if relative.startswith(("mistake_pool/", "correct_pool/"))
            }
            if not source_kinds:
                continue
            if len(source_kinds) != 1:
                raise CycleError("experiment improperly combined learning pools")
            mode = "correct" if "correct_pool" in source_kinds else "mistake"
            data = json.loads(report_path.read_text(encoding="utf-8"))
            report = data["report"]
            decision = data["decision"]
            if (
                report["bundle_sha256"] != digest
                or report["lockbox_start_ts_ns"] != bundle.boundaries.lockbox_start_ts_ns
                or report["lockbox_end_ts_ns"] != bundle.boundaries.lockbox_end_ts_ns
                or not isinstance(decision["promoted"], bool)
            ):
                raise CycleError("learning-cycle evaluation metadata mismatch")
            previous.append({
                "mode": mode,
                "bundle_sha256": digest,
                "lockbox_start_ts_ns": bundle.boundaries.lockbox_start_ts_ns,
                "lockbox_end_ts_ns": bundle.boundaries.lockbox_end_ts_ns,
                "promoted": decision["promoted"],
            })
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise CycleError(f"cannot verify evaluated learning experiment {digest}") from exc

    previous.sort(key=lambda x: (x["lockbox_start_ts_ns"], x["bundle_sha256"]))
    last_end = -1
    for index, item in enumerate(previous):
        expected = CYCLE[index % len(CYCLE)]
        if item["mode"] != expected or item["lockbox_start_ts_ns"] <= last_end:
            raise CycleError("learning-cycle sequence or lockbox chronology inconsistent; refusing next training")
        item["round"] = index + 1
        last_end = item["lockbox_end_ts_ns"]
    return previous


def _pending_learning_experiments(root: Path) -> int:
    """Block a new round if a previously trained learning experiment is unevaluated."""

    count = 0
    for training_report in sorted((root / "reports").glob("training-*.json")):
        digest = training_report.stem.removeprefix("training-")
        if (root / "reports" / f"evaluation-{digest}.json").exists():
            continue
        path = root / "models" / "experiments" / f"{digest}.json"
        if not path.exists():
            raise CycleError(f"pending training experiment bundle missing: {digest}")
        try:
            bundle = artifact.load_model_bundle(path)
        except (OSError, ValueError) as exc:
            raise CycleError("pending experiment bundle failed hash verification") from exc
        if any(k.startswith(("mistake_pool/", "correct_pool/")) for k in bundle.input_hashes):
            count += 1
    return count


def _learning_pool(root: Path, mode: str, allowed: set[str] | None = None):
    if mode == "correct":
        return verified_correct_pool(root, allowed_tickers=allowed)
    return verified_mistake_pool(root, allowed_tickers=allowed)


def inspect_training_readiness(root: Path) -> dict[str, Any]:
    """A data progress percentage that NEVER reaches 100 unless training is eligible."""

    root = root.resolve()
    history = _completed_learning_rounds(root)
    completed = len(history)
    mode = CYCLE[completed % len(CYCLE)]
    cycle_position = completed % len(CYCLE) + 1
    result: dict[str, Any] = {
        "cycle": ["mistake", "mistake", "mistake", "correct"],
        "round_number": completed + 1,
        "cycle_position": cycle_position,
        "completed_rounds": completed,
        "mode": mode,
        "mode_label": "Correct refresher" if mode == "correct" else "Learn from mistakes",
        "total_enrolled": 0,
        "eligible_markets": 0,
        "target_markets": TARGET_CASE_MARKETS,
        "outcome_classes": 0,
        "new_evaluation_markets": 0,
        "target_new_markets": TARGET_NEW_MARKETS,
        "progress_percent": 0,
        "ready": False,
        "status": "COLLECTING",
        "reason": "Collecting verified settled forecasts and new evaluation markets.",
        "previous_round": history[-1] if history else None,
        "pending_experiments": 0,
    }

    try:
        previous_end = training._previous_lockbox_end_ts_ns(root)
        if previous_end is None:
            result["status"] = "BLOCKED"
            result["reason"] = "Promote and independently evaluate a baseline model before starting the learning cycle."
            return result
        root_models = root / "models" / "default.json"
        if not root_models.is_file():
            result["status"] = "BLOCKED"
            result["reason"] = "A promoted baseline model is required."
            return result

        path = root / "derived" / "features.parquet"
        if not path.is_file():
            result["status"] = "BLOCKED"
            result["reason"] = "Historical feature dataset is missing. Backfill and rebuild it."
            return result

        groups = training._metadata_groups(path)
        fresh = [g for g in groups if g[1] > previous_end]
        result["new_evaluation_markets"] = len(fresh)

        if len(fresh) >= TARGET_NEW_MARKETS:
            development, _calibration, _lockbox, _ = training._learning_windows(path, previous_end)
        else:
            # This is a collection estimate only. Unused new markets are not
            # selected until the distinct calibration/lockbox windows exist.
            development = [g for g in groups if g[2] <= previous_end]
        allowed = {g[0] for g in development}
        cases = _learning_pool(root, mode, allowed)
        result["eligible_markets"] = len(cases)
        result["outcome_classes"] = len({case.selected_label for case, _, _ in cases})

        # Display partial evidence collection, not the minimum gate ratio.
        # Using min() makes the UI read 0% whenever even one prerequisite
        # is missing, despite growth in the other independent categories.
        # These weights are a *readiness indicator*, not a fraction of
        # completed training, and NEVER authorize training by themselves.
        case_ratio = min(1.0, len(cases) / TARGET_CASE_MARKETS)
        class_ratio = min(1.0, result["outcome_classes"] / 2)
        fresh_ratio = min(1.0, len(fresh) / TARGET_NEW_MARKETS)
        result["progress_percent"] = min(
            99, int(100 * (0.4 * case_ratio + 0.2 * class_ratio + 0.4 * fresh_ratio))
        )
        result["total_enrolled"] = len(_learning_pool(root, mode))
        if len(fresh) < TARGET_NEW_MARKETS:
            result["reason"] = (
                f"{len(fresh)}/{TARGET_NEW_MARKETS} new markets since the last evaluated lockbox. "
                "Backfill and rebuild causal features for additional settled markets."
            )
            return result
        if len(cases) < TARGET_CASE_MARKETS:
            # New historical parquet rows do not retroactively create a
            # point-in-time final forecast or an officially settled mistake.
            if not cases:
                result["reason"] = (
                    f"0/{TARGET_CASE_MARKETS} eligible {mode} cases despite "
                    f"{len(fresh)} fresh historical feature markets. "
                    f"{result['total_enrolled']} verified settled cases enrolled overall; "
                    "none match the permitted development feature window. "
                    "Historical backfill alone cannot recreate 480s forecasts. "
                    "Review verified official settlements, mistake enrollment and "
                    "causal features; the separate prospective 480s trainer uses "
                    "immutable forward snapshots as they accumulate."
                )
            else:
                result["reason"] = (
                    f"{len(cases)}/{TARGET_CASE_MARKETS} eligible {mode} "
                    "officially settled cases with matching causal development features; "
                    f"{result['total_enrolled']} enrolled overall. "
                    "Additional historical market rows alone do not raise this count."
                )
            return result
        if result["outcome_classes"] < 2:
            result["reason"] = "Selected examples must contain both ABOVE and BELOW settlement outcomes."
            return result

        # Enforce the identical integrity and partition logic used by training,
        # without loading calibration or lockbox labels/features.
        context = training._verified_context(
            root, mistakes_only=mode == "mistake", correct_only=mode == "correct",
        )
        if len(context.plan.development_groups) != len(cases):
            result["reason"] = "Selected training-case count changed during verification."
            return result
        pending = _pending_learning_experiments(root)
        result["pending_experiments"] = pending
        if pending:
            result["status"] = "EVALUATION_PENDING"
            result["reason"] = "A previous learning experiment has not completed its lockbox evaluation."
            return result

        result["progress_percent"] = 100
        result["ready"] = True
        result["status"] = "TRAINING_READY"
        result["reason"] = "Data verified. Training can be started manually."
        return result
    except (Exception,) as exc:
        LOG.warning("Learning-cycle eligibility blocked: %s", exc)
        result["status"] = "BLOCKED"
        result["reason"] = f"{type(exc).__name__}: {exc}"
        return result


def _write_worker_state(root: Path, *, stage: str, mode: str,
                        round_number: int, **values) -> None:
    prior = _run_state(root)
    same_run = (prior is not None and prior.get("round_number") == round_number
                and prior.get("mode") == mode)
    retained = {}
    if same_run and stage != "STARTING":
        retained = {
            key: prior[key] for key in (
                "log_path", "worker_pid", "started_utc", "dataset_path",
                "dataset_markets_completed", "dataset_markets_total",
                "dataset_last_market", "dataset_action", "dataset_row_count",
                "dataset_market_count", "dataset_verified",
            ) if key in prior
        }
    now = datetime.now(timezone.utc).isoformat()
    percent = WORKER_STAGE_PROGRESS.get(
        stage, int(prior.get("progress_percent", 0)) if same_run else 0
    )
    events = list(prior.get("events", [])) if same_run and stage != "STARTING" else []
    if not events or events[-1].get("stage") != stage:
        events.append({"stage": stage, "at_utc": now, "progress_percent": percent})
    retained.setdefault("started_utc", now)
    _atomic_state(_state_path(root), {
        **retained,
        "stage": stage,
        "mode": mode,
        "round_number": round_number,
        "progress_percent": percent,
        "events": events[-32:],
        "updated_utc": now,
        **values,
    })


def _forward_failure_reasons(report: dict[str, Any]) -> list[str]:
    """Explain failing acceptance criteria with actual matched-market numbers."""
    status = report.get("status")
    if status == "INSUFFICIENT_PAIRED_VALIDATION":
        return [report.get("reason") or "Not enough same-market validation comparisons"]
    if status not in ("VALIDATION_JOINT_GAIN", "NO_VALIDATED_JOINT_GAIN"):
        return [report.get("reason") or f"Forward training unavailable: {status}"]

    if status == "VALIDATION_JOINT_GAIN":
        return [
            "Retrospective joint improvement is NOT live promotion approval.",
            "A new, independent prospective cohort after the model is frozen, "
            "with matching live feature compatibility and exchange cost evidence, "
            "is required before activation.",
        ]

    candidate = report.get("candidate_69_gate") or {}
    incumbent = report.get("incumbent_69_gate") or {}
    n = report.get("matched_validation_markets") or candidate.get("settled_markets") or 0
    cq, iq = candidate.get("qualified") or 0, incumbent.get("qualified") or 0
    ca, ia = candidate.get("qualified_accuracy"), incumbent.get("qualified_accuracy")
    def pct(value: Any) -> str:
        return f"{100 * value:.1f}%" if isinstance(value, (int, float)) else "unavailable"

    reasons = []
    if cq <= iq:
        reasons.append(
            f"Coverage failed: candidate {cq}/{n} ({pct(candidate.get('coverage'))}) "
            f"vs current model {iq}/{n} ({pct(incumbent.get('coverage'))}); "
            f"{iq-cq} fewer qualifying predictions."
        )
    if ca is None or (ia is not None and ca < ia):
        reasons.append(f"Accuracy failed: candidate {pct(ca)} vs current model {pct(ia)}.")
    cm = report.get("candidate_probability_metrics") or {}
    im = report.get("incumbent_probability_metrics") or {}
    for metric, label in (("brier", "Brier score"), ("log_loss", "Log loss")):
        candidate_value, incumbent_value = cm.get(metric), im.get(metric)
        if (isinstance(candidate_value, (int, float))
                and isinstance(incumbent_value, (int, float))
                and candidate_value > incumbent_value):
            reasons.append(
                f"{label} worsened: candidate {candidate_value:.4f}, "
                f"current model {incumbent_value:.4f} (lower is better)."
            )
    return reasons or [
        "Candidate did not pass the complete accuracy, coverage and proper-score "
        "gates on the same later settled markets."
    ]


def run_forward_training_worker(root: Path) -> int:
    """Persistent offline 480s training. A research score is never a live PASS."""
    from ..launcher import SingleStarter
    from . import forward_learning_480

    root = root.resolve()
    mode, round_number = FORWARD_MODE, 0
    with SingleStarter(root / "training_runs" / "worker.lock"):
        try:
            _write_worker_state(root, stage="BUILDING_DATASET", mode=mode,
                                round_number=round_number,
                                dataset_action="Building point-in-time forward 480s snapshot dataset")
            dataset_path, ready = forward_learning_480.create_verified_forward_dataset(root)
            _write_worker_state(
                root, stage="VERIFYING_DATASET", mode=mode,
                round_number=round_number, dataset_path=str(dataset_path),
                dataset_market_count=ready["eligible_480_markets"],
                dataset_row_count=ready["eligible_480_markets"],
                dataset_verified=True,
                dataset_action="Rechecked frozen dataset SHA-256 and official settlements",
            )

            def progress(stage: str) -> None:
                if stage not in ("VERIFYING", "SPLITTING", "FITTING", "SCORING",
                                 "COMPARING"):
                    raise CycleError(f"unexpected forward training stage: {stage}")
                _write_worker_state(root, stage=stage, mode=mode,
                                    round_number=round_number)

            result = forward_learning_480.evaluate_verified_forward_candidate(
                root, progress=progress, dataset_path=dataset_path,
            )
            _write_worker_state(root, stage="PROMOTION_GATE", mode=mode,
                                round_number=round_number)
            decision = (
                "NOT_DEPLOYABLE" if result.get("status") == "VALIDATION_JOINT_GAIN"
                else "VALIDATION_FAILED" if result.get("status") == "NO_VALIDATED_JOINT_GAIN"
                else "INSUFFICIENT_EVIDENCE"
            )
            reasons = _forward_failure_reasons(result)
            # Save the actual measured report for refresh/restart review.
            report_name = f"forward-480-{int(time.time_ns())}-{uuid.uuid4().hex[:10]}.json"
            report_path = root / "training_runs" / "reports" / report_name
            _atomic_state(report_path, result)
            _write_worker_state(
                root, stage="COMPLETE", mode=mode, round_number=round_number,
                decision=decision, promoted=False, reasons=reasons,
                results=result, report_path=str(report_path),
            )
            return 0 if decision in ("VALIDATION_FAILED", "NOT_DEPLOYABLE") else 2
        except Exception as exc:
            LOG.exception("Verified 480s training failed")
            _write_worker_state(
                root, stage="FAILED", mode=mode, round_number=round_number,
                decision="ERROR", promoted=False,
                error=f"{type(exc).__name__}: {exc}",
            )
            return 2


def run_training_worker(root: Path, *, mode: str, round_number: int, git_sha: str) -> int:
    """Long-running, explicitly user-triggered train + one-time evaluation."""

    root = root.resolve()
    from ..launcher import SingleStarter
    from .config import BootstrapSettings
    from .evaluate import train_experiment, run_lockbox_evaluation

    with SingleStarter(root / "training_runs" / "worker.lock"):
        try:
            _write_worker_state(root, stage="VERIFYING", mode=mode, round_number=round_number)
            if mode == BASELINE_MODE:
                eligibility = inspect_baseline_training_readiness(root)
                if round_number != 0 or not eligibility["ready"]:
                    raise CycleError("full-model retraining evidence changed since Train Now: "
                                     + eligibility.get("reason", "not ready"))
            else:
                eligibility = inspect_training_readiness(root)
                if (not eligibility["ready"] or eligibility["mode"] != mode
                        or eligibility["round_number"] != round_number):
                    raise CycleError("learning eligibility/cycle changed since Train Now; refusing to run")
            settings = BootstrapSettings(bootstrap_dir=root)
            if mode == BASELINE_MODE:
                from . import dataset
                market_root = root / "raw" / "kalshi" / "markets"
                _write_worker_state(
                    root, stage="BUILDING_DATASET", mode=mode, round_number=round_number,
                    dataset_action="Rebuilding verified historical market dataset",
                )
                if market_root.exists():
                    report_tick = {"completed": -1}
                    def on_build_progress(completed: int, total: int, ticker: str, stage: str) -> None:
                        if completed != report_tick["completed"] and (
                            total <= 50 or completed == total or
                            completed % max(1, total//50) == 0
                        ):
                            report_tick["completed"] = completed
                            _write_worker_state(
                                root, stage="BUILDING_DATASET", mode=mode,
                                round_number=round_number,
                                dataset_markets_completed=completed,
                                dataset_markets_total=total,
                                dataset_last_market=ticker,
                            )
                    dataset_report = dataset.build_dataset(
                        root, settings, progress_callback=on_build_progress,
                    )
                    _write_worker_state(
                        root, stage="BUILDING_DATASET", mode=mode, round_number=round_number,
                        dataset_market_count=dataset_report.market_count,
                        dataset_row_count=dataset_report.row_count,
                        dataset_path=str(getattr(dataset_report, "dataset_path",
                                                 root / "derived" / "features.parquet")),
                        dataset_action="Rebuilt locally sourced, provenance-manifested dataset",
                    )
                else:
                    _write_worker_state(
                        root, stage="BUILDING_DATASET", mode=mode, round_number=round_number,
                        dataset_action="Reused existing SHA-verified dataset: raw rebuild source missing",
                    )
                _write_worker_state(
                    root, stage="VERIFYING_DATASET", mode=mode, round_number=round_number,
                )
                # Re-read full provenance, all source hashes, chronological
                # data plan and class labels AFTER dataset generation.
                context = training._verified_context(root)
                _write_worker_state(
                    root, stage="VERIFYING_DATASET", mode=mode, round_number=round_number,
                    dataset_verified=True,
                    dataset_action="Dataset manifest, provenance, and source hashes passed",
                )
                refreshed = inspect_baseline_training_readiness(root)
                if not refreshed["ready"]:
                    raise CycleError("Rebuilt dataset failed independent evaluation readiness: "
                                     + refreshed["reason"])
            else:
                _write_worker_state(
                    root, stage="VERIFYING_DATASET", mode=mode, round_number=round_number,
                    dataset_action="Verified pre-existing hash-sealed historical training dataset",
                )
                # The existing immutable-round readiness already calls
                # _verified_context with the exact enrolled-case selection.
                # Do not perform a redundant rescan between eligibility
                # verification and the actual fit.
                _write_worker_state(
                    root, stage="VERIFYING_DATASET", mode=mode, round_number=round_number,
                    dataset_verified=True,
                )
            _write_worker_state(root, stage="SPLITTING", mode=mode, round_number=round_number)
            _write_worker_state(root, stage="TRAINING", mode=mode, round_number=round_number)
            experiment = train_experiment(
                root, settings, git_sha=git_sha,
                mistakes_only=mode == "mistake",
                correct_only=mode == "correct",
            )
            _write_worker_state(
                root, stage="EVALUATING", mode=mode, round_number=round_number,
                experiment_sha256=experiment.experiment_bundle_sha256,
            )
            evaluation = run_lockbox_evaluation(root, experiment.experiment_path)
            _write_worker_state(root, stage="PROMOTION_GATE", mode=mode,
                                round_number=round_number)
            _write_worker_state(
                root, stage="COMPLETE", mode=mode, round_number=round_number,
                experiment_sha256=experiment.experiment_bundle_sha256,
                promoted=evaluation.decision.promoted,
                decision=("PROMOTED" if evaluation.decision.promoted else "VALIDATION_FAILED"),
                reasons=list(evaluation.decision.reason_codes) or (
                    ["Passed all independent lockbox and incumbent checks; "
                     "hash-sealed model promoted. Restart to activate."]
                    if evaluation.decision.promoted
                    else ["Independent lockbox validation did not authorize promotion."]),
                reason_codes=list(evaluation.decision.reason_codes),
                evaluation_report=str(evaluation.report_path),
            )
            return 0
        except Exception as exc:
            LOG.exception("Learning-cycle training failed")
            _write_worker_state(
                root, stage="FAILED", mode=mode, round_number=round_number,
                error=f"{type(exc).__name__}: {exc}",
            )
            return 2


class TrainingController:
    """Local-server owner of the one explicit Train Now subprocess."""

    def __init__(self, root: Path, project_root: Path | None = None) -> None:
        self.root = root.resolve()
        self.project_root = (project_root or Path.cwd()).resolve()
        self._lock = threading.RLock()
        self._process: subprocess.Popen | None = None
        self._cached: dict[str, Any] | None = None
        self._cached_at = 0.0
        self._forward_cached: dict[str, Any] | None = None
        self._forward_at = 0.0
        self._baseline_cached: dict[str, Any] | None = None
        self._baseline_at = 0.0
        self._log_handle = None

    def _readiness(self, *, force: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if force or self._cached is None or now - self._cached_at > CACHE_SECONDS:
            self._cached = inspect_training_readiness(self.root)
            self._cached_at = now
        return dict(self._cached)

    def status(self, *, force: bool = False) -> dict[str, Any]:
        with self._lock:
            info = self._readiness(force=force)
            now = time.monotonic()
            if (force or self._forward_cached is None
                    or now - self._forward_at > CACHE_SECONDS):
                try:
                    from .forward_learning_480 import inspect_forward_training_readiness
                    self._forward_cached = inspect_forward_training_readiness(self.root)
                except Exception as exc:
                    LOG.warning("Forward 480s training evidence unavailable: %s", exc)
                    self._forward_cached = {
                        "status": "EVIDENCE_UNAVAILABLE", "ready": False,
                        "eligible_480_markets": 0, "required_markets": 120,
                        "progress_percent": 0,
                        "reason": f"{type(exc).__name__}: {exc}",
                        "live_model_changed": False,
                    }
                self._forward_at = now
            info["forward_480"] = dict(self._forward_cached)
            if (force or self._baseline_cached is None
                    or now - self._baseline_at > CACHE_SECONDS):
                self._baseline_cached = inspect_baseline_training_readiness(self.root)
                self._baseline_at = now
            info["baseline_retrain"] = dict(self._baseline_cached)
            selected = _select_training_mode(
                info, info["forward_480"], info["baseline_retrain"],
            )
            info["selected_mode"] = selected
            info["train_available"] = selected is not None
            info["train_reason"] = (
                "Verified forward 480s data ready for supervised training and validation."
                if selected == FORWARD_MODE else
                info["baseline_retrain"].get("reason") if selected == BASELINE_MODE else
                info.get("reason") if selected is None else
                "Learning cycle ready for independent lockbox training and promotion evaluation."
            )
            from .model_identity import current_model_identity
            info["current_model"] = current_model_identity(self.root)
            info["run"] = _run_state(self.root)
            if self._process is not None:
                code = self._process.poll()
                info["running"] = code is None
                if code is not None:
                    info["worker_exit_code"] = code
                    if info["run"] is not None and info["run"].get("stage") in ACTIVE_WORKER_STAGES:
                        info["run"] = {
                            **info["run"], "stage": "INTERRUPTED",
                            "error": f"worker exited with status {code}",
                        }
                    self._process = None
                    if self._log_handle is not None:
                        self._log_handle.close()
                        self._log_handle = None
                    # Reflect a newly evaluated round immediately after completion.
                    self._cached_at = 0
                    self._baseline_at = 0
                    self._forward_at = 0
            else:
                info["running"] = False
                if info["run"] is not None and info["run"].get("stage") in ACTIVE_WORKER_STAGES:
                    info["run"] = {
                        **info["run"], "stage": "INTERRUPTED",
                        "error": "Dashboard restarted before this run completed",
                    }
            return info

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                raise CycleError("a training session is already running")
            info = self._readiness(force=True)
            from .forward_learning_480 import inspect_forward_training_readiness
            forward = inspect_forward_training_readiness(self.root)
            self._forward_cached = forward
            self._forward_at = time.monotonic()
            baseline = inspect_baseline_training_readiness(self.root)
            self._baseline_cached = baseline
            self._baseline_at = time.monotonic()
            selected = _select_training_mode(info, forward, baseline)
            if selected is None:
                raise CycleError(
                    "No eligible training path: historical cycle: "
                    + str(info.get("reason", "unavailable"))
                    + " | full model: "
                    + str(baseline.get("reason", "unavailable"))
                    + " | verified 480s: "
                    + str(forward.get("reason", "unavailable"))
                )
            active_round = 0 if selected in (FORWARD_MODE, BASELINE_MODE) else info["round_number"]
            active_mode = selected

            git = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=self.project_root,
                check=True, capture_output=True, text=True, timeout=10,
            ).stdout.strip()
            if len(git) != 40 or any(c not in "0123456789abcdef" for c in git.lower()):
                raise CycleError("cannot verify current repository Git SHA")
            logfile = self.root / "training_runs" / (
                f"{'forward-480' if selected == FORWARD_MODE else 'full-model' if selected == BASELINE_MODE else 'round-'+str(active_round).zfill(4)}-{int(time.time_ns())}.log"
            )
            logfile.parent.mkdir(parents=True, exist_ok=True)
            self._log_handle = logfile.open("ab")
            _write_worker_state(
                self.root, stage="STARTING",
                mode=active_mode, round_number=active_round,
                log_path=str(logfile),
            )
            try:
                # Yield CPU priority to the latency-critical live forecasting
                # process when long experiment fitting is active on Windows.
                priority = subprocess.BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 0
                self._process = subprocess.Popen(
                    [
                        sys.executable, "-m", "kalshi_edge.bootstrap.training_cycle",
                        "--worker", "--root", str(self.root), "--mode", active_mode,
                        "--round-number", str(active_round),
                        "--git-sha", git,
                    ],
                    cwd=self.project_root,
                    stdin=subprocess.DEVNULL,
                    stdout=self._log_handle,
                    stderr=subprocess.STDOUT,
                    creationflags=priority,
                )
                _write_worker_state(
                    self.root, stage="STARTING", mode=active_mode,
                    round_number=active_round,
                    worker_pid=self._process.pid,
                    log_path=str(logfile),
                )
            except (OSError, subprocess.SubprocessError):
                self._log_handle.close()
                self._log_handle = None
                raise
            return {
                "started": True,
                "round_number": active_round,
                "mode": active_mode,
                "worker_pid": self._process.pid,
                "log_path": str(logfile),
            }

    def stop(self) -> None:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                self._process.terminate()
                try:
                    self._process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=5)
            self._process = None
            if self._log_handle is not None:
                self._log_handle.close()
                self._log_handle = None


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Internal supervised KalshiEdge learning-cycle worker")
    parser.add_argument("--worker", action="store_true", required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--mode", choices=("mistake", "correct", BASELINE_MODE, FORWARD_MODE), required=True)
    parser.add_argument("--round-number", type=int, required=True)
    parser.add_argument("--git-sha", required=True)
    args = parser.parse_args()
    if args.mode == FORWARD_MODE:
        if args.round_number != 0:
            raise CycleError("invalid forward-training round identity")
        return run_forward_training_worker(args.root)
    return run_training_worker(
        args.root, mode=args.mode,
        round_number=args.round_number, git_sha=args.git_sha,
    )


if __name__ == "__main__":
    raise SystemExit(main())
