from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_CHECKPOINT_SECONDS = (
    840,
    780,
    720,
    660,
    600,
    540,
    480,
    420,
    360,
    300,
    240,
    180,
    120,
    60,
    45,
    30,
    20,
    10,
)


class BootstrapSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KALSHI_BOOTSTRAP_",
        env_file=".env",
        extra="ignore",
    )

    bootstrap_dir: Path = Path("data/bootstrap")
    series_ticker: str = "KXBTC15M"
    binance_symbol: str = "BTCUSDT"
    checkpoint_seconds: tuple[int, ...] = DEFAULT_CHECKPOINT_SECONDS
    kalshi_stale_seconds: float = Field(default=5.0, gt=0)
    binance_stale_seconds: float = Field(default=5.0, gt=0)
    random_seed: int = 73115
