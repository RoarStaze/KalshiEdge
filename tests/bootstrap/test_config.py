from pathlib import Path

from kalshi_edge.bootstrap.config import BootstrapSettings


def test_bootstrap_settings_defaults_are_isolated_and_kxbtc15m() -> None:
    settings = BootstrapSettings()
    assert settings.bootstrap_dir == Path("data/bootstrap")
    assert settings.series_ticker == "KXBTC15M"
    assert settings.binance_symbol == "BTCUSDT"
    assert settings.checkpoint_seconds == (
        840, 780, 720, 660, 600, 540, 480, 420, 360,
        300, 240, 180, 120, 60, 45, 30, 20, 10,
    )
    assert settings.kalshi_stale_seconds == 5.0
    assert settings.binance_stale_seconds == 5.0
    assert settings.random_seed == 73115
