"""Missing official 1s Binance day should heal training inputs incrementally.

Never fabricate candles, bypass manifest verification or loop on an unavailable
or corrupt raw archive. Existing dataset checkpoints must remain reusable.
"""
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from kalshi_edge.bootstrap import backfill, dataset, evaluate, training_cycle
from kalshi_edge.bootstrap.config import BootstrapSettings


def _worker_ok(monkeypatch, tmp_path, build):
    (tmp_path / "raw" / "kalshi" / "markets").mkdir(parents=True)
    monkeypatch.setattr(training_cycle, "inspect_baseline_training_readiness",
                        lambda root: {"ready": True, "reason": "verified"})
    monkeypatch.setattr(dataset, "build_dataset", build)
    monkeypatch.setattr(training_cycle.training, "_verified_context",
                        lambda root: SimpleNamespace())
    monkeypatch.setattr(evaluate, "train_experiment",
                        lambda *a, **kw: SimpleNamespace(
                            experiment_bundle_sha256="a"*64,
                            experiment_path=tmp_path/"experiment.json"))
    monkeypatch.setattr(evaluate, "run_lockbox_evaluation",
                        lambda *a, **kw: SimpleNamespace(
                            decision=SimpleNamespace(promoted=False,
                                                     reason_codes=("NOT_BEATEN",)),
                            report_path=tmp_path/"report.json"))
    monkeypatch.setattr(training_cycle, "_verified_lockbox_comparison",
                        lambda _: {})
    return lambda: training_cycle.run_training_worker(
        tmp_path, mode="baseline", round_number=0, git_sha="b"*40)


def test_worker_recovers_exact_missing_day_and_continues_from_checkpoints(tmp_path, monkeypatch):
    calls = []
    def build(root, settings, *, progress_callback=None):
        calls.append("build")
        if len([c for c in calls if c == "build"]) == 1:
            progress_callback(29253, 29865, "before-gap", "markets")
            raise dataset.DatasetBuildError(
                "required Binance history is missing for 2026-10-27")
        progress_callback(29865, 29865, "after-repair", "markets")
        return SimpleNamespace(market_count=29865, row_count=120000)
    def repair(settings, *, required_dates):
        assert required_dates == (date(2026,10,27),)
        calls.append("repair")
        return SimpleNamespace(unavailable_dates=(), normalized_archives=1)
    monkeypatch.setattr(backfill, "backfill_binance", repair)
    run = _worker_ok(monkeypatch, tmp_path, build)
    assert run() == 0
    assert calls == ["build", "repair", "build"]
    result = training_cycle._run_state(tmp_path)
    assert result["stage"] == "COMPLETE"
    assert result["dataset_markets_completed"] == 29865


def test_worker_repairs_normalization_if_raw_verified_but_parquet_missing(tmp_path, monkeypatch):
    count = 0
    def build(root, settings, *, progress_callback=None):
        nonlocal count
        count += 1
        if count == 1:
            raise dataset.DatasetBuildError(
                "Binance normalized history is missing for verified raw archive 2026-10-27")
        return SimpleNamespace(market_count=1, row_count=1)
    repaired = []
    monkeypatch.setattr(backfill,"backfill_binance",
                        lambda settings, *, required_dates:
                        repaired.append(required_dates[0]) or
                        SimpleNamespace(unavailable_dates=(), normalized_archives=1))
    run = _worker_ok(monkeypatch, tmp_path, build)
    assert run() == 0
    assert repaired == [date(2026,10,27)]


def test_training_never_repairs_corrupted_provenance(tmp_path, monkeypatch):
    def build(*args, **kwargs):
        raise dataset.DatasetBuildError(
            "Binance normalized provenance verification failed for 2026-10-27")
    monkeypatch.setattr(backfill, "backfill_binance",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            AssertionError("must not auto-replace corrupt sources")))
    run = _worker_ok(monkeypatch, tmp_path, build)
    assert run() == 2
    state = training_cycle._run_state(tmp_path)
    assert state["stage"] == "FAILED"
    assert "provenance" in state["error"]


def test_worker_fails_closed_if_day_still_missing_after_one_repair(tmp_path, monkeypatch):
    attempts = []
    def build(*args, **kwargs):
        attempts.append("build")
        raise dataset.DatasetBuildError(
            "required Binance history is missing for 2026-10-27")
    def repair(settings, *, required_dates):
        attempts.append("repair")
        return SimpleNamespace(unavailable_dates=(), normalized_archives=1)
    monkeypatch.setattr(backfill,"backfill_binance",repair)
    run = _worker_ok(monkeypatch, tmp_path, build)
    assert run() == 2
    assert attempts == ["build", "repair", "build"]
    assert "2026-10-27" in training_cycle._run_state(tmp_path)["error"]


def test_binance_backfill_can_request_only_one_verified_enrolled_day(tmp_path, monkeypatch):
    from kalshi_edge.bootstrap import backfill as bf
    from kalshi_edge.bootstrap.provenance import write_raw_artifact
    monkeypatch.setattr(bf, "_dates_for_valid_kalshi_markets",
                        lambda root, *, stop_after=None: [date(2026,10,26),date(2026,10,27)])
    class FakeClient:
        def __init__(self): self.urls=[]
        def download_and_verify(self, url, checksum_url, root):
            self.urls.append(url)
            assert checksum_url == url + ".CHECKSUM"
            filename = url.rsplit("/",1)[-1]
            return write_raw_artifact(root=root, source="binance",
                                      logical_name="archive/"+filename,
                                      content=b"valid archived data",
                                      metadata={"source_locator":url})
    client=FakeClient()
    def convert(source, target):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"PAR1")
        return 1
    report=bf.backfill_binance(
        BootstrapSettings(bootstrap_dir=tmp_path),
        client=client,parquet_converter=convert,
        required_dates=(date(2026,10,27),))
    assert len(client.urls)==1
    assert client.urls[0].endswith("BTCUSDT-1s-2026-10-27.zip")
    assert report.dates==("2026-10-27",)
    assert report.normalized_archives==1


def test_binance_backfill_rejects_unenrolled_requested_day(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setattr(backfill, "_dates_for_valid_kalshi_markets",
                        lambda root, *, stop_after=None: [date(2026,10,26)])
    with pytest.raises(Exception, match="verified|required|market|enrolled"):
        backfill.backfill_binance(
            BootstrapSettings(bootstrap_dir=tmp_path),
            required_dates=(date(2026,10,27),))


def test_failed_training_ui_distinguishes_stopped_run_from_active_progress():
    from kalshi_edge.bootstrap.dashboard_ui import PAGE
    from kalshi_edge.bootstrap.site_pages import TRAIN_STATUS_SESSION_HELPERS

    assert 'failed?"FAILED"' in PAGE
    assert 'failed?"Dataset build stopped"' in PAGE
    assert 'run.stage==="FAILED"?"Previous training stopped' in PAGE
    assert 'snapshot.status.run?.stage==="FAILED"' in TRAIN_STATUS_SESSION_HELPERS
    assert "Previous training failed" in TRAIN_STATUS_SESSION_HELPERS


def test_targeted_binance_repair_stops_market_scan_after_verified_required_date(tmp_path, monkeypatch):
    """Repair near newest markets shouldn't rescan all 30k historical records."""
    import json
    from kalshi_edge.bootstrap import backfill as bf
    from kalshi_edge.bootstrap.provenance import write_raw_artifact

    root=tmp_path/"data/bootstrap"
    valid={
        "market":{
            "ticker":"KXBTC15M-ZZZ-RECENT","event_ticker":"KXBTC15M-EVENT",
            "market_type":"binary","open_time":"2026-10-31T12:00:00Z",
            "close_time":"2026-10-31T12:15:00Z",
            "settlement_ts":"2026-10-31T12:16:00Z","status":"finalized",
            "result":"yes","settlement_value_dollars":"74100.0",
            "strike_type":"greater","floor_strike":74000.0,
            "rules_primary":"above strike","rules_secondary":"verified",
            "is_provisional":False
        }
    }
    write_raw_artifact(root=root,source="kalshi",
        logical_name="markets/KXBTC15M-ZZZ-RECENT.json",
        content=json.dumps(valid).encode(),metadata={})
    bad_market=root/"raw/kalshi/markets/KXBTC15M-AAA-OLD.json"
    bad_market.write_text("corrupt",encoding="utf-8")
    checks=[]
    monkeypatch.setattr(bf, "_required_kalshi_history_available",
                        lambda root, ticker: True)
    real_verify=bf.verify_artifact
    def observed(*args):
        checks.append(args[0].name)
        return real_verify(*args)
    monkeypatch.setattr(bf,"verify_artifact",observed)

    required=bf._dates_for_valid_kalshi_markets(
        root,stop_after={date(2026,10,31)})
    assert date(2026,10,31) in required
    assert checks==["KXBTC15M-ZZZ-RECENT.json"]

    # The full-archive path still performs strict verification of every
    # source and rejects the deliberately corrupted unrelated market.
    import pytest
    with pytest.raises(Exception,match="provenance verification"):
        bf._dates_for_valid_kalshi_markets(root)
