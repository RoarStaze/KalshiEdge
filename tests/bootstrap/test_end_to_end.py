from __future__ import annotations

import inspect
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kalshi_edge import cli
from kalshi_edge.bootstrap import artifact, evaluate, live
from kalshi_edge.bootstrap.binance_history import BinanceBar, convert_spot_1s_to_parquet
from kalshi_edge.bootstrap.config import BootstrapSettings
from kalshi_edge.bootstrap.dataset import build_dataset
from kalshi_edge.bootstrap.live_kalshi import BRTIObservation, LiveMarket, LiveQuote, LiveTrade
from kalshi_edge.bootstrap.provenance import RawArtifact, sha256_file, write_manifest, write_raw_artifact


NS = 1_000_000_000
BASE = datetime(2026, 8, 1, 0, 15, tzinfo=timezone.utc)
MARKET_COUNT = 12


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _market_prior(label_yes: bool) -> float:
    # Deliberately wrong Kalshi prior so a causal BTC-derived candidate has a
    # clear synthetic lockbox opportunity without changing production gates.
    return 0.25 if label_yes else 0.75


def _seed_kalshi(root: Path) -> None:
    for index in range(MARKET_COUNT):
        opened = BASE + timedelta(minutes=15 * index)
        closed = opened + timedelta(minutes=15)
        label_yes = index % 2 == 0
        ticker = f"KXBTC15M-E2E-{index:02d}"
        prior = _market_prior(label_yes)
        market = {
            "market": {
                "ticker": ticker,
                "event_ticker": f"KXBTC15M-E2E-EVENT-{index:02d}",
                "market_type": "binary",
                "open_time": _iso(opened),
                "close_time": _iso(closed),
                "settlement_ts": _iso(closed + timedelta(minutes=1)),
                "status": "finalized",
                "result": "yes" if label_yes else "no",
                "settlement_value_dollars": "101.0" if label_yes else "99.0",
                "strike_type": "greater",
                "floor_strike": 100.0,
                "rules_primary": "Resolves Yes when the final BRTI average is at least the strike.",
                "rules_secondary": "CF Benchmarks BRTI",
                "is_provisional": False,
            }
        }
        trades = {
            "ticker": ticker,
            "trades": [
                {
                    "created_time": _iso(opened + timedelta(seconds=30)),
                    "yes_price_dollars": f"{prior:.2f}",
                    "count_fp": "10.0",
                    "taker_side": "yes" if index % 3 else "no",
                }
            ],
        }
        candles = {
            "ticker": ticker,
            "candlesticks": [
                {
                    "end_period_ts": int((opened + timedelta(minutes=minute)).timestamp()),
                    "yes_bid": {"close": f"{max(0.0, prior - 0.01):.2f}"},
                    "yes_ask": {"close": f"{min(1.0, prior + 0.01):.2f}"},
                    "price": {
                        "close": f"{prior:.2f}",
                        "high": f"{min(1.0, prior + 0.02):.2f}",
                        "low": f"{max(0.0, prior - 0.02):.2f}",
                    },
                    "volume": "10.0",
                    "open_interest": "5.0",
                }
                for minute in range(1, 16)
            ],
        }
        for logical_name, payload in (
            (f"markets/{ticker}.json", market),
            (f"trades/{ticker}.json", trades),
            (f"candlesticks/{ticker}.json", candles),
        ):
            write_raw_artifact(
                root=root,
                source="kalshi",
                logical_name=logical_name,
                content=(json.dumps(payload, sort_keys=True) + "\n").encode("utf-8"),
                metadata={"source_locator": f"synthetic-official-shaped:{logical_name}"},
            )


def _synthetic_price(timestamp: datetime) -> float:
    if timestamp < BASE:
        return 100.0
    index = min(MARKET_COUNT - 1, int((timestamp - BASE).total_seconds() // (15 * 60)))
    return 101.0 if index % 2 == 0 else 99.0


def _seed_binance(root: Path) -> None:
    start = BASE - timedelta(minutes=15)
    end = BASE + timedelta(minutes=15 * MARKET_COUNT)
    rows = [
        "open_time,open,high,low,close,volume,close_time,quote_volume,trades,taker_buy_base,taker_buy_quote,ignore"
    ]
    seconds = int((end - start).total_seconds())
    for offset in range(seconds + 1):
        timestamp = start + timedelta(seconds=offset)
        price = _synthetic_price(timestamp)
        open_us = int(timestamp.timestamp()) * 1_000_000
        rows.append(
            f"{open_us},{price:.4f},{price + 0.01:.4f},{price - 0.01:.4f},{price:.4f},1.0,"
            f"{open_us + 999999},100.0,1,0.6,60.0,0"
        )

    buffer = io.BytesIO()
    archive_name = "BTCUSDT-1s-2026-08-01.zip"
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("BTCUSDT-1s-2026-08-01.csv", "\n".join(rows) + "\n")
    raw = write_raw_artifact(
        root=root,
        source="binance",
        logical_name=f"archive/{archive_name}",
        content=buffer.getvalue(),
        metadata={"source_locator": "https://data.binance.vision/synthetic-official-shaped"},
    )

    normalized = root / "normalized" / "binance" / "1s" / "BTCUSDT-1s-2026-08-01.parquet"
    row_count = convert_spot_1s_to_parquet(root / raw.path, normalized, batch_size=2048)
    normalized_artifact = RawArtifact(
        path=normalized.relative_to(root),
        manifest_path=Path("manifests/binance_normalized/1s/BTCUSDT-1s-2026-08-01.parquet.manifest.json"),
        sha256=sha256_file(normalized),
        source="binance_normalized",
        retrieval_ts_utc="2026-08-31T00:00:00+00:00",
        byte_count=normalized.stat().st_size,
        metadata={
            "source_raw_path": raw.path.as_posix(),
            "source_raw_sha256": raw.sha256,
            "row_count": row_count,
        },
    )
    write_manifest(root, normalized_artifact)


def _live_state(now_ns: int) -> live.LiveFeatureState:
    state = live.LiveFeatureState()
    market_open = now_ns - 13 * 60 * NS
    market_close = now_ns + 2 * 60 * NS
    ticker = "KXBTC15M-E2E-LIVE"
    state.update_market(
        LiveMarket(
            ticker=ticker,
            strike=100.0,
            open_ts_ns=market_open,
            close_ts_ns=market_close,
            open_interest=5.0,
        )
    )
    state.update_kalshi(LiveQuote(market_ticker=ticker, source_ts_ns=now_ns, yes_bid=0.49, yes_ask=0.51))
    state.update_kalshi(
        LiveTrade(
            market_ticker=ticker,
            source_ts_ns=now_ns - NS,
            yes_price=0.50,
            count=1.0,
            taker_side="yes",
        )
    )
    for offset in range(900, -1, -1):
        ts_ns = now_ns - offset * NS
        state.update_binance(
            BinanceBar(
                ts_ns=ts_ns,
                open=101.0,
                high=101.01,
                low=100.99,
                close=101.0,
                base_volume=1.0,
                quote_volume=100.0,
                trade_count=1,
                taker_buy_base=0.6,
                taker_buy_quote=60.0,
            )
        )
    state.update_brti(BRTIObservation(source_ts_ns=now_ns - NS, value=101.0))
    return state


def test_bootstrap_end_to_end_build_train_promote_and_predict(tmp_path: Path) -> None:
    root = tmp_path / "data" / "bootstrap"
    phase1_raw = tmp_path / "data" / "raw"
    settings = BootstrapSettings(bootstrap_dir=root)

    _seed_kalshi(root)
    _seed_binance(root)
    assert not phase1_raw.exists()

    dataset = build_dataset(root, settings)
    assert dataset.market_count == MARKET_COUNT
    assert dataset.row_count == MARKET_COUNT * len(settings.checkpoint_seconds)
    assert dataset.leakage_finding_count == 0
    assert not phase1_raw.exists()

    training_run = evaluate.train_experiment(root, settings, git_sha="f" * 40)
    experiment = artifact.load_model_bundle(training_run.experiment_path)
    assert experiment.stage == "experiment"
    assert experiment.bundle_sha256 == training_run.experiment_bundle_sha256
    assert artifact.bundle_sha256(experiment) == experiment.bundle_sha256
    assert not (root / "models" / "default.json").exists()

    evaluation = evaluate.run_lockbox_evaluation(root, training_run.experiment_path)
    assert evaluation.decision.promoted is True, evaluation.decision.model_dump()
    assert evaluation.promoted_path is not None
    assert evaluation.default_path == root / "models" / "default.json"

    promoted, pipeline = live.load_default_model(root)
    assert promoted.stage == "promoted"
    assert promoted.bundle_sha256 == artifact.bundle_sha256(promoted)
    assert tuple(promoted.feature_names) == tuple(pipeline.required_feature_names)

    # Direct use of an experiment bundle remains rejected even after a default exists.
    try:
        live.LivePredictor(state=live.LiveFeatureState(), bundle=experiment, pipeline=pipeline, settings=settings)
    except live.LiveModelError:
        pass
    else:
        raise AssertionError("experiment bundle was accepted for live inference")

    now_ns = int((BASE + timedelta(hours=4)).timestamp() * NS)
    state = _live_state(now_ns)
    predictor = live.LivePredictor(state=state, bundle=promoted, pipeline=pipeline, settings=settings)
    record = predictor.predict(now_ns)
    assert record.status == "OK", record.model_dump()
    assert record.predicted_side in {"ABOVE", "BELOW"}
    assert record.model_hash == promoted.bundle_sha256
    assert record.feed_quality.healthy is True

    writer = live.PredictionJsonlWriter(root)
    prediction_path = writer.append(record)
    assert prediction_path.is_relative_to(root / "predictions")
    assert not phase1_raw.exists()

    stale = predictor.predict(now_ns + 10 * NS)
    assert stale.status == "NO_PREDICTION"
    assert stale.reason in {"STALE_KALSHI_BOOK", "STALE_BINANCE", "STALE_BRTI"}

    import kalshi_edge.bootstrap.live as live_module
    import kalshi_edge.bootstrap.live_kalshi as live_kalshi_module

    combined_source = inspect.getsource(live_module) + inspect.getsource(live_kalshi_module)
    assert "RawSegmentWriter" not in combined_source
    assert "KalshiCollector" not in combined_source
    for obj in (predictor, live_kalshi_module.KalshiLiveFeed):
        for forbidden in ("create_order", "cancel_order", "place_order", "portfolio"):
            assert not hasattr(obj, forbidden)


def test_task10_cli_docs_and_ci_contract() -> None:
    parser = cli.build_parser()
    cases = (
        ["bootstrap-backfill", "--source", "kalshi"],
        ["bootstrap-backfill", "--source", "binance"],
        ["bootstrap-backfill", "--source", "all"],
        ["bootstrap-build-dataset"],
        ["bootstrap-train"],
        ["bootstrap-evaluate"],
        ["predict-live"],
    )
    for argv in cases:
        assert parser.parse_args(argv).command == argv[0]

    repo_root = Path(__file__).resolve().parents[2]
    runbook = repo_root / "docs" / "RUNBOOK_BOOTSTRAP_PREDICTOR.md"
    assert runbook.exists(), "Task 10 runbook is required"
    runbook_text = runbook.read_text(encoding="utf-8")
    for required in (
        "bootstrap-backfill --source all",
        "bootstrap-build-dataset",
        "bootstrap-train",
        "bootstrap-evaluate",
        "predict-live",
        "KalshiEdge Phase1 Collector",
        "READ_ONLY",
        "no order placement",
    ):
        assert required in runbook_text

    readme = (repo_root / "README.md").read_text(encoding="utf-8")
    assert "RUNBOOK_BOOTSTRAP_PREDICTOR.md" in readme
    assert "bootstrap predictor" in readme.lower()

    project_control = (repo_root / "docs" / "PROJECT_CONTROL.md").read_text(encoding="utf-8")
    assert "bootstrap predictor" in project_control.lower()
    assert "no live-trading" in project_control.lower()
    assert "gate" in project_control.lower() and "not passed" in project_control.lower()

    workflow = (repo_root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "python -m compileall -q src tests" in workflow
