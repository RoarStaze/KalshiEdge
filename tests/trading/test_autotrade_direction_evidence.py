"""Read-only evidence for the unresolved, real-world DOWN fill mismatches.

All Kalshi responses are synthetic. No credentials, order actions, or private
order/fill identifiers may enter diagnostic reports.
"""
from __future__ import annotations

import json

from kalshi_edge.trading.autotrade_history import AutoTradeHistory
import kalshi_edge.trading.autotrade_pnl as pnl
from kalshi_edge.trading.autotrade_pnl import enrich_history
from kalshi_edge.trading.autotrade_profit_audit import audit_autotrade_profit

TICKER = "KXBTC15M-DIRECTION-EVIDENCE"
SECRET_ORDER = "sensitive-exchange-order-id"


def _row():
    return {
        "ticker": TICKER,
        "status": "FILLED",
        "outcome": "DOWN",
        "stages": [{
            "stage": "FINAL_BUY",
            "status": "FILLED",
            "outcome": "DOWN",
            "order_id": SECRET_ORDER,
            "fill_count": "0.10",
        }],
    }


def _fill(**changes):
    fill = {
        "ticker": TICKER,
        "order_id": SECRET_ORDER,
        "fill_id": "sensitive-fill-id",
        "outcome_side": "yes",
        "book_side": "bid",
        "side": "yes",
        "action": "buy",
        "yes_price_dollars": "0.3000",
        "no_price_dollars": "0.7000",
        "count_fp": "0.10",
        "fee_cost": "0.001000",
    }
    fill.update(changes)
    return fill


class FakeReadOnlyAccount:
    def __init__(self, fill):
        self.fill = fill
        self.called = []

    def get(self, path, *, params=None):
        self.called.append(path)
        if path == "/portfolio/settlements":
            return {"settlements": [{
                "ticker": TICKER,
                "market_result": "no",
                "yes_count_fp": "0.00",
                "no_count_fp": "0.10",
            }], "cursor": ""}
        if path == "/portfolio/fills":
            return {"fills": [self.fill], "cursor": ""}
        raise AssertionError("Unexpected endpoint " + path)


def test_failed_down_direction_exposes_both_enum_schemes_not_private_ids():
    row = enrich_history(
        [_row()],
        FakeReadOnlyAccount(_fill()),
        require_settlement_counts=True,
    )[0]
    assert row["pnl_reason"] == "FILL_DIRECTION_CONFLICT"
    assert row["realized_pnl_dollars"] is None
    assert row["direction_evidence"] == [{
        "journal_action": "BUY",
        "journal_outcome": "DOWN",
        "outcome_side": "yes",
        "book_side": "bid",
        "legacy_side": "yes",
        "legacy_action": "buy",
    }]
    assert SECRET_ORDER not in json.dumps(row["direction_evidence"])


def test_canonical_down_but_conflicting_legacy_is_explained_not_accepted():
    row = enrich_history(
        [_row()],
        FakeReadOnlyAccount(_fill(
            outcome_side="no", book_side="ask",
            side="yes", action="buy",
        )),
        require_settlement_counts=True,
    )[0]
    assert row["pnl_reason"] == "FILL_DIRECTION_CONFLICT"
    assert row["realized_pnl_dollars"] is None
    assert row["direction_evidence"][0]["outcome_side"] == "no"
    assert row["direction_evidence"][0]["book_side"] == "ask"
    assert row["direction_evidence"][0]["legacy_action"] == "buy"
    assert row["direction_evidence"][0]["legacy_side"] == "yes"


def test_evidence_is_allowlisted_capped_and_omits_secrets():
    stage = [(SECRET_ORDER, "BUY", "DOWN", "0.10")]
    entries = [_fill(fill_id=f"sensitive-fill-{i}") for i in range(15)]
    entries[0]["side"] = "user-secret:9381"
    entries[1]["book_side"] = "unexpected-private-value"
    evidence = pnl._safe_direction_evidence(stage, entries)
    assert len(evidence) == 8
    assert evidence[0]["legacy_side"] == "INVALID"
    assert evidence[1]["book_side"] == "INVALID"
    output = json.dumps(evidence)
    assert "sensitive-" not in output
    assert "user-secret" not in output
    assert "unexpected-private" not in output
    assert all(
        set(item) == {
            "journal_action", "journal_outcome",
            "outcome_side", "book_side", "legacy_side", "legacy_action",
        }
        for item in evidence
    )


def test_default_audit_stays_summary_only_and_explicit_details_include_evidence(tmp_path):
    root = tmp_path / "bootstrap"
    store = AutoTradeHistory(root / "data" / "autotrade_history.sqlite3")
    store.record_attempt(
        ticker=TICKER, attempted_at_ns=1_900_000_000_000_000_000,
        outcome="DOWN", amount="0.10", confidence=0.83,
    )
    store.record_stage_attempt(
        ticker=TICKER, stage="FINAL_BUY",
        attempted_at_ns=1_900_000_000_000_000_000,
        outcome="DOWN", amount="0.10", confidence=0.83,
    )
    store.record_stage_result(
        ticker=TICKER, stage="FINAL_BUY", status="FILLED",
        order_id=SECRET_ORDER, fill_count="0.10",
    )
    store.record_result(
        ticker=TICKER, status="FILLED",
        order_id=SECRET_ORDER, fill_count="0.10",
    )

    summary = audit_autotrade_profit(root, client=FakeReadOnlyAccount(_fill()))
    assert summary["verified_settled_markets"] == 0
    assert summary["verified_net_pnl_dollars"] is None
    assert summary["evidence_reason_counts"]["FILL_DIRECTION_CONFLICT"] == 1
    assert "market_details" not in summary
    assert "direction_evidence" not in json.dumps(summary)

    detailed = audit_autotrade_profit(
        root, client=FakeReadOnlyAccount(_fill()), details=True,
    )
    market = detailed["market_details"][0]
    assert market["pnl_reason"] == "FILL_DIRECTION_CONFLICT"
    assert market["realized_pnl_dollars"] is None
    assert market["direction_evidence"][0]["journal_outcome"] == "DOWN"
    assert market["direction_evidence"][0]["outcome_side"] == "yes"
    assert "sensitive" not in json.dumps(detailed)
    assert "order_id" not in json.dumps(detailed)
    assert "client_order_id" not in json.dumps(detailed)
