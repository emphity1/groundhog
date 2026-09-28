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
from collections.abc import Iterator
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from groundhog.ingest import IngestError
from groundhog.schema import ChannelKind, Quality, Reading

__all__ = ["ChannelSpec", "OpssatSource", "OpssatSourceConfig"]

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
        try:
            channel, stamp, text_value = (row[i] for i in at)
            mission_ts = datetime.fromisoformat(stamp)
            value = float(text_value)
        except (IndexError, ValueError) as exc:
            raise IngestError(f"{path}:{line}: {exc}") from None
        if mission_ts.tzinfo is None:
            raise IngestError(f"{path}:{line}: timestamp {stamp!r} has no time zone")
        if not math.isfinite(value):
            raise IngestError(f"{path}:{line}: value {text_value!r} is not finite")
        points.setdefault(channel, []).append((mission_ts.astimezone(UTC), value))

    series: dict[str, tuple[list[datetime], list[float]]] = {}
    for channel, samples in points.items():
        samples.sort()
        times = [t for t, _ in samples]
        for earlier, later in pairwise(times):
            if earlier == later:
                raise IngestError(f"{path}: channel {channel} has two samples at {later}")
        series[channel] = (times, [v for _, v in samples])
    return series


def _check_declared(found: set[str], declared: set[str], path: Path) -> None:
    if undeclared := sorted(found - declared):
        raise IngestError(
            f"{path}: channels without a declared kind and unit: {', '.join(undeclared)}"
        )
    if missing := sorted(declared - found):
        raise IngestError(f"declared channels absent from {path}: {', '.join(missing)}")
