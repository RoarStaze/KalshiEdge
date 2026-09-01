from __future__ import annotations

"""Isolated, read-only live inference for the promoted bootstrap model."""

import asyncio
import json
import math
import os
import pickle
import statistics
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from pydantic import Field

from ..config import CollectorSettings
from . import artifact, features, structural, training
from .binance_history import BinanceBar
from .config import BootstrapSettings
from .live_binance import BinanceLiveFeed
from .live_kalshi import BRTIObservation, KalshiLiveFeed, LiveMarket, LiveQuote, LiveTrade, MarketLifecycle
from .types import FeedQuality, FeatureRow, MarketLabel, PredictionRecord


NS = 1_000_000_000
FINAL_MINUTE_SECONDS = 60


class LiveModelError(RuntimeError):
    """Raised when the promoted live model cannot be verified safely."""


class LiveStateError(RuntimeError):
    """Raised when a causal live feature row cannot be constructed."""


class LivePredictionRecord(PredictionRecord):
    strike: float | None = None
    seconds_remaining: float | None = Field(default=None, ge=0.0)
    kalshi_yes_bid: float | None = Field(default=None, ge=0.0, le=1.0)
    kalshi_yes_ask: float | None = Field(default=None, ge=0.0, le=1.0)
    kalshi_prior: float | None = Field(default=None, ge=0.0, le=1.0)
    brti_value: float | None = Field(default=None, gt=0.0)
    btc_reference: float | None = Field(default=None, gt=0.0)
    structural_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    ml_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    residual_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    final_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    component_disagreement: float | None = Field(default=None, ge=0.0, le=1.0)
    model_version: str | None = None


def load_default_bundle(root: Path) -> artifact.ModelBundle:
    """Load only the Task-8 promoted/default hash-addressed model bundle."""
    root = root.resolve()
    pointer_path = root / "models" / "default.json"
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        relative = str(pointer["path"])
        expected_hash = str(pointer["bundle_sha256"]).lower()
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise LiveModelError("no valid promoted default model pointer exists") from exc
    if len(expected_hash) != 64 or any(char not in "0123456789abcdef" for char in expected_hash):
        raise LiveModelError("default model pointer has invalid bundle hash")

    promoted_root = (root / "models" / "promoted").resolve()
    path = (root / relative).resolve()
    if promoted_root not in path.parents:
        raise LiveModelError("default model pointer does not target the promoted model directory")
    try:
        bundle = artifact.load_model_bundle(path)
    except Exception as exc:
        raise LiveModelError("promoted default model failed hash verification") from exc
    if bundle.stage != "promoted":
        raise LiveModelError("live inference accepts promoted model bundles only")
    if bundle.bundle_sha256 != expected_hash:
        raise LiveModelError("default pointer hash does not match promoted bundle")
    if bundle.source_experiment_sha256 is None or bundle.promotion_rule is None:
        raise LiveModelError("promoted bundle lacks Task-8 promotion provenance")
    return bundle


def load_default_model(root: Path) -> tuple[artifact.ModelBundle, training.FittedBootstrapPipeline]:
    bundle = load_default_bundle(root)
    try:
        payload = artifact.serialized_pipeline_bytes(bundle)
        pipeline = pickle.loads(payload)
    except Exception as exc:
        raise LiveModelError("promoted model pipeline could not be loaded") from exc
    if not isinstance(pipeline, training.FittedBootstrapPipeline):
        raise LiveModelError("promoted bundle contains an unexpected pipeline type")
    return bundle, pipeline


class LiveFeatureState:
    """In-memory causal rolling state. It owns no filesystem and no collector state."""

    def __init__(self) -> None:
        self.market: LiveMarket | None = None
        self.quotes: list[LiveQuote] = []
        self.trades: list[LiveTrade] = []
        self.binance_bars: list[BinanceBar] = []
        self.brti_observations: list[BRTIObservation] = []
        self.latest_quote_ts_ns: int | None = None
        self.latest_binance_ts_ns: int | None = None
        self.latest_brti_ts_ns: int | None = None
        self.invalid_reason: str | None = None

    @property
    def latest_quote(self) -> LiveQuote | None:
        return self.quotes[-1] if self.quotes else None

    @property
    def latest_bar(self) -> BinanceBar | None:
        return self.binance_bars[-1] if self.binance_bars else None

    @property
    def latest_brti(self) -> BRTIObservation | None:
        return self.brti_observations[-1] if self.brti_observations else None

    def update_market(self, market: LiveMarket) -> None:
        if market.close_ts_ns <= market.open_ts_ns or market.strike <= 0.0 or not math.isfinite(market.strike):
            self.invalid_reason = "UNREADABLE_STRIKE"
            return
        if self.market is None or self.market.ticker != market.ticker:
            self.quotes.clear()
            self.trades.clear()
            self.brti_observations.clear()
            self.latest_quote_ts_ns = None
            self.latest_brti_ts_ns = None
            self.invalid_reason = None
        self.market = market

    def update_kalshi(self, event: LiveQuote | LiveTrade | MarketLifecycle) -> None:
        if isinstance(event, LiveQuote):
            if self.market is not None and event.market_ticker != self.market.ticker:
                self.invalid_reason = "KALSHI_TICKER_MISMATCH"
                return
            if event.yes_bid is not None and event.yes_ask is not None and event.yes_bid > event.yes_ask + 1e-12:
                self.invalid_reason = "INCONSISTENT_KALSHI_QUOTE"
                return
            if self.latest_quote_ts_ns is not None and event.source_ts_ns < self.latest_quote_ts_ns:
                self.invalid_reason = "OUT_OF_ORDER_KALSHI_BOOK"
                return
            self.quotes.append(event)
            self.quotes = self.quotes[-4096:]
            self.latest_quote_ts_ns = event.source_ts_ns
            return

        if isinstance(event, LiveTrade):
            if self.market is not None and event.market_ticker != self.market.ticker:
                self.invalid_reason = "KALSHI_TICKER_MISMATCH"
                return
            if self.trades and event.source_ts_ns < self.trades[-1].source_ts_ns:
                self.invalid_reason = "OUT_OF_ORDER_KALSHI_TRADE"
                return
            self.trades.append(event)
            self.trades = self.trades[-8192:]
            return

        if isinstance(event, MarketLifecycle):
            if self.market is None or event.market_ticker != self.market.ticker:
                return
            if event.floor_strike is not None:
                self.market = self.market.model_copy(update={"strike": event.floor_strike})
            if event.event_type in {"deactivated", "determined", "settled"}:
                self.market = None
            return

        raise TypeError("unsupported Kalshi live event")

    def update_binance(self, bar: BinanceBar) -> None:
        if self.latest_binance_ts_ns is not None:
            if bar.ts_ns < self.latest_binance_ts_ns:
                self.invalid_reason = "OUT_OF_ORDER_BINANCE"
                return
            if bar.ts_ns == self.latest_binance_ts_ns:
                if bar != self.binance_bars[-1]:
                    self.invalid_reason = "AMBIGUOUS_BINANCE_BAR"
                return
        self.binance_bars.append(bar)
        self.binance_bars = self.binance_bars[-1800:]
        self.latest_binance_ts_ns = bar.ts_ns

    def update_brti(self, observation: BRTIObservation) -> None:
        if self.latest_brti_ts_ns is not None and observation.source_ts_ns <= self.latest_brti_ts_ns:
            # Preserve the observation so final-minute grouping can explicitly detect
            # duplicate/ambiguous seconds, but mark impossible timestamp ordering.
            self.invalid_reason = "AMBIGUOUS_BRTI_ORDERING"
        self.brti_observations.append(observation)
        self.brti_observations = self.brti_observations[-240:]
        self.latest_brti_ts_ns = max(self.latest_brti_ts_ns or 0, observation.source_ts_ns)

    def _completed_candles(self, now_ns: int) -> tuple[features.HistoricalKalshiCandle, ...]:
        if self.market is None:
            return ()
        buckets: dict[int, dict[str, list]] = {}
        for quote in self.quotes:
            if quote.market_ticker != self.market.ticker or quote.source_ts_ns > now_ns:
                continue
            end_ns = (quote.source_ts_ns // (60 * NS) + 1) * 60 * NS
            if end_ns > now_ns:
                continue
            buckets.setdefault(end_ns, {"quotes": [], "trades": []})["quotes"].append(quote)
        for trade in self.trades:
            if trade.market_ticker != self.market.ticker or trade.source_ts_ns > now_ns:
                continue
            end_ns = (trade.source_ts_ns // (60 * NS) + 1) * 60 * NS
            if end_ns > now_ns:
                continue
            buckets.setdefault(end_ns, {"quotes": [], "trades": []})["trades"].append(trade)

        output: list[features.HistoricalKalshiCandle] = []
        for end_ns in sorted(buckets):
            item = buckets[end_ns]
            quote_rows: list[LiveQuote] = sorted(item["quotes"], key=lambda value: value.source_ts_ns)
            trade_rows: list[LiveTrade] = sorted(item["trades"], key=lambda value: value.source_ts_ns)
            latest_quote = quote_rows[-1] if quote_rows else None
            prices = [trade.yes_price for trade in trade_rows]
            output.append(
                features.HistoricalKalshiCandle(
                    end_ts_ns=end_ns,
                    yes_bid_close=None if latest_quote is None else latest_quote.yes_bid,
                    yes_ask_close=None if latest_quote is None else latest_quote.yes_ask,
                    price_close=prices[-1] if prices else None,
                    price_high=max(prices) if prices else None,
                    price_low=min(prices) if prices else None,
                    volume=sum(trade.count for trade in trade_rows),
                    open_interest=self.market.open_interest,
                )
            )
        return tuple(output)

    def feature_row(self, now_ns: int) -> FeatureRow:
        market = self.market
        if market is None:
            raise LiveStateError("active market is unavailable")
        if not (market.open_ts_ns <= now_ns <= market.close_ts_ns):
            raise LiveStateError("prediction timestamp is outside active market")
        if not self.binance_bars:
            raise LiveStateError("Binance history is unavailable")
        historical_trades = tuple(
            features.HistoricalKalshiTrade(
                ts_ns=trade.source_ts_ns,
                yes_price=trade.yes_price,
                count=trade.count,
                taker_side=trade.taker_side,
            )
            for trade in self.trades
            if trade.market_ticker == market.ticker and trade.source_ts_ns <= now_ns
        )
        label = MarketLabel(
            ticker=market.ticker,
            strike=market.strike,
            strike_type="greater",
            yes_is_above=True,
            result="no",
            settlement_value=market.strike,
            open_ts_ns=market.open_ts_ns,
            close_ts_ns=market.close_ts_ns,
        )
        try:
            return features.build_feature_row(
                label,
                now_ns,
                features.HistoricalKalshiState(
                    trades=historical_trades,
                    candles=self._completed_candles(now_ns),
                ),
                features.HistoricalBTCState(bars=tuple(bar for bar in self.binance_bars if bar.ts_ns <= now_ns)),
            )
        except Exception as exc:
            raise LiveStateError("could not construct causal live feature row") from exc

    def final_minute_state(self, now_ns: int, feature_row: FeatureRow) -> structural.FinalMinuteState:
        market = self.market
        if market is None:
            raise LiveStateError("active market is unavailable")
        final_start = market.close_ts_ns - FINAL_MINUTE_SECONDS * NS
        if now_ns < final_start or now_ns > market.close_ts_ns:
            raise LiveStateError("final-minute state requested outside final minute")
        elapsed = min(60, max(0, math.ceil((now_ns - final_start) / NS)))
        by_second: dict[int, list[BRTIObservation]] = {}
        for observation in self.brti_observations:
            if not (final_start <= observation.source_ts_ns < now_ns):
                continue
            second = int((observation.source_ts_ns - final_start) // NS)
            if 0 <= second < 60:
                by_second.setdefault(second, []).append(observation)
        if any(len(values) != 1 for values in by_second.values()):
            raise LiveStateError("AMBIGUOUS_FINAL_MINUTE_BRTI")
        if set(by_second) != set(range(elapsed)):
            raise LiveStateError("INCOMPLETE_FINAL_MINUTE_BRTI")
        ordered = tuple(
            structural.FinalMinuteObservation(
                second_index=second,
                value=by_second[second][0].value,
                source_ts_ns=by_second[second][0].source_ts_ns,
            )
            for second in range(elapsed)
        )
        current = ordered[-1].value if ordered else (self.latest_brti.value if self.latest_brti is not None else 0.0)
        if current <= 0.0:
            raise LiveStateError("INCOMPLETE_FINAL_MINUTE_BRTI")
        return structural.FinalMinuteState(
            strike=market.strike,
            current_value=current,
            volatility_per_second=max(0.0, float(feature_row.features.get("btc_realized_vol_60s", 0.0))),
            elapsed_observations=elapsed,
            observations=ordered,
        )


class LivePredictor:
    def __init__(
        self,
        *,
        state: LiveFeatureState,
        bundle: artifact.ModelBundle,
        pipeline: object,
        settings: BootstrapSettings,
    ) -> None:
        if bundle.stage != "promoted":
            raise LiveModelError("live predictor accepts promoted model bundles only")
        if bundle.source_experiment_sha256 is None or bundle.promotion_rule is None:
            raise LiveModelError("promoted bundle lacks Task-8 promotion provenance")
        if bundle.bundle_sha256 is None:
            raise LiveModelError("promoted bundle is not hash-sealed")
        if artifact.bundle_sha256(bundle) != bundle.bundle_sha256:
            raise LiveModelError("promoted bundle failed content-hash verification")

        self.state = state
        self.bundle = bundle
        self.pipeline = pipeline
        self.settings = settings
        self.model_hash = bundle.bundle_sha256
        self._model_feature_mismatch = tuple(bundle.feature_names) != tuple(getattr(pipeline, "required_feature_names", ()))

    def _quality(self, now_ns: int, reasons: Sequence[str] = ()) -> FeedQuality:
        kalshi_stale = None if self.state.latest_quote_ts_ns is None else max(0.0, (now_ns - self.state.latest_quote_ts_ns) / NS)
        binance_stale = None if self.state.latest_binance_ts_ns is None else max(0.0, (now_ns - self.state.latest_binance_ts_ns) / NS)
        brti_stale = None if self.state.latest_brti_ts_ns is None else max(0.0, (now_ns - self.state.latest_brti_ts_ns) / NS)
        return FeedQuality(
            healthy=not reasons,
            reasons=tuple(reasons),
            kalshi_stale_seconds=kalshi_stale,
            binance_stale_seconds=binance_stale,
            brti_stale_seconds=brti_stale,
        )

    def _no_prediction(self, now_ns: int, reason: str) -> LivePredictionRecord:
        market = self.state.market
        quote = self.state.latest_quote
        brti = self.state.latest_brti
        bar = self.state.latest_bar
        return LivePredictionRecord(
            prediction_ts_ns=now_ns,
            market_ticker=None if market is None else market.ticker,
            status="NO_PREDICTION",
            feed_quality=self._quality(now_ns, (reason,)),
            model_hash=self.model_hash,
            reason=reason,
            strike=None if market is None else market.strike,
            seconds_remaining=None if market is None else max(0.0, (market.close_ts_ns - now_ns) / NS),
            kalshi_yes_bid=None if quote is None else quote.yes_bid,
            kalshi_yes_ask=None if quote is None else quote.yes_ask,
            brti_value=None if brti is None else brti.value,
            btc_reference=None if bar is None else bar.close,
            model_version=self.bundle.model_version,
        )

    @staticmethod
    def _prior(row: FeatureRow) -> float:
        f = row.features
        if f.get("kalshi_quote_available", 0.0) > 0.0:
            value = f.get("kalshi_mid")
            if value is not None and math.isfinite(value) and 0.0 <= value <= 1.0:
                return float(value)
        if f.get("kalshi_trade_available", 0.0) > 0.0:
            value = f.get("kalshi_last_trade_yes")
            if value is not None and math.isfinite(value) and 0.0 <= value <= 1.0:
                return float(value)
        return 0.5

    def _manual_final_minute_predictions(self, row: FeatureRow, now_ns: int) -> dict[str, float]:
        pipeline = self.pipeline
        x = [[float(row.features[name]) for name in pipeline.outcome_feature_names]]
        historical = float(pipeline.outcome_model.predict_proba(x)[0][1])
        logistic = float(pipeline.logistic_model.predict_proba(x)[0][1])
        prior = self._prior(row)
        final_state = self.state.final_minute_state(now_ns, row)
        structural_p = float(pipeline.structural_model.predict_final_minute(final_state))
        residual = float(pipeline.residual_model.predict(x, [prior])[0])
        components = {
            "kalshi_prior": [prior],
            "historical_ml": [historical],
            "structural": [structural_p],
            "residual_corrected": [residual],
        }
        active = {name: values for name, values in components.items() if pipeline.stacker.weights.get(name, 0.0) > 0.0}
        raw = float(pipeline.stacker.predict(active)[0])
        candidate = float(pipeline.calibrator.predict([raw])[0])
        return {
            "candidate": candidate,
            "kalshi_prior": prior,
            "structural": structural_p,
            "logistic": logistic,
            "historical_ml": historical,
            "residual_corrected": residual,
        }

    def predict(self, now_ns: int) -> LivePredictionRecord:
        if self._model_feature_mismatch:
            return self._no_prediction(now_ns, "PROMOTED_MODEL_FEATURE_MISMATCH")
        if self.state.invalid_reason is not None:
            reason = self.state.invalid_reason
            if reason.startswith("AMBIGUOUS_BRTI") and self.state.market is not None and now_ns >= self.state.market.close_ts_ns - 60 * NS:
                reason = "AMBIGUOUS_FINAL_MINUTE_BRTI"
            return self._no_prediction(now_ns, reason)
        market = self.state.market
        if market is None:
            return self._no_prediction(now_ns, "MISSING_ACTIVE_MARKET")
        if not math.isfinite(market.strike) or market.strike <= 0.0:
            return self._no_prediction(now_ns, "UNREADABLE_STRIKE")
        if not (market.open_ts_ns <= now_ns < market.close_ts_ns):
            return self._no_prediction(now_ns, "NO_ACTIVE_MARKET_AT_TIMESTAMP")

        quote = self.state.latest_quote
        if quote is None or quote.yes_bid is None or quote.yes_ask is None:
            return self._no_prediction(now_ns, "MISSING_KALSHI_BOOK")
        if quote.yes_bid > quote.yes_ask:
            return self._no_prediction(now_ns, "INCONSISTENT_KALSHI_QUOTE")
        if self.state.latest_quote_ts_ns is None or now_ns < self.state.latest_quote_ts_ns:
            return self._no_prediction(now_ns, "INCONSISTENT_KALSHI_TIMESTAMP")
        if (now_ns - self.state.latest_quote_ts_ns) / NS > self.settings.kalshi_stale_seconds:
            return self._no_prediction(now_ns, "STALE_KALSHI_BOOK")

        if self.state.latest_bar is None or self.state.latest_binance_ts_ns is None:
            return self._no_prediction(now_ns, "MISSING_BINANCE")
        if now_ns < self.state.latest_binance_ts_ns:
            return self._no_prediction(now_ns, "INCONSISTENT_BINANCE_TIMESTAMP")
        if (now_ns - self.state.latest_binance_ts_ns) / NS > self.settings.binance_stale_seconds:
            return self._no_prediction(now_ns, "STALE_BINANCE")

        if self.state.latest_brti is None or self.state.latest_brti_ts_ns is None:
            return self._no_prediction(now_ns, "MISSING_BRTI")
        if now_ns < self.state.latest_brti_ts_ns:
            return self._no_prediction(now_ns, "INCONSISTENT_BRTI_TIMESTAMP")
        if (now_ns - self.state.latest_brti_ts_ns) / NS > self.settings.kalshi_stale_seconds:
            return self._no_prediction(now_ns, "STALE_BRTI")

        try:
            full_row = self.state.feature_row(now_ns)
        except LiveStateError:
            return self._no_prediction(now_ns, "FEATURE_CONSTRUCTION_FAILED")
        missing = [name for name in self.bundle.feature_names if name not in full_row.features]
        if missing:
            return self._no_prediction(now_ns, "FEATURE_SCHEMA_MISMATCH")
        row = full_row.model_copy(update={"features": {name: full_row.features[name] for name in self.bundle.feature_names}})

        seconds_remaining = (market.close_ts_ns - now_ns) / NS
        try:
            if seconds_remaining < 60.0:
                values = self._manual_final_minute_predictions(row, now_ns)
            else:
                output = self.pipeline.predict([row])
                values = {name: float(vector[0]) for name, vector in output.items()}
        except LiveStateError as exc:
            reason = str(exc)
            if reason in {"AMBIGUOUS_FINAL_MINUTE_BRTI", "INCOMPLETE_FINAL_MINUTE_BRTI"}:
                return self._no_prediction(now_ns, reason)
            return self._no_prediction(now_ns, "LIVE_INFERENCE_INPUT_FAILED")
        except Exception:
            return self._no_prediction(now_ns, "LIVE_INFERENCE_FAILED")

        required_outputs = ("candidate", "kalshi_prior", "structural", "historical_ml", "residual_corrected")
        if any(name not in values or not math.isfinite(values[name]) or not 0.0 <= values[name] <= 1.0 for name in required_outputs):
            return self._no_prediction(now_ns, "INVALID_MODEL_OUTPUT")
        p_yes = min(1.0, max(0.0, values["candidate"]))
        components = [values[name] for name in ("kalshi_prior", "structural", "historical_ml", "residual_corrected")]
        disagreement = max(components) - min(components)
        confidence = min(1.0, 2.0 * abs(p_yes - 0.5))
        return LivePredictionRecord(
            prediction_ts_ns=now_ns,
            market_ticker=market.ticker,
            status="OK",
            p_yes=p_yes,
            p_no=1.0 - p_yes,
            predicted_side="ABOVE" if p_yes >= 0.5 else "BELOW",
            feed_quality=self._quality(now_ns),
            model_hash=self.model_hash,
            reason=None,
            strike=market.strike,
            seconds_remaining=seconds_remaining,
            kalshi_yes_bid=quote.yes_bid,
            kalshi_yes_ask=quote.yes_ask,
            kalshi_prior=values["kalshi_prior"],
            brti_value=self.state.latest_brti.value,
            btc_reference=self.state.latest_bar.close,
            structural_probability=values["structural"],
            ml_probability=values["historical_ml"],
            residual_probability=values["residual_corrected"],
            final_probability=p_yes,
            confidence=confidence,
            component_disagreement=disagreement,
            model_version=self.bundle.model_version,
        )


class PredictionJsonlWriter:
    def __init__(self, bootstrap_root: Path) -> None:
        root = bootstrap_root.resolve()
        if root.name == "raw" and root.parent.name == "data":
            raise ValueError("live predictions cannot target canonical Phase 1 data/raw")
        self.root = root

    def append(self, record: LivePredictionRecord) -> Path:
        day = datetime.fromtimestamp(record.prediction_ts_ns / NS, tz=timezone.utc).date().isoformat()
        path = self.root / "predictions" / day / "predictions.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(record.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return path


async def _run_live_async(collector: CollectorSettings, bootstrap: BootstrapSettings) -> None:
    bundle, pipeline = load_default_model(bootstrap.bootstrap_dir)
    state = LiveFeatureState()
    predictor = LivePredictor(state=state, bundle=bundle, pipeline=pipeline, settings=bootstrap)
    writer = PredictionJsonlWriter(bootstrap.bootstrap_dir)
    kalshi = KalshiLiveFeed(collector, series_ticker=bootstrap.series_ticker)
    binance = BinanceLiveFeed(bootstrap.binance_symbol)

    async def on_kalshi(event: object) -> None:
        if isinstance(event, LiveMarket):
            state.update_market(event)
        elif isinstance(event, BRTIObservation):
            state.update_brti(event)
        elif isinstance(event, (LiveQuote, LiveTrade, MarketLifecycle)):
            state.update_kalshi(event)

    async def on_binance(bar: BinanceBar) -> None:
        state.update_binance(bar)

    async def predict_loop() -> None:
        while True:
            now_ns = time.time_ns()
            writer.append(predictor.predict(now_ns))
            sleep_seconds = max(0.001, 1.0 - ((time.time_ns() - now_ns) / NS))
            await asyncio.sleep(sleep_seconds)

    tasks = [
        asyncio.create_task(kalshi.run_forever(on_kalshi), name="bootstrap-kalshi-live"),
        asyncio.create_task(binance.run_forever(on_binance), name="bootstrap-binance-live"),
        asyncio.create_task(predict_loop(), name="bootstrap-prediction-loop"),
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def run_live() -> int:
    """Run the isolated predictor forever; this entrypoint has no trading side effects."""
    asyncio.run(_run_live_async(CollectorSettings(), BootstrapSettings()))
    return 0