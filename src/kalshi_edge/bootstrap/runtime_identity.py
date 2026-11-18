"""Frozen-on-start build identity for the unified local dashboard.

A newly started launcher must not silently reuse a previously started
server that still contains the old paper scalp UI or 480-second gate.

This is diagnostic source-version identity, NOT a security attestation.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Iterable


# These modules contain the loaded unified server, browser UI and paper
# trading entry/exit paths. The source digest works in editable installs and
# installed packages, including local changes without a new git commit.
_DASHBOARD_FILES = (
    "bootstrap/final_history.py",
    "bootstrap/site_pages.py",
    "bootstrap/strat_ui.py",
    "trading/strat_policy.py",
    "trading/strat_paper.py",
    "bootstrap/scalp_paper_ui.py",
    "trading/paper_scalper.py",
    "trading/scalp_policy.py",
)


def source_build_id(paths: Iterable[Path]) -> str:
    """Fingerprint actual source bytes, including path identity and order."""
    h = sha256()
    for path in paths:
        path = Path(path)
        h.update(path.name.encode("utf-8"))
        h.update(b"\x00")
        h.update(path.read_bytes())
        h.update(b"\x00")
    return h.hexdigest()[:16]


def dashboard_build_id() -> str:
    """Compute the local dashboard source build at process startup."""
    package_root = Path(__file__).resolve().parent.parent
    return source_build_id(package_root / file for file in _DASHBOARD_FILES)
