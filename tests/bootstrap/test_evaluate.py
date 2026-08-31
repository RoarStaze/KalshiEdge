from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest

from kalshi_edge.bootstrap import artifact, evaluate
from kalshi_edge.bootstrap.config import BootstrapSettings
from kalshi_edge.bootstrap.types import FeatureRow


REQUIRED_ABLATIONS = (
    "external_btc_features",
    "kalshi_flow_features",
    "structural_component",
    "residual_component",
    "xgboost_vs_lower_variance",
)


def _metric(*, brier: float, log_loss: float, ece: float = 0.05) -> artifact.MetricSnapshot:
    return artifact.MetricSnapshot(
        brier=brier,
        log_loss=log_loss,
        accuracy=0.6,
        ece=ece,
        sharpness=0.15,
    )


def _report(
    *,
    bundle_sha256: str = "a" * 64,
    candidate: artifact.MetricSnapshot | None = None,
    prior: artifact.MetricSnapshot | None = None,
    leakage_passed: bool = True,
    schema_match: bool = True,
    ablations_present: bool = True,
) -> evaluate.BootstrapEvaluationReport:
    return evaluate.BootstrapEvaluationReport(
        bundle_sha256=bundle_sha256,
        dataset_sha256="b" * 64,
        model_version="bootstrap-hybrid-v1",
        lockbox_start_ts_ns=300,
        lockbox_end_ts_ns=400,
        evaluated_rows=20,
        excluded_rows=4,
        feature_schema_match=schema_match,
        leakage_audit=artifact.LeakageEvidence(
            passed=leakage_passed,
            finding_count=0 if leakage_passed else 1,
            finding_codes=() if leakage_passed else ("FUTURE_SOURCE_TIMESTAMP",),
        ),
        required_ablations_present=ablations_present,
        metrics={
            "candidate": candidate or _metric(brier=0.20, log_loss=0.60),
            "kalshi_prior": prior or _metric(brier=0.23, log_loss=0.66),
            "structural": _metric(brier=0.22, log_loss=0.64),
            "logistic": _metric(brier=0.21, log_loss=0.63),
            "naive_50": _metric(brier=0.25, log_loss=0.693147),
        },
        comparison_rule=evaluate.PROMOTION_RULE_ID,
    )


def test_promotion_requires_conservative_lockbox_superiority_to_kalshi() -> None:
    decision = evaluate.promotion_decision(_report())
    assert decision.promoted is True
    assert decision.superior_to_kalshi is True
    assert decision.brier_improvement > 0.0
    assert decision.log_loss_improvement > 0.0

    logloss_regression = evaluate.promotion_decision(
        _report(candidate=_metric(brier=0.20, log_loss=0.70), prior=_metric(brier=0.23, log_loss=0.66))
    )
    assert logloss_regression.promoted is False
    assert "KALSHI_COMPLEMENTARY_METRIC_DEGRADED" in logloss_regression.reason_codes

    no_improvement = evaluate.promotion_decision(
        _report(candidate=_metric(brier=0.23, log_loss=0.66), prior=_metric(brier=0.23, log_loss=0.66))
    )
    assert no_improvement.promoted is False
    assert "NO_PREDECLARED_KALSHI_IMPROVEMENT" in no_improvement.reason_codes


def test_promotion_fails_closed_on_leakage_schema_ablation_or_calibration_regression() -> None:
    assert evaluate.promotion_decision(_report(leakage_passed=False)).promoted is False
    assert evaluate.promotion_decision(_report(schema_match=False)).promoted is False
    assert evaluate.promotion_decision(_report(ablations_present=False)).promoted is False

    degraded_ece = evaluate.promotion_decision(
        _report(candidate=_metric(brier=0.20, log_loss=0.60, ece=0.09), prior=_metric(brier=0.23, log_loss=0.66, ece=0.05))
    )
    assert degraded_ece.promoted is False
    assert "CALIBRATION_MATERIALLY_DEGRADED" in degraded_ece.reason_codes


def test_ablation_retains_only_later_period_value() -> None:
    retained = evaluate.ablation_decision(
        "external_btc_features",
        full_metrics=_metric(brier=0.20, log_loss=0.60),
        ablated_metrics=_metric(brier=0.22, log_loss=0.64),
    )
    assert retained.retained is True

    excluded = evaluate.ablation_decision(
        "kalshi_flow_features",
        full_metrics=_metric(brier=0.22, log_loss=0.64),
        ablated_metrics=_metric(brier=0.21, log_loss=0.63),
    )
    assert excluded.retained is False
    assert "excluded" in excluded.reason.lower()

    assert evaluate.REQUIRED_ABLATIONS == REQUIRED_ABLATIONS


def _bundle() -> artifact.ModelBundle:
    payload = b"placeholder-pipeline"
    ablations = tuple(
        artifact.AblationEvidence(
            name=name,
            full_metrics=_metric(brier=0.20, log_loss=0.60),
            ablated_metrics=_metric(brier=0.22, log_loss=0.64),
            retained=True,
            reason="later-period probability metrics improved",
        )
        for name in REQUIRED_ABLATIONS
    )
    return artifact.ModelBundle(
        stage="experiment",
        model_version="bootstrap-hybrid-v1",
        git_sha="a" * 40,
        random_seed=73115,
        feature_schema_version=1,
        feature_names=("btc_distance_bps", "kalshi_mid", "seconds_remaining"),
        boundaries=artifact.SplitBoundaries(
            train_start_ts_ns=100,
            train_end_ts_ns=200,
            calibration_start_ts_ns=201,
            calibration_end_ts_ns=250,
            lockbox_start_ts_ns=300,
            lockbox_end_ts_ns=400,
        ),
        input_hashes={"derived/features.parquet": "b" * 64},
        training_config_hash="c" * 64,
        library_versions={"python": "3.13.15"},
        components=(artifact.ComponentIdentity(name="kalshi_prior", kind="benchmark", identity="kalshi_mid", weight=1.0),),
        calibration_method="identity",
        leakage_audit=artifact.LeakageEvidence(passed=True, finding_count=0, finding_codes=()),
        metrics={"candidate": _metric(brier=0.20, log_loss=0.60), "kalshi_prior": _metric(brier=0.23, log_loss=0.66)},
        ablations=ablations,
        excluded_feature_families=(),
        excluded_components=(),
        serialized_pipeline_b64=base64.b64encode(payload).decode("ascii"),
        serialized_pipeline_sha256=hashlib.sha256(payload).hexdigest(),
    )


def _row(ts_ns: int, label: int, prior: float, ticker: str, *, seconds_remaining: float = 60.0) -> FeatureRow:
    return FeatureRow(
        market_ticker=ticker,
        market_date="2026-08-01",
        split_group_id=ticker,
        checkpoint_ts_ns=ts_ns,
        label_yes=label,
        features={"btc_distance_bps": 1.0, "kalshi_mid": prior, "seconds_remaining": seconds_remaining},
        source_max_ts_ns={"binance": ts_ns},
    )


def test_evaluate_lockbox_filters_before_prediction_and_uses_same_rows_for_benchmarks(monkeypatch) -> None:
    bundle = _bundle()
    rows = (
        _row(150, 1, 0.9, "TRAIN"),
        _row(225, 0, 0.1, "CAL"),
        _row(320, 0, 0.4, "LOCK-A"),
        _row(335, 1, 0.6, "LOCK-SUBMIN", seconds_remaining=30.0),
        _row(350, 1, 0.6, "LOCK-B"),
    )
    scored: list[str] = []

    def fake_predict(_bundle, selected):
        scored.extend(row.market_ticker for row in selected)
        return {
            "candidate": [0.2, 0.8],
            "kalshi_prior": [0.4, 0.6],
            "structural": [0.3, 0.7],
            "logistic": [0.25, 0.75],
            "naive_50": [0.5, 0.5],
        }

    monkeypatch.setattr(evaluate, "_predict_bundle_rows", fake_predict)
    report = evaluate.evaluate_lockbox(bundle, rows)

    assert scored == ["LOCK-A", "LOCK-B"]
    assert report.evaluated_rows == 2
    assert report.excluded_rows == 3
    assert report.metrics["candidate"].brier < report.metrics["kalshi_prior"].brier
    assert report.feature_schema_match is True
    assert report.leakage_audit.passed is True


def test_evaluate_lockbox_rejects_promoted_bundle_or_feature_schema_mismatch(monkeypatch) -> None:
    rows = (_row(320, 0, 0.4, "LOCK-A"), _row(350, 1, 0.6, "LOCK-B"))
    promoted = _bundle().model_copy(update={"stage": "promoted"})
    with pytest.raises(evaluate.EvaluationError, match="experiment"):
        evaluate.evaluate_lockbox(promoted, rows)

    mismatched = rows[0].model_copy(update={"features": {"kalshi_mid": 0.4, "seconds_remaining": 60.0}})
    monkeypatch.setattr(evaluate, "_predict_bundle_rows", lambda *_: {})
    with pytest.raises(evaluate.EvaluationError, match="feature schema"):
        evaluate.evaluate_lockbox(_bundle(), (mismatched, rows[1]))


def test_train_experiment_saves_only_experiment_and_report(monkeypatch, tmp_path: Path) -> None:
    settings = BootstrapSettings(bootstrap_dir=tmp_path)
    monkeypatch.setattr(evaluate, "_build_experiment_bundle", lambda *_args, **_kwargs: _bundle())

    result = evaluate.train_experiment(tmp_path, settings, git_sha="a" * 40)

    assert result.experiment_path.parent == tmp_path.resolve() / "models" / "experiments"
    assert result.report_path.parent == tmp_path.resolve() / "reports"
    assert result.experiment_bundle_sha256 == result.experiment_path.stem
    assert not (tmp_path / "models" / "default.json").exists()
    assert artifact.load_model_bundle(result.experiment_path).stage == "experiment"


def test_finalize_evaluation_promotes_only_passing_experiment_and_is_one_time(tmp_path: Path) -> None:
    experiment_path = artifact.save_model_bundle(_bundle(), tmp_path / "models" / "experiments")
    report = _report(bundle_sha256=experiment_path.stem)

    result = evaluate.finalize_evaluation(tmp_path, experiment_path, report)

    assert result.decision.promoted is True
    assert result.promoted_path is not None
    promoted = artifact.load_model_bundle(result.promoted_path)
    assert promoted.stage == "promoted"
    assert promoted.source_experiment_sha256 == experiment_path.stem
    pointer = json.loads((tmp_path / "models" / "default.json").read_text(encoding="utf-8"))
    assert pointer["bundle_sha256"] == promoted.bundle_sha256
    assert pointer["path"] == result.promoted_path.relative_to(tmp_path).as_posix()
    assert result.report_path.exists()

    with pytest.raises(evaluate.EvaluationError, match="already been evaluated"):
        evaluate.finalize_evaluation(tmp_path, experiment_path, report)


def test_failed_promotion_never_replaces_existing_default(tmp_path: Path) -> None:
    default_path = tmp_path / "models" / "default.json"
    default_path.parent.mkdir(parents=True)
    default_path.write_text('{"bundle_sha256":"old","path":"models/promoted/old.json"}\n', encoding="utf-8")
    before = default_path.read_bytes()

    experiment_path = artifact.save_model_bundle(_bundle(), tmp_path / "models" / "experiments")
    failed = _report(
        bundle_sha256=experiment_path.stem,
        candidate=_metric(brier=0.24, log_loss=0.68),
        prior=_metric(brier=0.23, log_loss=0.66),
    )
    result = evaluate.finalize_evaluation(tmp_path, experiment_path, failed)

    assert result.decision.promoted is False
    assert result.promoted_path is None
    assert default_path.read_bytes() == before
