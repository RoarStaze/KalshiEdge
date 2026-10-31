"""AutoTrade P/L must use authenticated exchange fills and settlement only.

All account responses are fake; no live orders or requests.
"""
from kalshi_edge.trading.autotrade_pnl import enrich_history


TICKER = "KXBTC15M-26OCT310900-00"


def stage(name, side, qty, oid, status="FILLED"):
    return {"stage": name, "outcome": side, "fill_count": qty,
            "order_id": oid, "status": status}


def entry(stages, ticker=TICKER):
    return {"ticker": ticker, "outcome": "UP", "status": "FILLED",
            "amount": "0.10", "stages": stages}


def fill(oid, side, qty, yes_price, no_price, fee="0.0000", action="buy"):
    return {"order_id": oid, "ticker": TICKER, "count_fp": qty,
            "yes_price_dollars": yes_price, "no_price_dollars": no_price,
            "fee_cost": fee, "action": action}


class Client:
    def __init__(self, fills, settlements):
        self.fills = fills
        self.settlements = settlements
        self.calls = []

    def get(self, path, *, params=None):
        self.calls.append((path, params))
        if path == "/portfolio/settlements":
            return {"settlements": self.settlements, "cursor": ""}
        if path == "/portfolio/fills":
            return {"fills": self.fills, "cursor": ""}
        raise AssertionError(path)


def settlement(result):
    return {"ticker": TICKER, "market_result": result, "settled_time": "2026-10-31T13:00:00Z"}


def test_winning_auto_order_payout_and_actual_fee_inclusive_profit():
    rows = [entry([stage("FINAL_BUY", "UP", "0.11", "order1")])]
    client = Client([fill("order1", "UP", "0.11", "0.7300", "0.2700", "0.0100")],
                    [settlement("yes")])
    output = enrich_history(rows, client)[0]
    assert output["max_payout_dollars"] == "0.1100"
    assert output["realized_pnl_dollars"] == "0.0197"
    assert output["pnl_status"] == "SETTLED"
    assert any(path == "/portfolio/fills" for path, _ in client.calls)


def test_losing_auto_order_is_negative_not_zero():
    rows = [entry([stage("FINAL_BUY", "UP", "0.11", "order1")])]
    client = Client([fill("order1", "UP", "0.11", "0.9000", "0.1000", "0.0010")],
                    [settlement("no")])
    output = enrich_history(rows, client)[0]
    assert output["realized_pnl_dollars"] == "-0.10"


def test_reversal_includes_both_buys_sell_and_all_fees():
    rows = [entry([stage("EARLY_BUY", "UP", "0.10", "first"),
                   stage("REVERSAL_SELL", "UP", "0.10", "second"),
                   stage("FINAL_BUY", "DOWN", "0.11", "third")])]
    client = Client([fill("first", "UP", "0.10", "0.5000", "0.5000", "0.0100"),
                     fill("second", "UP", "0.10", "0.6000", "0.4000", "0.0050", "sell"),
                     fill("third", "DOWN", "0.11", "0.3000", "0.7000", "0.0050")],
                    [settlement("no")])
    output = enrich_history(rows, client)[0]
    # -0.060 + 0.055 - 0.082 + 0.110 = +0.023
    assert output["max_payout_dollars"] == "0.1100"
    assert output["realized_pnl_dollars"] == "0.0230"


def test_unsettled_shows_potential_payout_but_not_realized_profit():
    rows = [entry([stage("FINAL_BUY", "UP", "0.11", "order1")])]
    output = enrich_history(rows, Client([], []))[0]
    assert output["max_payout_dollars"] == "0.1100"
    assert output["realized_pnl_dollars"] is None
    assert output["pnl_status"] == "PENDING"


def test_missing_or_incomplete_fills_cannot_claim_exact_profit():
    rows = [entry([stage("FINAL_BUY", "UP", "0.11", "order1")])]
    output = enrich_history(rows, Client([fill("order1", "UP", "0.10", "0.7300", "0.2700")],
                                         [settlement("yes")]))[0]
    assert output["realized_pnl_dollars"] is None
    assert output["pnl_status"] == "UNAVAILABLE"


def test_other_manual_orders_in_same_market_cannot_be_attributed_to_auto():
    rows = [entry([stage("FINAL_BUY", "UP", "0.11", "order1")])]
    client = Client([fill("order1", "UP", "0.11", "0.7300", "0.2700"),
                     fill("manual2", "UP", "0.03", "0.7300", "0.2700")],
                    [settlement("yes")])
    output = enrich_history(rows, client)[0]
    assert output["realized_pnl_dollars"] is None
    assert output["pnl_status"] == "MIXED"


def test_unknown_submission_never_claims_payout_or_profit():
    rows = [entry([stage("FINAL_BUY", "UP", None, None, "UNKNOWN")])]
    client = Client([], [settlement("yes")])
    output = enrich_history(rows, client)[0]
    assert output["max_payout_dollars"] is None
    assert output["realized_pnl_dollars"] is None
    assert output["pnl_status"] == "UNAVAILABLE"
    assert not client.calls


def test_failed_exchange_fetch_does_not_fabricate_or_hide_history():
    class Offline:
        def get(self, *_args, **_kwargs):
            raise OSError("Kalshi unreachable")
    output = enrich_history([entry([stage("FINAL_BUY", "UP", "0.11", "order1")])],
                            Offline())[0]
    assert output["max_payout_dollars"] == "0.1100"
    assert output["realized_pnl_dollars"] is None
    assert output["pnl_status"] == "UNAVAILABLE"


def test_missing_marked_price_or_fee_refuses_exact_pnl():
    row = fill("order1", "UP", "0.11", "0.7300", "0.2700")
    del row["fee_cost"]
    output = enrich_history([entry([stage("FINAL_BUY", "UP", "0.11", "order1")])],
                            Client([row], [settlement("yes")]))[0]
    assert output["realized_pnl_dollars"] is None


def test_ui_shows_signed_green_max_payout_and_signed_realized_pnl():
    from kalshi_edge.bootstrap.dashboard_ui import PAGE
    from kalshi_edge.bootstrap.manual_trade_ui import integrate_manual_trade
    page = integrate_manual_trade(PAGE)
    assert "entry.max_payout_dollars" in page
    assert "entry.realized_pnl_dollars" in page
    assert "mt-auto-pnl-gain" in page
    assert "mt-auto-pnl-loss" in page
    assert 'shown("manual-payout","+"+money(' in page
    assert "Max payout" in page
