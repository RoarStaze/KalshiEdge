"""Regression checks for the Trade-page probability semantics.

The hero must show the model's directional probability, not the rescaled
strength field. This is a display correction, not a new calibrated model.
"""
from kalshi_edge.bootstrap.dashboard_ui import PAGE
from kalshi_edge.bootstrap.final_forecast import MINIMUM_FINAL_CONFIDENCE


def test_trade_hero_labels_directional_probability_without_accuracy_claim() -> None:
    assert '<div class="confidence-label">Forecast probability</div>' in PAGE
    assert 'aria-label="Directional probability indicator"' in PAGE
    assert "Probability of the predicted direction" in PAGE
    assert "Model confidence</div>" not in PAGE


def test_trade_hero_uses_locked_or_live_directional_probability_not_strength() -> None:
    assert "const sideProbability=locked?f.directional_probability:" in PAGE
    assert 'text("confidence",Number.isFinite(sideProbability)?Math.round(sideProbability*100)+"%":"—");' in PAGE
    assert 'Math.max(0,sideProbability*100)' in PAGE
    assert 'text("detail-strength",pct(strength));' in PAGE
    assert 'text("confidence",Number.isFinite(strength)?' not in PAGE


def test_trade_copy_explains_actual_63pct_strength_gate_and_coinflip_veto() -> None:
    assert MINIMUM_FINAL_CONFIDENCE == 0.63
    assert "81.5% predicted-side probability" in PAGE
    assert "69% produce SKIP" not in PAGE
    assert "near-strike" in PAGE
    assert "not historical accuracy" in PAGE
