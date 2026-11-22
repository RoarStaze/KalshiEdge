"""Forward-only, append-only 15-minute airport-proxy forecast journal.

No official Kalshi temperature labels are derived or manufactured here.
A later matching METAR is an observation proxy only; serially correlated
15-minute samples cannot establish independent contract profitability.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .predictor import STATIONS, _time, _utc, _finite
from .short_horizon import read_quarter_hour_forecast

WINDOW_SECONDS = 300
MAX_MATCH_DISTANCE = timedelta(minutes=2,seconds=30)
LABEL_DELAY = timedelta(minutes=10)


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS weather_15m_snapshots("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "station TEXT NOT NULL,"
        "window_start TEXT NOT NULL,"
        "asof TEXT NOT NULL,"
        "target_at TEXT NOT NULL,"
        "forecast_json TEXT NOT NULL,"
        "payload_sha256 TEXT NOT NULL,"
        "UNIQUE(station,window_start))"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS weather_15m_snapshots_station "
        "ON weather_15m_snapshots(station,asof)"
    )


def capture_quarter_hour_snapshots(db_path: Path, *,
                                   asof: datetime | None = None,
                                   stations: tuple[str,...] = tuple(STATIONS)) -> dict:
    """Record the first forecast seen per station and real five-minute window."""
    asof=_utc(asof or datetime.now(timezone.utc))
    if not stations or len(set(stations))!=len(stations) or any(s not in STATIONS for s in stations):
        raise ValueError("unsupported or duplicate airport")
    result={"mode":"PAPER_ONLY","inserted":0,"statuses":{},"asof":asof.isoformat()}
    if not Path(db_path).is_file():
        result["statuses"]={s:"MISSING_DATABASE" for s in stations}
        return result
    window=datetime.fromtimestamp(
        int(asof.timestamp())//WINDOW_SECONDS*WINDOW_SECONDS,tz=timezone.utc)
    proposals=[]
    for station in stations:
        forecast=read_quarter_hour_forecast(db_path,station,asof)
        result["statuses"][station]=forecast["status"]
        if forecast["status"]!="READY_RESEARCH":
            continue
        if forecast["tradable"] or forecast["settlement_verified"]:
            raise ValueError("15m research forecast must never become a trade")
        if (forecast["asof"]!=asof.isoformat() or
            _time(forecast["target_at"])!=asof+timedelta(minutes=15)):
            raise ValueError("forecast horizon/timestamp mismatch")
        receipts=[forecast.get("metar_received_at"), *forecast.get("nwp_receipts_utc",{}).values()]
        if len(receipts)!=3 or any(
            not isinstance(x,str) or _time(x)>asof for x in receipts
        ):
            raise ValueError("future or missing receipt in 15m forecast")
        encoded=json.dumps(forecast,sort_keys=True,allow_nan=False,separators=(",",":"))
        proposals.append((
            station,window.isoformat(),asof.isoformat(),
            forecast["target_at"],encoded,hashlib.sha256(encoded.encode()).hexdigest(),
        ))
    if not proposals:
        return result
    uri=Path(db_path).resolve().as_uri()+"?mode=rw"
    with closing(sqlite3.connect(uri,uri=True,timeout=5)) as conn:
        conn.execute("PRAGMA busy_timeout=5000")
        _ensure_table(conn)
        for row in proposals:
            cursor=conn.execute(
                "INSERT OR IGNORE INTO weather_15m_snapshots("
                "station,window_start,asof,target_at,forecast_json,payload_sha256)"
                " VALUES(?,?,?,?,?,?)",row)
            result["inserted"]+=int(cursor.rowcount)
        conn.commit()
    return result


def score_quarter_hour_snapshots(
    db_path: Path, *, station: str | None = None,
    asof: datetime | None = None,
) -> dict:
    """Score frozen outlooks against later METAR *observation proxies* only.

    Do not call these Kalshi settlement labels. Evaluation is descriptive,
    windows within and across nearby stations are highly correlated, and
    headline 90% coverage cannot be treated as a certified 90% interval.
    """
    if station is not None and station not in STATIONS:
        raise ValueError("unknown station")
    asof=_utc(asof or datetime.now(timezone.utc))
    result={
        "mode":"PAPER_ONLY",
        "label_kind":"AIRPORT_METAR_PROXY_NOT_MARKET_SETTLEMENT",
        "matched_forecasts":0,"recorded_forecasts":0,
        "mae_f":None,"persistence_baseline_mae_f":None,
        "interval_90_observed_coverage":None,
        "independently_calibrated":False,
        "serially_correlated_windows":True,
    }
    if not Path(db_path).is_file():
        return result
    uri=Path(db_path).resolve().as_uri()+"?mode=ro"
    with closing(sqlite3.connect(uri,uri=True,timeout=1.5)) as conn:
        conn.execute("PRAGMA query_only=ON")
        sql=("SELECT station,asof,target_at,forecast_json,payload_sha256 "
             "FROM weather_15m_snapshots")
        parameters=()
        if station is not None:
            sql+=" WHERE station=?"
            parameters=(station,)
        sql+=" ORDER BY asof DESC LIMIT 2000"
        try:
            snapshots=conn.execute(sql,parameters).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                return result
            raise
        result["recorded_forecasts"]=len(snapshots)
        abs_err=[]
        pers_err=[]
        covered=0
        for code,start_raw,target_raw,raw,digest in snapshots:
            if hashlib.sha256(raw.encode()).hexdigest()!=digest:
                raise ValueError("15m forecast journal integrity error")
            frozen=json.loads(raw)
            target=_time(target_raw)
            start=_time(start_raw)
            if (frozen.get("station")!=code or frozen.get("asof")!=start.isoformat()
                or frozen.get("target_at")!=target.isoformat()
                or target-start!=timedelta(minutes=15)):
                raise ValueError("15m forecast journal metadata mismatch")
            if target+LABEL_DELAY>asof:
                continue
            if frozen.get("status")!="READY_RESEARCH":
                continue
            lo=(target-MAX_MATCH_DISTANCE).isoformat()
            hi=(target+MAX_MATCH_DISTANCE).isoformat()
            try:
                candidates=conn.execute(
                    "SELECT event_time,received_at,payload FROM samples "
                    "WHERE station=? AND source='METAR' AND event_time>=? "
                    "AND event_time<=? AND received_at<=? "
                    "ORDER BY event_time,received_at LIMIT 100",
                    (code,lo,hi,asof.isoformat()),
                ).fetchall()
            except sqlite3.OperationalError:
                continue
            eligible=[]
            for event_raw,received_raw,payload in candidates:
                try:
                    event,receipt=_time(event_raw),_time(received_raw)
                    if receipt<event or event<=start or receipt>asof:
                        continue
                    obj=json.loads(payload)
                    value=_finite(obj["temp_f"],-130,150)
                except (ValueError,TypeError,KeyError,json.JSONDecodeError):
                    continue
                eligible.append((abs((event-target).total_seconds()),receipt,event,value))
            if not eligible:
                continue
            _,_,_,actual=min(eligible)
            point=_finite(frozen["mean_f"],-130,150)
            old=_finite(frozen["latest_observed_f"],-130,150)
            interval=frozen["interval_90_f"]
            if (not isinstance(interval,list) or len(interval)!=2 or
                not float(interval[0])<=point<=float(interval[1])):
                raise ValueError("invalid 15m forecast interval")
            abs_err.append(abs(actual-point))
            pers_err.append(abs(actual-old))
            covered+=int(float(interval[0])<=actual<=float(interval[1]))
        if abs_err:
            result.update({
                "matched_forecasts":len(abs_err),
                "mae_f":round(sum(abs_err)/len(abs_err),5),
                "persistence_baseline_mae_f":round(sum(pers_err)/len(abs_err),5),
                "interval_90_observed_coverage":round(covered/len(abs_err),5),
            })
    return result
