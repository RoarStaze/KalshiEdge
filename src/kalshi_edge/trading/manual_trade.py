"""User-initiated, one-order-at-a-time Kalshi event-market trading.

No background order loop. Quotes are read from the current live market;
one browser click consumes a short-lived server quote and sends at most
one signed, immediate-or-cancel order. Unknown outcomes are not retried.
"""
from __future__ import annotations

import base64
from decimal import Decimal, ROUND_FLOOR
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time
import uuid

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

from .risk import decimal, taker_fee_reserve
from .paper import PublicMarketClient


API_ROOT = "https://external-api.kalshi.com"
PREFIX = "/trade-api/v2"
STEP = Decimal("0.01")
ONE = Decimal("1")
MAX_REQUEST_DOLLARS = Decimal("100000")
QUOTE_LIFETIME_SECONDS = 9


class TradingError(ValueError):
    pass


def _money(value):
    return format(value.quantize(Decimal("0.0001")), ".4f")


def _top(rows):
    if not isinstance(rows, list):
        raise TradingError("Market orderbook missing bid levels")
    valid = []
    for row in rows:
        try:
            p, n = decimal(row[0]), decimal(row[1])
            if 0 < p < 1 and n >= STEP:
                valid.append((p, n))
        except (IndexError, TypeError, ValueError):
            continue
    if not valid:
        raise TradingError("No executable liquidity at the best bid")
    return max(valid, key=lambda item: item[0])


def quote_from_book(book: dict, action: str, outcome: str) -> dict:
    """Order V2 always prices YES; UP=YES, DOWN=NO."""
    if action not in ("BUY", "SELL") or outcome not in ("UP", "DOWN"):
        raise TradingError("Invalid order direction")
    fp = book.get("orderbook_fp")
    if not isinstance(fp, dict):
        raise TradingError("Fixed-point orderbook unavailable")
    if (action, outcome) in (("BUY", "UP"), ("SELL", "DOWN")):
        no_bid, quantity = _top(fp.get("no_dollars"))
        yes_price = ONE - no_bid
    else:
        yes_price, quantity = _top(fp.get("yes_dollars"))
    book_side = "bid" if (action, outcome) in (("BUY", "UP"), ("SELL", "DOWN")) else "ask"
    unit_cash = yes_price if outcome == "UP" else ONE - yes_price
    return {"yes_price": yes_price, "unit_cash": unit_cash, "available": quantity, "book_side": book_side}


class KalshiOrderClient:
    """Signed authenticated Kalshi GET requests and explicit one-shot order POST."""
    def __init__(self, vault, *, transport=None):
        self.vault = vault
        self.http = httpx.Client(base_url=API_ROOT, timeout=6, transport=transport,
                                 follow_redirects=False, trust_env=False)

    def _request(self, method, path, *, params=None, body=None):
        if not path.startswith("/portfolio/") or "?" in path:
            raise TradingError("Invalid authenticated path")
        absolute = PREFIX + path
        api_key, pem = self.vault.load()
        key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
        timestamp = str(time.time_ns() // 1_000_000)
        message = (timestamp + method + absolute).encode("utf-8")
        if isinstance(key, ed25519.Ed25519PrivateKey):
            signature = key.sign(message)
        elif isinstance(key, rsa.RSAPrivateKey):
            signature = key.sign(message, padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                                                       salt_length=hashes.SHA256().digest_size),
                                 hashes.SHA256())
        else:
            raise TradingError("Unsupported Kalshi private key")
        headers = {
            "KALSHI-ACCESS-KEY": api_key,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("ascii"),
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
        }
        response = self.http.request(method, absolute, params=params, json=body, headers=headers)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise TradingError("Invalid exchange response")
        return data

    def get(self, path, *, params=None):
        return self._request("GET", path, params=params)

    def send_order(self, request: dict):
        required = {"ticker", "client_order_id", "side", "count", "price"}
        if set(request) != required:
            raise TradingError("Invalid signed order fields")
        if not request["ticker"].startswith("KXBTC15M-"):
            raise TradingError("Non-BTC market rejected")
        payload = {
            **request,
            "time_in_force": "immediate_or_cancel",
            "self_trade_prevention_type": "taker_at_cross",
            "post_only": False,
            "cancel_order_on_pause": True,
            "reduce_only": False,
            "exchange_index": -1,
        }
        return self._request("POST", "/portfolio/events/orders", body=payload)

    def close(self):
        self.http.close()


class ManualTrader:
    """Quotes, holds and consumes a short-lived manual order request once."""
    def __init__(self, root: Path, *, vault=None, client=None, market_client=None):
        from .vault import CredentialVault
        self.root = Path(root)
        self.vault = vault if vault is not None else CredentialVault()
        self.client = client if client is not None else KalshiOrderClient(self.vault)
        self.market_client = market_client if market_client is not None else PublicMarketClient()
        self._pending = {}
        self._lock = threading.Lock()

    def _account(self, ticker: str):
        balance_raw = self.client.get("/portfolio/balance")
        balance = decimal(balance_raw.get("balance_dollars",
                              Decimal(str(balance_raw["balance"])) / 100
                              if "balance" in balance_raw else None))
        positions = self.client.get("/portfolio/positions", params={"ticker": ticker, "limit": 10})
        rows = positions.get("market_positions")
        if not isinstance(rows, list):
            raise TradingError("Kalshi position response unavailable")
        position = Decimal("0")
        for row in rows:
            if row.get("ticker") == ticker:
                position += decimal(row["position_fp"])
        return balance, position

    def size_order(self, balance: Decimal, action: str, outcome: str, units: str,
                   amount: Decimal, position: Decimal, book: dict, market_payload: dict):
        market = market_payload.get("market")
        if not isinstance(market, dict) or market.get("status") not in ("active", "open"):
            raise TradingError("Market is not open")
        price = quote_from_book(book, action, outcome)
        value = decimal(amount)
        if not 0 < value <= MAX_REQUEST_DOLLARS:
            raise TradingError("Enter a positive amount under $100,000")
        if units not in ("DOLLARS", "SHARES"):
            raise TradingError("Select DOLLARS or SHARES")
        # Absent capability is NOT evidence that fractional contracts are supported.
        step = STEP if market.get("fractional_trading_enabled") is True else ONE
        per = price["unit_cash"]
        if per <= 0:
            raise TradingError("No executable quote")
        if action == "SELL":
            owned = max(Decimal("0"), position if outcome == "UP" else -position)
            if owned < step:
                raise TradingError("No available position to sell for the selected side")
        else:
            owned = balance
        desired = (value / per) if units == "DOLLARS" else value
        count = min(desired, price["available"], owned if action == "SELL" else desired)
        count = (count / step).to_integral_value(rounding=ROUND_FLOOR) * step
        if count < step:
            raise TradingError("Amount is smaller than one executable share increment")
        fee = taker_fee_reserve(per, count)
        if action == "BUY":
            while count >= step and (count * per + fee > min(balance, value if units == "DOLLARS" else balance)):
                count -= step
                fee = taker_fee_reserve(per, count) if count >= step else Decimal("0")
        if count < step:
            raise TradingError("Insufficient cash for the requested size including fee reserve")
        # Fees for SELL are estimated; realized amounts come from the exchange.
        gross = count * per
        effective = gross + fee if action == "BUY" else max(Decimal("0"), gross - fee)
        return {
            "side": price["book_side"], "action": action, "outcome": outcome,
            "price": _money(price["yes_price"]), "unit_price": _money(per),
            "count": _money(count), "fee_estimate": _money(fee),
            "amount_estimate": _money(effective),
            "maximum_payout": _money(count if action == "BUY" else Decimal("0")),
            "available_balance": _money(balance),
            "position_yes": _money(position),
        }

    def _prepare(self, ticker, action, outcome, units, amount):
        if not ticker.startswith("KXBTC15M-") or not ticker.replace("-", "").isalnum():
            raise TradingError("Unsupported market ticker")
        market, book = self.market_client.quote(ticker)
        if market.get("market", {}).get("ticker") != ticker:
            raise TradingError("Market ticker changed")
        close_raw = market["market"].get("close_time")
        try:
            close = datetime.fromisoformat(close_raw.replace("Z", "+00:00"))
            if close.tzinfo is None or close.timestamp() <= time.time() + 1:
                raise ValueError("Market closing or closed")
        except (TypeError, AttributeError, ValueError) as exc:
            raise TradingError("Market is closing or has no valid close time") from exc
        balance, position = self._account(ticker)
        plan = self.size_order(balance, action, outcome, units, decimal(amount),
                               position, book, market)
        plan["ticker"] = ticker
        return plan

    def preview(self, ticker, action, outcome, units, amount):
        plan = self._prepare(ticker, action, outcome, units, amount)
        preview_id = str(uuid.uuid4())
        with self._lock:
            now = time.monotonic()
            self._pending = {k: v for k, v in self._pending.items()
                             if now - v["at"] < QUOTE_LIFETIME_SECONDS}
            self._pending[preview_id] = {
                "at": now, "ticker": ticker, "action": action,
                "outcome": outcome, "units": units, "amount": str(amount),
                "plan": plan,
            }
        return {"preview_id": preview_id, "expires_in_seconds": QUOTE_LIFETIME_SECONDS, **plan}

    def place(self, preview_id, current_ticker):
        # Atomically consume BEFORE doing anything that may submit an order.
        with self._lock:
            data = self._pending.pop(preview_id, None)
        if data is None or time.monotonic() - data["at"] > QUOTE_LIFETIME_SECONDS:
            raise TradingError("Quote expired; refresh and click again")
        if current_ticker != data["ticker"]:
            raise TradingError("Market changed; refresh quote")
        old = data["plan"]
        fresh = self._prepare(data["ticker"], data["action"], data["outcome"],
                              data["units"], data["amount"])
        if fresh["side"] != old["side"] or Decimal(fresh["count"]) < Decimal(old["count"]):
            raise TradingError("Position, balance or liquidity changed; quote again")
        if data["action"] == "BUY" and Decimal(fresh["unit_price"]) > Decimal(old["unit_price"]):
            raise TradingError("Price moved against you; quote again")
        if data["action"] == "SELL" and Decimal(fresh["unit_price"]) < Decimal(old["unit_price"]):
            raise TradingError("Sell price moved against you; quote again")
        # A short-lived, explicitly clicked action makes ONE signed POST.
        order = {
            "ticker": data["ticker"], "client_order_id": str(uuid.uuid4()),
            "side": old["side"], "price": old["price"], "count": old["count"],
        }
        # The exchange enforces IOC; no unfilled shares remain resting.
        result = self.client.send_order(order)
        order_id = result.get("order_id")
        if not isinstance(order_id, str) or not order_id:
            raise TradingError("Exchange response ambiguous; check Kalshi orders before retry")
        fills = decimal(result.get("fill_count"))
        if fills < 0 or fills > Decimal(old["count"]):
            raise TradingError("Exchange-reported fill quantity invalid")
        average = result.get("average_fill_price")
        fees = result.get("average_fee_paid")
        return {
            "status": "FILLED" if fills == Decimal(old["count"]) else "PARTIAL" if fills > 0 else "NO_FILL",
            "order_id": order_id, "ticker": data["ticker"],
            "action": data["action"], "outcome": data["outcome"],
            "fill_count": _money(fills),
            "average_yes_price": average if fills else None,
            "average_fee_paid": fees if fills else None,
            "requested_count": old["count"],
            "estimated_per_share": old["unit_price"],
        }

    def close(self):
        self.client.close()
        self.market_client.close()
