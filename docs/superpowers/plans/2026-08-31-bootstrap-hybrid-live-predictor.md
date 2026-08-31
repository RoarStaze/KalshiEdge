# Bootstrap Hybrid Live Predictor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only KXBTC15M probability engine that backfills official historical Kalshi + Binance data, constructs point-in-time leakage-safe observations, trains chronologically validated structural/ML/residual components, calibrates an ensemble against the Kalshi market benchmark, and emits live ABOVE/BELOW probabilities without affecting the Phase 1 collector.

**Architecture:** Add a separate `kalshi_edge.bootstrap` package and `data/bootstrap/` storage tree. Historical training uses exact Kalshi outcomes/settlement values, Kalshi historical trades/candles, and official Binance BTCUSDT high-frequency history. Live inference runs in its own process with its own read-only Kalshi WebSocket plus Binance public stream, so the existing collector's fsync/write loop and `data/raw` hierarchy are never touched.

**Tech Stack:** Python >=3.12; existing httpx/websockets/cryptography/pydantic stack; DuckDB + PyArrow for derived datasets; NumPy + scikit-learn for deterministic numerical/statistical pipelines; XGBoost as an additional nonlinear candidate only if it passes chronological OOS selection; pytest for TDD; JSON/Parquet model and report artifacts with SHA-256 provenance.

**Spec:** `docs/superpowers/specs/2026-08-31-bootstrap-hybrid-live-predictor-design.md`

## Global Constraints

- Existing `kalshi-edge collect` behavior and `data/raw` canonical Phase 1 data must not be modified by bootstrap code.
- Bootstrap mode is strictly read-only: no order placement, cancellation, portfolio mutation, or live-trading authorization.
- Phase 1 gate remains `NOT PASSED` until its existing acceptance criteria are evidenced; bootstrap model performance does not bypass it.
- Historical labels come from Kalshi `result` + `settlement_value_dollars`; external BTC prices are features, not settlement labels.
- Every historical feature must be provably observable at or before its checkpoint timestamp.
- All observations from one market ticker belong to one chronological split group.
- Final lockbox data is never used for feature selection, hyperparameter selection, ensemble weighting, or calibration-method selection.
- Kalshi contemporaneous probability is the primary benchmark; superiority claims require untouched chronological probability-metric improvement.
- Live inference emits `NO_PREDICTION` on stale/missing/inconsistent required inputs.
- Secrets remain in `.env` / ignored key files; bootstrap manifests and model metadata never serialize key IDs or private-key paths.
- First production bootstrap model uses only audited official Kalshi/Binance data; third-party KXBTC15M archives remain quarantined.

---

### Task 1: Bootstrap package contracts, configuration, dependencies, and CLI shell

**Files:**
- Create: `src/kalshi_edge/bootstrap/__init__.py`
- Create: `src/kalshi_edge/bootstrap/config.py`
- Create: `src/kalshi_edge/bootstrap/types.py`
- Modify: `src/kalshi_edge/cli.py`
- Modify: `pyproject.toml`
- Add/update locked research dependency files without changing the Phase 1 runtime lock used by the collector container.
- Test: `tests/bootstrap/test_config.py`
- Test: `tests/bootstrap/test_cli.py`

**Interfaces:**
- `BootstrapSettings(BaseSettings)` with `bootstrap_dir`, `series_ticker`, `binance_symbol`, `checkpoint_seconds`, `kalshi_stale_seconds`, `binance_stale_seconds`, `random_seed`.
- `MarketLabel`, `FeatureRow`, `PredictionRecord`, and `FeedQuality` immutable dataclasses/Pydantic models.
- CLI commands: `bootstrap-backfill`, `bootstrap-build-dataset`, `bootstrap-train`, `bootstrap-evaluate`, `predict-live`.

- [ ] **Step 1: Write failing configuration/CLI tests** asserting default bootstrap root resolves to `data/bootstrap`, series is `KXBTC15M`, symbol is `BTCUSDT`, checkpoints equal `(840,780,720,660,600,540,480,420,360,300,240,180,120,60,45,30,20,10)`, and all five commands parse without changing existing Phase 1 commands.
- [ ] **Step 2: Run** `pytest -q tests/bootstrap/test_config.py tests/bootstrap/test_cli.py tests/test_cli.py` and confirm the new imports/commands fail while existing CLI tests remain green.
- [ ] **Step 3: Implement the minimal package/contracts/config/CLI parser additions.** Keep execution branches thin: each new CLI command imports and invokes one bootstrap entry function; `collect` remains byte-for-byte behaviorally equivalent.
- [ ] **Step 4: Add research dependencies** for `numpy`, `scikit-learn`, `duckdb`, `pyarrow`, and `xgboost` in the research extra and a separate reproducible research lock. Do not add them to `requirements.runtime.lock`, so the Phase 1 collector image remains lean and unchanged.
- [ ] **Step 5: Run** `pytest -q` and `python -m compileall -q src tests`; then commit `feat: add bootstrap predictor contracts and CLI`.

### Task 2: Provenance-first bootstrap storage

**Files:**
- Create: `src/kalshi_edge/bootstrap/provenance.py`
- Create: `src/kalshi_edge/bootstrap/storage.py`
- Test: `tests/bootstrap/test_provenance.py`

**Interfaces:**
- `sha256_file(path: Path) -> str`
- `write_raw_artifact(*, root: Path, source: str, logical_name: str, content: bytes, metadata: dict[str, Any]) -> RawArtifact`
- `write_manifest(root: Path, artifact: RawArtifact) -> Path`
- `verify_artifact(artifact_path: Path, manifest_path: Path) -> bool`

`RawArtifact` records exact relative path, SHA-256, source, retrieval UTC timestamp, source locator, parser version, requested range/ticker, byte count, row count when known, normalized timestamp unit, duplicate count, gap count, and HTTP/archive metadata.

- [ ] **Step 1: Write failing tests** proving bootstrap files land under `data/bootstrap/raw/...`, manifests land under `data/bootstrap/manifests/...`, hashes detect a one-byte mutation, and no path can escape the configured bootstrap root.
- [ ] **Step 2: Run** `pytest -q tests/bootstrap/test_provenance.py` and confirm failure before implementation.
- [ ] **Step 3: Implement atomic write + SHA-256 manifest creation** using temp-file then `os.replace`, canonical JSON (`sort_keys=True`, compact separators), and explicit UTF-8 encoding for JSON metadata.
- [ ] **Step 4: Add a test** proving bootstrap writers reject any target whose resolved path is the canonical Phase 1 `data/raw` tree.
- [ ] **Step 5: Run focused + full tests** and commit `feat: add bootstrap provenance storage`.

### Task 3: Official Kalshi historical backfill and exact labels

**Files:**
- Create: `src/kalshi_edge/bootstrap/kalshi_history.py`
- Create: `src/kalshi_edge/bootstrap/labels.py`
- Test: `tests/bootstrap/test_kalshi_history.py`
- Test: `tests/bootstrap/test_labels.py`

**Interfaces:**
- `KalshiHistoricalClient(settings: CollectorSettings, bootstrap: BootstrapSettings)`
- `get_cutoff() -> HistoricalCutoff`
- `discover_markets() -> list[dict[str, Any]]`
- `fetch_market(ticker: str) -> dict[str, Any]`
- `fetch_trades(ticker: str) -> list[dict[str, Any]]`
- `fetch_candlesticks(ticker: str, *, start_ts: int, end_ts: int, period_interval: int = 1) -> list[dict[str, Any]]`
- `normalize_market_label(payload: dict[str, Any]) -> MarketLabel`

- [ ] **Step 1: Write failing HTTP-mocked tests** for cursor pagination, historical cutoff routing, per-ticker trades, 1-minute candles, HTTP error propagation, and rate-limit retry with bounded exponential backoff honoring `Retry-After` when present.
- [ ] **Step 2: Write failing label tests** using representative ABOVE and BELOW historical market payloads. Assert `ticker`, strike, result, settlement value, open/close/settlement timestamps and result mapping are preserved. Reject unresolved/missing-label/ambiguous-strike records explicitly.
- [ ] **Step 3: Implement signed read-only GET support** by reusing `load_private_key()` and `create_auth_headers()`; do not add POST/PUT/DELETE methods to this client.
- [ ] **Step 4: Implement `bootstrap-backfill --source kalshi`** to discover the full available KXBTC15M universe programmatically and persist raw market/trade/candle responses plus manifests. Incremental reruns skip an artifact only when its existing manifest hash verifies.
- [ ] **Step 5: Run focused/full tests** and commit `feat: backfill official Kalshi history`.

### Task 4: Official Binance high-frequency BTC backfill

**Files:**
- Create: `src/kalshi_edge/bootstrap/binance_history.py`
- Test: `tests/bootstrap/test_binance_history.py`

**Interfaces:**
- `BinanceArchiveClient`
- `archive_urls(symbol: str, dates: Iterable[date], dataset: str = "klines", interval: str = "1s") -> list[str]`
- `download_and_verify(url: str, checksum_url: str, root: Path) -> RawArtifact`
- `parse_spot_1s(path: Path) -> Iterable[BinanceBar]`
- `normalize_epoch_to_ns(value: int) -> int`

- [ ] **Step 1: Write failing tests** for official archive URL construction, checksum success/failure, ZIP parsing, microsecond timestamp normalization for post-2025 spot history, duplicate detection, monotonic timestamp checks, and missing-second gap statistics.
- [ ] **Step 2: Run** `pytest -q tests/bootstrap/test_binance_history.py` and confirm failures.
- [ ] **Step 3: Implement streaming downloads** to bootstrap temp files with checksum verification before finalization. Never load multi-GB archives fully into memory.
- [ ] **Step 4: Implement normalized Parquet conversion** with columns `ts_ns`, `open`, `high`, `low`, `close`, `base_volume`, `quote_volume`, `trade_count`, `taker_buy_base`, `taker_buy_quote`; preserve original ZIP as immutable raw input.
- [ ] **Step 5: Add `bootstrap-backfill --source binance`**, run focused/full tests, and commit `feat: backfill official Binance BTC history`.

### Task 5: Leakage-safe point-in-time feature dataset

**Files:**
- Create: `src/kalshi_edge/bootstrap/features.py`
- Create: `src/kalshi_edge/bootstrap/dataset.py`
- Create: `src/kalshi_edge/bootstrap/leakage.py`
- Test: `tests/bootstrap/test_features.py`
- Test: `tests/bootstrap/test_dataset.py`
- Test: `tests/bootstrap/test_leakage.py`

**Interfaces:**
- `build_feature_row(label: MarketLabel, checkpoint_ts_ns: int, kalshi: HistoricalKalshiState, btc: HistoricalBTCState) -> FeatureRow`
- `build_dataset(root: Path, settings: BootstrapSettings) -> DatasetBuildReport`
- `audit_feature_row(row: FeatureRow) -> list[LeakageFinding]`
- `audit_dataset(table: pa.Table) -> LeakageAuditReport`

- [ ] **Step 1: Write failing timestamp-boundary tests** proving a trade at `t+1ns` is excluded from a row at `t`, a 1-minute Kalshi candle cannot contribute close/high/low until that minute completes, and rolling BTC returns/volatility use only timestamps `<= checkpoint`.
- [ ] **Step 2: Write failing group-integrity tests** proving all 18 checkpoint rows from one market share `market_ticker`, `market_date`, label, and a single split-group identity.
- [ ] **Step 3: Implement deterministic features**: seconds remaining, elapsed fraction, strike, BTC distance/bps, normalized distance, returns (5/10/15/30/60/120/300/600s), realized volatility (15/30/60/180/300/900s), high/low excursions, distance velocity/acceleration, volume/trade-count windows, taker-buy imbalance, Kalshi prior/mid/spread/last trade, probability returns, Kalshi trade flow, quote/trade staleness, and structural discrepancy fields.
- [ ] **Step 4: Implement automatic leakage audit** rejecting result/settlement fields from feature columns, noncausal candle use, future rolling-window inputs, full-dataset normalization state, cross-split market duplication, and rows with feature source timestamps later than checkpoint.
- [ ] **Step 5: Write the final feature matrix to Parquet plus provenance JSON**, run focused/full tests, and commit `feat: build leakage-safe bootstrap dataset`.

### Task 6: Structural settlement probability engine

**Files:**
- Create: `src/kalshi_edge/bootstrap/structural.py`
- Test: `tests/bootstrap/test_structural.py`

**Interfaces:**
- `required_remaining_mean(*, strike: float, observed_values: Sequence[float], total_observations: int = 60) -> float`
- `StructuralModel.fit(training_rows: Sequence[FeatureRow]) -> StructuralModel`
- `StructuralModel.predict_proba(state: StructuralState) -> float`
- `StructuralModel.predict_final_minute(state: FinalMinuteState) -> float`

- [ ] **Step 1: Write failing exact-average tests**: with 30 observed values averaging the strike, required remaining mean equals strike; with 60 observations, probability collapses deterministically to the verified rule result; duplicated/missing/ambiguous final-minute samples fail closed.
- [ ] **Step 2: Write seeded simulation tests** requiring probabilities in `[0,1]`, monotonic increase with positive normalized distance holding volatility/time fixed, and exact reproducibility for the configured seed.
- [ ] **Step 3: Implement two candidates**: realized-volatility diffusion with weak drift capped by a past-only estimate, and empirical standardized-residual bootstrap. Fit every parameter from training rows only.
- [ ] **Step 4: Add chronological selector** that chooses the structural candidate with lower validation log loss, using Brier as first tiebreak and calibration error as second; save selection evidence.
- [ ] **Step 5: Run focused/full tests** and commit `feat: add structural settlement model`.

### Task 7: Chronological ML, residual correction, stacking, and calibration

**Files:**
- Create: `src/kalshi_edge/bootstrap/splits.py`
- Create: `src/kalshi_edge/bootstrap/models.py`
- Create: `src/kalshi_edge/bootstrap/calibration.py`
- Create: `src/kalshi_edge/bootstrap/metrics.py`
- Test: `tests/bootstrap/test_splits.py`
- Test: `tests/bootstrap/test_models.py`
- Test: `tests/bootstrap/test_calibration.py`

**Interfaces:**
- `make_walk_forward_splits(markets: Sequence[MarketIndex], *, min_train_markets: int, validation_markets: int, embargo_markets: int) -> list[WalkForwardSplit]`
- `train_candidate_models(dataset, splits, seed) -> CandidateResults`
- `fit_residual_model(...) -> ResidualModel`
- `fit_stacker(oof_predictions, labels) -> Stacker`
- `fit_calibrator(predictions, labels) -> Calibrator`
- `probability_metrics(y_true, p) -> ProbabilityMetrics`

- [ ] **Step 1: Write failing split tests** proving chronological ordering, market-level exclusivity, embargo enforcement, expanding-window semantics, and a final untouched lockbox that never appears in development folds.
- [ ] **Step 2: Implement candidates**: regularized logistic regression; scikit-learn histogram gradient boosting; XGBoost classifier with fixed seed and deterministic CPU settings. Hyperparameter grids remain compact and are evaluated only on inner chronological folds.
- [ ] **Step 3: Implement residual correction** as a regularized model of outcome residual in logit space relative to clipped Kalshi prior (`eps=1e-4`). If untouched-development OOF Brier/log loss do not improve over uncorrected Kalshi prior, set residual component weight to zero.
- [ ] **Step 4: Implement nonnegative simplex stacking** over Kalshi prior, structural, historical ML, and residual-corrected predictions. Fit weights only on OOF chronological predictions; zero-weight components remain excluded at live inference.
- [ ] **Step 5: Implement calibration selection** between Platt/logistic and isotonic on a later calibration block; choose by log loss then Brier, rejecting isotonic when sample support is insufficient or later-period calibration degrades.
- [ ] **Step 6: Implement metrics**: Brier, log loss, accuracy, 10-bin ECE/reliability table, sharpness summary, and market/day clustered bootstrap CIs with seeded resampling.
- [ ] **Step 7: Run focused/full tests** and commit `feat: train chronological bootstrap ensemble`.

### Task 8: Model artifact, untouched evaluation, ablation, and promotion gate

**Files:**
- Create: `src/kalshi_edge/bootstrap/artifact.py`
- Create: `src/kalshi_edge/bootstrap/evaluate.py`
- Test: `tests/bootstrap/test_artifact.py`
- Test: `tests/bootstrap/test_evaluate.py`

**Interfaces:**
- `save_model_bundle(bundle: ModelBundle, root: Path) -> Path`
- `load_model_bundle(path: Path) -> ModelBundle`
- `evaluate_lockbox(bundle, dataset) -> BootstrapEvaluationReport`
- `promotion_decision(report: BootstrapEvaluationReport) -> PromotionDecision`

- [ ] **Step 1: Write failing artifact tests** requiring model version, git SHA, seed, exact feature list/order, train/calibration/lockbox boundaries, raw/derived input hashes, library versions, component identities/weights, calibration method, metrics, leakage-audit result, and bundle SHA-256.
- [ ] **Step 2: Write failing promotion tests** requiring valid leakage audit, no train/serve feature mismatch, and finite probability metrics. The report must never call a model superior to Kalshi unless lockbox Brier or log loss improves with the predeclared comparison and the complementary metric does not show material degradation.
- [ ] **Step 3: Implement ablations** for external BTC, Kalshi flow, structural component, residual component, and XGBoost-vs-low-variance model. Features/components that do not add later-period value are excluded from the promoted bundle.
- [ ] **Step 4: Implement `bootstrap-train` and `bootstrap-evaluate`** producing JSON reports under `data/bootstrap/reports/` and a hash-addressed promoted bundle under `data/bootstrap/models/` only when promotion checks pass. A non-promoted model may still be saved as an experiment but cannot be the default live bundle.
- [ ] **Step 5: Run focused/full tests** and commit `feat: add bootstrap model evaluation gate`.

### Task 9: Isolated live read-only inference

**Files:**
- Create: `src/kalshi_edge/bootstrap/live.py`
- Create: `src/kalshi_edge/bootstrap/live_kalshi.py`
- Create: `src/kalshi_edge/bootstrap/live_binance.py`
- Test: `tests/bootstrap/test_live.py`
- Test: `tests/bootstrap/test_live_feeds.py`

**Interfaces:**
- `KalshiLiveFeed` uses existing auth + protocol semantics but never writes to collector storage.
- `BinanceLiveFeed` consumes the public BTCUSDT live stream needed to reproduce trained BTC feature semantics.
- `LiveFeatureState.update_*()` maintains only causal rolling windows.
- `LivePredictor.predict(now_ns: int) -> PredictionRecord`.

- [ ] **Step 1: Write failing feed-isolation tests** proving live predictor objects never instantiate `RawSegmentWriter`, never receive the Phase 1 collector data directory as a writable target, and expose no order-entry method.
- [ ] **Step 2: Write failing stale-feed tests**: missing BRTI, stale Kalshi book, stale Binance path, missing active market/strike, schema mismatch, or loaded-model feature mismatch yields `NO_PREDICTION` with explicit reason.
- [ ] **Step 3: Implement separate authenticated Kalshi WebSocket** for current KXBTC15M orderbook/trades/BRTI/lifecycle and a separate public Binance BTCUSDT stream. Reuse existing signing/subscription parsing where safe, but no mutable state is shared with `KalshiCollector`.
- [ ] **Step 4: Implement live feature parity** with the promoted model's exact feature manifest. Unsupported live-only orderbook-depth features may be emitted as diagnostics but cannot enter the learned model until collector-native history supports them.
- [ ] **Step 5: Implement final-minute BRTI mode** using official BRTI observations from the predictor feed; enforce unique chronological observations and compute required remaining mean before structural simulation.
- [ ] **Step 6: Emit one JSONL `PredictionRecord` per second** under `data/bootstrap/predictions/YYYY-MM-DD/`, including timestamp, ticker, strike, seconds remaining, Kalshi bid/ask/prior, BRTI, BTC reference, structural/ML/residual/final probabilities, predicted side, confidence, disagreement, staleness, model hash/version, and status/reason.
- [ ] **Step 7: Run focused/full tests** and commit `feat: add isolated live bootstrap predictor`.

### Task 10: End-to-end CLI, CI, documentation, and single local execution handoff

**Files:**
- Modify: `src/kalshi_edge/cli.py`
- Modify: `README.md`
- Create: `docs/RUNBOOK_BOOTSTRAP_PREDICTOR.md`
- Modify: `docs/PROJECT_CONTROL.md`
- Modify: `.github/workflows/ci.yml` only as needed to exercise research tests without changing collector runtime semantics.
- Test: `tests/bootstrap/test_end_to_end.py`

**Interfaces:**
- `kalshi-edge bootstrap-backfill --source kalshi|binance|all`
- `kalshi-edge bootstrap-build-dataset`
- `kalshi-edge bootstrap-train`
- `kalshi-edge bootstrap-evaluate`
- `kalshi-edge predict-live`

- [ ] **Step 1: Add a synthetic end-to-end test** that writes tiny official-shaped Kalshi/Binance fixtures, builds a point-in-time dataset, trains candidates, evaluates a held-out block, saves/loads a model bundle, and produces one live prediction without touching `data/raw`.
- [ ] **Step 2: Run** `pytest -q`, `python -m compileall -q src tests`, `kalshi-edge --help`, and the production Docker build; verify all preexisting Phase 1 tests still pass.
- [ ] **Step 3: Document exact Windows commands** for bootstrap backfill/build/train/evaluate/live prediction, expected disk locations, stopping/restarting the predictor, secret handling, data provenance checks, and the fact that `KalshiEdge Phase1 Collector` remains independently scheduled/running.
- [ ] **Step 4: Update project control accurately**: Phase 1 collector is live and gate remains incomplete; bootstrap predictor is a parallel read-only capability, not Phase 2 completion and not live-trading authorization.
- [ ] **Step 5: Commit** `docs: add bootstrap predictor runbook and project state`, open a PR, inspect full diff, run GitHub Actions, and merge only if clean.
- [ ] **Step 6: Use Codex exactly once for host-only execution after merge.** The minimal Codex task is: update the local clone, install the research lock, run `bootstrap-backfill --source all`, `bootstrap-build-dataset`, `bootstrap-train`, `bootstrap-evaluate`, start `predict-live` persistently beside the untouched collector, and return only the generated evaluation summary/model hash/current prediction/process status. Codex must not redesign, edit code, push/merge, or stop the collector unless an actual host-only defect blocks execution.

## Completion Evidence

Implementation is complete only when all of the following are freshly evidenced:

- Existing Phase 1 collector tests and behavior remain green.
- Bootstrap historical raw artifacts/manifests hash-verify.
- Historical label mapping is validated against actual Kalshi settlement fields.
- Point-in-time leakage audit passes.
- Chronological market-grouped walk-forward evaluation runs without random row splitting.
- Kalshi prior, structural model, logistic reference, nonlinear candidate, residual correction, and final ensemble are reported on the same OOS blocks.
- Untouched lockbox is evaluated exactly once for the promoted configuration.
- Model bundle contains reproducibility/provenance metadata and verifies its hash.
- Live predictor reproduces the promoted feature contract, fails closed on stale inputs, emits calibrated ABOVE/BELOW probability records, and has no order-entry capability.
- `kalshi-edge collect` and the Windows `KalshiEdge Phase1 Collector` remain independent and continue accumulating canonical Phase 1 data.
