# SPDX-License-Identifier: Apache-2.0
"""OPSSAT-AD: the telemetry fetched by ``scripts/fetch_data.py opssat``, as readings.

Only ``segments.csv`` is telemetry. ``dataset.csv`` holds per-segment features
derived by the dataset authors and is not read. DATA.md describes both files.

Three rules shape what comes out:

* **Kind and unit come from the configuration.** The files carry neither, and a
  guessed kind is how a status flag ends up averaged. Every channel in the data
  must be declared, and every declared channel must be in the data.
* **Quality is ``ok`` for every reading.** The source carries no quality
  information and the adapter synthesises nothing. Jitter and gaps are
  properties of the stream, which the replay reproduces, not defects of a sample.
* **Labels never enter a reading.** ``anomaly``, ``label``, ``segment`` and
  ``train`` are ground truth and split metadata. Detectors must not see them.
"""

from __future__ import annotations

import bisect
import csv
import hashlib
import io
import math
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import groupby, pairwise
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from groundhog.ingest import IngestError
from groundhog.schema import ChannelKind, Quality, Reading

__all__ = [
    "ChannelSpec",
    "LabelledSample",
    "LabelledSegment",
    "OpssatSource",
    "OpssatSourceConfig",
    "labelled_samples",
    "labelled_segments",
    "nominal_values",
]

# Part of the dataset's format, not a tunable.
_COLUMNS = ("channel", "timestamp", "value")


class ChannelSpec(BaseModel):
    """How to interpret one channel. The source files do not say."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: ChannelKind
    unit: str | None = Field(description="Engineering unit, null when unknown")


class OpssatSourceConfig(BaseModel):
    """The ``source`` section of a replay configuration for OPSSAT-AD."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["opssat"]
    path: Path = Field(description="segments.csv as fetched by scripts/fetch_data.py")
    mission: str = Field(min_length=1, description="Mission id carried by every reading")
    channels: dict[str, ChannelSpec] = Field(min_length=1)


class OpssatSource:
    """Readings from OPSSAT-AD, one channel at a time, in mission-time order.

    The file is parsed once, at construction. Readings are built lazily, so memory
    holds timestamps and values rather than one model per sample.
    """

    def __init__(self, config: OpssatSourceConfig) -> None:
        self._config = config
        try:
            raw = config.path.read_bytes()
        except FileNotFoundError:
            raise IngestError(
                f"{config.path} not found: fetch it with `make data-fetch DS=opssat`"
            ) from None
        self._md5 = hashlib.md5(raw, usedforsecurity=False).hexdigest()
        self._series = _parse(raw.decode("utf-8"), config.path)
        _check_declared(set(self._series), set(config.channels), config.path)

    def channels(self) -> tuple[str, ...]:
        return tuple(sorted(self._series))

    def readings(
        self, channel: str, start: datetime | None, end: datetime | None
    ) -> Iterator[Reading]:
        times, values = self._series[channel]
        spec = self._config.channels[channel]
        first = 0 if start is None else bisect.bisect_left(times, start)
        stop = len(times) if end is None else bisect.bisect_left(times, end)
        for i in range(first, stop):
            yield Reading(
                mission=self._config.mission,
                channel=channel,
                mission_ts=times[i],
                value=values[i],
                kind=spec.kind,
                unit=spec.unit,
                quality=Quality.OK,
            )

    def describe(self) -> dict[str, Any]:
        return {
            "adapter": "opssat",
            "md5": self._md5,
            "mission": self._config.mission,
            "channels": {
                name: spec.model_dump(mode="json")
                for name, spec in sorted(self._config.channels.items())
            },
        }


def _parse(text: str, path: Path) -> dict[str, tuple[list[datetime], list[float]]]:
    """Per channel: timestamps in strictly increasing order, and their values."""
    rows = csv.reader(io.StringIO(text))
    header = next(rows, None)
    if header is None or not set(_COLUMNS) <= set(header):
        raise IngestError(f"{path}: expected columns {', '.join(_COLUMNS)}, found {header}")
    at = [header.index(column) for column in _COLUMNS]

    points: dict[str, list[tuple[datetime, float]]] = {}
    for line, row in enumerate(rows, start=2):
        channel, mission_ts, value = _measurement(row, at, path, line)
        points.setdefault(channel, []).append((mission_ts, value))

    series: dict[str, tuple[list[datetime], list[float]]] = {}
    for channel, samples in points.items():
        samples.sort()
        times = [t for t, _ in samples]
        for earlier, later in pairwise(times):
            if earlier == later:
                raise IngestError(f"{path}: channel {channel} has two samples at {later}")
        series[channel] = (times, [v for _, v in samples])
    return series


@dataclass(frozen=True)
class LabelledSample:
    """One row of ``segments.csv`` with its ground truth and split."""

    channel: str
    mission_ts: datetime
    value: float
    anomaly: bool
    segment: int
    sampling_s: int
    train: bool
    """The dataset's own split, used only for the comparison with the literature."""


@dataclass(frozen=True)
class LabelledSegment:
    """One labelled segment: a window of one channel, from its first to its last sample."""

    id: int
    channel: str
    sampling_s: int
    anomaly: bool
    train: bool
    start: datetime
    end: datetime
    samples: int


def labelled_samples(path: Path) -> list[LabelledSample]:
    """Every row of ``segments.csv``, in file order, with its labels.

    For fitting and for scoring, never for streaming: labels never reach a reading,
    which is read through :class:`OpssatSource`.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise IngestError(f"{path} not found: fetch it with `make data-fetch DS=opssat`") from None
    rows = csv.reader(io.StringIO(text))
    header = next(rows, None)
    columns = (*_COLUMNS, "anomaly", "segment", "sampling", "train")
    if header is None or not set(columns) <= set(header):
        raise IngestError(f"{path}: expected columns {', '.join(columns)}, found {header}")
    at = [header.index(column) for column in columns]

    samples = []
    for line, row in enumerate(rows, start=2):
        channel, mission_ts, value = _measurement(row, at, path, line)
        anomaly, segment, sampling, train = (row[i] for i in at[3:])
        if anomaly not in ("0", "1"):
            raise IngestError(f"{path}:{line}: anomaly label {anomaly!r} is neither 0 nor 1")
        if train not in ("0", "1"):
            raise IngestError(f"{path}:{line}: train flag {train!r} is neither 0 nor 1")
        if not (segment.isdigit() and sampling.isdigit() and int(sampling) > 0):
            raise IngestError(
                f"{path}:{line}: segment {segment!r} and sampling {sampling!r} must be "
                "a non-negative and a positive integer"
            )
        samples.append(
            LabelledSample(
                channel=channel,
                mission_ts=mission_ts,
                value=value,
                anomaly=anomaly == "1",
                segment=int(segment),
                sampling_s=int(sampling),
                train=train == "1",
            )
        )
    return samples


def nominal_values(
    samples: Iterable[LabelledSample],
    start: datetime | None,
    end: datetime | None,
    *,
    official_train: bool = False,
) -> dict[str, list[float]]:
    """Per channel, the values labelled nominal with ``start <= mission_ts < end``.

    With ``official_train``, only those of segments the dataset's own split marks
    for training: the comparison with the literature fits there (ADR 0001).
    """
    nominal: dict[str, list[float]] = {}
    for s in samples:
        in_window = (start is None or s.mission_ts >= start) and (end is None or s.mission_ts < end)
        if in_window and not s.anomaly and (s.train or not official_train):
            nominal.setdefault(s.channel, []).append(s.value)
    return nominal


def labelled_segments(samples: Iterable[LabelledSample]) -> list[LabelledSegment]:
    """The segments, by channel and then start. Only time varies within a segment."""
    segments = []
    for segment, group in groupby(sorted(samples, key=lambda s: s.segment), lambda s: s.segment):
        members = sorted(group, key=lambda s: s.mission_ts)
        first = members[0]
        uniform = {(s.channel, s.sampling_s, s.anomaly, s.train) for s in members}
        if len(uniform) > 1:
            raise IngestError(
                f"segment {segment} mixes channels, sampling, labels or split: {sorted(uniform)}"
            )
        segments.append(
            LabelledSegment(
                id=segment,
                channel=first.channel,
                sampling_s=first.sampling_s,
                anomaly=first.anomaly,
                train=first.train,
                start=first.mission_ts,
                end=members[-1].mission_ts,
                samples=len(members),
            )
        )
    return sorted(segments, key=lambda seg: (seg.channel, seg.start))


def _measurement(
    row: list[str], at: list[int], path: Path, line: int
) -> tuple[str, datetime, float]:
    """Channel, UTC mission time and value of one row, or an error naming the line."""
    try:
        channel, stamp, text_value = (row[i] for i in at[:3])
        mission_ts = datetime.fromisoformat(stamp)
        value = float(text_value)
    except (IndexError, ValueError) as exc:
        raise IngestError(f"{path}:{line}: {exc}") from None
    if mission_ts.tzinfo is None:
        raise IngestError(f"{path}:{line}: timestamp {stamp!r} has no time zone")
    if not math.isfinite(value):
        raise IngestError(f"{path}:{line}: value {text_value!r} is not finite")
    return channel, mission_ts.astimezone(UTC), value


def _check_declared(found: set[str], declared: set[str], path: Path) -> None:
    if undeclared := sorted(found - declared):
        raise IngestError(
            f"{path}: channels without a declared kind and unit: {', '.join(undeclared)}"
        )
    if missing := sorted(declared - found):
        raise IngestError(f"declared channels absent from {path}: {', '.join(missing)}")
