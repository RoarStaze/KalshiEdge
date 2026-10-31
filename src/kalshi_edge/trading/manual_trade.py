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

from .risk import decimal, taker_fee_reserve, reference_nondirect_fee
from .paper import PublicMarketClient


API_ROOT = "https://external-api.kalshi.com"
PREFIX = "/trade-api/v2"
STEP = Decimal("0.01")
ONE = Decimal("1")
MAX_REQUEST_DOLLARS = Decimal("100000")
QUOTE_LIFETIME_SECONDS = 9
# Kalshi Basic/Advanced accounts may request /portfolio/balance only once/second.
ACCOUNT_BALANCE_MIN_INTERVAL_SECONDS = 1.15
ACCOUNT_SNAPSHOT_SECONDS = 1.15


class TradingError(ValueError):
    pass


class NoLiquidityError(TradingError):
    """Selected side has no executable order-book quote (not a data error)."""
    pass


def _money(value):
    return format(value.quantize(Decimal("0.0001")), ".4f")


def _shares(value):
    return format(value.quantize(Decimal("0.01")), ".2f")


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
        raise NoLiquidityError("No liquidity")
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
        if set(request) != required | {"reduce_only"}:
            raise TradingError("Invalid signed order fields")
        if not request["ticker"].startswith("KXBTC15M-"):
            raise TradingError("Non-BTC market rejected")
        payload = {
            **request,
            "time_in_force": "immediate_or_cancel",
            "self_trade_prevention_type": "taker_at_cross",
            "post_only": False,
            "cancel_order_on_pause": True,
            "reduce_only": bool(request.get("reduce_only")),
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
        # Serialize all balance reads across browser tabs and HTTP worker threads.
        self._account_lock = threading.Lock()
        self._account_cache = None
        self._last_balance_get_at = None

    def _account(self, ticker: str, *, fresh: bool = False):
        with self._account_lock:
            now = time.monotonic()
            cached = self._account_cache
            if (not fresh and cached is not None and cached["ticker"] == ticker
                    and now - cached["at"] < ACCOUNT_SNAPSHOT_SECONDS):
                return cached["balance"], cached["position"]

            # Never return a pre-trade balance when explicitly asked for fresh.
            self._account_cache = None
            if self._last_balance_get_at is not None:
                pause = ACCOUNT_BALANCE_MIN_INTERVAL_SECONDS - (
                    now - self._last_balance_get_at)
                if pause > 0:
                    time.sleep(pause)

            self._last_balance_get_at = time.monotonic()
            try:
                balance_raw = self.client.get("/portfolio/balance")
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 429:
                    raise
                # Only a read is retried. Do not retry any live order POST.
                time.sleep(ACCOUNT_BALANCE_MIN_INTERVAL_SECONDS)
                self._last_balance_get_at = time.monotonic()
                balance_raw = self.client.get("/portfolio/balance")

            balance = decimal(balance_raw.get("balance_dollars",
                                  Decimal(str(balance_raw["balance"])) / 100
                                  if "balance" in balance_raw else None))
            marked_cents = balance_raw.get("portfolio_value")
            # Kalshi returns the marked value of held contracts in integer cents.
            # Cash is returned separately as a fixed-point dollar string.
            # Never present cash alone as the full portfolio if valuation is missing.
            if marked_cents is not None:
                if type(marked_cents) is not int or marked_cents < 0:
                    raise TradingError("Invalid Kalshi portfolio value")
                portfolio_total = balance + Decimal(marked_cents) / 100
            else:
                portfolio_total = None
            positions = self.client.get("/portfolio/positions",
                                        params={"ticker": ticker, "limit": 10})
            rows = positions.get("market_positions")
            if not isinstance(rows, list):
                raise TradingError("Kalshi position response unavailable")
            position = Decimal("0")
            exposure = Decimal("0")
            has_exposure = True
            for row in rows:
                if row.get("ticker") == ticker:
                    position += decimal(row["position_fp"])
                    raw = row.get("market_exposure_dollars")
                    if raw is None:
                        has_exposure = False
                    else:
                        exposure += decimal(raw)
            self._account_cache = {
                "ticker": ticker, "balance": balance, "position": position,
                "exposure": exposure if has_exposure and exposure >= 0 else None,
                "portfolio_total": portfolio_total,
                "at": time.monotonic(),
            }
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
        # Public GET /markets/{ticker} may omit fractional_trading_enabled.
        # V2 accepts decimal count strings; allow 0.01 contract increments
        # unless the market explicitly rejects fractional trading. Kalshi
        # remains the final authority and can reject an unsupported order.
        # Never round count *up* or bypass liquidity/balance checks.
        step = ONE if (market.get("fractional_trading_enabled") is False and
                       not (action == "SELL" and position % ONE != 0)) else STEP
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
        if action == "BUY":
            # Size by the fee-inclusive debit at the exchange's non-direct
            # cent-aligned balance precision. The previous taker_fee_reserve
            # adds an extra one-cent buffer, systematically understating
            # purchasable contracts, especially for sub-cent prices.
            # This is a standard-schedule estimate, not a guaranteed Kalshi
            # fee quote; execution may still be rejected or partially filled.
            budget = min(balance, value) if units == "DOLLARS" else balance
            low, high = 0, int(count / step)
            while low < high:
                mid = (low + high + 1) // 2
                candidate = step * mid
                candidate_fee = reference_nondirect_fee(per, candidate)
                if candidate * per + candidate_fee <= budget:
                    low = mid
                else:
                    high = mid - 1
            count = step * low
            fee = reference_nondirect_fee(per, count) if count >= step else Decimal("0")
        else:
            # Approximate the executable cash-out, without the extra $0.01
            # sizing reserve used for paper-trading risk estimates.
            fee = reference_nondirect_fee(per, count)
        if count < step:
            raise TradingError("Insufficient cash for the requested size including fee reserve")
        # Fees for SELL are estimated; realized amounts come from the exchange.
        gross = count * per
        effective = gross + fee if action == "BUY" else max(Decimal("0"), gross - fee)
        return {
            "side": price["book_side"], "action": action, "outcome": outcome,
            "price": _money(price["yes_price"]), "unit_price": _money(per),
            "count": _shares(count), "fee_estimate": format(fee, ".6f"),
            "amount_estimate": _money(effective),
            "maximum_payout": _money(count if action == "BUY" else Decimal("0")),
            "available_balance": _money(balance),
            "position_yes": _shares(position),
        }

    def _prepare(self, ticker, action, outcome, units, amount, *, fresh_account=False):
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
        balance, position = self._account(ticker, fresh=fresh_account)
        plan = self.size_order(balance, action, outcome, units, decimal(amount),
                               position, book, market)
        plan["ticker"] = ticker
        return plan

    def market(self, ticker):
        if not ticker.startswith("KXBTC15M-") or not ticker.replace("-", "").isalnum():
            raise TradingError("Unsupported market ticker")
        market, book = self.market_client.quote(ticker)
        if market.get("market", {}).get("ticker") != ticker:
            raise TradingError("Market ticker mismatch")
        balance, position = self._account(ticker)
        with self._account_lock:
            cache = self._account_cache
            exposure = (cache.get("exposure") if cache is not None
                        and cache["ticker"] == ticker else None)
        average_entry = (exposure / abs(position) if
                         exposure is not None and position != 0 else None)
        if average_entry is not None and not 0 <= average_entry <= 1:
            average_entry = None
        try:
            up = quote_from_book(book, "BUY", "UP")
        except NoLiquidityError:
            up = None
        try:
            down = quote_from_book(book, "BUY", "DOWN")
        except NoLiquidityError:
            down = None
        return {
            "ticker": ticker,
            "status": market["market"].get("status"),
            "up_ask": _money(up["unit_cash"]) if up else None,
            "down_ask": _money(down["unit_cash"]) if down else None,
            "available_balance": _money(balance),
            "portfolio_total": _money(cache["portfolio_total"])
            if cache is not None and cache["ticker"] == ticker
            and cache.get("portfolio_total") is not None else None,
            "position_yes": _money(position),
            "position_exposure_dollars": _money(exposure) if exposure is not None else None,
            "average_entry_price": _money(average_entry) if average_entry is not None else None,
        }

    def preview_all(self, ticker: str):
        """Preview the entire held UP or DOWN position without user-entered size.

        If top-of-book quantity cannot cover the full position, do not
        advertise a partial liquidation as Sell All.
        """
        _, position = self._account(ticker)
        if position == 0:
            raise TradingError("No position to sell in the current market")
        outcome = "UP" if position > 0 else "DOWN"
        preview = self.preview(ticker, "SELL", outcome, "SHARES", _shares(abs(position)))
        if Decimal(preview["count"]) != abs(position):
            with self._lock:
                self._pending.pop(preview["preview_id"], None)
            raise NoLiquidityError("Not enough liquidity to sell entire position")
        with self._lock:
            pending = self._pending.get(preview["preview_id"])
            if pending is None:
                raise TradingError("Sell quote expired; refresh")
            pending["sell_all"] = True
        return {**preview, "sell_all": True}

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
        return {"preview_id": preview_id, "expires_in_seconds": QUOTE_LIFETIME_SECONDS,
                "units": units, "input_amount": str(amount), **plan}

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
                              data["units"], data["amount"], fresh_account=True)
        if data.get("sell_all") and fresh["position_yes"] != old["position_yes"]:
            raise TradingError("Position changed; refresh sell quote")
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
            "reduce_only": data["action"] == "SELL",
        }
        # The exchange enforces IOC; no unfilled shares remain resting.
        try:
            result = self.client.send_order(order)
        finally:
            # A fill or uncertain order outcome invalidates all cached balances.
            with self._account_lock:
                self._account_cache = None
        order_id = result.get("order_id")
        if not isinstance(order_id, str) or not order_id:
            raise TradingError("Exchange response ambiguous; check Kalshi orders before retry")
        fills = decimal(result.get("fill_count"))
        if fills < 0 or fills > Decimal(old["count"]):
            raise TradingError("Exchange-reported fill quantity invalid")
        average = result.get("average_fill_price")
        fees = result.get("average_fee_paid")
        actual_unit = None
        actual_cost = None
        actual_fees = None
        max_payout = None
        actual_proceeds = None
        if fills > 0 and average is not None:
            yes_price = decimal(average)
            if not 0 <= yes_price <= 1:
                raise TradingError("Exchange-reported average price invalid")
            unit = yes_price if data["outcome"] == "UP" else ONE - yes_price
            actual_unit = _money(unit)
            principal = unit * fills
            fee_paid = decimal(fees) * fills if fees is not None else None
            actual_fees = _money(fee_paid) if fee_paid is not None else None
            if data["action"] == "BUY":
                actual_cost = _money(principal + fee_paid) if fee_paid is not None else None
                max_payout = _money(fills)
            else:
                actual_proceeds = _money(principal - fee_paid) if fee_paid is not None else None
        return {
            "status": "FILLED" if fills == Decimal(old["count"]) else "PARTIAL" if fills > 0 else "NO_FILL",
            "order_id": order_id, "ticker": data["ticker"],
            "action": data["action"], "outcome": data["outcome"],
            "fill_count": _shares(fills),
            "average_yes_price": average if fills else None,
            "actual_unit_price": actual_unit,
            "average_fee_paid": fees if fills else None,
            "actual_fees": actual_fees,
            "actual_cost": actual_cost, "actual_proceeds": actual_proceeds,
            "max_payout": max_payout,
            "requested_count": old["count"],
            "estimated_per_share": old["unit_price"],
        }

    def close(self):
        self.client.close()
        self.market_client.close()
