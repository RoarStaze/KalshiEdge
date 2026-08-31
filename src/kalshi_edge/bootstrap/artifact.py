from __future__ import annotations

"""Immutable, hash-addressed bootstrap model bundles."""

import base64
import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


BUNDLE_SCHEMA_VERSION = 1


class ArtifactIntegrityError(RuntimeError):
    """Raised when a model bundle is malformed or fails integrity verification."""


class SplitBoundaries(BaseModel):
    model_config = ConfigDict(frozen=True)

    train_start_ts_ns: int = Field(gt=0)
    train_end_ts_ns: int = Field(gt=0)
    calibration_start_ts_ns: int = Field(gt=0)
    calibration_end_ts_ns: int = Field(gt=0)
    lockbox_start_ts_ns: int = Field(gt=0)
    lockbox_end_ts_ns: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_order(self) -> "SplitBoundaries":
        if self.train_start_ts_ns > self.train_end_ts_ns:
            raise ValueError("training boundary is reversed")
        if self.train_end_ts_ns >= self.calibration_start_ts_ns:
            raise ValueError("calibration must start after training")
        if self.calibration_start_ts_ns > self.calibration_end_ts_ns:
            raise ValueError("calibration boundary is reversed")
        if self.calibration_end_ts_ns >= self.lockbox_start_ts_ns:
            raise ValueError("lockbox must start after calibration")
        if self.lockbox_start_ts_ns > self.lockbox_end_ts_ns:
            raise ValueError("lockbox boundary is reversed")
        return self


class MetricSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    brier: float = Field(ge=0.0, le=1.0)
    log_loss: float = Field(ge=0.0)
    accuracy: float = Field(ge=0.0, le=1.0)
    ece: float = Field(ge=0.0, le=1.0)
    sharpness: float = Field(ge=0.0)

    @field_validator("brier", "log_loss", "accuracy", "ece", "sharpness")
    @classmethod
    def finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("metric must be finite")
        return value


class LeakageEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    passed: bool
    finding_count: int = Field(ge=0)
    finding_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_consistency(self) -> "LeakageEvidence":
        if self.passed and (self.finding_count != 0 or self.finding_codes):
            raise ValueError("passed leakage audit cannot contain findings")
        if not self.passed and self.finding_count == 0:
            raise ValueError("failed leakage audit must contain at least one finding")
        return self


class ComponentIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    kind: Literal["benchmark", "structural", "model", "residual", "stacker", "calibrator"]
    identity: str
    weight: float = Field(ge=0.0, le=1.0)


class AblationEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    full_metrics: MetricSnapshot
    ablated_metrics: MetricSnapshot
    retained: bool
    reason: str


class ModelBundle(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = BUNDLE_SCHEMA_VERSION
    stage: Literal["experiment", "promoted"]
    model_version: str
    git_sha: str
    random_seed: int
    feature_schema_version: int = Field(gt=0)
    feature_names: tuple[str, ...]
    boundaries: SplitBoundaries
    input_hashes: dict[str, str]
    training_config_hash: str
    library_versions: dict[str, str]
    components: tuple[ComponentIdentity, ...]
    calibration_method: Literal["platt", "isotonic", "identity"]
    leakage_audit: LeakageEvidence
    metrics: dict[str, MetricSnapshot]
    ablations: tuple[AblationEvidence, ...] = ()
    excluded_feature_families: tuple[str, ...] = ()
    excluded_components: tuple[str, ...] = ()
    serialized_pipeline_b64: str
    serialized_pipeline_sha256: str
    source_experiment_sha256: str | None = None
    promotion_rule: str | None = None
    promotion_reason_codes: tuple[str, ...] = ()
    bundle_sha256: str | None = None

    @field_validator("git_sha")
    @classmethod
    def valid_git_sha(cls, value: str) -> str:
        if len(value) != 40 or any(character not in "0123456789abcdefABCDEF" for character in value):
            raise ValueError("git_sha must be a 40-character hexadecimal commit SHA")
        return value.lower()

    @field_validator("training_config_hash", "serialized_pipeline_sha256")
    @classmethod
    def valid_sha256(cls, value: str) -> str:
        return _validated_sha256(value)

    @field_validator("input_hashes")
    @classmethod
    def valid_input_hashes(cls, value: dict[str, str]) -> dict[str, str]:
        if not value:
            raise ValueError("model bundle requires at least one input hash")
        return {str(path): _validated_sha256(digest) for path, digest in value.items()}

    @field_validator("feature_names")
    @classmethod
    def valid_feature_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or len(set(value)) != len(value):
            raise ValueError("feature names must be non-empty and unique")
        return value

    @field_validator("source_experiment_sha256", "bundle_sha256")
    @classmethod
    def valid_optional_sha(cls, value: str | None) -> str | None:
        return None if value is None else _validated_sha256(value)


def _validated_sha256(value: str) -> str:
    text = str(value).lower()
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError("value must be a 64-character SHA-256 hex digest")
    return text


def _canonical_bundle_bytes(bundle: ModelBundle) -> bytes:
    payload = bundle.model_dump(mode="json", exclude={"bundle_sha256"})
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def bundle_sha256(bundle: ModelBundle) -> str:
    return hashlib.sha256(_canonical_bundle_bytes(bundle)).hexdigest()


def _decode_pipeline(bundle: ModelBundle) -> bytes:
    try:
        payload = base64.b64decode(bundle.serialized_pipeline_b64.encode("ascii"), validate=True)
    except (ValueError, UnicodeError) as exc:
        raise ArtifactIntegrityError("pipeline payload is not valid base64") from exc
    actual = hashlib.sha256(payload).hexdigest()
    if actual != bundle.serialized_pipeline_sha256:
        raise ArtifactIntegrityError("pipeline payload SHA-256 does not match bundle metadata")
    return payload


def serialized_pipeline_bytes(bundle: ModelBundle) -> bytes:
    """Return verified serialized pipeline bytes for trusted, hash-verified bundles."""
    if bundle.bundle_sha256 is None or bundle_sha256(bundle) != bundle.bundle_sha256:
        raise ArtifactIntegrityError("bundle SHA-256 must verify before pipeline payload is exposed")
    return _decode_pipeline(bundle)


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


def save_model_bundle(bundle: ModelBundle, root: Path) -> Path:
    """Seal and atomically save one immutable bundle under its content hash."""
    _decode_pipeline(bundle)
    digest = bundle_sha256(bundle)
    if bundle.bundle_sha256 is not None and bundle.bundle_sha256 != digest:
        raise ArtifactIntegrityError("bundle SHA-256 does not match bundle contents")
    sealed = bundle.model_copy(update={"bundle_sha256": digest})
    encoded = json.dumps(
        sealed.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8") + b"\n"
    root = root.resolve()
    target = root / f"{digest}.json"
    if target.exists():
        try:
            existing = load_model_bundle(target)
        except ArtifactIntegrityError as exc:
            raise ArtifactIntegrityError(f"existing bundle at {target} is corrupt") from exc
        if existing.bundle_sha256 != digest:
            raise ArtifactIntegrityError("existing hash-addressed bundle does not match requested digest")
        return target
    _atomic_write(target, encoded)
    return target


def load_model_bundle(path: Path) -> ModelBundle:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        bundle = ModelBundle.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ArtifactIntegrityError(f"model bundle is unreadable or invalid: {path}") from exc
    if bundle.bundle_sha256 is None:
        raise ArtifactIntegrityError("model bundle is missing bundle SHA-256")
    actual = bundle_sha256(bundle)
    if actual != bundle.bundle_sha256:
        raise ArtifactIntegrityError("bundle SHA-256 does not match bundle contents")
    if path.stem != bundle.bundle_sha256:
        raise ArtifactIntegrityError("hash-addressed bundle filename does not match bundle SHA-256")
    _decode_pipeline(bundle)
    return bundle


def metric_snapshot(metrics: object) -> MetricSnapshot:
    """Convert a Task 7 ProbabilityMetrics-like object to portable artifact metrics."""
    return MetricSnapshot(
        brier=float(getattr(metrics, "brier")),
        log_loss=float(getattr(metrics, "log_loss")),
        accuracy=float(getattr(metrics, "accuracy")),
        ece=float(getattr(metrics, "ece")),
        sharpness=float(getattr(metrics, "sharpness")),
    )


def component_weights(bundle: ModelBundle) -> Mapping[str, float]:
    return {component.name: component.weight for component in bundle.components}
