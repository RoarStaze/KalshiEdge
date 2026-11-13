"""Read-only normalization for Kalshi V2 YES-book fills and legacy fills.

An economic BUY DOWN is equivalent to SELL YES. Kalshi V2 uses a YES
order-book BID/ASK for that action, while older payloads can use
action=buy/side=no or action=sell/side=yes. Both representations must
describe the SAME signed exposure as the immutable local AutoTrade
journal, or the caller refuses to report realized P/L.

Never constructs a fill, defaults a fee, or submits an order.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

ONE=Decimal("1")
PRICE_TOLERANCE=Decimal("0.000001")


def _money(value:Any) -> Decimal:
    if value is None or isinstance(value,bool):
        raise ValueError("Missing, invalid or boolean exchange price")
    try:
        number=Decimal(str(value))
    except (ValueError,TypeError,InvalidOperation) as exc:
        raise ValueError("Invalid exchange price") from exc
    if not number.is_finite() or not (0<=number<=ONE):
        raise ValueError("Exchange price outside [0,1]")
    return number


def _expected_yes_exposure(action:str,outcome:str) -> int:
    """Net YES equivalent of journaled UP/DOWN economic transaction."""
    if action not in ("BUY","SELL") or outcome not in ("UP","DOWN"):
        raise ValueError("Invalid journal direction")
    return 1 if (action,outcome) in (("BUY","UP"),("SELL","DOWN")) else -1


def _scheme_exposure(side:str|None, direction:str|None, *,
                     flavor:str) -> int|None:
    if side is None and direction is None:
        return None
    if flavor=="legacy":
        if side not in ("yes","no") or direction not in ("buy","sell"):
            raise ValueError("Incomplete or invalid legacy fill identity")
        return 1 if (side,direction) in (("yes","buy"),("no","sell")) else -1
    if flavor=="v2":
        if side not in ("yes","no") or direction not in ("bid","ask"):
            raise ValueError("Incomplete or invalid V2 book identity")
        # Official Kalshi V2 mapping: bid == yes, ask == no, always.
        # The former code incorrectly assigned +1 to (no, ask), which
        # caused valid journaled BUY DOWN fills to be rejected.
        # Do not accept contradictory pairs such as yes/ask or no/bid.
        if (side, direction) not in (("yes", "bid"), ("no", "ask")):
            raise ValueError("Conflicting V2 outcome/book sides")
        return 1 if side == "yes" else -1
    raise ValueError("Unsupported fill identity scheme")


def validated_fill_unit_price(
    fill:dict[str,Any], *, ticker:str, action:str, outcome:str,
) -> Decimal:
    """Verify market, action and binary price; return the economic unit cost.

    `outcome_side`/`book_side` are first-party Kalshi V2 fields.
    Legacy `side`/`action` are accepted for archived fill responses.
    When both representations exist they must be economically equivalent.
    One real YES/NO fill price is sufficient to infer the other by binary
    complement. Inconsistent paired prices remain unavailable.
    """
    if not isinstance(fill,dict) or not isinstance(ticker,str) or not ticker:
        raise ValueError("Missing exchange market")
    reported=(fill.get("ticker"),fill.get("market_ticker"))
    if not any(x is not None for x in reported):
        raise ValueError("No exchange ticker")
    if any(x is not None and x!=ticker for x in reported):
        raise ValueError("Exchange fill ticker does not match AutoTrade")

    expected=_expected_yes_exposure(action,outcome)
    scheme_v2=_scheme_exposure(
        fill.get("outcome_side"),fill.get("book_side"),flavor="v2",
    )
    scheme_legacy=_scheme_exposure(
        fill.get("side"),fill.get("action"),flavor="legacy",
    )
    if scheme_v2 is None and scheme_legacy is None:
        raise ValueError("No verifiable exchange fill action/side")
    if any(x is not None and x!=expected for x in (scheme_v2,scheme_legacy)):
        raise ValueError("Exchange fill conflicts with journaled direction")

    raw_yes=fill.get("yes_price_dollars")
    raw_no=fill.get("no_price_dollars")
    if raw_yes is None and raw_no is None:
        raise ValueError("Missing actual exchange fill price")
    yes=_money(raw_yes) if raw_yes is not None else None
    no=_money(raw_no) if raw_no is not None else None
    if yes is None:
        yes=ONE-no
    elif no is None:
        no=ONE-yes
    elif abs(yes+no-ONE)>PRICE_TOLERANCE:
        raise ValueError("Exchange YES/NO fill prices are inconsistent")
    return yes if outcome=="UP" else no
