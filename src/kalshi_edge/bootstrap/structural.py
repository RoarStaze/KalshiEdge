from __future__ import annotations

"""Structural KXBTC15M settlement probability engine."""

import json
import math
import os
import random
from pathlib import Path
from typing import Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .types import FeatureRow


CandidateName = Literal["diffusion", "empirical_residual"]


class StructuralDataError(RuntimeError):
    """Raised when a structural probability cannot be produced safely."""


class StructuralState(BaseModel):
    model_config = ConfigDict(frozen=True)

    current_value: float = Field(gt=0)
    strike: float = Field(gt=0)
    seconds_remaining: float = Field(gt=0)
    volatility_per_second: float = Field(ge=0)
    recent_return_5s: float = Field(default=0.0, gt=-1.0)


class FinalMinuteObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    second_index: int = Field(ge=0, lt=60)
    value: float = Field(gt=0)
    source_ts_ns: int = Field(gt=0)
    ambiguous: bool = False


class FinalMinuteState(BaseModel):
    model_config = ConfigDict(frozen=True)

    strike: float = Field(gt=0)
    current_value: float = Field(gt=0)
    volatility_per_second: float = Field(ge=0)
    elapsed_observations: int = Field(ge=0, le=60)
    observations: tuple[FinalMinuteObservation, ...] = ()


def required_remaining_mean(
    *,
    strike: float,
    observed_values: Sequence[float],
    total_observations: int = 60,
) -> float:
    """Return the remaining-sample mean required for an at-least-strike payout."""
    if not math.isfinite(strike) or strike <= 0:
        raise StructuralDataError("strike must be a finite positive value")
    if total_observations <= 0:
        raise StructuralDataError("total observations must be positive")
    if len(observed_values) >= total_observations:
        raise StructuralDataError("no remaining observations exist")

    total = 0.0
    for value in observed_values:
        if not math.isfinite(value) or value <= 0:
            raise StructuralDataError("observed values must be finite and positive")
        total += value
    remaining = total_observations - len(observed_values)
    return (total_observations * strike - total) / remaining


def _validate_final_minute_state(state: FinalMinuteState) -> None:
    seconds = [observation.second_index for observation in state.observations]
    if len(set(seconds)) != len(seconds):
        raise StructuralDataError("duplicate final-minute observation")
    if any(observation.ambiguous for observation in state.observations):
        raise StructuralDataError("ambiguous final-minute observation")
    if len(state.observations) != state.elapsed_observations:
        raise StructuralDataError("missing final-minute observation")
    if set(seconds) != set(range(state.elapsed_observations)):
        raise StructuralDataError("missing final-minute observation")

    ordered = sorted(state.observations, key=lambda observation: observation.second_index)
    timestamps = [observation.source_ts_ns for observation in ordered]
    if len(set(timestamps)) != len(timestamps):
        raise StructuralDataError("duplicate final-minute source timestamp")
    if any(current <= previous for previous, current in zip(timestamps, timestamps[1:])):
        raise StructuralDataError("ambiguous final-minute timestamp ordering")


def _clip(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def _fit_path_statistics(training_rows: Sequence[FeatureRow]) -> tuple[float, float, tuple[float, ...]]:
    samples: list[tuple[float, float]] = []
    for row in training_rows:
        features = row.features
        if features.get("btc_return_5s_available", 0.0) <= 0.0:
            continue
        return_5s = features.get("btc_return_5s")
        sigma = features.get("btc_realized_vol_60s")
        if return_5s is None or sigma is None or return_5s <= -1.0 or sigma < 0.0:
            continue
        if not math.isfinite(return_5s) or not math.isfinite(sigma):
            continue
        samples.append((math.log1p(return_5s), sigma))

    if not samples:
        raise StructuralDataError("structural model requires usable past-only return observations")

    per_second = [log_return / 5.0 for log_return, _ in samples]
    raw_drift = sum(per_second) / len(per_second)
    mean_abs = sum(abs(value) for value in per_second) / len(per_second)
    drift_cap = 0.25 * mean_abs
    drift = _clip(raw_drift, -drift_cap, drift_cap) if drift_cap > 0.0 else 0.0

    residuals: list[float] = []
    for log_return, sigma in samples:
        scale = sigma * math.sqrt(5.0)
        if scale > 0.0:
            residual = (log_return - drift * 5.0) / scale
            if math.isfinite(residual):
                residuals.append(residual)
    if not residuals:
        residuals.append(0.0)
    else:
        mean_residual = sum(residuals) / len(residuals)
        centered = [value - mean_residual for value in residuals]
        rms = math.sqrt(sum(value * value for value in centered) / len(centered))
        residuals = [value / rms for value in centered] if rms > 0.0 else [0.0]
    return drift, drift_cap, tuple(residuals)


class StructuralModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate: CandidateName
    seed: int
    simulations: int = Field(gt=0)
    drift_per_second: float = 0.0
    drift_cap_per_second: float = Field(default=0.0, ge=0.0)
    residuals: tuple[float, ...] = ()

    @classmethod
    def fit(
        cls,
        training_rows: Sequence[FeatureRow],
        *,
        candidate: CandidateName = "diffusion",
        seed: int = 73115,
        simulations: int = 4096,
    ) -> "StructuralModel":
        if not training_rows:
            raise StructuralDataError("structural model requires training rows")
        drift, drift_cap, residuals = _fit_path_statistics(training_rows)
        return cls(
            candidate=candidate,
            seed=seed,
            simulations=simulations,
            drift_per_second=drift,
            drift_cap_per_second=drift_cap,
            residuals=residuals,
        )

    def _effective_drift(self, recent_return_5s: float = 0.0) -> float:
        if recent_return_5s <= -1.0 or not math.isfinite(recent_return_5s):
            raise StructuralDataError("recent return must be finite and greater than -1")
        local = math.log1p(recent_return_5s) / 5.0
        cap = self.drift_cap_per_second
        if cap <= 0.0:
            return 0.0
        local = _clip(local, -cap, cap)
        return _clip(0.5 * (self.drift_per_second + local), -cap, cap)

    def _shock(self, rng: random.Random) -> float:
        if self.candidate == "diffusion":
            return rng.gauss(0.0, 1.0)
        if not self.residuals:
            raise StructuralDataError("empirical residual model has no fitted residuals")
        return self.residuals[rng.randrange(len(self.residuals))]

    def _simulate_values(
        self,
        *,
        start_value: float,
        steps: int,
        volatility_per_second: float,
        drift_per_second: float,
        rng: random.Random,
    ) -> list[float]:
        if steps <= 0:
            return []
        value = start_value
        values: list[float] = []
        for _ in range(steps):
            log_step = drift_per_second + volatility_per_second * self._shock(rng)
            value *= math.exp(log_step)
            if not math.isfinite(value) or value <= 0.0:
                raise StructuralDataError("simulated structural path became invalid")
            values.append(value)
        return values

    def predict_proba(self, state: StructuralState) -> float:
        if state.seconds_remaining < 60.0:
            raise StructuralDataError("final-minute state is required once fewer than 60 seconds remain")
        steps = max(60, math.ceil(state.seconds_remaining))
        drift = self._effective_drift(state.recent_return_5s)
        rng = random.Random(self.seed)
        wins = 0
        for _ in range(self.simulations):
            path = self._simulate_values(
                start_value=state.current_value,
                steps=steps,
                volatility_per_second=state.volatility_per_second,
                drift_per_second=drift,
                rng=rng,
            )
            settlement_average = sum(path[-60:]) / 60.0
            if settlement_average >= state.strike:
                wins += 1
        return wins / self.simulations

    def predict_final_minute(self, state: FinalMinuteState) -> float:
        _validate_final_minute_state(state)
        ordered = sorted(state.observations, key=lambda observation: observation.second_index)
        known_values = [observation.value for observation in ordered]
        if state.elapsed_observations == 60:
            settlement_average = sum(known_values) / 60.0
            return 1.0 if settlement_average >= state.strike else 0.0

        remaining = 60 - state.elapsed_observations
        required_mean = required_remaining_mean(
            strike=state.strike,
            observed_values=known_values,
            total_observations=60,
        )
        start_value = known_values[-1] if known_values else state.current_value
        rng = random.Random(self.seed)
        wins = 0
        for _ in range(self.simulations):
            future = self._simulate_values(
                start_value=start_value,
                steps=remaining,
                volatility_per_second=state.volatility_per_second,
                drift_per_second=self.drift_per_second,
                rng=rng,
            )
            if sum(future) / remaining >= required_mean:
                wins += 1
        return wins / self.simulations


class StructuralCandidateMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate: CandidateName
    log_loss: float = Field(ge=0.0)
    brier_score: float = Field(ge=0.0, le=1.0)
    calibration_error: float = Field(ge=0.0, le=1.0)
    row_count: int = Field(gt=0)


class StructuralSelectionEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    chosen_candidate: CandidateName
    metrics: tuple[StructuralCandidateMetrics, ...]
    training_end_ts_ns: int
    validation_start_ts_ns: int
    evaluated_rows: int = Field(gt=0)
    excluded_subminute_rows: int = Field(ge=0)
    seed: int
    simulations: int = Field(gt=0)


class StructuralSelection(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: StructuralModel
    evidence: StructuralSelectionEvidence


def structural_state_from_row(row: FeatureRow) -> StructuralState:
    features = row.features
    required = ("btc_close", "strike", "seconds_remaining", "btc_realized_vol_60s")
    missing = [name for name in required if name not in features]
    if missing:
        raise StructuralDataError(f"structural validation row is missing features: {', '.join(missing)}")
    recent_return = features.get("btc_return_5s", 0.0) if features.get("btc_return_5s_available", 0.0) > 0.0 else 0.0
    return StructuralState(
        current_value=features["btc_close"],
        strike=features["strike"],
        seconds_remaining=features["seconds_remaining"],
        volatility_per_second=features["btc_realized_vol_60s"],
        recent_return_5s=recent_return,
    )


def _candidate_metrics(candidate: CandidateName, probabilities: Sequence[float], labels: Sequence[int]) -> StructuralCandidateMetrics:
    if not probabilities or len(probabilities) != len(labels):
        raise StructuralDataError("candidate metrics require aligned non-empty probabilities and labels")
    epsilon = 1e-12
    clipped = [_clip(probability, epsilon, 1.0 - epsilon) for probability in probabilities]
    log_loss = -sum(
        label * math.log(probability) + (1 - label) * math.log(1.0 - probability)
        for probability, label in zip(clipped, labels)
    ) / len(labels)
    brier = sum((probability - label) ** 2 for probability, label in zip(probabilities, labels)) / len(labels)

    calibration_total = 0.0
    bin_count = 10
    for bin_index in range(bin_count):
        members = [
            (probability, label)
            for probability, label in zip(probabilities, labels)
            if min(int(probability * bin_count), bin_count - 1) == bin_index
        ]
        if not members:
            continue
        mean_probability = sum(probability for probability, _ in members) / len(members)
        mean_label = sum(label for _, label in members) / len(members)
        calibration_total += len(members) * abs(mean_probability - mean_label)
    calibration_error = calibration_total / len(labels)
    return StructuralCandidateMetrics(
        candidate=candidate,
        log_loss=log_loss,
        brier_score=brier,
        calibration_error=calibration_error,
        row_count=len(labels),
    )


def choose_structural_candidate(metrics: Sequence[StructuralCandidateMetrics]) -> CandidateName:
    if not metrics:
        raise StructuralDataError("candidate selection requires metrics")
    return min(
        metrics,
        key=lambda item: (item.log_loss, item.brier_score, item.calibration_error, item.candidate),
    ).candidate


def _group_id(row: FeatureRow) -> str:
    return row.split_group_id or row.market_ticker


def select_structural_model(
    training_rows: Sequence[FeatureRow],
    validation_rows: Sequence[FeatureRow],
    *,
    seed: int = 73115,
    simulations: int = 4096,
) -> StructuralSelection:
    if not training_rows or not validation_rows:
        raise StructuralDataError("structural selection requires non-empty training and validation rows")

    training_groups = {_group_id(row) for row in training_rows}
    validation_groups = {_group_id(row) for row in validation_rows}
    overlap = training_groups & validation_groups
    if overlap:
        raise StructuralDataError(f"split group overlap between training and validation: {sorted(overlap)[0]}")

    training_end = max(row.checkpoint_ts_ns for row in training_rows)
    validation_start = min(row.checkpoint_ts_ns for row in validation_rows)
    if training_end >= validation_start:
        raise StructuralDataError("validation rows must occur strictly after all training rows")

    evaluable = [row for row in validation_rows if row.features.get("seconds_remaining", 0.0) >= 60.0]
    excluded_subminute = len(validation_rows) - len(evaluable)
    if not evaluable:
        raise StructuralDataError("no causally evaluable pre-final-minute validation rows")

    models: dict[CandidateName, StructuralModel] = {}
    metrics: list[StructuralCandidateMetrics] = []
    labels = [row.label_yes for row in evaluable]
    for candidate in ("diffusion", "empirical_residual"):
        model = StructuralModel.fit(
            training_rows,
            candidate=candidate,
            seed=seed,
            simulations=simulations,
        )
        probabilities = [model.predict_proba(structural_state_from_row(row)) for row in evaluable]
        models[candidate] = model
        metrics.append(_candidate_metrics(candidate, probabilities, labels))

    chosen = choose_structural_candidate(metrics)
    evidence = StructuralSelectionEvidence(
        chosen_candidate=chosen,
        metrics=tuple(metrics),
        training_end_ts_ns=training_end,
        validation_start_ts_ns=validation_start,
        evaluated_rows=len(evaluable),
        excluded_subminute_rows=excluded_subminute,
        seed=seed,
        simulations=simulations,
    )
    return StructuralSelection(model=models[chosen], evidence=evidence)


def save_selection_evidence(evidence: StructuralSelectionEvidence, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    content = json.dumps(
        evidence.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ) + "\n"
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)
