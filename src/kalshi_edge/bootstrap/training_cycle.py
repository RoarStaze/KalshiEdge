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
            result["reason"] = (
                f"{len(cases)}/{TARGET_CASE_MARKETS} eligible {mode} markets with rebuilt causal features."
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


def _write_worker_state(root: Path, *, stage: str, mode: str, round_number: int, **values) -> None:
    prior = _run_state(root)
    retained = {}
    if prior is not None and prior.get("round_number") == round_number:
        retained = {
            key: prior[key] for key in ("log_path", "worker_pid")
            if key in prior
        }
    _atomic_state(_state_path(root), {
        **retained,
        "stage": stage,
        "mode": mode,
        "round_number": round_number,
        "updated_utc": datetime.now(timezone.utc).isoformat(),
        **values,
    })


def run_training_worker(root: Path, *, mode: str, round_number: int, git_sha: str) -> int:
    """Long-running, explicitly user-triggered train + one-time evaluation."""

    root = root.resolve()
    from ..launcher import SingleStarter
    from .config import BootstrapSettings
    from .evaluate import train_experiment, run_lockbox_evaluation

    with SingleStarter(root / "training_runs" / "worker.lock"):
        try:
            eligibility = inspect_training_readiness(root)
            if not eligibility["ready"] or eligibility["mode"] != mode or eligibility["round_number"] != round_number:
                raise CycleError("training eligibility/cycle changed since Train Now; refusing to run")
            _write_worker_state(root, stage="TRAINING", mode=mode, round_number=round_number)
            settings = BootstrapSettings(bootstrap_dir=root)
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
            _write_worker_state(
                root, stage="COMPLETE", mode=mode, round_number=round_number,
                experiment_sha256=experiment.experiment_bundle_sha256,
                promoted=evaluation.decision.promoted,
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
            info["run"] = _run_state(self.root)
            if self._process is not None:
                code = self._process.poll()
                info["running"] = code is None
                if code is not None:
                    info["worker_exit_code"] = code
                    if info["run"] is not None and info["run"].get("stage") in {"STARTING", "TRAINING", "EVALUATING"}:
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
            else:
                info["running"] = False
                if info["run"] is not None and info["run"].get("stage") in {"STARTING", "TRAINING", "EVALUATING"}:
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
            if not info["ready"]:
                raise CycleError(info["reason"])

            git = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=self.project_root,
                check=True, capture_output=True, text=True, timeout=10,
            ).stdout.strip()
            if len(git) != 40 or any(c not in "0123456789abcdef" for c in git.lower()):
                raise CycleError("cannot verify current repository Git SHA")
            logfile = self.root / "training_runs" / (
                f"round-{info['round_number']:04d}-{int(time.time())}.log"
            )
            logfile.parent.mkdir(parents=True, exist_ok=True)
            self._log_handle = logfile.open("ab")
            _write_worker_state(
                self.root, stage="STARTING",
                mode=info["mode"], round_number=info["round_number"],
                log_path=str(logfile),
            )
            try:
                # Yield CPU priority to the latency-critical live forecasting
                # process when long experiment fitting is active on Windows.
                priority = subprocess.BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 0
                self._process = subprocess.Popen(
                    [
                        sys.executable, "-m", "kalshi_edge.bootstrap.training_cycle",
                        "--worker", "--root", str(self.root), "--mode", info["mode"],
                        "--round-number", str(info["round_number"]),
                        "--git-sha", git,
                    ],
                    cwd=self.project_root,
                    stdin=subprocess.DEVNULL,
                    stdout=self._log_handle,
                    stderr=subprocess.STDOUT,
                    creationflags=priority,
                )
                _write_worker_state(
                    self.root, stage="STARTING", mode=info["mode"],
                    round_number=info["round_number"],
                    worker_pid=self._process.pid,
                    log_path=str(logfile),
                )
            except (OSError, subprocess.SubprocessError):
                self._log_handle.close()
                self._log_handle = None
                raise
            return {
                "started": True,
                "round_number": info["round_number"],
                "mode": info["mode"],
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
    parser.add_argument("--mode", choices=("mistake", "correct"), required=True)
    parser.add_argument("--round-number", type=int, required=True)
    parser.add_argument("--git-sha", required=True)
    args = parser.parse_args()
    return run_training_worker(
        args.root, mode=args.mode,
        round_number=args.round_number, git_sha=args.git_sha,
    )


if __name__ == "__main__":
    raise SystemExit(main())
