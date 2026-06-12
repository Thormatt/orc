from orc.eval.calibrate import sweep_threshold
from orc.metrics.calibration import ConfidenceResult


def test_sweep_finds_lowest_threshold_meeting_target() -> None:
    # Lowest threshold that still hits the accuracy target accepts the most at
    # Tier 1 (minimal escalation). At >=0.80 accepted accuracy is 1.0.
    results = [
        ConfidenceResult(0.99, True),
        ConfidenceResult(0.98, True),
        ConfidenceResult(0.80, True),
        ConfidenceResult(0.79, False),
    ]
    r = sweep_threshold(results, target=0.95)
    assert r.achievable is True
    assert r.threshold == 0.80
    assert r.accepted_accuracy == 1.0
    assert r.escalation_rate == 0.25  # only the 0.79 item falls below 0.80


def test_sweep_reports_unachievable_target() -> None:
    # The top-confidence item is wrong, so no cutoff reaches 0.95.
    results = [ConfidenceResult(0.99, False), ConfidenceResult(0.98, True)]
    r = sweep_threshold(results, target=0.95)
    assert r.achievable is False
    assert r.max_accuracy == 0.5  # best accepted subset accuracy


def test_sweep_empty_results_is_unachievable() -> None:
    r = sweep_threshold([], target=0.95)
    assert r.achievable is False
    assert r.escalation_rate == 0.0
