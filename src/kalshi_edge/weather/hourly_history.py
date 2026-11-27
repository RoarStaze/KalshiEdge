"""Read-only, source-audited, one-per-hour WeatherEdge forecast history.

No external calls, order writes, database writes, inferred settlements or
synthetic sample rows. A city/hour never contributes more than one result.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .hourly import CITIES, listed_event_target, parse_index, parse_markets
from .hourly_forward import _frozen, _outcome, _utc
from .status import weather_database_path
from .hourly_market_results import verify_final_market

MAX_AUDIT_ROWS = 30000
MAX_DISPLAY_ROWS = 750
MIN_ACCURACY_HOURS = 10


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _probability(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("INVALID_RECORDED_HOURLY_PROBABILITY")
    p = float(value)
    if not math.isfinite(p) or not 0 <= p <= 1:
        raise ValueError("INVALID_RECORDED_HOURLY_PROBABILITY")
    return p


def _base_report() -> dict:
    return {
        "mode": "HOURLY_HISTORY_READ_ONLY",
        "status": "NO_RECORDED_HOURLY_FORECASTS",
        "source": "KALSHI_WEATHER_INDEX",
        "real_orders_enabled": False,
        "calibration_certified": False,
        "summary": {
            "total_final_forecasts": 0,
            "settled_directional_forecasts": 0,
            "correct": 0,
            "incorrect": 0,
            "skipped": 0,
            "pending_settlement": 0,
            "accuracy": None,
        },
        "forecasts": [],
    }


def _rows(conn: sqlite3.Connection, sql: str) -> list[tuple]:
    try:
        rows = conn.execute(sql, (MAX_AUDIT_ROWS + 1,)).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return []
        raise
    if len(rows) > MAX_AUDIT_ROWS:
        raise ValueError("HOURLY_HISTORY_AUDIT_LIMIT_EXCEEDED")
    return rows


def _checked_forward(row: tuple, market_evidence: tuple | None = None) -> tuple[tuple, dict]:
    (city, event, ticker, target, lead, asof, raw, digest,
     result, value, event_ms, checked_at, config, index_raw,
     index_sha, market_raw, market_sha) = row
    frozen = _frozen(raw, digest)
    if (city != frozen["city"] or event != frozen["event_ticker"] or
        ticker != frozen["market_ticker"] or target != frozen["target_utc"] or
        asof != frozen["asof"] or lead != frozen["lead_bucket"]):
        raise ValueError("HOURLY_HISTORY_FORWARD_IDENTITY_CHANGED")
    if result is None:
        observed = None
    else:
        observed = _outcome((result, value, event_ms, checked_at, config,
                             index_raw, index_sha, market_raw, market_sha),
                            frozen)
    official = (verify_final_market(city,event,ticker,
        frozen["threshold_f"],*market_evidence) if market_evidence else None)
    if observed is not None and official is not None and (
        bool(observed[0]) != (official=="yes")):
        raise ValueError("HOURLY_FINAL_MARKET_AND_INDEX_DISAGREE")
    actual = (("ABOVE" if observed[0] else "BELOW") if observed is not None
              else ("ABOVE" if official=="yes" else "BELOW") if official else None)
    info = {
        "city": city, "event_ticker": event, "market_ticker": ticker,
        "target_utc": target,
        "market_close": frozen["market_close_utc"],
        "asof": asof, "lead_minutes": lead,
        "model_kind": frozen["model_kind"],
        "probability_yes": _probability(frozen["probability_yes"]),
        "threshold_f": frozen["threshold_f"],
        "actual":actual,
        "observed_index_f": observed[1] if observed is not None else None,
        "evidence_kind": ("INDEX_RECONCILED_SETTLEMENT" if observed is not None else
                          "KALSHI_FINALIZED_MARKET" if official else None),
        "source": "V9_FORWARD_CHECKPOINT",
    }
    # Select the smallest available precommitted lead bucket. Selection
    # must NOT depend on whether a later settlement was recorded.
    return ((city, event), (0, lead, asof), info)


def _checked_legacy(row: tuple, market_evidence: tuple | None = None) -> tuple[tuple, dict]:
    (city, event, ticker, target_raw, asof_raw, source_ms, receipt,
     raw, digest, actual_result, observed_value, observed_ms, checked_at,
     index_raw, index_sha, market_raw, market_sha, recorded_ticker) = row
    if _digest(raw) != digest:
        raise ValueError("TAMPERED_LEGACY_HOURLY_FORECAST")
    frozen = json.loads(raw)
    if not isinstance(frozen, dict) or city not in CITIES:
        raise ValueError("INVALID_LEGACY_HOURLY_FORECAST")
    asof, target = _utc(asof_raw), _utc(target_raw)
    if (frozen.get("city") != city or frozen.get("event_ticker") != event or
        frozen.get("target_utc") != target_raw or frozen.get("asof") != asof_raw or
        not asof < target or listed_event_target(city, event) != target or
        frozen.get("source_event_ms") != source_ms or
        frozen.get("source_received_at") != receipt or
        _utc(receipt) > asof):
        raise ValueError("LEGACY_HOURLY_CHRONOLOGY_CHANGED")
    selected = frozen.get("selected_market")
    if not isinstance(selected, dict) or selected.get("ticker") != ticker:
        raise ValueError("LEGACY_SELECTED_MARKET_CHANGED")
    strike = selected.get("threshold_f")
    if (isinstance(strike, bool) or not isinstance(strike, (int, float)) or
        not math.isfinite(strike) or not -130 < strike < 160):
        raise ValueError("INVALID_LEGACY_STRIKE")
    close_raw = selected.get("close_time_utc")
    market_close = None
    if close_raw is not None:
        close = _utc(close_raw)
        if not asof < close:
            raise ValueError("LEGACY_FORECAST_AFTER_MARKET_CLOSE")
        market_close = close.isoformat()
    p = _probability(selected.get("probability_yes"))
    if actual_result is None:
        observed = None
    else:
        if actual_result not in ("yes", "no") or recorded_ticker != ticker:
            raise ValueError("LEGACY_SETTLEMENT_TICKER_CHANGED")
        if (_digest(index_raw) != index_sha or
            _digest(market_raw) != market_sha):
            raise ValueError("TAMPERED_LEGACY_SETTLEMENT_SOURCE")
        checked = _utc(checked_at)
        if not target + timedelta(minutes=6) <= checked <= target + timedelta(days=7):
            raise ValueError("INVALID_LEGACY_SETTLEMENT_RECEIPT")
        index = parse_index(json.loads(index_raw), city, checked,
                            max_age_seconds=8 * 24 * 3600)
        start = int((target - timedelta(minutes=60)).timestamp() * 1000)
        end = int(target.timestamp() * 1000)
        viable = [x for x in index if start <= x["event_ms"] <= end]
        if not viable:
            raise ValueError("NO_CANONICAL_LEGACY_INDEX_READING")
        canonical = max(viable, key=lambda x: x["event_ms"])
        market = [x for x in parse_markets(json.loads(market_raw),city,event,
                                           settled=True)
                  if x["ticker"] == ticker]
        if (len(market) != 1 or market[0].get("result") != actual_result or
            canonical["event_ms"] != observed_ms or
            abs(canonical["temp_f"] - observed_value) > 1e-6 or
            abs(market[0]["threshold_f"] - strike) > 1e-6 or
            (canonical["temp_f"] >= strike) != (actual_result == "yes")):
            raise ValueError("LEGACY_HOURLY_SETTLEMENT_RECONCILIATION_CHANGED")
        observed = ("ABOVE" if actual_result == "yes" else "BELOW",observed_value)
    official=(verify_final_market(city,event,ticker,strike,*market_evidence)
              if market_evidence else None)
    if observed is not None and official is not None and (
        (observed[0]=="ABOVE") != (official=="yes")):
        raise ValueError("LEGACY_FINAL_MARKET_AND_INDEX_DISAGREE")
    actual = (observed[0] if observed is not None else
              ("ABOVE" if official=="yes" else "BELOW") if official else None)
    model = frozen.get("model_kind")
    info = {
        "city": city, "event_ticker": event, "market_ticker": ticker,
        "target_utc": target.isoformat(), "market_close": market_close,
        "asof": asof.isoformat(), "lead_minutes":
            round((target - asof).total_seconds()/60,3),
        "model_kind": model if isinstance(model,str) else "UNSPECIFIED",
        "probability_yes": p, "threshold_f": strike,
        "actual": actual,
        "observed_index_f": observed[1] if observed is not None else None,
        "evidence_kind": ("INDEX_RECONCILED_SETTLEMENT" if observed is not None else
                          "KALSHI_FINALIZED_MARKET" if official else None),
        "source": "LEGACY_PRECOMMITTED",
    }
    return ((city,event),(1,0,asof_raw),info)


def read_hourly_history(root: Path, *, db_path: Path | None = None,
                        preview: bool = False) -> dict:
    """Return source-audited history, newest-first, without writing data."""
    out = _base_report()
    from .status import read_collector_health
    out["collector"]=read_collector_health(root,preview=preview)
    path = Path(db_path or weather_database_path(root))
    if preview or not path.is_file():
        return out

    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro",
                                    uri=True,timeout=3)) as conn:
            conn.execute("PRAGMA query_only=ON")
            market_evidence={}
            market_checks={}
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                            "AND name='weather_hourly_final_market_checks'").fetchone():
                for city,event,ticker,state,detail,checked_at,attempts in conn.execute(
                    """SELECT city,event_ticker,market_ticker,state,detail,
                              checked_at,attempt_count
                       FROM weather_hourly_final_market_checks"""):
                    market_checks[city,event,ticker]=(
                        state,detail,checked_at,attempts)
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                            "AND name='weather_hourly_final_market_results'").fetchone():
                for city,event,ticker,result,settled,received,raw,sha in conn.execute(
                    """SELECT city,event_ticker,market_ticker,result,settled_at,
                              received_at,market_raw,market_sha
                       FROM weather_hourly_final_market_results"""):
                    market_evidence[city,event,ticker]=(
                        result,settled,received,raw,sha)
            forward = _rows(conn, """SELECT
                s.city,s.event_ticker,s.market_ticker,s.target_utc,
                s.lead_bucket,s.asof,s.forecast_json,s.forecast_sha,
                o.result,o.observed_index_f,o.observed_event_ms,o.checked_at,
                o.index_config_version,o.source_index_raw,o.source_index_sha,
                o.market_raw,o.market_sha
              FROM weather_hourly_forward_snapshots s
              LEFT JOIN weather_hourly_forward_outcomes o
                ON s.city=o.city AND s.event_ticker=o.event_ticker
                 AND s.market_ticker=o.market_ticker
              ORDER BY s.target_utc DESC LIMIT ?""")
            legacy = _rows(conn, """SELECT
                s.city,s.event_ticker,s.market_ticker,s.target_utc,s.asof,
                s.source_reading_ms,s.source_receipt,s.forecast_json,
                s.forecast_sha,o.result,o.observed_index_f,o.observed_event_ms,
                o.checked_at,o.source_index_raw,o.source_index_sha,
                o.market_raw,o.market_sha,o.market_ticker
              FROM weather_hourly_snapshots s
              LEFT JOIN weather_hourly_settlements o
                ON s.city=o.city AND s.event_ticker=o.event_ticker
              ORDER BY s.target_utc DESC LIMIT ?""")

        selected: dict[tuple[str,str], tuple[tuple,dict]] = {}
        for row in forward:
            key, preference, info = _checked_forward(
                row,market_evidence.get((row[0],row[1],row[2])))
            existing = selected.get(key)
            if existing is None or preference < existing[0]:
                selected[key] = (preference, info)
        for row in legacy:
            key, preference, info = _checked_legacy(
                row,market_evidence.get((row[0],row[1],row[2])))
            existing = selected.get(key)
            if existing is None or preference < existing[0]:
                selected[key] = (preference, info)

        rows=[]
        correct=incorrect=pending=0
        for _,info in selected.values():
            p=info["probability_yes"]
            side="ABOVE" if p>.5 else ("BELOW" if p<.5 else "EVEN")
            actual=info.pop("actual")
            operational=market_checks.get(
                (info["city"],info["event_ticker"],info["market_ticker"]))
            info["settlement_check_state"]=operational[0] if operational else "NOT_CHECKED"
            info["settlement_check_reason"]=operational[1] if operational else None
            info["settlement_checked_at"]=operational[2] if operational else None
            info["settlement_check_attempts"]=operational[3] if operational else 0
            if actual is None:
                verdict="PENDING"
                pending+=1
                target_dt=_utc(info["target_utc"])
                now=datetime.now(timezone.utc)
                info["pending_reason"]=("HOUR_NOT_FINISHED" if target_dt>now else
                    "AWAITING_OFFICIAL_FINAL_RESULT")
            else:
                info["pending_reason"]=None
            if actual is not None and side == "EVEN":
                verdict="UNSCORED"  # Never invent a stored SKIP decision.
            elif actual is not None:
                verdict="CORRECT" if side==actual else "INCORRECT"
                if verdict=="CORRECT":
                    correct+=1
                else:
                    incorrect+=1
            rows.append({
                **info,
                "forecast":side,
                "directional_probability":max(p,1.-p) if side!="EVEN" else None,
                # Same directional-strength definition used by Bitcoin
                # history: 2*max(p_yes,p_no)-1. This is NOT measured skill.
                "confidence":round(2*max(p,1.-p)-1,6) if side!="EVEN" else None,
                "actual_side":actual,
                "verdict":verdict,
            })
        rows.sort(key=lambda x:(x["target_utc"],x["city"]),reverse=True)
        n=correct+incorrect
        summary=out["summary"]
        summary.update({
            "total_final_forecasts":len(rows),
            "settled_directional_forecasts":n,
            "correct":correct,"incorrect":incorrect,
            "pending_settlement":pending,
            "pending_unchecked":sum(
                1 for x in rows if x["verdict"]=="PENDING" and
                x["settlement_check_state"]=="NOT_CHECKED"),
            "pending_feed_errors":sum(
                1 for x in rows if x["verdict"]=="PENDING" and
                x["settlement_check_state"] in
                    ("FETCH_ERROR","INVALID_OFFICIAL_EVIDENCE")),
            "pending_waiting_exchange":sum(
                1 for x in rows if x["verdict"]=="PENDING" and
                x["settlement_check_state"]=="AWAITING_FINALIZATION"),
            # Show observed, source-verified arithmetic from the first
            # settled round. Under ten, label it as a small, uncalibrated
            # sample rather than hiding a real correct/incorrect ratio.
            "accuracy":round(correct/n,6) if n else None,
        })
        out["forecasts"]=rows[:MAX_DISPLAY_ROWS]
        if rows:
            out["status"]=("MEASURED_PROSPECTIVE_RESEARCH" if n>=MIN_ACCURACY_HOURS
                           else "SMALL_SAMPLE_UNCERTIFIED" if n
                           else "AWAITING_VERIFIED_SETTLEMENTS")
        return out
    except (sqlite3.Error, ValueError, OSError, KeyError, TypeError,
            OverflowError, json.JSONDecodeError, AttributeError) as exc:
        # A partial audit must never yield a favorable-looking history.
        out=_base_report()
        out["status"]="HOURLY_HISTORY_AUDIT_ERROR"
        out["reason"]=f"{type(exc).__name__}: {exc}"[:190]
        return out
