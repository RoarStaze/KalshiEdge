from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import tempfile
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict

from .provenance import RawArtifact, write_manifest


BINANCE_ARCHIVE_ORIGIN = "https://data.binance.vision"
_ONE_SECOND_NS = 1_000_000_000
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_SYMBOL_RE = re.compile(r"^[A-Z0-9]+$")


class BinanceDataError(RuntimeError):
    pass


class BinanceBar(BaseModel):
    model_config = ConfigDict(frozen=True)

    ts_ns: int
    open: float
    high: float
    low: float
    close: float
    base_volume: float
    quote_volume: float
    trade_count: int
    taker_buy_base: float
    taker_buy_quote: float


class BinanceParseStats(BaseModel):
    model_config = ConfigDict(frozen=True)

    row_count: int
    duplicate_count: int
    gap_count: int
    missing_seconds: int
    first_ts_ns: int | None
    last_ts_ns: int | None


def normalize_epoch_to_ns(value: int) -> int:
    """Normalize Binance millisecond/microsecond/nanosecond Unix timestamps to ns."""
    if 1_000_000_000_000 <= value < 100_000_000_000_000:
        return value * 1_000_000
    if 100_000_000_000_000 <= value < 100_000_000_000_000_000:
        return value * 1_000
    if 100_000_000_000_000_000 <= value < 100_000_000_000_000_000_000:
        return value
    raise BinanceDataError(f"unsupported Binance epoch magnitude: {value}")


def archive_urls(
    symbol: str,
    dates: Iterable[date],
    dataset: str = "klines",
    interval: str = "1s",
) -> list[str]:
    symbol = symbol.upper()
    if not _SYMBOL_RE.fullmatch(symbol):
        raise ValueError("Binance symbol must contain only A-Z and digits")
    if dataset not in {"klines", "aggTrades", "trades"}:
        raise ValueError(f"unsupported Binance archive dataset: {dataset}")
    if not interval or "/" in interval or ".." in interval:
        raise ValueError("invalid Binance kline interval")

    result: list[str] = []
    for day in sorted(set(dates)):
        stamp = day.isoformat()
        if dataset == "klines":
            relative = f"/data/spot/daily/klines/{symbol}/{interval}/{symbol}-{interval}-{stamp}.zip"
        else:
            relative = f"/data/spot/daily/{dataset}/{symbol}/{symbol}-{dataset}-{stamp}.zip"
        result.append(f"{BINANCE_ARCHIVE_ORIGIN}{relative}")
    return result


def _validate_official_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "data.binance.vision":
        raise BinanceDataError("only official data.binance.vision HTTPS archives are accepted")
    name = Path(parsed.path).name
    if not name or name in {".", ".."}:
        raise BinanceDataError("invalid Binance archive URL")
    return name


def _expected_checksum(text: str) -> str:
    token = text.strip().split(maxsplit=1)[0] if text.strip() else ""
    if not _SHA256_RE.fullmatch(token):
        raise BinanceDataError("invalid Binance checksum response")
    return token.lower()


def _reject_phase1_root(root: Path) -> Path:
    resolved = root.resolve()
    if resolved.name.lower() == "raw" and resolved.parent.name.lower() == "data":
        raise ValueError("bootstrap storage cannot use the Phase 1 data/raw root")
    return resolved


class BinanceArchiveClient:
    def __init__(self, *, http_client: httpx.Client | None = None, timeout_seconds: float = 60.0) -> None:
        self._http = http_client or httpx.Client(timeout=timeout_seconds, follow_redirects=True)

    def close(self) -> None:
        self._http.close()

    def download_and_verify(self, url: str, checksum_url: str, root: Path) -> RawArtifact:
        filename = _validate_official_url(url)
        _validate_official_url(checksum_url)
        if checksum_url != f"{url}.CHECKSUM":
            raise BinanceDataError("checksum URL must be the official companion .CHECKSUM")

        checksum_response = self._http.get(checksum_url)
        checksum_response.raise_for_status()
        expected = _expected_checksum(checksum_response.text)

        root = _reject_phase1_root(root)
        raw_parent = root / "raw" / "binance" / "archive"
        raw_parent.mkdir(parents=True, exist_ok=True)
        final_path = raw_parent / filename
        descriptor, temp_name = tempfile.mkstemp(prefix=f".{filename}.", suffix=".tmp", dir=raw_parent)
        temp_path = Path(temp_name)
        digest = hashlib.sha256()
        byte_count = 0

        try:
            with os.fdopen(descriptor, "wb") as handle:
                with self._http.stream("GET", url) as response:
                    response.raise_for_status()
                    for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
                        handle.write(chunk)
                        digest.update(chunk)
                        byte_count += len(chunk)
                handle.flush()
                os.fsync(handle.fileno())

            actual = digest.hexdigest()
            if actual != expected:
                raise BinanceDataError(f"Binance checksum mismatch: expected {expected}, got {actual}")

            os.replace(temp_path, final_path)
            stats = inspect_spot_1s(final_path) if "/klines/" in url and "/1s/" in url else None
            metadata: dict[str, object] = {
                "source_locator": url,
                "checksum_url": checksum_url,
                "official_checksum_sha256": expected,
                "parser_version": "1",
                "archive_format": "zip",
                "timestamp_unit": "source ms/us; normalized to ns during parsing",
            }
            if stats is not None:
                metadata.update(stats.model_dump(mode="json"))

            manifest_relative = Path("manifests") / "binance" / "archive" / f"{filename}.manifest.json"
            artifact = RawArtifact(
                path=final_path.relative_to(root),
                manifest_path=manifest_relative,
                sha256=actual,
                source="binance",
                retrieval_ts_utc=datetime.now(timezone.utc).isoformat(),
                byte_count=byte_count,
                metadata=metadata,
            )
            write_manifest(root, artifact)
            return artifact
        finally:
            if temp_path.exists():
                temp_path.unlink()


def download_and_verify(url: str, checksum_url: str, root: Path) -> RawArtifact:
    client = BinanceArchiveClient()
    try:
        return client.download_and_verify(url, checksum_url, root)
    finally:
        client.close()


def _open_csv_rows(path: Path) -> Iterator[list[str]]:
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            members = [name for name in archive.namelist() if not name.endswith("/") and name.lower().endswith(".csv")]
            if len(members) != 1:
                raise BinanceDataError(f"expected exactly one CSV in Binance ZIP, found {len(members)}")
            with archive.open(members[0], "r") as raw:
                with io.TextIOWrapper(raw, encoding="utf-8", newline="") as text:
                    yield from csv.reader(text)
        return

    with path.open("r", encoding="utf-8", newline="") as text:
        yield from csv.reader(text)


def _parse_row(row: list[str], line_number: int) -> BinanceBar | None:
    if not row or all(not cell.strip() for cell in row):
        return None
    first = row[0].strip()
    if not first.isdigit():
        normalized = first.lower().replace(" ", "_")
        if normalized in {"open_time", "opentime"}:
            return None
        raise BinanceDataError(f"invalid Binance timestamp at row {line_number}: {first!r}")
    if len(row) < 11:
        raise BinanceDataError(f"Binance 1s kline row {line_number} has {len(row)} columns; expected at least 11")
    try:
        return BinanceBar(
            ts_ns=normalize_epoch_to_ns(int(first)),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            base_volume=float(row[5]),
            quote_volume=float(row[7]),
            trade_count=int(row[8]),
            taker_buy_base=float(row[9]),
            taker_buy_quote=float(row[10]),
        )
    except (ValueError, TypeError) as exc:
        raise BinanceDataError(f"invalid Binance 1s kline row {line_number}") from exc


def parse_spot_1s(path: Path) -> Iterator[BinanceBar]:
    previous_ts: int | None = None
    for line_number, row in enumerate(_open_csv_rows(path), start=1):
        bar = _parse_row(row, line_number)
        if bar is None:
            continue
        if previous_ts is not None:
            if bar.ts_ns == previous_ts:
                raise BinanceDataError(f"duplicate Binance 1s timestamp at row {line_number}: {bar.ts_ns}")
            if bar.ts_ns < previous_ts:
                raise BinanceDataError(f"Binance 1s timestamps are not monotonic at row {line_number}")
        previous_ts = bar.ts_ns
        yield bar


def inspect_spot_1s(path: Path) -> BinanceParseStats:
    row_count = 0
    gap_count = 0
    missing_seconds = 0
    first_ts: int | None = None
    last_ts: int | None = None

    for bar in parse_spot_1s(path):
        if first_ts is None:
            first_ts = bar.ts_ns
        if last_ts is not None:
            delta = bar.ts_ns - last_ts
            if delta > _ONE_SECOND_NS:
                gap_count += 1
                missing_seconds += max(0, (delta // _ONE_SECOND_NS) - 1)
        last_ts = bar.ts_ns
        row_count += 1

    return BinanceParseStats(
        row_count=row_count,
        duplicate_count=0,
        gap_count=gap_count,
        missing_seconds=missing_seconds,
        first_ts_ns=first_ts,
        last_ts_ns=last_ts,
    )


def convert_spot_1s_to_parquet(path: Path, output_path: Path, *, batch_size: int = 65_536) -> int:
    """Convert an official Binance 1s archive to normalized Parquet without loading it all into memory."""
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise BinanceDataError("pyarrow is required for Binance Parquet normalization; install the research extra") from exc

    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent)
    os.close(descriptor)
    temp_path = Path(temp_name)
    schema = pa.schema(
        [
            ("ts_ns", pa.int64()),
            ("open", pa.float64()),
            ("high", pa.float64()),
            ("low", pa.float64()),
            ("close", pa.float64()),
            ("base_volume", pa.float64()),
            ("quote_volume", pa.float64()),
            ("trade_count", pa.int64()),
            ("taker_buy_base", pa.float64()),
            ("taker_buy_quote", pa.float64()),
        ]
    )
    writer = None
    rows: list[dict[str, int | float]] = []
    row_count = 0
    try:
        writer = pq.ParquetWriter(str(temp_path), schema=schema, compression="zstd")
        for bar in parse_spot_1s(path):
            rows.append(bar.model_dump())
            if len(rows) >= batch_size:
                writer.write_table(pa.Table.from_pylist(rows, schema=schema))
                row_count += len(rows)
                rows.clear()
        if rows:
            writer.write_table(pa.Table.from_pylist(rows, schema=schema))
            row_count += len(rows)
        writer.close()
        writer = None
        os.replace(temp_path, output_path)
        return row_count
    finally:
        if writer is not None:
            writer.close()
        if temp_path.exists():
            temp_path.unlink()
