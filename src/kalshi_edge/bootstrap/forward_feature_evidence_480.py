"""Immutable, label-free 480s live evidence and verified outcome join.

The authoritative final decision is locked and saved BEFORE any capture.
Only an independently verified official Kalshi settlement supplies a label.
Neither this collector nor the reporting CLI trains, promotes or trades.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Literal, TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .final_forecast import (
    FINAL_CHECKPOINT_SECONDS, MAX_CHECKPOINT_LATENESS_NS,
    FinalForecastRecord,
)
from .final_history import (
    SettlementIntegrityError, _close_ns, _forecasts,
    _load_saved_settlement,
)
from .types import FeatureRow

if TYPE_CHECKING:
    from .live import LiveFeatureState, LivePredictionRecord

NS = 1_000_000_000
SCHEMA = "KXBTC15M_480S_FORWARD_FEATURE_EVIDENCE_V1"
FEATURE_DIR = Path("research/480_forward_features")
TICKER = re.compile(r"^KXBTC15M-[A-Z0-9-]+$")
KEY = re.compile(r"^[a-z][a-z0-9_]*$")
SHA = re.compile(r"^[0-9a-f]{64}$")


def _canonical(data:dict[str,Any]) -> bytes:
    return json.dumps(data,sort_keys=True,separators=(",",":"),allow_nan=False).encode("utf-8")


def _hash(data:dict[str,Any]) -> str:
    return hashlib.sha256(_canonical(data)).hexdigest()


def _day(close_ns:int) -> str:
    return datetime.fromtimestamp(close_ns/NS,timezone.utc).date().isoformat()


def _final_file(root:Path,final:FinalForecastRecord) -> Path:
    return root/"final_forecasts"/_day(_close_ns(final))/f"{final.market_ticker}.json"


class Forward480FeatureSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version:Literal["KXBTC15M_480S_FORWARD_FEATURE_EVIDENCE_V1"] = SCHEMA
    market_ticker:str
    scheduled_ts_ns:int = Field(gt=0)
    prediction_ts_ns:int = Field(gt=0)
    market_close_ts_ns:int = Field(gt=0)
    model_hash:str | None = None
    forecast_sha256:str
    status:Literal["FEATURES_OK","FEATURES_UNAVAILABLE"]
    reason:str | None = None
    features:dict[str,float]
    source_max_ts_ns:dict[str,int]
    snapshot_sha256:str

    @model_validator(mode="after")
    def verify(self):
        if not TICKER.fullmatch(self.market_ticker):
            raise ValueError("unsafe 480s market ticker")
        if self.market_close_ts_ns != self.scheduled_ts_ns+FINAL_CHECKPOINT_SECONDS*NS:
            raise ValueError("snapshot is not an 8-minute checkpoint")
        if not (self.scheduled_ts_ns <= self.prediction_ts_ns
                <= self.scheduled_ts_ns+MAX_CHECKPOINT_LATENESS_NS):
            raise ValueError("snapshot checkpoint timestamp is outside allowed lock")
        if not SHA.fullmatch(self.forecast_sha256):
            raise ValueError("invalid immutable final forecast digest")
        if self.model_hash is not None and not SHA.fullmatch(self.model_hash.lower()):
            raise ValueError("invalid model digest")
        if len(self.features)>256 or len(self.source_max_ts_ns)>32:
            raise ValueError("oversized 480s evidence")
        if self.status=="FEATURES_UNAVAILABLE":
            if not self.reason or len(self.reason)>160 or self.features or self.source_max_ts_ns:
                raise ValueError("unavailable evidence cannot contain fabricated features")
        elif not self.features or self.reason is not None or not self.source_max_ts_ns:
            raise ValueError("available evidence requires features and source timestamps")
        for name,value in self.features.items():
            if (not KEY.fullmatch(name) or any(x in name for x in (
                "label", "settlement", "actual", "result", "correct", "future",
            )) or type(value) not in (int,float) or not math.isfinite(value)):
                raise ValueError("feature name or value is not valid causal evidence")
        for name,ts in self.source_max_ts_ns.items():
            if (not KEY.fullmatch(name) or type(ts) is not int or ts<=0
                    or ts>self.prediction_ts_ns):
                raise ValueError("source timestamp is missing, invalid or future")
        payload=self.model_dump(mode="json",exclude={"snapshot_sha256"})
        if self.snapshot_sha256 != _hash(payload):
            raise ValueError("feature snapshot SHA-256 mismatch")
        return self


def _validate_forecast_file(final:FinalForecastRecord,forecast_file:Path) -> str:
    if final.checkpoint_seconds!=FINAL_CHECKPOINT_SECONDS:
        raise ValueError("legacy 240s final cannot produce a 480s snapshot")
    if not TICKER.fullmatch(final.market_ticker):
        raise ValueError("unsafe final ticker")
    if not (final.scheduled_ts_ns<=final.prediction_ts_ns
            <=final.scheduled_ts_ns+MAX_CHECKPOINT_LATENESS_NS):
        raise ValueError("late or early final is not eligible for causal snapshot")
    expected_name=f"{final.market_ticker}.json"
    if (forecast_file.name!=expected_name
            or forecast_file.parent.name!=_day(_close_ns(final))
            or forecast_file.parent.parent.name!="final_forecasts"):
        raise ValueError("final forecast file path does not match checkpoint identity")
    try:
        raw=forecast_file.read_bytes()
        saved=FinalForecastRecord.model_validate_json(raw)
    except (OSError,ValueError) as exc:
        raise ValueError("missing or malformed immutable final forecast") from exc
    if saved!=final:
        raise ValueError("immutable forecast and requested snapshot do not match")
    return hashlib.sha256(raw).hexdigest()


def build_forward_snapshot(
    final:FinalForecastRecord,forecast_file:Path,
    row:FeatureRow|None,*,reason:str|None=None,
) -> Forward480FeatureSnapshot:
    """Exclude the dummy FeatureRow.label_yes created by live.feature_row()."""
    forecast_file=Path(forecast_file)
    final_hash=_validate_forecast_file(final,forecast_file)
    if row is not None:
        if (row.market_ticker!=final.market_ticker
                or row.checkpoint_ts_ns!=final.prediction_ts_ns):
            raise ValueError("feature snapshot does not match final checkpoint identity")
        if reason is not None:
            raise ValueError("available feature snapshot cannot have failure reason")
        feature_values=dict(row.features)
        source_timestamps=dict(row.source_max_ts_ns)
        status="FEATURES_OK"
    else:
        if not reason:
            raise ValueError("unavailable snapshot must record why features are missing")
        feature_values={}
        source_timestamps={}
        status="FEATURES_UNAVAILABLE"
    payload=dict(
        schema_version=SCHEMA,
        market_ticker=final.market_ticker,
        scheduled_ts_ns=final.scheduled_ts_ns,
        prediction_ts_ns=final.prediction_ts_ns,
        market_close_ts_ns=_close_ns(final),
        model_hash=final.model_hash,
        forecast_sha256=final_hash,
        status=status,
        reason=reason,
        features=feature_values,
        source_max_ts_ns=source_timestamps,
    )
    return Forward480FeatureSnapshot(**payload,snapshot_sha256=_hash(payload))


class Forward480FeatureWriter:
    """One saved checkpoint per ticker; reject duplicate conflicts and edits."""

    def __init__(self,root:Path):
        root=Path(root).resolve()
        if root.name=="raw" and root.parent.name=="data":
            raise ValueError("research snapshot cannot write to canonical raw data")
        self.root=root

    def save(self,item:Forward480FeatureSnapshot) -> Path|None:
        item=Forward480FeatureSnapshot.model_validate(item.model_dump(mode="json"))
        path=(self.root/FEATURE_DIR/_day(item.market_close_ts_ns)
              /f"{item.market_ticker}.json")
        path.parent.mkdir(parents=True,exist_ok=True)
        encoded=_canonical(item.model_dump(mode="json"))+b"\n"
        try:
            with path.open("xb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            try:
                existing=Forward480FeatureSnapshot.model_validate_json(path.read_bytes())
            except (OSError,ValueError) as exc:
                raise ValueError("existing forward evidence is invalid") from exc
            if existing!=item:
                raise ValueError("conflicting immutable forward feature evidence")
            return None
        return path


def load_forward_snapshot(root:Path,final:FinalForecastRecord) -> Forward480FeatureSnapshot:
    """Hash/identity verification is mandatory before any settlement join."""
    root=Path(root).resolve()
    if final.checkpoint_seconds!=FINAL_CHECKPOINT_SECONDS:
        raise ValueError("not a 480s final")
    path=root/FEATURE_DIR/_day(_close_ns(final))/f"{final.market_ticker}.json"
    try:
        raw=path.read_bytes()
        if len(raw)>80_000:
            raise ValueError("oversized snapshot")
        snapshot=Forward480FeatureSnapshot.model_validate_json(raw)
    except (OSError,ValueError) as exc:
        raise ValueError("missing or invalid forward snapshot") from exc
    if (snapshot.market_ticker!=final.market_ticker
            or snapshot.scheduled_ts_ns!=final.scheduled_ts_ns
            or snapshot.prediction_ts_ns!=final.prediction_ts_ns
            or snapshot.market_close_ts_ns!=_close_ns(final)
            or snapshot.model_hash!=final.model_hash):
        raise ValueError("snapshot is for a different final forecast")
    forecast_file=_final_file(root,final)
    if _validate_forecast_file(final,forecast_file)!=snapshot.forecast_sha256:
        raise ValueError("source immutable final forecast digest changed")
    return snapshot


def capture_forward_480_feature(
    *,state:"LiveFeatureState",prediction:"LivePredictionRecord",
    final:FinalForecastRecord,forecast_file:Path,
    writer:Forward480FeatureWriter,
) -> Path|None:
    """Invoked only after final is durably saved, never on decision path."""
    row=None
    reason=None
    if (prediction.prediction_ts_ns!=final.prediction_ts_ns
            or prediction.market_ticker!=final.market_ticker
            or prediction.model_hash!=final.model_hash):
        reason="PREDICTION_IDENTITY_MISMATCH"
    elif prediction.status!="OK" or not prediction.feed_quality.healthy:
        reason=prediction.reason or "UNHEALTHY_CHECKPOINT_INPUTS"
    else:
        try:
            row=state.feature_row(final.prediction_ts_ns)
        except Exception:
            reason="CAUSAL_FEATURE_ROW_UNAVAILABLE"
    item=build_forward_snapshot(final,forecast_file,row,reason=reason)
    return writer.save(item)
