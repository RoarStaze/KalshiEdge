"""Fail-closed research contract for KXGOLD15M GoldPredict AI V1.

This is a capability/status boundary, not a model implementation. The gold
collection audits do not yet prove an as-of feature dataset or settlement oracle.
Never infer readiness from the presence of inventory or candle probe files.
"""
from __future__ import annotations


def gold_experiment_status() -> dict:
    """Describe explicitly disabled gold capabilities without reading secrets."""
    return {
        "experiment": "GoldPredict AI — Experimental V1",
        "series_ticker": "KXGOLD15M",
        "settlement_source": "Metal.Index.1OZGOLD/USD",
        "stage": "DATA_VALIDATION",
        "readiness": {
            "contract_rules_verified": False,
            "checkpoint_verified": False,
            "settlement_oracle_verified": False,
            "point_in_time_features_verified": False,
            "chronological_lockbox_verified": False,
            "calibration_verified": False,
        },
        "training_enabled": False,
        "predictions_enabled": False,
        "trading_enabled": False,
        "automated_orders_enabled": False,
    }
