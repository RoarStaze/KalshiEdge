"""Collect SiddhaBot's *original* XGBoost probabilities at the 480s checkpoint.

Run with SiddhaBot's isolated Python environment while its unmodified
read-only/paper-disabled data service is active. This file imports upstream
feature creation and calibration; it never imports or calls order placement.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

NS = 1_000_000_000
CHECKPOINT_NS = 480 * NS
MAX_OFFSET_NS = 5 * NS
POLICY = "siddhabot-prospective-480-v1"
TICKER = re.compile(r"^KXBTC15M-[A-Z0-9-]+$")


def _setup_upstream(repo: Path) -> tuple[object, object, object]:
    if not (repo / "backend" / "core" / "btc_paper_trading.py").is_file():
        raise RuntimeError("SiddhaBot checkout missing: run installer first")
    # A non-trading benchmark must override even a user's existing live config.
    os.environ.update({
        "SIMULATION_MODE": "true",
        "BTC_LIVE_TRADING_ENABLED": "false",
        "BTC_PAPER_TRADING_ENABLED": "false",
        "WEATHER_BOT_ENABLED": "false",
        "WHALE_SCANNER_ENABLED": "false",
    })
    os.environ.setdefault(
        "DATABASE_URL", "sqlite:///" + (repo / "siddhabot_benchmark.sqlite3").as_posix()
    )
    os.chdir(repo)
    sys.path.insert(0, str(repo))
    import numpy as np
    from backend.core import btc_paper_trading as paper
    from backend.core.btc_model_training import (
        FEATURE_COLUMNS, apply_calibration, apply_live_recalibration,
    )
    return np, paper, (FEATURE_COLUMNS, apply_calibration, apply_live_recalibration)


async def _sample(root: Path, repo: Path, commit: str, np: object,
                  paper: object, inference: tuple) -> str | None:
    started = time.time_ns()
    ctx = await paper._build_live_features()
    finished = time.time_ns()
    if not isinstance(ctx, dict):
        return None
    ticker = ctx.get("ticker")
    close_time = ctx.get("close_time")
    if (not isinstance(ticker, str) or not TICKER.fullmatch(ticker)
            or not isinstance(close_time, datetime)):
        return None
    if close_time.tzinfo is None:
        close_time = close_time.replace(tzinfo=timezone.utc)
    close_ns = int(close_time.timestamp() * NS)
    if (started > finished or finished >= close_ns
            or abs(close_ns - started - CHECKPOINT_NS) > MAX_OFFSET_NS
            or abs(close_ns - finished - CHECKPOINT_NS) > MAX_OFFSET_NS):
        return None
    model = paper._load_model()
    if model is None:
        return None
    columns, apply_calibration, apply_live_recalibration = inference
    features = ctx.get("features")
    if not isinstance(features, dict):
        return None
    values = []
    for name in columns:
        value = features.get(name)
        if type(value) not in (float, int) or not math.isfinite(float(value)):
            return None
        values.append(float(value))
    raw = float(model.predict_proba(np.array([values]))[:, 1][0])
    p_yes = float(apply_live_recalibration(apply_calibration(raw)))
    if not math.isfinite(p_yes) or not 0 <= p_yes <= 1:
        return None
    artifact = repo / "backend" / "btcmarket" / "artifacts" / "kxbtc15m_xgb.json"
    model_sha256 = hashlib.sha256(artifact.read_bytes()).hexdigest()
    day = datetime.fromtimestamp(close_ns / NS, timezone.utc).date().isoformat()
    directory = root / "benchmarks" / "siddhabot" / "predictions" / day
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{ticker}.json"
    payload = {
        "benchmark_version": POLICY,
        "ticker": ticker,
        "close_ts_ns": close_ns,
        "started_ts_ns": started,
        "finished_ts_ns": finished,
        "p_yes": p_yes,
        "model_sha256": model_sha256,
        "source_commit": commit,
    }
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"), allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        return None  # immutable first valid checkpoint only
    # Start benchmark period at the first successfully saved concurrent input.
    marker = root / "benchmarks" / "siddhabot" / "capture-start.json"
    try:
        with marker.open("x", encoding="utf-8") as stream:
            json.dump({"started_ts_ns": max(1, started - MAX_OFFSET_NS)}, stream)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        pass
    return ticker


async def _capture_loop(root: Path, repo: Path, commit: str, np: object,
                        paper: object, inference: tuple) -> None:
    last_error = None
    while True:
        # Keep this recorder in the SAME Python process as upstream's
        # scheduler. _build_live_features() reads its in-memory 1Hz Coinbase
        # TWAP tick buffer, which is NOT shared across separate processes.
        phase = time.time() % 900
        if abs(phase - 420.0) > 5:
            await asyncio.sleep(0.5)
            continue
        try:
            ticker = await _sample(root, repo, commit, np, paper, inference)
            if ticker:
                print("Captured original SiddhaBot probability for", ticker, flush=True)
                last_error = None
                await asyncio.sleep(10)  # never overwrite a market
        except Exception as exc:
            error = type(exc).__name__ + ": " + str(exc)
            if error != last_error:
                print("Capture unavailable:", error[:220], file=sys.stderr, flush=True)
                last_error = error
        await asyncio.sleep(0.5)


def main() -> int:
    parser = argparse.ArgumentParser(description="SiddhaBot source service + 480s recorder, same process")
    parser.add_argument("--root", type=Path, required=True, help="KalshiEdge data/bootstrap root")
    parser.add_argument("--repo", type=Path, required=True, help="Pinned SiddhaBot checkout")
    args = parser.parse_args()
    root = args.root.resolve()
    repo = args.repo.resolve()
    np, paper, inference = _setup_upstream(repo)
    commit = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    print("SiddhaBot native source + 480s capture (NO ORDERS):", commit, flush=True)
    print("The data scheduler and recorder share the original live 1Hz price buffer.", flush=True)

    # Upstream registers its own data/scheduler startup before this callback.
    # Neither original upstream modules nor its trading configuration are changed.
    from backend.api.main import app
    import uvicorn

    @app.on_event("startup")
    async def _start_siddha_recorder() -> None:
        app.state.siddha_benchmark_task = asyncio.create_task(
            _capture_loop(root, repo, commit, np, paper, inference)
        )

    @app.on_event("shutdown")
    async def _stop_siddha_recorder() -> None:
        task = getattr(app.state, "siddha_benchmark_task", None)
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    uvicorn.run(app, host="127.0.0.1", port=8001)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
