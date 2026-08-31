from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from hashlib import sha256
import json
from typing import Iterable


@dataclass
class OrderBook:
    market_ticker: str
    yes: dict[Decimal, Decimal] = field(default_factory=dict)
    no: dict[Decimal, Decimal] = field(default_factory=dict)

    @staticmethod
    def _load_side(rows: Iterable[Iterable[str]]) -> dict[Decimal, Decimal]:
        result: dict[Decimal, Decimal] = {}
        for price, count in rows:
            quantity = Decimal(count)
            if quantity > 0:
                result[Decimal(price)] = quantity
        return result

    def apply_snapshot(self, *, yes_dollars_fp: list[list[str]], no_dollars_fp: list[list[str]]) -> None:
        self.yes = self._load_side(yes_dollars_fp)
        self.no = self._load_side(no_dollars_fp)

    def apply_delta(self, *, side: str, price_dollars: str, delta_fp: str) -> None:
        if side not in {"yes", "no"}:
            raise ValueError(f"unsupported side: {side}")
        ladder = self.yes if side == "yes" else self.no
        price = Decimal(price_dollars)
        new_qty = ladder.get(price, Decimal("0")) + Decimal(delta_fp)
        if new_qty < 0:
            raise ValueError("orderbook delta would create negative quantity")
        if new_qty == 0:
            ladder.pop(price, None)
        else:
            ladder[price] = new_qty

    @property
    def best_yes_bid(self) -> Decimal | None:
        return max(self.yes, default=None)

    @property
    def best_no_bid(self) -> Decimal | None:
        return max(self.no, default=None)

    @property
    def best_yes_ask(self) -> Decimal | None:
        return None if self.best_no_bid is None else Decimal("1.0000") - self.best_no_bid

    @property
    def best_no_ask(self) -> Decimal | None:
        return None if self.best_yes_bid is None else Decimal("1.0000") - self.best_yes_bid

    @property
    def yes_spread(self) -> Decimal | None:
        if self.best_yes_bid is None or self.best_yes_ask is None:
            return None
        return self.best_yes_ask - self.best_yes_bid

    def state_hash(self) -> str:
        canonical = {
            "market_ticker": self.market_ticker,
            "yes": [[str(p), str(self.yes[p])] for p in sorted(self.yes)],
            "no": [[str(p), str(self.no[p])] for p in sorted(self.no)],
        }
        blob = json.dumps(canonical, separators=(",", ":"), sort_keys=True).encode()
        return sha256(blob).hexdigest()
