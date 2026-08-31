from kalshi_edge.cli import build_parser


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
