"""Profit audit explanation regressions: no real exchange requests."""
from kalshi_edge.trading.autotrade_history import AutoTradeHistory
from kalshi_edge.trading.autotrade_pnl import enrich_history
from kalshi_edge.trading.autotrade_profit_audit import audit_autotrade_profit

TICKER = "KXBTC15M-DIAGNOSTIC-0000"


def _row():
    return {
        "ticker": TICKER, "status": "FILLED", "outcome": "UP",
        "stages": [{
            "stage": "FINAL_BUY", "status": "FILLED",
            "outcome": "UP", "order_id": "private-exchange-order",
            "fill_count": "0.10",
        }],
    }


class FakeExchange:
    def __init__(self, *, settlement=True, shares="0.10", fill=True, extra=False):
        self.settlement = settlement
        self.shares = shares
        self.fill = fill
        self.extra = extra

    def get(self, path, *, params=None):
        if path == "/portfolio/settlements":
            items = [{
                "ticker": TICKER, "market_result": "yes",
                "yes_count_fp": self.shares, "no_count_fp": "0.00",
            }] if self.settlement else []
            return {"settlements": items, "cursor": ""}
        if path == "/portfolio/fills":
            items = [{
                "ticker": TICKER, "order_id": "private-exchange-order",
                "fill_id": "private-fill",
                "action": "buy", "side": "yes", "count_fp": "0.10",
                "yes_price_dollars": "0.6000", "no_price_dollars": "0.4000",
                "fee_cost": "0.001000",
            }] if self.fill else []
            if self.extra:
                items.append({
                    "ticker": TICKER, "order_id": "other-user-manual-order",
                    "fill_id": "other-fill", "action": "buy", "side": "yes",
                    "count_fp": "0.10", "yes_price_dollars": "0.6000",
                    "no_price_dollars": "0.4000", "fee_cost": "0.001000",
                })
            return {"fills": items, "cursor": ""}
        raise AssertionError("Unexpected exchange API path")


def test_reason_codes_distinguish_archived_or_pending_from_position_mismatch():
    absent = enrich_history([_row()], FakeExchange(settlement=False),
                            require_settlement_counts=True)[0]
    mismatch = enrich_history([_row()], FakeExchange(shares="0.20"),
                              require_settlement_counts=True)[0]
    assert absent["pnl_reason"] == "SETTLEMENT_NOT_IN_LIVE_API"
    assert mismatch["pnl_reason"] == "SETTLEMENT_POSITION_MISMATCH"
    assert absent["realized_pnl_dollars"] is None
    assert mismatch["realized_pnl_dollars"] is None


def test_reason_codes_distinguish_missing_fill_and_other_account_fill():
    missing = enrich_history([_row()], FakeExchange(fill=False),
                             require_settlement_counts=True)[0]
    mixed = enrich_history([_row()], FakeExchange(extra=True),
                           require_settlement_counts=True)[0]
    assert missing["pnl_reason"] == "FILL_QUANTITY_UNRECONCILED"
    assert mixed["pnl_reason"] == "OTHER_ACCOUNT_FILL_IN_MARKET"
    assert missing["realized_pnl_dollars"] is None
    assert mixed["realized_pnl_dollars"] is None


def test_verified_profit_reason_and_read_error_remain_distinct():
    good = enrich_history([_row()], FakeExchange(),
                          require_settlement_counts=True)[0]
    assert good["pnl_reason"] == "VERIFIED"
    assert good["realized_pnl_dollars"] == "0.0390"

    class FailedAPI:
        def get(self, *args, **kwargs):
            raise OSError("Network unavailable")

    failed = enrich_history([_row()], FailedAPI(),
                            require_settlement_counts=True)[0]
    assert failed["pnl_reason"] == "EXCHANGE_EVIDENCE_UNAVAILABLE"
    assert failed["realized_pnl_dollars"] is None


def test_audit_counts_all_reason_codes_and_details_omit_order_identifiers(tmp_path):
    root = tmp_path / "bootstrap"
    store = AutoTradeHistory(root / "data" / "autotrade_history.sqlite3")
    entries = [
        ("KXBTC15M-DIAGNOSTIC-0000", "FILLED", "0.10"),
        ("KXBTC15M-DIAGNOSTIC-0001", "NO_FILL", "0.00"),
        ("KXBTC15M-DIAGNOSTIC-0002", "UNKNOWN", None),
    ]
    for i, (ticker, status, qty) in enumerate(entries):
        store.record_attempt(
            ticker=ticker, attempted_at_ns=1_900_000_000_000_000_000+i,
            outcome="UP", amount="0.10", confidence=0.98,
        )
        store.record_stage_attempt(
            ticker=ticker, stage="FINAL_BUY",
            attempted_at_ns=1_900_000_000_000_000_000+i,
            outcome="UP", amount="0.10", confidence=0.98,
        )
        store.record_stage_result(
            ticker=ticker, stage="FINAL_BUY", status=status,
            order_id=f"private-order-{i}" if status != "UNKNOWN" else None,
            fill_count=qty,
        )
        store.record_result(
            ticker=ticker, status=status,
            order_id=f"private-order-{i}" if status != "UNKNOWN" else None,
            fill_count=qty,
        )

    report = audit_autotrade_profit(root)
    assert report["evidence_reason_counts"] == {
        "EXCHANGE_NOT_QUERIED": 1,
        "NO_FILL": 1,
        "ORDER_OUTCOME_UNKNOWN": 1,
    }
    assert "market_details" not in report

    detailed = audit_autotrade_profit(root, details=True)
    assert len(detailed["market_details"]) == 3
    assert detailed["market_details"][0]["ticker"].startswith("KXBTC15M-")
    for row in detailed["market_details"]:
        assert "order_id" not in row
        assert "client_order_id" not in row
        assert "api_key" not in row
        assert row["realized_pnl_dollars"] is None


def test_audit_reasons_have_same_number_of_markets_as_journal(tmp_path):
    root = tmp_path / "bootstrap"
    store = AutoTradeHistory(root / "data" / "autotrade_history.sqlite3")
    store.record_attempt(ticker=TICKER, attempted_at_ns=123,
                         outcome="UP", amount="0.10", confidence=0.9)
    store.record_stage_attempt(ticker=TICKER, stage="FINAL_BUY",
                               attempted_at_ns=123, outcome="UP",
                               amount="0.10", confidence=0.9)
    store.record_stage_result(ticker=TICKER, stage="FINAL_BUY",
                              status="FILLED", order_id="private-exchange-order",
                              fill_count="0.10")
    store.record_result(ticker=TICKER, status="FILLED",
                        order_id="private-exchange-order", fill_count="0.10")
    report = audit_autotrade_profit(root, client=FakeExchange(), details=True)
    assert report["verified_settled_markets"] == 1
    assert report["evidence_reason_counts"] == {"VERIFIED": 1}
    assert sum(report["evidence_reason_counts"].values()) == report["journal_market_count"]
    assert report["market_details"][0]["pnl_reason"] == "VERIFIED"
    assert report["market_details"][0]["realized_pnl_dollars"] == "0.0390"
