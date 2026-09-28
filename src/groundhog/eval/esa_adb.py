# SPDX-License-Identifier: Apache-2.0
"""ESA-ADB metrics, exactly as the benchmark's reference implementation computes them.

Source: arXiv:2406.17826 and the reference code vendored, unchanged, under
third_party/esa_adb/ (commit 67194d3). This is a port, not a reinterpretation: the
tests feed both the same inputs and require the same numbers. Where the reference
has a quirk, it is kept, and a comment says so. Up to floating-point summation
order, the results agree to within 1e-12.

Inputs follow the reference:

* A detector's output is a series of ``(timestamp, flagged)`` points per channel.
  A flagged point covers ``[t_i, t_i+1)``, and the series is extended with its
  first and last values to the full evaluated range.
* Annotations are closed intervals ``[start, end]`` with an id; one id may have
  several fragments.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from groundhog.eval.intervals import Atomic, Span, to_ns

__all__ = [
    "Annotation",
    "ChannelAware",
    "EventWise",
    "Timing",
    "adtqc",
    "channel_aware",
    "detections",
    "event_series",
    "event_wise",
    "logical_sum",
]

Series = Sequence[tuple[int, bool]]  # (nanoseconds since the epoch, flagged)
FullRange = tuple[int, int]  # the evaluated range, closed, in nanoseconds

_POINT_ANOMALY_NS = 1_000_000  # the reference widens point anomalies by 1 ms for CA-F


@dataclass(frozen=True)
class Annotation:
    """One annotated fragment. Fragments sharing an id are one anomaly."""

    id: str
    channel: str
    start: datetime
    end: datetime


@dataclass(frozen=True)
class EventWise:
    true_positives: int
    false_positives: int
    false_negatives: int
    redundant: int
    """Detections beyond the first on the same annotated fragment (``TP_r``)."""
    precision: float
    """Corrected event-wise precision: raw precision times the time-wise TNR."""
    tnr: float | None
    """None when the raw precision is 0 and the reference applies no correction."""
    recall: float
    f_beta: float
    alarming_precision: float


@dataclass(frozen=True)
class Timing:
    """ADTQC over the anomalies with at least one detection."""

    score: float | None
    """Mean timing quality; None when no anomaly was detected (NaN in the reference)."""
    before: int
    after: int
    after_rate: float | None
    latencies: tuple[tuple[str, float, float], ...]
    """Per detected anomaly: id, detection start minus anomaly start (s), quality."""


@dataclass(frozen=True)
class ChannelAware:
    precision: float | None
    recall: float | None
    f_beta: float | None
    """Means over anomalies; None when there are none (NaN in the reference)."""


def detections(series: Series, full_range: FullRange) -> Span:
    """``convert_time_series_to_events``, after the full-range extension the metrics apply."""
    points = list(series)
    if not points:
        raise ValueError("a detection series needs at least one point")
    if points[0][0] > full_range[0]:
        points.insert(0, (full_range[0], points[0][1]))
    if points[-1][0] < full_range[1]:
        points.append((full_range[1], points[-1][1]))
    count = len(points)
    atomics: list[Atomic] = []
    start = 0
    while start < count:
        end = start
        while end < count and points[end][1] == points[start][1]:
            end += 1
        if points[start][1]:
            if end == count:  # a run to the last point is closed
                atomics.append(Atomic(points[start][0], points[end - 1][0], True, True))
            else:
                atomics.append(Atomic(points[start][0], points[end][0], True, False))
        start = end
    return Span(*atomics)


def event_series(
    intervals: Iterable[tuple[datetime, datetime | None]], full_range: FullRange
) -> list[tuple[int, bool]]:
    """Groundhog events of one channel as a detection series (ADR 0009).

    An event covers ``[t_start, t_end]``: flagged from ``t_start``, cleared one
    nanosecond after ``t_end``. An event still open runs to the end of the range.
    """
    points: list[tuple[int, bool]] = [(full_range[0], False)]
    for start, end in sorted(intervals, key=lambda interval: interval[0]):
        points.append((to_ns(start), True))
        if end is not None:
            points.append((to_ns(end) + 1, False))
    return points


def logical_sum(series_by_channel: Mapping[str, Series]) -> list[tuple[int, bool]]:
    """The OR of the channels' detection series, on the union of their timestamps.

    Before its first point a channel holds its first value, as the metrics extend it.
    """
    if not all(series_by_channel.values()):
        raise ValueError("a detection series needs at least one point")
    moments = sorted({t for series in series_by_channel.values() for t, _ in series})
    cursors = dict.fromkeys(series_by_channel, 0)
    values = {channel: series[0][1] for channel, series in series_by_channel.items()}
    combined = []
    for moment in moments:
        for channel, series in series_by_channel.items():
            i = cursors[channel]
            while i < len(series) and series[i][0] <= moment:
                values[channel] = series[i][1]
                i += 1
            cursors[channel] = i
        combined.append((moment, any(values.values())))
    return combined


def _ids(annotations: Sequence[Annotation]) -> list[str]:
    return list(dict.fromkeys(a.id for a in annotations))  # order of first appearance


def _closed(annotations: Iterable[Annotation], widen_points: bool = False) -> Span:
    atomics = []
    for a in annotations:
        start, end = to_ns(a.start), to_ns(a.end)
        if widen_points and start == end:
            end = start + _POINT_ANOMALY_NS
        atomics.append(Atomic(start, end, True, True))
    return Span(*atomics)


def _check_range(annotations: Sequence[Annotation], full_range: FullRange) -> None:
    for a in annotations:
        if to_ns(a.start) < full_range[0] or to_ns(a.end) > full_range[1]:
            raise ValueError(f"annotation {a.id} lies outside the evaluated range")


def event_wise(
    annotations: Sequence[Annotation], detected: Span, full_range: FullRange, beta: float
) -> EventWise:
    """Corrected event-wise precision, recall and F-beta, and alarming precision.

    ``ESAScores.score`` without the affiliation-based part, which Groundhog does
    not report yet.
    """
    _check_range(annotations, full_range)
    tp = fp = fn = redundant = 0
    predicted = detected.atomics
    matched = [False] * len(predicted)
    for aid in _ids(annotations):
        fragments = _closed(a for a in annotations if a.id == aid).atomics
        hits = [0] * len(fragments)
        detected_once = False
        for p, prediction in enumerate(predicted):
            touches = [not (prediction & fragment).empty for fragment in fragments]
            if not any(touches):
                continue
            matched[p] = True
            if not detected_once:
                tp += 1
                detected_once = True
            for i, touched in enumerate(touches):
                hits[i] += touched
        redundant += sum(h - 1 for h in hits if h > 1)
        if not detected_once:
            fn += 1

    everything = _closed(annotations)
    for prediction, was_matched in zip(predicted, matched, strict=True):
        if not was_matched and (Span(prediction) & everything).empty:
            fp += 1

    precision = tp / (tp + fp) if tp + fp else 0.0
    alarming = tp / (tp + redundant) if tp + redundant else 0.0
    tnr = None
    if precision > 0:  # Sehili & Zhang's correction, only when there is something to correct
        nominal = Span.closed(*full_range) - everything
        if nominal.seconds() == 0:  # the reference divides by zero here
            raise ValueError("no nominal time in the evaluated range: the TNR is undefined")
        false_alarm_time = nominal & detected
        tnr = 1 - false_alarm_time.seconds() / nominal.seconds()
        precision *= tnr
    recall = tp / (tp + fn) if tp + fn else 0.0
    divider = beta**2 * precision + recall
    f_beta = ((1 + beta**2) * precision * recall) / divider if divider else 0.0
    return EventWise(tp, fp, fn, redundant, precision, tnr, recall, f_beta, alarming)


def _timing_quality(latency: int, alpha: int, beta: int, exponent: float) -> float:
    """The ADTQC curve, equation 5 of the paper, on nanoseconds."""
    if (alpha == 0 or beta == 0) and latency == 0:
        return 1.0
    if latency <= -alpha or latency >= beta:
        return 0.0
    if latency <= 0:
        return float(((latency + alpha) / alpha) ** exponent)
    return float(1.0 / (1.0 + (latency / (beta - latency)) ** exponent))


def adtqc(
    annotations: Sequence[Annotation],
    detected: Mapping[str, Span],
    full_range: FullRange,
    exponent: float,
) -> Timing:
    """Anomaly detection timing quality (``ADTQC.score``).

    Detections are matched channel by channel. Timing is taken from the earliest
    start among the detections that touch the anomaly.
    """
    _check_range(annotations, full_range)
    ids = _ids(annotations)
    starts = sorted(min(to_ns(a.start) for a in annotations if a.id == aid) for aid in ids)
    before: list[float] = []
    after: list[float] = []
    latencies: list[tuple[str, float, float]] = []
    for aid in ids:
        rows = [a for a in annotations if a.id == aid]
        per_channel = {
            c: _closed(a for a in rows if a.channel == c) for c in sorted({a.channel for a in rows})
        }
        touching = [
            prediction
            for channel, truth in per_channel.items()
            for prediction in detected[channel]
            if not (Span(prediction) & truth).empty
        ]
        predictions = Span(*touching)
        if predictions.empty:  # no detection, no timing
            continue
        truth = Span.union(per_channel.values())
        length = truth.upper - truth.lower
        # list.index finds the first anomaly with this start time: the reference's choice.
        position = starts.index(truth.lower)
        previous = starts[position - 1] if position > 0 else truth.lower - length
        alpha = min(length, truth.lower - previous)
        latency = predictions.lower - truth.lower
        quality = _timing_quality(latency, alpha, length, exponent)
        (before if latency < 0 else after).append(quality)
        latencies.append((aid, latency / 1e9, quality))
    scores = [quality for _, _, quality in latencies]  # in anomaly order, as the reference
    return Timing(
        score=sum(scores) / len(scores) if scores else None,
        before=len(before),
        after=len(after),
        after_rate=len(after) / len(scores) if scores else None,
        latencies=tuple(latencies),
    )


def channel_aware(
    annotations: Sequence[Annotation],
    detected: Mapping[str, Span],
    full_range: FullRange,
    beta: float,
) -> ChannelAware:
    """Channel-aware precision, recall and F-beta (``ChannelAwareFScore.score``).

    For every anomaly, each channel is a true positive if annotated and detected
    within the anomaly's full time span, a false negative if annotated but not
    detected, and a false positive if detected but not annotated, unless that
    detection belongs to another anomaly on the same channel. Channels are those of
    ``detected``; annotations on any other channel are ignored, as in the reference.
    """
    _check_range(annotations, full_range)
    channels = list(detected)
    ids = _ids(annotations)
    spans = {
        aid: {
            c: _closed((a for a in annotations if a.id == aid and a.channel == c), True)
            for c in channels
        }
        for aid in ids
    }
    precisions, recalls, scores = [], [], []
    for aid in ids:
        full = Span.union(spans[aid].values())
        tp = fp = fn = 0
        for channel in channels:
            affected = not spans[aid][channel].empty
            detection = full & detected[channel]
            if affected and not detection.empty:
                tp += 1
            elif affected:
                fn += 1
            elif not detection.empty and not any(
                not (detection & spans[other][channel]).empty
                for other in ids
                if other != aid and not spans[other][channel].empty
            ):
                fp += 1
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        divider = beta**2 * precision + recall
        precisions.append(precision)
        recalls.append(recall)
        scores.append(((1 + beta**2) * precision * recall) / divider if divider else 0.0)
    if not ids:
        return ChannelAware(None, None, None)
    return ChannelAware(
        precision=sum(precisions) / len(ids),
        recall=sum(recalls) / len(ids),
        f_beta=sum(scores) / len(ids),
    )
