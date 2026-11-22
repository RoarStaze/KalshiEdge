"""Kalshi hourly-index physical guidance from exact public calibration history.

NOAA forecasts are *not* the HF-ASOS observations used for settlement.
This module uses Kalshi's published station weights/offsets verbatim, samples
NOAA 2-m grid forecasts at AWC ICAO airport-coordinate proxies, and stores
the result as research guidance only. It never claims canonical settlement,
calibrated probabilities, automatic promotion or a profitable bet.
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
from typing import Awaitable, Callable

import httpx

from .nwp_collection import (nwp_url, fetch_grib_temperature,
                             decode_station_temperatures)

API = "https://external-api.kalshi.com/trade-api/v2"
AWC = "https://aviationweather.gov/api/data/stationinfo"
MAX_RESPONSE = 4_000_000
_ALLOWED_CITIES = frozenset(("chicago", "nyc", "miami", "la-coastal"))


def _utc(value: datetime | str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value,str) else value
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("MISSING_UTC_OFFSET")
    return dt.astimezone(timezone.utc)


def _milliseconds(value: object) -> int:
    if isinstance(value,bool) or not isinstance(value,int) or not 1_500_000_000_000 < value < 4_200_000_000_000:
        raise ValueError("INVALID_CALIBRATION_TIMESTAMP")
    return value


def _numeric(value: object, lower: float, upper: float) -> float:
    if isinstance(value,bool) or not isinstance(value,(int,float)):
        raise ValueError("INVALID_CALIBRATION_NUMBER")
    x=float(value)
    if not math.isfinite(x) or not lower <= x <= upper:
        raise ValueError("INVALID_CALIBRATION_NUMBER")
    return x


def _canonical(raw: object) -> str:
    return json.dumps(raw,sort_keys=True,allow_nan=False,separators=(",",":"))


def parse_calibrations(payload: dict, city: str) -> list[dict]:
    """Strictly parse Kalshi's official celsius calibration timeline schema."""
    if city not in _ALLOWED_CITIES or not isinstance(payload,dict) or payload.get("city")!=city or payload.get("units")!="celsius":
        raise ValueError("INVALID_KALSHI_CALIBRATION_SOURCE")
    raw=payload.get("calibrations")
    if not isinstance(raw,list) or not 1<=len(raw)<=1000:
        raise ValueError("MISSING_KALSHI_CALIBRATION_TIMELINE")
    output=[]
    seen_versions=set()
    prev=-1
    for entry in raw:
        if not isinstance(entry,dict):
            raise ValueError("INVALID_CALIBRATION_ENTRY")
        version=entry.get("config_version")
        if not isinstance(version,str) or not 1<=len(version)<=120 or version in seen_versions:
            raise ValueError("INVALID_OR_DUPLICATE_CONFIG_VERSION")
        effective=_milliseconds(entry.get("effective_at_ms"))
        published=_milliseconds(entry.get("published_at_ms"))
        if effective<=prev:
            raise ValueError("NONMONOTONIC_CALIBRATION_TIMELINE")
        prev=effective
        seen_versions.add(version)
        raw_stations=entry.get("stations")
        if not isinstance(raw_stations,list) or not 3<=len(raw_stations)<=20:
            raise ValueError("INVALID_STATION_MEMBERSHIP")
        stations=[]
        station_ids=set()
        for member in raw_stations:
            if not isinstance(member,dict):
                raise ValueError("INVALID_STATION_MEMBER")
            sid=member.get("station_id")
            if not isinstance(sid,str) or re.fullmatch(r"K[A-Z0-9]{3}1M",sid) is None or sid in station_ids:
                raise ValueError("DUPLICATE_OR_INVALID_STATION_ID")
            station_ids.add(sid)
            stations.append({
                "station_id":sid,
                "weight":_numeric(member.get("weight"),1e-7,1.0),
                "offset_c":_numeric(member.get("offset_c"),-30,30),
            })
        if abs(sum(m["weight"] for m in stations)-1.0)>1e-5:
            raise ValueError("WEIGHTS_DO_NOT_SUM_TO_ONE")
        reference=_numeric(entry.get("city_reference_c"),-30,30)
        actual=sum(m["weight"]*m["offset_c"] for m in stations)
        if abs(reference-actual)>1e-3:
            raise ValueError("CITY_REFERENCE_MISMATCH")
        config={"city":city,"version":version,
                "effective_at_ms":effective,"published_at_ms":published,
                "city_reference_c":reference,"stations":stations,
                "source":"KALSHI_PUBLIC_CALIBRATIONS"}
        config["config_sha256"]=hashlib.sha256(_canonical(config).encode()).hexdigest()
        output.append(config)
    return output


def select_calibration(timeline: list[dict], target: datetime,
                       asof: datetime) -> dict | None:
    """Use only the version effective for event time and already published."""
    ts=int(_utc(target).timestamp()*1000)
    known=int(_utc(asof).timestamp()*1000)
    matching=[item for item in timeline if item["effective_at_ms"]<=ts]
    if not matching:
        return None
    chosen=max(matching,key=lambda item:item["effective_at_ms"])
    # Do not silently use an obsolete version if an effective update is known.
    if chosen["published_at_ms"]>known:
        return None
    return chosen


def aggregate_index_celsius(config: dict, temperatures_c: dict[str,float]) -> float:
    """Kalshi methodology weighted z_i=x_i-b_i+B_c; no missing-grid imputation."""
    stations=config["stations"]
    required={m["station_id"] for m in stations}
    if set(temperatures_c)!=required:
        # Without actual source QC and known per-minute availability, we
        # require all model grid points rather than guessing a contributor set.
        raise ValueError("INCOMPLETE_FORECAST_STATION_MEMBERSHIP")
    reference=float(config["city_reference_c"])
    numerator=0.0
    total=0.0
    for m in stations:
        x=_numeric(temperatures_c[m["station_id"]],-90,80)
        w=m["weight"]
        numerator+=w*(x-m["offset_c"]+reference)
        total+=w
    if abs(total-1.0)>1e-5:
        raise ValueError("INVALID_TOTAL_STATION_WEIGHT")
    return numerator/total


def parse_station_coordinates(payload: object,
                              configs: list[dict]) -> dict[str,tuple[float,float]]:
    """Match listed 1M IDs to AWC's exact ICAO IDs (grid-location proxy only)."""
    if isinstance(payload,dict):
        payload=payload.get("stations",payload.get("data"))
    if not isinstance(payload,list):
        raise ValueError("INVALID_AWC_STATION_RESPONSE")
    required={member["station_id"] for c in configs for member in c["stations"]}
    needed_icao={sid[:-2] for sid in required}
    found={}
    for row in payload:
        if not isinstance(row,dict):
            continue
        code=row.get("icaoId")
        if code not in needed_icao:
            continue
        if code in found:
            raise ValueError("DUPLICATE_AWC_STATION_ID")
        latitude=_numeric(row.get("lat"),-90,90)
        longitude=_numeric(row.get("lon"),-180,180)
        if not (20<latitude<60 and -130<longitude<-60):
            raise ValueError("UNEXPECTED_AWC_COORDINATE_REGION")
        found[code]=(latitude,longitude)
    if set(found)!=needed_icao:
        raise ValueError("MISSING_STATION_COORDINATE")
    return {sid:found[sid[:-2]] for sid in required}


def _init(conn: sqlite3.Connection) -> None:
    # Independent workers may start in either order on first boot.
    # Initialize the official hourly tables as well so read-only forecast
    # endpoints never encounter a half-initialized weather database.
    from .hourly import _init as init_hourly
    init_hourly(conn)
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("""CREATE TABLE IF NOT EXISTS weather_hourly_physics_calibrations(
        city TEXT NOT NULL,config_version TEXT NOT NULL,
        effective_at_ms INTEGER NOT NULL,published_at_ms INTEGER NOT NULL,
        config_json TEXT NOT NULL,config_sha256 TEXT NOT NULL,
        original_response_sha256 TEXT NOT NULL,received_at TEXT NOT NULL,
        PRIMARY KEY(city,config_version))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS weather_hourly_physics_forecasts(
        city TEXT NOT NULL,target_utc TEXT NOT NULL,model TEXT NOT NULL,
        model_run_at TEXT NOT NULL,received_at TEXT NOT NULL,
        config_version TEXT NOT NULL,config_sha256 TEXT NOT NULL,
        index_f REAL NOT NULL,nwp_field_sha256 TEXT NOT NULL,
        sampled_station_kelvin_json TEXT NOT NULL,coordinate_source TEXT NOT NULL,
        PRIMARY KEY(city,target_utc,model,model_run_at,received_at))""")
    conn.execute("""CREATE INDEX IF NOT EXISTS hourly_physics_lookup
        ON weather_hourly_physics_forecasts(city,target_utc,received_at)""")


async def _default_grib_fetch(client: httpx.AsyncClient, model: str,
                              run: datetime, hour: int,
                              coords: dict[str,tuple[float,float]]
                              ) -> tuple[dict[str,float],str]:
    data=await fetch_grib_temperature(client,nwp_url(model,run,hour))
    samples=await asyncio.to_thread(
        decode_station_temperatures,data,coords,
        expected_run=run,expected_valid_at=run+timedelta(hours=hour))
    return samples,hashlib.sha256(data).hexdigest()


def _store_calibration_history(db_path: Path, configs: dict,
                               responses: dict, now: datetime) -> None:
    """Append-only versioned Kalshi records, independent of NOAA availability."""
    path=Path(db_path)
    path.parent.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(path,timeout=10)) as conn:
        _init(conn)
        for city,(timeline,_) in configs.items():
            for entry in timeline:
                serialized=_canonical(entry)
                row=conn.execute(
                    "SELECT config_sha256 FROM weather_hourly_physics_calibrations "
                    "WHERE city=? AND config_version=?",
                    (city,entry["version"])).fetchone()
                if row and row[0]!=entry["config_sha256"]:
                    raise ValueError("IMMUTABLE_KALSHI_CALIBRATION_CHANGED")
                conn.execute("""INSERT OR IGNORE INTO weather_hourly_physics_calibrations
                   (city,config_version,effective_at_ms,published_at_ms,
                   config_json,config_sha256,original_response_sha256,received_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                   (city,entry["version"],entry["effective_at_ms"],
                    entry["published_at_ms"],serialized,entry["config_sha256"],
                    responses[city],now.isoformat()))
        conn.commit()


async def collect_hourly_physics_once(
    db_path: Path, *,
    client: httpx.AsyncClient | None=None,
    cities: tuple[str,...]=("chicago","nyc","miami","la-coastal"),
    clock: Callable[[],datetime] | None=None,
    grib_fetch: Callable[[httpx.AsyncClient,str,datetime,int,dict],
                         Awaitable[tuple[dict[str,float],str]]] | None=None,
) -> dict:
    """Fetch authoritative config + AWC grid points + both NOAA forecasts.

    Missing sources fail closed; no synthetic filler, live orders or model
    activation. All source bytes and local receipts are provenance-stamped.
    """
    if not cities or any(c not in _ALLOWED_CITIES for c in cities):
        raise ValueError("INVALID_CITY_LIST")
    now=_utc((clock or (lambda:datetime.now(timezone.utc)))())
    target=now.replace(minute=0,second=0,microsecond=0)+timedelta(hours=1)
    if (target-now).total_seconds()<=0:
        raise ValueError("INVALID_TARGET_TIME")
    own=client is None
    if own:
        client=httpx.AsyncClient(timeout=60,follow_redirects=False,
            headers={"User-Agent":"KalshiEdge-Weather-Physical-Research/1.0"})
    assert client is not None
    fetcher=grib_fetch or _default_grib_fetch
    result={"mode":"HOURLY_PHYSICAL_RESEARCH","real_orders_enabled":False,
            "cities":{},"target_utc":target.isoformat(),
            "model_status":{}}
    configs={}
    responses={}
    try:
        for city in cities:
            try:
                response=await client.get(f"{API}/live_data/weather/{city}/calibrations")
                response.raise_for_status()
                if len(response.content)>MAX_RESPONSE:
                    raise ValueError("OVERSIZED_CALIBRATIONS")
                timeline=parse_calibrations(response.json(),city)
                chosen=select_calibration(timeline,target,now)
                if chosen is None:
                    raise ValueError("NO_PUBLISHED_CONFIG_EFFECTIVE_AT_TARGET")
                configs[city]=(timeline,chosen)
                responses[city]=hashlib.sha256(response.content).hexdigest()
            except (httpx.HTTPError,ValueError,TypeError,KeyError,
                    OverflowError,json.JSONDecodeError) as exc:
                result["cities"][city]={"state":"UNAVAILABLE",
                    "reason":f"{type(exc).__name__}: {exc}"[:190]}
        if not configs:
            return result
        # Preserve actual Kalshi calibration history even if AWC/NOAA fails.
        _store_calibration_history(db_path,configs,responses,now)

        # One station metadata query and one GRIB decode per model for all
        # active city members, rather than 2*N cities repeated GRIB fetches.
        try:
            all_configs=[chosen for _,chosen in configs.values()]
            ids=sorted({m["station_id"][:-2] for cfg in all_configs for m in cfg["stations"]})
            meta=await client.get(AWC,params={"ids":",".join(ids),"format":"json"})
            meta.raise_for_status()
            if len(meta.content)>MAX_RESPONSE:
                raise ValueError("OVERSIZED_AWC_METADATA")
            coords=parse_station_coordinates(meta.json(),all_configs)
        except (httpx.HTTPError,ValueError,TypeError,KeyError,json.JSONDecodeError) as exc:
            for city in configs:
                result["cities"][city]={"state":"UNAVAILABLE",
                    "reason":f"{type(exc).__name__}: {exc}"[:190]}
            return result

        sampled={}
        for model in ("HRRR","NBM"):
            error="NO_PUBLISHED_NOAA_CYCLE"
            for hours_ago in (1,2,3):
                run=now.replace(minute=0,second=0,microsecond=0)-timedelta(hours=hours_ago)
                delta=(target-run).total_seconds()/3600
                if not delta.is_integer() or not 1<=delta<=48:
                    continue
                try:
                    values,sha=await fetcher(client,model,run,int(delta),coords)
                    receipt=_utc((clock or (lambda:datetime.now(timezone.utc)))())
                    if (not isinstance(values,dict) or set(values)!=set(coords) or
                        not isinstance(sha,str) or re.fullmatch(r"[0-9a-f]{64}",sha) is None or
                        receipt<run or receipt>=target):
                        raise ValueError("INVALID_NOAA_GRID_OR_RECEIPT")
                    for val in values.values():
                        _numeric(val,150,350)
                    sampled[model]=(values,sha,run,receipt)
                    break
                except (httpx.HTTPError,ValueError,TypeError,KeyError,
                        OverflowError,OSError,ImportError) as exc:
                    error=f"{type(exc).__name__}: {exc}"[:160]
            result["model_status"][model]="READY" if model in sampled else error

        to_write=[]
        for city,(_,cfg) in configs.items():
            forecasts={}
            for model,(values,sha,run,receipt) in sampled.items():
                station_kelvin={m["station_id"]:values[m["station_id"]]
                                 for m in cfg["stations"]}
                try:
                    converted={key:value-273.15 for key,value in station_kelvin.items()}
                    index_c=aggregate_index_celsius(cfg,converted)
                    index_f=32+index_c*1.8
                    if not -130<index_f<160:
                        raise ValueError("IMPLAUSIBLE_FORECAST_INDEX")
                    forecasts[model]=(index_f,sha,run,receipt,station_kelvin)
                except (ValueError,TypeError,KeyError) as exc:
                    result["model_status"][model]=f"INVALID_CITY_GRID:{city}:{exc}"
            result["cities"][city]={
                "state":"RESEARCH_READY" if len(forecasts)==2 else "PARTIAL_RESEARCH",
                "configuration":cfg["version"],
                "member_count":len(cfg["stations"]),
                "models":sorted(forecasts),
                "forecast_target_utc":target.isoformat(),
                "coordinate_provenance":"AWC_ICAO_AIRPORT_PROXY_NOT_HF_SENSOR_LOCATION",
                "real_orders_enabled":False,
            }
            for model,(value,sha,run,receipt,stations) in forecasts.items():
                to_write.append((city,target.isoformat(),model,run.isoformat(),
                    receipt.isoformat(),cfg["version"],cfg["config_sha256"],
                    value,sha,_canonical(stations),
                    "AWC_ICAO_AIRPORT_PROXY_NOT_HF_SENSOR_LOCATION"))

        path=Path(db_path)
        path.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(path,timeout=10)) as conn:
            _init(conn)
            conn.executemany("""INSERT OR IGNORE INTO weather_hourly_physics_forecasts
               (city,target_utc,model,model_run_at,received_at,config_version,
               config_sha256,index_f,nwp_field_sha256,sampled_station_kelvin_json,
               coordinate_source) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",to_write)
            conn.commit()
        return result
    finally:
        if own:
            await client.aclose()


def read_hourly_physics(conn: sqlite3.Connection, city: str,
                        target: datetime, asof: datetime) -> dict:
    """Read stamped station-index forecasts; never treat as trained probabilities."""
    output={"status":"NO_PHYSICAL_GUIDANCE","model_count":0,
            "real_orders_enabled":False,
            "coordinate_provenance":"AWC_ICAO_AIRPORT_PROXY_NOT_HF_SENSOR_LOCATION"}
    if city not in _ALLOWED_CITIES:
        return output
    target=_utc(target)
    asof=_utc(asof)
    try:
        # A later published configuration effective at this target makes
        # even a previously valid physical projection obsolete.
        active=conn.execute("""SELECT config_version,config_sha256
            FROM weather_hourly_physics_calibrations
            WHERE city=? AND effective_at_ms<=? AND published_at_ms<=?
            ORDER BY effective_at_ms DESC LIMIT 1""",
            (city,int(target.timestamp()*1000),int(asof.timestamp()*1000))).fetchone()
        if active is None:
            return output
        _init_read_rows=conn.execute("""SELECT model,index_f,config_version,
              config_sha256,received_at,model_run_at FROM weather_hourly_physics_forecasts
              WHERE city=? AND target_utc=? AND received_at<=? AND received_at<?
              ORDER BY received_at DESC""",
              (city,target.isoformat(),asof.isoformat(),target.isoformat())).fetchall()
        selected={}
        model_receipts={}
        model_runs={}
        version=None
        digest=None
        for model,value,cfg,sha,received,run in _init_read_rows:
            if model not in ("HRRR","NBM") or model in selected:
                continue
            if (asof-_utc(received))>timedelta(hours=3):
                continue
            config=conn.execute("""SELECT config_json FROM weather_hourly_physics_calibrations
                WHERE city=? AND config_version=? AND config_sha256=?""",
                (city,cfg,sha)).fetchone()
            if not config:
                continue
            effective=json.loads(config[0])
            # Verify stored source-derived configuration bytes before using
            # them to label a NOAA forecast as Kalshi-calibrated.
            embedded_sha=effective.pop("config_sha256",None)
            if (embedded_sha!=sha or
                hashlib.sha256(_canonical(effective).encode()).hexdigest()!=sha):
                continue
            if (effective["effective_at_ms"]>int(target.timestamp()*1000) or
                effective["published_at_ms"]>int(_utc(received).timestamp()*1000)):
                continue
            if version is None:
                version,digest=cfg,sha
            if cfg!=version or sha!=digest or (cfg,sha)!=tuple(active):
                continue
            if not (isinstance(value,(int,float)) and math.isfinite(value)):
                continue
            # Retain actual provider model cycle and *local* receipt times
            # so future validation cannot pair a model that arrived too late.
            cycle=_utc(run)
            receipt=_utc(received)
            if cycle>receipt or cycle>=target:
                continue
            selected[model]=float(value)
            model_receipts[model]=receipt.isoformat()
            model_runs[model]=cycle.isoformat()
        if set(selected)!={"HRRR","NBM"}:
            output["model_count"]=len(selected)
            return output
        output.update({
            "status":"RESEARCH_ONLY_NOT_PROMOTED",
            "model_count":2,
            "calibration_version":version,
            "forecast_target_utc":target.isoformat(),
            "hrrr_index_f":round(selected["HRRR"],3),
            "nbm_index_f":round(selected["NBM"],3),
            "ensemble_index_f":round((selected["HRRR"]+selected["NBM"])/2,3),
            "model_receipts":model_receipts,
            "model_runs":model_runs,
            "blend_method":"UNTRAINED_EQUAL_WEIGHT_RESEARCH",
        })
    except (sqlite3.Error,ValueError,TypeError,KeyError,OverflowError,
            json.JSONDecodeError):
        return output
    return output
