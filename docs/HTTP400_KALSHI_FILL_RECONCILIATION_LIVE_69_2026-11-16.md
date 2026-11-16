# Repair local NOT_SUBMITTED versus Kalshi filled orders; activate 69% probability gate

## Incident / evidence

On November 16, 2026 the user showed:

- Kalshi's own portfolio history lists a BTC 15-minute NO position with **$3.00 cost**, **$0 payout**, and **-$3.00 realized return**.
- KalshiEdge AUTO displayed the corresponding-looking DOWN entry as **SKIPPED / FINAL BUY NOT_SUBMITTED**, and the live status message said **Kalshi rejected order HTTP 400**.
- These screenshots establish a real realized loss and an inconsistency in displayed execution history. They do **not** prove whether the accepted order belongs to the precise attempted client order ID. An unrelated manual, earlier, or retried submission in the same market cannot be attributed without exchange order identity.

**Safety:** Stop / disarm AutoTrade until the user's actual persisted client IDs can be reconciled. Do not automatically POST another order in the same market after an HTTP 400 or lost response.

## Fix 1: evidence-first execution and realized P/L

- `ManualTrader._place`: an exchange HTTP 400 returned **after POST** no longer proves that the order definitely failed. Propagate uncertainty; journal `UNKNOWN`, never automatic retry.
- `autotrade_pnl.enrich_history`: before interpreting a saved `NOT_SUBMITTED` / `UNKNOWN` stage, make a **read-only** authenticated `GET /portfolio/orders`. A recovery is allowed only if **exactly one** exchange order matches the stage's pre-POST `client_order_id`, full market ticker, book side (UP=bid / DOWN=ask), positive executed size, bounded initial size and original quoted size. Do not infer fills by matching market ticker or direction alone.
- A recovered order is reflected **in memory** as filled/partial, not written over the immutable local journal. P/L is shown only after separate independent `GET /portfolio/settlements` and `GET /portfolio/fills` evidence has passed the current fee, quantity, side and order-ID cross-checks. Mixed manual fills are not attributed to AutoTrade.
- If exchange history is unreachable/incomplete, or the order ID does not match, the UI displays **VERIFY KALSHI** / **execution unverified** rather than falsely claiming `SKIPPED`, `NO ORDER`, `FILLED`, or `$0` realized P/L.
- The existing AUTO journal is not deleted or rewritten; existing stage/order-id uniqueness and no-repeat behavior remain in place.

**Limitations:** `GET /portfolio/orders` can have a bounded history/page window. Older orders missing a persisted `client_order_id`, offloaded historical records, or unrelated fills cannot be recovered safely. A pending external fill is never a verified profit without an independent settlement/fee record.

## Fix 2: 69% everywhere the *active* policy matters

- Strict `predicted-side probability > 69%` in the new locked eight-minute final, and AutoTrade must use that same number (equality is SKIP).
- This is **not** the historical normalized strength `2*abs(p_yes-0.5)` gate; new forward research, training lockbox promotion, and paired model comparisons explicitly use predicted-side probability, not a misleading model-strength label.
- Trade, Train and AUTO text display the correct **69%** active probability rule.
- Existing saved 63% forecasts and archived 63%/69%-strength research reports stay unchanged, including the originally frozen 63% validation manifest. Old reasons remain recognized when read historically.
- The new future 69% research baseline is a different `frozen_69_baseline.json` file with a new schema; it requires a **separate one-time** registration by the user and cannot be manufactured from the sealed 63% history.

## Windows commands (after CI & GitHub merge)

```powershell
# FIRST: disarm AutoTrade and stop/restart the local application.
cd "$HOME\Desktop\KalshiEdge"
git status --short
git switch main
git pull --ff-only origin main
$py = ".\.venv\Scripts\python.exe"

# Confirm local regression tests use simulation only; no real Kalshi POST:
& $py -m pytest -q tests/trading/test_http400_reconcile_and_live69.py

# Register one NEW forward 69% cohort. Does NOT overwrite prior 63% study:
& $py -m kalshi_edge.bootstrap.prospective_error_monitor_480 --freeze --json

# Future monitoring (read-only):
& $py -m kalshi_edge.bootstrap.prospective_error_monitor_480
```

Open **AUTO > AutoTrade History** after restarting and after the account GETs succeed. The historical loss should be visible if the local journal and authenticated Kalshi records contain matching persisted order identities. If the exact exchange evidence cannot be matched, KalshiEdge must say **unverified** rather than fabricate a fill or a zero loss.

Maintain local `src/kalshi_edge.egg-info/*` changes, untracked `.venv-timesfm3/` and backups; avoid `git reset --hard` or `git clean`. There is no full model retraining and no automatic fund movement from these commands.

**These changes improve execution accounting and the stated selection policy. They do not demonstrate that a 69% probability cutoff produces profit or improved expected value.**
