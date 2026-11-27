"""WeatherEdge V13: reproducible point-in-time paired model benchmark.

Research only: identical target hours, expanding training prefixes, never
an index value after each forecast's local receipt cutoff. Real Kalshi market
Brier/log loss use ONLY frozen exact markets and source-audited YES/NO labels.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .hourly_hybrid import fit_hybrid, predict_hybrid
from .predictor import normal_cdf
from .status import weather_database_path

BLOCK=24
FOLDS=3
MIN_PREFIX=72
MIN_EVAL=BLOCK*FOLDS
Z90=1.64485362695
MODEL="REGULARIZED_HOURLY_HYBRID_V13"


def _empty_metrics() -> dict:
    return {"hours":0,"mae_f":None,"crps":None,"coverage_90":None,
            "market_hours":0,"brier":None,"log_loss":None,
            "direction_accuracy":None}


def _empty_report(status: str) -> dict:
    return {"status":status,"incumbent_model":"LIVE_WEATHEREDGE_V12",
            "candidate_model":MODEL,
            "matched_hours":0,"verified_market_hours":0,
            "frozen_live_hours":0,
            "folds":[],"incumbent":_empty_metrics(),"candidate":_empty_metrics(),
            "parameters":None,"eligible_for_live_promotion":False,
            "calibration_certified":False,"real_orders_enabled":False}


def _iso(value: object) -> datetime:
    if not isinstance(value,str):
        raise ValueError("INVALID_HOURLY_BENCHMARK_TIME")
    stamp=datetime.fromisoformat(value.replace("Z","+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("NAIVE_HOURLY_BENCHMARK_TIME")
    return stamp.astimezone(timezone.utc)


def _pair(row: dict,mean: float,sigma: float) -> dict:
    from .hourly_nowcast import _crps
    actual=float(row["observed_index_f"])
    if not all(math.isfinite(v) for v in (actual,mean,sigma)) or sigma<=0:
        raise ValueError("INVALID_PAIRED_PREDICTION")
    result={"err":abs(actual-mean),"crps":_crps(mean,sigma,actual),
            "covered":int(abs(actual-mean)<=Z90*sigma),
            "market":None}
    strike=row.get("market_threshold_f")
    y=row.get("market_outcome_yes")
    if strike is not None or y is not None:
        if (type(y) is not bool or type(strike) not in (int,float) or
            not math.isfinite(strike) or not -130.<strike<160. or
            not isinstance(row.get("market_evidence_kind"),str) or
            not row["market_evidence_kind"]):
            raise ValueError("MARKET_RESULT_WITHOUT_VERIFIED_FROZEN_STRIKE")
        p=max(1e-8,min(1.-1e-8,1.-normal_cdf(float(strike),mean,sigma)))
        result["market"]={
            "brier":(p-float(y))**2,
            "log_loss":-math.log(p if y else 1.-p),
            "correct":int((p>=.5)==y),
        }
    return result


def _aggregate(records: list[dict]) -> dict:
    if not records:
        return _empty_metrics()
    n=len(records)
    market=[r["market"] for r in records if r["market"] is not None]
    k=len(market)
    return {"hours":n,
            "mae_f":round(sum(r["err"] for r in records)/n,6),
            "crps":round(sum(r["crps"] for r in records)/n,6),
            "coverage_90":round(sum(r["covered"] for r in records)/n,6),
            "market_hours":k,
            "brier":round(sum(r["brier"] for r in market)/k,6) if k else None,
            "log_loss":round(sum(r["log_loss"] for r in market)/k,6) if k else None,
            "direction_accuracy":round(sum(r["correct"] for r in market)/k,6)
                                if k else None}


def _base_prediction(row: dict,decision: dict) -> tuple[float,float]:
    from .hourly_nowcast import nowcast_prediction
    return nowcast_prediction(
        row["current_index_f"],row["baseline_sigma_f"],
        row["fast_slope"],row["slow_slope"],row["hours_ahead"],
        decision,diurnal_delta_f=row.get("diurnal_delta_f"))


def compare_models(cases: list[dict], *,
                   incumbent_selector=None) -> dict:
    """Three disjoint 24-hour out-of-training comparisons; identical row set.

    Any missing or corrupted case fails closed. The candidate is never tuned
    using market settlement outcomes; those labels are held solely for score.
    """
    report=_empty_report("INSUFFICIENT_WALKFORWARD_HOURS")
    if len(cases)<MIN_PREFIX+MIN_EVAL:
        return report
    from .hourly_nowcast import choose_index_nowcast
    select=incumbent_selector or choose_index_nowcast
    ordered=sorted(cases,key=lambda x:_iso(x["target_utc"]))
    targets=[_iso(row["target_utc"]) for row in ordered]
    if (len(set(targets))!=len(targets) or
        any(_iso(row["forecast_asof"])>=_iso(row["target_utc"])
            for row in ordered)):
        return _empty_report("INVALID_CHRONOLOGICAL_HOURLY_HISTORY")
    start=len(ordered)-MIN_EVAL
    if any((targets[i]-targets[i-1]).total_seconds()!=3600
           for i in range(start+1,len(targets))):
        return _empty_report("INSUFFICIENT_CONTIGUOUS_HOURLY_HISTORY")
    base_records=[]
    new_records=[]
    frozen_live_hours=0
    frozen_models=set()
    last_params=None
    for i in range(FOLDS):
        end_train=start+i*BLOCK
        older=ordered[:end_train]
        holdout=ordered[end_train:end_train+BLOCK]
        try:
            incumbent=select(older)
            params=fit_hybrid(older)
            older_records=[]
            newer_records=[]
            fold_frozen=0
            for row in holdout:
                if ("live_frozen_mean_f" in row and
                    "live_frozen_sigma_f" in row and
                    isinstance(row.get("live_frozen_model_kind"),str)):
                    old_mean=float(row["live_frozen_mean_f"])
                    old_sigma=float(row["live_frozen_sigma_f"])
                    if (not all(math.isfinite(v) for v in
                            (old_mean,old_sigma)) or
                        old_sigma<=.001 or old_sigma>30.):
                        raise ValueError("INVALID_FROZEN_LIVE_PREDICTION")
                    fold_frozen+=1
                    frozen_models.add(row["live_frozen_model_kind"])
                else:
                    # The reconstructed V12 observation path is useful
                    # diagnostic research but not the full live model, which
                    # can include settlement and physical ensemble upgrades.
                    old_mean,old_sigma=_base_prediction(row,incumbent)
                new_mean,new_sigma=predict_hybrid(row,params)
                older_records.append(_pair(row,old_mean,old_sigma))
                newer_records.append(_pair(row,new_mean,new_sigma))
        except (ValueError,KeyError,TypeError,OverflowError,
                ZeroDivisionError,IndexError):
            return _empty_report("INVALID_PAIRED_MODEL_EVIDENCE")
        base_records.extend(older_records)
        new_records.extend(newer_records)
        frozen_live_hours+=fold_frozen
        old=_aggregate(older_records)
        new=_aggregate(newer_records)
        report["folds"].append({
            "fold":i+1,"hours":BLOCK,
            "frozen_live_hours":fold_frozen,
            "train_hours":len(older),
            "start_target_utc":holdout[0]["target_utc"],
            "end_target_utc":holdout[-1]["target_utc"],
            "incumbent_model":incumbent.get("model") if incumbent.get("enabled")
                               else "CURRENT_INDEX_PERSISTENCE",
            "incumbent":old,"candidate":new,
        })
        last_params=params
    a=_aggregate(base_records)
    b=_aggregate(new_records)
    report.update({"matched_hours":MIN_EVAL,
                   "verified_market_hours":a["market_hours"],
                   "frozen_live_hours":frozen_live_hours,
                   "frozen_live_model_kinds":sorted(frozen_models),
                   "incumbent":a,"candidate":b,
                   "parameters":last_params})
    good_temp=(
        b["mae_f"]<=a["mae_f"]-.05 and
        b["crps"]<=a["crps"]-.025 and
        b["coverage_90"]>=.75 and
        b["coverage_90"]>=a["coverage_90"]-.07 and
        sum(f["candidate"]["crps"]<f["incumbent"]["crps"]
            for f in report["folds"])>=2)
    if frozen_live_hours<MIN_EVAL:
        report["status"]="INSUFFICIENT_FROZEN_LIVE_FORECASTS"
        return report
    if a["market_hours"]<24 or b["market_hours"]<24:
        report["status"]="INSUFFICIENT_VERIFIED_MARKET_PAIRS"
        return report
    good_market=(
        b["brier"]<=a["brier"]-.01 and
        b["direction_accuracy"]>=a["direction_accuracy"]-.01)
    if good_temp and good_market:
        report["status"]="PAIRED_EVIDENCE_IMPROVED"
        report["eligible_for_live_promotion"]=True
    else:
        report["status"]="PAIRED_GATES_NOT_MET"
    return report


def _freeze_market_pairs(conn: sqlite3.Connection,city: str,
                         lead: int,cases: list[dict]) -> None:
    """Decorate only exact saved forward checkpoints with audited outcomes."""
    from .hourly_forward import _frozen,_outcome
    from .hourly_market_results import verify_final_market

    tables={row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "weather_hourly_forward_snapshots" not in tables:
        return
    expected={row["target_utc"]:row for row in cases}
    finals={}
    if "weather_hourly_final_market_results" in tables:
        for data in conn.execute("""SELECT city,event_ticker,market_ticker,
                     result,settled_at,received_at,market_raw,market_sha
                     FROM weather_hourly_final_market_results WHERE city=?""",
                     (city,)):
            finals[tuple(data[:3])]=data[3:]
    source_outcomes={}
    if "weather_hourly_forward_outcomes" in tables:
        for data in conn.execute("""SELECT city,event_ticker,market_ticker,
             result,observed_index_f,observed_event_ms,checked_at,
             index_config_version,source_index_raw,source_index_sha,
             market_raw,market_sha
             FROM weather_hourly_forward_outcomes WHERE city=?""",(city,)):
            source_outcomes[tuple(data[:3])]=data[3:]

    for city0,event,ticker,target,checkpoint,asof,raw,digest in conn.execute(
        """SELECT city,event_ticker,market_ticker,target_utc,lead_bucket,
                  asof,forecast_json,forecast_sha
           FROM weather_hourly_forward_snapshots
           WHERE city=? AND lead_bucket=? ORDER BY target_utc""",(city,lead)):
        row=expected.get(target)
        if row is None:
            continue
        frozen=_frozen(raw,digest)
        if (frozen["city"]!=city0 or frozen["event_ticker"]!=event or
            frozen["market_ticker"]!=ticker or frozen["target_utc"]!=target or
            frozen["lead_bucket"]!=checkpoint or frozen["asof"]!=asof):
            raise ValueError("TAMPERED_COMPARISON_FORECAST_IDENTITY")
        if abs((_iso(row["forecast_asof"])-_iso(asof)).total_seconds())>90:
            continue
        # Actual precommitted full live forecast, not just V12 observation
        # nowcast. This includes any active V1 settlement correction or
        # HRRR/NBM physical blend from the originally stored prediction.
        row.update({"live_frozen_mean_f":float(frozen["predicted_index_f"]),
                    "live_frozen_sigma_f":float(frozen["sigma_f"]),
                    "live_frozen_model_kind":frozen["model_kind"]})
        key=(city0,event,ticker)
        observed=source_outcomes.get(key)
        official=finals.get(key)
        y=None
        evidence=None
        if observed is not None:
            match=_outcome(observed,frozen)
            if match is not None:
                y=bool(match[0])
                evidence="INDEX_RECONCILED_SETTLEMENT"
        if official is not None:
            verified=verify_final_market(city0,event,ticker,
                        frozen["threshold_f"],*official)
            if y is not None and y!=(verified=="yes"):
                raise ValueError("OFFICIAL_MARKET_AND_INDEX_CONFLICT")
            y=(verified=="yes")
            if evidence is None:
                evidence="KALSHI_FINALIZED_MARKET"
        if y is not None:
            row.update({"market_threshold_f":frozen["threshold_f"],
                        "market_outcome_yes":bool(y),
                        "market_evidence_kind":evidence})


def compare_local_weather_models(db_path: Path, *,
                                 asof: datetime | None = None,
                                 cities: tuple[str,...] | None = None,
                                 leads: tuple[int,...] | None = None) -> dict:
    """Read original local receipts, not public hindsight-retrieved Index bars."""
    from .hourly import CITIES
    from .hourly_nowcast import LEAD_MINUTES,index_cases,_utc
    path=Path(db_path)
    report={"mode":"HOURLY_MODEL_PAIRED_COMPARISON_V13",
            "status":"NO_LOCAL_WEATHER_HISTORY","real_orders_enabled":False,
            "calibration_certified":False,"any_promoted":False,
            "groups":[],"asof_utc":None}
    if not path.is_file():
        return report
    now=_utc(asof or datetime.now(timezone.utc))
    report["asof_utc"]=now.isoformat()
    requested_cities=tuple(CITIES) if cities is None else cities
    requested_leads=LEAD_MINUTES if leads is None else leads
    if any(c not in CITIES for c in requested_cities):
        raise ValueError("UNKNOWN_COMPARISON_CITY")
    if any(l not in LEAD_MINUTES for l in requested_leads):
        raise ValueError("UNKNOWN_COMPARISON_LEAD")
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+"?mode=ro",
                                    uri=True,timeout=8)) as conn:
            conn.execute("PRAGMA query_only=ON")
            available={r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if "weather_hourly_index" not in available:
                report["status"]="INSUFFICIENT_HISTORY"
                return report
            for city in requested_cities:
                cfg_row=conn.execute("""SELECT config_version
                    FROM weather_hourly_index WHERE city=? AND received_at<=?
                    ORDER BY received_at DESC LIMIT 1""",(city,now.isoformat())).fetchone()
                if cfg_row is None:
                    for lead in requested_leads:
                        report["groups"].append({"city":city,"lead_minutes":lead,
                            **_empty_report("NO_SOURCE_INDEX_RECORDS")})
                    continue
                cfg=cfg_row[0]
                for lead in requested_leads:
                    cases=[]
                    try:
                        cases=index_cases(conn,city,now,lead_minutes=lead,
                                          config_version=cfg)
                        _freeze_market_pairs(conn,city,lead,cases)
                        paired=compare_models(cases)
                    except (sqlite3.Error,ValueError,KeyError,TypeError,
                            OverflowError,json.JSONDecodeError) as exc:
                        paired=_empty_report("SOURCE_AUDIT_ERROR")
                        paired["reason"]=f"{type(exc).__name__}: {exc}"[:140]
                    report["groups"].append({"city":city,"lead_minutes":lead,
                                               "config_version":cfg,
                                               "available_index_hours":len(cases),
                                               **paired})
        report["any_promoted"]=any(g["eligible_for_live_promotion"]
                                   for g in report["groups"])
        report["status"]="RESEARCH_SCORED" if any(
            g["matched_hours"] for g in report["groups"]) else "INSUFFICIENT_HISTORY"
    except (sqlite3.Error,OSError,ValueError,KeyError,TypeError):
        report["status"]="SOURCE_AUDIT_ERROR"
        report["any_promoted"]=False
        report["groups"]=[]
    return report




CACHE_NAME="hourly-model-comparison.json"
MAX_CACHE_BYTES=250_000


def _missing_cache(status: str) -> dict:
    return {"mode":"HOURLY_MODEL_PAIRED_COMPARISON_V13",
            "status":status,"real_orders_enabled":False,
            "calibration_certified":False,"any_promoted":False,
            "groups":[],"asof_utc":None}


def _canonical(data: dict) -> str:
    return json.dumps(data,sort_keys=True,separators=(",",":"),
                      allow_nan=False,ensure_ascii=True)


def write_local_comparison(db_path: Path,report: dict) -> None:
    """Collector-owned, atomic, checksum-checked summary; no raw local data."""
    if (not isinstance(report,dict) or
        report.get("mode")!="HOURLY_MODEL_PAIRED_COMPARISON_V13" or
        report.get("real_orders_enabled") is not False or
        report.get("calibration_certified") is not False or
        not isinstance(report.get("groups"),list) or
        len(report["groups"])>30):
        raise ValueError("INVALID_COMPARISON_REPORT")
    folder=Path(db_path).parent
    folder.mkdir(parents=True,exist_ok=True)
    data={**report,"computed_at_utc":datetime.now(timezone.utc).isoformat()}
    data.pop("cache_sha256",None)
    data["cache_sha256"]=hashlib.sha256(_canonical(data).encode()).hexdigest()
    encoded=_canonical(data)
    if len(encoded.encode())>MAX_CACHE_BYTES:
        raise ValueError("OVERSIZED_COMPARISON_REPORT")
    tmp=None
    try:
        with tempfile.NamedTemporaryFile(mode="w",encoding="utf-8",
               dir=folder,prefix=".model-comparison-",suffix=".tmp",
               delete=False) as f:
            tmp=Path(f.name)
            f.write(encoded)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp,folder/CACHE_NAME)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def read_local_comparison(db_path: Path,*,preview: bool = False) -> dict:
    """Never calculate a model on a web GET; read bounded recorded report."""
    if preview:
        return _missing_cache("PREVIEW_NO_LOCAL_MODEL_COMPARISON")
    path=Path(db_path).parent/CACHE_NAME
    if not path.is_file():
        return _missing_cache("NO_COMPARISON_REPORT")
    try:
        if path.stat().st_size>MAX_CACHE_BYTES:
            raise ValueError("COMPARISON_CACHE_TOO_LARGE")
        data=json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data,dict):
            raise ValueError("INVALID_COMPARISON_CACHE")
        signature=data.pop("cache_sha256",None)
        if not isinstance(signature,str) or hashlib.sha256(
                _canonical(data).encode()).hexdigest()!=signature:
            raise ValueError("CHANGED_COMPARISON_CACHE")
        if (data.get("mode")!="HOURLY_MODEL_PAIRED_COMPARISON_V13" or
            data.get("real_orders_enabled") is not False or
            data.get("calibration_certified") is not False or
            not isinstance(data.get("groups"),list) or
            len(data["groups"])>30):
            raise ValueError("INVALID_COMPARISON_MODE")
        timestamp=_iso(data["computed_at_utc"])
        if abs((datetime.now(timezone.utc)-timestamp).total_seconds())>3*3600:
            # A stale old report may be shown but may NOT authorize a model
            # to replace the current live predictor.
            data["status"]="STALE_COMPARISON_REPORT"
            data["any_promoted"]=False
            for group in data["groups"]:
                group["eligible_for_live_promotion"]=False
        return data
    except (OSError,ValueError,KeyError,TypeError,OverflowError,
            json.JSONDecodeError) as exc:
        return {**_missing_cache("INVALID_COMPARISON_CACHE"),
                "reason":f"{type(exc).__name__}: {exc}"[:150]}


def _load_cached_for_root(root: Path, *,preview: bool = False) -> dict:
    return read_local_comparison(weather_database_path(root),preview=preview)


def main() -> int:
    from kalshi_edge.bootstrap.config import BootstrapSettings
    parser=argparse.ArgumentParser(description=(
        "Compare V12 incumbent and V13 experimental WeatherEdge on "
        "unchanged, timestamped local weather observations. Never trades."))
    parser.add_argument("--db",type=Path,default=None,
                        help="Optional exact local WeatherEdge SQLite path.")
    args=parser.parse_args()
    db=args.db or weather_database_path(BootstrapSettings().bootstrap_dir)
    report=compare_local_weather_models(db)
    print(json.dumps(report,indent=2,allow_nan=False,sort_keys=True))
    return 0 if report["status"]!="SOURCE_AUDIT_ERROR" else 2


if __name__=="__main__":
    raise SystemExit(main())
