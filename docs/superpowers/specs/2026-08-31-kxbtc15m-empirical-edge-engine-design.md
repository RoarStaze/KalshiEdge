# KXBTC15M Empirical Edge Engine Design

## Purpose

The project measures conditional KXBTC15M resolution probabilities from executable market paths and only treats a candidate as an edge after statistical, economic, execution, and regime validation. Generic BTC direction prediction is not the primary strategy.

## Required system boundaries

1. Canonical data ingestion and deterministic replay.
2. Path/touch event extraction with explicit executable semantics.
3. Preregistered statistical research and immutable hypothesis states.
4. Pessimistic queue/fill-aware backtesting.
5. Shadow/paper live counterfactual execution.
6. Risk, markout, adverse-selection, drift, and kill-switch systems.
7. Live execution disabled until explicit Phase 5 authorization.

## Data truth hierarchy

- Settlement/fundamental anchor: CF Benchmarks BRTI and KXBTC15M market rules.
- Kalshi orderbook/trades: executable market microstructure.
- Coinbase/Kraken/Binance: optional supplemental leading/reference features only after evidence that they add predictive value without leakage.

## Research governance

Every hypothesis receives an ID, immutable definition at validation entry, primary metric, effect-size threshold, power target, alpha/FDR policy, economic threshold, discovery set, validation set, final untouched lockbox, leakage checks, dependence/effective-sample-size method, regime tests, and sensitivity tests. Brittle bucket-only effects are rejected.

## Economic gate

A strategy is not approved unless the conservative lower confidence bound of expected value exceeds actual executable break-even after verified fees, spread, slippage, queue/fill uncertainty, adverse-selection reserve, and safety margin.

## Deployment gates

The six phase gates and current state are maintained in `docs/PROJECT_CONTROL.md`. No later phase can be activated merely because code exists.
