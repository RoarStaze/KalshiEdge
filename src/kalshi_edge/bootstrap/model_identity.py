"""Version numbers are attached to hash-sealed promoted model pointers.

A numbered training attempt is not a model. Only a model promoted into
models/default.json receives a new number. Legacy pointers start at Model #1.
No model/version state is modified by status GETs.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _hash(text: object) -> bool:
    return (isinstance(text, str) and len(text) == 64
            and all(char in "0123456789abcdef" for char in text.lower()))


def current_model_identity(root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "model_number": None,
        "promoted_hash": None,
        "previous_model_hash": None,
        "previous_model_number": None,
        "number_source": "UNAVAILABLE",
    }
    try:
        raw = json.loads((Path(root).resolve() / "models" / "default.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return result
    if not isinstance(raw, dict) or not _hash(raw.get("bundle_sha256")):
        return result
    number = raw.get("model_number", 1)
    if type(number) is not int or not 1 <= number <= 1_000_000:
        return result
    previous = raw.get("previous_model_hash")
    if previous is not None and not _hash(previous):
        return result
    if previous is not None and number <= 1:
        return result
    result.update(
        model_number=number,
        promoted_hash=raw["bundle_sha256"].lower(),
        previous_model_hash=previous.lower() if previous else None,
        previous_model_number=(number - 1 if previous else None),
        number_source=("pointer" if "model_number" in raw else "legacy_default"),
    )
    return result


def next_promoted_model_number(root: Path, new_bundle_hash: str) -> int:
    if not _hash(new_bundle_hash):
        raise ValueError("Cannot number unverified promoted model hash")
    root = Path(root).resolve()
    pointer_exists = (root / "models" / "default.json").is_file()
    previous = current_model_identity(root)
    if not pointer_exists:
        return 1
    if previous["model_number"] is None:
        raise ValueError("Existing model pointer is invalid; refusing promotion numbering")
    if previous["promoted_hash"] == new_bundle_hash.lower():
        return previous["model_number"]
    if previous["model_number"] >= 1_000_000:
        raise ValueError("Model number limit exceeded")
    return previous["model_number"] + 1
