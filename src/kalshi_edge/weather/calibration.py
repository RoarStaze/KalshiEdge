"""Honest, station/day-level prospective temperature probability audits.

Scoring uses fixed outcome bins for comparable multiclass Brier and log loss.
A source URL, local SHA-256 and reviewer timestamp do NOT independently
authenticate a weather settlement. No automatic live-trading promotion.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import random
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .predictor import STATIONS, _finite, _time, normal_cdf
from .prospective import journal_summary, read_station_snapshots
from .status import weather_database_path

FIRST_DEGREE_F = -60
LAST_DEGREE_F = 140
PRIMARY_CHECKPOINT_STANDARD_HOUR = 12
MIN_MEASURED_DAYS = 10
MIN_COMPARATIVE_DAYS = 30
MIN_CERTIFICATION_DAYS = 60
_SETTLEMENT_SOURCE_URL = {
    "THE_WEATHER_COMPANY": "https://weather.com/kalshi",
    "NWS_CLI": "https://api.weather.gov/products/",
}
_VALID_TICKER = re.compile(r"^[A-Z0-9][A-Z0-9-]{3,99}$")
_HEX256 = re.compile(r"^[0-9a-fA-F]{64}$")


def wilson_interval(hits: int, total: int, *, z: float = 1.95996398454) -> list[float]:
    if total <= 0 or not 0 <= hits <= total:
        raise ValueError("invalid coverage counts")
    p = hits / total
    denom = 1 + z*z/total
    center = (p + z*z/(2*total)) / denom
    rad = z * math.sqrt((p*(1-p) + z*z/(4*total))/total)/denom
    return [round(max(0., center-rad), 6), round(min(1., center+rad), 6)]


def score_gaussian_degree(mean: float, sigma: float, actual_f: float) -> dict:
    """One *fixed* set of integer-degree outcomes for all comparison models.

    Two open tails capture all outcomes outside -60..140. The old dynamic
    buckets moved with the predicted mean and cannot be used to compare
    multiclass Brier scores for models with differing predictions.
    """
    mean = _finite(mean, -150, 200)
    sigma = _finite(sigma, 0, 100)
    actual = _finite(actual_f, -150, 200)
    if sigma <= 0 or abs(actual - round(actual)) > 1e-8:
        raise ValueError("non-positive sigma or non-integer settlement outcome")
    point = int(round(actual))
    previous = normal_cdf(FIRST_DEGREE_F - .5, mean, sigma)
    probabilities = [previous]
    for degree in range(FIRST_DEGREE_F, LAST_DEGREE_F+1):
        current = normal_cdf(degree + .5, mean, sigma)
        probabilities.append(max(0., current-previous))
        previous = current
    probabilities.append(max(0., 1-previous))
    total = sum(probabilities)
    if not math.isfinite(total) or total <= 0:
        raise ValueError("invalid Gaussian probability mass")
    probabilities = [p/total for p in probabilities]
    outcome = (
        0 if point < FIRST_DEGREE_F
        else len(probabilities)-1 if point > LAST_DEGREE_F
        else point - FIRST_DEGREE_F + 1
    )
    truth_probability = max(1e-15, probabilities[outcome])
    brier = sum(p*p for p in probabilities) + 1 - 2*probabilities[outcome]
    return {
        "log_loss": -math.log(truth_probability),
        "multiclass_brier": max(0., brier),
        "absolute_error_f": abs(actual-mean),
    }


def _evidence_item(raw: dict, root: Path) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("settlement evidence must be JSON object")
    station = raw["station"]
    if station not in STATIONS:
        raise ValueError("unknown official weather station")
    climate_day = date.fromisoformat(raw["climate_day"])
    actual = _finite(raw["actual_max_f"], -150, 200)
    if abs(actual-round(actual)) > 1e-8:
        raise ValueError("daily settlement temperature must be whole-degree Fahrenheit")
    source = raw["official_source"]
    rules_source = raw["market_rules_source"]
    if source not in _SETTLEMENT_SOURCE_URL or source != rules_source:
        raise ValueError("market-specific settlement publisher not verified")
    uri = raw["source_url"]
    expected = _SETTLEMENT_SOURCE_URL[source]
    if not isinstance(uri,str) or not (
        uri == expected or (source == "NWS_CLI" and uri.startswith(expected))
    ):
        raise ValueError("publisher capture URL mismatches market rules")
    ticker = raw["market_ticker"]
    if not isinstance(ticker, str) or not _VALID_TICKER.fullmatch(ticker):
        raise ValueError("missing or invalid market ticker")
    digest = raw["sha256"]
    if not isinstance(digest,str) or not _HEX256.fullmatch(digest):
        raise ValueError("missing or invalid capture SHA-256")
    name = raw["capture_file"]
    # Disallow absolute, symlinks escaping the evidence folder and subpaths,
    # so no arbitrary local file can be read by the calibration endpoint.
    if (not isinstance(name,str) or len(name)>150 or
        name != Path(name).name or name in (".","..")):
        raise ValueError("unsafe settlement capture path")
    path = (root/"evidence"/name).resolve()
    evidence_dir = (root/"evidence").resolve()
    if path.parent != evidence_dir or not path.is_file() or path.stat().st_size>5_000_000:
        raise ValueError("settlement capture not found or unsafe")
    actual_digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if not hmac.compare_digest(actual_digest, digest.lower()):
        raise ValueError("settlement capture checksum mismatch")
    reviewed = _time(raw["reviewed_at"])
    # A preliminary future observation is not an eligible settlement record.
    offset = timedelta(hours=STATIONS[station].standard_offset_hours)
    day_end = datetime(climate_day.year,climate_day.month,climate_day.day,
                       tzinfo=timezone.utc)-offset+timedelta(days=1)
    if reviewed < day_end:
        raise ValueError("settlement evidence reviewed before end of climate day")
    return {
        "station":station,"climate_day":climate_day.isoformat(),
        "actual_max_f":int(round(actual)),
        "market_ticker":ticker,
        "source_url":uri, "sha256":actual_digest,
        "official_source":source,
        "evidence_state":"MANUAL_REVIEWED_UNVERIFIED",
    }


def audit_settlement_evidence(root: Path) -> tuple[list[dict],list[dict]]:
    """Validate *local bytes and metadata*, not the publisher's online identity.

    A genuinely independent certification still requires publisher retrieval
    and matching the exact per-market Kalshi contract rules and revisions.
    """
    root=Path(root)
    manifest=root/"settlement_evidence.jsonl"
    if not manifest.is_file():
        return [], []
    if manifest.stat().st_size>10_000_000:
        return [], [{"line":0,"reason":"evidence manifest too large"}]
    accepted, rejected, seen = [], [], set()
    for line_num,line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        if line_num>100_000:
            rejected.append({"line":line_num,"reason":"evidence manifest too long"})
            break
        if not line.strip():
            continue
        try:
            case=_evidence_item(json.loads(line), root)
            key=(case["station"],case["climate_day"])
            if key in seen:
                raise ValueError("duplicate station and climate day")
            seen.add(key)
            accepted.append(case)
        except (OSError,ValueError,TypeError,KeyError,OverflowError,json.JSONDecodeError) as exc:
            rejected.append({"line":line_num,"reason":str(exc)[:250]})
    return accepted,rejected


def _bootstrap_paired_ci(deltas: list[float], *, samples: int = 1500) -> list[float] | None:
    if len(deltas) < MIN_COMPARATIVE_DAYS:
        return None
    rng = random.Random(445501)
    n = len(deltas)
    estimates = []
    for _ in range(samples):
        estimates.append(sum(deltas[rng.randrange(n)] for _ in range(n))/n)
    estimates.sort()
    return [round(estimates[int(.025*samples)],8),
            round(estimates[int(.975*samples)-1],8)]


def evaluate_station(station: str, snapshots: list[dict], evidence: list[dict],
                     *, min_days: int = MIN_MEASURED_DAYS) -> dict:
    """Precommitted local-standard-noon snapshot per *independent* day.

    Match first observed usable noon snapshot only, irrespective of later
    accuracy. Never peek at future snapshots or score multiple times/day.
    """
    if station not in STATIONS or min_days<1:
        raise ValueError("invalid station/minimum days")
    earliest = {}
    for row in snapshots:
        if row.get("station") != station or row.get("checkpoint_hour_standard") != 12:
            continue
        prediction = row.get("forecast")
        if not isinstance(prediction,dict) or prediction.get("status") != "READY_RESEARCH":
            continue
        key=row.get("climate_day")
        if not isinstance(key,str) or prediction.get("climate_day")!=key:
            continue
        asof=_time(row["asof"])
        if prediction.get("asof")!=asof.isoformat():
            continue
        if key not in earliest or asof < _time(earliest[key]["asof"]):
            earliest[key]=row
    labels={}
    for row in evidence:
        if row.get("station")!=station or row.get("evidence_state")!="MANUAL_REVIEWED_UNVERIFIED":
            continue
        key=row.get("climate_day")
        if key in labels:
            raise ValueError("duplicate settled climate day")
        labels[key]=row
    scored=[]
    for day in sorted(set(earliest).intersection(labels)):
        frozen=earliest[day]["forecast"]
        actual=labels[day]["actual_max_f"]
        mean,sigma=frozen.get("mean_f"),frozen.get("sigma_f")
        nbm=frozen.get("nbm_max_f")
        observed=frozen.get("observed_max_f")
        try:
            model=score_gaussian_degree(mean,sigma,actual)
            # Compare to a fixed *identically scored* NBM+observed baseline,
            # never to model-centered bins or a baseline refitted to the test.
            baseline=score_gaussian_degree(max(float(nbm),float(observed)),3.,actual)
        except (ValueError,TypeError,OverflowError):
            continue
        scored.append({"day":day,"model":model,"baseline":baseline,
                       "actual":actual,"mean":float(mean),"sigma":float(sigma)})
    total=len(scored)
    result={
        "station":station, "forecast_noon_days":len(earliest),
        "matched_days":total,
        "status":"WAITING_FOR_SETTLEMENT_EVIDENCE" if not total
                 else "INSUFFICIENT_EVIDENCE" if total<min_days
                 else "MEASURED_RESEARCH_NOT_CERTIFIED",
        "multiclass_brier":None,"log_loss":None,"mae_f":None,
        "nbm_baseline":None,"90_interval":None,"50_interval":None,
        "80_interval":None,"paired_brier_delta":None,
        "paired_brier_bootstrap_95_ci":None,
        "calibration_certified":False,
        "model_improvement_verified":False,
        "source_evidence":"MANUAL_REVIEWED_NOT_INDEPENDENTLY_AUTHENTICATED",
    }
    if total < min_days:
        return result
    def mean(key):
        return round(sum(day["model"][key] for day in scored)/total,8)
    result.update({
        "multiclass_brier":mean("multiclass_brier"),
        "log_loss":mean("log_loss"),
        "mae_f":round(mean("absolute_error_f"),4),
        "nbm_baseline":{
            k:round(sum(d["baseline"][k] for d in scored)/total,8)
            for k in ("multiclass_brier","log_loss","absolute_error_f")
        },
    })
    for nominal,key,z in ((.5,"50_interval",.674489750196),(.8,"80_interval",1.28155156554),
                          (.9,"90_interval",1.64485362695)):
        covered=sum(abs(d["actual"]-d["mean"]) <= z*d["sigma"] for d in scored)
        result[key]={"nominal":nominal,"covered_days":covered,
                     "empirical_coverage":round(covered/total,6),
                     "wilson_95_ci":wilson_interval(covered,total)}
    differences=[d["model"]["multiclass_brier"]-d["baseline"]["multiclass_brier"]
                 for d in scored]
    result["paired_brier_delta"]=round(sum(differences)/total,8)
    result["paired_brier_bootstrap_95_ci"]=_bootstrap_paired_ci(differences)
    return result


def calibration_status(bootstrap_root: Path, *, preview: bool = False) -> dict:
    """Read-only dashboard endpoint; never invent scores or promote a model."""
    result={
        "mode":"PAPER_ONLY","calibration_certified":False,
        "model_improvement_verified":False,"live_orders_enabled":False,
        "status":"WAITING_FOR_SETTLEMENT_EVIDENCE",
        "evidence_state":"MANUAL_CLAIMS_NOT_INDEPENDENTLY_CERTIFIED",
        "matched_days":0,"rejected_evidence_count":0,
        "journal":{"recorded_snapshots":0,"independent_station_days":0,
                   "recorded_noon_snapshots":0},
        "stations":[],
    }
    if preview:
        result["status"]="PREVIEW_NO_EVIDENCE"
        result["stations"]=[evaluate_station(s,[],[]) for s in STATIONS]
        return result
    db=weather_database_path(bootstrap_root)
    weather_root=db.parent
    try:
        journal=journal_summary(db)
        settled,rejected=audit_settlement_evidence(weather_root)
        result["journal"]=journal
        result["rejected_evidence_count"]=len(rejected)
        result["evidence_errors"]=rejected[:20]
        stations=[]
        for station in STATIONS:
            rows=read_station_snapshots(db,station)
            stations.append(evaluate_station(station,rows,settled))
        result["stations"]=stations
        result["matched_days"]=sum(s["matched_days"] for s in stations)
        if result["matched_days"]:
            result["status"]="MEASURED_RESEARCH_NOT_CERTIFIED"
        elif journal["recorded_noon_snapshots"] and not settled:
            result["status"]="WAITING_FOR_SETTLEMENT_EVIDENCE"
        elif settled:
            result["status"]="WAITING_FOR_MATCHED_NOON_FORECASTS"
        else:
            result["status"]="COLLECTING_PROSPECTIVE_FORECASTS" if journal["recorded_snapshots"] else "WAITING_FOR_PROSPECTIVE_FORECASTS"
    except (OSError,ValueError,TypeError,json.JSONDecodeError) as exc:
        result["status"]="CALIBRATION_AUDIT_ERROR"
        result["reason"]=f"{type(exc).__name__}: {exc}"[:250]
        result["stations"]=[evaluate_station(s,[],[]) for s in STATIONS]
        result["matched_days"]=0
    return result
