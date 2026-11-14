"""TimesFM3 research pilot: bounded, read-only, no final lockbox access."""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "research" / "timesfm3_quickcheck.py"


def pilot():
    spec = importlib.util.spec_from_file_location("timesfm3_quickcheck", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_chronological_development_sample_is_deterministic_and_bounded():
    mod = pilot()
    names = tuple(f"market-{i}" for i in range(200))
    selection = mod.sample_spaced(names, limit=16)
    assert len(selection) == 16
    assert len(set(selection)) == 16
    assert selection[0] == names[0]
    assert selection[-1] == names[-1]
    assert selection == mod.sample_spaced(names, limit=16)
    assert mod.sample_spaced(names[:8], limit=16) == names[:8]
    with pytest.raises(ValueError):
        mod.sample_spaced(names, limit=0)


def test_causal_5s_window_ends_before_checkpoint_and_does_not_impute_gaps():
    mod = pilot()
    checkpoint = 2000
    # Last completed data is from checkpoint minus 5 seconds.
    source = {t: float(t) for t in range(1950, 2020)}
    context = mod.causal_five_second_context(source, checkpoint_second=checkpoint, steps=8)
    assert context == [1960.0, 1965.0, 1970.0, 1975.0,
                       1980.0, 1985.0, 1990.0, 1995.0]
    assert context[-1] == checkpoint - 5
    assert 2000.0 not in context
    del source[1970]
    with pytest.raises(ValueError, match="missing"):
        mod.causal_five_second_context(source, checkpoint_second=checkpoint, steps=8)


def test_quick_score_comparison_is_matched_by_ticker_and_has_no_fake_probability():
    mod = pilot()
    prepared = {
        "records": [
            {"ticker": "m1", "label_yes": 1, "incumbent_p_yes": .92,
             "strike": 100.0, "strike_type": "greater",
             "context": [99.0, 99.0]},
            {"ticker": "m2", "label_yes": 0, "incumbent_p_yes": .89,
             "strike": 100.0, "strike_type": "greater_or_equal",
             "context": [101.0, 101.0]},
            {"ticker": "m3", "label_yes": 0, "incumbent_p_yes": .1,
             "strike": 100.0, "strike_type": "greater",
             "context": [101.0, 101.0]},
        ],
        "experiment_kind": "NON_DEPLOYABLE_EXPLORATORY",
        "incumbent_bundle_sha": "a" * 64,
    }
    forecast = {
        "results": [
            {"ticker": "m1", "forecast_price": 101.0},
            {"ticker": "m2", "forecast_price": 100.0},
            {"ticker": "m3", "forecast_price": 101.0},
        ],
        "checkpoint": "google/timesfm-3.0-pytorch",
    }
    result = mod.compare(prepared, forecast)
    assert result["count"] == 3
    assert result["incumbent_accuracy"] == pytest.approx(2 / 3)
    assert result["timesfm3_accuracy"] == pytest.approx(1 / 3)
    assert result["timesfm3_only_correct"] == 0
    assert result["incumbent_only_correct"] == 1
    assert result["confidence_gated_timesfm3_accuracy"] is None
    assert result["brier_timesfm3"] is None
    assert result["promotion_eligible"] is False


def test_refuses_missing_forecasts_duplicate_tickers_and_nonfinite_prediction():
    mod = pilot()
    prepared = {"records": [
        {"ticker": "m1", "label_yes": 1, "incumbent_p_yes": .8,
         "strike": 100., "strike_type": "greater", "context": [95.]},
    ], "experiment_kind": "NON_DEPLOYABLE_EXPLORATORY"}
    for results in ([], [{"ticker": "m1", "forecast_price": math.nan}],
                    [{"ticker": "m1", "forecast_price": 110.0},
                     {"ticker": "m1", "forecast_price": 100.0}]):
        with pytest.raises(ValueError):
            mod.compare(prepared, {"results": results})


def test_full_model_import_is_not_required_for_inference_and_license_gate_precedes_load():
    mod = pilot()
    with pytest.raises(ValueError, match="license"):
        mod.run_infer(Path("no-input.json"), Path("no-output.json"),
                      accepted_noncommercial_license=False)


def test_prepare_rejects_no_out_of_fit_cohort_by_default_but_accepts_explicit_biased_diagnostic():
    mod = pilot()
    groups = [(f"KXBTC15M-{i:04d}", 1000 + 10 * i, 1009 + 10 * i)
              for i in range(40)]
    development = {ticker for ticker, _, _ in groups[:32]}
    with pytest.raises(ValueError, match="--allow-incumbent-training-overlap"):
        mod.select_research_markets(
            groups, development, incumbent_calibration_end_ns=1800,
            next_calibration_start_ns=1400,
            allow_incumbent_training_overlap=False,
        )
    chosen, scope = mod.select_research_markets(
        groups, development, incumbent_calibration_end_ns=1800,
        next_calibration_start_ns=1400,
        allow_incumbent_training_overlap=True,
    )
    assert scope == "INCUMBENT_TRAINING_OVERLAP"
    assert len(chosen) == 32
    assert set(chosen) == development
    assert all("KXBTC15M-00" in ticker for ticker in chosen)


def test_prepare_prefers_out_of_fit_dev_groups_without_biased_flag():
    mod = pilot()
    groups = [(f"m{i}", 1000 + 20 * i, 1010 + 20 * i) for i in range(25)]
    development = {ticker for ticker, _, _ in groups[:20]}
    chosen, scope = mod.select_research_markets(
        groups, development, incumbent_calibration_end_ns=1210,
        next_calibration_start_ns=1400,
        allow_incumbent_training_overlap=False,
    )
    assert scope == "POST_INCUMBENT_CALIBRATION"
    assert chosen == tuple(f"m{i}" for i in range(11, 20))


def test_prepare_never_allows_locked_or_calibration_groups_even_with_explicit_overlap():
    mod = pilot()
    groups = [(f"m{i}", 1000 + 20 * i, 1010 + 20 * i) for i in range(30)]
    development = {f"m{i}" for i in range(10)}
    chosen, scope = mod.select_research_markets(
        groups, development, incumbent_calibration_end_ns=2000,
        next_calibration_start_ns=1200,
        allow_incumbent_training_overlap=True,
    )
    assert scope == "INCUMBENT_TRAINING_OVERLAP"
    assert chosen == tuple(f"m{i}" for i in range(10))
    assert "m10" not in chosen
    assert "m29" not in chosen


def test_in_sample_score_explicitly_disclaims_which_model_is_better():
    mod = pilot()
    prepared = {
        "records": [
            {"ticker": "m1", "label_yes": 1, "incumbent_p_yes": .99,
             "strike": 100.0, "strike_type": "greater", "context": [99.0]},
        ],
        "sample_scope": "INCUMBENT_TRAINING_OVERLAP",
        "experiment_kind": "NON_DEPLOYABLE_EXPLORATORY",
    }
    forecasts = {"results": [{"ticker": "m1", "forecast_price": 102.0}]}
    comparison = mod.compare(prepared, forecasts)
    assert comparison["incumbent_results_in_sample"] is True
    assert comparison["comparison_valid_for_superiority"] is False
    assert comparison["winner"] is None
    assert "in-sample" in comparison["limitations"].lower()
    assert comparison["promotion_eligible"] is False


def test_commandline_explicit_overlap_flag_is_forwarded_not_silent(monkeypatch, capsys):
    mod = pilot()
    calls = []

    def fake_prepare(**kwargs):
        calls.append(kwargs)
        return {"status": "PREPARED"}
    monkeypatch.setattr(mod, "run_prepare", fake_prepare)
    assert mod.main(["prepare", "--markets", "32"]) == 0
    assert calls[-1]["allow_incumbent_training_overlap"] is False
    assert mod.main(["prepare", "--markets", "32",
                     "--allow-incumbent-training-overlap"]) == 0
    assert calls[-1]["allow_incumbent_training_overlap"] is True
