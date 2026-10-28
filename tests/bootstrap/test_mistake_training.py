from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from kalshi_edge.bootstrap import training
from kalshi_edge.bootstrap import mistake_learning


def _groups(count: int):
    # Non-overlapping chronological market groups with intact 15m windows.
    return [(f"KXBTC15M-M{i:04d}", 100000 + i * 100, 100050 + i * 100) for i in range(count)]


def _existing_model(root: Path) -> None:
    default = root / "models" / "default.json"
    default.parent.mkdir(parents=True, exist_ok=True)
    default.write_text("{}")


def test_mistake_training_selects_only_error_case_development_markets(monkeypatch, tmp_path: Path) -> None:
    groups = _groups(90)
    _existing_model(tmp_path)
    monkeypatch.setattr(training, "_metadata_groups", lambda _path: groups)
    # Previous eval ends on market 19; 70 new groups remain. The newest 14
    # are held out as a new lockbox; 5 immediately before are calibration.
    selected = {g[0] for g in groups[20:40:1]}
    seen = []

    def mock_verified(root, *, allowed_tickers=None):
        seen.append(set(allowed_tickers))
        assert all(name not in allowed_tickers for name in [g[0] for g in groups[76:]])
        return tuple(
            (
                SimpleNamespace(market_ticker=name, selected_label="yes"),
                tmp_path / "mistake_pool" / "2026-10-28" / (name + ".json"),
                "a" * 64,
            )
            for name in sorted(selected & allowed_tickers)
        )

    monkeypatch.setattr(mistake_learning, "verified_mistake_pool", mock_verified)
    plan, cases = training._mistake_partition_plan(
        tmp_path, tmp_path / "features.parquet", previous_lockbox_end_ts_ns=101950
    )
    assert len(plan.development_groups) == 20
    assert set(plan.development_groups) == selected
    assert len(plan.calibration_groups) >= 2
    assert len(plan.lockbox_groups) >= 2
    assert set(plan.calibration_groups).isdisjoint(selected)
    assert set(plan.lockbox_groups).isdisjoint(selected)
    assert plan.boundaries.train_end_ts_ns < plan.boundaries.calibration_start_ts_ns
    assert plan.boundaries.calibration_end_ts_ns < plan.boundaries.lockbox_start_ts_ns
    assert len(cases) == 20
    assert len(seen) == 1


def test_mistake_training_fails_before_model_fitting_without_enough_cases(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(training, "_metadata_groups", lambda _path: _groups(70))
    _existing_model(tmp_path)
    monkeypatch.setattr(
        mistake_learning, "verified_mistake_pool",
        lambda _root, *, allowed_tickers: (),
    )
    with pytest.raises(training.TrainingError, match="at least 12"):
        training._mistake_partition_plan(
            tmp_path, tmp_path / "features.parquet", previous_lockbox_end_ts_ns=100950,
        )


def test_mistake_training_requires_fresh_untouched_markets(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(training, "_metadata_groups", lambda _path: _groups(30))
    _existing_model(tmp_path)
    with pytest.raises(training.TrainingError, match="at least 20"):
        training._mistake_partition_plan(
            tmp_path, tmp_path / "features.parquet", previous_lockbox_end_ts_ns=102000,
        )


def test_mistake_training_refuses_no_prior_evaluation_or_default(tmp_path: Path) -> None:
    with pytest.raises(training.TrainingError, match="prior evaluation"):
        training._mistake_partition_plan(tmp_path, tmp_path / "features.parquet", None)
    with pytest.raises(training.TrainingError, match="promoted default"):
        training._mistake_partition_plan(tmp_path, tmp_path / "features.parquet", 1000)


def test_mistake_mode_dataset_context_defaults_baseline_only_in_programmatic_calls() -> None:
    assert training.DatasetContext.__dataclass_fields__["mistakes_only"].default is False
    assert training.DatasetContext.__dataclass_fields__["selected_tickers"].default == ()
