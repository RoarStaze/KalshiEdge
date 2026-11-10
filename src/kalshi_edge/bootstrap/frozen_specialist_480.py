"""Immutable numeric-only 480s specialist research freeze; never a live trade model.

The offline CLI trains using provenance-verified development rows only.
The frozen JSON is self-hashed and exclusively created; later scoring rejects
any observation at/before freeze time and never updates model weights.
There is no model promotion, AutoTrade connection or API order capability.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import time
from typing import Any, Mapping, Sequence

from .specialist_480 import (
    CHECKPOINT_SECONDS, CHECKPOINT_TOLERANCE_SECONDS, FEATURES,
    MIN_TRAIN_MARKETS, _eligible_features, select_eight_minute_rows,
)
from .types import FeatureRow

MODEL_NAME = "KXBTC15M_FROZEN_480S_LOGISTIC_V1"
FILENAME = Path("research/480_specialist/frozen.json")
_FIELDS = {
    "model", "feature_names", "dataset_sha256", "fit_markets",
    "trained_through_ns", "frozen_at_ns", "mean", "scale",
    "coefficients", "intercept", "sha256",
}


def _canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _validate_frozen(model: Mapping[str, Any]) -> None:
    if not isinstance(model, Mapping) or set(model) != _FIELDS:
        raise ValueError("invalid frozen 480s model fields")
    digest = model["dataset_sha256"]
    if (model["model"] != MODEL_NAME or model["feature_names"] != list(FEATURES)
            or type(digest) is not str or len(digest) != 64
            or any(ch not in "0123456789abcdef" for ch in digest)):
        raise ValueError("invalid frozen model provenance")
    trained = model["trained_through_ns"]
    frozen = model["frozen_at_ns"]
    if (type(model["fit_markets"]) is not int or model["fit_markets"] < MIN_TRAIN_MARKETS
            or type(trained) is not int or type(frozen) is not int
            or not 0 < trained < frozen):
        raise ValueError("invalid frozen model chronological boundary")

    size = len(FEATURES)
    for field in ("mean", "scale", "coefficients"):
        vector = model[field]
        if (not isinstance(vector, list) or len(vector) != size
                or any(type(v) not in (int, float) or not math.isfinite(v)
                       for v in vector)):
            raise ValueError("invalid frozen model numeric coefficients")
    if (any(v <= 0 for v in model["scale"])
            or type(model["intercept"]) not in (int, float)
            or not math.isfinite(model["intercept"])):
        raise ValueError("invalid frozen model scaling/intercept")
    checksum = model["sha256"]
    if type(checksum) is not str or checksum != _hash({
            key: value for key, value in model.items() if key != "sha256"}):
        raise ValueError("frozen model hash integrity check failed")


def fit_frozen_specialist(
    development_rows: Sequence[FeatureRow],
    *, dataset_sha256: str, frozen_at_ns: int,
) -> dict[str, Any]:
    """Fit EXACT fixed specialist on verified development rows (caller verifies dataset).

    The function cannot access a calibration or lockbox cohort; its
    caller is responsible for supplying only a verified development split.
    """
    eligible, _rejected = select_eight_minute_rows(development_rows)
    if len(eligible) < MIN_TRAIN_MARKETS:
        raise ValueError("insufficient eligible historical 480s development markets")
    labels = [row.label_yes for row in eligible]
    if set(labels) != {0, 1}:
        raise ValueError("both settled training outcome classes required")
    if (type(frozen_at_ns) is not int
            or frozen_at_ns <= eligible[-1].checkpoint_ts_ns):
        raise ValueError("freeze time must strictly follow all fitted markets")

    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    fitted = make_pipeline(
        StandardScaler(),
        LogisticRegression(C=.15, solver="lbfgs", max_iter=1000, random_state=0),
    )
    fitted.fit([[r.features[key] for key in FEATURES] for r in eligible], labels)
    scaler = fitted.named_steps["standardscaler"]
    logistic = fitted.named_steps["logisticregression"]
    raw = {
        "model": MODEL_NAME,
        "feature_names": list(FEATURES),
        "dataset_sha256": dataset_sha256,
        "fit_markets": len(eligible),
        "trained_through_ns": eligible[-1].checkpoint_ts_ns,
        "frozen_at_ns": frozen_at_ns,
        "mean": [float(x) for x in scaler.mean_],
        "scale": [float(x) for x in scaler.scale_],
        "coefficients": [float(x) for x in logistic.coef_[0]],
        "intercept": float(logistic.intercept_[0]),
    }
    result = {**raw, "sha256": _hash(raw)}
    _validate_frozen(result)
    return result


def score_frozen_specialist(model: Mapping[str, Any], row: FeatureRow) -> float:
    """Score one *future* true-480s tick using frozen weights; no refitting."""
    _validate_frozen(model)
    if not isinstance(row, FeatureRow):
        raise ValueError("shadow requires a causal FeatureRow")
    remaining = row.features.get("seconds_remaining")
    if (type(remaining) not in (int, float) or not math.isfinite(remaining)
            or not (CHECKPOINT_SECONDS - CHECKPOINT_TOLERANCE_SECONDS <= remaining <= CHECKPOINT_SECONDS)):
        raise ValueError("shadow requires the original eight-minute checkpoint")
    if row.checkpoint_ts_ns <= model["frozen_at_ns"]:
        raise ValueError("shadow observation must be later than model freeze")
    problem = _eligible_features(row)
    if problem is not None:
        raise ValueError("shadow features are not causal/eligible: " + problem)
    logit = float(model["intercept"])
    for name, mean, scale, weight in zip(
        FEATURES, model["mean"], model["scale"], model["coefficients"],
    ):
        logit += float(weight) * ((row.features[name] - float(mean)) / float(scale))
    if logit >= 0:
        return 1.0 / (1.0 + math.exp(-logit))
    exponential = math.exp(logit)
    return exponential / (1.0 + exponential)


def save_frozen_specialist(path: Path, model: Mapping[str, Any]) -> Path:
    """Exclusive immutable research write; an existing model is never replaced."""
    _validate_frozen(model)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = _canonical(model) + b"\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    fd = os.open(target, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        # Do not leave partially serialized artifacts that could later load.
        target.unlink(missing_ok=True)
        raise
    return target


def load_frozen_specialist(path: Path) -> dict[str, Any]:
    target = Path(path)
    with target.open("rb") as handle:
        blob = handle.read(32_001)
    if len(blob) > 32_000:
        raise ValueError("frozen model is unexpectedly large")
    try:
        raw = json.loads(blob)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("invalid frozen model JSON") from exc
    _validate_frozen(raw)
    return raw


def main(argv: list[str] | None = None) -> int:
    from . import training
    from .config import BootstrapSettings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=BootstrapSettings().bootstrap_dir)
    args = parser.parse_args(argv)
    path = args.root.resolve() / FILENAME
    try:
        if path.exists():
            model = load_frozen_specialist(path)
            status = "EXISTING_FROZEN_MODEL"
        else:
            context = training._verified_context(args.root.resolve())
            prior_rows = training._read_rows_before_lockbox(context)
            groups = set(context.plan.development_groups)
            development = [r for r in prior_rows if training._group(r) in groups]
            model = fit_frozen_specialist(
                development,
                dataset_sha256=context.input_hashes["derived/features.parquet"],
                frozen_at_ns=time.time_ns(),
            )
            save_frozen_specialist(path, model)
            status = "NEW_FROZEN_MODEL"
    except (OSError, ValueError, training.TrainingError) as exc:
        print(json.dumps({
            "status": "FREEZE_REFUSED", "error": str(exc),
            "live_model_changed": False, "trades": False,
        }, indent=2))
        return 2
    print(json.dumps({
        "status": status,
        "path": str(path),
        "model_sha256": model["sha256"],
        "verified_dataset_sha256": model["dataset_sha256"],
        "fit_markets": model["fit_markets"],
        "trained_through_ns": model["trained_through_ns"],
        "frozen_at_ns": model["frozen_at_ns"],
        "future_only": True,
        "live_model_changed": False,
        "trades": False,
        "shadow_live_connected": False,
        "note": "Research weights are frozen; prospective live wiring is a separate milestone.",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
