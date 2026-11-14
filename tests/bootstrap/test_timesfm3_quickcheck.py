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
