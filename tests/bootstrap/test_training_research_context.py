"""Fail-closed validation for explicit historical research context override."""
from types import SimpleNamespace
import pytest

from kalshi_edge.bootstrap import training


def fixture():
    groups=[(f"m{i}",10000+i*900,10000+i*900+780) for i in range(100)]
    base=SimpleNamespace(
        dataset_path="features.parquet",
        manifest_path="features.parquet.manifest.json",
        provenance_path="features.provenance.json",
        feature_names=("btc_close","strike","seconds_remaining"),
        input_hashes={"derived/features.parquet":"a"*64},
        plan=SimpleNamespace(
            development_groups=tuple(f"m{i}" for i in range(80)),
            calibration_groups=tuple(f"m{i}" for i in range(80,90)),
            lockbox_groups=tuple(f"m{i}" for i in range(90,100)),
            boundaries=SimpleNamespace(calibration_start_ts_ns=groups[80][1]),
        ),
    )
    plan=SimpleNamespace(
        development_groups=tuple(f"m{i}" for i in range(40)),
        calibration_groups=tuple(f"m{i}" for i in range(40,45)),
        lockbox_groups=tuple(f"m{i}" for i in range(46,55)),
        boundaries=SimpleNamespace(
            train_start_ts_ns=groups[0][1],
            train_end_ts_ns=groups[39][2],
            calibration_start_ts_ns=groups[40][1],
            calibration_end_ts_ns=groups[44][2],
            lockbox_start_ts_ns=groups[46][1],
            lockbox_end_ts_ns=groups[54][2],
        ),
    )
    override=SimpleNamespace(
        dataset_path=base.dataset_path,
        manifest_path=base.manifest_path,
        provenance_path=base.provenance_path,
        feature_names=base.feature_names,
        input_hashes=base.input_hashes,
        selected_tickers=(),
        mistakes_only=False,
        plan=plan,
    )
    return base,override,groups


def test_research_override_allows_only_preexisting_development_groups():
    base, override, groups=fixture()
    training._validate_research_context(base,override,groups)


def test_research_override_rejects_current_calibration_or_untouched_lockbox_group():
    base, override, groups=fixture()
    override.plan.lockbox_groups=tuple([*override.plan.lockbox_groups,"m97"])
    override.plan.boundaries.lockbox_end_ts_ns=groups[97][2]
    with pytest.raises(training.TrainingError,match="research|lockbox|development"):
        training._validate_research_context(base,override,groups)


def test_research_override_rejects_feature_provenance_tampering():
    base, override, groups=fixture()
    override.input_hashes={"derived/features.parquet":"b"*64}
    with pytest.raises(training.TrainingError,match="provenance|hash"):
        training._validate_research_context(base,override,groups)


def test_research_override_rejects_train_calibration_test_time_overlap():
    base, override, groups=fixture()
    override.plan.development_groups=tuple([*override.plan.development_groups,"m50"])
    override.plan.boundaries.train_end_ts_ns=groups[50][2]
    with pytest.raises(training.TrainingError,match="chronolog|overlap"):
        training._validate_research_context(base,override,groups)
