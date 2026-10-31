"""Explicit-per-click manual orders for the current KXBTC15M Kalshi market.

No autonomous orders, timers, background trading, or predictions as execution instructions.
Each browser click carries a unique idempotency key and creates one IOC order only.
"""
from __future__ import annotations

import base64
from decimal import Decimal, ROUND_FLOOR
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
from urllib.parse import urlencode

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

from .risk import decimal, taker_fee_reserve
from .paper import PublicMarketClient

API_ROOT = "https://external-api.kalshi.com"
PREFIX = "/trade-api/v2"
TICKER_RE = re.compile(r"^KXBTC15M-[A-Z0-9-]+$")
REQUEST_RE = re.compile(r"^[a-f0-9-]{36}$")


class ManualTradeError(ValueError):
    """User-facing validation failure, never an implicit trading instruction."""


def _price(value):
    n = decimal(value)
    if not Decimal("0") < n < Decimal("1"):
        raise ManualTradeError("No executable price")
    return n


def _level(book: dict, key: str):
    fp = book.get("orderbook_fp")
    levels = fp.get(key) if isinstance(fp, dict) else None
    if not isinstance(levels, list):
        raise ManualTradeError("No executable orderbook")
    found = []
    for item in levels:
        try:
            p, qty = _price(item[0]), decimal(item[1])
        except (IndexError, TypeError, ValueError):
            continue
        if qty > 0:
            found.append((p, qty))
    if not found:
        return None
    return max(found, key=lambda x: x[0])


def quote_from_orderbook(book: dict) -> dict:
    """Best quoted bid/ask; NO derived as 1 - YES and not interchangeable with YES bid."""
    y = _level(book, "yes_dollars")
    n = _level(book, "no_dollars")
    def entry(price: Decimal | None, qty: Decimal | None, v2: str):
        return None if price is None else {
            "price": format(price, ".4f"),
            "available_shares": str(qty), "book_side": v2,
        }
    return {
        "yes": {
            "buy": entry(1-n[0], n[1], "bid") if n else None,
            "sell": entry(y[0], y[1], "ask") if y else None,
        },
        "no": {
            "buy": entry(1-y[0], y[1], "ask") if y else None,
            "sell": entry(n[0], n[1], "bid") if n else None,
        },
    }


def plan_manual_order(quotes: dict, action: str, side: str, mode: str, amount: str,
                      balance: Decimal, position: Decimal, *, fractional: bool) -> dict:
    if action not in ("buy", "sell") or side not in ("yes", "no") or mode not in ("dollars", "shares"):
        raise ManualTradeError("Invalid trade selection")
    value = decimal(amount)
    if value <= 0 or value > Decimal("1000000"):
        raise ManualTradeError("Amount must be between 0 and 1,000,000")
    quote = quotes.get(side, {}).get(action)
    if not isinstance(quote, dict):
        raise ManualTradeError("No executable liquidity on the selected side")
    price, available = _price(quote["price"]), decimal(quote["available_shares"])
    step = Decimal(".01") if fractional else Decimal("1")
    holdings = max(Decimal("0"), position if side == "yes" else -position)
    if action == "sell" and holdings < step:
        raise ManualTradeError("No matching position to sell")
    if mode == "shares":
        requested = value
        if requested != (requested / step).to_integral_value() * step:
            raise ManualTradeError("Quantity is not supported by this market")
        count = requested
    else:
        # For buys, dollars are an all-in maximum. For sells, dollars cap the
        # approximate gross proceeds; the final payout is based on actual fills.
        maximum = value / price
        count = maximum.to_integral_value(rounding=ROUND_FLOOR) * step if step == 1 else (
            (maximum / step).to_integral_value(rounding=ROUND_FLOOR) * step
        )
    count = min(count, (available / step).to_integral_value(rounding=ROUND_FLOOR) * step)
    if action == "sell":
        count = min(count, (holdings / step).to_integral_value(rounding=ROUND_FLOOR) * step)
    if count < step:
        raise ManualTradeError("Amount is smaller than the minimum available contract quantity")
    if mode == "shares" and count != value:
        raise ManualTradeError("Not enough available shares or liquidity")
    if action == "buy":
        budget = min(decimal(balance), value if mode == "dollars" else decimal(balance))
        while count >= step and price*count + taker_fee_reserve(price,count) > budget:
            count -= step
        if count < step:
            raise ManualTradeError("Insufficient available balance including estimated fees")
        if mode == "shares" and count != value:
            raise ManualTradeError("Insufficient available balance including estimated fees")
    fee = taker_fee_reserve(price, count)
    total = price*count+fee
    result = {
        "action": action, "side": side, "book_side": quote["book_side"],
        "count": format(count, ".2f"), "outcome_price": format(price, ".4f"),
        "v2_yes_price": format(price if quote["book_side"] == "bid" and side == "yes"
                             or quote["book_side"] == "ask" and side == "yes"
                             else Decimal("1")-price, ".4f"),
        "estimated_fee_reserve": format(fee, ".4f"),
        "estimated_total": format(total, ".4f"),
        "reduce_only": action == "sell",
    }
    # All V2 quotes use YES book price: bid YES is YES price, ask YES is
    # YES price. NO BUY uses ask YES (YES bid); NO SELL uses bid YES (YES ask).
    if side == "no":
        result["v2_yes_price"] = format(Decimal("1")-price, ".4f")
    return result


class KalshiManualClient:
    """V2 authenticated exchange client; sign path without query parameters."""
    def __init__(self, vault, *, transport=None):
        self.vault = vault
        self.http = httpx.Client(base_url=API_ROOT, timeout=7, trust_env=False,
                                 follow_redirects=False, transport=transport)

    def request(self, method: str, path: str, *, params=None, data=None) -> dict:
        if not path.startswith(PREFIX + "/") or "?" in path:
            raise ManualTradeError("Invalid authenticated API path")
        key_id, pem = self.vault.load()
        key = serialization.load_pem_private_key(pem.encode(), password=None)
        ts = str(time.time_ns() // 1_000_000)
        message = (ts + method.upper() + path).encode()
        if isinstance(key, ed25519.Ed25519PrivateKey):
            signature = key.sign(message)
        elif isinstance(key, rsa.RSAPrivateKey):
            signature = key.sign(message,
                padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                            salt_length=padding.PSS.DIGEST_LENGTH),
                hashes.SHA256())
        else:
            raise ManualTradeError("Unsupported Kalshi key type")
        headers = {
            "KALSHI-ACCESS-KEY": key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
        }
        response = self.http.request(method, path, params=params, json=data, headers=headers)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ManualTradeError("Unexpected Kalshi response")
        return payload

    def balance(self):
        result = self.request("GET", PREFIX+"/portfolio/balance")
        return decimal(result["balance_dollars"]) if "balance_dollars" in result else decimal(result["balance"])/100

    def position(self, ticker):
        result = self.request("GET", PREFIX+"/portfolio/positions", params={"ticker":ticker})
        positions = result.get("market_positions", [])
        if not isinstance(positions, list):
            raise ManualTradeError("Unexpected portfolio positions")
        return sum((decimal(p["position_fp"]) for p in positions if p.get("ticker") == ticker), Decimal("0"))

    def order(self, order_id):
        if not REQUEST_RE.fullmatch(order_id):
            raise ManualTradeError("Invalid order ID")
        return self.request("GET", PREFIX+"/portfolio/orders/"+order_id)

    def create(self, ticker, plan, request_id):
        # Only explicit POST from the manual trade endpoint calls this.
        return self.request("POST", PREFIX+"/portfolio/events/orders", data={
            "ticker": ticker, "client_order_id": request_id,
            "side": plan["book_side"], "count": plan["count"],
            "price": plan["v2_yes_price"], "time_in_force": "immediate_or_cancel",
            "self_trade_prevention_type": "taker_at_cross",
            "post_only": False, "cancel_order_on_pause": True,
            "reduce_only": plan["reduce_only"],
            "exchange_index": -1,
        })

    def close(self):
        self.http.close()


class ManualTrading:
    def __init__(self, root: Path, vault, *, api=None, public=None, live_reader=None):
        self.root = root.resolve()
        self.vault = vault
        self.api = api or KalshiManualClient(vault)
        self.public = public or PublicMarketClient()
        if live_reader is None:
            from kalshi_edge.bootstrap.live_dashboard import read_live_dashboard
            live_reader = read_live_dashboard
        self.live_reader = live_reader
        self._mutex = threading.Lock()
        ledger = self.root/"trading"/"manual.sqlite3"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        self.ledger = ledger
        with sqlite3.connect(str(ledger)) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS clicks (
                request_id TEXT PRIMARY KEY, ticker TEXT NOT NULL,
                created_ns INTEGER NOT NULL, state TEXT NOT NULL,
                exchange_order_id TEXT, error TEXT
            )""")

    def _current(self):
        current = self.live_reader(self.root)
        ticker = current.get("market_ticker")
        if not isinstance(ticker,str) or not TICKER_RE.fullmatch(ticker):
            raise ManualTradeError("Waiting for the current Kalshi BTC 15-minute market")
        return ticker

    def _quote(self, ticker):
        market, orderbook = self.public.quote(ticker)
        m = market.get("market")
        if not isinstance(m,dict) or m.get("ticker") != ticker or m.get("status") not in ("active", "open"):
            raise ManualTradeError("Market is not open")
        from datetime import datetime, timezone
        try:
            close = datetime.fromisoformat(m["close_time"].replace("Z","+00:00"))
            if close.tzinfo is None or close <= datetime.now(timezone.utc):
                raise ManualTradeError("Market is closed")
        except (KeyError, AttributeError, ValueError) as exc:
            raise ManualTradeError("Market has no valid closing time") from exc
        return m, quote_from_orderbook(orderbook)

    def status(self):
        saved = bool(self.vault is not None and self.vault.exists())
        try:
            ticker = self._current()
            market, prices = self._quote(ticker)
        except (ManualTradeError, httpx.HTTPError, ValueError) as exc:
            return {"available":False, "message":str(exc), "credentials_saved":saved}
        result = {"available":True, "ticker":ticker,"quotes":prices,
                  "fractional": market.get("fractional_trading_enabled") is True,
                  "balance_dollars":None,"position_fp":None, "credentials_saved":saved}
        if not saved:
            result["message"] = "Save Kalshi API credentials in AutoTrader below"
        else:
            try:
                result["balance_dollars"] = str(self.api.balance())
                result["position_fp"] = str(self.api.position(ticker))
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                result["message"] = "Cannot read authenticated Kalshi account"
        return result

    def submit(self, payload:dict):
        if not isinstance(payload,dict) or set(payload) != {
            "ticker","action","side","mode","amount","request_id","quoted_price"
        }:
            raise ManualTradeError("Invalid manual trade submission")
        ticker=payload["ticker"]
        request_id=payload["request_id"]
        if not isinstance(ticker,str) or not TICKER_RE.fullmatch(ticker):
            raise ManualTradeError("Invalid market ticker")
        if not isinstance(request_id,str) or not REQUEST_RE.fullmatch(request_id):
            raise ManualTradeError("Invalid unique request ID")
        if not self.vault or not self.vault.exists():
            raise ManualTradeError("Save real Kalshi API credentials first")
        if ticker != self._current():
            raise ManualTradeError("Market has changed. Refresh the quote")
        # Cross-thread and restart idempotency. Persist BEFORE any exchange POST.
        with self._mutex,sqlite3.connect(str(self.ledger),timeout=5) as db:
            cur=db.execute("INSERT OR IGNORE INTO clicks(request_id,ticker,created_ns,state) "
                           "VALUES(?,?,?,'PROCESSING')", (request_id,ticker,time.time_ns()))
            if cur.rowcount != 1:
                raise ManualTradeError("Trade request already processed; check your Kalshi orders")
        exchange_id = None
        try:
            market, prices = self._quote(ticker)
            action, side = payload["action"], payload["side"]
            quote = prices.get(side, {}).get(action) if isinstance(side,str) else None
            if quote is None:
                raise ManualTradeError("Selected orderbook quote disappeared")
            shown_price = _price(payload["quoted_price"])
            fresh_price = _price(quote["price"])
            # Refuse any adverse drift; never spend beyond user's displayed quote.
            if (action == "buy" and fresh_price > shown_price
                    or action == "sell" and fresh_price < shown_price):
                raise ManualTradeError("Price moved against you; refresh quote")
            balance = self.api.balance()
            position = self.api.position(ticker)
            plan = plan_manual_order(prices, action, side, payload["mode"], payload["amount"],
                                     balance, position,
                                     fractional=market.get("fractional_trading_enabled") is True)
            result = self.api.create(ticker,plan,request_id)
            exchange_id=result.get("order_id")
            if not isinstance(exchange_id,str) or not exchange_id:
                raise ManualTradeError("Kalshi response missing order ID; check order history")
            fills = decimal(result.get("fill_count", "0"))
            count = decimal(plan["count"])
            if not Decimal("0") <= fills <= count:
                raise ManualTradeError("Kalshi returned invalid filled quantity")
            state = "FILLED" if fills == count else "PARTIAL" if fills > 0 else "NO_FILL"
            with self._mutex,sqlite3.connect(str(self.ledger)) as db:
                db.execute("UPDATE clicks SET state=?,exchange_order_id=? WHERE request_id=?",
                           (state,exchange_id,request_id))
            return {
                "state": state, "ticker":ticker, "side":side, "action":action,
                "order_id":exchange_id, "filled_shares":str(fills),
                "requested_shares":plan["count"],
                "indicative_cost":plan["estimated_total"],
                "indicative_fees":plan["estimated_fee_reserve"],
                "limit_price":plan["outcome_price"], "remaining_count":result.get("remaining_count"),
            }
        except Exception as exc:
            # Unknown exchange outcome is NEVER automatically resubmitted.
            status = "UNKNOWN" if exchange_id is not None or isinstance(exc,httpx.RequestError) else "ERROR"
            with self._mutex, sqlite3.connect(str(self.ledger)) as db:
                db.execute("UPDATE clicks SET state=?,exchange_order_id=?,error=? WHERE request_id=?",
                           (status,exchange_id,str(exc)[:160],request_id))
            raise

    def close(self):
        self.api.close()
        self.public.close()
