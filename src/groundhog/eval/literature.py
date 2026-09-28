# SPDX-License-Identifier: Apache-2.0
"""The seven metrics of the OPSSAT-AD paper, for the comparison with the literature.

The paper scores its baselines per segment with scikit-learn: ``accuracy_score``,
``precision_score``, ``recall_score``, ``f1_score``, ``matthews_corrcoef``,
``roc_auc_score`` (its AUC_ROC) and ``average_precision_score`` (its AUC_PR). This
module computes the same definitions, following scikit-learn's arithmetic, without
the dependency. The tests pin values scikit-learn computes on the same inputs.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import groupby

__all__ = ["Classification", "average_precision", "classification", "roc_auc"]


@dataclass(frozen=True)
class Classification:
    true_positives: int
    false_positives: int
    true_negatives: int
    false_negatives: int
    accuracy: float
    precision: float
    recall: float
    f1: float
    mcc: float
    auc_roc: float
    auc_pr: float


def classification(
    labels: Sequence[bool], flagged: Sequence[bool], scores: Sequence[float]
) -> Classification:
    """``labels`` the truth, ``flagged`` the decisions and ``scores`` the graded
    output, one per segment. Undefined ratios are 0, as scikit-learn returns them."""
    if not len(labels) == len(flagged) == len(scores):
        raise ValueError("labels, decisions and scores must have one entry per segment")
    tp = sum(t and f for t, f in zip(labels, flagged, strict=True))
    fp = sum(f and not t for t, f in zip(labels, flagged, strict=True))
    fn = sum(t and not f for t, f in zip(labels, flagged, strict=True))
    tn = len(labels) - tp - fp - fn
    return Classification(
        true_positives=tp,
        false_positives=fp,
        true_negatives=tn,
        false_negatives=fn,
        accuracy=(tp + tn) / len(labels),
        precision=tp / (tp + fp) if tp + fp else 0.0,
        recall=tp / (tp + fn) if tp + fn else 0.0,
        f1=2 * tp / (2 * tp + fp + fn) if tp + fp + fn else 0.0,
        mcc=_mcc(tp, fp, tn, fn),
        auc_roc=roc_auc(labels, scores),
        auc_pr=average_precision(labels, scores),
    )


def _mcc(tp: int, fp: int, tn: int, fn: int) -> float:
    """``matthews_corrcoef``: its covariance form, 0 when a denominator term is."""
    n = tp + fp + tn + fn
    true_sums, flagged_sums = (tn + fp, fn + tp), (tn + fn, fp + tp)
    cov_true_flagged = (tp + tn) * n - sum(
        t * f for t, f in zip(true_sums, flagged_sums, strict=True)
    )
    cov_flagged = n * n - sum(f * f for f in flagged_sums)
    cov_true = n * n - sum(t * t for t in true_sums)
    if cov_flagged * cov_true == 0:
        return 0.0
    return cov_true_flagged / math.sqrt(cov_true * cov_flagged)


def _curve(labels: Sequence[bool], scores: Sequence[float]) -> list[tuple[int, int]]:
    """(true positives, false positives) flagged at each distinct score, highest first."""
    if len(labels) != len(scores):
        raise ValueError("labels and scores must have one entry per segment")
    if all(labels) or not any(labels):
        raise ValueError("both classes must be present")
    points = []
    tp = fp = 0
    ranked = sorted(zip(scores, labels, strict=True), key=lambda pair: pair[0], reverse=True)
    for _, tied in groupby(ranked, key=lambda pair: pair[0]):
        for _, label in tied:
            tp += label
            fp += not label
        points.append((tp, fp))
    return points


def average_precision(labels: Sequence[bool], scores: Sequence[float]) -> float:
    """``average_precision_score``: the sum over thresholds of the recall gained times
    the precision there, with no interpolation."""
    points = _curve(labels, scores)
    positives = points[-1][0]
    total = 0.0
    previous_recall = 0.0
    for tp, fp in points:
        recall = tp / positives
        total += (recall - previous_recall) * (tp / (tp + fp))
        previous_recall = recall
    return total


def roc_auc(labels: Sequence[bool], scores: Sequence[float]) -> float:
    """``roc_auc_score``: the trapezoidal area under the ROC curve, ties counted half."""
    points = _curve(labels, scores)
    positives, negatives = points[-1]
    area = 0.0
    previous = (0.0, 0.0)
    for tp, fp in points:
        fpr, tpr = fp / negatives, tp / positives
        area += (fpr - previous[0]) * (tpr + previous[1]) / 2
        previous = (fpr, tpr)
    return area
