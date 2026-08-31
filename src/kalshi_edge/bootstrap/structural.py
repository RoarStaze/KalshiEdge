from __future__ import annotations

"""Structural KXBTC15M settlement probability engine."""

import math
import random
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
