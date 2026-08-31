from kalshi_edge.discovery import extract_market_tickers


def test_extract_market_tickers_filters_kxbtc15m_open_markets() -> None:
    payload = {
        "events": [
            {
                "series_ticker": "KXBTC15M",
                "markets": [
                    {"ticker": "KXBTC15M-A", "status": "active"},
                    {"ticker": "KXBTC15M-B", "status": "finalized"},
                ],
            },
            {"series_ticker": "OTHER", "markets": [{"ticker": "OTHER-A", "status": "active"}]},
        ]
    }
    assert extract_market_tickers(payload, "KXBTC15M") == ["KXBTC15M-A"]
