"""Read-only, Bitcoin-style WeatherEdge History with persistent audited verdicts.

One earliest frozen forecast per city/hour. Forecasts are never reconstructed
from outcomes. The weather collector alone writes first-seen verdicts.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .status import weather_database_path

VALID_VERDICTS = frozenset(("ALL", "CORRECT", "INCORRECT", "SKIP", "PENDING"))


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.utcoffset() is None:
        raise ValueError("NAIVE_WEATHER_HISTORY_TIME")
    return dt.astimezone(timezone.utc)


def _number(value: object, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("INVALID_HISTORY_NUMBER")
    num = float(value)
    if not math.isfinite(num) or num < low or num > high:
        raise ValueError("INVALID_HISTORY_NUMBER")
    return num


def _init_history(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("""CREATE TABLE IF NOT EXISTS weather_hourly_history_verdicts(
      city TEXT NOT NULL, event_ticker TEXT NOT NULL, market_ticker TEXT NOT NULL,
      verdict TEXT NOT NULL, forecast_sha TEXT NOT NULL,
      index_sha TEXT NOT NULL, market_sha TEXT NOT NULL,
      evidence_digest TEXT NOT NULL, verified_at TEXT NOT NULL,
      PRIMARY KEY(city,event_ticker))""")
    conn.execute("""CREATE INDEX IF NOT EXISTS wx_history_verdict_market
      ON weather_hourly_history_verdicts(market_ticker)""")


_SELECT = """SELECT
 s.city, s.event_ticker, s.market_ticker AS original_ticker,
 s.asof, s.target_utc, s.source_reading_ms, s.source_receipt,
 s.forecast_json, s.forecast_sha,
 o.market_ticker AS settled_ticker, o.result AS settled_result,
 o.observed_index_f, o.observed_event_ms, o.checked_at,
 o.source_index_sha, o.market_sha, o.source_index_raw, o.market_raw,
 v.market_ticker AS ledger_ticker, v.verdict AS ledger_verdict,
 v.forecast_sha AS ledger_forecast_sha, v.index_sha AS ledger_index_sha,
 v.market_sha AS ledger_market_sha,
 v.evidence_digest AS ledger_evidence_digest
 FROM weather_hourly_snapshots s
 LEFT JOIN weather_hourly_settlements o
   ON s.city=o.city AND s.event_ticker=o.event_ticker
 LEFT JOIN weather_hourly_history_verdicts v
   ON s.city=v.city AND s.event_ticker=v.event_ticker
 ORDER BY s.target_utc DESC,s.city ASC"""


def _query(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute(_SELECT).fetchall()


def _evaluate(row: sqlite3.Row) -> tuple[dict, dict | None]:
    """Verify the original frozen forecast, published index and exact ticker."""
    from .hourly import CITIES, listed_event_target, parse_index, parse_markets

    city, event = row["city"], row["event_ticker"]
    if city not in CITIES or not isinstance(event, str):
        raise ValueError("HISTORY_CITY_EVENT_MISMATCH")
    raw, sha = row["forecast_json"], row["forecast_sha"]
    if not isinstance(raw, str) or not isinstance(sha, str) or _hash(raw) != sha:
        raise ValueError("TAMPERED_HOURLY_FORECAST")
    frozen = json.loads(raw)
    asof = _utc(row["asof"])
    target = _utc(row["target_utc"])
    if (not isinstance(frozen, dict) or
        frozen.get("city") != city or frozen.get("event_ticker") != event or
        frozen.get("source") != "KALSHI_WEATHER_INDEX" or
        frozen.get("status") != "READY_RESEARCH" or
        _utc(frozen["asof"]) != asof or _utc(frozen["target_utc"]) != target or
        listed_event_target(city,event) != target or asof >= target or
        _utc(row["source_receipt"]) > asof or
        frozen.get("source_received_at") != row["source_receipt"] or
        frozen.get("source_event_ms") != row["source_reading_ms"] or
        isinstance(row["source_reading_ms"], bool) or
        not isinstance(row["source_reading_ms"], int) or
        row["source_reading_ms"] > int(asof.timestamp()*1000)):
        raise ValueError("HISTORY_FORECAST_CHRONOLOGY_MISMATCH")
    selected = frozen.get("selected_market")
    if not isinstance(selected,dict) or selected.get("ticker") != row["original_ticker"]:
        raise ValueError("HISTORY_SELECTED_MARKET_CHANGED")
    threshold = _number(selected.get("threshold_f"), -129.,159.)
    p = _number(selected.get("probability_yes"),0.,1.)
    if not row["original_ticker"].startswith(event+"-"):
        raise ValueError("HISTORY_MARKET_EVENT_MISMATCH")
    closing = selected.get("close_time_utc")
    if closing is not None and _utc(closing) <= asof:
        raise ValueError("HISTORY_SNAPSHOT_AFTER_MARKET_CLOSE")
    direction = "ABOVE" if p > .5 else "BELOW" if p < .5 else "SKIP"
    observed_result = row["settled_result"]
    if observed_result is None:
        if row["ledger_verdict"] is not None:
            raise ValueError("HISTORY_VERDICT_WITH_MISSING_SETTLEMENT")
        return frozen, None

    if (observed_result not in ("yes","no") or
        row["settled_ticker"] != row["original_ticker"] or
        _hash(row["source_index_raw"]) != row["source_index_sha"] or
        _hash(row["market_raw"]) != row["market_sha"]):
        raise ValueError("HISTORY_SETTLEMENT_SOURCE_CHANGED")
    checked = _utc(row["checked_at"])
    if not target+timedelta(minutes=6) <= checked <= target+timedelta(days=7):
        raise ValueError("HISTORY_SETTLEMENT_CHECK_OUTSIDE_WINDOW")
    index_payload = json.loads(row["source_index_raw"])
    observations = parse_index(index_payload, city, checked,
                               max_age_seconds=8*24*3600)
    start_ms = int((target-timedelta(hours=1)).timestamp()*1000)
    target_ms = int(target.timestamp()*1000)
    eligible = [point for point in observations
                if start_ms <= point["event_ms"] <= target_ms]
    if not eligible:
        raise ValueError("HISTORY_NO_CANONICAL_INDEX")
    canonical = max(eligible,key=lambda point:point["event_ms"])
    if (canonical["event_ms"] != row["observed_event_ms"] or
        abs(canonical["temp_f"] - _number(row["observed_index_f"],-129.,159.)) > 1e-6 or
        canonical["config_version"] != frozen.get("source_config_version")):
        raise ValueError("HISTORY_OBSERVED_INDEX_CHANGED")
    markets = parse_markets(json.loads(row["market_raw"]),city,event,settled=True)
    matching = [m for m in markets if m["ticker"]==row["original_ticker"]]
    if (len(matching)!=1 or matching[0]["result"] != observed_result or
        abs(matching[0]["threshold_f"]-threshold)>1e-6 or
        (canonical["temp_f"]>=threshold) != (observed_result=="yes")):
        raise ValueError("HISTORY_MARKET_OUTCOME_CONFLICT")
    settlement_ts = matching[0].get("settlement_ts")
    if (not isinstance(settlement_ts,str) or
        not target <= _utc(settlement_ts) <= checked):
        raise ValueError("HISTORY_SETTLEMENT_TIME_INVALID")

    verdict = ("SKIP" if direction=="SKIP" else
               "CORRECT" if (direction=="ABOVE") == (observed_result=="yes")
               else "INCORRECT")
    digest = _hash("|".join((
        sha,row["source_index_sha"],row["market_sha"],row["original_ticker"],
        observed_result,verdict)))
    return frozen, {
        "verdict":verdict,"forecast":direction,
        "actual_side":"ABOVE" if observed_result=="yes" else "BELOW",
        "observed_index_f":canonical["temp_f"],
        "evidence_digest":digest,
        "evidence_kind":("EXACT_TARGET_INDEX" if canonical["event_ms"]==target_ms
                         else "LATEST_PUBLISHED_IN_HOUR"),
    }


def _ledger_matches(row: sqlite3.Row, match: dict) -> bool:
    return (
        row["ledger_ticker"]==row["original_ticker"] and
        row["ledger_verdict"]==match["verdict"] and
        row["ledger_forecast_sha"]==row["forecast_sha"] and
        row["ledger_index_sha"]==row["source_index_sha"] and
        row["ledger_market_sha"]==row["market_sha"] and
        row["ledger_evidence_digest"]==match["evidence_digest"]
    )


def _empty() -> dict:
    return {
        "mode":"HOURLY_HISTORY_READ_ONLY","real_orders_enabled":False,
        "status":"WAITING_FOR_RECORDED_WEATHER_FORECASTS",
        "calibration_certified":False,
        "summary":{
            "total_final_forecasts":0,"correct":0,"incorrect":0,"skipped":0,
            "settled_directional_forecasts":0,"accuracy":None,
            "pending_settlement":0,"unverified_settlements":0,
        },
        "forecasts":[],"filtered_count":0,"offset":0,"limit":50,"next_offset":None,
    }


def sync_hourly_history(db_path: Path) -> dict:
    """Collector-only durable append-only verdicts from prior frozen evidence."""
    path=Path(db_path)
    result={"mode":"HOURLY_HISTORY_READ_ONLY","real_orders_enabled":False,
            "recorded":0,"unverified":0,"status":"NO_DATABASE"}
    if not path.is_file():
        return result
    try:
        with closing(sqlite3.connect(path,timeout=15)) as conn:
            _init_history(conn)
            rows=_query(conn)
            for row in rows:
                if row["settled_result"] is None:
                    continue
                try:
                    _, verified=_evaluate(row)
                    if verified is None:
                        continue
                    if row["ledger_verdict"] is not None:
                        if not _ledger_matches(row,verified):
                            result["unverified"]+=1
                        continue
                    inserted=conn.execute("""INSERT OR IGNORE INTO weather_hourly_history_verdicts(
                      city,event_ticker,market_ticker,verdict,forecast_sha,index_sha,
                      market_sha,evidence_digest,verified_at)
                      VALUES(?,?,?,?,?,?,?,?,?)""",(
                      row["city"],row["event_ticker"],row["original_ticker"],
                      verified["verdict"],row["forecast_sha"],
                      row["source_index_sha"],row["market_sha"],
                      verified["evidence_digest"],
                      datetime.now(timezone.utc).isoformat())).rowcount
                    result["recorded"]+=inserted
                except (sqlite3.Error,ValueError,KeyError,TypeError,
                        OverflowError,AttributeError,json.JSONDecodeError):
                    result["unverified"]+=1
            conn.commit()
        result["status"]="READY" if not result["unverified"] else "UNVERIFIED_EVIDENCE"
    except (sqlite3.Error,OSError,ValueError,KeyError,TypeError,
            AttributeError,OverflowError) as exc:
        result["status"]="HOURLY_HISTORY_SYNC_ERROR"
        result["reason"]=f"{type(exc).__name__}: {exc}"[:180]
    return result


def read_hourly_history(root: Path, *, db_path: Path | None = None,
                        preview: bool = False, city: str = "all",
                        verdict: str = "ALL", limit: int = 50,
                        offset: int = 0) -> dict:
    """Read-only complete audit. Scores count only verified stored verdicts."""
    from .hourly import CITIES
    if city not in ("all",*CITIES):
        raise ValueError("UNKNOWN_WEATHER_HISTORY_CITY")
    if verdict not in VALID_VERDICTS:
        raise ValueError("UNKNOWN_WEATHER_HISTORY_FILTER")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("INVALID_WEATHER_HISTORY_LIMIT")
    if type(offset) is not int or not 0 <= offset <= 100000:
        raise ValueError("INVALID_WEATHER_HISTORY_OFFSET")
    report=_empty()
    report.update({"limit":limit,"offset":offset,"city":city,"filter":verdict})
    path=Path(db_path or weather_database_path(root))
    if preview:
        report["status"]="PREVIEW_NO_REAL_WEATHER_HISTORY"
        return report
    if not path.is_file():
        return report
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+"?mode=ro",
                                    uri=True,timeout=4)) as conn:
            conn.execute("PRAGMA query_only=ON")
            # Historic databases before V11 can lack the new ledger.
            tables={r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if "weather_hourly_snapshots" not in tables:
                return report
            if "weather_hourly_history_verdicts" not in tables:
                report["status"]="WAITING_FOR_VERDICT_SYNC"
                return report
            rows=_query(conn)
        summary=report["summary"]
        all_rows=[]
        for row in rows:
            frozen,verified=_evaluate(row)
            if city!="all" and row["city"]!=city:
                continue
            selected=frozen["selected_market"]
            p=float(selected["probability_yes"])
            direction="ABOVE" if p>.5 else "BELOW" if p<.5 else "SKIP"
            actual=None
            result="PENDING"
            evidence_kind=None
            if row["ledger_verdict"] is not None:
                if verified is None or not _ledger_matches(row,verified):
                    raise ValueError("TAMPERED_WEATHER_HISTORY_VERDICT")
                result=verified["verdict"]
                actual=verified["actual_side"]
                evidence_kind=verified["evidence_kind"]
            elif row["settled_result"] is not None:
                # A settlement has been received but its verified verdict
                # still awaits the next owned collector journal pass.
                summary["unverified_settlements"]+=1

            summary["total_final_forecasts"]+=1
            if result=="CORRECT":
                summary["correct"]+=1
            elif result=="INCORRECT":
                summary["incorrect"]+=1
            elif result=="SKIP":
                summary["skipped"]+=1
            else:
                summary["pending_settlement"]+=1

            all_rows.append({
                "city":row["city"],"event_ticker":row["event_ticker"],
                "market_ticker":row["original_ticker"],
                "market_close":selected.get("close_time_utc"),
                "target_utc":row["target_utc"],"forecast_time":row["asof"],
                "forecast":direction,
                "directional_probability":max(p,1-p) if direction!="SKIP" else None,
                "confidence":(2*max(p,1-p)-1) if direction!="SKIP" else None,
                "model_kind":frozen.get("model_kind","UNKNOWN"),
                "threshold_f":float(selected["threshold_f"]),
                "actual_side":actual,"verdict":result,
                "observed_index_f":verified["observed_index_f"] if actual else None,
                "evidence_kind":evidence_kind,
            })
        n=summary["correct"]+summary["incorrect"]
        summary["settled_directional_forecasts"]=n
        summary["accuracy"]=summary["correct"]/n if n else None
        filtered=[r for r in all_rows if verdict=="ALL" or r["verdict"]==verdict]
        report["filtered_count"]=len(filtered)
        report["forecasts"]=filtered[offset:offset+limit]
        report["next_offset"]=offset+limit if offset+limit<len(filtered) else None
        report["status"]="READY" if all_rows else "WAITING_FOR_RECORDED_WEATHER_FORECASTS"
        if summary["unverified_settlements"]:
            report["status"]="AWAITING_VERDICT_RECONCILIATION"
    except (sqlite3.Error,OSError,ValueError,KeyError,TypeError,
            OverflowError,json.JSONDecodeError,AttributeError) as exc:
        empty=_empty()
        report.update({
            "status":"HOURLY_HISTORY_AUDIT_ERROR",
            "reason":f"{type(exc).__name__}: {exc}"[:180],
            "summary":empty["summary"],"forecasts":[],"filtered_count":0,
            "next_offset":None,
        })
    return report
