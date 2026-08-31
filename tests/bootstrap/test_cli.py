from pathlib import Path

from kalshi_edge.cli import build_parser, main


def test_cli_exposes_bootstrap_predictor_commands() -> None:
    parser = build_parser()
    backfill = parser.parse_args(["bootstrap-backfill", "--source", "all"])
    build = parser.parse_args(["bootstrap-build-dataset"])
    train = parser.parse_args(["bootstrap-train"])
    evaluate = parser.parse_args(["bootstrap-evaluate"])
    live = parser.parse_args(["predict-live"])

    assert backfill.command == "bootstrap-backfill"
    assert backfill.source == "all"
    assert build.command == "bootstrap-build-dataset"
    assert train.command == "bootstrap-train"
    assert evaluate.command == "bootstrap-evaluate"
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
