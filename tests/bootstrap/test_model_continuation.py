"""Continue the current fitted model without reconstructing all components.

TDD RED: this module is deliberately absent until the failure is observed.
"""
from __future__ import annotations
import copy
from types import SimpleNamespace

import pytest
from kalshi_edge.bootstrap import model_continuation as subject


def synthetic_classification_rows():
    x=[[float(i%11)/5, float((i*7)%9)/4] for i in range(100)]
    y=[int((row[0]*.9-row[1]*.3+(i%3)*.2)>0.5) for i,row in enumerate(x)]
    return x,y


def test_logistic_continuation_preserves_fitted_scaler_and_parent_weights():
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import make_pipeline
    import numpy as np

    x,y=synthetic_classification_rows()
    original=make_pipeline(StandardScaler(),LogisticRegression(max_iter=200)).fit(x[:80],y[:80])
    old_weights=original.named_steps["logisticregression"].coef_.copy()
    old_mean=original.named_steps["standardscaler"].mean_.copy()
    continued=subject.continue_fitted_classifier(original,x[80:],y[80:])
    assert continued is not original
    assert np.array_equal(original.named_steps["logisticregression"].coef_,old_weights)
    assert np.array_equal(continued.named_steps["standardscaler"].mean_,old_mean)
    assert np.array_equal(original.named_steps["standardscaler"].mean_,old_mean)
    assert continued.named_steps["logisticregression"].warm_start
    assert 0 <= continued.predict_proba([[.1,.2]])[0,1] <= 1


def test_histogram_boosting_continuation_adds_estimators_from_old_fitted_state():
    from sklearn.ensemble import HistGradientBoostingClassifier
    x,y=synthetic_classification_rows()
    original=HistGradientBoostingClassifier(max_iter=6,max_leaf_nodes=4,
                                            min_samples_leaf=3,random_state=5).fit(x[:80],y[:80])
    before=original.n_iter_
    continued=subject.continue_fitted_classifier(original,x[80:],y[80:])
    assert original.n_iter_==before
    assert continued.n_iter_>before
    assert continued.warm_start is True
    assert 0<=continued.predict_proba([[.1,.2]])[0,1]<=1


def test_xgb_continuation_adds_only_new_trees_and_does_not_mutate_parent():
    xgboost=pytest.importorskip("xgboost")
    x,y=synthetic_classification_rows()
    original=xgboost.XGBClassifier(
        n_estimators=6,max_depth=2,learning_rate=.1,
        device="cpu",tree_method="hist",n_jobs=1,random_state=3,
    ).fit(x[:80],y[:80])
    before=original.get_booster().num_boosted_rounds()
    continued=subject.continue_fitted_classifier(original,x[80:],y[80:])
    assert original.get_booster().num_boosted_rounds()==before
    assert continued.get_booster().num_boosted_rounds()>before
    assert 0<=continued.predict_proba([[.1,.2]])[0,1]<=1


def test_single_class_training_batch_cannot_corrupt_active_model():
    from sklearn.ensemble import HistGradientBoostingClassifier
    x,y=synthetic_classification_rows()
    original=HistGradientBoostingClassifier(max_iter=5).fit(x,y)
    with pytest.raises(ValueError,match="classes|outcome"):
        subject.continue_fitted_classifier(original,x[:15],[1]*15)


def test_matching_active_model_and_unused_later_markets_are_only_training_inputs():
    from kalshi_edge.bootstrap.types import FeatureRow
    base=2_000_000_000_000_000_000
    def row(i):
        return FeatureRow(
            market_ticker=f"KXBTC15M-TEST-{i}",
            split_group_id=f"KXBTC15M-TEST-{i}",
            checkpoint_ts_ns=base+i*900_000_000_000,
            label_yes=i%2,
            features={"seconds_remaining":480.0,"kalshi_mid":.4,"x":float(i)},
            source_max_ts_ns={"binance":base+i*900_000_000_000-1},
        )
    rows=[row(i) for i in range(5)]
    finals={r.market_ticker:SimpleNamespace(
        market_ticker=r.market_ticker,checkpoint_seconds=480,
        prediction_ts_ns=r.checkpoint_ts_ns,
        model_hash="a"*64 if i<4 else "b"*64,
        p_yes=.6,
        coin_flip_assessment="LOW",
    ) for i,r in enumerate(rows)}
    result=subject.fresh_active_pairs(
        rows,finals,active_hash="a"*64,
        after_ns=rows[0].checkpoint_ts_ns,
        required_features=("x","kalshi_mid"),
    )
    assert [r.market_ticker for r,_ in result]==[rows[i].market_ticker for i in (1,2,3)]


def test_continuation_selection_requires_new_active_model_evidence():
    from kalshi_edge.bootstrap import training_cycle
    assert training_cycle._select_training_mode(
        {"ready":False},{"ready":True},{"ready":True},
        continuation={"ready":False},
    ) is None
    assert training_cycle._select_training_mode(
        {"ready":False},{"ready":True},{"ready":True},
        continuation={"ready":True},
    )==training_cycle.CONTINUE_MODE
