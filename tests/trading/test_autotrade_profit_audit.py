"""Read-only full-journal AutoTrade accounting tests; exchange replies are synthetic.

This suite must never load credentials, send POST, place orders or inspect live accounts.
"""
from decimal import Decimal

from kalshi_edge.trading.autotrade_history import AutoTradeHistory
from kalshi_edge.trading.autotrade_pnl import enrich_history

BASE = "KXBTC15M-LEDGER-"


def _stage(ticker: str, i: int):
    return {
        "stage": "FINAL_BUY", "outcome": "UP", "fill_count": "0.10",
        "order_id": f"order-{i}", "status": "FILLED",
    }


def _fill(ticker: str, i: int):
    return {
        "ticker": ticker, "fill_id": f"fill-{i}", "order_id": f"order-{i}",
        "action": "buy", "side": "yes", "count_fp": "0.10",
        "yes_price_dollars": "0.6000", "no_price_dollars": "0.4000",
        "fee_cost": "0.001000",
    }


class FakeAccount:
    def __init__(self, ticker_count: int, *, mismatched=()):
        self.mismatched = set(mismatched)
        self.tick = [f"{BASE}{i:04d}" for i in range(ticker_count)]
        self.orders = {ticker: _fill(ticker, i) for i, ticker in enumerate(self.tick)}
        self.calls = []

    def get(self, path, *, params=None):
        assert path in ("/portfolio/settlements", "/portfolio/fills")
        self.calls.append((path, dict(params or {})))
        if path == "/portfolio/settlements":
            return {
                "settlements": [
                    {
                        "ticker": ticker, "market_result": "yes",
                        "yes_count_fp": "0.20" if ticker in self.mismatched else "0.10",
                        "no_count_fp": "0.00",
                    }
                    for ticker in self.tick
                ],
                "cursor": "",
            }
        tickers = params["ticker"].split(",")
        assert 1 <= len(tickers) <= 100
        return {"fills": [self.orders[t] for t in tickers], "cursor": ""}


def _journal(tmp_path, count=1):
    root = tmp_path / "bootstrap"
    store = AutoTradeHistory(root / "data" / "autotrade_history.sqlite3")
    for i in range(count):
        ticker = f"{BASE}{i:04d}"
        store.record_attempt(
            ticker=ticker, attempted_at_ns=1_900_000_000_000_000_000 + i,
            outcome="UP", amount="0.10", confidence=0.98,
        )
        store.record_stage_attempt(
            ticker=ticker, stage="FINAL_BUY",
            attempted_at_ns=1_900_000_000_000_000_000 + i,
            outcome="UP", amount="0.10", confidence=0.98,
        )
        store.record_stage_result(
            ticker=ticker, stage="FINAL_BUY", status="FILLED",
            order_id=f"order-{i}", fill_count="0.10",
        )
        store.record_result(ticker=ticker, status="FILLED",
                            order_id=f"order-{i}", fill_count="0.10")
    return root, store


def test_settlement_position_mismatch_must_never_be_certified_as_profit(tmp_path):
    ticker = f"{BASE}0000"
    account = FakeAccount(1, mismatched=(ticker,))
    row = {
        "ticker": ticker, "status": "FILLED", "outcome": "UP",
        "stages": [_stage(ticker, 0)],
    }
    enriched = enrich_history([row], account)[0]
    assert enriched["realized_pnl_dollars"] is None
    assert enriched["pnl_status"] != "SETTLED"


def test_full_journal_ledger_reads_beyond_ui_limit_and_batches_exchange_fills(tmp_path):
    from kalshi_edge.trading.autotrade_profit_audit import audit_autotrade_profit

    root, store = _journal(tmp_path, 105)
    assert len(store.recent(limit=100)) == 100
    assert len(store.all_attempts()) == 105
    exchange = FakeAccount(105)
    report = audit_autotrade_profit(root, client=exchange)
    assert report["scope"] == "LOCAL_AUTOTRADE_JOURNAL_WITH_LIVE_SETTLEMENT_EVIDENCE"
    assert report["journal_market_count"] == 105
    assert report["verified_settled_markets"] == 105
    assert report["unverified_filled_markets"] == 0
    assert report["complete_for_journal_markets"] is True
    assert Decimal(report["verified_net_pnl_dollars"]) == Decimal("4.095")
    fills = [params for path, params in exchange.calls if path == "/portfolio/fills"]
    assert len(fills) == 2
    assert sorted(len(p["ticker"].split(",")) for p in fills) == [5, 100]
    assert all(path.startswith("/portfolio/") for path, _ in exchange.calls)


def test_offline_and_missing_settlement_data_do_not_invent_account_profit(tmp_path):
    from kalshi_edge.trading.autotrade_profit_audit import audit_autotrade_profit

    root, _ = _journal(tmp_path, 2)
    offline = audit_autotrade_profit(root)
    assert offline["journal_market_count"] == 2
    assert offline["verified_net_pnl_dollars"] is None
    assert offline["complete_for_journal_markets"] is False

    class NoSettlements:
        def get(self, path, *, params=None):
            assert path == "/portfolio/settlements"
            return {"settlements": [], "cursor": ""}

    unavailable = audit_autotrade_profit(root, client=NoSettlements())
    assert unavailable["journal_market_count"] == 2
    assert unavailable["verified_settled_markets"] == 0
    assert unavailable["verified_net_pnl_dollars"] is None
    assert unavailable["complete_for_journal_markets"] is False


def test_strict_audit_rejects_settlement_without_position_evidence(tmp_path):
    from kalshi_edge.trading.autotrade_profit_audit import audit_autotrade_profit

    root, _ = _journal(tmp_path)
    ticker = f"{BASE}0000"

    class IncompleteSettlement:
        def get(self, path, *, params=None):
            if path == "/portfolio/settlements":
                return {"settlements": [{"ticker": ticker, "market_result": "yes"}], "cursor": ""}
            return {"fills": [_fill(ticker, 0)], "cursor": ""}

    report = audit_autotrade_profit(root, client=IncompleteSettlement())
    assert report["verified_net_pnl_dollars"] is None
    assert report["verified_settled_markets"] == 0
    assert report["complete_for_journal_markets"] is False
