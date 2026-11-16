"""Read-only *proxy-input* historical replay: KalshiEdge versus frozen SiddhaBot XGBoost.

KalshiEdge has Binance BTCUSDT 1s history, NOT SiddhaBot's original Coinbase
BTC-USD snapshots/quote-age time series. This comparison substitutes verified
Binance bars and explicitly labels two unavailable features as zero proxies.
It is a useful descriptive comparison, NOT an exact native SiddhaBot replay,
out-of-sample KalshiEdge retraining test, or deployment promotion.
"""
from __future__ import annotations

import argparse
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import tempfile
from typing import Any, Sequence

from .siddhabot_comparison import _score, _locked_score

NS = 1_000_000_000
MINUTE_NS = 60 * NS
HOUR_NS = 3600 * NS
LOOKBACK_NS = 4 * HOUR_NS
MODEL_TRAINED_AT = datetime(2026, 9, 11, 21, 49, 52, tzinfo=timezone.utc)
PINNED_UPSTREAM = "00f6c52b74aa17dcb69971b4c9480db7d173bfcc"
ARTIFACT_BLOBS = {
    "kxbtc15m_xgb.json": "f28cee583232df7c8c8d5cd0ace89ba83d556e33",
    "calibration.json": "a49ec3af47036a490151f97a2d5f938bc916ae85",
}
# Original SiddhaBot's 23-column XGBoost model feature ordering.
SIDDHA_COLUMNS = (
    "seconds_remaining", "elapsed_fraction", "distance_log", "yes_mid", "spread",
    "gbm_prob", "realized_vol_per_second", "m1_ret_1m", "m1_ret_5m",
    "m1_vol_sum_5m", "m1_vol_sum_15m", "h1_log_return", "h1_range_pct",
    "quote_age_seconds", "btc_move_since_quote", "path_efficiency_15m",
    "hour_sin", "hour_cos", "rsi_14", "macd_histogram_pct", "bollinger_pct_b",
    "vwap_deviation_60m", "settlement_bias",
)


class HistoricalBenchmarkError(ValueError):
    pass


def git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(("blob " + str(len(data)) + "\0").encode("ascii") + data).hexdigest()


def _checked_artifact(root: Path, name: str) -> bytes:
    """Retrieve immutable original XGB artifact, verify Git's exact blob SHA1."""
    if name not in ARTIFACT_BLOBS:
        raise HistoricalBenchmarkError("unknown SiddhaBot artifact")
    destination = root / "benchmarks" / "siddhabot" / "artifacts" / name
    existing = root / "benchmarks" / "siddhabot" / "repo" / "backend" / "btcmarket" / "artifacts" / name
    for path in (destination, existing):
        if path.is_file():
            content = path.read_bytes()
            if git_blob_sha1(content) == ARTIFACT_BLOBS[name]:
                return content
            if path == destination:
                raise HistoricalBenchmarkError("cached SiddhaBot artifact SHA is incorrect")
    import httpx

    url = (
        "https://raw.githubusercontent.com/SiddhaBasu/kalshi-bot/"
        + PINNED_UPSTREAM + "/backend/btcmarket/artifacts/" + name
    )
    try:
        with httpx.Client(timeout=35, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
            data = response.content
    except (httpx.HTTPError, OSError) as exc:
        raise HistoricalBenchmarkError("cannot download original SiddhaBot artifact: " + str(exc)) from exc
    if len(data) > 250_000 or git_blob_sha1(data) != ARTIFACT_BLOBS[name]:
        raise HistoricalBenchmarkError("SiddhaBot immutable artifact verification failed")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as handle:
        handle.write(data)
        temporary = Path(handle.name)
    try:
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return data


def trailing_settlement_bias(previous: Sequence[tuple[int, int]], checkpoint_ns: int) -> float | None:
    """Last 20 previously *closed* verified settlements; 60s latency assumption."""
    eligible = [(closed, y) for closed, y in previous
                if closed + MINUTE_NS <= checkpoint_ns and y in (0, 1)]
    eligible.sort(key=lambda value: value[0])
    recent = eligible[-20:]
    return sum(y for _, y in recent) / len(recent) if len(recent) >= 5 else None


def build_proxy_features(final: Any, ticks: Any, *, settlement_bias: float | None) -> dict[str, float] | None:
    """Calculate frozen model's 23 features from checkpoint-causal data only.

    Requires four complete hours of contiguous Binance 1s bars, 181 full
    finished minutes, and two finished hours. Never uses a future candle.
    """
    import numpy as np

    checkpoint = int(final.prediction_ts_ns)
    close_ns = int(final.scheduled_ts_ns) + int(final.checkpoint_seconds) * NS
    if (final.checkpoint_seconds != 480
            or not 0 <= checkpoint - int(final.scheduled_ts_ns) <= 2 * NS
            or close_ns - checkpoint < 478 * NS):
        return None
    if (settlement_bias is None or not math.isfinite(settlement_bias)
            or not 0 <= settlement_bias <= 1):
        return None
    bid = getattr(final, "kalshi_yes_bid", None)
    ask = getattr(final, "kalshi_yes_ask", None)
    strike = getattr(final, "strike", None)
    if (not all(type(v) in (int, float) and math.isfinite(v) for v in (bid, ask, strike))
            or not 0 < bid <= ask <= 1 or strike <= 0):
        return None
    if not {"ts_ns", "close", "high", "low", "base_volume"}.issubset(ticks.columns):
        return None
    # Archived timestamps align on integer seconds; live records have ns.
    anchor = (checkpoint // NS) * NS
    prior = ticks.loc[
        (ticks["ts_ns"] >= anchor - LOOKBACK_NS)
        & (ticks["ts_ns"] + NS <= anchor)
    ].sort_values("ts_ns")
    if len(prior) != LOOKBACK_NS // NS:
        return None
    ts = prior["ts_ns"].to_numpy(dtype=np.int64)
    if (ts[0] != anchor - LOOKBACK_NS or ts[-1] + NS != anchor
            or np.any(np.diff(ts) != NS)):
        return None
    raw = prior[["close", "high", "low", "base_volume"]].to_numpy(dtype=float)
    if not np.isfinite(raw).all() or np.any(raw[:, 0] <= 0) or np.any(raw[:, 3] < 0):
        return None
    spot = float(prior["close"].iloc[-60:].mean())
    seconds_remaining = (close_ns - checkpoint) / NS
    elapsed = 1.0 - seconds_remaining / 900.0

    minutes = prior.groupby((prior["ts_ns"] // MINUTE_NS).to_numpy(), sort=True).agg(
        close=("close", "last"), volume=("base_volume", "sum"), n=("close", "size"),
    )
    latest = checkpoint // MINUTE_NS - 1
    selected = minutes.reindex(np.arange(latest - 180, latest + 1))
    if selected.isna().any().any() or not (selected["n"] == 60).all():
        return None
    close = selected["close"].astype(float)
    volume = selected["volume"].astype(float)
    returns = np.log(close / close.shift(1)).dropna()
    if len(returns) != 180:
        return None
    sigma = float(returns.std(ddof=1) / math.sqrt(60))
    if not math.isfinite(sigma) or sigma <= 0:
        return None
    sigma_h = sigma * math.sqrt(seconds_remaining)
    z = (math.log(spot / strike) - .5 * sigma_h ** 2) / sigma_h
    gbm = min(.99, max(.01, .5 * (1 + math.erf(z / math.sqrt(2)))))
    movement = float(returns.iloc[-15:].abs().sum())
    path = (float(abs(math.log(close.iloc[-1] / close.iloc[-16]))) / movement
            if movement > 0 else 0.0)

    hours = prior.groupby((prior["ts_ns"] // HOUR_NS).to_numpy(), sort=True).agg(
        close=("close", "last"), high=("high", "max"), low=("low", "min"),
        n=("close", "size"),
    )
    last_hour = checkpoint // HOUR_NS - 1
    hour_pair = hours.reindex([last_hour - 1, last_hour])
    if hour_pair.isna().any().any() or not (hour_pair["n"] == 3600).all():
        return None
    h1_return = math.log(float(hour_pair["close"].iloc[-1]) / float(hour_pair["close"].iloc[-2]))
    h1_range = (
        float(hour_pair["high"].iloc[-1]) - float(hour_pair["low"].iloc[-1])
    ) / float(hour_pair["close"].iloc[-1])

    delta = close.diff()
    gains, losses = delta.clip(lower=0), (-delta).clip(lower=0)
    gain = gains.rolling(14, min_periods=14).mean().iloc[-1]
    loss = losses.rolling(14, min_periods=14).mean().iloc[-1]
    rsi = (100.0 if gain > 0 else 50.0) if loss == 0 else 100 - 100 / (1 + gain/loss)
    ema12 = close.ewm(span=12, adjust=False, min_periods=12).mean()
    ema26 = close.ewm(span=26, adjust=False, min_periods=26).mean()
    line = ema12 - ema26
    signal = line.ewm(span=9, adjust=False, min_periods=9).mean()
    macd = float(((line - signal) / close).iloc[-1])
    center = close.iloc[-20:].mean()
    std = close.iloc[-20:].std(ddof=1)
    if std <= 0:
        return None
    bollinger = float((close.iloc[-1] - center + 2*std) / (4*std))
    last60volume = float(volume.iloc[-60:].sum())
    if last60volume <= 0:
        return None
    vwap = float((close.iloc[-60:] * volume.iloc[-60:]).sum() / last60volume)
    dt = datetime.fromtimestamp(checkpoint / NS, timezone.utc)
    hour_fraction = dt.hour + dt.minute / 60
    features = {
        "seconds_remaining": seconds_remaining, "elapsed_fraction": elapsed,
        "distance_log": math.log(spot / strike), "yes_mid": (bid+ask)/2,
        "spread": ask-bid, "gbm_prob": gbm, "realized_vol_per_second": sigma,
        "m1_ret_1m": float(math.log(close.iloc[-1]/close.iloc[-2])),
        "m1_ret_5m": float(math.log(close.iloc[-1]/close.iloc[-6])),
        "m1_vol_sum_5m": float(volume.iloc[-5:].sum()),
        "m1_vol_sum_15m": float(volume.iloc[-15:].sum()),
        "h1_log_return": h1_return, "h1_range_pct": h1_range,
        # Exact historic quote-change age and spot movement since repricing
        # are unavailable. Zero proxies are NOT claimed as original inputs.
        "quote_age_seconds": 0.0, "btc_move_since_quote": 0.0,
        "path_efficiency_15m": min(1., max(0., path)),
        "hour_sin": math.sin(2*math.pi*hour_fraction/24),
        "hour_cos": math.cos(2*math.pi*hour_fraction/24),
        "rsi_14": float(rsi), "macd_histogram_pct": macd,
        "bollinger_pct_b": bollinger,
        "vwap_deviation_60m": (float(close.iloc[-1])-vwap)/vwap,
        "settlement_bias": settlement_bias,
    }
    if set(features) != set(SIDDHA_COLUMNS) or not all(math.isfinite(v) for v in features.values()):
        return None
    return features


class _VerifiedSecondHistory:
    """SHA-verified, bounded-memory daily Parquet cache. Read-only."""
    def __init__(self, root: Path, max_days: int = 5):
        self.root = root
        self.max_days = max_days
        self.cache: OrderedDict[str, Any] = OrderedDict()

    def day(self, d: date):
        import pyarrow.parquet as pq
        from .provenance import verify_artifact

        key = d.isoformat()
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        name = f"BTCUSDT-1s-{key}.parquet"
        file = self.root / "normalized" / "binance" / "1s" / name
        manifest = self.root / "manifests" / "binance_normalized" / "1s" / (name + ".manifest.json")
        if not file.is_file() or not verify_artifact(file, manifest):
            raise HistoricalBenchmarkError(f"missing/invalid verified Binance archive: {key}")
        table = pq.read_table(file, columns=["ts_ns", "close", "high", "low", "base_volume"])
        self.cache[key] = table.to_pandas()
        if len(self.cache) > self.max_days:
            self.cache.popitem(last=False)
        return self.cache[key]

    def before(self, checkpoint_ns: int):
        import pandas as pd

        anchor = (checkpoint_ns // NS) * NS
        start = anchor - LOOKBACK_NS
        current = datetime.fromtimestamp(start/NS, timezone.utc).date()
        end = datetime.fromtimestamp((anchor-NS)/NS, timezone.utc).date()
        blocks = []
        while current <= end:
            frame = self.day(current)
            blocks.append(frame[(frame.ts_ns >= start) & (frame.ts_ns + NS <= anchor)])
            current += timedelta(days=1)
        return pd.concat(blocks, ignore_index=True) if blocks else pd.DataFrame()


def evaluate_paired(rows: Sequence[tuple[float, float, int, str]]) -> dict[str, Any]:
    edge, siddha, label, locked = zip(*rows) if rows else ([], [], [], [])
    return {
        "markets": len(rows),
        "kalshiedge_common_gate": _score(edge, label),
        "siddhabot_common_gate": _score(siddha, label),
        "kalshiedge_original_locked_policy": _locked_score(locked, label),
        "paired_brier": {
            "kalshiedge": sum((p-y)**2 for p,y in zip(edge, label))/len(rows) if rows else None,
            "siddhabot": sum((p-y)**2 for p,y in zip(siddha, label))/len(rows) if rows else None,
        },
    }


def _prob(value: Any) -> float | None:
    return (float(value) if type(value) in (float, int) and math.isfinite(value)
            and 0 <= value <= 1 else None)


def run_historical(root: Path, *, max_markets: int = 400) -> dict[str, Any]:
    """Archived FINAL forecasts only: no model retrain, no artificial live results."""
    from .final_history import _forecasts, _load_saved_settlement, SettlementIntegrityError
    from .config import BootstrapSettings
    import numpy as np
    import xgboost as xgb

    root = root.resolve()
    if max_markets < 1:
        raise HistoricalBenchmarkError("max_markets must be positive")
    if BootstrapSettings().binance_symbol.upper() != "BTCUSDT":
        raise HistoricalBenchmarkError("requires verified BTCUSDT 1-second archives")
    finals = [
        f for f in _forecasts(root)
        if f.checkpoint_seconds == 480
        and datetime.fromtimestamp(f.prediction_ts_ns/NS, timezone.utc) > MODEL_TRAINED_AT
    ]
    if not finals:
        return {"status": "NO_HISTORICAL_FINAL_FORECASTS", "eligible_finals": 0}

    settled = []
    integrity_errors = 0
    for final in finals:
        try:
            settlement = _load_saved_settlement(root, final)
        except SettlementIntegrityError:
            integrity_errors += 1
            continue
        if settlement is not None:
            settled.append((final, int(settlement.result == "yes")))
    settled.sort(key=lambda x: x[0].prediction_ts_ns)
    period = settled[-max_markets:]
    if not period:
        return {"status": "NO_VERIFIED_SETTLEMENTS", "eligible_finals": len(finals)}

    _checked_artifact(root, "kxbtc15m_xgb.json")
    calibration = json.loads(_checked_artifact(root, "calibration.json"))
    if not all(type(calibration.get(k)) in (float, int) and math.isfinite(calibration[k])
               for k in ("a", "b")):
        raise HistoricalBenchmarkError("invalid original SiddhaBot Platt calibration")
    model_file = root / "benchmarks" / "siddhabot" / "artifacts" / "kxbtc15m_xgb.json"
    if not model_file.is_file():
        model_file = root / "benchmarks" / "siddhabot" / "repo" / "backend" / "btcmarket" / "artifacts" / "kxbtc15m_xgb.json"
    if git_blob_sha1(model_file.read_bytes()) != ARTIFACT_BLOBS["kxbtc15m_xgb.json"]:
        raise HistoricalBenchmarkError("model SHA verification failed")
    model = xgb.XGBClassifier()
    model.load_model(str(model_file))
    if getattr(model, "n_features_in_", len(SIDDHA_COLUMNS)) != len(SIDDHA_COLUMNS):
        raise HistoricalBenchmarkError("upstream XGBoost feature dimensions differ")

    history = _VerifiedSecondHistory(root)
    previous = [(f.scheduled_ts_ns + 480*NS, y) for f,y in settled]
    ready = []
    unavailable: dict[str, int] = {}
    for final, label in period:
        p_edge = _prob(final.p_yes)
        if p_edge is None:
            reason = "NO_KALSHIEDGE_PROBABILITY"
        else:
            bias = trailing_settlement_bias(previous, final.prediction_ts_ns)
            if bias is None:
                reason = "INSUFFICIENT_PRIOR_SETTLEMENTS"
            else:
                try:
                    ticks = history.before(final.prediction_ts_ns)
                    vector = build_proxy_features(final, ticks, settlement_bias=bias)
                    reason = None if vector is not None else "CAUSAL_PROXY_FEATURES_UNAVAILABLE"
                except HistoricalBenchmarkError:
                    reason = "BINANCE_ARCHIVE_MISSING_OR_INVALID"
        if reason is not None:
            unavailable[reason] = unavailable.get(reason, 0) + 1
            continue
        ready.append((p_edge, label, final.forecast, vector))

    if not ready:
        return {
            "status": "NO_COMPARABLE_HISTORICAL_MARKETS",
            "eligible_settled_finals": len(period),
            "unscorable": unavailable, "invalid_settlements": integrity_errors,
            "limitation": "Original Coinbase and historical quote-age inputs unavailable",
        }

    inputs = np.asarray([[vec[name] for name in SIDDHA_COLUMNS] for _,_,_,vec in ready], dtype=float)
    raw = model.predict_proba(inputs)[:, 1]
    scored = []
    a, b = calibration["a"], calibration["b"]
    for (edge, label, locked, _), value in zip(ready, raw):
        p = min(1-1e-6, max(1e-6, float(value)))
        logit = a * math.log(p/(1-p)) + b
        calibrated = 1/(1+math.exp(-logit)) if logit >= 0 else math.exp(logit)/(1+math.exp(logit))
        scored.append((edge, calibrated, label, locked))
    return {
        "status": "HISTORICAL_PROXY_REPLAY_COMPLETE",
        "upstream_commit": PINNED_UPSTREAM,
        "original_model_git_blob": ARTIFACT_BLOBS["kxbtc15m_xgb.json"],
        "checkpoint_seconds": 480,
        "strictly_greater_than_directional_gate": .63,
        "eligible_settled_finals": len(period),
        "scored_paired_markets": len(scored),
        "invalid_settlements": integrity_errors, "unscorable": unavailable,
        "scores": evaluate_paired(scored),
        "limitations": [
            "SiddhaBot features reconstructed from Binance BTCUSDT, not native Coinbase BTC-USD",
            "Quote age and price since last quote change are zero proxies (upstream reports zero feature importance)",
            "Trailing market settlement bias assumes previous outcomes available 60 seconds after close",
            "Original fixed XGBoost artifact plus Platt calibration only; not original secondary live recalibration",
            "Coverage only among paired, historically reconstructible markets; exclusions reported separately",
            "No retraining, trades, live changes or claim of fully independent comparative predictive superiority",
        ],
    }


def format_historical(result: dict[str, Any]) -> str:
    lines = [
        "KalshiEdge vs SiddhaBot — PREVIOUS KXBTC15M MARKETS (480s)",
        "Mode: SiddhaBot frozen XGBoost with BINANCE PROXY inputs; NOT original Coinbase replay.",
        "Status: " + result["status"],
    ]
    if "scores" not in result:
        return "\n".join(lines + ["No comparable historical markets available.", json.dumps(result, indent=2)])
    report = result["scores"]
    edge = report["kalshiedge_common_gate"]
    siddha = report["siddhabot_common_gate"]
    pct = lambda n: f"{n*100:.2f}%" if n is not None else "N/A"
    lines.extend([
        f"Eligible settled finals: {result['eligible_settled_finals']} | Matched historical inputs: {result['scored_paired_markets']}",
        "Common gate: strictly >63% predicted-side probability (same for both).",
        "",
        f"{'Metric':<23}{'KalshiEdge':>15}{'SiddhaBot proxy':>19}",
        "-"*57,
        f"{'Accuracy':<23}{pct(edge['accuracy']):>15}{pct(siddha['accuracy']):>19}",
        f"{'Coverage':<23}{pct(edge['coverage']):>15}{pct(siddha['coverage']):>19}",
        f"{'Qualified':<23}{edge['qualified']:>15}{siddha['qualified']:>19}",
        f"{'Correct':<23}{edge['correct']:>15}{siddha['correct']:>19}",
        f"{'Incorrect':<23}{edge['incorrect']:>15}{siddha['incorrect']:>19}",
        f"{'Skipped by gate':<23}{edge['skipped']:>15}{siddha['skipped']:>19}",
        f"{'Brier (lower better)':<23}{report['paired_brier']['kalshiedge']:>15.5f}{report['paired_brier']['siddhabot']:>19.5f}",
        "",
        f"KalshiEdge ACTUAL locked-policy accuracy: {pct(report['kalshiedge_original_locked_policy']['accuracy'])}",
        f"KalshiEdge ACTUAL locked-policy coverage: {pct(report['kalshiedge_original_locked_policy']['coverage'])}",
        f"Markets excluded as unscorable: {result['unscorable']}",
        "CAUTION: SiddhaBot input reconstruction is approximate; do not read this as a native, definitive winner.",
    ])
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    from .config import BootstrapSettings

    parser = argparse.ArgumentParser(description="Read-only prior-market SiddhaBot XGBoost proxy replay")
    parser.add_argument("--root", type=Path, default=BootstrapSettings().bootstrap_dir)
    parser.add_argument("--max-markets", type=int, default=400)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run_historical(args.root, max_markets=args.max_markets)
    except (HistoricalBenchmarkError, OSError, ValueError, ImportError) as exc:
        result = {"status": "UNAVAILABLE", "reason": str(exc)}
    print(json.dumps(result, indent=2, sort_keys=True) if args.json else format_historical(result))
    return 0 if result.get("status") == "HISTORICAL_PROXY_REPLAY_COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
