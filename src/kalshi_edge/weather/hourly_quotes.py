"""Read-only Kalshi hourly quote evidence, independent from model probabilities.

The public individual-market endpoint may expose YES/NO asks and offered
fixed-point quantity. Neither an old last trade nor a model probability is an
executable price. This module never creates or submits orders.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable

import httpx

API = "https://external-api.kalshi.com/trade-api/v2"
QUOTE_TTL_SECONDS = 35
MAX_MARKETS_PER_CITY = 2
MAX_SOURCE_BYTES = 3_000_000


def _utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value=datetime.fromisoformat(value.replace("Z","+00:00"))
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("NAIVE_QUOTE_TIMESTAMP")
    return value.astimezone(timezone.utc)


def _json(data: dict) -> str:
    return json.dumps(data,sort_keys=True,allow_nan=False,separators=(",",":"))


def _dec(raw: object, *, maximum: Decimal, allow_zero: bool = True) -> Decimal | None:
    if raw is None or raw=="":
        return None
    if not isinstance(raw,str) or not re.fullmatch(r"\d+(?:\.\d{1,8})?",raw):
        raise ValueError("NON_DECIMAL_KALSHI_ASK_OR_SIZE")
    try:
        dec=Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError("INVALID_KALSHI_ASK_OR_SIZE") from exc
    if not dec.is_finite() or dec<0 or dec>maximum or (dec==0 and not allow_zero):
        raise ValueError("OUT_OF_RANGE_KALSHI_ASK_OR_SIZE")
    return dec


def parse_live_market_quote(market: dict, *,city: str,event: str,
                            ticker: str,threshold_f: float,
                            receipt: datetime) -> dict:
    """Reconcile independent market GET to a previously verified HS contract."""
    from .hourly import _valid_market
    at=_utc(receipt)
    if not isinstance(market,dict):
        raise ValueError("INVALID_KALSHI_MARKET")
    if market.get("status") not in ("active","open","closed","settled"):
        raise ValueError("UNKNOWN_KALSHI_MARKET_STATUS")
    # To recognize closure, validate the *same* source market while allowing
    # the status to differ. All identity, rules and strike checks still apply.
    probe=dict(market)
    probe["status"]="active"
    item=_valid_market(probe,city,event)
    if (item is None or item["ticker"]!=ticker or
        not math.isfinite(threshold_f) or
        abs(item["threshold_f"]-threshold_f)>1e-6):
        raise ValueError("KALSHI_QUOTE_MARKET_IDENTITY_MISMATCH")
    close_raw=item["close_time_utc"]
    if not close_raw:
        raise ValueError("KALSHI_CLOSE_TIME_UNVERIFIED")
    close=_utc(close_raw)
    result={"status":"NO_VISIBLE_ASK",
            "city":city,"event_ticker":event,"ticker":ticker,
            "threshold_f":float(threshold_f),"close_time_utc":close.isoformat(),
            "received_at":at.isoformat(),
            "yes_ask_dollars":None,"no_ask_dollars":None,
            "yes_ask_size_fp":None,"no_ask_size_fp":None,
            "real_orders_enabled":False}
    if market["status"] in ("closed","settled") or close<=at:
        result["status"]="CLOSED"
        return result
    valid=0
    for side in ("yes","no"):
        price_field=side+"_ask_dollars"
        size_field=side+"_ask_size_fp"
        p=_dec(market.get(price_field),maximum=Decimal("1"))
        size=_dec(market.get(size_field),maximum=Decimal("1000000000"))
        if p is not None and size is not None and p>0 and size>0:
            result[price_field]=f"{p:.4f}"
            result[size_field]=f"{size:.2f}"
            valid+=1
    if valid:
        result["status"]="VALID_ASK"
    return result


def _init_quotes(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("""CREATE TABLE IF NOT EXISTS weather_hourly_quote_snapshots(
        ticker TEXT PRIMARY KEY,city TEXT NOT NULL,event_ticker TEXT NOT NULL,
        threshold_f REAL NOT NULL,received_at TEXT NOT NULL,
        payload_json TEXT NOT NULL,payload_sha256 TEXT NOT NULL)""")


def store_quote(conn: sqlite3.Connection, *,city: str,event: str,ticker: str,
                threshold_f: float,receipt: datetime,quote: dict) -> None:
    """Store the latest independently validated quote with hash and receipt."""
    at=_utc(receipt)
    if (quote.get("city")!=city or quote.get("event_ticker")!=event or
        quote.get("ticker")!=ticker or
        quote.get("received_at")!=at.isoformat() or
        quote.get("real_orders_enabled") is not False or
        abs(float(quote["threshold_f"])-threshold_f)>1e-6):
        raise ValueError("QUOTE_STORAGE_IDENTITY_MISMATCH")
    _init_quotes(conn)
    payload=_json(quote)
    digest=hashlib.sha256(payload.encode()).hexdigest()
    conn.execute("""INSERT INTO weather_hourly_quote_snapshots
        (ticker,city,event_ticker,threshold_f,received_at,payload_json,payload_sha256)
        VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(ticker) DO UPDATE SET
        city=excluded.city,event_ticker=excluded.event_ticker,
        threshold_f=excluded.threshold_f,received_at=excluded.received_at,
        payload_json=excluded.payload_json,payload_sha256=excluded.payload_sha256
        WHERE weather_hourly_quote_snapshots.received_at<=excluded.received_at""",
        (ticker,city,event,threshold_f,at.isoformat(),payload,digest))


def _empty(status: str) -> dict:
    return {"status":status,"yes_ask_dollars":None,"no_ask_dollars":None,
            "yes_ask_size_fp":None,"no_ask_size_fp":None,
            "quote_received_at":None}


def read_latest_quote(conn: sqlite3.Connection, *,city: str,event: str,
                      ticker: str,threshold_f: float,asof: datetime) -> dict:
    """No quotes from the future, after expiry, or older than 35 seconds."""
    now=_utc(asof)
    try:
        record=conn.execute("""SELECT city,event_ticker,threshold_f,received_at,
                      payload_json,payload_sha256
                  FROM weather_hourly_quote_snapshots WHERE ticker=?""",
                  (ticker,)).fetchone()
    except sqlite3.Error:
        return _empty("QUOTE_PENDING")
    if not record:
        return _empty("QUOTE_PENDING")
    row_city,row_event,row_strike,received,payload,digest=record
    if row_city!=city or row_event!=event or abs(row_strike-threshold_f)>1e-6:
        return _empty("QUOTE_UNAVAILABLE")
    try:
        at=_utc(received)
        if at>now:
            return _empty("QUOTE_PENDING")
        if not isinstance(payload,str) or not isinstance(digest,str):
            return _empty("CORRUPTED_QUOTE")
        if hashlib.sha256(payload.encode()).hexdigest()!=digest:
            return _empty("CORRUPTED_QUOTE")
        data=json.loads(payload)
        if (data["city"]!=city or data["event_ticker"]!=event or
            data["ticker"]!=ticker or
            abs(float(data["threshold_f"])-threshold_f)>1e-6 or
            _utc(data["received_at"])!=at or
            data["real_orders_enabled"] is not False):
            return _empty("CORRUPTED_QUOTE")
        close=_utc(data["close_time_utc"])
        if data["status"]=="CLOSED" or now>=close:
            return _empty("CLOSED")
        if (now-at).total_seconds()>QUOTE_TTL_SECONDS:
            return _empty("STALE_QUOTE")
        if data["status"] not in ("VALID_ASK","NO_VISIBLE_ASK"):
            return _empty("QUOTE_UNAVAILABLE")
        return {"status":data["status"],
                "yes_ask_dollars":data["yes_ask_dollars"],
                "no_ask_dollars":data["no_ask_dollars"],
                "yes_ask_size_fp":data["yes_ask_size_fp"],
                "no_ask_size_fp":data["no_ask_size_fp"],
                "quote_received_at":at.isoformat(),
                "close_time_utc":close.isoformat()}
    except (ValueError,TypeError,KeyError,OverflowError,json.JSONDecodeError):
        return _empty("CORRUPTED_QUOTE")


async def collect_hourly_quotes_once(db_path: Path, *,
       client: httpx.AsyncClient | None=None,
       clock: Callable[[],datetime] | None=None) -> dict:
    """Bounded exact-market GET quote checks for only eligible listed markets."""
    from .hourly import read_hourly_forecasts
    now=_utc((clock or (lambda:datetime.now(timezone.utc)))())
    result={"mode":"HOURLY_QUOTES_READ_ONLY","real_orders_enabled":False,
            "checked":0,"valid_asks":0,"no_visible_ask":0,
            "closed":0,"unavailable":0,"errors":[]}
    path=Path(db_path)
    if not path.is_file():
        result["state"]="WAITING_FOR_LISTED_MARKETS"
        return result
    report=read_hourly_forecasts(path.parent,db_path=path,asof=now)
    selected=[]
    for row in report.get("forecasts",[]):
        if row.get("status")!="READY_RESEARCH":
            continue
        markets=row.get("markets") or []
        predicted=row.get("predicted_index_f")
        if not isinstance(predicted,(float,int)) or not math.isfinite(predicted):
            continue
        nearest=sorted(
            (m for m in markets if isinstance(m.get("ticker"),str)
             and isinstance(m.get("threshold_f"),(int,float))
             and (m.get("close_time_utc") is None or
                  _utc(m["close_time_utc"])>now)),
            key=lambda m:abs(m["threshold_f"]-predicted))
        for item in nearest[:MAX_MARKETS_PER_CITY]:
            selected.append((row["city"],row["event_ticker"],item))
    if not selected:
        result["state"]="WAITING_FOR_LISTED_MARKETS"
        return result
    own=client is None
    if own:
        client=httpx.AsyncClient(timeout=8.,follow_redirects=False,
          headers={"User-Agent":"KalshiEdge-Weather-Quote-Research/1.0"})
    assert client is not None
    guard=asyncio.Semaphore(3)
    async def fetch_one(city,event,item):
        async with guard:
            try:
                ticker=item["ticker"]
                reply=await client.get(f"{API}/markets/{ticker}")
                reply.raise_for_status()
                if len(reply.content)>MAX_SOURCE_BYTES:
                    raise ValueError("OVERSIZED_KALSHI_MARKET_DETAIL")
                payload=reply.json()
                if not isinstance(payload,dict) or not isinstance(payload.get("market"),dict):
                    raise ValueError("INVALID_KALSHI_MARKET_DETAIL")
                received=_utc((clock or (lambda:datetime.now(timezone.utc)))())
                parsed=parse_live_market_quote(
                    payload["market"],city=city,event=event,ticker=ticker,
                    threshold_f=item["threshold_f"],receipt=received)
                return city,event,item,received,parsed,None
            except (httpx.HTTPError,ValueError,KeyError,TypeError,
                    json.JSONDecodeError,OverflowError) as exc:
                return city,event,item,None,None,f"{type(exc).__name__}: {exc}"[:160]
    try:
        outcomes=await asyncio.gather(*(fetch_one(*entry) for entry in selected))
        with closing(sqlite3.connect(path,timeout=10)) as conn:
            _init_quotes(conn)
            for city,event,item,received,quote,error in outcomes:
                result["checked"]+=1
                if error:
                    result["unavailable"]+=1
                    result["errors"].append(error)
                    continue
                store_quote(conn,city=city,event=event,ticker=item["ticker"],
                    threshold_f=item["threshold_f"],receipt=received,quote=quote)
                if quote["status"]=="VALID_ASK":
                    result["valid_asks"]+=1
                elif quote["status"]=="NO_VISIBLE_ASK":
                    result["no_visible_ask"]+=1
                else:
                    result["closed"]+=1
            conn.commit()
        result["state"]="READY" if result["valid_asks"] else (
            "NO_VISIBLE_ASK" if result["no_visible_ask"] else
            "UNAVAILABLE" if result["unavailable"] else "NO_OPEN_MARKET")
    finally:
        if own:
            await client.aclose()
    return result
