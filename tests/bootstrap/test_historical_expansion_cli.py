"""Historical-first bulk data import must use real official sources and never train."""
from __future__ import annotations

import json

import pytest

from kalshi_edge import cli


class FakeReport:
    def __init__(self, **values):
        self.values = values

    def model_dump(self, *, mode="json"):
        assert mode == "json"
        return dict(self.values)


def test_bulk_history_cli_runs_all_three_verified_steps_in_order(
    tmp_path, monkeypatch, capsys,
):
    monkeypatch.setenv("KALSHI_BOOTSTRAP_BOOTSTRAP_DIR", str(tmp_path))
    calls = []

    def kalshi(settings, bootstrap, *, since_date=None, until_date=None,
               max_markets=None, progress_callback=None):
        calls.append("kalshi")
        assert bootstrap.bootstrap_dir == tmp_path
        assert max_markets == 12000
        assert since_date is None and until_date is None
        progress_callback(0, 12000, "", "markets")
        progress_callback(3, 12000, "KXBTC15M-TEST", "markets")
        return FakeReport(market_count=12000, downloaded_artifacts=120,
                          skipped_artifacts=210, excluded_markets=[])

    def binance(bootstrap, *, progress_callback=None):
        calls.append("binance")
        progress_callback(0, 120, "", "days")
        progress_callback(2, 120, "2026-10-30", "days")
        return FakeReport(day_count=120, downloaded_archives=14,
                          skipped_archives=106, normalized_archives=10,
                          unavailable_dates=[])

    def build(root, settings, *, progress_callback=None):
        calls.append("dataset")
        assert root == tmp_path
        progress_callback(0, 10000, "", "markets")
        progress_callback(10000, 10000, "KXBTC15M-LAST", "markets")
        return FakeReport(market_count=10000, row_count=180000,
                          excluded_markets=2000,
                          dataset_path=str(tmp_path / "derived" / "features.parquet"))

    monkeypatch.setattr("kalshi_edge.bootstrap.backfill.backfill_kalshi", kalshi)
    monkeypatch.setattr("kalshi_edge.bootstrap.backfill.backfill_binance", binance)
    monkeypatch.setattr("kalshi_edge.bootstrap.dataset.build_dataset", build)
    monkeypatch.setattr("kalshi_edge.bootstrap.evaluate.train_experiment",
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            AssertionError("historical import must never train or promote")
                        ))
    assert cli.main(["bootstrap-expand-history", "--max-markets", "12000"]) == 0
    assert calls == ["kalshi", "binance", "dataset"]

    output = capsys.readouterr().out
    progress = [
        json.loads(line.removeprefix("KALSHI_EDGE_PROGRESS "))
        for line in output.splitlines()
        if line.startswith("KALSHI_EDGE_PROGRESS ")
    ]
    assert any(x["source"] == "kalshi" and x["completed"] == 3
               and x["total"] == 12000 for x in progress)
    assert any(x["source"] == "binance" and x["completed"] == 2
               and x["unit"] == "days" for x in progress)
    assert any(x["source"] == "dataset" and x["completed"] == 10000
               for x in progress)
    result = json.loads(output.split("KALSHI_EDGE_EXPANSION_RESULT ", 1)[1])
    assert result["status"] == "HISTORICAL_DATASET_BUILT"
    assert result["kalshi"]["market_count"] == 12000
    assert result["dataset"]["market_count"] == 10000
    assert result["dataset"]["row_count"] == 180000
    assert result["models_trained"] is False
    assert result["model_promoted"] is False


@pytest.mark.parametrize("arguments", [
    ["--max-markets", "0"],
    ["--max-markets", "-1"],
    ["--since-date", "2026-06-01"],
    ["--until-date", "2026-06-01"],
    ["--since-date", "2026-11-01", "--until-date", "2026-06-01"],
])
def test_bulk_history_rejects_invalid_limits_without_network(arguments, monkeypatch):
    monkeypatch.setattr("kalshi_edge.bootstrap.backfill.backfill_kalshi",
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            AssertionError("must validate before network access")
                        ))
    with pytest.raises(SystemExit) as exc:
        cli.main(["bootstrap-expand-history", *arguments])
    assert exc.value.code == 2


def test_bulk_history_terminates_on_source_failure_without_lying_about_dataset(
    monkeypatch,
):
    calls = []
    def kalshi(*args, **kwargs):
        calls.append("kalshi")
        return FakeReport(market_count=1, downloaded_artifacts=1,
                          skipped_artifacts=0, excluded_markets=[])
    def fail_binance(*args, **kwargs):
        calls.append("binance")
        raise RuntimeError("checksum mismatch")
    def no_dataset(*args, **kwargs):
        calls.append("dataset")
        raise AssertionError("dataset must not be built after invalid archive")
    monkeypatch.setattr("kalshi_edge.bootstrap.backfill.backfill_kalshi", kalshi)
    monkeypatch.setattr("kalshi_edge.bootstrap.backfill.backfill_binance", fail_binance)
    monkeypatch.setattr("kalshi_edge.bootstrap.dataset.build_dataset", no_dataset)
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        cli.main(["bootstrap-expand-history"])
    assert calls == ["kalshi", "binance"]


def test_bulk_history_date_range_forwarded_to_official_discovery(
    tmp_path, monkeypatch,
):
    import datetime
    monkeypatch.setenv("KALSHI_BOOTSTRAP_BOOTSTRAP_DIR", str(tmp_path))
    recorded = []
    def kalshi(*args, **kwargs):
        recorded.append((kwargs["since_date"], kwargs["until_date"],
                         kwargs["max_markets"]))
        return FakeReport(market_count=1)
    monkeypatch.setattr("kalshi_edge.bootstrap.backfill.backfill_kalshi", kalshi)
    monkeypatch.setattr("kalshi_edge.bootstrap.backfill.backfill_binance",
                        lambda *a, **kw: FakeReport(day_count=1))
    monkeypatch.setattr("kalshi_edge.bootstrap.dataset.build_dataset",
                        lambda *a, **kw: FakeReport(market_count=1, row_count=18))
    assert cli.main(["bootstrap-expand-history", "--since-date", "2026-06-01",
                     "--until-date", "2026-10-31", "--max-markets", "5000"]) == 0
    assert recorded == [(datetime.date(2026, 6, 1),
                         datetime.date(2026, 10, 31), 5000)]
