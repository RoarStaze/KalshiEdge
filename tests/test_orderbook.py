from decimal import Decimal

from kalshi_edge.orderbook import OrderBook


def test_orderbook_applies_snapshot_and_delta_and_derives_implied_asks() -> None:
    book = OrderBook("KXBTC15M-X")
    book.apply_snapshot(
        yes_dollars_fp=[["0.4200", "13.00"], ["0.4000", "5.00"]],
        no_dollars_fp=[["0.5600", "17.00"], ["0.5000", "2.00"]],
    )

    assert book.best_yes_bid == Decimal("0.4200")
    assert book.best_no_bid == Decimal("0.5600")
    assert book.best_yes_ask == Decimal("0.4400")
    assert book.yes_spread == Decimal("0.0200")

    book.apply_delta(side="yes", price_dollars="0.4300", delta_fp="10.00")
    assert book.best_yes_bid == Decimal("0.4300")
    book.apply_delta(side="yes", price_dollars="0.4300", delta_fp="-10.00")
    assert book.best_yes_bid == Decimal("0.4200")
