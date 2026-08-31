from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CollectorSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KALSHI_", env_file=".env", extra="ignore")

    key_id: str | None = None
    private_key_path: Path | None = None
    env: Literal["production", "demo"] = "production"
    data_dir: Path = Path("./data")
    series_ticker: str = "KXBTC15M"
    segment_max_events: int = Field(default=1000, ge=1)
    fsync_every: int = Field(default=1, ge=1)
    reconnect_initial_seconds: float = Field(default=0.5, gt=0)
    reconnect_max_seconds: float = Field(default=30.0, gt=0)
    stale_after_seconds: float = Field(default=15.0, gt=1)

    @property
    def rest_base_url(self) -> str:
        if self.env == "demo":
            return "https://demo-api.kalshi.co/trade-api/v2"
        return "https://external-api.kalshi.com/trade-api/v2"

    @property
    def ws_url(self) -> str:
        if self.env == "demo":
            return "wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2"
        return "wss://external-api-ws.kalshi.com/trade-api/ws/v2"

    def require_credentials(self) -> tuple[str, Path]:
        if not self.key_id or not self.private_key_path:
            raise RuntimeError("KALSHI_KEY_ID and KALSHI_PRIVATE_KEY_PATH are required for WebSocket collection")
        return self.key_id, self.private_key_path
