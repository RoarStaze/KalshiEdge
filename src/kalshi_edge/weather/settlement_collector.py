"""First-party Weather Company + Kalshi settlement evidence reconciliation.

UNVERIFIED SCHEMA NOTE: weather.com/kalshi/api/climate/primary is a public
site endpoint, not a published stable API contract. The parser deliberately
accepts only explicit, tightly checked climate rows, and reports
UNSUPPORTED_PUBLISHER_SCHEMA if the live response differs. It NEVER infers
settlement from METAR, nearby airports or a settled Kalshi price.

Every accepted value must agree with independently retrieved, settled Kalshi
binary rules/results, with raw HTTPS response bytes preserved and rechecked
before any model score. Exchange and publisher are two separate sources;
neither supplies a third-party digital signature, so certification remains
False even on an exact match.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import httpx

from .predictor import STATIONS, _utc, station_day, ticker_matches_station_day
from .prospective import read_station_snapshots

TWC_ENDPOINT = "https://weather.com/kalshi/api/climate/primary"
KALSHI_MARKETS_ENDPOINT = "https://external-api.kalshi.com/trade-api/v2/markets"
MAX_BYTES = 2_000_000
# Do not overload a publisher site; public visitors are not offered a guaranteed SLA.
PUBLISHER_REQUEST_INTERVAL_SECONDS = 10
MAX_LOOKBACK_DAYS = 7
AUTO_MANIFEST_NAME = "settlement_auto.jsonl"
_RULE_NUM = r"(-?\d+(?:\.\d+)?)"


def _source_rows(data: object) -> list[dict]:
    if not isinstance(data, dict):
        raise ValueError("UNSUPPORTED_PUBLISHER_SCHEMA")
    for key in ("data", "reports", "rows", "stations"):
        found = data.get(key)
        if isinstance(found, list):
            if len(found) > 10_000:
                raise ValueError("OVERSIZED_PUBLISHER_ROWS")
            return found
    raise ValueError("UNSUPPORTED_PUBLISHER_SCHEMA")


def extract_twc_high(body: bytes, climate_day: date, station: str) -> int:
    """Only a final, explicit °F station-day report can be an actual high.

    An unavailable or changed live schema stops scoring instead of guessing.
    This adapter is intentionally small and awaits real live endpoint
    compatibility validation on the user's running KalshiEdge installation.
    """
    spec=STATIONS.get(station)
    if spec is None or not spec.settlement_station:
        raise ValueError("UNSUPPORTED_STATION")
    if not isinstance(body,bytes) or len(body)>MAX_BYTES:
        raise ValueError("INVALID_PUBLISHER_PAYLOAD")
    try:
        rows=_source_rows(json.loads(body))
    except (json.JSONDecodeError, UnicodeError, TypeError) as exc:
        raise ValueError("UNSUPPORTED_PUBLISHER_SCHEMA") from exc
    chosen=[]
    for row in rows:
        if not isinstance(row,dict):
            continue
        code=row.get("climateId", row.get("stationCliId", row.get("station")))
        if code not in (spec.settlement_station, spec.settlement_station.removeprefix("CLI")):
            continue
        if row.get("date") != climate_day.isoformat():
            continue
        status=row.get("status",row.get("reportStatus"))
        if not isinstance(status,str) or status.lower() not in ("final","official","revised"):
            raise ValueError("PUBLISHER_VALUE_NOT_FINAL")
        unit=row.get("temperatureUnit",row.get("unit","F"))
        if not isinstance(unit,str) or unit.upper() not in ("F","FAHRENHEIT"):
            raise ValueError("PUBLISHER_UNITS_NOT_F")
        high=row.get("highF",row.get("maxTemp"))
        if isinstance(high,bool) or not isinstance(high,(int,float)) or not math.isfinite(high):
            raise ValueError("PUBLISHER_HIGH_MISSING")
        if abs(high-round(high))>1e-9 or not -130<int(round(high))<150:
            raise ValueError("PUBLISHER_HIGH_INVALID")
        chosen.append(int(round(high)))
    if len(chosen)!=1:
        raise ValueError("PUBLISHER_STATION_DAY_MISSING_OR_AMBIGUOUS")
    return chosen[0]


def _rules_yes(rules: str, value: int) -> bool:
    """Read a narrow subset of literal Kalshi binary high-temperature rules."""
    rule=rules.lower()
    if "maximum temperature recorded" not in rule:
        raise ValueError("UNSUPPORTED_MARKET_RULE")
    comparators=(
        (rf"\bless than\s+{_RULE_NUM}\s*°",lambda a,b: value<a),
        (rf"\bgreater than\s+{_RULE_NUM}\s*°",lambda a,b: value>a),
        (rf"\bbetween\s+{_RULE_NUM}\s*(?:°)?\s*(?:and|to|-)\s*{_RULE_NUM}\s*°",lambda a,b: a<=value<=b),
    )
    matches=[]
    for pattern,fn in comparators:
        m=re.search(pattern,rule)
        if m:
            a=float(m.group(1));b=float(m.group(2)) if m.lastindex==2 else a
            matches.append(fn(a,b))
    if len(matches)!=1:
        raise ValueError("UNSUPPORTED_MARKET_RULE")
    return matches[0]


def verify_kalshi_markets(markets: list, station: str, climate_day: date,
                          official_high_f: int) -> str:
    """Compare independent publisher high with *settled* Kalshi binary results.

    Verify the exact station, climate date, cited publisher and every returned
    bracket before returning a matching market ticker. No inference from
    prices, probability, expiration_value or unofficial nearby observations.
    """
    spec=STATIONS.get(station)
    if spec is None or not spec.kalshi_series or not spec.settlement_station:
        raise ValueError("UNSUPPORTED_STATION")
    if not isinstance(markets,list) or not 2<=len(markets)<=1000:
        raise ValueError("INSUFFICIENT_SETTLED_MARKETS")
    event=spec.kalshi_series+"-"+climate_day.strftime("%y%b%d").upper()
    yes,no=[],[]
    for market in markets:
        if not isinstance(market,dict):
            raise ValueError("INVALID_MARKET_RECORD")
        ticker=market.get("ticker")
        if (not isinstance(ticker,str) or
            market.get("event_ticker")!=event or
            not ticker_matches_station_day(station,ticker,climate_day)):
            raise ValueError("STATION_OR_EVENT_MISMATCH")
        if market.get("status")!="settled" or market.get("result") not in ("yes","no"):
            raise ValueError("MARKET_NOT_SETTLED")
        if not market.get("settlement_ts"):
            raise ValueError("SETTLEMENT_TIMESTAMP_MISSING")
        rules=market.get("rules_primary")
        if not isinstance(rules,str):
            raise ValueError("MARKET_RULES_MISSING")
        if (f"({spec.settlement_station})" not in rules or
            "according to The Weather Company" not in rules or
            f"for {climate_day:%b} {climate_day.day}, {climate_day.year}" not in rules):
            raise ValueError("OFFICIAL_SETTLEMENT_RULES_MISMATCH")
        expected=_rules_yes(rules,official_high_f)
        reported=market["result"]=="yes"
        if reported!=expected:
            raise ValueError("PUBLISHER_MARKET_OUTCOME_CONFLICT")
        (yes if reported else no).append(ticker)
    if not yes or not no:
        raise ValueError("INSUFFICIENT_OUTCOME_CROSSCHECK")
    return sorted(yes)[0]


def _atomic_bytes(path: Path, data: bytes) -> None:
    if path.exists():
        if hashlib.sha256(path.read_bytes()).digest()!=hashlib.sha256(data).digest():
            raise ValueError("EXISTING_EVIDENCE_BYTES_CHANGED")
        return
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent,prefix=".weather-source-",
                                         suffix=".tmp",delete=False) as f:
            temporary=Path(f.name)
            f.write(data)
            f.flush();os.fsync(f.fileno())
        os.replace(temporary,path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _atomic_manifest(path: Path, record: dict) -> None:
    entries=[]
    if path.is_file():
        if path.stat().st_size>5_000_000:
            raise ValueError("SETTLEMENT_MANIFEST_TOO_LARGE")
        entries=[json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                 if line.strip()]
    key=(record["station"],record["climate_day"])
    entries=[r for r in entries if (r["station"],r["climate_day"])!=key]
    entries.append(record)
    entries.sort(key=lambda r:(r["climate_day"],r["station"]))
    encoded=("\n".join(json.dumps(r,sort_keys=True,allow_nan=False) for r in entries)+"\n").encode()
    _atomic_bytes(path.parent/".manifest-checkpoint.tmp.keep", b"") if False else None
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent,prefix=".auto-labels-",
                                         suffix=".tmp",delete=False) as f:
            temporary=Path(f.name)
            f.write(encoded);f.flush();os.fsync(f.fileno())
        os.replace(temporary,path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


async def collect_station_day(root: Path, station: str, climate_day: date,
                              *, client: httpx.AsyncClient | None = None,
                              clock: Callable[[],datetime] | None = None,
                              twc_body: bytes | None = None) -> dict:
    """Attempt dual-primary-source reconciliation; never manufacture labels."""
    spec=STATIONS.get(station)
    if spec is None or spec.kalshi_series is None:
        return {"station":station,"state":"UNSUPPORTED_STATION"}
    now=_utc((clock or (lambda:datetime.now(timezone.utc)))())
    _,start,end,_=station_day(spec,
        datetime(climate_day.year,climate_day.month,climate_day.day,
                 tzinfo=timezone.utc)-timedelta(hours=spec.standard_offset_hours))
    if now<end+timedelta(hours=8):
        return {"station":station,"state":"NOT_FINAL"}
    own=client is None
    if own:
        client=httpx.AsyncClient(timeout=25,follow_redirects=False,
                                 headers={"User-Agent":"KalshiEdgeWeatherEvidence/0.4"})
    assert client is not None
    try:
        try:
            if twc_body is None:
                response=await client.get(TWC_ENDPOINT,
                    params={"date":climate_day.isoformat()})
                response.raise_for_status()
                twc_body=response.content
            if len(twc_body)>MAX_BYTES:
                raise ValueError("PUBLISHER_RESPONSE_TOO_LARGE")
            high=extract_twc_high(twc_body,climate_day,station)
            event=spec.kalshi_series+"-"+climate_day.strftime("%y%b%d").upper()
            result=await client.get(KALSHI_MARKETS_ENDPOINT,
                                    params={"event_ticker":event,"limit":1000})
            result.raise_for_status()
            kalshi_body=result.content
            if len(kalshi_body)>MAX_BYTES:
                raise ValueError("MARKET_RESPONSE_TOO_LARGE")
            payload=json.loads(kalshi_body)
            if not isinstance(payload,dict) or payload.get("cursor"):
                raise ValueError("MARKET_RESULTS_INCOMPLETE")
            match=verify_kalshi_markets(payload.get("markets"),station,
                                        climate_day,high)
        except (httpx.HTTPError,ValueError,TypeError,OverflowError,
                KeyError,json.JSONDecodeError) as exc:
            return {"station":station,"climate_day":climate_day.isoformat(),
                    "state":"UNVERIFIED",
                    "reason":f"{type(exc).__name__}: {exc}"[:220]}
        root=Path(root)
        twc_sha=hashlib.sha256(twc_body).hexdigest()
        kalshi_sha=hashlib.sha256(kalshi_body).hexdigest()
        prefix=f"{station}_{climate_day.isoformat()}"
        twc_name=f"{prefix}_twc_{twc_sha[:20]}.json"
        kalshi_name=f"{prefix}_kalshi_{kalshi_sha[:20]}.json"
        _atomic_bytes(root/"evidence"/twc_name,twc_body)
        _atomic_bytes(root/"evidence"/kalshi_name,kalshi_body)
        row={
            "station":station,"climate_day":climate_day.isoformat(),
            "actual_max_f":high,
            "market_ticker":match,
            "official_source":"THE_WEATHER_COMPANY",
            "market_rules_source":"THE_WEATHER_COMPANY",
            "source_url":TWC_ENDPOINT+"?date="+climate_day.isoformat(),
            "source_capture_file":twc_name,"source_sha256":twc_sha,
            "kalshi_url":KALSHI_MARKETS_ENDPOINT+"?event_ticker="+event,
            "kalshi_capture_file":kalshi_name,"kalshi_sha256":kalshi_sha,
            "retrieved_at":now.isoformat(),
            "evidence_state":"AUTO_CORROBORATED_UNCERTIFIED",
        }
        _atomic_manifest(root/AUTO_MANIFEST_NAME,row)
        return {"station":station,"climate_day":climate_day.isoformat(),
                "state":"CROSSCHECKED_UNCERTIFIED",
                "actual_max_f":high,"ticker":match}
    finally:
        if own:
            await client.aclose()


async def poll_settlements(db_path: Path, *,
                           client: httpx.AsyncClient | None = None,
                           clock: Callable[[],datetime] | None = None,
                           lookback_days: int = MAX_LOOKBACK_DAYS) -> dict:
    """Automatically review only already frozen prospective station-days.

    Runs under WeatherSupervisor with 6h cadence; no background daemon outside
    `kalshiedge start`. Bounded network calls; revised results re-audited.
    """
    if not 1<=lookback_days<=MAX_LOOKBACK_DAYS:
        raise ValueError("SETTLEMENT_LOOKBACK_INVALID")
    root=Path(db_path).parent
    now=_utc((clock or (lambda:datetime.now(timezone.utc)))())
    rows=read_station_snapshots(db_path,limit=800)
    days=sorted({
        (r["station"],date.fromisoformat(r["climate_day"]))
        for r in rows
        if r["station"] in STATIONS and STATIONS[r["station"]].kalshi_series
        and timedelta(0)<=now-(
            datetime.fromisoformat(r["climate_day"]).replace(tzinfo=timezone.utc)
        )<=timedelta(days=lookback_days+2)
    })
    if not days:
        return {"mode":"DATA_ONLY","state":"NO_ELIGIBLE_FORECAST_DAYS",
                "crosschecked":0,"unverified":0,"details":[]}
    own=client is None
    if own:
        client=httpx.AsyncClient(timeout=25,follow_redirects=False,
                                 headers={"User-Agent":"KalshiEdgeWeatherEvidence/0.4"})
    details=[]
    try:
        # Fetch the publisher once per date and reuse raw bytes across stations.
        cache={}
        for station,day in days:
            if day not in cache:
                try:
                    reply=await client.get(TWC_ENDPOINT,
                        params={"date":day.isoformat()})
                    reply.raise_for_status()
                    if len(reply.content)>MAX_BYTES:
                        raise ValueError("publisher response too large")
                    cache[day]=reply.content
                except (httpx.HTTPError,ValueError) as exc:
                    cache[day]=exc
                if own:
                    await asyncio.sleep(PUBLISHER_REQUEST_INTERVAL_SECONDS)
            content=cache[day]
            if isinstance(content,Exception):
                details.append({"station":station,"climate_day":day.isoformat(),
                                "state":"UNVERIFIED","reason":str(content)[:160]})
            else:
                details.append(await collect_station_day(
                    root,station,day,client=client,clock=lambda:now,
                    twc_body=content))
    finally:
        if own:
            await client.aclose()
    count=sum(x.get("state")=="CROSSCHECKED_UNCERTIFIED" for x in details)
    return {"mode":"DATA_ONLY","state":"RECONCILED" if count else "UNVERIFIED",
            "crosschecked":count,
            "unverified":len(details)-count,
            "details":details[:30]}
