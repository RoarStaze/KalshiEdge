"""Immutable forward hourly Kalshi Index forecasts and exact-contract audits.

This module is entirely research-only. It never writes orders, never
retrospectively creates forecasts, and requires exact published index and
settled-contract agreement before evaluating a frozen prediction.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import httpx

from .status import weather_database_path

LEADS = (60, 45, 30, 15, 5)
LEAD_WINDOW_MINUTES = 1.5
MIN_VERIFIED_HOURS = 10
Z90 = 1.64485362695


def _utc(value: str | datetime) -> datetime:
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if not isinstance(stamp, datetime) or stamp.utcoffset() is None:
        raise ValueError("NAIVE_FORWARD_TIMESTAMP")
    return stamp.astimezone(timezone.utc)


def _number(value: object, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("INVALID_FORWARD_NUMBER")
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError("INVALID_FORWARD_NUMBER")
    return number


def _sha(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _init_forward(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("""CREATE TABLE IF NOT EXISTS weather_hourly_forward_snapshots(
        city TEXT NOT NULL, event_ticker TEXT NOT NULL,
        market_ticker TEXT NOT NULL, target_utc TEXT NOT NULL,
        lead_bucket INTEGER NOT NULL, asof TEXT NOT NULL,
        forecast_json TEXT NOT NULL, forecast_sha TEXT NOT NULL,
        PRIMARY KEY(city,event_ticker,lead_bucket))""")
    conn.execute("""CREATE INDEX IF NOT EXISTS weather_forward_target
        ON weather_hourly_forward_snapshots(target_utc)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS weather_hourly_forward_outcomes(
        city TEXT NOT NULL, event_ticker TEXT NOT NULL,
        market_ticker TEXT NOT NULL, result TEXT NOT NULL,
        observed_index_f REAL NOT NULL, observed_event_ms INTEGER NOT NULL,
        checked_at TEXT NOT NULL, index_config_version TEXT NOT NULL,
        source_index_raw TEXT NOT NULL, source_index_sha TEXT NOT NULL,
        market_raw TEXT NOT NULL, market_sha TEXT NOT NULL,
        PRIMARY KEY(city,event_ticker,market_ticker))""")


def _freeze(conn: sqlite3.Connection, row: dict, asof: datetime) -> dict:
    from .hourly import CITIES, listed_event_target

    if not isinstance(row, dict) or row.get("status") != "READY_RESEARCH":
        raise ValueError("NO_READY_FORECAST")
    city = row.get("city")
    if city not in CITIES:
        raise ValueError("INVALID_FORWARD_CITY")
    event = row.get("event_ticker")
    target = _utc(row["target_utc"])
    if (not isinstance(event, str) or
            listed_event_target(city, event) != target or
            _utc(row["asof"]) != asof or target <= asof):
        raise ValueError("INVALID_FORWARD_EVENT_OR_TIME")
    lead = (target-asof).total_seconds()/60.
    bucket = min(LEADS, key=lambda x: abs(x-lead))
    if abs(bucket-lead) > LEAD_WINDOW_MINUTES:
        raise ValueError("OUTSIDE_FORWARD_CHECKPOINT")

    mean = _number(row["predicted_index_f"], -129., 159.)
    sigma = _number(row["sigma_f"], .001, 30.)
    kind = row.get("model_kind")
    if not isinstance(kind, str) or not kind or len(kind) > 120:
        raise ValueError("INVALID_FORWARD_MODEL")

    readings = row.get("markets")
    if not isinstance(readings, list) or not readings:
        raise ValueError("NO_LISTED_FORWARD_CONTRACT")
    for candidate in readings:
        if not isinstance(candidate, dict):
            raise ValueError("MALFORMED_FORWARD_MARKET")
        _number(candidate.get("probability_yes"), 0., 1.)
        _number(candidate.get("threshold_f"), -129., 159.)
    chosen = min(readings, key=lambda x:(
        abs(float(x["probability_yes"])-.5), float(x["threshold_f"])))
    ticker = chosen.get("ticker")
    if not isinstance(ticker, str) or not ticker.startswith(event+"-"):
        raise ValueError("INVALID_FORWARD_TICKER")
    closing = _utc(chosen["close_time_utc"])
    if not asof < closing:
        raise ValueError("FORWARD_MARKET_CLOSED")
    listed_receipt = _utc(chosen["market_metadata_received_at"])
    if listed_receipt > asof:
        raise ValueError("FUTURE_MARKET_METADATA")

    source_event = row.get("source_event_ms")
    if not isinstance(source_event, int) or isinstance(source_event, bool):
        raise ValueError("INVALID_FORWARD_SOURCE_TIME")
    source_ts = datetime.fromtimestamp(source_event/1000., tz=timezone.utc)
    source_receipt = _utc(row["source_received_at"])
    if not source_ts <= source_receipt <= asof or asof-source_ts > timedelta(minutes=10):
        raise ValueError("STALE_OR_FUTURE_SOURCE_RECEIPT")
    cfg = row.get("source_config_version")
    if not isinstance(cfg, str) or not cfg or len(cfg)>160:
        raise ValueError("INVALID_FORWARD_INDEX_CONFIG")

    original = conn.execute("""SELECT received_at,temp_f,status,config_version,
                             payload,source_sha FROM weather_hourly_index
             WHERE city=? AND event_ms=?""", (city,source_event)).fetchone()
    if original is None:
        raise ValueError("MISSING_SOURCE_INDEX_ROW")
    stored_receipt, stored_value, status, stored_cfg, original_raw, source_sha = original
    if (status not in ("normal", "degraded") or stored_cfg != cfg or
        _utc(stored_receipt) != source_receipt or
        not isinstance(source_sha,str) or len(source_sha)!=64 or
        any(c not in "0123456789abcdef" for c in source_sha.lower())):
        raise ValueError("SOURCE_INDEX_METADATA_MISMATCH")
    source = json.loads(original_raw)
    if (not isinstance(source,dict) or
        source.get("t")!=source_event or source.get("status")!=status or
        not isinstance(source.get("contributors"),int) or
        isinstance(source["contributors"],bool) or source["contributors"]<=0 or
        abs(_number(source.get("v"),-129.,159.) -
            _number(stored_value,-129.,159.))>1e-6):
        raise ValueError("SOURCE_INDEX_RAW_MISMATCH")
    if row.get("current_index_f") is not None and (
        abs(_number(row["current_index_f"],-129.,159.)-float(stored_value))>1e-6):
        raise ValueError("FORWARD_CURRENT_INDEX_MISMATCH")

    market = conn.execute("""SELECT city,event_ticker,target_utc,threshold_f,
                              received_at,metadata
             FROM weather_hourly_markets WHERE ticker=?""",(ticker,)).fetchone()
    if market is None:
        raise ValueError("MISSING_ORIGINAL_MARKET")
    market_city,market_event,market_target,market_strike,market_received,market_raw=market
    original_market=json.loads(market_raw)
    if (market_city!=city or market_event!=event or
        _utc(market_target)!=target or _utc(market_received)>asof or
        not isinstance(original_market,dict) or
        original_market.get("source")!="KALSHI_WEATHER_INDEX" or
        original_market.get("ticker")!=ticker or
        original_market.get("event_ticker")!=event or
        original_market.get("city")!=city or
        abs(_number(market_strike,-129.,159.)-
            _number(chosen["threshold_f"],-129.,159.))>1e-6 or
        abs(_number(original_market.get("threshold_f"),-129.,159.)-
            _number(chosen["threshold_f"],-129.,159.))>1e-6):
        raise ValueError("ORIGINAL_MARKET_MISMATCH")

    return {
        "city":city,"event_ticker":event,"market_ticker":ticker,
        "target_utc":target.isoformat(),"asof":asof.isoformat(),
        "lead_bucket":bucket,"actual_lead_minutes":round(lead,3),
        "model_kind":kind,"predicted_index_f":mean,"sigma_f":sigma,
        "threshold_f":float(chosen["threshold_f"]),
        "probability_yes":float(chosen["probability_yes"]),
        "market_close_utc":closing.isoformat(),
        "market_metadata_received_at":listed_receipt.isoformat(),
        "source_config_version":cfg,
        "source_event_ms":source_event,
        "source_received_at":source_receipt.isoformat(),
        "index_source_sha":source_sha,
        "index_payload":original_raw,
        "index_payload_sha":_sha(original_raw),
        "market_metadata":market_raw,
        "market_metadata_sha":_sha(market_raw),
    }


def journal_forward(db_path: Path, *, asof: datetime | None = None,
                    forecasts: list[dict] | None = None) -> dict:
    """Write first actual-forecast checkpoint per city/event/lead, never replace."""
    now = _utc(asof or datetime.now(timezone.utc))
    path = Path(db_path)
    report = {"mode":"HOURLY_FORWARD_READ_ONLY","inserted":0,
              "skipped":0,"real_orders_enabled":False}
    if not path.is_file():
        return report
    if forecasts is None:
        from .hourly import read_hourly_forecasts
        forecasts = read_hourly_forecasts(path.parent,db_path=path,asof=now).get("forecasts",[])
    with closing(sqlite3.connect(path,timeout=10)) as conn:
        _init_forward(conn)
        for row in forecasts:
            try:
                frozen=_freeze(conn,row,now)
                raw=json.dumps(frozen,sort_keys=True,allow_nan=False)
                cur=conn.execute("""INSERT OR IGNORE INTO weather_hourly_forward_snapshots
                    (city,event_ticker,market_ticker,target_utc,lead_bucket,
                     asof,forecast_json,forecast_sha) VALUES(?,?,?,?,?,?,?,?)""",
                    (frozen["city"],frozen["event_ticker"],frozen["market_ticker"],
                     frozen["target_utc"],frozen["lead_bucket"],frozen["asof"],
                     raw,_sha(raw)))
                report["inserted"]+=cur.rowcount
            except (ValueError,KeyError,TypeError,OverflowError,sqlite3.Error,
                    json.JSONDecodeError) :
                report["skipped"]+=1
        conn.commit()
    return report


def _frozen(raw: str, digest: str) -> dict:
    from .hourly import CITIES, listed_event_target
    if _sha(raw)!=digest:
        raise ValueError("TAMPERED_FORWARD_SNAPSHOT")
    snap=json.loads(raw)
    city=snap["city"]
    if city not in CITIES:
        raise ValueError("UNKNOWN_FORWARD_CITY")
    target=_utc(snap["target_utc"])
    asof=_utc(snap["asof"])
    lead=_number(snap["actual_lead_minutes"],0.,65.)
    bucket=snap["lead_bucket"]
    if (not isinstance(bucket,int) or bucket not in LEADS or
        abs(lead-bucket)>LEAD_WINDOW_MINUTES+.001 or
        abs((target-asof).total_seconds()/60.-lead)>.002 or
        listed_event_target(city,snap["event_ticker"])!=target or
        not asof<_utc(snap["market_close_utc"]) or
        not asof<target or
        not _utc(snap["source_received_at"])<=asof):
        raise ValueError("INVALID_FROZEN_FORWARD_CHRONOLOGY")
    if _sha(snap["index_payload"])!=snap["index_payload_sha"] or (
        _sha(snap["market_metadata"])!=snap["market_metadata_sha"]):
        raise ValueError("TAMPERED_FROZEN_SOURCE")
    source=json.loads(snap["index_payload"])
    market=json.loads(snap["market_metadata"])
    if (source.get("t")!=snap["source_event_ms"] or
        not isinstance(source.get("contributors"),int) or
        source.get("contributors",0)<=0 or
        source.get("status") not in ("normal","degraded") or
        not isinstance(market,dict) or
        market.get("source")!="KALSHI_WEATHER_INDEX" or
        market.get("ticker")!=snap["market_ticker"] or
        market.get("event_ticker")!=snap["event_ticker"] or
        abs(_number(market.get("threshold_f"),-129,159)-
            _number(snap["threshold_f"],-129,159))>1e-6):
        raise ValueError("INVALID_FROZEN_FORWARD_EVIDENCE")
    _number(snap["predicted_index_f"],-129.,159.)
    _number(snap["sigma_f"],.001,30.)
    _number(snap["probability_yes"],0.,1.)
    if not isinstance(snap["model_kind"],str) or not snap["model_kind"]:
        raise ValueError("INVALID_FROZEN_MODEL")
    return snap


async def settle_forward_once(db_path: Path, *, client: httpx.AsyncClient | None = None,
                              clock: Callable[[],datetime] | None = None) -> dict:
    """Reconcile exact top-hour index and exact precommitted market result."""
    from .hourly import API, MAX_SIZE, parse_index, parse_markets
    now=_utc((clock or (lambda:datetime.now(timezone.utc)))())
    path=Path(db_path)
    result={"mode":"HOURLY_FORWARD_READ_ONLY","verified":0,
            "unverified":0,"real_orders_enabled":False}
    if not path.is_file():
        return result
    with closing(sqlite3.connect(path.resolve().as_uri()+"?mode=ro",
                                uri=True,timeout=3)) as conn:
        try:
            rows=conn.execute("""SELECT s.city,s.event_ticker,s.market_ticker,
                    s.target_utc,s.forecast_json,s.forecast_sha
                FROM weather_hourly_forward_snapshots s
                LEFT JOIN weather_hourly_forward_outcomes o
                  ON s.city=o.city AND s.event_ticker=o.event_ticker
                   AND s.market_ticker=o.market_ticker
                WHERE o.city IS NULL AND s.target_utc>=? AND s.target_utc<=?
                ORDER BY s.target_utc DESC LIMIT 250""",
                ((now-timedelta(days=7)).isoformat(),
                 (now-timedelta(minutes=6)).isoformat())).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                return result
            raise
    pending={}
    for city,event,ticker,target_raw,raw,digest in rows:
        try:
            frozen=_frozen(raw,digest)
            if (city!=frozen["city"] or event!=frozen["event_ticker"] or
                ticker!=frozen["market_ticker"] or
                target_raw!=frozen["target_utc"]):
                raise ValueError("FORWARD_SNAPSHOT_IDENTITY_CHANGED")
            pending.setdefault((city,event,ticker,target_raw),frozen)
        except (ValueError,TypeError,KeyError,json.JSONDecodeError):
            result["unverified"]+=1
    own=client is None
    if own:
        client=httpx.AsyncClient(timeout=25,follow_redirects=False,
            headers={"User-Agent":"KalshiEdge-ForwardWeather-Audit/1.0"})
    assert client is not None
    try:
        for (city,event,ticker,target_raw),frozen in pending.items():
            target=_utc(target_raw)
            try:
                start_ms=int((target-timedelta(minutes=60)).timestamp()*1000)
                end_ms=int(target.timestamp()*1000)
                source=await client.get(f"{API}/live_data/weather/{city}",
                                        params={"from":start_ms,"to":end_ms})
                source.raise_for_status()
                if len(source.content)>MAX_SIZE:
                    raise ValueError("OVERSIZED_FORWARD_INDEX")
                index_json=source.json()
                points=parse_index(index_json,city,now,
                                    max_age_seconds=8*24*3600)
                exact=[p for p in points if p["event_ms"]==end_ms and
                       p["status"]=="normal" and
                       p["config_version"]==frozen["source_config_version"]]
                if len(exact)!=1:
                    raise ValueError("NO_EXACT_HOURLY_INDEX")
                observed=exact[0]
                reply=await client.get(f"{API}/markets",
                    params={"event_ticker":event,"limit":200})
                reply.raise_for_status()
                if len(reply.content)>MAX_SIZE:
                    raise ValueError("OVERSIZED_FORWARD_SETTLEMENT")
                market_json=reply.json()
                matches=[m for m in parse_markets(market_json,city,event,settled=True)
                         if m["ticker"]==ticker and
                         abs(m["threshold_f"]-frozen["threshold_f"])<=1e-6]
                if len(matches)!=1 or matches[0].get("result") not in ("yes","no"):
                    raise ValueError("MISSING_EXACT_SETTLED_MARKET")
                match=matches[0]
                settlement=_utc(match["settlement_ts"])
                if not target<=settlement<=now:
                    raise ValueError("INVALID_OFFICIAL_SETTLEMENT_TIME")
                if (observed["temp_f"]>=frozen["threshold_f"]) != (match["result"]=="yes"):
                    raise ValueError("HOURLY_INDEX_MARKET_CONFLICT")
                original_index=source.content.decode("utf-8")
                original_market=reply.content.decode("utf-8")
                with closing(sqlite3.connect(path,timeout=10)) as conn:
                    _init_forward(conn)
                    cur=conn.execute("""INSERT OR IGNORE INTO weather_hourly_forward_outcomes
                        (city,event_ticker,market_ticker,result,observed_index_f,
                         observed_event_ms,checked_at,index_config_version,
                         source_index_raw,source_index_sha,market_raw,market_sha)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (city,event,ticker,match["result"],observed["temp_f"],
                         observed["event_ms"],now.isoformat(),observed["config_version"],
                         original_index,_sha(original_index),original_market,
                         _sha(original_market)))
                    conn.commit()
                    result["verified"]+=cur.rowcount
            except (httpx.HTTPError,ValueError,TypeError,OverflowError,
                    KeyError,sqlite3.Error,json.JSONDecodeError,UnicodeError) as exc:
                result["unverified"]+=1
                result.setdefault("errors",[]).append({
                    "event":event,"reason":f"{type(exc).__name__}: {exc}"[:160]})
    finally:
        if own:
            await client.aclose()
    return result


def _outcome(row: tuple, frozen: dict) -> tuple[int,float] | None:
    from .hourly import parse_index, parse_markets
    (result,value,event_ms,checked_at,config,index_raw,index_sha,
     market_raw,market_sha)=row
    if _sha(index_raw)!=index_sha or _sha(market_raw)!=market_sha:
        raise ValueError("TAMPERED_FORWARD_SETTLEMENT")
    checked=_utc(checked_at)
    target=_utc(frozen["target_utc"])
    if not target+timedelta(minutes=6)<=checked<=target+timedelta(days=7):
        raise ValueError("INVALID_FORWARD_CHECK_TIME")
    points=parse_index(json.loads(index_raw),frozen["city"],checked,
                       max_age_seconds=8*24*3600)
    exact=[p for p in points if p["event_ms"]==int(target.timestamp()*1000)
           and p["status"]=="normal" and p["config_version"]==config]
    if len(exact)!=1 or (config!=frozen["source_config_version"]):
        return None
    canonical=exact[0]
    if canonical["event_ms"]!=event_ms or abs(canonical["temp_f"]-value)>1e-6:
        raise ValueError("CHANGED_FORWARD_INDEX_OUTCOME")
    matching=[m for m in parse_markets(json.loads(market_raw),frozen["city"],
                    frozen["event_ticker"],settled=True)
             if m["ticker"]==frozen["market_ticker"]]
    if (len(matching)!=1 or matching[0].get("result")!=result or
        abs(matching[0]["threshold_f"]-frozen["threshold_f"])>1e-6 or
        (canonical["temp_f"]>=frozen["threshold_f"])!=(result=="yes")):
        raise ValueError("CHANGED_FORWARD_MARKET_OUTCOME")
    settlement=_utc(matching[0]["settlement_ts"])
    if not target<=settlement<=checked:
        raise ValueError("INVALID_FORWARD_SETTLEMENT_TIME")
    return (1 if result=="yes" else 0,float(value))


def forward_accuracy(root: Path, *, db_path: Path | None = None,
                     preview: bool = False) -> dict:
    """Read-only, source-audited per-city/per-lead/per-model proper scores."""
    report={"mode":"HOURLY_FORWARD_READ_ONLY","status":"NO_FROZEN_FORECASTS",
            "total_frozen":0,"total_verified":0,"groups":[],
            "calibration_certified":False,"real_orders_enabled":False}
    path=Path(db_path or weather_database_path(root))
    if preview or not path.is_file():
        return report
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+"?mode=ro",
                                    uri=True,timeout=3)) as conn:
            conn.execute("PRAGMA query_only=ON")
            try:
                records=conn.execute("""SELECT
                    s.city,s.event_ticker,s.market_ticker,s.target_utc,
                    s.lead_bucket,s.asof,s.forecast_json,s.forecast_sha,
                    o.result,o.observed_index_f,o.observed_event_ms,o.checked_at,
                    o.index_config_version,o.source_index_raw,o.source_index_sha,
                    o.market_raw,o.market_sha
                FROM weather_hourly_forward_snapshots s
                LEFT JOIN weather_hourly_forward_outcomes o
                  ON s.city=o.city AND s.event_ticker=o.event_ticker
                   AND s.market_ticker=o.market_ticker
                ORDER BY s.city,s.lead_bucket,s.target_utc
                LIMIT 30000""").fetchall()
            except sqlite3.OperationalError as exc:
                if "no such table" in str(exc):
                    return report
                raise
        cohorts=defaultdict(lambda:{"events":set(),"frozen":0,
                                   "verified":0,"errors":[],"probabilities":[]})
        for row in records:
            (city,event,ticker,target,lead,asof,raw,digest,
             settled,value,event_ms,checked_at,config,index_raw,index_sha,
             market_raw,market_sha)=row
            frozen=_frozen(raw,digest)
            if (city!=frozen["city"] or event!=frozen["event_ticker"] or
                ticker!=frozen["market_ticker"] or target!=frozen["target_utc"] or
                lead!=frozen["lead_bucket"] or asof!=frozen["asof"]):
                raise ValueError("CHANGED_FORWARD_SNAPSHOT_IDENTITY")
            key=(city,lead,frozen["model_kind"])
            cohort=cohorts[key]
            if event in cohort["events"]:
                raise ValueError("DUPLICATE_FORWARD_COHORT_EVENT")
            cohort["events"].add(event)
            cohort["frozen"]+=1
            if settled is None:
                continue
            checked_row=(settled,value,event_ms,checked_at,config,index_raw,
                         index_sha,market_raw,market_sha)
            observed=_outcome(checked_row,frozen)
            if observed is None:
                continue  # Calibration configuration changed before target.
            y,actual=observed
            cohort["verified"]+=1
            cohort["errors"].append((abs(actual-frozen["predicted_index_f"]),
                (frozen["probability_yes"]-y)**2,
                -math.log(max(1e-15,frozen["probability_yes"] if y else
                              1.-frozen["probability_yes"])),
                int(abs(actual-frozen["predicted_index_f"])<=Z90*frozen["sigma_f"])))
        report["total_frozen"]=len(records)
        for (city,lead,model),cohort in sorted(cohorts.items()):
            n=cohort["verified"]
            entry={"city":city,"lead_bucket":lead,"model_kind":model,
                   "frozen":cohort["frozen"],"verified_hours":n,
                   "pending":cohort["frozen"]-n,
                   "mae_f":None,"brier":None,"log_loss":None,
                   "coverage_90":None,
                   "status":"INSUFFICIENT_VERIFIED_EVIDENCE"}
            if n>=MIN_VERIFIED_HOURS:
                errors=cohort["errors"]
                entry.update({"mae_f":round(sum(e[0] for e in errors)/n,6),
                              "brier":round(sum(e[1] for e in errors)/n,6),
                              "log_loss":round(sum(e[2] for e in errors)/n,6),
                              "coverage_90":round(sum(e[3] for e in errors)/n,6),
                              "status":"MEASURED_PROSPECTIVE_RESEARCH"})
            report["total_verified"]+=n
            report["groups"].append(entry)
        if report["total_frozen"]:
            report["status"]=(
                "MEASURED_PROSPECTIVE_RESEARCH" if any(
                    g["status"]=="MEASURED_PROSPECTIVE_RESEARCH" for g in report["groups"])
                else "INSUFFICIENT_VERIFIED_EVIDENCE" if report["total_verified"] else
                "COLLECTING_PENDING_VERIFIED_OUTCOMES")
    except (sqlite3.Error,OSError,ValueError,KeyError,TypeError,
            OverflowError,json.JSONDecodeError) as exc:
        report.update({"status":"HOURLY_FORWARD_AUDIT_ERROR",
                       "reason":f"{type(exc).__name__}: {exc}"[:180],
                       "groups":[],"total_verified":0,"total_frozen":0})
    return report
