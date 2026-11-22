from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import date, datetime
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
    bootstrap_backfill.add_argument(
        "--since-date", type=date.fromisoformat, default=None,
        help="inclusive UTC market-open date (requires --until-date)",
    )
    bootstrap_backfill.add_argument(
        "--until-date", type=date.fromisoformat, default=None,
        help="inclusive UTC market-open date (requires --since-date)",
    )
    bootstrap_backfill.add_argument(
        "--max-markets", type=int, default=None,
        help="hard cap; when capped, select the most recent markets in the selected range",
    )
    expand = sub.add_parser(
        "bootstrap-expand-history",
        help="import maximum available historical KXBTC15M/BTC data and build verified point-in-time features; never trains or trades",
    )
    expand.add_argument(
        "--since-date", type=date.fromisoformat, default=None,
        help="inclusive UTC market-open date (requires --until-date)",
    )
    expand.add_argument(
        "--until-date", type=date.fromisoformat, default=None,
        help="inclusive UTC market-open date (requires --since-date)",
    )
    expand.add_argument(
        "--max-markets", type=int, default=None,
        help="optional cap to newest markets; omit for all discoverable historical markets",
    )
    sub.add_parser("bootstrap-build-dataset", help="build point-in-time bootstrap feature dataset")

    bootstrap_train = sub.add_parser("bootstrap-train", help="train a development-only bootstrap experiment")
    bootstrap_train.add_argument("--git-sha", default=None, help="explicit 40-character Git SHA for reproducible host runs")
    bootstrap_train.add_argument(
        "--initial-baseline", action="store_true",
        help="initial full-history bootstrap only, forbidden after a default model is promoted",
    )
    sub.add_parser(
        "mistake-pool", help="verify settled mistakes/low-confidence enrollments; no retraining or lockbox access",
    )

    volatility_upgrade = sub.add_parser(
        "volatility-upgrade",
        help="train a 240-second volatility probability challenger, independently evaluate, and promote only if superior",
    )
    volatility_upgrade.add_argument("--check", action="store_true", help="check fresh lockbox eligibility without training or evaluation")
    volatility_upgrade.add_argument("--git-sha", default=None, help="explicit Git SHA for the candidate bundle")

    bootstrap_evaluate = sub.add_parser("bootstrap-evaluate", help="evaluate the untouched lockbox and promote an eligible bootstrap model")
    bootstrap_evaluate.add_argument("--bundle", type=Path, default=None, help="experiment bundle path; defaults to latest experiment")

    app = sub.add_parser("start", help="launch unified local dashboard, live model and settlement tracking")
    app.add_argument("--no-browser", action="store_true", help="do not automatically open the browser")
    app.add_argument("--port", type=int, default=8899, help="local dashboard port (default 8899)")
    # Isolated weather research/observation commands. No exchange orders.
    sub.add_parser(
        "weather-watch",
        help="managed read-only NOAA weather collector (normally run by kalshiedge start)",
    )
    weather_forecast = sub.add_parser(
        "weather-forecast", help="inspect hourly Kalshi Weather Index target probabilities (paper only)"
    )
    weather_forecast.add_argument("--city", choices=("chicago","nyc","miami","la-coastal"))
    weather_forecast.add_argument("--asof", help="timezone-aware historical as-of timestamp for point-in-time audit")
    weather_export = sub.add_parser(
        "weather-export-training", help="join original weather snapshots to documented first-party publisher labels"
    )
    weather_export.add_argument("--station", choices=("KMDW","KORD","KDEN","KATL","KLAX"), required=True)
    weather_export.add_argument("--evidence-jsonl", type=Path, required=True)
    weather_export.add_argument("--out-jsonl", type=Path, required=True)
    weather_export.add_argument("--db", type=Path, default=None)
    weather_train = sub.add_parser(
        "weather-train", help="fit research-only station model from labeled daily JSONL with chronological holdout"
    )
    weather_train.add_argument("--station", choices=("KMDW","KORD","KDEN","KATL","KLAX"), required=True)
    weather_train.add_argument("--input-jsonl", type=Path, required=True)
    weather_train.add_argument("--model-out", type=Path, default=None)
    weather_train.add_argument("--min-days", type=int, default=60)
    weather_metars = sub.add_parser(
        "weather-collect-metars", help="read NOAA AWC observations into the WeatherEdge SQLite database"
    )
    weather_metars.add_argument("--stations", default="KMDW,KORD,KDEN,KATL,KLAX")
    weather_metars.add_argument("--db", type=Path, default=None)
    weather_metars.add_argument("--watch", action="store_true", help="poll in foreground every five minutes")
    weather_metars.add_argument("--interval-seconds", type=int, default=300)
    weather_nwp = sub.add_parser(
        "weather-collect-nwp", help="collect NOAA HRRR and NBM GRIB2 station guidance"
    )
    weather_nwp.add_argument("--db", type=Path, default=None)
    weather_nwp.add_argument("--models", default="HRRR,NBM")
    weather_nwp.add_argument("--forecast-hours", default="1,2,3,4,5,6,7,8,9,10,11,12")
    weather_nwp.add_argument("--lookback-hours", type=int, default=5)
    weather_nwp.add_argument("--watch", action="store_true")
    weather_nwp.add_argument("--interval-seconds", type=int, default=3600)
    sub.add_parser("predict-live", help="run the read-only live predictor from a promoted/default bootstrap model")
    history = sub.add_parser("final-history", help="show local final-forecast history and sync official Kalshi settlements")
    history.add_argument("--once", action="store_true", help="sync available official settlements once and exit")
    history.add_argument("--port", type=int, default=8899, help="local dashboard TCP port (default 8899)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in ("bootstrap-backfill", "bootstrap-expand-history"):
        has_since = args.since_date is not None
        has_until = args.until_date is not None
        if has_since != has_until:
            parser.error("--since-date and --until-date must be supplied together")
        if has_since and args.since_date > args.until_date:
            parser.error("--since-date cannot be later than --until-date")
        if args.max_markets is not None and args.max_markets < 1:
            parser.error("--max-markets must be a positive integer")
        if args.command == "bootstrap-backfill" and args.source == "binance" and (has_since or args.max_markets is not None):
            parser.error("date and market limits apply only when --source includes kalshi")
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
            reports["kalshi"] = backfill_kalshi(
                CollectorSettings(),
                bootstrap,
                since_date=args.since_date,
                until_date=args.until_date,
                max_markets=args.max_markets,
            ).model_dump(mode="json")
        if args.source in {"binance", "all"}:
            reports["binance"] = backfill_binance(bootstrap).model_dump(mode="json")
        print(json.dumps(reports, indent=2, sort_keys=True))
        return 0
    if args.command == "bootstrap-expand-history":
        from .bootstrap import backfill, dataset
        from .bootstrap.config import BootstrapSettings

        bootstrap = BootstrapSettings()

        def emit_progress(
            source: str, completed: int, total: int,
            item: str, phase: str, unit: str,
        ) -> None:
            print(
                "KALSHI_EDGE_PROGRESS "
                + json.dumps({
                    "source": source, "phase": phase, "completed": completed,
                    "total": total, "item": item, "unit": unit,
                }, sort_keys=True),
                flush=True,
            )

        def kalshi_progress(done: int, total: int, item: str, phase: str) -> None:
            emit_progress("kalshi", done, total, item, phase, "markets")

        def binance_progress(done: int, total: int, item: str, phase: str) -> None:
            emit_progress("binance", done, total, item, phase, "days")

        def dataset_progress(done: int, total: int, item: str, phase: str) -> None:
            emit_progress("dataset", done, total, item, phase, "markets")

        # Each stage is provenance-checked and resumable by reusing only
        # already verified artifacts. A failed stage must stop this command;
        # never build or train from incomplete source history.
        kalshi_report = backfill.backfill_kalshi(
            CollectorSettings(), bootstrap,
            since_date=args.since_date,
            until_date=args.until_date,
            max_markets=args.max_markets,
            progress_callback=kalshi_progress,
        )
        binance_report = backfill.backfill_binance(
            bootstrap, progress_callback=binance_progress,
        )
        dataset_report = dataset.build_dataset(
            bootstrap.bootstrap_dir, bootstrap,
            progress_callback=dataset_progress,
        )
        print("KALSHI_EDGE_EXPANSION_RESULT " + json.dumps({
            "status": "HISTORICAL_DATASET_BUILT",
            "kalshi": kalshi_report.model_dump(mode="json"),
            "binance": binance_report.model_dump(mode="json"),
            "dataset": dataset_report.model_dump(mode="json"),
            "models_trained": False,
            "model_promoted": False,
            "warning": (
                "Historical market features use only inputs available before each "
                "checkpoint. Historic observations cannot reproduce missing live "
                "order books or retroactively issued forecasts. Use Train Now "
                "for a separate protected training and promotion evaluation."
            ),
        }, sort_keys=True), flush=True)
        return 0

    if args.command == "bootstrap-build-dataset":
        from .bootstrap.config import BootstrapSettings
        from .bootstrap.dataset import build_dataset

        bootstrap = BootstrapSettings()
        def progress(completed: int, total: int, item: str, phase: str) -> None:
            print(
                "KALSHI_EDGE_PROGRESS "
                + json.dumps(
                    {"completed": completed, "total": total, "item": item, "unit": "markets", "phase": phase},
                    sort_keys=True,
                ),
                flush=True,
            )

        report = build_dataset(bootstrap.bootstrap_dir, bootstrap, progress_callback=progress)
        print(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True))
        return 0
    if args.command == "mistake-pool":
        from .bootstrap.config import BootstrapSettings
        from .bootstrap.mistake_learning import enroll_settled_forecasts, verified_mistake_pool

        root = BootstrapSettings().bootstrap_dir
        stats = enroll_settled_forecasts(root)
        cases = verified_mistake_pool(root)
        print(json.dumps({
            "policy": "settled-final-mistakes-and-low-confidence-v1",
            "status": "ENROLLED" if cases else "NO_ELIGIBLE_SETTLED_CASES",
            "enrollment": stats,
            "total_verified_cases": len(cases),
            "wrong_final_cases": sum(case.is_incorrect for case, _, _ in cases),
            "low_confidence_cases": sum("LOW_CONFIDENCE" in case.reasons for case, _, _ in cases),
            "cases": [
                {
                    "market_ticker": case.market_ticker,
                    "forecast": case.forecast,
                    "actual_side": case.actual_side,
                    "reasons": case.reasons,
                }
                for case, _, _ in cases
            ],
        }, indent=2))
        return 0 if stats["source_errors"] == 0 else 2
    if args.command == "bootstrap-train":
        from .bootstrap.config import BootstrapSettings
        from .bootstrap.evaluate import train_experiment

        bootstrap = BootstrapSettings()
        if args.initial_baseline and (bootstrap.bootstrap_dir / "models" / "default.json").exists():
            parser.error("--initial-baseline is forbidden when a promoted model exists")
        if args.initial_baseline:
            # Initial historical baseline is permitted once, before promotion.
            report = train_experiment(
                bootstrap.bootstrap_dir, bootstrap, git_sha=args.git_sha,
            )
        else:
            from .bootstrap.training_cycle import inspect_training_readiness
            status = inspect_training_readiness(bootstrap.bootstrap_dir)
            if not status["ready"]:
                parser.error("training cycle is not ready: " + status["reason"])
            mode = status["mode"]
            report = train_experiment(
                bootstrap.bootstrap_dir, bootstrap, git_sha=args.git_sha,
                mistakes_only=mode == "mistake",
                correct_only=mode == "correct",
            )
        print(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True))
        return 0
    if args.command == "volatility-upgrade":
        from .bootstrap.config import BootstrapSettings
        from .bootstrap.volatility_upgrade import preflight, run_upgrade
        from .bootstrap.evaluate import _resolved_git_sha

        root = BootstrapSettings().bootstrap_dir
        if args.check:
            status = preflight(root)
            print(json.dumps(status, indent=2, sort_keys=True))
            return 0 if status["ready"] else 2
        report = run_upgrade(root, git_sha=_resolved_git_sha(args.git_sha))
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["promoted"] else 2
    if args.command == "bootstrap-evaluate":
        from .bootstrap.config import BootstrapSettings
        from .bootstrap.evaluate import run_lockbox_evaluation

        bootstrap = BootstrapSettings()
        run = run_lockbox_evaluation(bootstrap.bootstrap_dir, args.bundle)
        print(json.dumps(run.model_dump(mode="json"), indent=2, sort_keys=True))
        return 0 if run.decision.promoted else 2
    if args.command == "start":
        from .launcher import start

        if not 1 <= args.port <= 65535:
            parser.error("--port must be between 1 and 65535")
        return start(port=args.port, open_browser=not args.no_browser)
    if args.command == "weather-watch":
        from .weather.runtime import main as weather_main

        return weather_main()
    if args.command == "weather-forecast":
        from .bootstrap.config import BootstrapSettings
        from .weather.hourly import read_hourly_forecasts

        asof = None
        if args.asof:
            try:
                asof = datetime.fromisoformat(args.asof.replace("Z","+00:00"))
                if asof.utcoffset() is None:
                    raise ValueError("timestamp lacks UTC offset")
            except ValueError as exc:
                parser.error(f"--asof must be timezone-aware: {exc}")
        result = read_hourly_forecasts(BootstrapSettings().bootstrap_dir,asof=asof)
        if args.city:
            result["forecasts"]=[
                item for item in result["forecasts"] if item["city"]==args.city
            ]
        print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
        return 0 if any(item.get("status")=="READY_RESEARCH"
                        for item in result["forecasts"]) else 2
    if args.command == "weather-export-training":
        from .bootstrap.config import BootstrapSettings
        from .weather.dataset import export_labeled_days
        from .weather.status import weather_database_path

        db = args.db or weather_database_path(BootstrapSettings().bootstrap_dir)
        try:
            report = export_labeled_days(
                db, args.evidence_jsonl, args.out_jsonl, args.station
            )
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            parser.error(f"weather historical export rejected: {exc}")
        print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
        return 0 if report["exported"] else 2
    if args.command == "weather-train":
        from .bootstrap.config import BootstrapSettings
        from .weather.training import train_model_file

        root = BootstrapSettings().bootstrap_dir
        destination = (args.model_out or
                       root.parent / "weather" / "models" / f"{args.station}.json")
        try:
            model = train_model_file(
                args.input_jsonl, args.station, destination, min_days=args.min_days
            )
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            parser.error(f"weather training rejected: {exc}")
        print(json.dumps({
            "model_path": str(destination), "mode": "RESEARCH_ONLY",
            "model": model,
        }, indent=2, sort_keys=True, allow_nan=False))
        return 0
    if args.command == "weather-collect-metars":
        from .bootstrap.config import BootstrapSettings
        from .weather.collection import collect_metars_once, watch_metars
        from .weather.predictor import STATIONS
        from .weather.status import weather_database_path

        stations = tuple(v.strip().upper() for v in args.stations.split(","))
        if not stations or any(x not in STATIONS for x in stations):
            parser.error("--stations must be a nonempty list of KMDW,KORD,KDEN,KATL,KLAX")
        if args.interval_seconds < 300:
            parser.error("--interval-seconds must be at least 300")
        db = args.db or weather_database_path(BootstrapSettings().bootstrap_dir)
        try:
            if args.watch:
                asyncio.run(watch_metars(
                    db, stations=stations, interval_seconds=args.interval_seconds
                ))
            else:
                result = asyncio.run(collect_metars_once(db, stations=stations))
                print(json.dumps(result, indent=2, sort_keys=True))
        except KeyboardInterrupt:
            return 130
        return 0
    if args.command == "weather-collect-nwp":
        from .bootstrap.config import BootstrapSettings
        from .weather.nwp_collection import collect_recent, watch_nwp
        from .weather.status import weather_database_path

        models = tuple(x.strip().upper() for x in args.models.split(","))
        try:
            hours = [int(x.strip()) for x in args.forecast_hours.split(",")]
        except ValueError:
            parser.error("--forecast-hours must be comma-separated integers")
        if not models or any(m not in ("HRRR","NBM") for m in models):
            parser.error("--models must be HRRR and/or NBM")
        if not hours or len(set(hours)) != len(hours) or any(not 1 <= h <= 48 for h in hours):
            parser.error("--forecast-hours must be unique integers between 1 and 48")
        if not 1 <= args.lookback_hours <= 24:
            parser.error("--lookback-hours must be 1..24")
        if args.watch and args.interval_seconds < 900:
            parser.error("--interval-seconds must be at least 900")
        db = args.db or weather_database_path(BootstrapSettings().bootstrap_dir)
        try:
            if args.watch:
                asyncio.run(watch_nwp(db, interval_seconds=args.interval_seconds))
            else:
                report = asyncio.run(collect_recent(
                    db, models=models, forecast_hours=hours,
                    lookback_hours=args.lookback_hours,
                ))
                print(json.dumps(report, indent=2, sort_keys=True))
                return 0 if all(x.get("mode") == "DATA_ONLY" for x in report.values()) else 2
        except (ImportError, OSError) as exc:
            parser.error("NOAA GRIB decoding requires optional weather dependencies: "
                         f"{exc}. Install with pip install -e '.[weather]'")
        except KeyboardInterrupt:
            return 130
        return 0
    if args.command == "predict-live":
        from .bootstrap.live import run_live

        return run_live()
    if args.command == "final-history":
        from .bootstrap.config import BootstrapSettings
        from .bootstrap.final_history import run_final_history

        if not 1 <= args.port <= 65535:
            parser.error("--port must be between 1 and 65535")
        return run_final_history(
            BootstrapSettings().bootstrap_dir,
            CollectorSettings().rest_base_url,
            port=args.port,
            once=args.once,
        )
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
