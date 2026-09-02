from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from kalshi_edge.bootstrap.config import BootstrapSettings
from kalshi_edge.bootstrap.dataset import DatasetBuildError, build_dataset
from kalshi_edge.bootstrap.provenance import RawArtifact, sha256_file, verify_artifact, write_manifest, write_raw_artifact


def _market_payload(ticker: str) -> dict:
    return {"market":{"ticker":ticker,"event_ticker":"KXBTC15M-EVENT","market_type":"binary","open_time":"2026-08-01T12:00:00Z","close_time":"2026-08-01T12:15:00Z","settlement_ts":"2026-08-01T12:16:00Z","status":"finalized","result":"yes","settlement_value_dollars":"101.0","strike_type":"greater","floor_strike":100.0,"rules_primary":"Resolves Yes if above target.","rules_secondary":"CF Benchmarks","is_provisional":False}}


def _trade_payload(ticker: str) -> dict:
    return {"ticker":ticker,"trades":[{"created_time":"2026-08-01T12:05:00Z","yes_price_dollars":"0.55","count_fp":"10.0","taker_side":"yes"}]}


def _candle_payload(ticker: str) -> dict:
    return {"ticker":ticker,"candlesticks":[{"end_period_ts":1785585960,"yes_bid":{"close":"0.54"},"yes_ask":{"close":"0.56"},"price":{"close":"0.55","high":"0.57","low":"0.53"},"volume":"10.0","open_interest":"5.0"}]}


def _write_kalshi(root: Path, logical: str, payload: dict) -> None:
    write_raw_artifact(root=root,source="kalshi",logical_name=logical,content=(json.dumps(payload)+"\n").encode(),metadata={"source_locator":logical})


def _seed(root: Path) -> None:
    ticker="KXBTC15M-TEST"
    for logical,payload in [(f"markets/{ticker}.json",_market_payload(ticker)),(f"trades/{ticker}.json",_trade_payload(ticker)),(f"candlesticks/{ticker}.json",_candle_payload(ticker))]:
        _write_kalshi(root,logical,payload)
    start=int(datetime(2026,8,1,12,0,tzinfo=timezone.utc).timestamp()*1_000_000_000)
    rows=[{"ts_ns":start+i*1_000_000_000,"open":100+i/1000,"high":100+i/1000,"low":100+i/1000,"close":100+i/1000,"base_volume":1.0,"quote_volume":100.0,"trade_count":1,"taker_buy_base":0.6,"taker_buy_quote":60.0} for i in range(901)]
    raw=write_raw_artifact(root=root,source="binance",logical_name="archive/BTCUSDT-1s-2026-08-01.zip",content=b"archive",metadata={"source_locator":"official"})
    out=root/"normalized/binance/1s/BTCUSDT-1s-2026-08-01.parquet"; out.parent.mkdir(parents=True,exist_ok=True); pq.write_table(pa.Table.from_pylist(rows),out)
    art=RawArtifact(path=out.relative_to(root),manifest_path=Path("manifests/binance_normalized/1s/BTCUSDT-1s-2026-08-01.parquet.manifest.json"),sha256=sha256_file(out),source="binance_normalized",retrieval_ts_utc="2026-08-31T00:00:00+00:00",byte_count=out.stat().st_size,metadata={"source_raw_path":raw.path.as_posix(),"source_raw_sha256":raw.sha256,"row_count":901})
    write_manifest(root,art)


def _seed_second_market_without_candles(root: Path, ticker: str = "KXBTC15M-MISSING") -> None:
    _write_kalshi(root,f"markets/{ticker}.json",_market_payload(ticker))
    _write_kalshi(root,f"trades/{ticker}.json",_trade_payload(ticker))


def test_build_dataset_writes_18_causal_rows_and_provenance(tmp_path: Path) -> None:
    root=tmp_path/"data/bootstrap"; _seed(root)
    report=build_dataset(root,BootstrapSettings(bootstrap_dir=root))
    assert report.market_count==1 and report.row_count==18 and report.leakage_finding_count==0
    table=pq.read_table(report.dataset_path)
    assert table.num_rows==18
    assert "settlement_value" not in table.column_names and "market_result_yes" not in table.column_names
    assert verify_artifact(report.dataset_path,report.manifest_path)
    assert report.provenance_path.exists()


def test_build_dataset_rejects_corrupt_input_manifest(tmp_path: Path) -> None:
    root=tmp_path/"data/bootstrap"; _seed(root)
    market=root/"raw/kalshi/markets/KXBTC15M-TEST.json"; market.write_text(market.read_text()+"x")
    with pytest.raises(DatasetBuildError,match="provenance"):
        build_dataset(root,BootstrapSettings(bootstrap_dir=root))


def test_build_dataset_excludes_market_when_required_candlestick_artifact_and_manifest_are_both_absent(tmp_path: Path) -> None:
    root=tmp_path/"data/bootstrap"; _seed(root); _seed_second_market_without_candles(root)

    report=build_dataset(root,BootstrapSettings(bootstrap_dir=root))

    assert report.market_count==1
    assert report.row_count==18
    assert report.excluded_markets==("KXBTC15M-MISSING",)


def test_build_dataset_rejects_partial_candlestick_provenance_when_data_exists_without_manifest(tmp_path: Path) -> None:
    root=tmp_path/"data/bootstrap"; _seed(root); _seed_second_market_without_candles(root)
    data=root/"raw/kalshi/candlesticks/KXBTC15M-MISSING.json"
    data.parent.mkdir(parents=True,exist_ok=True)
    data.write_text(json.dumps(_candle_payload("KXBTC15M-MISSING"))+"\n",encoding="utf-8")

    with pytest.raises(DatasetBuildError,match="provenance"):
        build_dataset(root,BootstrapSettings(bootstrap_dir=root))


def test_build_dataset_rejects_partial_candlestick_provenance_when_manifest_exists_without_data(tmp_path: Path) -> None:
    root=tmp_path/"data/bootstrap"; _seed(root); _seed_second_market_without_candles(root)
    _write_kalshi(root,"candlesticks/KXBTC15M-MISSING.json",_candle_payload("KXBTC15M-MISSING"))
    (root/"raw/kalshi/candlesticks/KXBTC15M-MISSING.json").unlink()

    with pytest.raises(DatasetBuildError,match="provenance"):
        build_dataset(root,BootstrapSettings(bootstrap_dir=root))
