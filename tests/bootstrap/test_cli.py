from pathlib import Path

from kalshi_edge.cli import build_parser, main


def test_cli_exposes_bootstrap_predictor_commands() -> None:
    parser = build_parser()
    backfill = parser.parse_args(["bootstrap-backfill", "--source", "all"])
    build = parser.parse_args(["bootstrap-build-dataset"])
    train = parser.parse_args(["bootstrap-train", "--git-sha", "a" * 40])
    evaluate = parser.parse_args(["bootstrap-evaluate", "--bundle", "experiment.json"])
    live = parser.parse_args(["predict-live"])

    assert backfill.command == "bootstrap-backfill"
    assert backfill.source == "all"
    assert build.command == "bootstrap-build-dataset"
    assert train.command == "bootstrap-train"
    assert train.git_sha == "a" * 40
    assert evaluate.command == "bootstrap-evaluate"
    assert evaluate.bundle == Path("experiment.json")
    assert live.command == "predict-live"


def test_existing_phase1_commands_still_parse() -> None:
    parser = build_parser()
    assert parser.parse_args(["collect"]).command == "collect"
    assert parser.parse_args(["verify-dataset", "data"]).command == "verify-dataset"


def test_dataset_build_command_executes_builder(monkeypatch, capsys) -> None:
    from kalshi_edge.bootstrap.dataset import DatasetBuildReport

    called: list[Path] = []

    def fake_build(root, settings):
        called.append(root)
        return DatasetBuildReport(
            market_count=2,
            row_count=36,
            excluded_markets=(),
            leakage_finding_count=0,
            dataset_path=Path("data/bootstrap/derived/features.parquet"),
            manifest_path=Path("data/bootstrap/manifests/dataset/features.parquet.manifest.json"),
            provenance_path=Path("data/bootstrap/derived/features.provenance.json"),
        )

    monkeypatch.setattr("kalshi_edge.bootstrap.dataset.build_dataset", fake_build)
    assert main(["bootstrap-build-dataset"]) == 0
    assert called
    output = capsys.readouterr().out
    assert '"row_count": 36' in output
    assert '"leakage_finding_count": 0' in output


def test_bootstrap_train_command_executes_real_entrypoint(monkeypatch, capsys) -> None:
    from kalshi_edge.bootstrap.evaluate import BootstrapTrainingReport

    called: list[str | None] = []

    def fake_train(root, settings, *, git_sha=None):
        called.append(git_sha)
        return BootstrapTrainingReport(
            experiment_bundle_sha256="a" * 64,
            experiment_path=Path("data/bootstrap/models/experiments/a.json"),
            report_path=Path("data/bootstrap/reports/training-a.json"),
            train_rows=100,
            calibration_rows=20,
            lockbox_rows=20,
            excluded_feature_families=("kalshi_flow_features",),
            excluded_components=("residual_component",),
        )

    monkeypatch.setattr("kalshi_edge.bootstrap.evaluate.train_experiment", fake_train)
    assert main(["bootstrap-train", "--git-sha", "a" * 40]) == 0
    assert called == ["a" * 40]
    output = capsys.readouterr().out
    assert '"experiment_bundle_sha256"' in output
    assert '"train_rows": 100' in output


def test_bootstrap_evaluate_command_returns_gate_status(monkeypatch, capsys) -> None:
    from kalshi_edge.bootstrap.evaluate import BootstrapEvaluationRun, PromotionDecision

    decision = PromotionDecision(
        promoted=False,
        superior_to_kalshi=False,
        reason_codes=("NO_PREDECLARED_KALSHI_IMPROVEMENT",),
        brier_improvement=0.0,
        log_loss_improvement=0.0,
        ece_change=0.0,
        comparison_rule="test",
    )
    run = BootstrapEvaluationRun(
        experiment_path=Path("experiment.json"),
        report_path=Path("evaluation.json"),
        decision=decision,
        promoted_path=None,
        default_path=None,
    )
    monkeypatch.setattr("kalshi_edge.bootstrap.evaluate.run_lockbox_evaluation", lambda *_args, **_kwargs: run)

    assert main(["bootstrap-evaluate", "--bundle", "experiment.json"]) == 2
    output = capsys.readouterr().out
    assert '"promoted": false' in output
