from kalshi_edge.config import CollectorSettings


def test_demo_environment_selects_documented_endpoints(monkeypatch) -> None:
    monkeypatch.setenv("KALSHI_ENV", "demo")
    settings = CollectorSettings(_env_file=None)
    assert settings.rest_base_url == "https://demo-api.kalshi.co/trade-api/v2"
    assert settings.ws_url == "wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2"


def test_collector_has_positive_stale_feed_timeout() -> None:
    from kalshi_edge.config import CollectorSettings
    settings = CollectorSettings(_env_file=None)
    assert settings.stale_after_seconds > 1
