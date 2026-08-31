from __future__ import annotations

import pytest

from kalshi_edge.bootstrap.labels import LabelNormalizationError, normalize_market_label


def _payload(*, result: str, settlement: str, floor_strike: float = 100000.0) -> dict:
    return {
        "market": {
            "ticker": "KXBTC15M-TEST",
            "event_ticker": "KXBTC15M-EVENT",
            "market_type": "binary",
            "open_time": "2026-08-01T12:00:00Z",
            "close_time": "2026-08-01T12:15:00Z",
            "settlement_ts": "2026-08-01T12:16:00Z",
            "status": "finalized",
            "result": result,
            "settlement_value_dollars": settlement,
            "strike_type": "greater",
            "floor_strike": floor_strike,
            "rules_primary": "Resolves Yes if the final BTC reference value is above the target price.",
            "rules_secondary": "Reference source: CF Benchmarks.",
            "is_provisional": False,
        }
    }


def test_normalize_yes_label_preserves_exact_settlement_and_contract_semantics() -> None:
    label = normalize_market_label(_payload(result="yes", settlement="100100.2500"))
    assert label.ticker == "KXBTC15M-TEST"
    assert label.strike == 100000.0
    assert label.result == "yes"
    assert label.settlement_value == 100100.25
    assert label.open_ts_ns == 1785585600 * 1_000_000_000
    assert label.close_ts_ns == 1785586500 * 1_000_000_000
    assert label.strike_type == "greater"
    assert label.yes_is_above is True


def test_normalize_no_label_preserves_official_result() -> None:
    label = normalize_market_label(_payload(result="no", settlement="99950.0000"))
    assert label.result == "no"
    assert label.settlement_value == 99950.0
    assert label.yes_is_above is True


@pytest.mark.parametrize(
    "mutation",
    [
        lambda market: market.update(result=""),
        lambda market: market.update(settlement_value_dollars=None),
        lambda market: market.update(floor_strike=None),
        lambda market: market.update(is_provisional=True),
        lambda market: market.update(strike_type="custom"),
    ],
)
def test_normalize_rejects_unresolved_or_ambiguous_market(mutation) -> None:
    payload = _payload(result="yes", settlement="100100.0000")
    mutation(payload["market"])
    with pytest.raises(LabelNormalizationError):
        normalize_market_label(payload)
