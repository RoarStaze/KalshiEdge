from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path

from .collector import KalshiCollector
from .config import CollectorSettings
from .replay import replay_dataset, replay_orderbook
from .storage import verify_segment
from .validation import evaluate_phase1_gate, verify_dataset


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kalshi-edge", description="KXBTC15M empirical data and prediction engine")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("collect", help="run the read-only Kalshi/BRTI collector")

    verify = sub.add_parser("verify-dataset", help="verify raw segment hashes and sequence integrity")
    verify.add_argument("data_dir", type=Path)

    gate = sub.add_parser("phase1-report", help="evaluate machine-checkable Phase 1 gate evidence")
    gate.add_argument("data_dir", type=Path)

    segment = sub.add_parser("verify-segment", help="verify one immutable raw segment")
    segment.add_argument("data_path", type=Path)
    segment.add_argument("hash_path", type=Path)

    replay = sub.add_parser("replay", help="deterministically replay one market from one raw segment")
    replay.add_argument("data_path", type=Path)
    replay.add_argument("market_ticker")

    replay_all = sub.add_parser("replay-dataset", help="deterministically replay one market across all WS segments")
    replay_all.add_argument("data_dir", type=Path)
    replay_all.add_argument("market_ticker")

    bootstrap_backfill = sub.add_parser("bootstrap-backfill", help="backfill bootstrap historical data")
    bootstrap_backfill.add_argument("--source", choices=("kalshi", "binance", "all"), default="all")
    sub.add_parser("bootstrap-build-dataset", help="build point-in-time bootstrap feature dataset")

    bootstrap_train = sub.add_parser("bootstrap-train", help="train bootstrap probability models")
    bootstrap_train.add_argument("--git-sha", default=None, help="explicit 40-character Git SHA for reproducible host runs")

    bootstrap_evaluate = sub.add_parser("bootstrap-evaluate", help="evaluate bootstrap models")
    bootstrap_evaluate.add_argument("--bundle", type=Path, default=None, help="experiment bundle path; defaults to latest experiment")

    sub.add_parser("predict-live", help="run the read-only live bootstrap predictor")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.command == "collect":
        asyncio.run(KalshiCollector(CollectorSettings()).run_forever())
        return 0
    if args.command == "verify-dataset":
        result = verify_dataset(args.data_dir)
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return 0 if result.passed else 2
    if args.command == "phase1-report":
        report = evaluate_phase1_gate(args.data_dir)
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        return 0 if report.machine_gate_passed else 2
    if args.command == "verify-segment":
        ok = verify_segment(args.data_path, args.hash_path)
        print(json.dumps({"passed": ok, "data_path": str(args.data_path)}, sort_keys=True))
        return 0 if ok else 2
    if args.command == "replay":
        result = replay_orderbook(args.data_path, args.market_ticker)
        print(json.dumps(result.__dict__, indent=2, sort_keys=True))
        return 0
    if args.command == "replay-dataset":
        result = replay_dataset(args.data_dir, args.market_ticker)
        print(json.dumps(result.__dict__, indent=2, sort_keys=True))
        return 0
    if args.command == "bootstrap-backfill":
        from .bootstrap.backfill import backfill_binance, backfill_kalshi
        from .bootstrap.config import BootstrapSettings

        bootstrap = BootstrapSettings()
        reports: dict[str, object] = {}
        if args.source in {"kalshi", "all"}:
            reports["kalshi"] = backfill_kalshi(CollectorSettings(), bootstrap).model_dump(mode="json")
        if args.source in {"binance", "all"}:
            reports["binance"] = backfill_binance(bootstrap).model_dump(mode="json")
        print(json.dumps(reports, indent=2, sort_keys=True))
        return 0
    if args.command == "bootstrap-build-dataset":
        from .bootstrap.config import BootstrapSettings
        from .bootstrap.dataset import build_dataset

        bootstrap = BootstrapSettings()
        report = build_dataset(bootstrap.bootstrap_dir, bootstrap)
        print(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True))
        return 0
    if args.command == "bootstrap-train":
        from .bootstrap.config import BootstrapSettings
        from .bootstrap.evaluate import train_experiment

        bootstrap = BootstrapSettings()
        report = train_experiment(bootstrap.bootstrap_dir, bootstrap, git_sha=args.git_sha)
        print(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True))
        return 0
    if args.command == "bootstrap-evaluate":
        from .bootstrap.config import BootstrapSettings
        from .bootstrap.evaluate import run_lockbox_evaluation

        bootstrap = BootstrapSettings()
        run = run_lockbox_evaluation(bootstrap.bootstrap_dir, args.bundle)
        print(json.dumps(run.model_dump(mode="json"), indent=2, sort_keys=True))
        return 0 if run.decision.promoted else 2
    raise AssertionError("live bootstrap prediction execution is wired by Task 9")


if __name__ == "__main__":
    raise SystemExit(main())
