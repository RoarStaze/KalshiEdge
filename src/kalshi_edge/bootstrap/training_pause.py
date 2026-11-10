"""Durable, run-scoped training pause intent.

This never serializes Python model memory. A worker only acknowledges a pause at
a safe market/stage boundary. App restarts replay unfinished atomic operations
from authenticated on-disk checkpoints.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import uuid
from typing import Any


class TrainingPaused(Exception):
    """A cooperative pause reached a safe checkpoint."""


class PauseControlError(ValueError):
    """Persisted pause intent is invalid; never guess its meaning."""


def _control_path(root: Path) -> Path:
    return root / "training_runs" / "control.json"


def _save(root: Path, data: dict[str, Any]) -> None:
    path = _control_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("x", encoding="utf-8", newline="\n") as file:
            json.dump(data, file, separators=(",", ":"), sort_keys=True)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def read_pause(root: Path) -> dict[str, Any]:
    path = _control_path(root)
    if not path.exists():
        return {}
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PauseControlError("training pause intent corrupted; manual review required") from exc
    if not isinstance(result, dict) or not isinstance(result.get("run_id"), str):
        raise PauseControlError("training pause intent has invalid run identity")
    if type(result.get("pause_requested")) is not bool or type(result.get("manual")) is not bool:
        raise PauseControlError("training pause intent has invalid flags")
    return result


def set_pause(root: Path, *, run_id: str, manual: bool) -> dict[str, Any]:
    if not isinstance(run_id, str) or not 1 <= len(run_id) <= 100:
        raise PauseControlError("invalid training run identity")
    result = {"run_id": run_id, "pause_requested": True, "manual": bool(manual)}
    _save(root, result)
    return result


def clear_pause(root: Path, *, run_id: str) -> dict[str, Any]:
    existing = read_pause(root)
    if existing and existing["run_id"] != run_id:
        raise PauseControlError("pause intent belongs to another training session")
    result = {"run_id": run_id, "pause_requested": False, "manual": False}
    _save(root, result)
    return result


def check_pause(root: Path, run_id: str) -> None:
    value = read_pause(root)
    if value.get("run_id") == run_id and value.get("pause_requested") is True:
        raise TrainingPaused("Training safely paused at a verified checkpoint")
