# Settlement-native KalshiEdge predictor challenger — design

**Goal:** Introduce an independent, causal, prospective 480-second settlement probability challenger that models the 60-second BRTI closing-average outcome. Add verifiable paired evidence without changing active forecasts, orders, or the confidence gate.

**Current state:** KalshiEdge already has V5 promoted predictions, BRTI data, a near-strike risk estimate, and several other research shadows. It does not yet record a dedicated probability forecast based on the mean of the final BRTI minute with immutable post-final evidence and a comparable post-settlement scorecard.

**Settlement definition:** Kalshi's October 2026 crypto-market explanation identifies the 15-minute outcome as comparing the closing-minute BRTI average with the opening-minute BRTI average; the actual per-contract rules control. The local market strike is treated as the verified opening-window reference. No single last tick is substituted for the 60-second closing average.

**Approach:** Use a predeclared zero-drift log-BRTI diffusion as a baseline research challenger. Its final-minute average has approximate log variance sigma²*(T - 2*w/3), where sigma is strictly causal per-sqrt-second BRTI realized volatility, T is remaining time and w=60 seconds. With 480 seconds remaining this is sigma²*440, compared to sigma²*480 for a single closing tick. This is an analytic approximation for the arithmetic price average, not a guaranteed calibrated probability or an assumption that returns are Gaussian.

**Evidence and safety:** Compute only from healthy same-checkpoint BRTI, source timestamps and causal sigma, after saving the immutable final decision. Store a hash-protected, create-once shadow record bound to the exact final-forecast file's SHA-256. Missing or stale inputs produce an explicit unavailable record, never a fabricated probability. An offline CLI joins these records with separately verified official settlements to report Brier, log loss, direction/69%-gate metrics and missing-evidence counts on the same matched markets.

**Non-goals:** No retroactive backfill of missing 480-second index observations, no outcome labels in the live record, no automatic recalibration or promotion, no AutoTrade changes, no claims of profitability from quotes. The existing model remains authoritative until future prospective, untouched evidence and execution-cost criteria pass.

**Next independent phases:** Evaluate on newly settled markets; compare the challenger and incumbent on paired results; design carefully separated regime specialists and calibration only after enough untouched evidence; promote only after independently approved objective gates.
