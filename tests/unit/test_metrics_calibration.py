from orc.metrics.calibration import ConfidenceResult, expected_calibration_error, reliability_bins


def test_reliability_bins_group_by_confidence_decile() -> None:
    # Two claims at ~0.95 (one right), two at ~0.55 (both right).
    results = [
        ConfidenceResult(confidence=0.95, correct=True),
        ConfidenceResult(confidence=0.92, correct=False),
        ConfidenceResult(confidence=0.55, correct=True),
        ConfidenceResult(confidence=0.51, correct=True),
    ]
    bins = reliability_bins(results, n_bins=10)
    top = next(b for b in bins if b.lo <= 0.95 < b.hi or (b.hi == 1.0 and b.lo <= 0.95))
    assert top.count == 2
    assert top.accuracy == 0.5
    assert round(top.mean_confidence, 3) == 0.935


def test_ece_is_weighted_gap_between_confidence_and_accuracy() -> None:
    # Perfectly calibrated: confidence == accuracy in every bin -> ECE 0.
    perfect = [ConfidenceResult(confidence=1.0, correct=True) for _ in range(4)]
    assert expected_calibration_error(reliability_bins(perfect, n_bins=10)) == 0.0
    # Overconfident: conf 1.0 but half wrong -> ECE 0.5.
    over = (
        [ConfidenceResult(confidence=1.0, correct=True) for _ in range(2)]
        + [ConfidenceResult(confidence=1.0, correct=False) for _ in range(2)]
    )
    assert expected_calibration_error(reliability_bins(over, n_bins=10)) == 0.5
