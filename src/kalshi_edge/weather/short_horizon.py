"""Point-in-time 15-minute-ahead airport temperature outlook.

No verified Kalshi 15-minute weather contracts are assumed to exist. This
research model estimates only the upcoming station METAR temperature, not a
Kalshi hourly Weather Index or any contract-specific official settlement.

Missing/late observations or unmatched NOAA forecast cycles block outputs.
The 1.25°F sigma and feature weights are deliberate UNCALIBRATED heuristics.
"""
from __future__ import annotations

import math
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .predictor import (
    STATIONS, NWP_MAX_AGE, _finite, _rows, _time, _utc, normal_cdf,
)
from .status import weather_database_path

HORIZON_MINUTES = 15
MAX_METAR_AGE = timedelta(minutes=20)
MIN_OBSERVATION_SEPARATION = timedelta(minutes=10)
MAX_OBSERVATION_SEPARATION = timedelta(minutes=45)
MIN_OBS_HISTORY_AGE = timedelta(minutes=65)
MODEL_SIGMA_F = 1.25  # ASSUMPTION; not a measured/calibrated 90% interval.
SIGMA_QUANTILE_90 = 1.64485362695


def _trend_from_observations(rows: list[tuple], asof: datetime) -> tuple:
    by_event: dict[datetime, tuple[datetime, float]] = {}
    for event, received, payload in rows:
        try:
            value=_finite(payload["temp_f"],-130,150)
        except (ValueError,TypeError,KeyError):
            continue
        if received>asof or event>asof:
            continue
        previous=by_event.get(event)
        if previous is None or received>previous[0]:
            by_event[event]=(received,value)
    if not by_event:
        raise LookupError("MISSING_METAR")
    events=sorted(by_event,reverse=True)
    latest_event=events[0]
    if asof-latest_event>MAX_METAR_AGE:
        raise LookupError("STALE_METAR")
    previous=next((t for t in events[1:] if
                   MIN_OBSERVATION_SEPARATION<=latest_event-t<=MAX_OBSERVATION_SEPARATION),None)
    if previous is None:
        raise LookupError("INSUFFICIENT_OBSERVATION_HISTORY")
    old=by_event[previous][1]
    new=by_event[latest_event][1]
    duration=(latest_event-previous).total_seconds()/3600
    trend=max(-8.,min(8.,(new-old)/duration))
    return new,trend,latest_event,by_event[latest_event][0]


def _model_temperature_slope(
    source: str, rows: list[tuple],
    target: datetime, asof: datetime,
) -> tuple[float,str,str]:
    """Use only two same-cycle NOAA valid hours straddling target.

    The 2-meter temperature is a grid point, NOT airport settlement. Use
    only its local temporal change, never the grid temperature level.
    """
    cycles: dict[datetime, dict[datetime, tuple[datetime,float]]] = {}
    for event,receipt,payload in rows:
        try:
            run=_time(payload["model_run_at"])
            valid=_time(payload["forecast_valid_at"])
            kelvin=_finite(payload["temperature_k"],150,350)
        except (KeyError,ValueError,TypeError,OverflowError):
            continue
        if (run!=event or run>asof or receipt>asof or receipt<run or
            valid<target-timedelta(hours=2) or valid>target+timedelta(hours=2)):
            continue
        previous=cycles.setdefault(run,{}).get(valid)
        if previous is None or receipt>previous[0]:
            cycles[run][valid]=(receipt,kelvin)
    for run in sorted(cycles,reverse=True):
        if not timedelta()<=asof-run<=NWP_MAX_AGE:
            continue
        valid_times=sorted(cycles[run])
        for left,right in zip(valid_times,valid_times[1:]):
            if not left<=target<=right or right-left>timedelta(hours=1):
                continue
            a,b=cycles[run][left],cycles[run][right]
            hours=(right-left).total_seconds()/3600
            slope=max(-8.,min(8.,(b[1]-a[1])*1.8/hours))
            latest_receipt=max(a[0],b[0]).isoformat()
            return slope,run.isoformat(),latest_receipt
    if cycles and all(asof-run>NWP_MAX_AGE for run in cycles):
        raise LookupError("STALE_"+source)
    raise LookupError("INCOMPLETE_"+source)


def read_quarter_hour_forecast(
    db_path: Path, station: str, asof: datetime | None = None,
) -> dict[str,Any]:
    """Forecast airport temperature at exactly asof + 15m from timely inputs."""
    if station not in STATIONS:
        raise ValueError("unknown airport station")
    asof=_utc(asof or datetime.now(timezone.utc))
    target=asof+timedelta(minutes=HORIZON_MINUTES)
    output: dict[str,Any]={
        "station":station, "name":STATIONS[station].name,
        "asof":asof.isoformat(), "target_at":target.isoformat(),
        "horizon_minutes":HORIZON_MINUTES, "status":"MISSING_DATABASE",
        "mode":"PAPER_ONLY", "tradable":False,
        "market_exists_15m":False,"settlement_verified":False,
        "settlement_target":"AIRPORT_METAR_PROXY_NOT_KALSHI_INDEX",
        "model_kind":None,"latest_observed_f":None,
        "mean_f":None,"sigma_f":None,"interval_90_f":None,
        "observed_trend_f_per_hour":None,"hrrr_trend_f_per_hour":None,
        "nbm_trend_f_per_hour":None,"direction_probabilities":{},
        "metar_event_at":None,"metar_received_at":None,
        "nwp_cycles_utc":{},"nwp_receipts_utc":{},
        "source_statuses":{"METAR":"NOT_CHECKED","HRRR":"NOT_CHECKED","NBM":"NOT_CHECKED"},
    }
    if not Path(db_path).is_file():
        return output
    try:
        uri=Path(db_path).resolve().as_uri()+"?mode=ro"
        with closing(sqlite3.connect(uri,uri=True,timeout=.5)) as conn:
            conn.execute("PRAGMA query_only=ON")
            observations=_rows(conn,station,"METAR",asof-MIN_OBS_HISTORY_AGE,asof)
            try:
                observed,obs_slope,event,receipt=_trend_from_observations(
                    observations,asof)
            except LookupError as exc:
                output["source_statuses"]["METAR"]=str(exc)
                raise
            output.update({
                "latest_observed_f":round(observed,3),
                "observed_trend_f_per_hour":round(obs_slope,4),
                "metar_event_at":event.isoformat(),
                "metar_received_at":receipt.isoformat(),
            })
            output["source_statuses"]["METAR"]="READY"
            slopes={}
            missing=[]
            for model in ("HRRR","NBM"):
                rows=_rows(conn,station,model,asof-NWP_MAX_AGE,asof)
                try:
                    slope,cycle,last_receipt=_model_temperature_slope(
                        model,rows,target,asof)
                except LookupError as exc:
                    output["source_statuses"][model]=str(exc)
                    missing.append(str(exc))
                else:
                    output["source_statuses"][model]="READY"
                    output[model.lower()+"_trend_f_per_hour"]=round(slope,4)
                    output["nwp_cycles_utc"][model]=cycle
                    output["nwp_receipts_utc"][model]=last_receipt
                    slopes[model]=slope
            if missing:
                raise LookupError(missing[0])
        # Calculate slope only from data available at asof, model's own
        # forecast not the final airport level. Weights and uncertainty are
        # research assumptions and MUST NOT be treated as calibrated odds.
        hours=(target-event).total_seconds()/3600
        nwp_rate=(slopes["HRRR"]+slopes["NBM"])/2.
        projection=max(-6.,min(6.,(.65*obs_slope+.35*nwp_rate)*hours))
        mean=observed+projection
        p_rise=1-normal_cdf(observed+.5,mean,MODEL_SIGMA_F)
        p_fall=normal_cdf(observed-.5,mean,MODEL_SIGMA_F)
        p_flat=max(0.,1-p_rise-p_fall)
        output.update({
            "status":"READY_RESEARCH",
            "model_kind":"UNCALIBRATED_OBS_NWP_DELTA",
            "mean_f":round(mean,3),"sigma_f":MODEL_SIGMA_F,
            "interval_90_f":[round(mean-SIGMA_QUANTILE_90*MODEL_SIGMA_F,2),
                             round(mean+SIGMA_QUANTILE_90*MODEL_SIGMA_F,2)],
            "direction_probabilities":{
                "rise_at_least_half_f":round(p_rise,8),
                "fall_at_least_half_f":round(p_fall,8),
                "within_half_f":round(p_flat,8),
            },
        })
    except LookupError as exc:
        output["status"]=str(exc)
    except (sqlite3.Error,OSError,TypeError,ValueError) as exc:
        output["status"]="DATA_UNAVAILABLE"
        output["reason"]=f"{type(exc).__name__}: {exc}"[:200]
    return output


def read_quarter_hour_forecasts(
    bootstrap_root: Path, *,
    asof: datetime | None = None, preview: bool = False,
) -> dict:
    """Safe, fixed-path JSON API; test preview never touches private data."""
    asof=_utc(asof or datetime.now(timezone.utc))
    if preview:
        forecasts=[]
        for station in STATIONS:
            empty=read_quarter_hour_forecast(
                Path("/__kalshiedge_weather_no_preview_data__"),
                station,asof)
            empty["status"]="PREVIEW_NO_DATA"
            forecasts.append(empty)
    else:
        db=weather_database_path(bootstrap_root)
        forecasts=[read_quarter_hour_forecast(db,s,asof) for s in STATIONS]
    return {
        "mode":"PAPER_ONLY", "horizon_minutes":HORIZON_MINUTES,
        "market_exists_15m":False,
        "model_calibrated":False,"no_live_orders":True,
        "forecasts":forecasts,
    }
