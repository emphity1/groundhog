# SPDX-License-Identifier: Apache-2.0
"""Samples, in stream order, through a detector and the arbiter, to events. No I/O."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from groundhog.arbiter import Arbiter
from groundhog.detectors.base import Detector
from groundhog.schema import DetectorId, Event, Sample

__all__ = ["Coverage", "DetectPipeline", "DetectorRun", "RunHeader", "RunInfo"]


@dataclass
class Coverage:
    """How much of the stream was monitored (ADR 0005)."""

    samples: int = 0
    """Samples received."""
    repeats: int = 0
    """Samples dropped as already processed (ADR 0004)."""
    scored: int = 0
    """Samples the detector had an opinion on: coverage is scored over the rest."""
    firing: int = 0
    """Scores with the detector's alarm condition holding."""

    @property
    def ratio(self) -> float:
        processed = self.samples - self.repeats
        return self.scored / processed if processed else 0.0


class DetectorRun(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: DetectorId
    model_version: str | None
    config_sha256: str


class RunInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tool: str
    version: str
    config: str
    detectors: tuple[DetectorRun, ...]
    arbiter_sha256: str


class RunHeader(BaseModel):
    """The first line of an event stream: which run produced the events after it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run: RunInfo


class DetectPipeline:
    """One detector and the arbiter, fed one sample at a time."""

    def __init__(self, detector: Detector, arbiter: Arbiter) -> None:
        self._detector = detector
        self._arbiter = arbiter
        self._last: dict[tuple[str, str], datetime] = {}
        self.coverage = Coverage()
        self.coverage_by_channel: dict[str, Coverage] = {}

    def feed(self, sample: Sample) -> list[Event]:
        """The events to publish after ``sample``. A repeat is dropped: within a
        channel mission time strictly increases, so a sample that does not move it
        forward has been processed already (ADR 0004)."""
        channel = self.coverage_by_channel.setdefault(sample.channel, Coverage())
        self.coverage.samples += 1
        channel.samples += 1
        key = (sample.mission, sample.channel)
        last = self._last.get(key)
        if last is not None and sample.mission_ts <= last:
            self.coverage.repeats += 1
            channel.repeats += 1
            return []
        self._last[key] = sample.mission_ts

        score = self._detector.update(sample)
        if score is None:
            return []
        self.coverage.scored += 1
        channel.scored += 1
        self.coverage.firing += score.firing
        channel.firing += score.firing
        return self._arbiter.update(score)

    def finish(self) -> list[Event]:
        """End of the stream: the events still open, published with ``t_end`` null."""
        return self._arbiter.finish()
