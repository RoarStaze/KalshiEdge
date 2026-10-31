"""Readiness displays measured partial collection, without bypassing train gates."""
from types import SimpleNamespace

from kalshi_edge.bootstrap import training_cycle


def _ready_inputs(tmp_path, monkeypatch, *, count, classes, fresh):
    root = tmp_path
    (root / "models").mkdir()
    (root / "models" / "default.json").write_text("{}")
    (root / "derived").mkdir()
    (root / "derived" / "features.parquet").write_bytes(b"fixture-not-read")
    monkeypatch.setattr(training_cycle.training, "_previous_lockbox_end_ts_ns", lambda _: 10000)
    groups = [(f"MARKET-DEV-{i}", 100 + i * 100, 150 + i * 100) for i in range(20)]
    groups += [(f"MARKET-FRESH-{i}", 10100 + i * 100, 10140 + i * 100) for i in range(fresh)]
    monkeypatch.setattr(training_cycle.training, "_metadata_groups", lambda _: groups)
    samples = [
        (SimpleNamespace(selected_label="ABOVE" if i % classes == 0 else "BELOW"),
         None, None) for i in range(count)
    ]
    monkeypatch.setattr(training_cycle, "_learning_pool", lambda *_args, **_kw: samples)
    return root


def test_partial_cases_progress_even_when_fresh_markets_are_zero(tmp_path,monkeypatch):
    root = _ready_inputs(tmp_path,monkeypatch,count=6,classes=2,fresh=0)
    status = training_cycle.inspect_training_readiness(root)
    assert status["eligible_markets"] == 6
    assert status["new_evaluation_markets"] == 0
    assert 0 < status["progress_percent"] < 100
    assert status["ready"] is False


def test_progress_increases_monotonically_with_independent_inputs(tmp_path,monkeypatch):
    root = _ready_inputs(tmp_path,monkeypatch,count=2,classes=1,fresh=0)
    first = training_cycle.inspect_training_readiness(root)
    _ready_inputs_existing = lambda **kw: None
    # Reconfigure the same mocked data inputs as evidence accumulates.
    groups = [(f"MARKET-DEV-{i}", 100 + i * 100, 150 + i * 100) for i in range(20)]
    groups += [(f"MARKET-FRESH-{i}", 10100+i*100, 10140+i*100) for i in range(4)]
    monkeypatch.setattr(training_cycle.training, "_metadata_groups", lambda _: groups)
    samples = [(SimpleNamespace(selected_label=("ABOVE" if i % 2 == 0 else "BELOW")),
                None,None) for i in range(6)]
    monkeypatch.setattr(training_cycle, "_learning_pool", lambda *_a,**_k: samples)
    second = training_cycle.inspect_training_readiness(root)
    assert 0 < first["progress_percent"] < second["progress_percent"] < 100
    assert second["outcome_classes"] == 2
    assert second["new_evaluation_markets"] == 4
    assert second["ready"] is False


def test_early_missing_baseline_reports_blocked_not_collecting(tmp_path,monkeypatch):
    monkeypatch.setattr(training_cycle.training, "_previous_lockbox_end_ts_ns", lambda _: None)
    status = training_cycle.inspect_training_readiness(tmp_path)
    assert status["progress_percent"] == 0
    assert status["status"] == "BLOCKED"
    assert "baseline" in status["reason"].lower()
    assert not status["ready"]


def test_missing_features_reports_blocked_reason(tmp_path,monkeypatch):
    (tmp_path/"models").mkdir()
    (tmp_path/"models"/"default.json").write_text("{}")
    monkeypatch.setattr(training_cycle.training,"_previous_lockbox_end_ts_ns",lambda _:10000)
    status=training_cycle.inspect_training_readiness(tmp_path)
    assert status["status"]=="BLOCKED"
    assert status["progress_percent"]==0
    assert "feature" in status["reason"].lower()
    assert not status["ready"]


def test_training_ui_displays_initial_values_and_refresh_failure_explicitly():
    from kalshi_edge.bootstrap.dashboard_ui import PAGE
    from kalshi_edge.bootstrap.site_pages import render_site_page
    from kalshi_edge.bootstrap.round_price_ui import integrate_round_price_hero
    from kalshi_edge.bootstrap.manual_trade_ui import integrate_manual_trade
    from kalshi_edge.bootstrap.api_credentials_ui import integrate_api_credentials
    html=render_site_page(integrate_api_credentials(integrate_manual_trade(integrate_round_price_hero(PAGE))),"train")
    assert 'id="training-cases">0' in html
    assert 'id="training-outcomes">0' in html
    assert 'id="training-lockbox">0' in html
    assert "Training readiness unavailable" in html
    assert "info.progress_percent" in html
    assert "refreshTraining()" in html
