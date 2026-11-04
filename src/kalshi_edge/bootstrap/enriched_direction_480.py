"""Fixed causal four-signal BTC feature challenger for KXBTC15M at 480 seconds.

Offline research only. Compares the same fixed logistic, six-feature
nonlinear, and enriched ten-feature nonlinear models on identical markets.
No model artifact, threshold change, exchange action or AutoTrade changes.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
from typing import Any, Sequence

from .nonlinear_direction_480 import (
    _nonlinear_model, _result_row,
    MIN_DEVELOPMENT, MIN_SCREEN, MIN_CONFIRM, MIN_QUALIFIED,
    FIT_FRACTION, SCREEN_FRACTION,
)
from .reliability_480_core import BASELINE_GATE, DIRECTION_C, classifier
from .specialist_480 import (
    FEATURES, NS, MIN_VALIDATION_MARKETS,
    _eligible_features, select_eight_minute_rows,
)
from .types import FeatureRow

# Fixed before any scoring. All four are computed solely from Binance bars
# completed at or before the checkpoint in bootstrap/features.py.
EXTRA_FEATURES = (
    "btc_return_15s",
    "btc_taker_buy_imbalance_60s",
    "btc_market_high_excursion_bps",
    "btc_market_low_excursion_bps",
)
ENRICHED_FEATURES = FEATURES + EXTRA_FEATURES


def enriched_feature_reason(row: FeatureRow) -> str | None:
    """Validate actual at-checkpoint additional evidence, never impute."""
    if _eligible_features(row) is not None:
        return "invalid_base_features"
    f = row.features
    available = f.get("btc_return_15s_available")
    if (type(available) not in (int,float)
            or not math.isfinite(available) or available < 1):
        return "missing_15s_return"
    for name in EXTRA_FEATURES:
        x = f.get(name)
        if type(x) not in (int,float) or not math.isfinite(x):
            return "missing_or_nonfinite_enriched_feature"
    if not -1 <= f["btc_taker_buy_imbalance_60s"] <= 1:
        return "invalid_btc_order_flow"
    if f["btc_market_low_excursion_bps"] > f["btc_market_high_excursion_bps"]:
        return "invalid_btc_excursion"
    if f["btc_return_15s"] <= -1:
        return "invalid_btc_return"
    return None


def select_enriched_rows(
    rows: Sequence[FeatureRow],
) -> tuple[list[FeatureRow],dict[str,int]]:
    base, exclusions = select_eight_minute_rows(rows)
    counts = Counter(exclusions)
    good = []
    for row in base:
        reason = enriched_feature_reason(row)
        if reason is None:
            good.append(row)
        else:
            counts[reason] += 1
    return good, dict(counts)


def _matrix(rows: Sequence[FeatureRow], names: tuple[str,...]):
    return [[row.features[name] for name in names] for row in rows]


def _groups(rows: Sequence[FeatureRow]) -> set[str]:
    return {row.split_group_id or row.market_ticker for row in rows}


def _joint(candidate:dict[str,Any],other:dict[str,Any]) -> bool:
    c,b = candidate["gate_69"],other["gate_69"]
    return bool(
        c["qualified"] >= MIN_QUALIFIED
        and c["accuracy"] is not None
        and b["accuracy"] is not None
        and c["coverage"] > b["coverage"]
        and c["accuracy"] > b["accuracy"]
        and c["correct_per_market"] > b["correct_per_market"]
    )


def _compare(
    rows: Sequence[FeatureRow], models:dict[str,Any],
) -> dict[str,Any]:
    if not rows:
        raise ValueError("cannot evaluate an empty period")
    y = [r.label_yes for r in rows]
    x6 = _matrix(rows, FEATURES)
    x10 = _matrix(rows, ENRICHED_FEATURES)
    scores = {
        "logistic_6":_result_row(y,[
            float(x) for x in models["logistic_6"].predict_proba(x6)[:,1]
        ]),
        "nonlinear_6":_result_row(y,[
            float(x) for x in models["nonlinear_6"].predict_proba(x6)[:,1]
        ]),
        "nonlinear_enriched_10":_result_row(y,[
            float(x) for x in models["nonlinear_enriched_10"].predict_proba(x10)[:,1]
        ]),
    }
    challenger = scores["nonlinear_enriched_10"]
    return {
        "markets":len(rows),
        **scores,
        "joint_vs_logistic":_joint(challenger,scores["logistic_6"]),
        "joint_vs_nonlinear_6":_joint(challenger,scores["nonlinear_6"]),
        "joint_objective":(
            _joint(challenger,scores["logistic_6"])
            and _joint(challenger,scores["nonlinear_6"])
        ),
        "baseline_logistic_accuracy_delta":(
            challenger["gate_69"]["accuracy"]
            - scores["logistic_6"]["gate_69"]["accuracy"]
            if (challenger["gate_69"]["accuracy"] is not None
                and scores["logistic_6"]["gate_69"]["accuracy"] is not None)
            else None
        ),
        "baseline_logistic_coverage_delta":(
            challenger["gate_69"]["coverage"]-scores["logistic_6"]["gate_69"]["coverage"]
        ),
        "note":"Same markets, same 69% confidence policy; includes ALL incorrect forecasts.",
    }
