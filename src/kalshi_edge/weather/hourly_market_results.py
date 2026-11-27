"""Read-only collection of *official finalized contract* outcomes for Weather History.

Kalshi's REST market status is 'finalized', not the query filter 'settled'.
The official binary contract result is authoritative for directional
correctness even if independent index-minute reconciliation is unavailable.
This is deliberately a separately labelled evidence tier, not certification
of the independent Weather Index observation. No orders or credentials.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
import httpx

from .hourly_forward import _utc, _frozen

TICKER = re.compile(r"^[A-Z0-9][A-Z0-9.-]{5,160}$")


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _init(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("""CREATE TABLE IF NOT EXISTS weather_hourly_final_market_results(
      city TEXT NOT NULL, event_ticker TEXT NOT NULL, market_ticker TEXT NOT NULL,
      result TEXT NOT NULL, settled_at TEXT NOT NULL,
      received_at TEXT NOT NULL, market_raw TEXT NOT NULL, market_sha TEXT NOT NULL,
      PRIMARY KEY(city,event_ticker,market_ticker))""")


def verify_final_market(city: str, event: str, ticker: str, threshold: float,
                        result: str, settled_at: str, received_at: str,
                        raw: str, digest: str) -> str:
    """Return YES or NO from an exact, final, nonprovisional Kalshi contract."""
    from .hourly import CITIES, parse_markets, listed_event_target
    if (city not in CITIES or not isinstance(event,str) or
        not isinstance(ticker,str) or not TICKER.fullmatch(ticker) or
        not ticker.startswith(event+"-") or
        not isinstance(raw,str) or _hash(raw)!=digest):
        raise ValueError("INVALID_FINAL_MARKET_EVIDENCE_IDENTITY")
    target=listed_event_target(city,event)
    if target is None:
        raise ValueError("INVALID_FINAL_MARKET_EVENT")
    checked=_utc(received_at)
    settled=_utc(settled_at)
    if not target<=settled<=checked or checked-target>timedelta(days=7):
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


async def collect_final_market_results_once(
    db_path: Path, *, client: httpx.AsyncClient | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict:
    """Persist exact finalized Kalshi results for previously frozen markets.

    Poll each distinct precommitted ticker once per cycle, up to seven days.
    Never create a prediction or outcome from a hypothetical market.
    """
    from .hourly import API, MAX_SIZE
    path=Path(db_path)
    now=_utc((clock or (lambda:datetime.now(timezone.utc)))())
    report={"mode":"HOURLY_FINAL_MARKET_RESULTS","real_orders_enabled":False,
            "verified":0,"awaiting":0,"unverified":0,"errors":[]}
    if not path.is_file():
        return report
    with closing(sqlite3.connect(path,timeout=8)) as conn:
        _init(conn)
        conn.commit()
        # Match exact market ticker and frozen evidence. The fallback preserves
        # older pre-V9 snapshots as well as all V9 prospective lead checkpoints.
        tables={r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        parts=[]
        params=[]
        for table,kind in (
            ("weather_hourly_forward_snapshots","v9"),
            ("weather_hourly_snapshots","legacy"),
        ):
            if table not in tables:
                continue
            # Table identifiers are fixed literals, never request-derived.
            parts.append(f"""SELECT city,event_ticker,market_ticker,target_utc,
                forecast_json,forecast_sha,'{kind}' AS kind
                FROM {table} WHERE target_utc>=? AND target_utc<=?""")
            params.extend(((now-timedelta(days=7)).isoformat(),
                           (now-timedelta(minutes=6)).isoformat()))
        rows=conn.execute(" UNION ALL ".join(parts),params).fetchall() if parts else []
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
            if not isinstance(threshold,(int,float)) or isinstance(threshold,bool):
                raise ValueError("INVALID_FROZEN_THRESHOLD")
            pending[key]=float(threshold)
        except (ValueError,TypeError,KeyError,json.JSONDecodeError):
            report["unverified"]+=1
    own=client is None
    if own:
        client=httpx.AsyncClient(timeout=20,follow_redirects=False,
            headers={"User-Agent":"KalshiEdge-HourlyFinalMarketAudit/1.0"})
    assert client is not None
    try:
        for (city,event,ticker),threshold in list(pending.items())[:250]:
            try:
                response=await client.get(f"{API}/markets/{ticker}")
                if response.status_code==404:
                    # Official older market archive, not a third-party feed.
                    response=await client.get(f"{API}/historical/markets/{ticker}")
                response.raise_for_status()
                if len(response.content)>MAX_SIZE:
                    raise ValueError("OVERSIZED_FINAL_MARKET_RESPONSE")
                raw=response.content.decode("utf-8")
                payload=json.loads(raw)
                market=payload.get("market") if isinstance(payload,dict) else None
                if not isinstance(market,dict):
                    raise ValueError("INVALID_FINAL_MARKET_RESPONSE")
                if market.get("status") not in ("finalized","settled"):
                    report["awaiting"]+=1
                    continue
                if market.get("is_provisional") is True:
                    report["awaiting"]+=1
                    continue
                if market.get("result") not in ("yes","no") or not isinstance(
                        market.get("settlement_ts"),str):
                    report["awaiting"]+=1
                    continue
                verify_final_market(city,event,ticker,threshold,
                    market["result"],market["settlement_ts"],
                    now.isoformat(),raw,_hash(raw))
                with closing(sqlite3.connect(path,timeout=10)) as conn:
                    _init(conn)
                    cur=conn.execute("""INSERT OR IGNORE INTO weather_hourly_final_market_results
                      (city,event_ticker,market_ticker,result,settled_at,
                       received_at,market_raw,market_sha)
                      VALUES(?,?,?,?,?,?,?,?)""",(
                        city,event,ticker,market["result"],market["settlement_ts"],
                        now.isoformat(),raw,_hash(raw)))
                    conn.commit()
                    report["verified"]+=cur.rowcount
            except (httpx.HTTPError,ValueError,TypeError,OverflowError,
                    KeyError,sqlite3.Error,UnicodeError,json.JSONDecodeError) as exc:
                report["unverified"]+=1
                if len(report["errors"])<12:
                    report["errors"].append({"ticker":ticker,
                       "reason":f"{type(exc).__name__}: {exc}"[:150]})
    finally:
        if own:
            await client.aclose()
    return report
