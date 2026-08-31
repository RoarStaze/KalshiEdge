from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict


MANIFEST_SCHEMA_VERSION = 1


class RawArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: Path
    manifest_path: Path
    sha256: str
    source: str
    retrieval_ts_utc: str
    byte_count: int
    metadata: dict[str, Any]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reject_phase1_root(root: Path) -> None:
    resolved = root.resolve()
    if resolved.name.lower() == "raw" and resolved.parent.name.lower() == "data":
        raise ValueError("bootstrap storage cannot use the Phase 1 data/raw root")


def _safe_component(value: str, *, label: str) -> str:
    candidate = Path(value)
    if not value or candidate.is_absolute() or len(candidate.parts) != 1 or value in {".", ".."}:
        raise ValueError(f"{label} would escape bootstrap storage")
    return value


def _safe_relative_path(value: str) -> Path:
    candidate = Path(value)
    if not value or candidate.is_absolute() or any(part in {".", ".."} for part in candidate.parts):
        raise ValueError("logical name would escape bootstrap storage")
    return candidate


def _assert_within(path: Path, parent: Path) -> None:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError as exc:
        raise ValueError("target would escape bootstrap storage") from exc


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def write_manifest(root: Path, artifact: RawArtifact) -> Path:
    root = root.resolve()
    manifest_path = root / artifact.manifest_path
    _assert_within(manifest_path, root / "manifests")
    payload = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "source": artifact.source,
        "path": artifact.path.as_posix(),
        "sha256": artifact.sha256,
        "byte_count": artifact.byte_count,
        "retrieval_ts_utc": artifact.retrieval_ts_utc,
        "metadata": artifact.metadata,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"
    _atomic_write(manifest_path, encoded)
    return manifest_path


def write_raw_artifact(
    *,
    root: Path,
    source: str,
    logical_name: str,
    content: bytes,
    metadata: dict[str, Any],
) -> RawArtifact:
    _reject_phase1_root(root)
    root = root.resolve()
    source_name = _safe_component(source, label="source")
    logical_path = _safe_relative_path(logical_name)

    raw_parent = root / "raw" / source_name
    raw_path = raw_parent / logical_path
    _assert_within(raw_path, raw_parent)

    manifest_relative = Path("manifests") / source_name / Path(f"{logical_path.as_posix()}.manifest.json")
    manifest_path = root / manifest_relative
    _assert_within(manifest_path, root / "manifests" / source_name)

    _atomic_write(raw_path, content)
    digest = sha256_file(raw_path)
    artifact = RawArtifact(
        path=raw_path.relative_to(root),
        manifest_path=manifest_relative,
        sha256=digest,
        source=source_name,
        retrieval_ts_utc=datetime.now(timezone.utc).isoformat(),
        byte_count=len(content),
        metadata=dict(metadata),
    )
    write_manifest(root, artifact)
    return artifact


def verify_artifact(artifact_path: Path, manifest_path: Path) -> bool:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected_hash = str(manifest["sha256"])
        expected_size = int(manifest["byte_count"])
        if artifact_path.stat().st_size != expected_size:
            return False
        return sha256_file(artifact_path) == expected_hash
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return False
