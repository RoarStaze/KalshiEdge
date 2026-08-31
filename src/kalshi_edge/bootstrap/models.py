from __future__ import annotations

"""Chronological ML candidates, residual correction, and simplex stacking."""

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .metrics import ProbabilityMetrics, probability_metrics
from .splits import WalkForwardSplit
from .types import FeatureRow


class ModelError(RuntimeError):
    """Raised when a bootstrap statistical model cannot be fit safely."""


CANDIDATE_NAMES = ("logistic", "hist_gradient_boosting", "xgboost")


@dataclass(frozen=True)
class CandidateResults:
    candidate_names: tuple[str, ...]
    best_candidate: str
    oof_predictions: dict[str, tuple[float | None, ...]]
    selected_params: dict[str, tuple[dict[str, Any], ...]]
    oof_metrics: dict[str, ProbabilityMetrics]
    feature_names: tuple[str, ...]
    final_models: dict[str, object]


class ResidualModel:
    def __init__(
        self,
        *,
        intercept: float,
        coefficients: Sequence[float],
        feature_means: Sequence[float],
        feature_scales: Sequence[float],
        component_weight: float,
    ) -> None:
        self.intercept = float(intercept)
        self.coefficients = tuple(float(value) for value in coefficients)
        self.feature_means = tuple(float(value) for value in feature_means)
        self.feature_scales = tuple(float(value) for value in feature_scales)
        self.component_weight = float(component_weight)

    def predict(self, feature_matrix: Sequence[Sequence[float]], prior_predictions: Sequence[float]) -> list[float]:
        x = _validate_matrix(feature_matrix)
        prior = _validate_probabilities(prior_predictions, expected=len(x))
        if len(x[0]) != len(self.coefficients):
            raise ModelError("residual feature width does not match fitted model")
        if self.component_weight <= 0.0:
            return list(prior)

        output: list[float] = []
        for row, probability in zip(x, prior):
            residual = self.intercept
            for value, coefficient, mean, scale in zip(
                row,
                self.coefficients,
                self.feature_means,
                self.feature_scales,
            ):
                residual += coefficient * ((value - mean) / scale)
            output.append(_sigmoid(_logit(probability) + residual))
        return output


class Stacker:
    def __init__(self, weights: Mapping[str, float]) -> None:
        self.weights = dict(weights)

    def predict(self, component_predictions: Mapping[str, Sequence[float]]) -> list[float]:
        if set(component_predictions) != set(self.weights):
            raise ModelError("stacker component set does not match fitted weights")
        lengths = {len(values) for values in component_predictions.values()}
        if len(lengths) != 1:
            raise ModelError("stacker components must have equal lengths")
        count = lengths.pop() if lengths else 0
        if count == 0:
            raise ModelError("stacker requires at least one prediction")
        output: list[float] = []
        for index in range(count):
            value = sum(self.weights[name] * float(component_predictions[name][index]) for name in self.weights)
            output.append(min(1.0, max(0.0, value)))
        return output


def _feature_names(rows: Sequence[FeatureRow]) -> tuple[str, ...]:
    names = sorted({name for row in rows for name in row.features})
    if not names:
        raise ModelError("candidate training requires at least one feature")
    return tuple(names)


def _row_matrix(rows: Sequence[FeatureRow], feature_names: Sequence[str]) -> list[list[float]]:
    matrix: list[list[float]] = []
    for row in rows:
        values: list[float] = []
        for name in feature_names:
            raw = row.features.get(name, 0.0)
            value = float(raw)
            if not math.isfinite(value):
                value = 0.0
            values.append(value)
        matrix.append(values)
    return matrix


def _group_order(rows: Sequence[FeatureRow]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for row in rows:
        group = row.split_group_id or row.market_ticker
        if group not in seen:
            seen.add(group)
            ordered.append(group)
    return tuple(ordered)


def _rows_for_group_indices(rows: Sequence[FeatureRow], groups: Sequence[str], indices: Sequence[int]) -> list[int]:
    wanted = {groups[index] for index in indices}
    return [index for index, row in enumerate(rows) if (row.split_group_id or row.market_ticker) in wanted]


def _fit_candidate(name: str, params: Mapping[str, Any], x: Sequence[Sequence[float]], y: Sequence[int], seed: int):
    if len(set(y)) < 2:
        raise ModelError("candidate training block must contain both classes")
    if name == "logistic":
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler

        return make_pipeline(
            StandardScaler(),
            LogisticRegression(
                C=float(params["C"]),
                solver="lbfgs",
                max_iter=1000,
                random_state=seed,
            ),
        ).fit(x, y)
    if name == "hist_gradient_boosting":
        from sklearn.ensemble import HistGradientBoostingClassifier

        return HistGradientBoostingClassifier(
            learning_rate=float(params["learning_rate"]),
            max_leaf_nodes=int(params["max_leaf_nodes"]),
            max_iter=48,
            min_samples_leaf=2,
            l2_regularization=1.0,
            random_state=seed,
        ).fit(x, y)
    if name == "xgboost":
        from xgboost import XGBClassifier

        return XGBClassifier(
            n_estimators=48,
            max_depth=int(params["max_depth"]),
            learning_rate=float(params["learning_rate"]),
            min_child_weight=1.0,
            subsample=1.0,
            colsample_bytree=1.0,
            reg_lambda=1.0,
            objective="binary:logistic",
            eval_metric="logloss",
            tree_method="hist",
            device="cpu",
            n_jobs=1,
            random_state=seed,
            verbosity=0,
        ).fit(x, y)
    raise ModelError(f"unknown candidate: {name}")


def _candidate_grid(name: str) -> tuple[dict[str, Any], ...]:
    if name == "logistic":
        return ({"C": 0.1}, {"C": 1.0})
    if name == "hist_gradient_boosting":
        return (
            {"learning_rate": 0.05, "max_leaf_nodes": 7},
            {"learning_rate": 0.1, "max_leaf_nodes": 15},
        )
    if name == "xgboost":
        return (
            {"learning_rate": 0.05, "max_depth": 2},
            {"learning_rate": 0.1, "max_depth": 3},
        )
    raise ModelError(f"unknown candidate: {name}")


def _predict_model(model: object, x: Sequence[Sequence[float]]) -> list[float]:
    raw = model.predict_proba(x)[:, 1]
    return [min(1.0, max(0.0, float(value))) for value in raw]


def _select_params(
    name: str,
    rows: Sequence[FeatureRow],
    groups: Sequence[str],
    train_group_indices: Sequence[int],
    feature_names: Sequence[str],
    seed: int,
) -> dict[str, Any]:
    if len(train_group_indices) < 3:
        return dict(_candidate_grid(name)[0])
    inner_validation_groups = max(1, min(2, len(train_group_indices) // 3))
    inner_train_groups = tuple(train_group_indices[:-inner_validation_groups])
    inner_validation = tuple(train_group_indices[-inner_validation_groups:])
    train_rows = _rows_for_group_indices(rows, groups, inner_train_groups)
    validation_rows = _rows_for_group_indices(rows, groups, inner_validation)
    if not train_rows or not validation_rows:
        return dict(_candidate_grid(name)[0])

    all_x = _row_matrix(rows, feature_names)
    labels = [row.label_yes for row in rows]
    best_params: dict[str, Any] | None = None
    best_key: tuple[float, float] | None = None
    for params in _candidate_grid(name):
        try:
            fitted = _fit_candidate(
                name,
                params,
                [all_x[index] for index in train_rows],
                [labels[index] for index in train_rows],
                seed,
            )
        except ModelError:
            continue
        probabilities = _predict_model(fitted, [all_x[index] for index in validation_rows])
        score = probability_metrics([labels[index] for index in validation_rows], probabilities)
        key = (score.log_loss, score.brier)
        if best_key is None or key < best_key:
            best_key = key
            best_params = dict(params)
    if best_params is None:
        return dict(_candidate_grid(name)[0])
    return best_params


def train_candidate_models(
    dataset: Sequence[FeatureRow],
    splits: Sequence[WalkForwardSplit],
    seed: int,
) -> CandidateResults:
    rows = tuple(dataset)
    if not rows:
        raise ModelError("candidate training dataset cannot be empty")
    if not splits:
        raise ModelError("candidate training requires walk-forward splits")
    groups = _group_order(rows)
    if any(max(fold.lockbox_indices, default=-1) >= len(groups) for fold in splits):
        raise ModelError("split references unknown market group")
    if any(fold.lockbox_indices != splits[0].lockbox_indices for fold in splits[1:]):
        raise ModelError("all walk-forward folds must share one untouched lockbox")

    lockbox_group_indices = set(splits[0].lockbox_indices)
    development_group_indices = tuple(index for index in range(len(groups)) if index not in lockbox_group_indices)
    development_rows = _rows_for_group_indices(rows, groups, development_group_indices)
    feature_names = _feature_names([rows[index] for index in development_rows])
    x = _row_matrix(rows, feature_names)
    y = [row.label_yes for row in rows]

    oof: dict[str, list[float | None]] = {name: [None] * len(rows) for name in CANDIDATE_NAMES}
    selected: dict[str, list[dict[str, Any]]] = {name: [] for name in CANDIDATE_NAMES}

    for fold_number, fold in enumerate(splits):
        train_rows = _rows_for_group_indices(rows, groups, fold.train_indices)
        validation_rows = _rows_for_group_indices(rows, groups, fold.validation_indices)
        if not train_rows or not validation_rows:
            raise ModelError("walk-forward fold has empty train or validation rows")
        for name in CANDIDATE_NAMES:
            params = _select_params(name, rows, groups, fold.train_indices, feature_names, seed + fold_number)
            selected[name].append(dict(params))
            fitted = _fit_candidate(
                name,
                params,
                [x[index] for index in train_rows],
                [y[index] for index in train_rows],
                seed + fold_number,
            )
            probabilities = _predict_model(fitted, [x[index] for index in validation_rows])
            for row_index, probability in zip(validation_rows, probabilities):
                if oof[name][row_index] is not None:
                    raise ModelError("OOF prediction would overwrite an earlier validation prediction")
                oof[name][row_index] = probability

    oof_metrics: dict[str, ProbabilityMetrics] = {}
    for name in CANDIDATE_NAMES:
        positions = [index for index, value in enumerate(oof[name]) if value is not None]
        if not positions:
            raise ModelError(f"candidate {name} produced no OOF predictions")
        oof_metrics[name] = probability_metrics(
            [y[index] for index in positions],
            [float(oof[name][index]) for index in positions],
        )
    best_candidate = min(CANDIDATE_NAMES, key=lambda name: (oof_metrics[name].log_loss, oof_metrics[name].brier))

    final_models: dict[str, object] = {}
    for name in CANDIDATE_NAMES:
        params = selected[name][-1]
        final_models[name] = _fit_candidate(
            name,
            params,
            [x[index] for index in development_rows],
            [y[index] for index in development_rows],
            seed,
        )

    return CandidateResults(
        candidate_names=CANDIDATE_NAMES,
        best_candidate=best_candidate,
        oof_predictions={name: tuple(values) for name, values in oof.items()},
        selected_params={name: tuple(values) for name, values in selected.items()},
        oof_metrics=oof_metrics,
        feature_names=feature_names,
        final_models=final_models,
    )


def _validate_matrix(matrix: Sequence[Sequence[float]]) -> list[list[float]]:
    rows = [list(map(float, row)) for row in matrix]
    if not rows:
        raise ModelError("feature matrix cannot be empty")
    width = len(rows[0])
    if width == 0 or any(len(row) != width for row in rows):
        raise ModelError("feature matrix must be rectangular and non-empty")
    for row in rows:
        if any(not math.isfinite(value) for value in row):
            raise ModelError("feature matrix contains non-finite value")
    return rows


def _validate_probabilities(probabilities: Sequence[float], *, expected: int) -> list[float]:
    values = list(map(float, probabilities))
    if len(values) != expected:
        raise ModelError("probability vector length does not match features")
    if any(not math.isfinite(value) or value < 0.0 or value > 1.0 for value in values):
        raise ModelError("probability must be within [0,1]")
    return values


def _logit(probability: float, eps: float = 1e-4) -> float:
    clipped = min(1.0 - eps, max(eps, probability))
    return math.log(clipped / (1.0 - clipped))


def _sigmoid(value: float) -> float:
    if value >= 0.0:
        return 1.0 / (1.0 + math.exp(-value))
    exp_value = math.exp(value)
    return exp_value / (1.0 + exp_value)


def _softplus(value: float) -> float:
    if value > 0.0:
        return value + math.log1p(math.exp(-value))
    return math.log1p(math.exp(value))


def _feature_standardization(feature_matrix: Sequence[Sequence[float]]) -> tuple[tuple[float, ...], tuple[float, ...]]:
    width = len(feature_matrix[0])
    count = len(feature_matrix)
    means = tuple(sum(row[column] for row in feature_matrix) / count for column in range(width))
    scales: list[float] = []
    for column, mean in enumerate(means):
        variance = sum((row[column] - mean) ** 2 for row in feature_matrix) / count
        scale = math.sqrt(variance)
        scales.append(scale if scale > 1e-12 else 1.0)
    return means, tuple(scales)


def _standardize(
    feature_matrix: Sequence[Sequence[float]],
    means: Sequence[float],
    scales: Sequence[float],
) -> list[list[float]]:
    return [
        [(value - mean) / scale for value, mean, scale in zip(row, means, scales)]
        for row in feature_matrix
    ]


def fit_residual_model(
    train_features: Sequence[Sequence[float]],
    train_labels: Sequence[int],
    train_prior: Sequence[float],
    validation_features: Sequence[Sequence[float]],
    validation_labels: Sequence[int],
    validation_prior: Sequence[float],
    seed: int,
) -> ResidualModel:
    from scipy.optimize import minimize

    del seed  # deterministic convex objective; retained for a stable public interface
    train_x = _validate_matrix(train_features)
    validation_x = _validate_matrix(validation_features)
    if len(train_x[0]) != len(validation_x[0]):
        raise ModelError("train and validation residual features must have equal width")
    train_y = list(train_labels)
    validation_y = list(validation_labels)
    if len(train_y) != len(train_x) or len(validation_y) != len(validation_x):
        raise ModelError("residual labels must match feature rows")
    if any(label not in (0, 1) for label in train_y + validation_y):
        raise ModelError("residual labels must be binary")
    if len(set(train_y)) < 2:
        raise ModelError("residual training block must contain both classes")
    train_p = _validate_probabilities(train_prior, expected=len(train_x))
    validation_p = _validate_probabilities(validation_prior, expected=len(validation_x))

    means, scales = _feature_standardization(train_x)
    standardized_train = _standardize(train_x, means, scales)
    prior_logits = [_logit(probability) for probability in train_p]
    l2_penalty = 0.25

    def objective(parameters) -> float:
        intercept = float(parameters[0])
        coefficients = [float(value) for value in parameters[1:]]
        negative_log_likelihood = 0.0
        for row, prior_logit, label in zip(standardized_train, prior_logits, train_y):
            residual = intercept + sum(coefficient * value for coefficient, value in zip(coefficients, row))
            score = prior_logit + residual
            negative_log_likelihood += _softplus(score) - label * score
        negative_log_likelihood /= len(train_y)
        regularization = 0.5 * l2_penalty * sum(coefficient * coefficient for coefficient in coefficients)
        return negative_log_likelihood + regularization

    result = minimize(
        objective,
        [0.0] * (len(train_x[0]) + 1),
        method="L-BFGS-B",
        options={"ftol": 1e-12, "maxiter": 1000},
    )
    if not result.success:
        raise ModelError(f"residual optimizer failed: {result.message}")

    candidate = ResidualModel(
        intercept=float(result.x[0]),
        coefficients=tuple(float(value) for value in result.x[1:]),
        feature_means=means,
        feature_scales=scales,
        component_weight=1.0,
    )
    corrected = candidate.predict(validation_x, validation_p)
    base_metrics = probability_metrics(validation_y, validation_p)
    corrected_metrics = probability_metrics(validation_y, corrected)
    improves = corrected_metrics.log_loss < base_metrics.log_loss and corrected_metrics.brier < base_metrics.brier
    if improves:
        return candidate
    return ResidualModel(
        intercept=candidate.intercept,
        coefficients=candidate.coefficients,
        feature_means=candidate.feature_means,
        feature_scales=candidate.feature_scales,
        component_weight=0.0,
    )


def fit_stacker(oof_predictions: Mapping[str, Sequence[float]], labels: Sequence[int]) -> Stacker:
    from scipy.optimize import minimize

    if not oof_predictions:
        raise ModelError("stacker requires at least one component")
    names = tuple(oof_predictions)
    count = len(labels)
    if count == 0 or any(len(values) != count for values in oof_predictions.values()):
        raise ModelError("stacker components and labels must have equal non-zero length")
    y = list(labels)
    if any(label not in (0, 1) for label in y):
        raise ModelError("stacker labels must be binary")
    component_values = {name: _validate_probabilities(values, expected=count) for name, values in oof_predictions.items()}

    def objective(raw_weights) -> float:
        probabilities = [
            sum(float(raw_weights[position]) * component_values[name][index] for position, name in enumerate(names))
            for index in range(count)
        ]
        return probability_metrics(y, probabilities).log_loss

    initial = [1.0 / len(names)] * len(names)
    result = minimize(
        objective,
        initial,
        method="SLSQP",
        bounds=[(0.0, 1.0)] * len(names),
        constraints={"type": "eq", "fun": lambda weights: float(sum(weights) - 1.0)},
        options={"ftol": 1e-12, "maxiter": 500, "disp": False},
    )
    if not result.success:
        best_name = min(names, key=lambda name: probability_metrics(y, component_values[name]).log_loss)
        weights = {name: 1.0 if name == best_name else 0.0 for name in names}
        return Stacker(weights)

    raw = [max(0.0, float(value)) for value in result.x]
    total = sum(raw)
    if total <= 0.0:
        raise ModelError("stacker optimizer returned zero total weight")
    normalized = [value / total for value in raw]
    normalized = [0.0 if value < 1e-10 else value for value in normalized]
    renormalized_total = sum(normalized)
    normalized = [value / renormalized_total for value in normalized]
    return Stacker({name: normalized[position] for position, name in enumerate(names)})
