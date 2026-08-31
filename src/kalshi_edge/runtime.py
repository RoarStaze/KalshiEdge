from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
import os
import platform
import subprocess
import sys
from typing import Any

from .config import CollectorSettings


def resolve_git_commit() -> str:
    explicit = os.getenv("KALSHI_BUILD_GIT_SHA") or os.getenv("GITHUB_SHA")
    if explicit:
        return explicit
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=1,
        ).strip()
    except (FileNotFoundError, subprocess.SubprocessError):
        return "unknown"


def build_runtime_snapshot(settings: CollectorSettings, *, git_commit: str | None = None) -> dict[str, Any]:
    try:
        project_version = version("kalshi-edge")
    except PackageNotFoundError:
        project_version = "unknown"
    return {
        "project_version": project_version,
        "git_commit": git_commit or resolve_git_commit(),
        "python_version": sys.version,
        "platform": platform.platform(),
        "config": {
            "env": settings.env,
            "data_dir": str(settings.data_dir.resolve()),
            "series_ticker": settings.series_ticker,
            "segment_max_events": settings.segment_max_events,
            "fsync_every": settings.fsync_every,
            "reconnect_initial_seconds": settings.reconnect_initial_seconds,
            "reconnect_max_seconds": settings.reconnect_max_seconds,
            "stale_after_seconds": settings.stale_after_seconds,
            "rest_base_url": settings.rest_base_url,
            "ws_url": settings.ws_url,
        },
    }
