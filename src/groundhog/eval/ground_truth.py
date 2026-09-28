# SPDX-License-Identifier: Apache-2.0
"""Labelled events and continuous runs: what detectors are scored against (ADR 0001).

OPSSAT-AD labels segments, fixed windows of one channel. Consecutive segments of a
channel form one **continuous run** until the channel falls silent for longer than
``run_gap_periods`` sampling periods. Within a run, each maximal stretch of
anomalous segments describes one anomaly: one **labelled event**, from its first
sample to its last.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from itertools import groupby

from groundhog.ingest.opssat import LabelledSegment

__all__ = ["LabelledEvent", "Run", "continuous_runs", "labelled_events"]


@dataclass(frozen=True)
class Run:
    """Consecutive segments of one channel with no silence longer than the gap rule."""

    channel: str
    segments: tuple[LabelledSegment, ...]

    @property
    def start(self) -> datetime:
        return self.segments[0].start

    @property
    def end(self) -> datetime:
        return self.segments[-1].end


@dataclass(frozen=True)
class LabelledEvent:
    """One anomaly: consecutive anomalous segments of one run."""

    id: str
    channel: str
    start: datetime
    end: datetime
    segments: tuple[int, ...]

    @property
    def duration_s(self) -> float:
        return (self.end - self.start).total_seconds()


def continuous_runs(segments: Iterable[LabelledSegment], run_gap_periods: float) -> list[Run]:
    """The runs of every channel. A run breaks where the silence between a segment
    and the one before it lasts more than ``run_gap_periods`` sampling periods of
    the later segment."""
    runs = []
    ordered = sorted(segments, key=lambda s: (s.channel, s.start))
    for channel, group in groupby(ordered, key=lambda s: s.channel):
        current: list[LabelledSegment] = []
        for segment in group:
            if current and (
                (segment.start - current[-1].end).total_seconds()
                > run_gap_periods * segment.sampling_s
            ):
                runs.append(Run(channel, tuple(current)))
                current = []
            current.append(segment)
        runs.append(Run(channel, tuple(current)))
    return runs


def labelled_events(
    segments: Iterable[LabelledSegment], run_gap_periods: float
) -> list[LabelledEvent]:
    """The labelled events, in order of start. An event's id names its channel and
    its first segment, so it can be found in the dataset."""
    events = []
    for run in continuous_runs(segments, run_gap_periods):
        for anomalous, stretch in groupby(run.segments, key=lambda s: s.anomaly):
            if anomalous:
                events.append(_event(run.channel, list(stretch)))
    return sorted(events, key=lambda e: (e.start, e.channel))


def _event(channel: str, stretch: Sequence[LabelledSegment]) -> LabelledEvent:
    return LabelledEvent(
        id=f"{channel}:{stretch[0].id}",
        channel=channel,
        start=stretch[0].start,
        end=stretch[-1].end,
        segments=tuple(s.id for s in stretch),
    )
