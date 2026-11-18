"""Nondeployable comparison: gold XGBoost, temporal lags, and regime clustering.

Models are trained solely on candidate research rows with Kalshi-reported
outcomes and an ASSUMED label-delay proxy. None are eligible for live use,
even if offline metrics appear attractive. No weights are saved or activated.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from pathlib import Path

from .historical_research import inspect_research_dataset, extract_research_rows


class GoldResearchModelError(ValueError):
    """Dataset cannot support an honest exploratory chronological comparison."""


def _day(ts_ns: int) -> str:
    return datetime.fromtimestamp(ts_ns/1_000_000_000, tz=timezone.utc).date().isoformat()


def _split(rows, *, min_train: int, min_test: int):
    if not 2 <= min_train or not 1 <= min_test:
        raise GoldResearchModelError("insufficient model split sizes")
    if not rows:
        raise GoldResearchModelError("no candidate research rows")
    unique = {r.ticker for r in rows}
    if len(unique) != len(rows):
        raise GoldResearchModelError("duplicate market in research rows")
    ordered = sorted(rows, key=lambda r: (r.decision_ns, r.ticker))
    candidates = []
    for day in sorted({_day(r.decision_ns) for r in ordered})[1:]:
        test = [r for r in ordered if _day(r.decision_ns) >= day]
        if len(test) < min_test:
            continue
        start = min(r.decision_ns for r in test)
        train = [r for r in ordered
                 if r.decision_ns < start and r.label_available_proxy_ns < start]
        if len(train) < min_train or set(r.label for r in train) != {0,1}:
            continue
        ratio = len(train)/(len(train)+len(test))
        candidates.append((abs(ratio-.70), train, test))
    if not candidates:
        raise GoldResearchModelError("insufficient purged UTC sessions or label classes")
    _, train, test = min(candidates, key=lambda item:item[0])
    if (max(r.label_available_proxy_ns for r in train) >= min(r.decision_ns for r in test)
            or max(r.decision_ns for r in train) >= min(r.decision_ns for r in test)):
        raise GoldResearchModelError("temporal leakage in research split")
    return train, test


def _metrics(prob, label):
    import numpy as np
    p = np.asarray(prob, dtype=np.float64).reshape(-1)
    truth = np.asarray(label, dtype=np.int8).reshape(-1)
    if len(p) != len(truth) or not len(p) or not np.all(np.isfinite(p)) or np.any((p<0)|(p>1)):
        raise GoldResearchModelError("invalid forecast probabilities")
    clipped = np.clip(p, 1e-12, 1 - 1e-12)
    confidence = np.maximum(p, 1-p)
    qualified = confidence > .69
    predicted = (p >= .5)
    return {
        "accuracy": float(np.mean(predicted == truth)),
        "brier": float(np.mean((p-truth)**2)),
        "log_loss": float(np.mean(-(truth*np.log(clipped)+(1-truth)*np.log(1-clipped)))),
        "coverage_69": float(np.mean(qualified)),
        "qualifying_count": int(np.sum(qualified)),
        "qualified_accuracy": float(np.mean((predicted == truth)[qualified]))
            if np.any(qualified) else None,
    }


def compare_research_models(
    dataset_path: Path, *, min_train: int = 100, min_test: int = 30,
) -> dict:
    """Compare honest baseline vs lagged, XGBoost and train-only regime contexts.

    This does not train a large language model or a foundation model.
    Optional FinBERT-based context must have been captured and scored *as of*
    decision time; no fabricated zero-sentiment feature is treated as evidence.
    """
    doc = inspect_research_dataset(Path(dataset_path))
    train, test = _split(
        extract_research_rows(doc), min_train=min_train, min_test=min_test,
    )
    try:
        import numpy as np
        from sklearn.cluster import KMeans
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
        from xgboost import XGBClassifier
    except ImportError as exc:
        raise GoldResearchModelError(
            "optional research dependencies required (pip install -e '.[research]')"
        ) from exc
    y_train = np.asarray([r.label for r in train], dtype=np.int8)
    y_test = np.asarray([r.label for r in test], dtype=np.int8)
    x_train = np.asarray([r.features for r in train], dtype=np.float32)
    x_test = np.asarray([r.features for r in test], dtype=np.float32)
    lag_train = np.asarray([r.lag_returns_bps for r in train], dtype=np.float32)
    lag_test = np.asarray([r.lag_returns_bps for r in test], dtype=np.float32)

    def xgb(x, xt):
        model = XGBClassifier(
            n_estimators=60, max_depth=2, learning_rate=.04,
            subsample=1, colsample_bytree=1,
            tree_method="hist", objective="binary:logistic",
            eval_metric="logloss", random_state=37, n_jobs=1,
        )
        model.fit(x, y_train)
        return model.predict_proba(xt)[:, 1]

    prevalence = np.full(len(test), float(np.mean(y_train)), dtype=np.float64)
    time_series = make_pipeline(
        StandardScaler(),
        LogisticRegression(max_iter=200, C=.1, random_state=37),
    )
    time_series.fit(lag_train, y_train)

    # Fit unsupervised regime clusters on training rows only. Only the training
    # regime scaler/centers are applied to holdout; no hindsight thresholds.
    regime_columns = (3,6,4)  # gold_ret_10m, vol_20m, ret_20m
    scaler = StandardScaler().fit(x_train[:, regime_columns])
    normalized_train = scaler.transform(x_train[:, regime_columns])
    normalized_test = scaler.transform(x_test[:, regime_columns])
    clusters = KMeans(n_clusters=3, n_init=10, random_state=37)
    train_cluster = clusters.fit_predict(normalized_train)
    test_cluster = clusters.predict(normalized_test)
    one_hot_train = np.eye(3)[train_cluster]
    one_hot_test = np.eye(3)[test_cluster]
    augmented_train = np.hstack([x_train, one_hot_train]).astype(np.float32)
    augmented_test = np.hstack([x_test, one_hot_test]).astype(np.float32)

    models = {
        "historical_prevalence": _metrics(prevalence, y_test),
        "xgboost": _metrics(xgb(x_train, x_test), y_test),
        "lagged_time_series": _metrics(time_series.predict_proba(lag_test)[:, 1], y_test),
        "regime_aware": _metrics(xgb(augmented_train, augmented_test), y_test),
    }
    macro_flag = any(r.macro_count > 0 for r in train + test)
    if macro_flag:
        # Missing sentiment is a separate indicator, not assumed neutral news.
        def macro_matrix(rows):
            return np.asarray([
                [r.macro_sentiment if r.macro_sentiment is not None else 0.,
                 1. if r.macro_count > 0 else 0.] for r in rows
            ], dtype=np.float32)
        x_macro = np.hstack([augmented_train, macro_matrix(train)])
        t_macro = np.hstack([augmented_test, macro_matrix(test)])
        models["regime_with_asof_macro"] = _metrics(xgb(x_macro, t_macro), y_test)
    return {
        "experiment": "GOLD_V14_HISTORICAL_FOUR_COMPONENT_RESEARCH",
        "research_only": True, "deployable": False,
        "trading_enabled": False, "models_saved": False,
        "label_status": "KALSHI_REPORTED_UNVERIFIED",
        "feature_source": "UNVERIFIED_PYTH_HISTORICAL_OHLC",
        "assumed_checkpoint_seconds": 480,
        "assumed_label_delay_hours": 24,
        "features_include_future_labels": False,
        "macro_component": ("ASOF_SENTIMENT_RESEARCH_UNVERIFIED_PROVENANCE"
                            if macro_flag else "DISABLED_NO_VERIFIED_ASOF_NEWS"),
        "time_series_component": "TRAIN_ONLY_LAGGED_LOGISTIC_REGRESSION",
        "regime_component": "TRAIN_ONLY_KMEANS_3_CLUSTERS",
        "train_count": len(train), "test_count": len(test),
        "train_max_label_available_ns": max(r.label_available_proxy_ns for r in train),
        "test_start_ns": min(r.decision_ns for r in test),
        "models": models,
        "note": "Retrospective, provenance-limited research; not validated Pyth settlement or executable P/L.",
    }
