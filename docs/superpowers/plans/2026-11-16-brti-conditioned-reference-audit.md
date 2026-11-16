# BRTI-Conditioned Reference Error Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) for tracking.

**Goal:** Test whether checkpoint-time BRTI/Binance strike-side disagreement adds evidence about model errors after stratifying by BRTI near-strike risk and restricting to the actual 63% live replay gate.

**Architecture:** One separate read-only reporting module joins immutable saved 480s finals, integrity-checked forward feature snapshots, and verified official Kalshi settlements. It computes predeclared two-by-two error tables in risk strata for all healthy model-direction records, actual issued forecasts and counterfactual current-policy replay. It also reports exactly how many correct and incorrect currently eligible directions a hypothetical disagreement veto would remove. No trade path or model code changes.

**Tech Stack:** Python 3, existing Pydantic snapshot verification, `pytest`, GitHub CI.

**Spec:** The existing `docs/READ_ONLY_SKIP_BRTI_FORENSICS_480_2026-11-16.md` and the reported 123-market user audit motivate the extension.

## Global Constraints

- Same exact current model hash and 480-second checkpoint. Never mix versions or 240s.
- Only source snapshots stamped on/before final prediction, with both prices no more than 5 seconds old. Require signed BRTI/Binance gap consistency; exclude missing/ambiguous point-in-time values.
- Settlement is exclusively an outcome label, never a feature. Reject invalid or unavailable official settlements.
- Live replay is strictly `max(p_yes,p_no)>0.63`, with contemporaneous saved BRTI veto; distinguish replay from originally issued forecasts.
- Report stratum denominators, missingness, small cells and correct forecasts lost to hypothetical veto. Missing data is not treated as agreement.
- No fitted weights, automatic deployment, model rewrite, training, trade submission, network requests, credentials or file writes from the CLI.
- This historical cohort is repeatedly inspected, so apparent effects are descriptive and never justify live changes alone.

## Review Focus

- A missing or 6+ second-old feed is excluded, not reinterpreted as agreement.
- Fake/inconsistent BRTI-Binance signed gap cannot enter attribution.
- A skipped historical forecast can qualify in policy replay but must NOT count as an actually issued trade.
- A high-risk z<=.20 case remains vetoed at the current live gate even if p>.63.
- Zero or single-member strata return null error rates and no fabricated certainty.

---

## Task 1: Analysis contracts and regression tests

- [ ] Add tests for risk-conditioned error tables, issued vs replay gate, hypothetical rejection accounting and stale/invalid inputs.
- [ ] Run branch CI to observe failure for missing report implementation (RED).

## Task 2: Implement the read-only report

- [ ] Implement `src/kalshi_edge/bootstrap/reference_conditioned_480.py` with strict joins, stratified descriptive scoring and CLI.
- [ ] Run CI and fix implementation until focused and full suite are green (GREEN).
- [ ] Verify no files are written, no connection/trade credentials are accessed.

## Task 3: Explain and merge

- [ ] Add reproducible command, denominators, interpretation and safety caveats to a separate runbook.
- [ ] Verify fresh full GitHub CI (tests and production build).
- [ ] Merge the PR to `main` and provide PowerShell update + audit command.

**Ruling:** Do not deploy a disagreement veto on a repeatedly inspected 123-market cohort. A safety gate needs prospectively independent outcomes, and skipping all disagreement would necessarily sacrifice some historically correct decisions.
