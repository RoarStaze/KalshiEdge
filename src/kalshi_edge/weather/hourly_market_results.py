"""Recover and audit immutable official Kalshi hourly market results.

The official finalized contract is authoritative for its own YES/NO outcome.
It is a separate evidence tier from the independently reconciled Index.
The live collector is bounded in time and request count, and fairly processes
both recent and older unresolved saved forecasts. Never creates forecasts.
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
from pathlib import Path
from typing import Callable

import httpx
from .hourly_forward import _utc, _frozen

TICKER = re.compile(r"^[A-Z0-9][A-Z0-9.-]{5,160}$")
MAX_RECOVERY_DAYS = 90
MAX_BATCH = 32
MAX_CONCURRENT = 4


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _init(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("""CREATE TABLE IF NOT EXISTS weather_hourly_final_market_results(
      city TEXT NOT NULL, event_ticker TEXT NOT NULL, market_ticker TEXT NOT NULL,
      result TEXT NOT NULL, settled_at TEXT NOT NULL,
      received_at TEXT NOT NULL, market_raw TEXT NOT NULL, market_sha TEXT NOT NULL,
      PRIMARY KEY(city,event_ticker,market_ticker))""")
    # Mutable operational diagnostics are stored separately from immutable
    # source and outcome evidence. This table never supplies labels.
    conn.execute("""CREATE TABLE IF NOT EXISTS weather_hourly_final_market_checks(
      city TEXT NOT NULL, event_ticker TEXT NOT NULL, market_ticker TEXT NOT NULL,
      state TEXT NOT NULL, detail TEXT NOT NULL,
      checked_at TEXT NOT NULL, attempt_count INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY(city,event_ticker,market_ticker))""")


def verify_final_market(city: str, event: str, ticker: str, threshold: float,
                        result: str, settled_at: str, received_at: str,
                        raw: str, digest: str) -> str:
    """Return verified exact final YES/NO; hashes and identity are mandatory."""
    from .hourly import CITIES, parse_markets, listed_event_target
    if (city not in CITIES or not isinstance(event,str) or
        not isinstance(ticker,str) or not TICKER.fullmatch(ticker) or
        not ticker.startswith(event+"-") or
        not isinstance(raw,str) or _hash(raw)!=digest or
        not isinstance(threshold,(int,float)) or
        isinstance(threshold,bool) or not math.isfinite(threshold)):
        raise ValueError("INVALID_FINAL_MARKET_EVIDENCE_IDENTITY")
    target=listed_event_target(city,event)
    if target is None:
        raise ValueError("INVALID_FINAL_MARKET_EVENT")
    checked=_utc(received_at)
    settled=_utc(settled_at)
    if (not target<=settled<=checked or
        checked-target>timedelta(days=MAX_RECOVERY_DAYS)):
        raise ValueError("FINAL_MARKET_SETTLEMENT_TIME_MISMATCH")
    payload=json.loads(raw)
    market=payload.get("market") if isinstance(payload,dict) else None
    if (not isinstance(market,dict) or
        market.get("ticker")!=ticker or market.get("event_ticker")!=event or
        market.get("status") not in ("finalized","settled") or
        market.get("is_provisional") is True or
        market.get("result") not in ("yes","no") or
        market.get("result")!=result or
        market.get("settlement_ts")!=settled_at):
        raise ValueError("FINAL_MARKET_NOT_VERIFIED")
    parsed=parse_markets({"markets":[market],"cursor":""},city,event,settled=True)
    if (len(parsed)!=1 or parsed[0]["ticker"]!=ticker or
        abs(parsed[0]["threshold_f"]-threshold)>1e-6):
        raise ValueError("FINAL_MARKET_THRESHOLD_MISMATCH")
    return result


def _recovery_batch(pending: dict, limit: int = MAX_BATCH) -> list[tuple]:
    """Reserve some capacity for oldest unresolved events on every poll."""
    entries=sorted(pending, key=lambda key:pending[key][1],reverse=True)
    if len(entries)<=limit:
        return entries
    recent=entries[:limit//2]
    older=list(reversed(entries[-(limit-len(recent)):]))
    return recent+older


async def collect_final_market_results_once(
    db_path: Path, *, client: httpx.AsyncClient | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict:
    """Bounded, fair recovery of official finalized outcomes up to 90 days."""
    from .hourly import API, MAX_SIZE
    path=Path(db_path)
    now=_utc((clock or (lambda:datetime.now(timezone.utc)))())
    report={"mode":"HOURLY_FINAL_MARKET_RESULTS","real_orders_enabled":False,
            "verified":0,"awaiting":0,"unverified":0,
            "checked":0,"remaining":0,"errors":[]}
    if not path.is_file():
        return report

    with closing(sqlite3.connect(path,timeout=10)) as conn:
        _init(conn)
        conn.commit()
        tables={r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        parts=[]
        params=[]
        for table,kind in (
            ("weather_hourly_forward_snapshots","v9"),
            ("weather_hourly_snapshots","legacy")):
            if table not in tables:
                continue
            parts.append(f"""SELECT city,event_ticker,market_ticker,target_utc,
                forecast_json,forecast_sha,'{kind}' AS kind
                FROM {table} WHERE target_utc>=? AND target_utc<=?""")
            params.extend(((now-timedelta(days=MAX_RECOVERY_DAYS)).isoformat(),
                           (now-timedelta(minutes=6)).isoformat()))
        rows=(conn.execute(" UNION ALL ".join(parts)+
                           " ORDER BY target_utc DESC",params).fetchall()
              if parts else [])
        existing={tuple(x) for x in conn.execute(
            "SELECT city,event_ticker,market_ticker FROM weather_hourly_final_market_results")}

    pending={}
    for city,event,ticker,target,raw,sha,kind in rows:
        key=(city,event,ticker)
        if key in existing or key in pending:
            continue
        try:
            if not isinstance(ticker,str) or not TICKER.fullmatch(ticker):
                raise ValueError("INVALID_FROZEN_TICKER")
            if kind=="v9":
                frozen=_frozen(raw,sha)
                threshold=frozen["threshold_f"]
                if (frozen["city"],frozen["event_ticker"],frozen["market_ticker"])!=key:
                    raise ValueError("INVALID_FROZEN_MARKET_IDENTITY")
            else:
                if _hash(raw)!=sha:
                    raise ValueError("TAMPERED_LEGACY_MARKET_FORECAST")
                frozen=json.loads(raw)
                if (frozen["city"],frozen["event_ticker"],
                    frozen["selected_market"]["ticker"])!=key:
                    raise ValueError("INVALID_LEGACY_MARKET_IDENTITY")
                threshold=frozen["selected_market"]["threshold_f"]
            if (isinstance(threshold,bool) or
                not isinstance(threshold,(int,float)) or
                not math.isfinite(threshold)):
                raise ValueError("INVALID_FROZEN_THRESHOLD")
            pending[key]=(float(threshold),target)
        except (ValueError,TypeError,KeyError,json.JSONDecodeError):
            report["unverified"]+=1

    batch=_recovery_batch(pending)
    report["checked"]=len(batch)
    report["remaining"]=len(pending)-len(batch)
    if not batch:
        return report

    own=client is None
    if own:
        client=httpx.AsyncClient(timeout=8,follow_redirects=False,
            headers={"User-Agent":"KalshiEdge-HourlyFinalMarketAudit/2.0"})
    assert client is not None
    semaphore=asyncio.Semaphore(MAX_CONCURRENT)
    # Public API read-limit headroom. Archive fallbacks count as requests too.
    # Synthetic clients are unthrottled to keep tests deterministic and fast.
    interval=0.16 if own else 0.
    pace=asyncio.Lock()
    next_slot=[asyncio.get_running_loop().time()]

    async def fetch(url: str) -> httpx.Response:
        async with pace:
            now_slot=asyncio.get_running_loop().time()
            if interval and next_slot[0]>now_slot:
                await asyncio.sleep(next_slot[0]-now_slot)
            next_slot[0]=asyncio.get_running_loop().time()+interval
        return await client.get(url)

    async def resolve(key: tuple) -> tuple[tuple,str,str,str | None,dict | None]:
        city,event,ticker=key
        threshold,_=pending[key]
        async with semaphore:
            try:
                response=await fetch(f"{API}/markets/{ticker}")
                if response.status_code==404:
                    response=await fetch(f"{API}/historical/markets/{ticker}")
                response.raise_for_status()
                if len(response.content)>MAX_SIZE:
                    raise ValueError("OVERSIZED_FINAL_MARKET_RESPONSE")
                raw=response.content.decode("utf-8")
                payload=json.loads(raw)
                market=payload.get("market") if isinstance(payload,dict) else None
                if not isinstance(market,dict):
                    raise ValueError("INVALID_FINAL_MARKET_RESPONSE")
                state=market.get("status")
                if state not in ("finalized","settled"):
                    return key,"AWAITING_FINALIZATION",f"official status={state}",None,None
                if market.get("is_provisional") is True:
                    return key,"AWAITING_FINALIZATION","official provisional result",None,None
                if market.get("result") not in ("yes","no") or not isinstance(
                        market.get("settlement_ts"),str):
                    return key,"AWAITING_FINALIZATION","official final result/timestamp unavailable",None,None
                verified=verify_final_market(
                    city,event,ticker,threshold,market["result"],
                    market["settlement_ts"],now.isoformat(),raw,_hash(raw))
                return key,"VERIFIED","exact finalized contract",raw,{
                    "result":verified,"settlement_ts":market["settlement_ts"]}
            except (httpx.HTTPError,ValueError,TypeError,OverflowError,
                    KeyError,UnicodeError,json.JSONDecodeError) as exc:
                category=("FETCH_ERROR" if isinstance(exc,httpx.HTTPError)
                          else "INVALID_OFFICIAL_EVIDENCE")
                return key,category,f"{type(exc).__name__}: {exc}"[:160],None,None

    try:
        resolved=await asyncio.gather(*(resolve(key) for key in batch))
        with closing(sqlite3.connect(path,timeout=12)) as conn:
            _init(conn)
            for key,state,detail,raw,data in resolved:
                city,event,ticker=key
                conn.execute("""INSERT INTO weather_hourly_final_market_checks
                   (city,event_ticker,market_ticker,state,detail,checked_at,attempt_count)
                   VALUES(?,?,?,?,?,?,1)
                   ON CONFLICT(city,event_ticker,market_ticker) DO UPDATE SET
                     state=excluded.state,detail=excluded.detail,
                     checked_at=excluded.checked_at,
                     attempt_count=attempt_count+1""",
                     (city,event,ticker,state,detail,now.isoformat()))
                if state=="VERIFIED":
                    inserted=conn.execute("""INSERT OR IGNORE INTO weather_hourly_final_market_results
                      (city,event_ticker,market_ticker,result,settled_at,
                       received_at,market_raw,market_sha)
                      VALUES(?,?,?,?,?,?,?,?)""",
                        (city,event,ticker,data["result"],data["settlement_ts"],
                         now.isoformat(),raw,_hash(raw))).rowcount
                    report["verified"]+=inserted
                elif state=="AWAITING_FINALIZATION":
                    report["awaiting"]+=1
                else:
                    report["unverified"]+=1
                    if len(report["errors"])<12:
                        report["errors"].append({"ticker":ticker,"reason":detail})
            conn.commit()
    finally:
        if own:
            await client.aclose()
    return report
