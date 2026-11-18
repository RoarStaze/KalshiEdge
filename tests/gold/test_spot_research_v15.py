"""Independent XAUUSD CSV research needs neither Pyth nor Kalshi training data."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import csv
import math

import pytest

from kalshi_edge.gold.spot_research import (
    SpotResearchError, build_spot_research, inspect_spot_research,
    spot_research_rows,
)
from kalshi_edge.gold.research_models import compare_spot_research_models
from kalshi_edge.gold.audit import main

START = int(datetime(2026, 8, 10, tzinfo=timezone.utc).timestamp() * 1000)
MINUTE = 60_000


def make_csv(path: Path, *, n: int = 4 * 1440, omit: set[int] | None = None):
    omit = omit or set()
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.writer(fp)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for i in range(n):
            if i in omit:
                continue
            v = 2000 + .002 * i + .25 * math.sin(i / 13) + .1 * math.cos(i / 41)
            writer.writerow([START + i * MINUTE, f"{v:.6f}", f"{v + .12:.6f}",
                             f"{v - .12:.6f}", f"{v:.6f}", "25"])


def test_no_pyth_spot_dataset_has_only_independent_gold_price_labels(tmp_path):
    file = tmp_path / "xauusd-m1.csv"
    make_csv(file)
    report = build_spot_research(file, tmp_path / "gold")
    assert report["candidate_research_rows"] > 250
    assert report["eligible_training_rows"] == 0
    assert report["dataset_ready"] is False
    assert report["settlement_oracle_verified"] is False
    assert report["label_status"] == "XAUUSD_15M_SPOT_DIRECTION_PROXY_NOT_KALSHI"
    assert report["source_label"] == "USER_SUPPLIED_XAUUSD_CSV"
    artifact = inspect_spot_research(Path(report["artifact_path"]))
    rows = spot_research_rows(artifact)
    assert len(rows) == report["candidate_research_rows"]
    assert all(row.ticker.startswith("SPOT-XAUUSD-") for row in rows)
    assert all(row.feature_end_ns <= row.decision_ns - 60 * 1_000_000_000 for row in rows)
    assert all(row.label_available_proxy_ns >= row.close_ns + 86400 * 1_000_000_000 for row in rows)
    assert all(len(row.lag_returns_bps) == 20 for row in rows)
    assert artifact["source_hashes"]["csv_sha256"]
    assert "KALSHI_REPORTED_UNVERIFIED" not in str(artifact)
    assert "UNVERIFIED_PYTH_HISTORICAL_OHLC" not in str(artifact)


def test_unavailable_future_candle_does_not_modify_earlier_features(tmp_path):
    file = tmp_path / "xauusd.csv"
    make_csv(file, n=200)
    first = build_spot_research(file, tmp_path / "gold")
    original = spot_research_rows(inspect_spot_research(Path(first["artifact_path"])))
    lines = file.read_text().splitlines()
    # The first candidate is about 30 minutes in; this late 1-minute
    # observation must not alter the earlier decision-time features.
    index = 100
    values = lines[index + 1].split(",")
    values[1:5] = ["9000", "9001", "8999", "9000"]
    lines[index + 1] = ",".join(values)
    file.write_text("\n".join(lines)+"\n")
    second = build_spot_research(file, tmp_path / "gold")
    updated = spot_research_rows(inspect_spot_research(Path(second["artifact_path"])))
    assert original[0].ticker == updated[0].ticker
    assert original[0].features == updated[0].features
    # Late future outcome may differ; label is NOT an input feature.


def test_gap_skips_windows_without_interpolating_or_filling(tmp_path):
    file = tmp_path / "xauusd.csv"
    make_csv(file, n=300, omit={29, 30, 32, 140})
    report = build_spot_research(file, tmp_path / "gold")
    assert report["excluded"]["INCOMPLETE_SPOT_WINDOW"] > 0
    assert report["candidate_research_rows"] > 0


@pytest.mark.parametrize("mutation", ["bad_price", "duplicate", "unsorted", "seconds", "bad_header"])
def test_bad_source_prices_and_timestamps_are_rejected(tmp_path, mutation):
    file = tmp_path / "xauusd.csv"
    make_csv(file, n=210)
    lines = file.read_text().splitlines()
    if mutation == "bad_price":
        lines[20] = lines[20].replace(lines[20].split(",")[2], "-1", 1)
    elif mutation == "duplicate":
        cols = lines[12].split(",")
        other = lines[13].split(",")
        other[0] = cols[0]
        lines[13] = ",".join(other)
    elif mutation == "unsorted":
        lines[12], lines[13] = lines[13], lines[12]
    elif mutation == "seconds":
        cols = lines[20].split(",")
        cols[0] = str(int(cols[0]) // 1000)
        lines[20] = ",".join(cols)
    else:
        lines[0] = "utc,open,high,low,close"
    file.write_text("\n".join(lines)+"\n")
    with pytest.raises(SpotResearchError):
        build_spot_research(file, tmp_path / "gold")


def test_research_artifact_sha256_rejects_mutation(tmp_path):
    file = tmp_path / "xauusd.csv"
    make_csv(file, n=250)
    report = build_spot_research(file, tmp_path / "gold")
    artifact = Path(report["artifact_path"])
    artifact.write_bytes(artifact.read_bytes() + b" ")
    with pytest.raises(SpotResearchError):
        inspect_spot_research(artifact)


def test_nonpyth_models_are_explicitly_non_deployable(tmp_path):
    pytest.importorskip("xgboost")
    pytest.importorskip("sklearn")
    file = tmp_path / "xauusd.csv"
    make_csv(file)
    report = build_spot_research(file, tmp_path / "gold")
    result = compare_spot_research_models(report["artifact_path"], min_train=30, min_test=40)
    assert result["deployable"] is False
    assert result["research_only"] is True
    assert result["label_status"] == "XAUUSD_15M_SPOT_DIRECTION_PROXY_NOT_KALSHI"
    assert result["train_max_label_available_ns"] < result["test_start_ns"]
    assert set(result["models"]) >= {"xgboost", "lagged_time_series", "regime_aware"}
    assert result["trading_enabled"] is False
    assert result["macro_component"] == "DISABLED_NO_VERIFIED_ASOF_NEWS"


def test_cli_no_api_key_or_pyth_artifact_required(tmp_path, capsys):
    file = tmp_path / "xauusd.csv"
    make_csv(file, n=200)
    assert main(["--build-spot-research", str(file),
                 "--output-root", str(tmp_path / "gold")]) == 0
    assert "XAUUSD_15M_SPOT_DIRECTION_PROXY_NOT_KALSHI" in capsys.readouterr().out
