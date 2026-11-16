"""Synthetic-only local fit verification; never interprets fixtures as trading skill."""
from __future__ import annotations

from types import SimpleNamespace

from kalshi_edge.bootstrap import live, signal_fusion_480 as sf


def _rows(*, perfect_base: bool) -> list[sf._Example]:
    rows = []
    first = 1_800_000_000_000_000_000
    for i in range(600):
        # Chronological deterministic class alternation; no random split.
        actual = int(i % 4 in (0, 1))
        positive = 1 if actual else -1
        model_p = (.99 if actual else .01) if perfect_base else (.51 if actual else .49)
        features = {
            "brti_distance_to_strike_bps": 25.0 * positive,
            "brti_binance_gap_bps": 1.2 * positive,
            "btc_return_15s": .001 * positive,
            "btc_return_15s_available": 1.,
            "btc_return_60s": .0015 * positive,
            "btc_return_60s_available": 1.,
            "btc_taker_buy_imbalance_60s": .6 * positive,
            "btc_base_volume_60s": 40.,
            "kalshi_yes_taker_imbalance_60s": .5 * positive,
        }
        vector = sf.build_signal_vector(model_p, features, risk_z=2.5)
        assert vector is not None
        ts = first + i * 900 * sf.NS if hasattr(sf, "NS") else first + i * 900_000_000_000
        rows.append(sf._Example(
            ticker=f"KXBTC15M-TEST-{i}",
            checkpoint_ts_ns=ts,
            fetched_ts_ns=ts + 600_000_000_000,
            probability=model_p,
            risk_z=2.5,
            vector=vector,
            actual_yes=actual,
        ))
    return rows


def _monkeypatch_snapshot_source(monkeypatch, rows):
    model_hash = "a" * 64
    monkeypatch.setattr(live, "load_default_bundle", lambda root: SimpleNamespace(bundle_sha256=model_hash))
    monkeypatch.setattr(sf, "_verified_examples", lambda root, sha: (
        rows,
        {"same_model_finals": len(rows), "eligible": len(rows), "missing_or_invalid": 0},
    ))
    return model_hash


def test_local_fitting_actually_persists_frozen_coefficients_after_paired_holdout(monkeypatch, tmp_path):
    model_hash = _monkeypatch_snapshot_source(monkeypatch, _rows(perfect_base=False))
    result = sf.train_verified_fusion(tmp_path, deploy=True)
    assert result["status"] == "DEPLOYED_RESTART_PREDICTOR"
    assert result["deployed"] is True
    assert result["brier_improvement"] > 0
    assert result["logloss_improvement"] > 0
    model = sf.load_active_fusion(tmp_path, model_hash)
    assert model is not None
    assert model.fit_markets >= 250
    assert model.evaluation_markets == sf.EVAL_MARKETS


def test_refuses_activation_when_original_model_already_beats_fusion(monkeypatch, tmp_path):
    model_hash = _monkeypatch_snapshot_source(monkeypatch, _rows(perfect_base=True))
    result = sf.train_verified_fusion(tmp_path, deploy=True)
    assert result["deployed"] is False
    assert result["status"] in {"HOLDOUT_NOT_BEATEN", "UNRELIABLE_BASE_MODEL_COEFFICIENT"}
    assert sf.load_active_fusion(tmp_path, model_hash) is None
