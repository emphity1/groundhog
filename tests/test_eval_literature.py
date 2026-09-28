# SPDX-License-Identifier: Apache-2.0
"""The OPSSAT-AD paper's seven metrics, against values scikit-learn computes.

The expected values were computed with scikit-learn 1.9.1 (``accuracy_score``,
``precision_score``, ``recall_score``, ``f1_score`` with ``zero_division=0``,
``matthews_corrcoef``, ``roc_auc_score``, ``average_precision_score``) on the same
inputs. scikit-learn is not a dependency; anyone can recompute them.
"""

from __future__ import annotations

import pytest

from groundhog.eval.literature import average_precision, classification, roc_auc

CASES = {
    "the first worked example": (
        [1, 0, 1, 1, 0, 0, 1, 0],
        [1, 0, 0, 1, 1, 0, 1, 0],
        [0.9, 0.1, 0.4, 0.8, 0.7, 0.2, 0.9, 0.4],
        {
            "accuracy": 0.75,
            "precision": 0.75,
            "recall": 0.75,
            "f1": 0.75,
            "mcc": 0.5,
            "auc_roc": 0.90625,
            "auc_pr": 0.9166666666666666,
        },
    ),
    "ties across classes": (
        [0, 1, 0, 1, 1, 0, 0, 0, 1, 0],
        [0, 1, 1, 1, 0, 0, 0, 0, 1, 0],
        [0.5, 0.5, 0.5, 1.0, 0.0, 0.0, 0.5, 0.0, 1.0, 0.5],
        {
            "accuracy": 0.8,
            "precision": 0.75,
            "recall": 0.75,
            "f1": 0.75,
            "mcc": 0.5833333333333334,
            "auc_roc": 0.7083333333333333,
            "auc_pr": 0.7071428571428571,
        },
    ),
    "nothing flagged, every score equal": (
        [0, 1, 0, 0, 1],
        [0, 0, 0, 0, 0],
        [0.0, 0.0, 0.0, 0.0, 0.0],
        {
            "accuracy": 0.6,
            "precision": 0.0,
            "recall": 0.0,
            "f1": 0.0,
            "mcc": 0.0,
            "auc_roc": 0.5,
            "auc_pr": 0.4,
        },
    ),
}


@pytest.mark.parametrize("name", CASES)
def test_the_values_scikit_learn_computes(name: str) -> None:
    labels, flagged, scores, expected = CASES[name]
    result = classification([bool(x) for x in labels], [bool(x) for x in flagged], scores)
    for metric, value in expected.items():
        assert getattr(result, metric) == pytest.approx(value, abs=1e-15), metric


def test_the_confusion_counts() -> None:
    labels, flagged, scores, _ = CASES["the first worked example"]
    result = classification([bool(x) for x in labels], [bool(x) for x in flagged], scores)
    counts = (result.true_positives, result.false_positives)
    assert (*counts, result.true_negatives, result.false_negatives) == (3, 1, 3, 1)


def test_a_perfect_ranking() -> None:
    labels = [False, True, False, True]
    assert roc_auc(labels, [0.1, 0.9, 0.2, 0.8]) == 1.0
    assert average_precision(labels, [0.1, 0.9, 0.2, 0.8]) == 1.0


def test_a_ranking_needs_both_classes() -> None:
    with pytest.raises(ValueError, match="both classes"):
        roc_auc([True, True], [0.1, 0.2])
    with pytest.raises(ValueError, match="both classes"):
        average_precision([False, False], [0.1, 0.2])


def test_lengths_must_agree() -> None:
    with pytest.raises(ValueError, match="one entry per segment"):
        classification([True, False], [True], [0.1, 0.2])
