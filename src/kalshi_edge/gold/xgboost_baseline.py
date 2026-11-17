"""Nondeployable XGBoost research utilities for hypothetical KXGOLD15M rows.

This module deliberately offers NO production training path from the existing
gold inventories or dataset readiness audits. It exercises leakage-safe
chronological splitting and baseline mathematics on separately supplied rows.
It cannot independently prove that the caller's evidence is authentic.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from pathlib import Path
from typing import Sequence

from .audit import SERIES_TICKER
from .dataset_readiness import GoldDatasetError, inspect_gold_dataset_audit


class GoldBaselineError(ValueError):
    """No valid gold-only chronological experiment can be run."""


@dataclass(frozen=True)
class GoldModelRow:
    """Research row; receipt/label times supplied by a caller, not verified here."""
    ticker: str
    decision_ns: int
    feature_receipt_ns: int
    label_known_ns: int
    label: int
    # yes best bid, yes best ask, volume observed (no Pyth/settlement inputs)
    features: tuple[float, float, float]


def _validate(row: GoldModelRow) -> None:
    if not isinstance(row, GoldModelRow) or not isinstance(row.ticker, str):
        raise GoldBaselineError("invalid market row")
    if not row.ticker.startswith(SERIES_TICKER + "-"):
        raise GoldBaselineError("non-gold ticker in research rows")
    times = (row.decision_ns, row.feature_receipt_ns, row.label_known_ns)
    if any(not isinstance(t, int) or isinstance(t, bool) or t <= 0 for t in times):
        raise GoldBaselineError("invalid research event timestamp")
    if row.feature_receipt_ns > row.decision_ns:
        raise GoldBaselineError("feature arrived after its decision time")
    if row.label_known_ns <= row.decision_ns:
        raise GoldBaselineError("label is already known at decision time")
    if isinstance(row.label, bool) or row.label not in (0, 1):
        raise GoldBaselineError("nonbinary settlement label")
    if not isinstance(row.features, tuple) or len(row.features) != 3:
        raise GoldBaselineError("expected three fixed gold quote features")
    if any(not isinstance(x, (int, float)) or isinstance(x, bool)
           or not math.isfinite(x) for x in row.features):
        raise GoldBaselineError("nonfinite or malformed research feature")
    bid, ask, volume = row.features
    if not (0 <= bid <= ask <= 1) or volume < 0:
        raise GoldBaselineError("invalid market quote or volume features")


def _day(row: GoldModelRow) -> str:
    return datetime.fromtimestamp(row.decision_ns / 1_000_000_000, timezone.utc).date().isoformat()


def chronological_gold_split(
    rows: Sequence[GoldModelRow], *, min_train: int = 100, min_test: int = 30,
) -> tuple[list[GoldModelRow], list[GoldModelRow]]:
    """Hold out complete later UTC dates; purge labels not known before test starts."""
    if min_train < 1 or min_test < 1:
        raise GoldBaselineError("minimum train/test rows must be positive")
    if not rows:
        raise GoldBaselineError("no rows")
    for row in rows:
        _validate(row)
    seen: set[str] = set()
    for row in rows:
        if row.ticker in seen:
            raise GoldBaselineError("duplicate market ticker in research rows")
        seen.add(row.ticker)

    ordered = sorted(rows, key=lambda r: (r.decision_ns, r.ticker))
    day_list = sorted({_day(row) for row in ordered})
    candidates: list[tuple[float, list[GoldModelRow], list[GoldModelRow]]] = []
    for split_day in day_list[1:]:
        earlier = [r for r in ordered if _day(r) < split_day]
        later = [r for r in ordered if _day(r) >= split_day]
        if len(later) < min_test:
            continue
        first_test_time = min(r.decision_ns for r in later)
        # A settled label from training must have actually become available
        # before the earliest holdout forecast.
        earlier = [r for r in earlier if r.label_known_ns < first_test_time]
        if len(earlier) < min_train:
            continue
        ratio = len(earlier) / (len(earlier) + len(later))
        candidates.append((abs(ratio - .70), earlier, later))
    if not candidates:
        raise GoldBaselineError("insufficient separated chronological sessions")
    _, train, test = min(candidates, key=lambda x: x[0])
    return train, test


def fit_research_baseline(
    rows: Sequence[GoldModelRow], *, min_train: int = 100, min_test: int = 30,
) -> dict:
    """Fit XGBoost on supplied rows without publishing weights or live predictions.

    This tests the estimator pipeline only; supplied labels have NOT been
    authenticated against the Pyth oracle. The result is NONDEPLOYABLE.
    """
    train, holdout = chronological_gold_split(rows, min_train=min_train, min_test=min_test)
    if set(r.label for r in train) != {0, 1}:
        raise GoldBaselineError("training rows contain only one class")
    try:
        import numpy as np
        from xgboost import XGBClassifier
    except ImportError as exc:
        raise GoldBaselineError("install optional research dependencies to test XGBoost") from exc

    model = XGBClassifier(
        n_estimators=24, max_depth=2, learning_rate=0.05,
        subsample=1, colsample_bytree=1,
        objective="binary:logistic", eval_metric="logloss",
        tree_method="hist", random_state=37, n_jobs=1,
    )
    x_train = np.asarray([r.features for r in train], dtype=np.float32)
    y_train = np.asarray([r.label for r in train], dtype=np.int8)
    model.fit(x_train, y_train)
    x_test = np.asarray([r.features for r in holdout], dtype=np.float32)
    y_test = np.asarray([r.label for r in holdout], dtype=np.int8)
    prob = np.asarray(model.predict_proba(x_test)[:, 1], dtype=np.float64)
    if not np.all(np.isfinite(prob)) or np.any((prob < 0) | (prob > 1)):
        raise GoldBaselineError("model produced invalid probabilities")
    return {
        "experiment": "KXGOLD15M_XGBOOST_RESEARCH_BASELINE",
        "research_only": True, "deployable": False,
        "source": "SUPPLIED_ROWS_NOT_VERIFIED_ORACLE",
        "train_count": len(train), "test_count": len(holdout),
        "train_end_ns": max(r.decision_ns for r in train),
        "test_start_ns": min(r.decision_ns for r in holdout),
        "accuracy": float(np.mean((prob >= .5) == y_test)),
        "brier": float(np.mean((prob - y_test) ** 2)),
        "log_loss": float(np.mean(-(y_test * np.log(np.clip(prob, 1e-12, 1))
                   + (1 - y_test) * np.log(np.clip(1 - prob, 1e-12, 1))))),
        "note": "Synthetic or caller-supplied research data; not an empirical gold-market result",
    }


def train_from_gold_audit(audit_path: Path, output_root: Path) -> dict:
    """Explicitly refuse all current dataset audits; no production fit is possible."""
    try:
        audit = inspect_gold_dataset_audit(audit_path)
    except GoldDatasetError as exc:
        raise GoldBaselineError(f"invalid gold dataset audit: {exc}") from exc
    if audit["report"]["dataset_ready"] is not True:
        raise GoldBaselineError(
            "gold dataset is not training-ready: independent Pyth oracle, contract "
            "timing and point-in-time feature evidence remain unverified"
        )
    # This branch is intentionally unreachable for schema v1 audits.
    raise GoldBaselineError("no approved gold model dataset schema exists")
