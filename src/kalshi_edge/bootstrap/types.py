from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MarketLabel(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticker: str
    strike: float
    result: Literal["yes", "no"]
    settlement_value: float
    open_ts_ns: int
    close_ts_ns: int
    settlement_ts_ns: int | None = None


class FeedQuality(BaseModel):
    model_config = ConfigDict(frozen=True)

    healthy: bool
    reasons: tuple[str, ...] = ()
    kalshi_stale_seconds: float | None = Field(default=None, ge=0)
    binance_stale_seconds: float | None = Field(default=None, ge=0)
    brti_stale_seconds: float | None = Field(default=None, ge=0)


class FeatureRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    market_ticker: str
    checkpoint_ts_ns: int
    label_yes: Literal[0, 1]
    features: dict[str, float]
    source_max_ts_ns: dict[str, int]


class PredictionRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    prediction_ts_ns: int
    market_ticker: str | None
    status: Literal["OK", "NO_PREDICTION"]
    p_yes: float | None = Field(default=None, ge=0, le=1)
    p_no: float | None = Field(default=None, ge=0, le=1)
    predicted_side: Literal["ABOVE", "BELOW"] | None = None
    feed_quality: FeedQuality
    model_hash: str | None
    reason: str | None = None

    @model_validator(mode="after")
    def validate_probability_state(self) -> "PredictionRecord":
        if self.status == "OK":
            if self.p_yes is None or self.p_no is None or self.predicted_side is None:
                raise ValueError("OK prediction requires probabilities and predicted side")
            if abs((self.p_yes + self.p_no) - 1.0) > 1e-9:
                raise ValueError("prediction probabilities must sum to 1")
        elif self.reason is None:
            raise ValueError("NO_PREDICTION requires a reason")
        return self
