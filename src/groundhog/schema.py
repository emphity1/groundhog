# SPDX-License-Identifier: Apache-2.0
"""Core data contracts for Groundhog.

Everything in the system speaks these types. A :class:`Sample` is one telemetry
measurement; an :class:`Event` is an anomaly candidate as the operator sees it.
Source adapters produce readings, a publisher (the replay engine, or a live feed)
turns each :class:`Reading` into a sample, detectors consume samples, the arbiter
produces events.

Two conventions that the rest of the codebase depends on:

* **Two clocks.** ``mission_ts`` is the timestamp the data carries — the moment
  the spacecraft measured it. ``wall_ts`` is when the sample entered this system,
  whether from a live feed or from the replay engine. Detection delay is measured
  on mission time; system latency on wall time. Mixing them produces meaningless
  numbers.
* **Quality travels.** A sample derived from a gap or flagged as stale keeps that
  mark, and any event built from it inherits it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    "ChannelContribution",
    "ChannelKind",
    "Detection",
    "DetectorId",
    "Event",
    "EventStatus",
    "OrbitalContext",
    "Quality",
    "Reading",
    "Sample",
    "Severity",
    "Verdict",
]


class ChannelKind(StrEnum):
    """What a channel's value means.

    The distinction is not cosmetic: averaging a status flag or interpolating a
    telecommand is meaningless, and pipelines that ignore this fail silently.
    """

    NUMERIC = "numeric"
    """A physical measurement: volts, degrees, rpm."""

    CATEGORICAL = "categorical"
    """A coded state, stored as its numeric code (operating mode, on/off flag)."""

    COUNTER = "counter"
    """A monotonic or resettable count, e.g. memory error counts."""

    TELECOMMAND = "telecommand"
    """A command issued from ground, recorded as an event on the timeline."""


class Quality(StrEnum):
    """How much the value can be trusted."""

    OK = "ok"
    STALE = "stale"
    """Repeated value where the source stopped updating."""

    GAP_FILLED = "gap_filled"
    """Synthesised to bridge a short gap, per the channel's gap policy."""

    SUSPECT = "suspect"
    """Kept, but flagged by ingest (out of physical range, decode issue)."""


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware (UTC)")
    return value.astimezone(UTC)


class Sample(BaseModel):
    """One telemetry measurement. Immutable once created."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mission: str = Field(min_length=1, description="Dataset or spacecraft identifier")
    channel: str = Field(min_length=1, description="Channel id, e.g. TM_EPS_BATT_V")
    mission_ts: datetime = Field(description="When the spacecraft measured it (UTC)")
    wall_ts: datetime = Field(description="When it entered this system (UTC)")
    value: float = Field(description="Calibrated value; categorical channels carry their code")
    kind: ChannelKind = ChannelKind.NUMERIC
    unit: str | None = Field(default=None, description="Engineering unit, when known")
    quality: Quality = Quality.OK

    _utc = field_validator("mission_ts", "wall_ts")(_require_utc)

    @property
    def key(self) -> str:
        """Partition key on the bus: samples of one channel stay ordered."""
        return f"{self.mission}/{self.channel}"


class Reading(BaseModel):
    """A sample as an archive holds it: every field except the publication time.

    Source adapters and the data lake deal in readings, because ``wall_ts`` does
    not exist until something publishes the value. :meth:`publish` is the only way
    a reading becomes a :class:`Sample`. See docs/adr/0002-readings-before-publication.md.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    mission: str = Field(min_length=1, description="Dataset or spacecraft identifier")
    channel: str = Field(min_length=1, description="Channel id, e.g. TM_EPS_BATT_V")
    mission_ts: datetime = Field(description="When the spacecraft measured it (UTC)")
    value: float = Field(description="Calibrated value; categorical channels carry their code")
    kind: ChannelKind = ChannelKind.NUMERIC
    unit: str | None = Field(default=None, description="Engineering unit, when known")
    quality: Quality = Quality.OK

    _utc = field_validator("mission_ts")(_require_utc)

    def publish(self, wall_ts: datetime) -> Sample:
        """The sample this reading becomes when it is published at ``wall_ts``."""
        return Sample(wall_ts=wall_ts, **self.model_dump())


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class DetectorId(StrEnum):
    """The detectors defined in ARCHITECTURE.md §4.7."""

    R0_LIMITS = "r0_limits"
    R1_STATS = "r1_stats"
    R2_PREDICTIVE = "r2_predictive"
    R3_RECONSTRUCTION = "r3_reconstruction"
    R4_UNSUPERVISED = "r4_unsupervised"


class Detection(BaseModel):
    """One detector's contribution to an event."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    detector: DetectorId
    model_version: str | None = Field(
        default=None, description="Registry version; None for rule-based detectors"
    )
    score: float = Field(ge=0.0, description="Detector score, higher is more anomalous")
    fired_at: datetime = Field(description="Mission time at which this detector fired")
    delay_s: float = Field(
        ge=0.0, description="Seconds of mission time between event start and this firing"
    )

    _utc = field_validator("fired_at")(_require_utc)


class ChannelContribution(BaseModel):
    """How much a channel contributed to the event score."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    channel: str = Field(min_length=1)
    weight: float = Field(ge=0.0, le=1.0)


class OrbitalContext(BaseModel):
    """The situation the spacecraft was in when the event started.

    Computed once, at event creation, and stored with the event. Recomputing it
    later is expensive and, once ephemerides are updated, not reproducible.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    lat_deg: float | None = Field(default=None, ge=-90.0, le=90.0)
    lon_deg: float | None = Field(default=None, ge=-180.0, le=180.0)
    altitude_km: float | None = Field(default=None, ge=0.0)
    in_eclipse: bool | None = None
    in_saa: bool | None = None
    ground_station: str | None = Field(default=None, description="Station in view, if any")
    elevation_deg: float | None = Field(default=None, ge=-90.0, le=90.0)
    kp_index: float | None = Field(default=None, ge=0.0, le=9.0)
    proton_flux: float | None = Field(default=None, ge=0.0)
    recent_telecommands: tuple[str, ...] = ()


class EventStatus(StrEnum):
    NEW = "new"
    TRIAGE = "triage"
    CLOSED = "closed"


class Verdict(StrEnum):
    """The operator's judgement. Feeds the label store and retraining."""

    CONFIRMED = "confirmed"
    FALSE_POSITIVE = "false_positive"


class Event(BaseModel):
    """An anomaly candidate: what reaches the operator, and what gets measured."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    mission: str = Field(min_length=1)
    t_start: datetime = Field(description="Event start, mission time")
    t_end: datetime | None = Field(default=None, description="None while still open")
    channels: tuple[ChannelContribution, ...] = Field(min_length=1)
    detections: tuple[Detection, ...] = Field(min_length=1)
    severity: Severity = Severity.MEDIUM
    quality: Quality = Quality.OK
    context: OrbitalContext | None = None
    status: EventStatus = EventStatus.NEW
    verdict: Verdict | None = None
    created_at: datetime = Field(description="Wall time at which the arbiter opened it")

    _utc = field_validator("t_start", "t_end", "created_at")(
        lambda v: v if v is None else _require_utc(v)
    )

    @model_validator(mode="after")
    def _check_interval(self) -> Event:
        if self.t_end is not None and self.t_end < self.t_start:
            raise ValueError("t_end must not precede t_start")
        return self

    @model_validator(mode="after")
    def _check_weights(self) -> Event:
        total = sum(c.weight for c in self.channels)
        if total > 1.0 + 1e-6:
            raise ValueError(f"channel contributions sum to {total:.3f}, must not exceed 1.0")
        return self

    @property
    def duration_s(self) -> float | None:
        """Mission-time duration, or None while the event is still open."""
        if self.t_end is None:
            return None
        return (self.t_end - self.t_start).total_seconds()

    @property
    def first_detection(self) -> Detection:
        """The detector that fired first — the one that owns the detection delay."""
        return min(self.detections, key=lambda d: d.fired_at)

    def fired_by(self, detector: DetectorId) -> Detection | None:
        for d in self.detections:
            if d.detector is detector:
                return d
        return None
