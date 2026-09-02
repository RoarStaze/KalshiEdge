from __future__ import annotations

import json
import os
import tempfile
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict

from .binance_history import BinanceBar
from .config import BootstrapSettings
from .features import (
    HistoricalBTCState,
    HistoricalKalshiCandle,
    HistoricalKalshiState,
    HistoricalKalshiTrade,
    build_market_feature_rows,
)
from .labels import LabelNormalizationError, normalize_market_label
from .leakage import audit_dataset_rows
from .provenance import RawArtifact, sha256_file, verify_artifact, write_manifest
from .types import FeatureRow, MarketLabel


NS = 1_000_000_000
MAX_FEATURE_LOOKBACK_SECONDS = 900
DATASET_SCHEMA_VERSION = 1


class DatasetBuildError(RuntimeError):
    pass


class DatasetBuildReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    market_count: int
    row_count: int
    excluded_markets: tuple[str, ...]
    leakage_finding_count: int
    dataset_path: Path
    manifest_path: Path
    provenance_path: Path


def _canonical_json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _manifest_for_raw(root: Path, raw_path: Path) -> Path:
    try:
        relative = raw_path.resolve().relative_to((root / "raw").resolve())
    except ValueError as exc:
        raise DatasetBuildError(f"input is outside bootstrap raw storage: {raw_path}") from exc
    return root / "manifests" / Path(f"{relative.as_posix()}.manifest.json")


def _require_verified(path: Path, manifest_path: Path, *, label: str) -> dict[str, Any]:
    if not path.exists() or not manifest_path.exists() or not verify_artifact(path, manifest_path):
        raise DatasetBuildError(f"provenance verification failed for {label}: {path}")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetBuildError(f"provenance manifest is unreadable for {label}: {manifest_path}") from exc
    if not isinstance(payload, dict):
        raise DatasetBuildError(f"provenance manifest is invalid for {label}: {manifest_path}")
    return payload


def _read_verified_json(root: Path, source: str, logical_name: str) -> tuple[dict[str, Any], dict[str, str]]:
    path = root / "raw" / source / logical_name
    manifest_path = root / "manifests" / source / f"{logical_name}.manifest.json"
    manifest = _require_verified(path, manifest_path, label=f"{source}/{logical_name}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetBuildError(f"verified JSON input is unreadable: {path}") from exc
    if not isinstance(payload, dict):
        raise DatasetBuildError(f"verified JSON input must be an object: {path}")
    return payload, {"path": path.relative_to(root).as_posix(), "sha256": str(manifest["sha256"])}


def _required_json_available(root: Path, source: str, logical_name: str) -> bool:
    path = root / "raw" / source / logical_name
    manifest_path = root / "manifests" / source / f"{logical_name}.manifest.json"
    data_exists = path.exists()
    manifest_exists = manifest_path.exists()
    if not data_exists and not manifest_exists:
        return False
    if data_exists != manifest_exists or not verify_artifact(path, manifest_path):
        raise DatasetBuildError(f"provenance verification failed for {source}/{logical_name}: {path}")
    return True


def _iso_to_ns(value: Any) -> int:
    if not isinstance(value, str) or not value.strip():
        raise DatasetBuildError(f"invalid ISO timestamp: {value!r}")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise DatasetBuildError(f"invalid ISO timestamp: {value!r}") from exc
    if dt.tzinfo is None:
        raise DatasetBuildError(f"timestamp must include timezone: {value!r}")
    return int(dt.timestamp() * NS)


def _float_value(value: Any, *, default: float | None = None) -> float:
    if value is None:
        if default is None:
            raise DatasetBuildError("required numeric value is missing")
        return default
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise DatasetBuildError(f"invalid numeric value: {value!r}") from exc


def _nested_number(payload: dict[str, Any], key: str, field: str, *, default: float | None = None) -> float | None:
    node = payload.get(key)
    if isinstance(node, dict):
        value = node.get(field)
        if value is not None:
            return _float_value(value)
    dollars = payload.get(f"{key}_dollars")
    if isinstance(dollars, dict):
        value = dollars.get(field)
        if value is not None:
            return _float_value(value)
    return default


def _parse_trades(payload: dict[str, Any]) -> tuple[HistoricalKalshiTrade, ...]:
    raw = payload.get("trades", [])
    if not isinstance(raw, list):
        raise DatasetBuildError("Kalshi trades payload does not contain a trades list")
    result: list[HistoricalKalshiTrade] = []
    for trade in raw:
        if not isinstance(trade, dict):
            raise DatasetBuildError("Kalshi trade row is not an object")
        side = str(trade.get("taker_side") or trade.get("taker_outcome_side") or "").lower()
        if side not in {"yes", "no"}:
            raise DatasetBuildError(f"Kalshi trade has invalid taker side: {side!r}")
        price = trade.get("yes_price_dollars")
        if price is None and trade.get("yes_price") is not None:
            price = float(trade["yes_price"]) / 100.0
        result.append(
            HistoricalKalshiTrade(
                ts_ns=_iso_to_ns(trade.get("created_time")),
                yes_price=_float_value(price),
                count=_float_value(trade.get("count_fp", trade.get("count", 0.0)), default=0.0),
                taker_side=side,
            )
        )
    return tuple(sorted(result, key=lambda item: item.ts_ns))


def _parse_candles(payload: dict[str, Any]) -> tuple[HistoricalKalshiCandle, ...]:
    raw = payload.get("candlesticks", [])
    if not isinstance(raw, list):
        raise DatasetBuildError("Kalshi candlestick payload does not contain a candlesticks list")
    result: list[HistoricalKalshiCandle] = []
    for candle in raw:
        if not isinstance(candle, dict):
            raise DatasetBuildError("Kalshi candlestick row is not an object")
        end_period_ts = candle.get("end_period_ts")
        try:
            end_ts_ns = int(end_period_ts) * NS
        except (TypeError, ValueError) as exc:
            raise DatasetBuildError(f"Kalshi candle has invalid end_period_ts: {end_period_ts!r}") from exc
        result.append(
            HistoricalKalshiCandle(
                end_ts_ns=end_ts_ns,
                yes_bid_close=_nested_number(candle, "yes_bid", "close"),
                yes_ask_close=_nested_number(candle, "yes_ask", "close"),
                price_close=_nested_number(candle, "price", "close"),
                price_high=_nested_number(candle, "price", "high"),
                price_low=_nested_number(candle, "price", "low"),
                volume=_float_value(candle.get("volume_fp", candle.get("volume", 0.0)), default=0.0),
                open_interest=_float_value(candle.get("open_interest_fp", candle.get("open_interest", 0.0)), default=0.0),
            )
        )
    return tuple(sorted(result, key=lambda item: item.end_ts_ns))


def _date_range(start_ns: int, end_ns: int) -> Iterable[date]:
    current = datetime.fromtimestamp(start_ns / NS, tz=timezone.utc).date()
    final = datetime.fromtimestamp(end_ns / NS, tz=timezone.utc).date()
    while current <= final:
        yield current
        current += timedelta(days=1)


class _DailyBinanceCache:
    def __init__(self, root: Path, settings: BootstrapSettings, provenance: dict[str, str], max_days: int = 3) -> None:
        self.root = root
        self.settings = settings
        self.provenance = provenance
        self.max_days = max_days
        self._cache: OrderedDict[str, tuple[BinanceBar, ...]] = OrderedDict()

    def _load_day(self, day: date) -> tuple[BinanceBar, ...]:
        try:
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise DatasetBuildError("pyarrow is required to build the bootstrap dataset") from exc

        filename = f"{self.settings.binance_symbol.upper()}-1s-{day.isoformat()}.parquet"
        path = self.root / "normalized" / "binance" / "1s" / filename
        manifest_path = self.root / "manifests" / "binance_normalized" / "1s" / f"{filename}.manifest.json"
        manifest = _require_verified(path, manifest_path, label=f"normalized Binance {day}")
        metadata = manifest.get("metadata")
        if not isinstance(metadata, dict):
            raise DatasetBuildError(f"normalized Binance manifest lacks metadata: {manifest_path}")
        raw_relative = metadata.get("source_raw_path")
        raw_sha = metadata.get("source_raw_sha256")
        if not isinstance(raw_relative, str) or not isinstance(raw_sha, str):
            raise DatasetBuildError(f"normalized Binance provenance lacks source raw identity: {manifest_path}")
        raw_path = self.root / raw_relative
        raw_manifest = _manifest_for_raw(self.root, raw_path)
        raw_payload = _require_verified(raw_path, raw_manifest, label=f"raw Binance source for {day}")
        if str(raw_payload.get("sha256")) != raw_sha:
            raise DatasetBuildError(f"provenance SHA mismatch between normalized and raw Binance data for {day}")

        self.provenance[path.relative_to(self.root).as_posix()] = str(manifest["sha256"])
        self.provenance[raw_path.relative_to(self.root).as_posix()] = raw_sha
        table = pq.read_table(path)
        required = {
            "ts_ns",
            "open",
            "high",
            "low",
            "close",
            "base_volume",
            "quote_volume",
            "trade_count",
            "taker_buy_base",
            "taker_buy_quote",
        }
        if not required.issubset(table.column_names):
            missing = sorted(required.difference(table.column_names))
            raise DatasetBuildError(f"normalized Binance Parquet missing columns: {missing}")
        rows = table.select(sorted(required)).to_pylist()
        bars = tuple(
            BinanceBar(
                ts_ns=int(row["ts_ns"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                base_volume=float(row["base_volume"]),
                quote_volume=float(row["quote_volume"]),
                trade_count=int(row["trade_count"]),
                taker_buy_base=float(row["taker_buy_base"]),
                taker_buy_quote=float(row["taker_buy_quote"]),
            )
            for row in rows
        )
        previous: int | None = None
        for bar in bars:
            if previous is not None and bar.ts_ns <= previous:
                raise DatasetBuildError(f"normalized Binance timestamps are not strictly increasing for {day}")
            previous = bar.ts_ns
        return bars

    def day(self, day: date) -> tuple[BinanceBar, ...]:
        key = day.isoformat()
        if key in self._cache:
            value = self._cache.pop(key)
            self._cache[key] = value
            return value
        value = self._load_day(day)
        self._cache[key] = value
        while len(self._cache) > self.max_days:
            self._cache.popitem(last=False)
        return value

    def window(self, label: MarketLabel) -> HistoricalBTCState:
        start_ns = label.open_ts_ns - MAX_FEATURE_LOOKBACK_SECONDS * NS
        end_ns = label.close_ts_ns
        bars: list[BinanceBar] = []
        for day in _date_range(start_ns, end_ns):
            bars.extend(bar for bar in self.day(day) if start_ns <= bar.ts_ns <= end_ns)
        bars.sort(key=lambda item: item.ts_ns)
        if not bars:
            raise DatasetBuildError(f"no Binance data overlaps market {label.ticker}")
        return HistoricalBTCState(bars=tuple(bars))


def _flatten_rows(rows: list[FeatureRow]) -> list[dict[str, Any]]:
    feature_names = sorted({name for row in rows for name in row.features})
    source_names = sorted({name for row in rows for name in row.source_max_ts_ns})
    flattened: list[dict[str, Any]] = []
    for row in rows:
        record: dict[str, Any] = {
            "market_ticker": row.market_ticker,
            "market_date": row.market_date,
            "split_group_id": row.split_group_id,
            "checkpoint_ts_ns": row.checkpoint_ts_ns,
            "label_yes": row.label_yes,
        }
        record.update({name: float(row.features.get(name, 0.0)) for name in feature_names})
        record.update({f"source_ts__{name}": row.source_max_ts_ns.get(name) for name in source_names})
        flattened.append(record)
    return flattened


def _write_parquet_atomic(path: Path, records: list[dict[str, Any]]) -> None:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise DatasetBuildError("pyarrow is required to build the bootstrap dataset") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(descriptor)
    temp_path = Path(temp_name)
    try:
        table = pa.Table.from_pylist(records)
        pq.write_table(table, temp_path, compression="zstd")
        with temp_path.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def build_dataset(root: Path, settings: BootstrapSettings | None = None) -> DatasetBuildReport:
    root = root.resolve()
    settings = settings or BootstrapSettings(bootstrap_dir=root)
    if settings.bootstrap_dir.resolve() != root:
        raise DatasetBuildError("bootstrap settings root does not match requested dataset root")

    market_root = root / "raw" / "kalshi" / "markets"
    if not market_root.exists():
        raise DatasetBuildError("Kalshi backfill must be completed before dataset build")

    labels: list[MarketLabel] = []
    excluded: list[str] = []
    provenance: dict[str, str] = {}
    market_payloads: dict[str, dict[str, Any]] = {}
    for market_path in sorted(market_root.glob("*.json")):
        logical = f"markets/{market_path.name}"
        payload, identity = _read_verified_json(root, "kalshi", logical)
        provenance[identity["path"]] = identity["sha256"]
        ticker = market_path.stem
        try:
            label = normalize_market_label(payload)
        except LabelNormalizationError:
            excluded.append(ticker)
            continue
        labels.append(label)
        market_payloads[label.ticker] = payload
    labels.sort(key=lambda item: (item.open_ts_ns, item.ticker))
    if not labels:
        raise DatasetBuildError("no valid settled KXBTC15M markets are available for dataset build")

    btc_cache = _DailyBinanceCache(root, settings, provenance)
    rows: list[FeatureRow] = []
    included_market_count = 0
    for label in labels:
        trades_name = f"trades/{label.ticker}.json"
        candles_name = f"candlesticks/{label.ticker}.json"
        trades_available = _required_json_available(root, "kalshi", trades_name)
        candles_available = _required_json_available(root, "kalshi", candles_name)
        if not trades_available or not candles_available:
            excluded.append(label.ticker)
            continue
        trades_payload, trades_identity = _read_verified_json(root, "kalshi", trades_name)
        candles_payload, candles_identity = _read_verified_json(root, "kalshi", candles_name)
        provenance[trades_identity["path"]] = trades_identity["sha256"]
        provenance[candles_identity["path"]] = candles_identity["sha256"]
        kalshi = HistoricalKalshiState(
            trades=_parse_trades(trades_payload),
            candles=_parse_candles(candles_payload),
        )
        btc = btc_cache.window(label)
        market_rows = build_market_feature_rows(
            label,
            kalshi,
            btc,
            checkpoint_seconds=settings.checkpoint_seconds,
        )
        rows.extend(market_rows)
        included_market_count += 1

    if included_market_count == 0 or not rows:
        raise DatasetBuildError("no markets with complete required history are available for dataset build")

    audit = audit_dataset_rows(rows)
    if not audit.passed:
        sample = "; ".join(f"{item.code}: {item.detail}" for item in audit.findings[:5])
        raise DatasetBuildError(f"leakage audit failed with {audit.finding_count} finding(s): {sample}")

    records = _flatten_rows(rows)
    dataset_path = root / "derived" / "features.parquet"
    _write_parquet_atomic(dataset_path, records)
    dataset_sha = sha256_file(dataset_path)
    manifest_relative = Path("manifests") / "dataset" / "features.parquet.manifest.json"
    artifact = RawArtifact(
        path=dataset_path.relative_to(root),
        manifest_path=manifest_relative,
        sha256=dataset_sha,
        source="bootstrap_dataset",
        retrieval_ts_utc=datetime.now(timezone.utc).isoformat(),
        byte_count=dataset_path.stat().st_size,
        metadata={
            "schema_version": DATASET_SCHEMA_VERSION,
            "row_count": len(rows),
            "market_count": included_market_count,
            "checkpoint_seconds": list(settings.checkpoint_seconds),
            "feature_names": sorted(rows[0].features),
            "target_column": "label_yes",
            "split_group_column": "split_group_id",
            "leakage_finding_count": audit.finding_count,
        },
    )
    manifest_path = write_manifest(root, artifact)

    provenance_path = root / "derived" / "features.provenance.json"
    provenance_payload = {
        "schema_version": DATASET_SCHEMA_VERSION,
        "dataset_path": dataset_path.relative_to(root).as_posix(),
        "dataset_sha256": dataset_sha,
        "market_count": included_market_count,
        "row_count": len(rows),
        "checkpoint_seconds": list(settings.checkpoint_seconds),
        "leakage_finding_count": audit.finding_count,
        "inputs": [
            {"path": path, "sha256": sha}
            for path, sha in sorted(provenance.items())
        ],
    }
    _atomic_write_bytes(provenance_path, _canonical_json_bytes(provenance_payload))

    return DatasetBuildReport(
        market_count=included_market_count,
        row_count=len(rows),
        excluded_markets=tuple(sorted(excluded)),
        leakage_finding_count=audit.finding_count,
        dataset_path=dataset_path,
        manifest_path=manifest_path,
        provenance_path=provenance_path,
    )