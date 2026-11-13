from __future__ import annotations

"""Four-round 3:1 retraining cycle with verifiable readiness and manual launch.

The three error-focused rounds and one correct-refresher round are counted only
after *successful independent lockbox evaluation*, regardless of promotion.
No live forecast, dataset or untouched lockbox is modified by status reads.
"""

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import artifact, training, training_pause, training_process_control
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
    "SCORING", "EVALUATING", "COMPARING", "PROMOTION_GATE", "RESUMING",
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
                "dataset_progress_phase", "dataset_last_market", "dataset_action", "dataset_row_count",
                "dataset_market_count", "dataset_verified", "experiment_path",
                "experiment_sha256", "resume_stage", "run_id", "git_sha",
                "fit_steps_completed", "fit_steps_total", "fit_step_detail",
                "worker_identity", "suspended_in_memory", "origin_git_sha",
                "forked_from_run_id", "forked_from_git_sha", "recovery_kind",
            ) if key in prior
        }
    now = datetime.now(timezone.utc).isoformat()
    percent = WORKER_STAGE_PROGRESS.get(
        stage, int(prior.get("progress_percent", 0)) if same_run else 0
    )
    if same_run and stage != "STARTING" and prior.get("resume_stage"):
        percent = max(percent, int(prior.get("progress_percent", 0)))
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


def _suspend_training_fit(root: Path, run: dict[str, Any], *,
                          manual: bool) -> bool:
    """Freeze only an identity-verified Stage-4 worker and persist the pause.

    The OS retains its native optimizer state while the app is closed.
    A missing process / forbidden suspend falls back to cooperative pause,
    so we never report PAUSED until a real suspension succeeds.
    """
    run_id, pid, token = (run.get("run_id"), run.get("worker_pid"),
                          run.get("worker_identity"))
    if not isinstance(run_id, str):
        raise CycleError("training pause requires an authenticated run ID")
    training_pause.set_pause(root, run_id=run_id, manual=manual)
    if run.get("stage") not in ("TRAINING", "FITTING"):
        return False
    if (type(pid) is not int or not isinstance(token, str)
            or not training_process_control.is_alive(pid, token)):
        return False
    try:
        training_process_control.suspend(pid)
    except (OSError, training_process_control.ProcessControlError) as exc:
        LOG.warning("Instant fitting pause unavailable; keeping safe pause intent: %s", exc)
        return False
    try:
        _write_worker_state(
            root, stage="PAUSED", mode=run["mode"],
            round_number=run["round_number"],
            resume_stage=run["stage"],
            pause_reason="manual" if manual else "shutdown",
            worker_pid=pid, worker_identity=token,
            suspended_in_memory=True,
        )
    except Exception:
        # Don't leave an OS-frozen worker with no durable pause record.
        training_process_control.resume(pid)
        raise
    return True


def _thaw_training_fit(root: Path, run: dict[str, Any]) -> bool:
    """Resume same PID only when its recorded creation identity still matches."""
    pid, token, run_id = (run.get("worker_pid"),
                          run.get("worker_identity"),run.get("run_id"))
    if (type(pid) is not int or not isinstance(token, str)
            or not isinstance(run_id, str)
            or not training_process_control.is_alive(pid, token)):
        return False
    previous=run.get("resume_stage")
    if previous not in ("TRAINING", "FITTING"):
        return False
    old_intent=training_pause.read_pause(root)
    training_pause.clear_pause(root, run_id=run_id)
    try:
        _write_worker_state(
            root, stage=previous, mode=run["mode"],
            round_number=run["round_number"],
            suspended_in_memory=False,
            pause_reason=None,
        )
        training_process_control.resume(pid)
    except Exception:
        training_pause.set_pause(
            root, run_id=run_id,
            manual=bool(old_intent.get("manual")),
        )
        _write_worker_state(
            root, stage="PAUSED", mode=run["mode"],
            round_number=run["round_number"],
            resume_stage=previous, suspended_in_memory=True,
        )
        raise
    return True


def _save_paused_state(root: Path, *, mode: str, round_number: int,
                       reason: str = "manual") -> None:
    """Write acknowledged PAUSED only after the last safe checkpoint."""
    prior = _run_state(root) or {}
    checkpoint = prior.get("stage", "VERIFYING")
    if checkpoint in ("PAUSED", "RESUMING"):
        checkpoint = prior.get("resume_stage") or "VERIFYING"
    _write_worker_state(
        root, stage="PAUSED", mode=mode, round_number=round_number,
        resume_stage=checkpoint, pause_reason=reason,
        error=None,
    )


def _pause_boundary(root: Path, *, mode: str, round_number: int,
                    stage: str, **kwargs: Any) -> None:
    """Complete and save the stage transition before checking pause intent."""
    _write_worker_state(root, stage=stage, mode=mode,
                        round_number=round_number, **kwargs)
    current = _run_state(root) or {}
    training_pause.check_pause(root, current.get("run_id", ""))


def _resume_experiment_path(root: Path, saved: dict[str, Any]) -> Path | None:
    if saved.get("mode") not in (BASELINE_MODE, "mistake", "correct"):
        return None
    if saved.get("resume_stage") not in (
        "EVALUATING", "SCORING", "COMPARING", "PROMOTION_GATE",
    ):
        return None
    name = saved.get("experiment_path")
    if not isinstance(name, str):
        return None
    root = root.resolve()
    path = Path(name).resolve()
    try:
        path.relative_to(root / "models" / "experiments")
    except ValueError:
        raise CycleError("resume experiment path is outside authenticated model storage")
    if path.suffix != ".json" or not path.exists():
        raise CycleError("saved experiment artifact is unavailable; cannot safely resume")
    if (root / "reports" / f"evaluation-{path.stem}.json").exists():
        raise CycleError(
            "evaluation already recorded for this candidate; refusing to replay "
            "the one-time promotion/lockbox evaluation"
        )
    return path


def _market_progress_reporter(
    root: Path, *, mode: str, round_number: int,
    clock: Callable[[], float] = time.monotonic,
    min_markets: int = 1, min_interval_seconds: float = 0.25,
    run_id: str | None = None, initial_completed: int = 0,
) -> Callable[[int, int, str, str], None]:
    """Stream verified market-file and feature-build counts independently.

    Publish the first count of each phase immediately, followed by every
    one item when time allows and at most four disk updates per second. Keep the fast
    worker-only endpoint independent of expensive training readiness checks.
    """
    if min_markets < 1 or min_interval_seconds < 0:
        raise ValueError("invalid market progress throttle")
    last_count = initial_completed if initial_completed > 0 else -1
    last_time = float("-inf")
    last_phase = "markets"
    resume_skip_active = initial_completed > 0

    def report(completed: int, total: int, ticker: str, stage: str) -> None:
        nonlocal last_count, last_time, last_phase, resume_skip_active
        if total <= 0 or not 0 <= completed <= total:
            raise ValueError("invalid dataset progress count")
        if run_id is not None and stage not in ("labels", "checkpoints"):
            # Feature-row processing continues to honor every market's
            # safe pause boundary, including the checkpointed replay loop.
            training_pause.check_pause(root, run_id)

        phase = stage if stage in ("labels", "checkpoints") else "markets"
        if phase in ("labels", "checkpoints"):
            # Replayed checkpoints are counted as verified again during a
            # new scan. Old feature cursors cannot suppress these updates.
            resume_skip_active = False
        if phase == "markets" and stage == "markets" and resume_skip_active:
            if completed <= initial_completed:
                return

        phase_changed = phase != last_phase
        if phase_changed:
            last_count = -1
            last_time = float("-inf")

        terminal = completed == total or stage not in ("labels", "checkpoints", "markets")
        now = clock()
        first = last_count < 0
        if not first and not terminal:
            if completed - last_count < min_markets:
                return
            if now - last_time < min_interval_seconds:
                return
        if not first and completed == last_count and not terminal:
            return

        if run_id is not None and stage in ("labels", "checkpoints"):
            # The preliminary verification pass has no row checkpoint to
            # protect; checking pause on its bounded progress writes avoids
            # a redundant disk read for every market JSON file.
            training_pause.check_pause(root, run_id)

        action = (
            "Finalizing dataset: validating leakage and writing artifacts"
            if stage not in ("labels", "checkpoints", "markets") else
            "Verifying market files against official source manifests"
            if phase_changed and phase == "labels" else
            "Validating saved dataset checkpoints and source SHA-256"
            if phase_changed and phase == "checkpoints" else
            "Building verified historical market feature rows"
            if phase_changed and phase == "markets" else None
        )
        _write_worker_state(
            root, stage="BUILDING_DATASET", mode=mode,
            round_number=round_number,
            dataset_progress_phase=phase,
            dataset_markets_completed=completed,
            dataset_markets_total=total,
            dataset_last_market=ticker,
            **({"dataset_action": action} if action is not None else {}),
        )
        last_count, last_time, last_phase = completed, now, phase

    return report


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
            previous = _run_state(root) or {}
            saved = previous.get("dataset_path")
            can_reuse = bool(
                previous.get("resume_stage") in (
                    "VERIFYING_DATASET", "SPLITTING", "VERIFYING",
                    "FITTING", "SCORING", "COMPARING", "PROMOTION_GATE",
                ) and previous.get("dataset_verified") and isinstance(saved, str)
            )
            _pause_boundary(root, stage="BUILDING_DATASET", mode=mode,
                            round_number=round_number,
                            dataset_action=(
                                "Reusing the same sealed 480s dataset"
                                if can_reuse else
                                "Building point-in-time forward 480s snapshot dataset"
                            ))
            if can_reuse:
                dataset_path = Path(saved).resolve()
                try:
                    dataset_path.relative_to(root / "training_runs" / "datasets")
                except ValueError as exc:
                    raise CycleError("sealed forward dataset path escapes training storage") from exc
                # SHA-256, original incumbent calls and settled labels all
                # must match the previously frozen dataset.
                rows, _incumbents = forward_learning_480.load_verified_forward_dataset(dataset_path)
                ready = forward_learning_480.assess_forward_training_readiness(rows)
                if not ready["ready"]:
                    raise CycleError("previously sealed 480s evidence is no longer eligible")
            else:
                dataset_path, ready = forward_learning_480.create_verified_forward_dataset(root)
            _pause_boundary(
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
                _pause_boundary(root, stage=stage, mode=mode,
                                round_number=round_number)

            result = forward_learning_480.evaluate_verified_forward_candidate(
                root, progress=progress, dataset_path=dataset_path,
            )
            _pause_boundary(root, stage="PROMOTION_GATE", mode=mode,
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
        except training_pause.TrainingPaused:
            intent=training_pause.read_pause(root)
            _save_paused_state(
                root, mode=mode, round_number=round_number,
                reason="manual" if intent.get("manual") else "shutdown",
            )
            return 0
        except Exception as exc:
            LOG.exception("Verified 480s training failed")
            _write_worker_state(
                root, stage="FAILED", mode=mode, round_number=round_number,
                decision="ERROR", promoted=False,
                error=f"{type(exc).__name__}: {exc}",
            )
            return 2


def _verified_lockbox_comparison(path: Path) -> dict[str, Any]:
    """Read only actual saved exchange-lockbox evaluation output for the UI."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        result = payload["report"]
        gates = result.get("decision_metrics") or {}
        prefix = "retrain_480_" if result.get("requires_retrain_480_gate") else "volatility_480_"
        cand = gates.get(prefix + "candidate")
        prior = gates.get(prefix + "incumbent")
        if not isinstance(cand, dict) or not isinstance(prior, dict):
            return {}
        def view(value: dict) -> dict:
            total = int(value["total_markets"])
            qualified = int(value["qualified_markets"])
            correct = int(value["qualified_correct"])
            if not (total >= qualified >= correct >= 0):
                raise ValueError("Invalid lockbox prediction counts")
            return {
                "settled_markets": total,
                "qualified": qualified,
                "correct": correct,
                "coverage": qualified/total if total else None,
                "qualified_accuracy": correct/qualified if qualified else None,
            }
        scores = result.get("metrics") or {}
        return {
            "candidate_69_gate": view(cand),
            "incumbent_69_gate": view(prior),
            "candidate_probability_metrics": scores.get(prefix + "candidate"),
            "incumbent_probability_metrics": scores.get(prefix + "incumbent"),
            "validation_source": "HASH_VERIFIED_FRESH_LOCKBOX_480",
        }
    except (OSError, ValueError, KeyError, TypeError, ArithmeticError):
        return {}


def _lockbox_rejection_reasons(codes: tuple[str, ...],
                               comparison: dict[str, Any]) -> list[str]:
    out = []
    cand = comparison.get("candidate_69_gate") or {}
    base = comparison.get("incumbent_69_gate") or {}
    if cand and base:
        n = cand.get("settled_markets") or 0
        ca, ia = cand.get("qualified") or 0, base.get("qualified") or 0
        cp, ip = cand.get("qualified_accuracy"), base.get("qualified_accuracy")
        def pct(x: Any) -> str:
            return f"{x*100:.1f}%" if isinstance(x, (int, float)) else "unavailable"
        out.append(
            f"Fresh 480s lockbox: candidate {ca}/{n} qualifying at "
            f"{pct(cp)} accuracy vs current model {ia}/{n} at "
            f"{pct(ip)} accuracy."
        )
    for code in codes:
        if code.endswith("COVERAGE_NOT_INCREASED"):
            out.append("Coverage did not increase: fewer or equal usable forecasts at the 69% gate.")
        elif code.endswith("QUALIFIED_ACCURACY_NOT_IMPROVED"):
            out.append("Qualified directional accuracy did not improve at the eight-minute checkpoint.")
        elif code.endswith("QUALIFIED_ACCURACY_DEGRADED"):
            out.append("Qualified directional accuracy is worse than the current model.")
        elif "Brier" in code or "CALIBRATION" in code or "ECE" in code:
            out.append("Probability quality or calibration gate failed: " + code.replace("_"," ").lower() + ".")
        else:
            out.append("Promotion gate: " + code.replace("_"," ").lower() + ".")
    return out



# Fail closed on provenance/checksum errors. Only genuine missing history (or
# a verified raw ZIP awaiting normalization) may be repaired automatically.
_MISSING_BINANCE_DAY = re.compile(
    r"(?:required Binance history is missing for |"
    r"Binance normalized history is missing for verified raw archive )"
    r"(\d{4}-\d{2}-\d{2})\Z"
)
MAX_TRAIN_BINANCE_DAY_REPAIRS = 32


def _build_dataset_with_binance_repair(
    root: Path,
    settings: Any,
    *,
    progress_callback: Callable[[int, int, str, str], None] | None,
    mode: str,
    round_number: int,
) -> Any:
    """Repair one official, checksum-verified Binance day and resume checkpoints.

    Never invent OHLCV, skip eligible settled markets, alter existing
    provenance, or retry an unchanged missing date indefinitely.
    """
    from . import backfill, dataset

    repaired: set[date] = set()
    while True:
        try:
            return dataset.build_dataset(
                root, settings, progress_callback=progress_callback,
            )
        except dataset.DatasetBuildError as exc:
            match = _MISSING_BINANCE_DAY.fullmatch(str(exc))
            if match is None:
                raise
            missing_day = date.fromisoformat(match.group(1))
            if missing_day in repaired:
                raise CycleError(
                    f"Binance {missing_day} still missing after verified repair; "
                    "saved dataset checkpoints retained. Check the official "
                    "archive and local disk before retrying Train Now."
                ) from exc
            if len(repaired) >= MAX_TRAIN_BINANCE_DAY_REPAIRS:
                raise CycleError(
                    f"More than {MAX_TRAIN_BINANCE_DAY_REPAIRS} Binance archive "
                    "dates need recovery. Saved dataset checkpoints retained; "
                    "run full verified Binance backfill before Train Now."
                ) from exc

            _pause_boundary(
                root, stage="BUILDING_DATASET",
                mode=mode, round_number=round_number,
                dataset_action=(
                    f"Recovering verified Binance BTC 1-second history "
                    f"for {missing_day} from the official archive"
                ),
            )
            try:
                report = backfill.backfill_binance(
                    settings, required_dates=(missing_day,),
                )
            except Exception as backfill_error:
                raise CycleError(
                    f"Official Binance archive repair failed for "
                    f"{missing_day}: {type(backfill_error).__name__}: "
                    f"{backfill_error}. No unverified replacement was used; "
                    "saved dataset checkpoints retained."
                ) from backfill_error
            if missing_day.isoformat() in report.unavailable_dates:
                raise CycleError(
                    f"Official Binance archive for {missing_day} has not "
                    "reached the verified daily publication window; "
                    "saved dataset checkpoints retained."
                )
            repaired.add(missing_day)
            _pause_boundary(
                root, stage="BUILDING_DATASET",
                mode=mode, round_number=round_number,
                dataset_action=(
                    f"Verified Binance archive for {missing_day}; "
                    "continuing saved dataset checkpoints"
                ),
            )



def _fit_progress_reporter(
    root: Path, *, mode: str, round_number: int,
) -> Callable[[int, int, str], None]:
    """Publish completed model operations, never elapsed-time estimates.

    The small worker-state file is read by the existing 250 ms progress
    endpoint; no expensive readiness check or model scoring is added.
    """
    def report(completed: int, total: int, detail: str) -> None:
        if (isinstance(completed, bool) or isinstance(total, bool)
                or not isinstance(completed, int) or not isinstance(total, int)
                or total <= 0 or not 0 <= completed <= total):
            raise ValueError("invalid completed fitting operation count")
        if not isinstance(detail, str) or not detail.strip():
            raise ValueError("fitting progress requires a named operation")
        _write_worker_state(
            root, stage="TRAINING", mode=mode, round_number=round_number,
            fit_steps_completed=completed, fit_steps_total=total,
            fit_step_detail=detail[:180],
        )
    return report


def run_training_worker(root: Path, *, mode: str, round_number: int, git_sha: str) -> int:
    """Long-running, explicitly user-triggered train + one-time evaluation."""

    root = root.resolve()
    from ..launcher import SingleStarter
    from .config import BootstrapSettings
    from .evaluate import train_experiment, run_lockbox_evaluation

    with SingleStarter(root / "training_runs" / "worker.lock"):
        try:
            resume_state = _run_state(root) or {}
            saved_experiment = _resume_experiment_path(root, resume_state)
            _pause_boundary(root, stage="VERIFYING", mode=mode, round_number=round_number)
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
                verified_existing=bool(
                    resume_state.get("dataset_verified")
                    and resume_state.get("resume_stage") in (
                        "SPLITTING","TRAINING","FITTING","EVALUATING",
                        "SCORING","COMPARING","PROMOTION_GATE",
                    )
                    and (root / "derived" / "features.parquet").is_file()
                )
                _pause_boundary(
                    root, stage="BUILDING_DATASET", mode=mode, round_number=round_number,
                    dataset_action=(
                        "Reusing authenticated completed dataset checkpoint"
                        if verified_existing else "Rebuilding verified historical market dataset"
                    ),
                )
                if verified_existing:
                    pass  # _verified_context below revalidates saved hashes.
                elif market_root.exists():
                    on_build_progress = _market_progress_reporter(
                        root, mode=mode, round_number=round_number,
                        run_id=(_run_state(root) or {}).get("run_id"),
                        initial_completed=int(resume_state.get("dataset_markets_completed") or 0)
                        if resume_state.get("resume_stage") in (
                            "BUILDING_DATASET","VERIFYING_DATASET",
                        ) else 0,
                    )
                    dataset_report = _build_dataset_with_binance_repair(
                        root, settings, progress_callback=on_build_progress,
                        mode=mode, round_number=round_number,
                    )
                    _pause_boundary(
                        root, stage="BUILDING_DATASET", mode=mode, round_number=round_number,
                        dataset_market_count=dataset_report.market_count,
                        dataset_row_count=dataset_report.row_count,
                        dataset_path=str(getattr(dataset_report, "dataset_path",
                                                 root / "derived" / "features.parquet")),
                        dataset_action="Rebuilt locally sourced, provenance-manifested dataset",
                    )
                else:
                    _pause_boundary(
                        root, stage="BUILDING_DATASET", mode=mode, round_number=round_number,
                        dataset_action="Reused existing SHA-verified dataset: raw rebuild source missing",
                    )
                _pause_boundary(
                    root, stage="VERIFYING_DATASET", mode=mode, round_number=round_number,
                )
                # Re-read full provenance, all source hashes, chronological
                # data plan and class labels AFTER dataset generation.
                context = training._verified_context(root)
                _pause_boundary(
                    root, stage="VERIFYING_DATASET", mode=mode, round_number=round_number,
                    dataset_verified=True,
                    dataset_action="Dataset manifest, provenance, and source hashes passed",
                )
                refreshed = inspect_baseline_training_readiness(root)
                if not refreshed["ready"]:
                    raise CycleError("Rebuilt dataset failed independent evaluation readiness: "
                                     + refreshed["reason"])
            else:
                _pause_boundary(
                    root, stage="VERIFYING_DATASET", mode=mode, round_number=round_number,
                    dataset_action="Verified pre-existing hash-sealed historical training dataset",
                )
                # The existing immutable-round readiness already calls
                # _verified_context with the exact enrolled-case selection.
                # Do not perform a redundant rescan between eligibility
                # verification and the actual fit.
                _pause_boundary(
                    root, stage="VERIFYING_DATASET", mode=mode, round_number=round_number,
                    dataset_verified=True,
                )
            _pause_boundary(root, stage="SPLITTING", mode=mode, round_number=round_number)
            if saved_experiment is not None:
                bundle = artifact.load_model_bundle(saved_experiment)
                if bundle.stage != "experiment":
                    raise CycleError("resume artifact is not a verified fitted experiment")
                from types import SimpleNamespace
                experiment = SimpleNamespace(
                    experiment_path=saved_experiment,
                    experiment_bundle_sha256=bundle.bundle_sha256,
                )
                _pause_boundary(
                    root, stage="EVALUATING", mode=mode, round_number=round_number,
                    experiment_path=str(saved_experiment),
                    experiment_sha256=bundle.bundle_sha256,
                )
            else:
                _pause_boundary(
                    root, stage="TRAINING", mode=mode, round_number=round_number,
                    fit_steps_completed=0, fit_steps_total=0,
                    fit_step_detail="Preparing verified model-fitting inputs",
                )
                on_fit_progress = _fit_progress_reporter(
                    root, mode=mode, round_number=round_number,
                )
                experiment = train_experiment(
                    root, settings, git_sha=git_sha,
                    mistakes_only=mode == "mistake",
                    correct_only=mode == "correct",
                    progress=on_fit_progress,
                )
                _pause_boundary(
                    root, stage="EVALUATING", mode=mode, round_number=round_number,
                    experiment_path=str(experiment.experiment_path),
                    experiment_sha256=experiment.experiment_bundle_sha256,
                )
            def report_progress(stage: str) -> None:
                if stage not in {"SCORING", "COMPARING", "PROMOTION_GATE"}:
                    raise CycleError(f"invalid full-model evaluation milestone: {stage}")
                _pause_boundary(root, stage=stage, mode=mode,
                                round_number=round_number)

            evaluation = run_lockbox_evaluation(
                root, experiment.experiment_path, progress=report_progress,
            )
            comparison = _verified_lockbox_comparison(evaluation.report_path)
            from .model_identity import current_model_identity
            model_id = current_model_identity(root) if evaluation.decision.promoted else {}
            rejection = _lockbox_rejection_reasons(
                evaluation.decision.reason_codes, comparison,
            )
            _write_worker_state(
                root, stage="COMPLETE", mode=mode, round_number=round_number,
                experiment_sha256=experiment.experiment_bundle_sha256,
                promoted=evaluation.decision.promoted,
                promoted_model_number=model_id.get("model_number"),
                decision=("PROMOTED" if evaluation.decision.promoted else "VALIDATION_FAILED"),
                reasons=(
                    ["Passed verified lockbox and incumbent checks. "
                     "Hash-sealed Model #"
                     + str(model_id.get("model_number") or "?")
                     + " is installed; restart to use it."]
                    if evaluation.decision.promoted else
                    rejection or ["Independent lockbox evaluation did not authorize promotion."]
                ),
                results=comparison,
                reason_codes=list(evaluation.decision.reason_codes),
                evaluation_report=str(evaluation.report_path),
            )
            return 0
        except training_pause.TrainingPaused:
            intent = training_pause.read_pause(root)
            _save_paused_state(
                root, mode=mode, round_number=round_number,
                reason="manual" if intent.get("manual") else "shutdown",
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
        self._adopted_pid: int | None = None
        self._adopted_identity: str | None = None
        self._suspended = False
        self._cached: dict[str, Any] | None = None
        self._cached_at = 0.0
        self._forward_cached: dict[str, Any] | None = None
        self._forward_at = 0.0
        self._baseline_cached: dict[str, Any] | None = None
        self._baseline_at = 0.0
        self._log_handle = None

    def _tracked_pid(self) -> int | None:
        if self._process is not None and self._process.poll() is None:
            return self._process.pid
        if (self._adopted_pid is not None
                and self._adopted_identity is not None
                and training_process_control.is_alive(
                    self._adopted_pid, self._adopted_identity)):
            return self._adopted_pid
        return None

    def _worker_running(self) -> bool:
        return self._tracked_pid() is not None and not self._suspended

    def _readiness(self, *, force: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if force or self._cached is None or now - self._cached_at > CACHE_SECONDS:
            self._cached = inspect_training_readiness(self.root)
            self._cached_at = now
        return dict(self._cached)

    def _pause_flags(self, run: dict[str, Any] | None,
                     running: bool) -> dict[str, bool]:
        intent = training_pause.read_pause(self.root)
        matches = bool(run and intent.get("run_id") == run.get("run_id"))
        requested = bool(matches and intent.get("pause_requested"))
        manual = bool(requested and intent.get("manual"))
        paused = bool(run and run.get("stage") == "PAUSED" and not running)
        return {
            "pause_requested": requested,
            "paused": paused,
            "can_resume": paused,
            "auto_resume": bool(run and
                                run.get("stage") in ACTIVE_WORKER_STAGES | {"PAUSED"}
                                and not manual),
        }

    def progress(self) -> dict[str, Any]:
        """Read persisted worker state; never recompute historical/model readiness."""
        with self._lock:
            run = _run_state(self.root)
            process = self._process
            running = self._worker_running()
            if (not running and run is not None
                    and run.get("stage") in ACTIVE_WORKER_STAGES):
                error = ("worker exited before recording completion"
                         if process is not None else
                         "Dashboard restarted before this run completed")
                run = {**run, "stage": "INTERRUPTED", "error": error}
            return {"running": running, "run": run,
                    **self._pause_flags(run, running)}

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
            if self._process is not None or self._adopted_pid is not None:
                code = (self._process.poll() if self._process is not None
                        else None if self._tracked_pid() is not None else 0)
                info["running"] = self._worker_running()
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
            info.update(self._pause_flags(info["run"], info["running"]))
            if info["paused"]:
                info["train_available"] = False
            return info

    def _git_sha(self) -> str:
        try:
            sha = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=self.project_root,
                check=True, capture_output=True, text=True, timeout=10,
            ).stdout.strip().lower()
        except (OSError, subprocess.SubprocessError) as exc:
            raise CycleError("cannot verify installed repository Git SHA") from exc
        if len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
            raise CycleError("cannot verify installed repository Git SHA")
        return sha

    def _unsafe_worktree_changes(self) -> list[str]:
        """Identify only dirty paths capable of changing the training runtime.

        Editable installs rewrite tracked egg-info metadata; local backup
        snapshots and separate, untracked tools are not imported by the model
        worker. Retain those files but fail closed for new/modified source,
        installed dependencies, ambiguous Git status or staging operations.
        """
        status = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z",
             "--untracked-files=all"],
            cwd=self.project_root, check=True, capture_output=True,
            text=True, timeout=10,
        ).stdout
        if not status:
            return []
        records = status.split("\0")
        if records[-1] != "":
            return ["invalid Git status framing"]
        nonruntime_metadata = frozenset({
            "src/kalshi_edge.egg-info/PKG-INFO",
            "src/kalshi_edge.egg-info/SOURCES.txt",
            "src/kalshi_edge.egg-info/dependency_links.txt",
            "src/kalshi_edge.egg-info/entry_points.txt",
            "src/kalshi_edge.egg-info/requires.txt",
            "src/kalshi_edge.egg-info/top_level.txt",
        })
        unsafe: list[str] = []
        for item in records[:-1]:
            if len(item) < 4 or item[2] != " ":
                unsafe.append("unrecognized Git status record")
                continue
            flags, name = item[:2], item[3:]
            if flags in (" M", "M ", "MM", "??") and name in nonruntime_metadata:
                continue
            if flags == "??" and name.startswith("tools/"):
                # Untracked utilities are outside the installed kalshi_edge
                # package and must not erase an independently verified dataset.
                continue
            if (flags == "??" and name.endswith(".bak")
                    and name.startswith(("src/kalshi_edge/bootstrap/",
                                         "tests/bootstrap/"))):
                continue
            unsafe.append(f"{flags} {name}")
        return unsafe

    def _compatible_frozen_source(self, saved_sha: str, current_sha: str) -> bool:
        """Allow an existing frozen PID to thaw only after non-worker Git changes.

        A Python process may lazily import modules after it resumes. Even an
        exact suspended PID is NOT safe to thaw under modified training/runtime
        source. Changes limited to documentation, tests, CI, and standalone
        browser UI modules do not affect its training code. Fail closed if
        repository history or the working tree cannot be verified.
        """
        if saved_sha == current_sha:
            return True
        if (not isinstance(saved_sha, str)
                or re.fullmatch(r"[0-9a-f]{40}", saved_sha) is None):
            return False
        browser_only = frozenset({
            "src/kalshi_edge/bootstrap/dashboard_ui.py",
            "src/kalshi_edge/bootstrap/site_pages.py",
            "src/kalshi_edge/bootstrap/site_motion.py",
            "src/kalshi_edge/bootstrap/round_price_ui.py",
            "src/kalshi_edge/bootstrap/manual_trade_ui.py",
            "src/kalshi_edge/bootstrap/autotrader_ui.py",
            "src/kalshi_edge/bootstrap/api_credentials_ui.py",
            "src/kalshi_edge/bootstrap/preview_site.py",
            # Accounting/reporting changes do not enter the training worker.
            "src/kalshi_edge/trading/autotrade_history.py",
            "src/kalshi_edge/trading/autotrade_pnl.py",
            "src/kalshi_edge/trading/autotrade_profit_audit.py",
        })
        try:
            diff = subprocess.run(
                ["git", "diff", "--no-renames", "--name-only",
                 saved_sha, current_sha, "--"],
                cwd=self.project_root, check=True,
                capture_output=True, text=True, timeout=10,
            ).stdout.splitlines()
            dirty = self._unsafe_worktree_changes()
        except (OSError, subprocess.SubprocessError):
            return False
        if dirty:
            return False

        # This controller class runs only in the parent dashboard. Its code
        # can change without replacing an already frozen training subprocess,
        # provided every worker definition and import outside it is identical.
        controller_module = "src/kalshi_edge/bootstrap/training_cycle.py"
        if controller_module in diff:
            def worker_region(contents: str) -> tuple[str, str] | None:
                prefix, separator, remainder = contents.partition(
                    "\nclass TrainingController:"
                )
                if not separator:
                    return None
                _parent_class, separator, suffix = remainder.partition(
                    "\ndef main() -> int:"
                )
                if not separator:
                    return None
                # Adding original-commit retention is only audit metadata:
                # normalize this exact, known tuple extension when comparing
                # old paused worker code with the upgraded controller.
                prefix = prefix.replace(
                    '"worker_identity", "suspended_in_memory", "origin_git_sha",',
                    '"worker_identity", "suspended_in_memory",',
                )
                return prefix, suffix

            try:
                original = subprocess.run(
                    ["git", "show", f"{saved_sha}:{controller_module}"],
                    cwd=self.project_root, check=True,
                    capture_output=True, text=True, timeout=10,
                ).stdout
                updated = subprocess.run(
                    ["git", "show", f"{current_sha}:{controller_module}"],
                    cwd=self.project_root, check=True,
                    capture_output=True, text=True, timeout=10,
                ).stdout
            except (OSError, subprocess.SubprocessError):
                return False
            original_worker = worker_region(original)
            if original_worker is None or original_worker != worker_region(updated):
                return False

        return all(
            name.startswith(("docs/", "tests/", ".github/"))
            or name in browser_only or name == controller_module
            for name in diff
        )

    def _fork_unfinished_fit_after_update(self, saved: dict[str, Any],
                                          current_sha: str) -> dict[str, Any]:
        """Start a NEW fit attempt only from an authenticated pre-evaluation checkpoint.

        A partially trained model cannot continue under changed Python code.
        The new worker verifies dataset hashes, schema, inputs and partitions
        before fitting; earlier artifacts and the original run are archived.
        """
        def blocked(detail: str) -> None:
            raise CycleError(
                "Git version changed since training paused; cannot safely restart "
                "the unfinished fit: " + detail
            )

        old_sha = saved.get("git_sha")
        old_id = saved.get("run_id")
        checkpoint = saved.get("resume_stage") or saved.get("stage")
        if (saved.get("mode") != BASELINE_MODE
                or checkpoint not in ("TRAINING", "FITTING")
                or saved.get("stage") not in ("PAUSED", "TRAINING", "FITTING")
                or saved.get("dataset_verified") is not True
                or saved.get("experiment_path") or saved.get("experiment_sha256")
                or saved.get("evaluation_report") or saved.get("promoted")
                or not isinstance(old_id, str)
                or re.fullmatch(r"[A-Za-z0-9_-]{1,100}", old_id) is None
                or not isinstance(old_sha, str)
                or re.fullmatch(r"[0-9a-f]{40}", old_sha) is None):
            blocked("only an unfinished, verified Stage-4 full-model fit can be retried")
        # The actual worker validates source manifests and provenance again.
        dataset_paths = (
            self.root / "derived" / "features.parquet",
            self.root / "derived" / "features.provenance.json",
            self.root / "manifests" / "dataset" / "features.parquet.manifest.json",
        )
        if not all(path.is_file() for path in dataset_paths):
            blocked("one or more saved dataset or provenance files are missing")

        try:
            unsafe = self._unsafe_worktree_changes()
            ancestor = subprocess.run(
                ["git", "merge-base", "--is-ancestor", old_sha, current_sha],
                cwd=self.project_root, check=False, capture_output=True, timeout=10,
            )
            changed = subprocess.run(
                ["git", "diff", "--no-renames", "--name-only",
                 old_sha, current_sha, "--"],
                cwd=self.project_root, check=True, capture_output=True,
                text=True, timeout=10,
            ).stdout.splitlines()
        except (OSError, subprocess.SubprocessError):
            blocked("the saved Git revision and working tree cannot be verified")
        if unsafe:
            blocked("the working tree contains runtime/dependency changes: "
                    + ", ".join(unsafe[:5]))
        if ancestor.returncode != 0:
            blocked("the saved commit is not in the installed commit history")
        dependency_files = {"requirements.lock", "requirements.research.lock",
                            "pyproject.toml"}
        if dependency_files.intersection(changed):
            blocked("the dependency lockfiles changed; restore the original environment")

        recovery = self.root / "training_runs" / "recovery" / f"{old_id}.json"
        if not recovery.exists():
            _atomic_state(recovery, saved)
        new_id = uuid.uuid4().hex
        _write_worker_state(
            self.root, stage="PAUSED", mode=saved["mode"],
            round_number=saved["round_number"],
            run_id=new_id, git_sha=current_sha,
            origin_git_sha=saved.get("origin_git_sha") or old_sha,
            forked_from_run_id=old_id, forked_from_git_sha=old_sha,
            recovery_kind="RETRY_UNFINISHED_FIT_AFTER_UPDATE",
            resume_stage="TRAINING", worker_pid=None, worker_identity=None,
            suspended_in_memory=False, error=None,
            fit_steps_completed=0, fit_steps_total=0,
            fit_step_detail="Verifying saved dataset before fitting with updated code",
        )
        return _run_state(self.root) or {}

    def _launch_resumed_checkpoint(self, old: dict[str, Any],
                                   saved: dict[str, Any], *,
                                   manual: bool) -> dict[str, Any]:
        """A spawn failure must never consume the original manual pause."""
        forked = old.get("run_id") != saved.get("run_id")
        run_id = saved.get("run_id")
        if not isinstance(run_id, str):
            raise CycleError("missing verified training resume identity")
        if forked:
            training_pause.reset_pause(self.root, run_id=run_id)
        else:
            training_pause.clear_pause(self.root, run_id=run_id)
        try:
            return self._launch_existing(saved, resume=True)
        except Exception:
            if forked and self._process is None and self._adopted_pid is None:
                _atomic_state(_state_path(self.root), old)
                training_pause.set_pause(
                    self.root, run_id=old["run_id"], manual=manual,
                )
            raise

    def _prepare_checkpoint_resume(self, saved: dict[str, Any],
                                   current_sha: str) -> dict[str, Any]:
        """Rebase a stopped worker only if its training code is unchanged.

        Preserve the first commit in the run history; the resumed process
        always receives the actual installed commit in --git-sha. Completed
        authenticated dataset/artifact checkpoints and the run ID are kept.
        """
        prior_sha = saved.get("git_sha")
        if prior_sha == current_sha:
            return saved
        if not self._compatible_frozen_source(prior_sha, current_sha):
            return self._fork_unfinished_fit_after_update(saved, current_sha)
        origin = saved.get("origin_git_sha") or prior_sha
        _write_worker_state(
            self.root, stage=saved["stage"], mode=saved["mode"],
            round_number=saved["round_number"],
            git_sha=current_sha, origin_git_sha=origin, error=None,
        )
        return _run_state(self.root) or {**saved, "git_sha": current_sha,
                                         "origin_git_sha": origin}

    def _launch_existing(self, saved: dict[str, Any], *, resume: bool) -> dict[str, Any]:
        """One subprocess launch path for explicit training and restart recovery."""
        mode = saved.get("mode")
        round_number = saved.get("round_number")
        run_id = saved.get("run_id")
        git = saved.get("git_sha")
        if (mode not in ("mistake", "correct", BASELINE_MODE, FORWARD_MODE)
                or type(round_number) is not int or round_number < 0
                or not isinstance(run_id, str) or not isinstance(git, str)
                or len(git) != 40):
            raise CycleError("saved training run identity is incomplete")
        if self._tracked_pid() is not None:
            raise CycleError("a training worker is already running")
        logfile = self.root / "training_runs" / (
            f"{'resume' if resume else mode}-{int(time.time_ns())}.log"
        )
        logfile.parent.mkdir(parents=True, exist_ok=True)
        self._log_handle = logfile.open("ab")
        previous_stage = saved.get("resume_stage") or saved.get("stage")
        try:
            if resume:
                _write_worker_state(
                    self.root, stage="RESUMING", mode=mode,
                    round_number=round_number, resume_stage=previous_stage,
                    log_path=str(logfile), run_id=run_id, git_sha=git,
                    error=None,
                )
            else:
                _write_worker_state(
                    self.root, stage="STARTING", mode=mode,
                    round_number=round_number, log_path=str(logfile),
                    run_id=run_id, git_sha=git,
                )
            priority = subprocess.BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 0
            self._process = subprocess.Popen(
                [
                    sys.executable, "-m", "kalshi_edge.bootstrap.training_cycle",
                    "--worker", "--root", str(self.root), "--mode", mode,
                    "--round-number", str(round_number), "--git-sha", git,
                ],
                cwd=self.project_root,
                stdin=subprocess.DEVNULL,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                creationflags=priority,
            )
            try:
                identity_token=training_process_control.identity(self._process.pid)
            except OSError as exc:
                # A restricted OS or unavailable process inspection must
                # never block training; use the original safe pause fallback.
                LOG.warning("Instant pause unavailable for worker %s: %s",
                            self._process.pid, exc)
                identity_token=None
            _write_worker_state(
                self.root, stage=("RESUMING" if resume else "STARTING"),
                mode=mode, round_number=round_number,
                worker_pid=self._process.pid,
                worker_identity=identity_token,
                suspended_in_memory=False,
                log_path=str(logfile), run_id=run_id, git_sha=git,
                **({"resume_stage": previous_stage} if resume else {}),
            )
        except Exception:
            if self._log_handle is not None:
                self._log_handle.close()
                self._log_handle = None
            raise
        return {
            "started": True, "resumed": resume,
            "mode": mode, "round_number": round_number, "run_id": run_id,
            "worker_pid": self._process.pid,
            "log_path": str(logfile),
        }

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._tracked_pid() is not None:
                raise CycleError("a training session is already running")
            prior = _run_state(self.root)
            if prior is not None and prior.get("stage") == "PAUSED":
                raise CycleError("training is paused; press Resume, not Train Now")
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
                    + " | full model: " + str(baseline.get("reason", "unavailable"))
                    + " | verified 480s: " + str(forward.get("reason", "unavailable"))
                )
            round_number = 0 if selected in (FORWARD_MODE, BASELINE_MODE) else info["round_number"]
            saved = {
                "mode": selected, "round_number": round_number,
                "run_id": uuid.uuid4().hex, "git_sha": self._git_sha(),
            }
            # This explicit new run is authorized only after checking there is
            # no currently running or manually paused earlier run.
            training_pause.reset_pause(self.root, run_id=saved["run_id"])
            return self._launch_existing(saved, resume=False)

    def pause(self) -> dict[str, Any]:
        with self._lock:
            pid=self._tracked_pid()
            if pid is None or self._suspended:
                raise CycleError("no active training process to pause")
            run=_run_state(self.root) or {}
            if run.get("stage") not in ACTIVE_WORKER_STAGES:
                raise CycleError("training is not in a pausable stage")
            if run.get("worker_pid") != pid:
                raise CycleError("training worker PID does not match active run")
            if not isinstance(run.get("run_id"),str):
                raise CycleError("training session predates pause support; update and restart")
            immediate=_suspend_training_fit(self.root,run,manual=True)
            if immediate:
                self._suspended=True
                return {
                    "pause_requested":True, "paused":True, "immediate":True,
                    "message":"Training paused instantly; model fit remains in memory "
                              "and can resume after reopening KalshiEdge.",
                }
            return {
                "pause_requested":True, "paused":False, "immediate":False,
                "message":"Pause requested; worker will stop at the next safe checkpoint.",
            }

    def resume(self) -> dict[str, Any]:
        with self._lock:
            saved=_run_state(self.root) or {}
            if saved.get("stage") != "PAUSED":
                raise CycleError("training is not paused")
            current_git_sha=self._git_sha()
            run_id=saved.get("run_id")
            if not isinstance(run_id,str):
                raise CycleError("missing verified training resume identity")
            if saved.get("suspended_in_memory") is True:
                pid,token=saved.get("worker_pid"),saved.get("worker_identity")
                if (type(pid) is int and isinstance(token,str)
                        and training_process_control.is_alive(pid,token)):
                    if not self._compatible_frozen_source(saved.get("git_sha"), current_git_sha):
                        raise CycleError(
                            "Git version changed since training paused; frozen worker code "
                            "is incompatible with this update. Restore the original "
                            "training version before resuming."
                        )
                    if not _thaw_training_fit(self.root,saved):
                        raise CycleError("suspended model worker identity changed")
                    self._adopted_pid=pid if self._process is None else self._adopted_pid
                    self._adopted_identity=token if self._process is None else self._adopted_identity
                    self._suspended=False
                    return {
                        "resumed":True, "in_memory":True,"worker_pid":pid,
                        "message":"Resumed the exact suspended model fit in memory.",
                    }
                if self._process is not None and self._process.poll() is None:
                    raise CycleError("worker identity changed; refusing to start a duplicate")
                self._adopted_pid=None
                self._adopted_identity=None
                self._suspended=False
                _write_worker_state(
                    self.root,stage="PAUSED",mode=saved["mode"],
                    round_number=saved["round_number"],
                    suspended_in_memory=False,
                    error="Suspended process no longer exists; resuming last durable checkpoint.",
                )
            if self._tracked_pid() is not None:
                raise CycleError("training is still active; wait for PAUSED acknowledgment")
            original=saved
            saved=self._prepare_checkpoint_resume(saved,current_git_sha)
            result=self._launch_resumed_checkpoint(original,saved,manual=True)
            return {
                **(result or {}),"resumed":True,"in_memory":False,
                "restarted_fit_after_update": (
                    original.get("run_id") != saved.get("run_id")
                ),
            }

    def recover(self) -> bool:
        """Auto-resume on reopen; honor manual pause across application close."""
        with self._lock:
            if self._tracked_pid() is not None:
                return False
            saved=_run_state(self.root)
            if not saved or saved.get("stage") not in ACTIVE_WORKER_STAGES | {"PAUSED"}:
                return False
            run_id=saved.get("run_id")
            intent=training_pause.read_pause(self.root)
            if intent and intent.get("run_id") != run_id:
                raise CycleError("saved pause request belongs to another training run")

            if saved.get("suspended_in_memory") is True:
                pid,token=saved.get("worker_pid"),saved.get("worker_identity")
                if (type(pid) is int and isinstance(token,str)
                        and training_process_control.is_alive(pid,token)):
                    self._adopted_pid=pid
                    self._adopted_identity=token
                    self._suspended=True
                    if intent.get("pause_requested") and intent.get("manual"):
                        return False
                    if not self._compatible_frozen_source(saved.get("git_sha"), self._git_sha()):
                        _write_worker_state(
                            self.root,stage="PAUSED",mode=saved["mode"],
                            round_number=saved["round_number"],
                            error="Training worker source changed; suspended fit remains "
                                  "paused until the original code version is restored.",
                        )
                        return False
                    if _thaw_training_fit(self.root,saved):
                        self._suspended=False
                        return True
                    return False
                # If Windows closed the worker too, never touch a recycled PID.
                # Existing disk-backed dataset/experiment checkpoints remain
                # eligible for recovery, but a partial native fit must replay.
                _write_worker_state(
                    self.root,stage=saved["stage"],mode=saved["mode"],
                    round_number=saved["round_number"],
                    suspended_in_memory=False,worker_pid=None,
                )
                saved=_run_state(self.root) or saved

            if intent.get("pause_requested") and intent.get("manual"):
                if saved.get("stage") != "PAUSED":
                    _save_paused_state(
                        self.root,mode=saved["mode"],
                        round_number=saved["round_number"],reason="manual",
                    )
                return False
            try:
                saved=self._prepare_checkpoint_resume(saved,self._git_sha())
            except CycleError as exc:
                _save_paused_state(
                    self.root,mode=saved["mode"],round_number=saved["round_number"],
                    reason="update_blocked",
                )
                _write_worker_state(
                    self.root,stage="PAUSED",mode=saved["mode"],
                    round_number=saved["round_number"], error=str(exc),
                )
                return False
            if not isinstance(run_id,str):
                raise CycleError("old training state has no run ID; cannot safely auto-resume")
            original=_run_state(self.root) or saved
            if saved.get("forked_from_run_id"):
                archive=self.root / "training_runs" / "recovery" / (
                    str(saved["forked_from_run_id"]) + ".json"
                )
                original=json.loads(archive.read_text(encoding="utf-8"))
            self._launch_resumed_checkpoint(original,saved,manual=False)
            return True

    def stop(self) -> None:
        with self._lock:
            process=self._process
            pid=self._tracked_pid()
            saved=_run_state(self.root) or {}
            if (pid is not None and saved.get("stage")=="PAUSED"
                    and saved.get("suspended_in_memory") is True
                    and saved.get("worker_pid")==pid
                    and training_process_control.is_alive(
                        pid,saved.get("worker_identity") or "")):
                # Launcher may have frozen the worker BEFORE the dashboard
                # shutdown hook runs. Don't suspend twice or terminate it.
                self._suspended=True
            if pid is not None and not self._suspended:
                run=saved
                run_id=run.get("run_id")
                intent=training_pause.read_pause(self.root)
                manual=bool(
                    isinstance(run_id,str) and
                    intent.get("run_id")==run_id and
                    intent.get("pause_requested") and intent.get("manual")
                )
                try:
                    if _suspend_training_fit(self.root,run,manual=manual):
                        self._suspended=True
                except Exception:
                    LOG.exception("Could not freeze training worker on application close")
            if self._suspended:
                # Leave verified frozen PID alive. A newly opened dashboard
                # adopts it and can continue the identical in-memory fit.
                self._process=None
                self._adopted_pid=None
                self._adopted_identity=None
                self._suspended=False
                if self._log_handle is not None:
                    self._log_handle.close()
                    self._log_handle=None
                return
            if process is not None and process.poll() is None:
                run=_run_state(self.root) or {}
                run_id=run.get("run_id")
                if isinstance(run_id,str):
                    intent=training_pause.read_pause(self.root)
                    if not (intent.get("run_id")==run_id and
                            intent.get("pause_requested") and intent.get("manual")):
                        training_pause.set_pause(self.root,run_id=run_id,manual=False)
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
            self._process=None
            self._adopted_pid=None
            self._adopted_identity=None
            if self._log_handle is not None:
                self._log_handle.close()
                self._log_handle=None


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
