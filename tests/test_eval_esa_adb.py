# SPDX-License-Identifier: Apache-2.0
"""Groundhog's ESA-ADB metrics against the reference implementation, number for number.

The reference lives unchanged in third_party/esa_adb. Two of its imports are
replaced here with stand-ins: ``sklearn.utils`` (input validation the metrics'
``score`` methods never call) and the affiliation-based library (a git submodule of
the original repository, for a score Groundhog does not report).
"""

from __future__ import annotations

import math
import random
import sys
import types
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from groundhog.eval.esa_adb import (
    Annotation,
    adtqc,
    channel_aware,
    detections,
    event_series,
    event_wise,
    logical_sum,
)
from groundhog.eval.intervals import Span, to_ns

REFERENCE = Path(__file__).resolve().parents[1] / "third_party" / "esa_adb"
SECOND = 10**9
T0 = to_ns(datetime(2022, 1, 1, tzinfo=UTC))
RANGE = (T0, T0 + 1000 * SECOND)
CHANNELS = ("A", "B", "C")
HALF_MS = 500_000


@pytest.fixture(scope="module")
def reference() -> Iterator[types.SimpleNamespace]:
    utils = types.ModuleType("sklearn.utils")
    for name in ("column_or_1d", "assert_all_finite", "check_consistent_length"):
        setattr(utils, name, lambda *args, **kwargs: None)
    sklearn = types.ModuleType("sklearn")
    sklearn.utils = utils  # type: ignore[attr-defined]
    affiliation = types.ModuleType("esa_adb_metrics.affiliation_based_metrics_repo.affiliation")

    def pr_from_events(pred: Any, truth: Any, span: Any) -> dict[str, list[float]]:
        half = [0.5] * len(truth)
        return {"individual_precision_probabilities": half, "individual_recall_probabilities": half}

    affiliation.pr_from_events = pr_from_events  # type: ignore[attr-defined]
    with pytest.MonkeyPatch.context() as patch:
        patch.syspath_prepend(str(REFERENCE))
        patch.setitem(sys.modules, "sklearn", sklearn)
        patch.setitem(sys.modules, "sklearn.utils", utils)
        patch.setitem(
            sys.modules,
            "esa_adb_metrics.affiliation_based_metrics_repo",
            types.ModuleType("esa_adb_metrics.affiliation_based_metrics_repo"),
        )
        patch.setitem(
            sys.modules, "esa_adb_metrics.affiliation_based_metrics_repo.affiliation", affiliation
        )
        from esa_adb_metrics import ESA_ADB_metrics, latency_metrics, ranking_metrics

        yield types.SimpleNamespace(
            esa=ESA_ADB_metrics.ESAScores,
            adtqc=latency_metrics.ADTQC,
            channel_aware=ranking_metrics.ChannelAwareFScore,
        )
        for name in [m for m in sys.modules if m.startswith("esa_adb_metrics")]:
            del sys.modules[name]


def stamp(ns: int) -> pd.Timestamp:
    return pd.Timestamp(ns)


def truth_frame(annotations: list[Annotation]) -> pd.DataFrame:
    return pd.DataFrame(
        [[a.id, a.channel, stamp(to_ns(a.start)), stamp(to_ns(a.end))] for a in annotations],
        columns=["ID", "Channel", "StartTime", "EndTime"],
    )


def reference_series(series: list[tuple[int, bool]]) -> list[list[Any]]:
    return [[stamp(t), int(v)] for t, v in series]


def at(seconds: int) -> datetime:
    return datetime.fromtimestamp((T0 + seconds * SECOND) / SECOND, tz=UTC)


def random_case(rng: random.Random) -> tuple[list[Annotation], dict[str, list[tuple[int, bool]]]]:
    """Coarse grid, so that detections and annotations often touch exactly.

    A few detection points sit half a millisecond off the grid, inside the 1 ms by
    which channel-aware scoring widens point anomalies.
    """
    grid = list(range(0, 1001, 50))
    annotations = []
    for i in range(rng.randint(1, 6)):
        start = rng.choice(grid[:-1])
        end = start if rng.random() < 0.15 else rng.choice([g for g in grid if g >= start])
        # Some ids get a second fragment: several fragments are one anomaly.
        aid = f"id{rng.randint(0, i)}" if rng.random() < 0.5 else f"id{i}"
        annotations.append(Annotation(aid, rng.choice(CHANNELS), at(start), at(end)))
    series = {}
    for channel in CHANNELS:
        moments = sorted(set(rng.sample(grid, rng.randint(1, 12))))
        series[channel] = [
            (
                T0 + m * SECOND + (HALF_MS if m < 1000 and rng.random() < 0.1 else 0),
                rng.random() < 0.5,
            )
            for m in moments
        ]
    return annotations, series


def close(ours: float | None, theirs: Any) -> bool:
    if ours is None:
        return bool(math.isnan(theirs))
    return math.isclose(ours, float(theirs), rel_tol=1e-12, abs_tol=1e-12)


def test_every_metric_matches_the_reference_on_random_cases(
    reference: types.SimpleNamespace,
) -> None:
    rng = random.Random(20260928)
    full = (stamp(RANGE[0]), stamp(RANGE[1]))
    undefined = 0
    for _ in range(300):
        annotations, series = random_case(rng)
        truth = truth_frame(annotations)
        combined = logical_sum(series)
        spans = {c: detections(s, RANGE) for c, s in series.items()}

        esa = reference.esa(betas=0.5, full_range=full)
        try:
            ours = event_wise(annotations, detections(combined, RANGE), RANGE, beta=0.5)
        except ValueError:  # no nominal time left: the reference fails too
            with pytest.raises(ZeroDivisionError):
                esa.score(truth.drop(columns="Channel"), reference_series(combined))
            undefined += 1
        else:
            theirs = esa.score(truth.drop(columns="Channel"), reference_series(combined))
            assert close(ours.precision, theirs["EW_precision"]), annotations
            assert close(ours.recall, theirs["EW_recall"]), annotations
            assert close(ours.f_beta, theirs["EW_F_0.50"]), annotations
            assert close(ours.alarming_precision, theirs["alarming_precision"]), annotations
            assert (ours.tnr is None) == (ours.true_positives == 0)

        timing = adtqc(annotations, spans, RANGE, exponent=math.e)
        theirs = reference.adtqc(full_range=full).score(
            truth, {c: reference_series(s) for c, s in series.items()}
        )
        assert close(timing.score, theirs["Total"]), annotations
        assert (timing.before, timing.after) == (theirs["Nb_Before"], theirs["Nb_After"])
        assert close(timing.after_rate, theirs["AfterRate"]), annotations

        aware = channel_aware(annotations, spans, RANGE, beta=0.5)
        theirs = reference.channel_aware(beta=0.5, full_range=full).score(
            truth, {c: reference_series(s) for c, s in series.items()}
        )
        assert close(aware.precision, theirs["channel_precision"]), annotations
        assert close(aware.recall, theirs["channel_recall"]), annotations
        assert close(aware.f_beta, theirs["channel_F0.50"]), annotations
    assert 0 < undefined < 30  # the failing case was exercised, but rarely


def on(moment: str) -> datetime:
    return datetime.fromisoformat(moment).replace(tzinfo=UTC)


def points(*pairs: tuple[str, int]) -> list[tuple[int, bool]]:
    return [(to_ns(on(moment)), bool(flag)) for moment, flag in pairs]


def test_the_event_wise_example_of_the_reference(reference: types.SimpleNamespace) -> None:
    """ESA_ADB_metrics.py's own example, without its label filter (Groundhog has none)."""
    annotations = [
        Annotation("id_0", "", on("2015-01-01"), on("2015-01-02")),
        Annotation("id_1", "", on("2015-01-04"), on("2015-01-05")),
        Annotation("id_2", "", on("2015-01-07"), on("2015-01-08")),
    ]
    series = points(("2015-01-01", 0), ("2015-01-04", 1), ("2015-01-09", 0))
    window = (to_ns(on("2015-01-01")), to_ns(on("2015-01-15")))
    ours = event_wise(annotations, detections(series, window), window, beta=0.5)
    theirs = reference.esa(betas=0.5, full_range=(stamp(window[0]), stamp(window[1]))).score(
        truth_frame(annotations).drop(columns="Channel"), reference_series(series)
    )
    assert (ours.true_positives, ours.false_negatives, ours.false_positives) == (2, 1, 0)
    assert close(ours.precision, theirs["EW_precision"])
    assert close(ours.recall, theirs["EW_recall"])
    assert close(ours.f_beta, theirs["EW_F_0.50"])
    assert close(ours.alarming_precision, theirs["alarming_precision"])


def test_the_timing_example_of_the_reference(reference: types.SimpleNamespace) -> None:
    """latency_metrics.py's own example, on a fixed date instead of today's."""
    annotations = [
        Annotation("id_0", "ch1", on("2015-01-01 08:10:16"), on("2015-01-01 08:10:35")),
        Annotation("id_0", "ch2", on("2015-01-01 08:10:10"), on("2015-01-01 08:10:24")),
        Annotation("id_1", "ch3", on("2015-01-01 08:10:30"), on("2015-01-01 08:10:34")),
        Annotation("id_1", "ch3", on("2015-01-01 08:10:40"), on("2015-01-01 08:10:45")),
        Annotation("id_2", "ch2", on("2015-01-01 08:10:54"), on("2015-01-01 08:11:06")),
        Annotation("id_2", "ch3", on("2015-01-01 08:10:54"), on("2015-01-01 08:11:06")),
        Annotation("id_3", "ch1", on("2015-01-01 08:11:08"), on("2015-01-01 08:11:24")),
    ]
    day = "2015-01-01 08:"
    series = {
        "ch1": points(
            (day + "10:10", 0), (day + "10:14", 1), (day + "10:31", 0), (day + "10:41", 1)
        ),
        "ch2": points((day + "10:10", 0), (day + "10:16", 1), (day + "10:22", 0)),
        "ch3": points((day + "10:10", 0), (day + "10:25", 1), (day + "10:41", 0)),
    }
    window = (to_ns(on(day + "10:10")), to_ns(on(day + "11:24")))
    spans = {c: detections(s, window) for c, s in series.items()}
    ours = adtqc(annotations, spans, window, exponent=math.e)
    theirs = reference.adtqc(full_range=(stamp(window[0]), stamp(window[1]))).score(
        truth_frame(annotations), {c: reference_series(s) for c, s in series.items()}
    )
    assert (ours.before, ours.after) == (theirs["Nb_Before"], theirs["Nb_After"])
    assert close(ours.score, theirs["Total"])
    assert close(ours.after_rate, theirs["AfterRate"])


def test_the_channel_aware_example_of_the_reference(reference: types.SimpleNamespace) -> None:
    """ranking_metrics.py's own example, with the range the reference would infer."""
    annotations = [
        Annotation("id_0", "ch1", on("2015-01-01"), on("2015-01-06")),
        Annotation("id_0", "ch2", on("2015-01-01"), on("2015-01-03")),
        Annotation("id_1", "ch1", on("2015-01-05"), on("2015-01-09")),
        Annotation("id_1", "ch3", on("2015-01-04"), on("2015-01-09")),
        Annotation("id_1", "ch4", on("2015-01-07"), on("2015-01-09")),
    ]
    series = {
        "ch1": points(("2015-01-01", 1), ("2015-01-05 12:00", 0)),
        "ch2": points(("2015-01-01", 0)),
        "ch3": points(("2015-01-01", 0), ("2015-01-04", 1), ("2015-01-08", 0)),
        "ch4": points(("2015-01-01", 0), ("2015-01-08", 1)),
    }
    window = (to_ns(on("2015-01-01")), to_ns(on("2015-01-09")))
    spans = {c: detections(s, window) for c, s in series.items()}
    ours = channel_aware(annotations, spans, window, beta=0.5)
    theirs = reference.channel_aware(beta=0.5).score(
        truth_frame(annotations), {c: reference_series(s) for c, s in series.items()}
    )
    assert close(ours.precision, theirs["channel_precision"])
    assert close(ours.recall, theirs["channel_recall"])
    assert close(ours.f_beta, theirs["channel_F0.50"])


def test_redundant_detections_are_counted_per_fragment(
    reference: types.SimpleNamespace,
) -> None:
    """One anomaly in two fragments, each touched by two separate detections."""
    annotations = [
        Annotation("id_0", "A", at(100), at(200)),
        Annotation("id_0", "A", at(400), at(500)),
    ]
    flags = [(0, 0), (120, 1), (140, 0), (160, 1), (180, 0), (420, 1), (440, 0), (460, 1), (480, 0)]
    series = [(T0 + t * SECOND, bool(flag)) for t, flag in flags]
    ours = event_wise(annotations, detections(series, RANGE), RANGE, beta=0.5)
    theirs = reference.esa(betas=0.5, full_range=(stamp(RANGE[0]), stamp(RANGE[1]))).score(
        truth_frame(annotations).drop(columns="Channel"), reference_series(series)
    )
    assert (ours.true_positives, ours.redundant) == (1, 2)
    assert close(ours.alarming_precision, theirs["alarming_precision"])


def test_a_point_anomaly_is_widened_by_a_millisecond_for_channel_awareness(
    reference: types.SimpleNamespace,
) -> None:
    """A detection starting half a millisecond after a point anomaly still finds it."""
    annotations = [Annotation("id_0", "A", at(100), at(100))]
    series = {"A": [(T0, False), (T0 + 100 * SECOND + HALF_MS, True), (T0 + 200 * SECOND, False)]}
    spans = {c: detections(s, RANGE) for c, s in series.items()}
    ours = channel_aware(annotations, spans, RANGE, beta=0.5)
    theirs = reference.channel_aware(beta=0.5, full_range=(stamp(RANGE[0]), stamp(RANGE[1]))).score(
        truth_frame(annotations), {c: reference_series(s) for c, s in series.items()}
    )
    assert ours.recall == theirs["channel_recall"] == 1.0


def test_no_nominal_time_is_refused_when_there_is_something_to_correct(
    reference: types.SimpleNamespace,
) -> None:
    esa = reference.esa(betas=0.5, full_range=(stamp(RANGE[0]), stamp(RANGE[1])))
    annotations = [Annotation("id_0", "A", at(0), at(1000))]
    truth = truth_frame(annotations).drop(columns="Channel")
    detected = [(T0, True)]
    with pytest.raises(ValueError, match="no nominal time"):
        event_wise(annotations, detections(detected, RANGE), RANGE, beta=0.5)
    with pytest.raises(ZeroDivisionError):
        esa.score(truth, reference_series(detected))
    # Nothing detected: precision is 0 and there is nothing to correct. (The reference
    # cannot score this at all: its affiliation part takes the maximum of no events.)
    silent = [(T0, False)]
    ours = event_wise(annotations, detections(silent, RANGE), RANGE, beta=0.5)
    assert (ours.precision, ours.tnr, ours.recall) == (0.0, None, 0.0)


class TestEventsAsDetections:
    def test_an_event_covers_exactly_its_closed_interval(self) -> None:
        series = event_series([(at(100), at(200))], RANGE)
        span = detections(series, RANGE)
        assert [(a.lower, a.upper, a.left_closed, a.right_closed) for a in span] == [
            (T0 + 100 * SECOND, T0 + 200 * SECOND + 1, True, False)
        ]
        # It touches an annotation ending at its start and one starting at its end...
        assert not (span & Span.closed(T0, T0 + 100 * SECOND)).empty
        assert not (span & Span.closed(T0 + 200 * SECOND, T0 + 300 * SECOND)).empty
        # ...but nothing after its last instant.
        assert (span & Span.closed(T0 + 200 * SECOND + 1, T0 + 300 * SECOND)).empty

    def test_an_open_event_runs_to_the_end_of_the_range(self) -> None:
        span = detections(event_series([(at(900), None)], RANGE), RANGE)
        assert (span.lower, span.upper) == (T0 + 900 * SECOND, RANGE[1])

    def test_two_events_on_either_side_of_a_gap_stay_two_detections(self) -> None:
        span = detections(event_series([(at(100), at(200)), (at(500), at(600))], RANGE), RANGE)
        assert len(span.atomics) == 2

    def test_the_logical_sum_is_the_union_of_the_channels(self) -> None:
        rng = random.Random(7)
        for _ in range(200):
            _, series = random_case(rng)
            union = Span.union(detections(s, RANGE) for s in series.values())
            assert detections(logical_sum(series), RANGE) == union
