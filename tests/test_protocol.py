from kalshi_edge.protocol import build_subscriptions, normalize_ws_message


def test_build_subscriptions_scopes_market_channels_and_brti_and_lifecycle() -> None:
    messages = build_subscriptions(["KXBTC15M-EXAMPLE"])
    assert messages[0]["params"] == {
        "channels": ["orderbook_delta"],
        "market_tickers": ["KXBTC15M-EXAMPLE"],
    }
    assert messages[1]["params"] == {
        "channels": ["trade"],
        "market_tickers": ["KXBTC15M-EXAMPLE"],
    }
    assert messages[2]["params"] == {
        "channels": ["cfbenchmarks_value"],
        "index_ids": ["BRTI"],
    }
    assert messages[3]["params"] == {"channels": ["market_lifecycle_v2"]}


def test_normalize_ws_message_keeps_exact_payload_and_extracts_source_timestamp() -> None:
    event = normalize_ws_message(
        {
            "type": "trade",
            "sid": 11,
            "msg": {
                "trade_id": "t1",
                "market_ticker": "KXBTC15M-X",
                "yes_price_dollars": "0.360",
                "no_price_dollars": "0.640",
                "count_fp": "3.00",
                "taker_side": "no",
                "ts_ms": 1_669_149_841_000,
            },
        },
        receive_ts_ns=1_669_149_841_999_000_000,
    )
    assert event.source_ts_ms == 1_669_149_841_000
    assert event.market_ticker == "KXBTC15M-X"
    assert event.payload["msg"]["trade_id"] == "t1"
    assert len(event.payload_sha256) == 64


def test_normalize_rest_payload_marks_source_and_snapshot_type() -> None:
    from kalshi_edge.protocol import normalize_rest_payload
    event = normalize_rest_payload(
        {"series": {"ticker": "KXBTC15M", "fee_type": "quadratic", "fee_multiplier": 1}},
        message_type="series_snapshot",
        receive_ts_ns=1_700_000_000_000_000_000,
        request_id="rest-1",
    )
    assert event.source == "kalshi_rest"
    assert event.connection_id == "rest-1"
    assert event.message_type == "series_snapshot"
    assert event.payload["series"]["ticker"] == "KXBTC15M"


def test_normalize_ws_frame_preserves_exact_wire_text() -> None:
    from hashlib import sha256
    from kalshi_edge.protocol import normalize_ws_frame

    raw = '{"type":"trade", "sid":11, "seq":2, "msg":{"market_ticker":"KXBTC15M-X","ts_ms":1700000000000}}\n'
    event = normalize_ws_frame(raw, receive_ts_ns=9, connection_id="conn-a")

    assert event.wire_encoding == "utf-8"
    assert event.wire_payload == raw
    assert event.wire_sha256 == sha256(raw.encode("utf-8")).hexdigest()
    assert event.payload["type"] == "trade"


def test_subscriptions_without_open_market_keep_benchmark_and_lifecycle() -> None:
    from kalshi_edge.protocol import build_subscriptions

    subscriptions = build_subscriptions([])
    channels = [item["params"]["channels"] for item in subscriptions]
    assert ["cfbenchmarks_value"] in channels
    assert ["market_lifecycle_v2"] in channels
    assert ["orderbook_delta"] not in channels
    assert ["trade"] not in channels


def test_control_snapshot_uses_non_network_source() -> None:
    from kalshi_edge.protocol import normalize_control_payload
    event = normalize_control_payload(
        {"project_version": "0.1.0"},
        message_type="collector_session_snapshot",
        receive_ts_ns=1,
        session_id="session-1",
    )
    assert event.source == "collector_control"
    assert event.connection_id == "session-1"
    assert event.message_type == "collector_session_snapshot"
