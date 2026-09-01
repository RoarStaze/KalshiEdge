from __future__ import annotations

from kalshi_edge.bootstrap import calibration


def test_small_support_forces_platt_and_predictions_are_bounded_monotone() -> None:
    probabilities = [0.1, 0.2, 0.3, 0.4, 0.6, 0.7, 0.8, 0.9, 0.15, 0.85, 0.25, 0.75]
    labels = [0, 0, 0, 0, 1, 1, 1, 1, 0, 1, 0, 1]

    fitted = calibration.fit_calibrator(probabilities, labels)
    transformed = fitted.predict([0.05, 0.25, 0.5, 0.75, 0.95])

    assert fitted.method == "platt"
    assert fitted.selection.isotonic_eligible is False
    assert all(0.0 <= value <= 1.0 for value in transformed)
    assert transformed == sorted(transformed)


def test_later_selection_block_controls_method_choice_and_is_reproducible() -> None:
    probabilities = [index / 100.0 for index in range(5, 95)]
    labels = [0 if value < 0.45 else 1 for value in probabilities]

    first = calibration.fit_calibrator(probabilities, labels)
    second = calibration.fit_calibrator(probabilities, labels)

    assert first.method in {"platt", "isotonic"}
    assert first.selection.selection_count > 0
    assert first.selection.fit_count > first.selection.selection_count
    assert first.selection == second.selection
    assert first.predict([0.2, 0.5, 0.8]) == second.predict([0.2, 0.5, 0.8])


def test_calibrator_rejects_single_class_block() -> None:
    try:
        calibration.fit_calibrator([0.1, 0.2, 0.3, 0.4], [0, 0, 0, 0])
    except calibration.CalibrationError as exc:
        assert "both classes" in str(exc)
    else:
        raise AssertionError("single-class calibration block must fail")
