"""Hourly-only Kalshi Weather Index probabilistic research.

Source: Kalshi's public GET /live_data/weather/{city} published canonical
minute observations, NOT a single airport, daily high or Synoptic raw feed.
All research estimates use actual locally stamped receipt time. No orders,
credentials, trading EV, model training on future outcomes, or daily labels.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import httpx

from .predictor import normal_cdf, _utc
from .hourly_learning import learning_decision, apply_correction
from .hourly_nowcast import (LEAD_MINUTES, index_nowcast_decision,
                             nowcast_prediction)
from .hourly_v15 import V15_KIND, predict_v15, v15_live_decision
from .hourly_physics import read_hourly_physics
from .hourly_quotes import read_latest_quote
from .hourly_physical_learning import (
    physical_live_decision, apply_locked_blend,
)
from .status import weather_database_path

API = "https://external-api.kalshi.com/trade-api/v2"
EASTERN = ZoneInfo("America/New_York")
MAX_SIZE = 3_000_000
COLLECT_SECONDS = 120
SETTLE_SECONDS = 300
MIN_SCORE_EVENTS = 10


@dataclass(frozen=True)
class City:
    label: str
    series: str


CITIES = {
    "chicago": City("Chicago Metro", "KXTEMPCHIHS"),
    "nyc": City("New York City", "KXTEMPNYCHS"),
    "la-coastal": City("Coastal Los Angeles", "KXTEMPLAXHS"),
    "miami": City("Miami", "KXTEMPMIAH"),
}


def event_for(city: str, asof: datetime) -> tuple[str, datetime]:
    """The target for the next clock hour, expressed in Eastern event time.

    UTC arithmetic avoids nonexistent local hours when DST starts. Market
    existence and exact scheduled expiration are still verified separately.
    """
    if city not in CITIES:
        raise ValueError("UNKNOWN_HOURLY_CITY")
    now = _utc(asof)
    target = datetime.fromtimestamp(
        (int(now.timestamp()) // 3600 + 1) * 3600, tz=timezone.utc)
    local_target = target.astimezone(EASTERN)
    if local_target.fold == 1:
        # On fall-back DST, Kalshi's contract defaults to the earlier
        # occurrence. Do not fabricate a second identical ET event ticker.
        target += timedelta(hours=1)
        local_target = target.astimezone(EASTERN)
    return (CITIES[city].series + "-" +
            local_target.strftime("%y%b%d%H").upper(), target)


def listed_event_target(city: str, event: str) -> datetime | None:
    """Decode the actual listed Eastern-hour event without guessing its ticker."""
    prefix = CITIES[city].series + "-"
    if not isinstance(event, str) or not event.startswith(prefix):
        return None
    suffix = event[len(prefix):]
    if not re.fullmatch(r"\d{2}[A-Z]{3}\d{2}\d{2}", suffix):
        return None
    try:
        local = datetime.strptime(suffix, "%y%b%d%H").replace(tzinfo=EASTERN, fold=0)
        if local.astimezone(timezone.utc).astimezone(EASTERN).strftime("%y%b%d%H").upper() != suffix:
            return None
        return local.astimezone(timezone.utc)
    except ValueError:
        return None


def _finite(value: object) -> float:
    if isinstance(value, bool):
        raise ValueError("boolean is not a temperature")
    number = float(value)
    if not math.isfinite(number) or not -130 < number < 160:
        raise ValueError("invalid index temperature")
    return number


def parse_index(payload: dict, city: str, received: datetime,
                *, max_age_seconds: int = 10800) -> list[dict]:
    """Conservatively decode Kalshi's documented canonical-index response.

    Backfilled points may be useful for archive analysis but are NEVER
    accepted as first-publication evidence for our prospective strategy.
    """
    received = _utc(received)
    if (not isinstance(payload, dict) or city not in CITIES or
        payload.get("city") != city or payload.get("units") != "fahrenheit"):
        raise ValueError("INVALID_KALSHI_INDEX_SOURCE")
    points = payload.get("timeseries")
    if not isinstance(points, list) or len(points) > 10_000:
        raise ValueError("INVALID_KALSHI_INDEX_SERIES")
    config = payload.get("config_version")
    if not isinstance(config, str) or not config or len(config) > 160:
        raise ValueError("MISSING_KALSHI_INDEX_CONFIG")
    result: list[dict] = []
    for point in points:
        if not isinstance(point, dict):
            continue
        if point.get("status") not in ("normal", "degraded"):
            continue
        receipt_basis = str(point.get("receipt_basis", "")).lower()
        if "backfill" in receipt_basis or "synthetic" in receipt_basis:
            continue
        t = point.get("t")
        if isinstance(t, bool) or not isinstance(t, int):
            continue
        event_ms = t
        now_ms = int(received.timestamp() * 1000)
        if not 1_600_000_000_000 < event_ms <= now_ms:
            continue
        if now_ms - event_ms > max_age_seconds * 1000:
            continue
        try:
            value = _finite(point["v"])
            if abs(round(value, 2) - value) > 1e-6:
                continue
        except (ValueError, TypeError, KeyError, OverflowError):
            continue
        contributors = point.get("contributors")
        if isinstance(contributors, bool) or not isinstance(contributors, int) or contributors <= 0:
            continue
        result.append({
            "event_ms": event_ms, "temp_f": round(value, 2),
            "status": point["status"], "contributors": contributors,
            "config_version": config,
            "raw": point,
        })
    return sorted(result, key=lambda x: x["event_ms"])


_ABOVE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*°\s*or above\s*$", re.I)


def _valid_market(market: dict, city: str, event: str, *,
                  settled: bool = False) -> dict | None:
    if not isinstance(market, dict):
        return None
    if (not isinstance(market.get("ticker"), str) or
        not market["ticker"].startswith(event + "-") or
        market.get("event_ticker") != event):
        return None
    # Kalshi's *filter* status="settled" corresponds to returned REST
    # lifecycle status="finalized". Preserve older settled fixtures too.
    if market.get("status") not in (("finalized", "settled") if settled else ("active", "open")):
        return None
    if settled and market.get("is_provisional") is True:
        return None
    if market.get("series_ticker") not in (None, CITIES[city].series):
        return None
    if market.get("market_type") not in (None, "binary"):
        return None
    rules = market.get("rules_primary")
    if not isinstance(rules, str) or not rules.strip():
        return None
    # The *whitelisted HS series* identifies the Kalshi multi-station index.
    # Wording can mention the index publisher (Synoptic Data) instead of the
    # literal string "Kalshi Weather Index". Never conflate the older
    # Weather Company single-airport contracts with the HS series.
    if "the weather company" in rules.lower() or "weather.com" in rules.lower():
        return None
    match = next((found for title in (market.get("yes_sub_title"), market.get("subtitle"))
                  if isinstance(title, str)
                  for found in [_ABOVE.fullmatch(title)] if found), None)
    if not match:
        return None
    try:
        threshold = _finite(float(match.group(1)))
        raw_strike = market.get("floor_strike")
        if raw_strike is not None:
            # Kalshi's "55° or above" index market is commonly listed as
            # "above 54.99°" with floor_strike=54.99 (ticker -T54.99).
            # Index resolution is 0.01°F. Preserve either exact or
            # one-cent-below representations, rejecting all other strikes.
            floor = _finite(raw_strike)
            if (abs(floor - threshold) > .001 and
                    abs(floor - (threshold - .01)) > .001):
                return None
    except (TypeError, ValueError, OverflowError):
        return None
    # The trading deadline belongs to the individual Kalshi contract.
    # Do not infer it from the forecast hour or settlement time.
    close_time_utc = None
    raw_close = market.get("close_time")
    if isinstance(raw_close, str):
        try:
            close = datetime.fromisoformat(raw_close.replace("Z", "+00:00"))
            if close.tzinfo is not None and close.utcoffset() is not None:
                close_time_utc = close.astimezone(timezone.utc).isoformat()
        except (ValueError, OverflowError):
            pass
    return {"city": city, "event_ticker": event, "ticker": market["ticker"],
            "threshold_f": threshold, "rules_primary": rules,
            "close_time_utc": close_time_utc,
            "source": "KALSHI_WEATHER_INDEX"}


def parse_markets(data: dict, city: str, event: str,
                  *, settled: bool = False) -> list[dict]:
    if city not in CITIES or not event.startswith(CITIES[city].series + "-"):
        raise ValueError("HOURLY_MARKET_CITY_MISMATCH")
    if not isinstance(data, dict) or data.get("cursor") not in ("", None):
        raise ValueError("INCOMPLETE_HOURLY_MARKET_PAGE")
    markets = data.get("markets")
    if not isinstance(markets, list) or len(markets) > 1000:
        raise ValueError("INVALID_HOURLY_MARKETS")
    selected = []
    seen = set()
    for row in markets:
        item = _valid_market(row, city, event, settled=settled)
        if item and item["ticker"] not in seen:
            item["result"] = row.get("result")
            item["settlement_ts"] = row.get("settlement_ts")
            selected.append(item)
            seen.add(item["ticker"])
    return sorted(selected, key=lambda x: x["threshold_f"])


async def _open_events_market_fallback(
    client: httpx.AsyncClient, city: str
) -> tuple[list[dict], int]:
    """Second independent official exchange listing, not invented events.

    Kalshi's filtered /markets listing may lag. /events?status=open with
    nested markets is the documented fallback. Reject mismatched series,
    event tickers and non-market payloads. Truncated pages are failures,
    not evidence that the market is absent.
    """
    markets: list[dict] = []
    event_count = 0
    seen: set[str] = set()
    cursor = ""
    for page in range(3):
        params = {"series_ticker": CITIES[city].series,
                  "status": "open", "with_nested_markets": "true",
                  "limit": 50}
        if cursor:
            params["cursor"] = cursor
        reply = await client.get(f"{API}/events", params=params)
        reply.raise_for_status()
        if len(reply.content) > MAX_SIZE:
            raise ValueError("OVERSIZED_HOURLY_EVENTS_RESPONSE")
        payload = reply.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
            raise ValueError("INVALID_HOURLY_EVENTS_RESPONSE")
        for item in payload["events"]:
            if not isinstance(item, dict):
                continue
            event_name = item.get("event_ticker")
            if (item.get("series_ticker") != CITIES[city].series or
                not isinstance(event_name, str) or
                listed_event_target(city, event_name) is None):
                continue
            event_count += 1
            nested = item.get("markets")
            if not isinstance(nested, list):
                continue
            for raw in nested:
                if not isinstance(raw, dict) or raw.get("event_ticker") != event_name:
                    continue
                ticker = raw.get("ticker")
                if not isinstance(ticker, str) or not ticker.startswith(event_name + "-"):
                    continue
                if ticker not in seen:
                    markets.append(raw)
                    seen.add(ticker)
        cursor = payload.get("cursor") or ""
        if not isinstance(cursor, str) or len(cursor) > 2048:
            raise ValueError("INVALID_HOURLY_EVENT_CURSOR")
        if not cursor:
            return markets, event_count
    raise ValueError("INCOMPLETE_HOURLY_EVENT_LISTING")


def _init(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("""CREATE TABLE IF NOT EXISTS weather_hourly_index(
        city TEXT NOT NULL, event_ms INTEGER NOT NULL, received_at TEXT NOT NULL,
        temp_f REAL NOT NULL, status TEXT NOT NULL, config_version TEXT NOT NULL,
        source_sha TEXT NOT NULL, payload TEXT NOT NULL,
        PRIMARY KEY(city,event_ms))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS weather_hourly_markets(
        ticker TEXT PRIMARY KEY, city TEXT NOT NULL, event_ticker TEXT NOT NULL,
        target_utc TEXT NOT NULL, threshold_f REAL NOT NULL,
        received_at TEXT NOT NULL, metadata TEXT NOT NULL)""")
    conn.execute("""CREATE INDEX IF NOT EXISTS hourly_markets_event
        ON weather_hourly_markets(city,event_ticker)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS weather_hourly_snapshots(
        city TEXT NOT NULL, event_ticker TEXT NOT NULL, market_ticker TEXT NOT NULL,
        asof TEXT NOT NULL, target_utc TEXT NOT NULL, source_reading_ms INTEGER NOT NULL,
        source_receipt TEXT NOT NULL, forecast_json TEXT NOT NULL, forecast_sha TEXT NOT NULL,
        PRIMARY KEY(city,event_ticker))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS weather_hourly_settlements(
        city TEXT NOT NULL, event_ticker TEXT NOT NULL, market_ticker TEXT NOT NULL,
        result TEXT NOT NULL, observed_index_f REAL NOT NULL,
        observed_event_ms INTEGER NOT NULL, checked_at TEXT NOT NULL,
        source_index_sha TEXT NOT NULL, market_sha TEXT NOT NULL,
        source_index_raw TEXT NOT NULL, market_raw TEXT NOT NULL,
        PRIMARY KEY(city,event_ticker))""")


async def collect_hourly_once(
    db_path: Path, *, client: httpx.AsyncClient | None = None,
    cities: tuple[str, ...] = tuple(CITIES),
    clock: Callable[[], datetime] | None = None,
) -> dict:
    """Pull Kalshi minute index & NEXT event's actual strike ladder. No orders."""
    if not cities or any(c not in CITIES for c in cities):
        raise ValueError("INVALID_CITY_LIST")
    current_clock = clock or (lambda: datetime.now(timezone.utc))
    own = client is None
    if own:
        client = httpx.AsyncClient(timeout=20, follow_redirects=False,
            headers={"User-Agent":"KalshiEdge-HourlyWeather-Research/1.0"})
    assert client is not None
    results = {}
    inserts: list[tuple] = []
    markets: list[tuple] = []
    try:
        for city in cities:
            try:
                response = await client.get(
                    f"{API}/live_data/weather/{city}", params={"last_sec":7200})
                response.raise_for_status()
                if len(response.content) > MAX_SIZE:
                    raise ValueError("OVERSIZED_INDEX_RESPONSE")
                receipt = _utc(current_clock())
                rows = parse_index(response.json(), city, receipt)
                source_sha = hashlib.sha256(response.content).hexdigest()
                for point in rows:
                    inserts.append((
                        city, point["event_ms"], receipt.isoformat(),
                        point["temp_f"], point["status"], point["config_version"],
                        source_sha, json.dumps(point["raw"], sort_keys=True)))
                # Discover *listed* contracts by the official index series.
                # A guessed event_ticker can point to an unlisted hour.
                listed: list[dict] = []
                seen_tickers: set[str] = set()
                cursor = ""
                for page in range(4):
                    params = {"series_ticker": CITIES[city].series,
                              "status": "open", "limit": 200}
                    if cursor:
                        params["cursor"] = cursor
                    reply = await client.get(f"{API}/markets", params=params)
                    reply.raise_for_status()
                    if len(reply.content) > MAX_SIZE:
                        raise ValueError("OVERSIZED_MARKET_RESPONSE")
                    payload = reply.json()
                    if not isinstance(payload, dict) or not isinstance(payload.get("markets"), list):
                        raise ValueError("INVALID_HOURLY_MARKETS")
                    for raw in payload["markets"]:
                        if not isinstance(raw, dict):
                            continue
                        ticker = raw.get("ticker")
                        if isinstance(ticker, str) and ticker not in seen_tickers:
                            listed.append(raw)
                            seen_tickers.add(ticker)
                    cursor = payload.get("cursor") or ""
                    if not isinstance(cursor, str) or len(cursor) > 2048:
                        raise ValueError("INVALID_HOURLY_CURSOR")
                    if not cursor:
                        break
                else:
                    raise ValueError("INCOMPLETE_HOURLY_SERIES_LISTING")

                # Find true target hours using the exchange event ticker
                # (Eastern clock hour). Do not treat future market close_time
                # as the index target hour.
                def classify(rows_to_scan: list[dict]):
                    by_event: dict[str, list[dict]] = {}
                    times: dict[str, datetime] = {}
                    count = {"unparseable": 0, "past": 0, "out_of_horizon": 0}
                    nearest: tuple[str, datetime] | None = None
                    for raw in rows_to_scan:
                        event_name = raw.get("event_ticker")
                        target_time = listed_event_target(city, event_name)
                        if target_time is None:
                            count["unparseable"] += 1
                            continue
                        if target_time <= receipt:
                            count["past"] += 1
                            continue
                        if target_time > receipt + timedelta(hours=3):
                            count["out_of_horizon"] += 1
                            # An event ticker alone is not evidence that its
                            # associated contract is still tradable. Only show
                            # next-listed timing when Kalshi provided an
                            # explicit closing timestamp after this receipt.
                            close_raw = raw.get("close_time")
                            try:
                                close = (datetime.fromisoformat(
                                    close_raw.replace("Z", "+00:00"))
                                    if isinstance(close_raw, str) else None)
                                if (close is not None and close.utcoffset() is not None
                                    and close.astimezone(timezone.utc) > receipt
                                    and (nearest is None or target_time < nearest[1])):
                                    nearest = (event_name, target_time)
                            except (ValueError, OverflowError):
                                pass
                            continue
                        times[event_name] = target_time
                        by_event.setdefault(event_name, []).append(raw)
                    return by_event, times, count, nearest

                def choose(by_event: dict[str, list[dict]],
                           times: dict[str, datetime]):
                    for candidate in sorted(times, key=lambda key: times[key]):
                        parsed = parse_markets(
                            {"markets": by_event[candidate], "cursor": ""},
                            city, candidate)
                        # A cached open status can outlive the actual close.
                        valid = [item for item in parsed
                                 if item["close_time_utc"] is None or
                                 datetime.fromisoformat(item["close_time_utc"]) > receipt]
                        if valid:
                            return candidate, times[candidate], valid
                    return None

                grouped, targets, rejected, next_listed = classify(listed)
                selected: list[dict] = []
                event, target = event_for(city, receipt)
                match = choose(grouped, targets)
                lookup_source = "markets"
                fallback_state = "NOT_NEEDED"
                fallback_error = None
                fallback_market_count = 0
                fallback_event_count = 0

                if match is None:
                    # Kalshi documents short-lived cached listing lag;
                    # independently check the official open-events endpoint
                    # rather than assuming 10 old markets are tradable now.
                    try:
                        additional, fallback_event_count = (
                            await _open_events_market_fallback(client, city))
                        fallback_market_count = len(additional)
                        fallback_state = (
                            "READY" if additional else
                            "NO_OPEN_EVENTS" if not fallback_event_count
                            else "NO_ELIGIBLE_EVENT_MARKETS")
                        extra_grouped, extra_targets, extra_rejected, extra_next = (
                            classify(additional))
                        for name, rows_for_event in extra_grouped.items():
                            grouped.setdefault(name, []).extend(rows_for_event)
                        targets.update(extra_targets)
                        for key in rejected:
                            rejected[key] += extra_rejected[key]
                        if extra_next is not None and (
                            next_listed is None or extra_next[1] < next_listed[1]):
                            next_listed = extra_next
                        match = choose(grouped, targets)
                        if match is not None:
                            lookup_source = "events_fallback"
                    except (httpx.HTTPError, ValueError, TypeError,
                            OverflowError, KeyError, json.JSONDecodeError) as exc:
                        fallback_state = "UNAVAILABLE"
                        fallback_error = f"{type(exc).__name__}: {exc}"[:180]

                if match is not None:
                    event, target, selected = match
                reason = None
                reason_code = None
                if not selected:
                    if not listed and not fallback_market_count:
                        reason_code = "NO_OPEN_INDEX_MARKETS"
                        reason = "Kalshi has no eligible open hourly contracts for this city."
                    elif targets:
                        reason_code = "NO_ACCEPTED_CONTRACTS"
                        reason = ("Kalshi listed an upcoming hourly index event, "
                                  "but no open contract passed threshold and close-time checks.")
                    elif rejected["out_of_horizon"]:
                        reason_code = "LISTED_OUTSIDE_FORECAST_HORIZON"
                        reason = ("The next listed hourly Kalshi market is beyond "
                                  "the verified three-hour forecast window.")
                    else:
                        reason_code = "NO_FUTURE_INDEX_EVENT"
                        reason = "No eligible future hourly Kalshi event is currently listed."

                for item in selected:
                    markets.append((
                        item["ticker"], city, event, target.isoformat(),
                        item["threshold_f"], receipt.isoformat(),
                        json.dumps(item, sort_keys=True)))
                results[city] = {
                    "state": "READY" if rows and selected else
                    ("NO_ACTIVE_HOURLY_MARKET" if rows else "NO_INDEX_POINTS"),
                    "index_points": len(rows), "market_strikes": len(selected),
                    "source_market_count": len(listed),
                    "fallback_market_count": fallback_market_count,
                    "fallback_event_count": fallback_event_count,
                    "fallback_state": fallback_state,
                    "fallback_error": fallback_error,
                    "lookup_source": lookup_source,
                    "future_event_count": len(grouped),
                    "rejected_listings": rejected,
                    "next_listed_target_utc": (
                        next_listed[1].isoformat() if next_listed else None),
                    "next_listed_event_ticker": next_listed[0] if next_listed else None,
                    "reason_code": reason_code,
                    "listed_event_tickers": list(dict.fromkeys(
                        str(x.get("event_ticker", "")) for x in listed))[:4],
                    "event_ticker": event, "reason": reason,
                }
            except (ValueError, TypeError, httpx.HTTPError,
                    KeyError, OverflowError, json.JSONDecodeError) as exc:
                results[city] = {"state":"UNAVAILABLE",
                                 "reason": f"{type(exc).__name__}: {exc}"[:190]}
        if inserts or markets:
            path = Path(db_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(path, timeout=10)) as conn:
                _init(conn)
                for row in inserts:
                    conn.execute("INSERT OR IGNORE INTO weather_hourly_index "
                                 "(city,event_ms,received_at,temp_f,status,config_version,"
                                 "source_sha,payload) VALUES(?,?,?,?,?,?,?,?)", row)
                for row in markets:
                    conn.execute("""INSERT INTO weather_hourly_markets
                      (ticker,city,event_ticker,target_utc,threshold_f,received_at,metadata)
                      VALUES(?,?,?,?,?,?,?) ON CONFLICT(ticker) DO UPDATE SET
                      received_at=excluded.received_at,
                      metadata=excluded.metadata""", row)
                conn.commit()
        return {"mode":"HOURLY_PAPER_ONLY","cities":results,"real_orders_enabled":False}
    finally:
        if own:
            await client.aclose()


def _forecast(conn: sqlite3.Connection, city: str, asof: datetime,
              *, force_next_index_hour: bool = False) -> dict:
    event, target = event_for(city, asof)
    # Follow actual collected Kalshi listings, not a speculative event hour.
    # An old entry is not evidence that a market is still open.
    selected = conn.execute("""
        SELECT event_ticker, target_utc FROM weather_hourly_markets
        WHERE city=? AND received_at>=? AND received_at<=?
          AND target_utc>? AND target_utc<=?
        ORDER BY target_utc ASC, received_at DESC LIMIT 1""",
        (city, (asof-timedelta(minutes=5)).isoformat(), asof.isoformat(),
         asof.isoformat(), (asof+timedelta(hours=3)).isoformat())).fetchone()
    if selected and not force_next_index_hour:
        event, target = selected[0], datetime.fromisoformat(selected[1])
    result = {
        "city": city, "name": CITIES[city].label, "series": CITIES[city].series,
        "status":"MISSING_INDEX","event_ticker":event,"target_utc":target.isoformat(),
        "asof":asof.isoformat(),"source":"KALSHI_WEATHER_INDEX",
        "current_index_f":None,"source_event_ms":None,"source_received_at":None,
        "predicted_index_f":None,"sigma_f":None,"interval_90_f":None,
        "model_kind":"UNVALIDATED_HOURLY_INDEX_TREND",
        "calibration_certified":False,"real_orders_enabled":False,
        "learning_status":"WAITING_FOR_INDEX",
        "learning_verified_hours":0,"learning_training_hours":0,
        "learning_validation_hours":0,"learning_validation":None,
        "index_nowcast_status":"WAITING_FOR_INDEX",
        "index_nowcast_history_hours":0,
        "index_nowcast_lead_minutes":None,
        "index_nowcast_validation":None,
        "minutes_to_target":round((target-asof).total_seconds()/60,2),
        "markets":[],
        # NOAA physical research is always independent of live Kalshi orders.
        "physical_ensemble":read_hourly_physics(conn,city,target,asof),
        "source_config_version":None,
        "pre_physical_mean_f":None,"pre_physical_sigma_f":None,
        "pre_physical_model_kind":None,
        "physical_adaptive_status":"WAITING_FOR_MATCHED_EVIDENCE",
        "physical_adaptive_paired_hours":0,
        "physical_adaptive_scored_hours":0,
        "physical_adaptive_research_scores":None,
        "physical_adaptive_lockbox_hours":0,
        "physical_adaptive_market_hours":0,
        "physical_adaptive_metrics":None,
    }
    points = conn.execute("""
       SELECT event_ms, received_at, temp_f, status, config_version FROM weather_hourly_index
       WHERE city=? AND received_at<=? AND event_ms<=?
       ORDER BY event_ms DESC LIMIT 100""",
       (city,asof.isoformat(),int(asof.timestamp()*1000))).fetchall()
    if not points:
        return result
    last = points[0]
    last_time = datetime.fromtimestamp(last[0]/1000,tz=timezone.utc)
    result["current_index_f"] = last[2]
    result["source_event_ms"] = last[0]
    result["source_received_at"] = last[1]
    result["source_config_version"] = last[4]
    if (asof-last_time).total_seconds() > 20*60:
        result["status"]="STALE_INDEX"
        return result
    # A short-horizon trend in the actual multi-station index. No future
    # airport high or any single-station settlement substitution.
    earlier = next((p for p in points[1:] if 10*60<=
                    (last[0]-p[0])/1000<=50*60), None)
    slope = 0.0
    if earlier:
        hours=(last[0]-earlier[0])/3_600_000
        slope=max(-5.,min(5.,(last[2]-earlier[2])/hours))
    hours_ahead=(target-last_time).total_seconds()/3600
    mean=last[2] + .55*slope*hours_ahead  # assumed damped persistence
    sigma=0.8 + .85*math.sqrt(max(.0,hours_ahead))
    if last[3]=="degraded":
        sigma+=.3
    result["raw_predicted_index_f"]=round(mean,6)
    result["raw_sigma_f"]=round(sigma,6)
    # Learning only from frozen, source-audited, already-published historical
    # outcomes. Never use future labels or unvalidated station-level data.
    decision=learning_decision(conn,city,asof)
    result["learning_status"]=decision["status"]
    result["learning_verified_hours"]=decision["verified_hours"]
    result["learning_training_hours"]=decision["training_hours"]
    result["learning_validation_hours"]=decision["validation_hours"]
    result["learning_validation"]=decision.get("validation")
    if decision["enabled"]:
        mean,sigma=apply_correction(mean,sigma,last[2],decision)
        result["model_kind"]="HISTORICAL_HOLDOUT_RESIDUAL_V1"
        result["learning_adjustment"]={
            "bias_f":decision["fitted_bias_f"],
            "trend_gain":decision["fitted_trend_gain"],
            "sigma_scale":decision["fitted_sigma_scale"]}
        result["index_nowcast_status"]="VERIFIED_SETTLEMENT_MODEL_PRIORITY"
    else:
        # Replay index minutes using their local receipt timestamps. Models
        # trained for a different lead time or index configuration cannot be
        # silently substituted; no market settlement label is fabricated.
        lead=(target-asof).total_seconds()/60
        if 0<lead<=60:
            bucket=min(LEAD_MINUTES,key=lambda k:abs(k-lead))
            nowcast=index_nowcast_decision(conn,city,asof,
                  lead_minutes=bucket,config_version=last[4],
                  target_utc=target)
            result["index_nowcast_lead_minutes"]=bucket
            result["index_nowcast_status"]=nowcast["status"]
            result["index_nowcast_history_hours"]=nowcast["history_hours"]
            result["index_nowcast_validation"]=nowcast.get("validation")
            live=nowcast.get("live_slopes")
            analogue_available=(
                nowcast.get("model") not in (
                    "DIURNAL_ANALOGUE_V5","REGULARIZED_HOURLY_HYBRID_V13",
                    "REGIME_RESIDUAL_HYBRID_V14") or
                isinstance(nowcast.get("live_diurnal_delta_f"),(int,float)))
            if nowcast["enabled"] and live is not None and analogue_available:
                nowcast_hours=(target-datetime.fromtimestamp(
                    live[3]/1000,tz=timezone.utc)).total_seconds()/3600
                mean,sigma=nowcast_prediction(live[2],sigma,live[0],live[1],
                            nowcast_hours,nowcast,
                            diurnal_delta_f=nowcast.get("live_diurnal_delta_f"))
                # A changed live model gets an independent identity so
                # earlier physical lockbox evidence cannot be inherited.
                result["model_kind"]=(
                    "HISTORICAL_REGIME_HYBRID_INDEX_NOWCAST_V14"
                    if nowcast["model"]=="REGIME_RESIDUAL_HYBRID_V14"
                    else ("HISTORICAL_ARCHIVE_HYBRID_INDEX_NOWCAST_V13"
                          if nowcast["status"]=="ARCHIVED_TEMPERATURE_V13_PROMOTED_UNCERTIFIED"
                          else "HISTORICAL_PAIRED_HYBRID_INDEX_NOWCAST_V13")
                    if nowcast["model"]=="REGULARIZED_HOURLY_HYBRID_V13"
                    else ("HISTORICAL_DIURNAL_INDEX_NOWCAST_V12"
                    if nowcast["model"]=="DIURNAL_ANALOGUE_V5"
                    else ("HISTORICAL_REGIME_SIGMA_INDEX_NOWCAST_V8"
                    if nowcast["model"]=="OBSERVATION_REGIME_SIGMA_V4"
                    else ("HISTORICAL_ADAPTIVE_INDEX_NOWCAST_V3"
                          if nowcast["model"]=="OBSERVATION_ADAPTIVE_V3"
                          else "HISTORICAL_INDEX_NOWCAST_V2"))))
                result["index_nowcast_selected_model"]=nowcast["model"]
            elif nowcast["enabled"] and not analogue_available:
                result["index_nowcast_status"]="DIURNAL_LIVE_FEATURE_UNAVAILABLE"
                result["index_nowcast_selected_model"]=nowcast["model"]
        else:
            result["index_nowcast_status"]="UNSUPPORTED_LEAD_TIME"
    # Preserve the ORIGINAL V13/V14 point-in-time counterfactual even after
    # V15 activates, so later paired research never loses its baseline.
    result["v15_reference_mean_f"]=round(mean,6)
    result["v15_reference_sigma_f"]=round(sigma,6)
    result["v15_reference_model_kind"]=result["model_kind"]
    # V15: learn source-time-safe NOAA HRRR/NBM correction only from older
    # immutable paired records with genuinely later canonical outcomes.
    # A missing, late or mismatched NOAA feed keeps V13/V14 unchanged.
    remaining=(target-asof).total_seconds()/60.
    v15_lead=(min(LEAD_MINUTES,key=lambda k:abs(k-remaining))
              if 0<remaining<=60 else None)
    # An already qualified, prospective, exact-market V4 lockbox is
    # stronger evidence than V15's temperature-only historical holdout.
    # Check that ORIGINAL pre-V15 baseline first, so changing model IDs
    # cannot inadvertently invalidate a superior proven physical blend.
    original_physical=physical_live_decision(
        conn,city,last[4],target,asof,result["physical_ensemble"],
        baseline_kind=result["model_kind"])
    if original_physical.get("enabled"):
        v15={"enabled":False,"status":"MARKET_VERIFIED_PHYSICAL_LOCK_PRIORITY"}
    else:
        v15=v15_live_decision(
            conn,city,last[4],target,asof,v15_lead,
            result["physical_ensemble"],baseline_kind=result["model_kind"])
    result["v15_status"]=v15["status"]
    result["v15_paired_hours"]=v15.get("history_hours",0)
    result["v15_training_hours"]=v15.get("train_hours",0)
    result["v15_validation_hours"]=v15.get("validation_hours",0)
    if v15["enabled"]:
        mean,sigma=predict_v15({
            "baseline_f":mean,"sigma_f":sigma,
            "hrrr_f":result["physical_ensemble"]["hrrr_index_f"],
            "nbm_f":result["physical_ensemble"]["nbm_index_f"],
        },v15["parameters"])
        result["model_kind"]=V15_KIND
        result["index_nowcast_selected_model"]=V15_KIND
        result["index_nowcast_status"]="V15_PAIRED_TEMPERATURE_IMPROVED"
        result["v15_validation"]={
            "incumbent":v15["evidence"]["incumbent"],
            "candidate":v15["evidence"]["candidate"],
            "market_verified_hours":v15["market_verified_hours"],
        }
    # Freeze the exact current-model estimate for prospective paired scores.
    # Never use an already blended prediction as the baseline label.
    result["pre_physical_mean_f"]=round(mean,6)
    result["pre_physical_sigma_f"]=round(sigma,6)
    result["pre_physical_model_kind"]=result["model_kind"]
    physical=(original_physical if original_physical.get("enabled")
              else physical_live_decision(
                  conn,city,last[4],target,asof,result["physical_ensemble"],
                  baseline_kind=result["pre_physical_model_kind"]))
    result["physical_adaptive_status"]=physical["status"]
    result["physical_adaptive_paired_hours"]=physical.get("paired_hours",0)
    result["physical_adaptive_scored_hours"]=physical.get("scored_hours",0)
    result["physical_adaptive_research_scores"]=physical.get("research_scores")
    result["physical_adaptive_lockbox_hours"]=physical.get("lockbox_hours",0)
    result["physical_adaptive_market_hours"]=physical.get("market_verified_hours",0)
    result["physical_adaptive_metrics"]=physical.get("metrics")
    if physical.get("enabled"):
        mean,sigma=apply_locked_blend(
            mean,sigma,result["physical_ensemble"]["hrrr_index_f"],
            result["physical_ensemble"]["nbm_index_f"],physical)
        result["model_kind"]="PROSPECTIVE_LOCKBOX_PHYSICAL_INDEX_V4"
        result["physical_adaptive_weights"]=physical["weights"]
    result["predicted_index_f"]=round(mean,3)
    result["sigma_f"]=round(sigma,3)
    result["interval_90_f"]=[
        round(mean-1.64485362695*sigma,2),
        round(mean+1.64485362695*sigma,2)]
    candidates=conn.execute("""SELECT ticker, threshold_f, received_at, metadata
       FROM weather_hourly_markets
       WHERE city=? AND event_ticker=? AND received_at<=?
       ORDER BY threshold_f""",
       (city,event,asof.isoformat())).fetchall()
    strikes=[]
    for ticker,threshold,receipt,raw in candidates:
        try:
            m=json.loads(raw)
            if m["source"]!="KALSHI_WEATHER_INDEX":
                continue
            close_at = m.get("close_time_utc")
            if close_at and datetime.fromisoformat(close_at) <= asof:
                continue
            # Quote is independent evidence from the individual public
            # market endpoint. Never treat historical last trade or unvalidated
            # model chance as a currently purchasable asking price.
            quote=read_latest_quote(conn,city=city,event=event,ticker=ticker,
                                   threshold_f=threshold,asof=asof)
            if quote["status"]=="CLOSED":
                continue
            probability=max(0.,min(1.,1-normal_cdf(threshold,mean,sigma)))
        except (ValueError,TypeError,KeyError,json.JSONDecodeError):
            continue
        strikes.append({"ticker":ticker,"threshold_f":threshold,
                        "probability_yes":round(probability,6),
                        "close_time_utc":quote.get("close_time_utc") or m.get("close_time_utc"),
                        "market_metadata_received_at":receipt,
                        "quote_status":quote["status"],
                        "quote_received_at":quote.get("quote_received_at"),
                        "yes_ask_dollars":quote["yes_ask_dollars"],
                        "no_ask_dollars":quote["no_ask_dollars"],
                        "yes_ask_size_fp":quote["yes_ask_size_fp"],
                        "no_ask_size_fp":quote["no_ask_size_fp"]})
    if not strikes:
        result["status"]="NO_VERIFIED_HOURLY_MARKETS"
        return result
    result["markets"]=strikes
    result["status"]="READY_RESEARCH"
    return result


def read_hourly_forecasts(root: Path, *, db_path: Path | None = None,
                          asof: datetime | None = None,
                          preview: bool = False) -> dict:
    asof=_utc(asof or datetime.now(timezone.utc))
    path=Path(db_path or weather_database_path(root))
    result={"mode":"HOURLY_PAPER_ONLY","real_orders_enabled":False,
            "calibration_certified":False,"source":"KALSHI_WEATHER_INDEX",
            "forecasts":[]}
    if preview or not path.is_file():
        for city in CITIES:
            event,target=event_for(city,asof)
            result["forecasts"].append({
                "city":city,"name":CITIES[city].label,"status":"MISSING_INDEX",
                "event_ticker":event,"target_utc":target.isoformat(),
                "current_index_f":None,"predicted_index_f":None,
                "interval_90_f":None,"markets":[],
                "source":"KALSHI_WEATHER_INDEX","real_orders_enabled":False,
                "calibration_certified":False})
        return result
    try:
        uri=path.resolve().as_uri()+"?mode=ro"
        with closing(sqlite3.connect(uri,uri=True,timeout=2)) as conn:
            conn.execute("PRAGMA query_only=ON")
            result["forecasts"]=[_forecast(conn,city,asof) for city in CITIES]
    except (sqlite3.Error,OSError,ValueError) as exc:
        result["status"]="HOURLY_DATABASE_ERROR"
        result["reason"]=f"{type(exc).__name__}: {exc}"[:190]
        return read_hourly_forecasts(root,db_path=path,asof=asof,preview=True)
    return result


def journal_hourly_forecasts(db_path: Path, *, asof: datetime | None = None) -> dict:
    asof=_utc(asof or datetime.now(timezone.utc))
    path=Path(db_path)
    result={"mode":"HOURLY_PAPER_ONLY","inserted":0}
    if not path.is_file():
        return result
    predictions=read_hourly_forecasts(path.parent,db_path=path,asof=asof)
    eligible=[]
    for row in predictions["forecasts"]:
        if row.get("status")!="READY_RESEARCH":
            continue
        lead=(datetime.fromisoformat(row["target_utc"])-asof).total_seconds()/60
        if not 5<=lead<=65 or not row["markets"]:
            continue
        # Ex-ante precommit to one nearest-50% threshold, not hindsight choice.
        chosen=min(row["markets"],key=lambda m:
            (abs(m["probability_yes"]-.5),m["threshold_f"]))
        frozen={**row,"selected_market":chosen}
        serialized=json.dumps(frozen,sort_keys=True,allow_nan=False)
        eligible.append((row["city"],row["event_ticker"],chosen["ticker"],
                         asof.isoformat(),row["target_utc"],row["source_event_ms"],
                         row["source_received_at"],serialized,
                         hashlib.sha256(serialized.encode()).hexdigest()))
    if eligible:
        with closing(sqlite3.connect(path,timeout=5)) as conn:
            _init(conn)
            for row in eligible:
                cursor=conn.execute("""INSERT OR IGNORE INTO weather_hourly_snapshots
                    (city,event_ticker,market_ticker,asof,target_utc,
                    source_reading_ms,source_receipt,forecast_json,forecast_sha)
                    VALUES(?,?,?,?,?,?,?,?,?)""",row)
                result["inserted"]+=cursor.rowcount
            conn.commit()
    return result


async def settle_hourly_once(db_path: Path, *, client: httpx.AsyncClient | None = None,
                             clock: Callable[[], datetime] | None = None) -> dict:
    """Check matched Kalshi canonical index AND settled directional result."""
    now=_utc((clock or (lambda:datetime.now(timezone.utc)))())
    path=Path(db_path)
    result={"mode":"HOURLY_PAPER_ONLY","verified":0,"unverified":0}
    if not path.is_file():
        return result
    uri=path.resolve().as_uri()+"?mode=ro"
    with closing(sqlite3.connect(uri,uri=True,timeout=3)) as conn:
        candidates=conn.execute("""SELECT s.city,s.event_ticker,s.market_ticker,
               s.target_utc,s.forecast_json
           FROM weather_hourly_snapshots s
           LEFT JOIN weather_hourly_settlements o
             ON o.city=s.city AND o.event_ticker=s.event_ticker
           WHERE o.event_ticker IS NULL
           ORDER BY s.target_utc DESC LIMIT 100""").fetchall()
    own=client is None
    if own:
        client=httpx.AsyncClient(timeout=25,follow_redirects=False,
             headers={"User-Agent":"KalshiEdge-HourlySettlement-Research/1.0"})
    assert client is not None
    try:
        for city,event,ticker,target_raw,frozen_json in candidates:
            target=_utc(datetime.fromisoformat(target_raw))
            if not timedelta(minutes=6)<=now-target<=timedelta(days=7):
                continue
            try:
                start_ms=int((target-timedelta(minutes=60)).timestamp()*1000)
                end_ms=int(target.timestamp()*1000)
                response=await client.get(f"{API}/live_data/weather/{city}",
                    params={"from":start_ms,"to":end_ms})
                response.raise_for_status()
                if len(response.content)>MAX_SIZE:
                    raise ValueError("HOURLY_SETTLEMENT_INDEX_TOO_LARGE")
                index=parse_index(response.json(),city,now,
                    max_age_seconds=8*24*3600)
                valid=[p for p in index if start_ms<=p["event_ms"]<=end_ms]
                if not valid:
                    raise ValueError("NO_CANONICAL_SETTLEMENT_INDEX")
                canonical=max(valid,key=lambda p:p["event_ms"])
                reply=await client.get(f"{API}/markets",
                    params={"event_ticker":event,"limit":200})
                reply.raise_for_status()
                if len(reply.content)>MAX_SIZE:
                    raise ValueError("HOURLY_SETTLEMENT_MARKETS_TOO_LARGE")
                markets=parse_markets(reply.json(),city,event,settled=True)
                matching=next((m for m in markets if m["ticker"]==ticker),None)
                if not matching or matching.get("result") not in ("yes","no"):
                    raise ValueError("HOURLY_MARKET_NOT_SETTLED")
                if not matching.get("settlement_ts"):
                    raise ValueError("HOURLY_SETTLEMENT_TS_MISSING")
                predicted_yes=canonical["temp_f"]>=matching["threshold_f"]
                if (matching["result"]=="yes")!=predicted_yes:
                    raise ValueError("INDEX_MARKET_OUTCOME_CONFLICT")
                now_str=now.isoformat()
                with closing(sqlite3.connect(path,timeout=5)) as conn:
                    _init(conn)
                    cursor=conn.execute("""INSERT OR IGNORE INTO weather_hourly_settlements
                        (city,event_ticker,market_ticker,result,observed_index_f,
                        observed_event_ms,checked_at,source_index_sha,market_sha,
                        source_index_raw,market_raw)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        (city,event,ticker,matching["result"],canonical["temp_f"],
                         canonical["event_ms"],now_str,
                         hashlib.sha256(response.content).hexdigest(),
                         hashlib.sha256(reply.content).hexdigest(),
                         response.content.decode("utf-8"),
                         reply.content.decode("utf-8")))
                    conn.commit()
                    result["verified"]+=cursor.rowcount
            except (httpx.HTTPError,ValueError,TypeError,OverflowError,
                    KeyError,sqlite3.Error,json.JSONDecodeError) as exc:
                result["unverified"]+=1
                result.setdefault("errors",[]).append(
                    {"event":event,"reason":f"{type(exc).__name__}: {exc}"[:170]})
        return result
    finally:
        if own:
            await client.aclose()


def hourly_calibration(root: Path, *, db_path: Path | None = None,
                       preview: bool = False) -> dict:
    from .hourly_forward import forward_accuracy
    path=Path(db_path or weather_database_path(root))
    result={"mode":"HOURLY_PAPER_ONLY","matched_hours":0,
            "brier":None,"log_loss":None,"empirical_90_coverage":None,
            "calibration_certified":False,"real_orders_enabled":False,
            "status":"WAITING_FOR_HOURLY_SETTLEMENTS",
            "forward_v9":forward_accuracy(root,db_path=path,preview=preview)}
    if preview or not path.is_file():
        return result
    try:
        uri=path.resolve().as_uri()+"?mode=ro"
        with closing(sqlite3.connect(uri,uri=True,timeout=2)) as conn:
            conn.execute("PRAGMA query_only=ON")
            rows=conn.execute("""SELECT s.city,s.event_ticker,s.target_utc,
               s.forecast_json,s.forecast_sha,o.result,o.observed_index_f,
               o.observed_event_ms,o.market_ticker,s.market_ticker,
               o.checked_at,o.source_index_raw,o.market_raw,
               o.source_index_sha,o.market_sha
               FROM weather_hourly_snapshots s
               JOIN weather_hourly_settlements o
               ON o.city=s.city AND o.event_ticker=s.event_ticker""").fetchall()
        observations=[]
        for (city,event,target_raw,raw,digest,yes,value,value_ms,
             settled_ticker,original_ticker,checked_at,index_raw,market_raw,
             index_sha,market_sha) in rows:
            if settled_ticker!=original_ticker:
                raise ValueError("HOURLY_SETTLEMENT_TICKER_CHANGED")
            if hashlib.sha256(raw.encode()).hexdigest()!=digest:
                raise ValueError("TAMPERED_HOURLY_FORECAST")
            if (hashlib.sha256(index_raw.encode()).hexdigest()!=index_sha or
                hashlib.sha256(market_raw.encode()).hexdigest()!=market_sha):
                raise ValueError("TAMPERED_HOURLY_SETTLEMENT_SOURCE")
            target=_utc(datetime.fromisoformat(target_raw))
            as_checked=_utc(datetime.fromisoformat(checked_at))
            indexed=parse_index(json.loads(index_raw),city,as_checked,
                                max_age_seconds=8*24*3600)
            lower_ms=int((target-timedelta(minutes=60)).timestamp()*1000)
            upper_ms=int(target.timestamp()*1000)
            viable=[x for x in indexed if lower_ms<=x["event_ms"]<=upper_ms]
            if not viable:
                raise ValueError("NO_RECORDED_CANONICAL_SETTLEMENT_POINT")
            canonical=max(viable,key=lambda x:x["event_ms"])
            market_row=next((x for x in parse_markets(json.loads(market_raw),city,
                            event,settled=True) if x["ticker"]==settled_ticker),None)
            if (market_row is None or market_row["result"]!=yes or
                canonical["event_ms"]!=value_ms or
                abs(canonical["temp_f"]-value)>1e-6 or
                (canonical["temp_f"]>=market_row["threshold_f"])!=(yes=="yes")):
                raise ValueError("HOURLY_SETTLEMENT_RECONCILIATION_CHANGED")
            frozen=json.loads(raw)
            chosen=frozen["selected_market"]
            if (chosen["ticker"]!=settled_ticker or
                abs(chosen["threshold_f"]-market_row["threshold_f"])>1e-6):
                raise ValueError("HOURLY_SNAPSHOT_MARKET_MISMATCH")
            p=chosen["probability_yes"]
            if not isinstance(p,(int,float)) or not 0<=p<=1:
                raise ValueError("INVALID_FROZEN_HOURLY_PROBABILITY")
            observations.append((p,1 if yes=="yes" else 0,
                abs(float(value)-frozen["predicted_index_f"])<=
                1.64485362695*frozen["sigma_f"]))
        n=len(observations)
        result["matched_hours"]=n
        if n<MIN_SCORE_EVENTS:
            result["status"]="INSUFFICIENT_HOURLY_EVIDENCE" if n else "WAITING_FOR_HOURLY_SETTLEMENTS"
            return result
        result["brier"]=round(sum((p-y)**2 for p,y,_ in observations)/n,6)
        result["log_loss"]=round(sum(-math.log(max(1e-15,
            p if y else 1-p)) for p,y,_ in observations)/n,6)
        count=sum(hit for _,_,hit in observations)
        result["empirical_90_coverage"]=round(count/n,5)
        result["status"]="MEASURED_UNCALIBRATED_RESEARCH"
    except (sqlite3.Error,OSError,ValueError,KeyError,TypeError) as exc:
        result["status"]="HOURLY_AUDIT_ERROR"
        result["reason"]=f"{type(exc).__name__}: {exc}"[:180]
    return result


def hourly_status(root: Path, *, db_path: Path | None = None,
                  preview: bool = False) -> dict:
    """Hourly-only read-only index source health; legacy daily rows ignored."""
    from .status import read_collector_health
    path=Path(db_path or weather_database_path(root))
    result={"mode":"HOURLY_PAPER_ONLY","real_orders_enabled":False,
            "status":"NOT_STARTED","sample_count":0,"city_count":0,
            "last_received_at":None,"cities":[],
            "collector":read_collector_health(root,preview=preview)}
    if preview or not path.is_file():
        return result
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+"?mode=ro",
                                    uri=True,timeout=2)) as conn:
            conn.execute("PRAGMA query_only=ON")
            rows=conn.execute("""SELECT city,COUNT(*),MAX(received_at)
                FROM weather_hourly_index GROUP BY city ORDER BY city""").fetchall()
        result["cities"]=[{"city":city,"points":n,"last_received_at":ts}
                           for city,n,ts in rows]
        result["sample_count"]=sum(n for _,n,_ in rows)
        result["city_count"]=len(rows)
        dates=[ts for _,_,ts in rows if ts]
        result["last_received_at"]=max(dates) if dates else None
        result["status"]="READY" if rows else "COLLECTING_INDEX"
    except sqlite3.OperationalError as exc:
        result["status"]="COLLECTING_INDEX" if "no such table" in str(exc) else "DATABASE_ERROR"
    except (sqlite3.Error,OSError,ValueError):
        result["status"]="DATABASE_ERROR"
    return result
