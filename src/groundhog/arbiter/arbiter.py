# SPDX-License-Identifier: Apache-2.0
"""The arbiter, minimal version (M3): firing scores in, events out.

The consecutive firing scores of one detector on one channel make one event
(docs/adr/0006-event-stream.md). An event is published:

* **when it opens**, at the first firing score. It starts at the score's onset,
  the start of the violation; its detection records when the detector fired.
* **when its severity rises.** Severity comes from the peak score through bands
  set per detector, because scores are not comparable across detectors.
* **when it closes**, at the first score that is not firing. It ends at the last
  firing one.
* **at the end of the stream, if still open**: once more, with ``t_end`` null.
  An open event is never dropped.

Every publication of an event carries the same ``id``, derived from what the event
is, so the last record for an id is the event's state. Repeated scores are
ignored: the arbiter is idempotent. Suppressing repeats and fusing detectors come
in M5, through :class:`EventHook`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from groundhog.schema import (
    ChannelContribution,
    Detection,
    DetectorId,
    Event,
    Quality,
    Score,
    Severity,
)

__all__ = [
    "Arbiter",
    "ArbiterError",
    "ArbiterSettings",
    "EventHook",
    "PassThrough",
    "SeverityBands",
]

_Subject = tuple[str, DetectorId, str]  # mission, detector, channel


class ArbiterError(Exception):
    """The arbiter was given something it is not configured for."""


class SeverityBands(BaseModel):
    """The peak scores at which one detector's events become medium, then high."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    medium: float = Field(gt=0, allow_inf_nan=False)
    high: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _ordered(self) -> SeverityBands:
        if self.high < self.medium:
            raise ValueError("the high band cannot start below the medium one")
        return self

    def of(self, peak: float) -> Severity:
        if peak >= self.high:
            return Severity.HIGH
        if peak >= self.medium:
            return Severity.MEDIUM
        return Severity.LOW


class ArbiterSettings(BaseModel):
    """The ``arbiter`` section of a detect configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    severity: dict[DetectorId, SeverityBands] = Field(
        min_length=1, description="Per detector: scores are not comparable across detectors"
    )


class EventHook(Protocol):
    """Where M5 plugs in: suppressing repeats of an ongoing event, fusing detectors."""

    def admit(self, event: Event) -> Event | None:
        """The event to publish in place of ``event``, or None to hold it back."""
        ...


class PassThrough:
    """M3: every event is published as it is."""

    def admit(self, event: Event) -> Event | None:
        return event


@dataclass
class _Open:
    id: str
    t_start: datetime
    fired_at: datetime
    last_firing: datetime
    peak: float
    quality: Quality
    severity: Severity
    created_at: datetime


class Arbiter:
    def __init__(
        self,
        settings: ArbiterSettings,
        model_versions: Mapping[DetectorId, str | None],
        clock: Callable[[], datetime],
        hook: EventHook | None = None,
    ) -> None:
        """``model_versions`` names the detectors whose scores are expected; ``clock``
        gives the wall time at which an event opens."""
        if missing := sorted(set(model_versions) - set(settings.severity)):
            raise ArbiterError(f"no severity bands for {', '.join(missing)}")
        self._bands = settings.severity
        self._versions = dict(model_versions)
        self._clock = clock
        self._hook = hook if hook is not None else PassThrough()
        self._open: dict[_Subject, _Open] = {}
        self._last_scored: dict[_Subject, datetime] = {}

    def update(self, score: Score) -> list[Event]:
        """The events to publish after ``score``: none, one, or a closing and an opening."""
        if score.detector not in self._versions:
            raise ArbiterError(f"a score from {score.detector}, which this arbiter does not expect")
        subject = (score.mission, score.detector, score.channel)
        last = self._last_scored.get(subject)
        if last is not None and score.mission_ts <= last:
            return []  # seen already: idempotent under (mission, channel, mission_ts)
        self._last_scored[subject] = score.mission_ts

        published: list[Event] = []
        current = self._open.get(subject)
        if current is not None and score.onset != current.t_start:
            # Not firing any more, or firing for a new condition: this event is over.
            del self._open[subject]
            published += self._publish(subject, current, t_end=current.last_firing)
            current = None

        if score.onset is not None:  # firing
            bands = self._bands[score.detector]
            if current is None:
                current = self._open[subject] = _Open(
                    id=_event_id(subject, score.onset),
                    t_start=score.onset,
                    fired_at=score.mission_ts,
                    last_firing=score.mission_ts,
                    peak=score.value,
                    quality=score.quality,
                    severity=bands.of(score.value),
                    created_at=self._clock(),
                )
                published += self._publish(subject, current, t_end=None)
            else:
                current.last_firing = score.mission_ts
                current.peak = max(current.peak, score.value)
                current.quality = Quality.worst(current.quality, score.quality)
                if (severity := bands.of(current.peak)) is not current.severity:
                    current.severity = severity
                    published += self._publish(subject, current, t_end=None)
        return published

    def finish(self) -> list[Event]:
        """At the end of the stream: every event still open, once more, with ``t_end`` null."""
        published: list[Event] = []
        for subject in sorted(self._open):
            published += self._publish(subject, self._open[subject], t_end=None)
        self._open.clear()
        return published

    def _publish(self, subject: _Subject, event: _Open, t_end: datetime | None) -> list[Event]:
        mission, detector, channel = subject
        admitted = self._hook.admit(
            Event(
                id=event.id,
                mission=mission,
                t_start=event.t_start,
                t_end=t_end,
                channels=(ChannelContribution(channel=channel, weight=1.0),),
                detections=(
                    Detection(
                        detector=detector,
                        model_version=self._versions[detector],
                        score=event.peak,
                        fired_at=event.fired_at,
                        delay_s=(event.fired_at - event.t_start).total_seconds(),
                    ),
                ),
                severity=event.severity,
                quality=event.quality,
                created_at=event.created_at,
            )
        )
        return [] if admitted is None else [admitted]


def _event_id(subject: _Subject, t_start: datetime) -> str:
    """What the event is, as its id: the same stream always yields the same ids."""
    mission, detector, channel = subject
    return f"{mission}:{detector}:{channel}:{t_start:%Y%m%dT%H%M%S.%fZ}"
