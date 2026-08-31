from __future__ import annotations

"""Structural KXBTC15M settlement probability engine."""

import math
from typing import Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .types import FeatureRow


class StructuralDataError(RuntimeError):
    """Raised when a structural probability cannot be produced safely."""


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


class StructuralModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate: Literal["diffusion", "empirical_residual"]
    seed: int
    simulations: int = Field(gt=0)

    @classmethod
    def fit(
        cls,
        training_rows: Sequence[FeatureRow],
        *,
        candidate: Literal["diffusion", "empirical_residual"] = "diffusion",
        seed: int = 73115,
        simulations: int = 4096,
    ) -> "StructuralModel":
        if not training_rows:
            raise StructuralDataError("structural model requires training rows")
        return cls(candidate=candidate, seed=seed, simulations=simulations)

    def predict_proba(self, state: object) -> float:
        raise StructuralDataError("pre-final-minute simulation is not implemented yet")

    def predict_final_minute(self, state: FinalMinuteState) -> float:
        _validate_final_minute_state(state)
        if state.elapsed_observations == 60:
            settlement_average = sum(observation.value for observation in state.observations) / 60.0
            return 1.0 if settlement_average >= state.strike else 0.0
        raise StructuralDataError("partial final-minute simulation is not implemented yet")
