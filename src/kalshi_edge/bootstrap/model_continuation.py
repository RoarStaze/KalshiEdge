"""Warm-start current fitted classifiers on NEW settled 480s evidence.

Readiness is read-only. No prior lockbox is reopened, model is overwritten,
or trade is placed. The evaluation/activation worker is a separate module.
"""
from __future__ import annotations

import copy
from dataclasses import replace
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from .final_forecast import FINAL_CHECKPOINT_SECONDS
from .types import FeatureRow

MODE = "continue_live"
MIN_NEW_MARKETS = 130
MIN_DEVELOPMENT = 40
CALIBRATION_MARKETS = 10
VALIDATION_MARKETS = 80
NEW_BOOSTING_ROUNDS = 12
LOGISTIC_ITERATIONS = 25


class ContinuationError(RuntimeError):
    """Continuing the currently active model cannot be verified."""


def continue_fitted_classifier(
    fitted: object, matrix: Sequence[Sequence[float]], labels: Sequence[int],
) -> object:
    """Reuse existing weights; never call the initial from-scratch fitter."""
    if (not matrix or len(matrix) != len(labels) or
            any(type(y) is not int or y not in (0, 1) for y in labels)
            or set(labels) != {0, 1}):
        raise ValueError("continued fitting requires both verified outcome classes")
    width = len(matrix[0])
    if (not width or any(len(row) != width for row in matrix)
            or any(type(v) not in (int, float) or not math.isfinite(v)
                   for row in matrix for v in row)):
        raise ValueError("continued fitting requires a finite unchanged feature schema")

    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline

    if isinstance(fitted, Pipeline):
        updated = copy.deepcopy(fitted)
        if (len(updated.steps) != 2
                or updated.steps[0][0] != "standardscaler"
                or updated.steps[1][0] != "logisticregression"
                or not isinstance(updated.steps[1][1], LogisticRegression)):
            raise ContinuationError("unsupported logistic pipeline cannot safely warm-start")
        scaler = updated.named_steps["standardscaler"]
        classifier = updated.named_steps["logisticregression"]
        classifier.set_params(warm_start=True, max_iter=LOGISTIC_ITERATIONS)
        classifier.fit(scaler.transform(matrix), labels)
        return updated

    if isinstance(fitted, HistGradientBoostingClassifier):
        updated = copy.deepcopy(fitted)
        previous_rounds = int(getattr(updated, "n_iter_", 0))
        if previous_rounds <= 0:
            raise ContinuationError("histogram booster lacks prior fitted trees")
        updated.set_params(
            warm_start=True, max_iter=previous_rounds + NEW_BOOSTING_ROUNDS,
        )
        updated.fit(matrix, labels)
        if updated.n_iter_ <= previous_rounds:
            raise ContinuationError("histogram booster did not add fitted iterations")
        return updated

    try:
        from xgboost import XGBClassifier
    except ImportError:
        XGBClassifier = None
    if XGBClassifier is not None and isinstance(fitted, XGBClassifier):
        original = copy.deepcopy(fitted)
        old_booster = original.get_booster()
        old_rounds = old_booster.num_boosted_rounds()
        params = original.get_params()
        params.update(n_estimators=NEW_BOOSTING_ROUNDS, device="cpu", n_jobs=1)
        continued = XGBClassifier(**params)
        continued.fit(matrix, labels, xgb_model=old_booster)
        if continued.get_booster().num_boosted_rounds() <= old_rounds:
            raise ContinuationError("XGBoost booster did not add fitted trees")
        return continued

    raise ContinuationError(
        "active classifier does not support safe previous-weight continuation"
    )


def fresh_active_pairs(
    rows: Sequence[FeatureRow], finals: Mapping[str, object],
    *, active_hash: str, after_ns: int,
    required_features: Sequence[str],
) -> list[tuple[FeatureRow, object]]:
    """Select only unique, recent, immutable, causally verified current-model markets."""
    if (len(active_hash) != 64 or
            any(c not in "0123456789abcdef" for c in active_hash.lower())):
        raise ValueError("invalid active model SHA-256")
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.market_ticker] = counts.get(row.market_ticker, 0) + 1
    selected: list[tuple[FeatureRow, object]] = []
    for row in rows:
        final = finals.get(row.market_ticker)
        remaining = row.features.get("seconds_remaining")
        if (
            counts[row.market_ticker] != 1 or final is None
            or getattr(final, "model_hash", None) != active_hash
            or getattr(final, "market_ticker", None) != row.market_ticker
            or getattr(final, "checkpoint_seconds", None) != FINAL_CHECKPOINT_SECONDS
            or getattr(final, "prediction_ts_ns", None) != row.checkpoint_ts_ns
            or type(getattr(final, "p_yes", None)) not in (float, int)
            or not math.isfinite(final.p_yes) or not 0 <= final.p_yes <= 1
            or row.checkpoint_ts_ns <= after_ns
            or type(remaining) not in (int, float)
            or not math.isfinite(remaining) or not 478 <= remaining <= 480
            or not all(name in row.features
                       and type(row.features[name]) in (int, float)
                       and math.isfinite(row.features[name])
                       for name in required_features)
            or any(type(ts) is not int or ts <= 0 or ts > row.checkpoint_ts_ns
                   for ts in row.source_max_ts_ns.values())
            or type(row.features.get("kalshi_mid")) not in (int, float)
            or not math.isfinite(row.features["kalshi_mid"])
            or not 0 <= row.features["kalshi_mid"] <= 1
            or type(row.label_yes) is not int or row.label_yes not in (0, 1)
        ):
            continue
        selected.append((row, final))
    return sorted(selected, key=lambda t: (t[0].checkpoint_ts_ns, t[0].market_ticker))


def source_pairs(
    root: Path, *, active_hash: str, required_features: Sequence[str],
) -> tuple[list[tuple[FeatureRow, object]], int, dict[str, Any]]:
    from . import forward_feature_evidence_480, training
    from .final_history import _forecasts

    root = Path(root).resolve()
    # The join verifies immutable causal features against official settled labels.
    rows, audit = forward_feature_evidence_480._joined(root)
    previously_evaluated_end = training._previous_lockbox_end_ts_ns(root) or 0
    finals = {f.market_ticker: f for f in _forecasts(root)
              if f.checkpoint_seconds == FINAL_CHECKPOINT_SECONDS}
    selected = fresh_active_pairs(
        rows, finals, active_hash=active_hash,
        after_ns=previously_evaluated_end, required_features=required_features,
    )
    return selected, previously_evaluated_end, audit


def inspect_continuation_readiness(root: Path) -> dict[str, Any]:
    from .live import load_default_model
    result: dict[str, Any] = {
        "mode": MODE, "ready": False, "status": "COLLECTING_NEW_SETTLEMENTS",
        "eligible_markets": 0, "required_markets": MIN_NEW_MARKETS,
        "reason": "No fresh verified settled 480s markets from the active model",
        "live_model_changed": False, "auto_trade_changed": False,
    }
    try:
        root = Path(root).resolve()
        bundle, pipeline = load_default_model(root)
        pairs, after, audit = source_pairs(
            root, active_hash=bundle.bundle_sha256,
            required_features=pipeline.required_feature_names,
        )
        result.update(
            active_model_sha256=bundle.bundle_sha256,
            eligible_markets=len(pairs),
            previously_evaluated_through_ns=after,
            verified_settled_forward_markets=audit.get("settled_480s", 0),
        )
        if len(pairs) < MIN_NEW_MARKETS:
            result["reason"] = (
                f"{len(pairs)}/{MIN_NEW_MARKETS} new official-settled, "
                "hash-verified 480s markets from the currently active model "
                "after the last used lockbox. Keep collecting fresh markets."
            )
            return result
        groups = (
            pairs[:-CALIBRATION_MARKETS-VALIDATION_MARKETS],
            pairs[-CALIBRATION_MARKETS-VALIDATION_MARKETS:-VALIDATION_MARKETS],
            pairs[-VALIDATION_MARKETS:],
        )
        if (len(groups[0]) < MIN_DEVELOPMENT
                or any({row.label_yes for row, _ in part} != {0, 1}
                       for part in groups)):
            result["reason"] = "New chronological training and validation need both outcome classes"
            return result
        result.update(
            ready=True, status="READY_TO_CONTINUE_ACTIVE_MODEL",
            reason=(
                f"{len(pairs)} new verified active-model markets: continue existing "
                "fitted logistic/boosting weights, reserve later markets for "
                "independent 63% confidence coverage-first comparison."
            ),
        )
    except Exception as exc:
        result["status"] = "EVIDENCE_UNAVAILABLE"
        result["reason"] = f"{type(exc).__name__}: {exc}"
    return result


def continue_pipeline(current: object, development: Sequence[FeatureRow]) -> object:
    """Preserve structural, residual, stacker and calibration exactly."""
    from .training import FittedBootstrapPipeline
    if not isinstance(current, FittedBootstrapPipeline):
        raise ContinuationError("current model must be a verified fitted full pipeline")
    names = current.outcome_feature_names
    matrix = [[float(row.features[name]) for name in names] for row in development]
    labels = [int(row.label_yes) for row in development]
    outcome = continue_fitted_classifier(current.outcome_model, matrix, labels)
    logistic = continue_fitted_classifier(current.logistic_model, matrix, labels)
    return replace(current, outcome_model=outcome, logistic_model=logistic)
