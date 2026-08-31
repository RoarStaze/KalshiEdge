from kalshi_edge.cli import build_parser


def test_cli_exposes_phase1_commands() -> None:
    parser = build_parser()
    assert parser.parse_args(["collect"]).command == "collect"
    assert parser.parse_args(["verify-dataset", "data"]).command == "verify-dataset"
    args = parser.parse_args(["replay", "segment.jsonl", "KXBTC15M-X"])
    assert args.market_ticker == "KXBTC15M-X"


def test_cli_exposes_dataset_replay_and_phase1_gate_report() -> None:
    parser = build_parser()
    replay = parser.parse_args(["replay-dataset", "data", "KXBTC15M-X"])
    gate = parser.parse_args(["phase1-report", "data"])
    assert replay.command == "replay-dataset"
    assert gate.command == "phase1-report"
