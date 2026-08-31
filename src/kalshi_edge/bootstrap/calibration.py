from __future__ import annotations

"""Later-block calibration selection for bootstrap probabilities."""

import math
from typing import Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .metrics import probability_metrics


class CalibrationError(RuntimeError):
    """Raised when a calibration model cannot be selected safely."""


class CalibrationSelection(BaseModel):
    model_config = ConfigDict(frozen=True)

    method: Literal["platt", "isotonic"]
    fit_count: int = Field(gt=0)
    selection_count: int = Field(gt=0)
    isotonic_eligible: bool
    platt_log_loss: float
    platt_brier: float
    isotonic_log_loss: float | None = None
    isotonic_brier: float | None = None


class Calibrator:
    def __init__(self, *, method: Literal["platt", "isotonic"], estimator: object, selection: CalibrationSelection) -> None:
        self.method = method
        self._estimator = estimator
        self.selection = selection

    def predict(self, probabilities: Sequence[float]) -> list[float]:
        checked = _validated_probabilities(probabilities)
        if self.method == "platt":
            matrix = [[_logit(probability)] for probability in checked]
            raw = self._estimator.predict_proba(matrix)[:, 1]
        else:
            raw = self._estimator.predict(checked)
        return [min(1.0, max(0.0, float(value))) for value in raw]


def _validated_probabilities(probabilities: Sequence[float]) -> list[float]:
    values = list(probabilities)
    if not values:
        raise CalibrationError("calibration probabilities cannot be empty")
    for probability in values:
        if not math.isfinite(probability) or probability < 0.0 or probability > 1.0:
            raise CalibrationError("calibration probability must be within [0,1]")
    return values


def _validate_labels(labels: Sequence[int], expected: int) -> list[int]:
    values = list(labels)
    if len(values) != expected:
        raise CalibrationError("probabilities and labels must have the same length")
    if any(label not in (0, 1) for label in values):
        raise CalibrationError("calibration labels must be binary")
    if len(set(values)) < 2:
        raise CalibrationError("calibration block must contain both classes")
    return values


def _logit(probability: float, eps: float = 1e-4) -> float:
    clipped = min(1.0 - eps, max(eps, probability))
    return math.log(clipped / (1.0 - clipped))


def _fit_platt(probabilities: Sequence[float], labels: Sequence[int]):
    from sklearn.linear_model import LogisticRegression

    estimator = LogisticRegression(
        C=1.0,
        solver="lbfgs",
        max_iter=1000,
        random_state=0,
    )
    estimator.fit([[_logit(probability)] for probability in probabilities], labels)
    return estimator


def _fit_isotonic(probabilities: Sequence[float], labels: Sequence[int]):
    from sklearn.isotonic import IsotonicRegression

    estimator = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
    estimator.fit(probabilities, labels)
    return estimator


def fit_calibrator(predictions: Sequence[float], labels: Sequence[int]) -> Calibrator:
    probabilities = _validated_probabilities(predictions)
    y = _validate_labels(labels, len(probabilities))
    if len(probabilities) < 6:
        raise CalibrationError("calibration block is too small")

    selection_count = max(2, len(probabilities) // 3)
    fit_count = len(probabilities) - selection_count
    fit_p = probabilities[:fit_count]
    fit_y = y[:fit_count]
    selection_p = probabilities[fit_count:]
    selection_y = y[fit_count:]
    if len(set(fit_y)) < 2:
        raise CalibrationError("calibration fit block must contain both classes")

    platt_initial = _fit_platt(fit_p, fit_y)
    platt_selection = [float(value) for value in platt_initial.predict_proba([[_logit(p)] for p in selection_p])[:, 1]]
    platt_metrics = probability_metrics(selection_y, platt_selection)

    isotonic_eligible = (
        len(probabilities) >= 40
        and len(set(probabilities)) >= 10
        and len(set(fit_p)) >= 8
        and len(selection_p) >= 10
    )
    isotonic_metrics = None
    if isotonic_eligible:
        isotonic_initial = _fit_isotonic(fit_p, fit_y)
        isotonic_selection = [float(value) for value in isotonic_initial.predict(selection_p)]
        isotonic_metrics = probability_metrics(selection_y, isotonic_selection)

    method: Literal["platt", "isotonic"] = "platt"
    if isotonic_metrics is not None:
        tolerance = 1e-12
        if isotonic_metrics.log_loss < platt_metrics.log_loss - tolerance:
            method = "isotonic"
        elif abs(isotonic_metrics.log_loss - platt_metrics.log_loss) <= tolerance and isotonic_metrics.brier < platt_metrics.brier:
            method = "isotonic"

    selection = CalibrationSelection(
        method=method,
        fit_count=fit_count,
        selection_count=selection_count,
        isotonic_eligible=isotonic_eligible,
        platt_log_loss=platt_metrics.log_loss,
        platt_brier=platt_metrics.brier,
        isotonic_log_loss=None if isotonic_metrics is None else isotonic_metrics.log_loss,
        isotonic_brier=None if isotonic_metrics is None else isotonic_metrics.brier,
    )
    estimator = _fit_platt(probabilities, y) if method == "platt" else _fit_isotonic(probabilities, y)
    return Calibrator(method=method, estimator=estimator, selection=selection)
