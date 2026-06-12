from orc.metrics.scoring import LabeledResult, confusion, scores


def test_confusion_counts_exact_label_matches() -> None:
    results = [
        LabeledResult(predicted="supported", expected="supported"),
        LabeledResult(predicted="supported", expected="not_found"),
        LabeledResult(predicted="not_found", expected="not_found"),
        LabeledResult(predicted="not_found", expected="supported"),
        LabeledResult(predicted=None, expected="supported"),  # errored, skipped
    ]
    cm = confusion(results, positive="supported")
    assert cm == {"tp": 1, "fp": 1, "tn": 1, "fn": 1}


def test_scores_precision_recall_f1_accuracy() -> None:
    s = scores({"tp": 3, "fp": 1, "tn": 4, "fn": 2})
    assert s["accuracy"] == 0.7
    assert s["precision"] == 0.75
    assert round(s["recall"], 4) == 0.6
    assert round(s["f1"], 4) == 0.6667


def test_scores_empty_is_zero() -> None:
    assert scores({"tp": 0, "fp": 0, "tn": 0, "fn": 0})["f1"] == 0.0
