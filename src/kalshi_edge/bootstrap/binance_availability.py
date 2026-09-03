from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from .provenance import sha256_file, verify_artifact, write_raw_artifact


AVAILABILITY_SCHEMA_VERSION = 1
PUBLICATION_POLICY = "daily archives become available the next UTC day"
PUBLICATION_POLICY_SOURCE = "https://github.com/binance/binance-public-data#readme"


class BinanceAvailabilitySnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    as_of_utc_date: date
    available_through_date: date
    path: Path
    sha256: str


def _snapshot_paths(root: Path, as_of_utc_date: date) -> tuple[Path, Path]:
    logical_name = f"daily/{as_of_utc_date.isoformat()}.json"
    data_path = root / "raw" / "binance_availability" / logical_name
    manifest_path = root / "manifests" / "binance_availability" / f"{logical_name}.manifest.json"
    return data_path, manifest_path


def _load_snapshot(root: Path, data_path: Path, manifest_path: Path) -> BinanceAvailabilitySnapshot:
    data_exists = data_path.exists()
    manifest_exists = manifest_path.exists()
    if data_exists != manifest_exists:
        raise RuntimeError(f"provenance is incomplete for existing Binance availability snapshot: {data_path.name}")
    if not data_exists:
        raise FileNotFoundError(data_path)
    if not verify_artifact(data_path, manifest_path):
        raise RuntimeError(f"provenance verification failed for existing Binance availability snapshot: {data_path.name}")
    try:
        payload = json.loads(data_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Binance availability snapshot is unreadable: {data_path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Binance availability snapshot is invalid: {data_path}")
    if int(payload.get("schema_version", -1)) != AVAILABILITY_SCHEMA_VERSION:
        raise RuntimeError(f"unsupported Binance availability snapshot schema: {data_path}")
    if payload.get("publication_policy") != PUBLICATION_POLICY:
        raise RuntimeError(f"unexpected Binance availability publication policy: {data_path}")
    try:
        as_of_utc_date = date.fromisoformat(str(payload["as_of_utc_date"]))
        available_through_date = date.fromisoformat(str(payload["available_through_date"]))
    except (KeyError, ValueError) as exc:
        raise RuntimeError(f"Binance availability snapshot has invalid dates: {data_path}") from exc
    if available_through_date != as_of_utc_date - timedelta(days=1):
        raise RuntimeError(f"Binance availability snapshot violates next-day publication rule: {data_path}")
    return BinanceAvailabilitySnapshot(
        as_of_utc_date=as_of_utc_date,
        available_through_date=available_through_date,
        path=data_path.relative_to(root),
        sha256=sha256_file(data_path),
    )


def record_daily_availability_snapshot(root: Path, as_of_utc_date: date) -> BinanceAvailabilitySnapshot:
    root = root.resolve()
    data_path, manifest_path = _snapshot_paths(root, as_of_utc_date)
    if data_path.exists() or manifest_path.exists():
        return _load_snapshot(root, data_path, manifest_path)

    available_through_date = as_of_utc_date - timedelta(days=1)
    payload = {
        "schema_version": AVAILABILITY_SCHEMA_VERSION,
        "as_of_utc_date": as_of_utc_date.isoformat(),
        "available_through_date": available_through_date.isoformat(),
        "publication_policy": PUBLICATION_POLICY,
        "publication_policy_source": PUBLICATION_POLICY_SOURCE,
    }
    content = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    artifact = write_raw_artifact(
        root=root,
        source="binance_availability",
        logical_name=f"daily/{as_of_utc_date.isoformat()}.json",
        content=content,
        metadata={
            "source_locator": PUBLICATION_POLICY_SOURCE,
            "publication_policy": PUBLICATION_POLICY,
        },
    )
    return BinanceAvailabilitySnapshot(
        as_of_utc_date=as_of_utc_date,
        available_through_date=available_through_date,
        path=artifact.path,
        sha256=artifact.sha256,
    )


def latest_daily_availability_snapshot(root: Path) -> BinanceAvailabilitySnapshot | None:
    root = root.resolve()
    daily_root = root / "raw" / "binance_availability" / "daily"
    manifest_root = root / "manifests" / "binance_availability" / "daily"
    if not daily_root.exists():
        if manifest_root.exists() and any(manifest_root.glob("*.manifest.json")):
            raise RuntimeError("Binance availability manifests exist without raw availability snapshots")
        return None

    data_paths = sorted(daily_root.glob("*.json"))
    manifest_paths = sorted(manifest_root.glob("*.json.manifest.json")) if manifest_root.exists() else []
    data_dates = {path.stem for path in data_paths}
    manifest_dates = {path.name.removesuffix(".json.manifest.json") for path in manifest_paths}
    if data_dates != manifest_dates:
        raise RuntimeError("Binance availability snapshot provenance is incomplete")
    if not data_paths:
        return None

    data_path = data_paths[-1]
    manifest_path = manifest_root / f"{data_path.name}.manifest.json"
    return _load_snapshot(root, data_path, manifest_path)
