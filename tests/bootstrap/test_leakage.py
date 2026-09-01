from __future__ import annotations

from kalshi_edge.bootstrap.leakage import audit_feature_row
from kalshi_edge.bootstrap.types import FeatureRow


def test_leakage_audit_rejects_future_source_timestamp() -> None:
    row = FeatureRow(
        market_ticker="KXBTC15M-TEST",
        market_date="2026-08-01",
        split_group_id="KXBTC15M-TEST",
        checkpoint_ts_ns=1_000,
        label_yes=1,
        features={"btc_close": 100.0},
        source_max_ts_ns={"binance": 1_001},
    )

    findings = audit_feature_row(row)

    assert any(item.code == "FUTURE_SOURCE_TIMESTAMP" for item in findings)


def test_leakage_audit_rejects_label_or_settlement_features() -> None:
    row = FeatureRow(
        market_ticker="KXBTC15M-TEST",
        market_date="2026-08-01",
        split_group_id="KXBTC15M-TEST",
        checkpoint_ts_ns=1_000,
        label_yes=0,
        features={
            "btc_close": 100.0,
            "settlement_value": 101.0,
            "market_result_yes": 1.0,
        },
        source_max_ts_ns={"binance": 1_000},
    )

    findings = audit_feature_row(row)
    codes = {item.code for item in findings}

    assert "FORBIDDEN_FEATURE" in codes
    assert len([item for item in findings if item.code == "FORBIDDEN_FEATURE"]) == 2
