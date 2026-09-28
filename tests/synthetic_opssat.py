# SPDX-License-Identifier: Apache-2.0
"""A small OPSSAT-AD file holding every feature the adapter and the replay must honour.

Like the real ``segments.csv``: segments stored out of time order, ids unrelated to
time, channels sharing timestamps (ties the replay must order), 1 s and 5 s
sampling, 5 s jitter, a gap inside a segment, and a gap of hours between runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from groundhog.ingest.opssat import ChannelSpec
from groundhog.schema import ChannelKind

HEADER = "channel,timestamp,value,label,sampling,anomaly,segment,train\n"


@dataclass(frozen=True)
class Segment:
    channel: str
    sampling: int
    anomaly: int
    id: int
    train: int
    samples: tuple[tuple[str, str], ...]  # (timestamp, value) exactly as in the file
    label: str = "anomaly"  # constant in the real file, whatever the anomaly flag says


# File order. In time: CH_A and CH_B tick together from 20:00:00, CH_C joins at
# 20:00:02 (a three-way tie), jitters (a 6 s step) and pauses 122 s inside its
# segment, then nothing from 20:02:25 until 23:00:00.
SEGMENTS = (
    Segment(
        "CH_A", 1, 0, 7, 1, tuple((f"2022-01-04T20:00:0{s}.000Z", f"1.0{s}") for s in range(5))
    ),
    Segment(
        "CH_B",
        1,
        0,
        9,
        0,
        (
            ("2022-01-04T23:00:01.000Z", "-2.1487e-05"),
            ("2022-01-04T23:00:02.000Z", "-2.05077e-05"),
            ("2022-01-04T23:00:03.000Z", "-2.10633e-05"),
        ),
    ),
    Segment(
        "CH_C",
        5,
        1,
        5,
        1,
        (
            ("2022-01-04T20:00:02.000Z", "0"),
            ("2022-01-04T20:00:07.000Z", "1"),
            ("2022-01-04T20:00:13.000Z", "2"),
            ("2022-01-04T20:00:18.000Z", "1"),
            ("2022-01-04T20:02:20.000Z", "0"),
            ("2022-01-04T20:02:25.000Z", "2"),
        ),
    ),
    Segment(
        "CH_A", 1, 0, 1, 0, tuple((f"2022-01-04T23:00:0{s}.000Z", f"1.1{s}") for s in range(3))
    ),
    Segment(
        "CH_B", 1, 0, 2, 1, tuple((f"2022-01-04T20:00:0{s}.000Z", f"2.0{s}") for s in range(5))
    ),
)

TOTAL = sum(len(s.samples) for s in SEGMENTS)
FIRST = datetime(2022, 1, 4, 20, 0, 0, tzinfo=UTC)
GAP = (datetime(2022, 1, 4, 20, 2, 25, tzinfo=UTC), datetime(2022, 1, 4, 23, 0, 0, tzinfo=UTC))
INNER_GAP = (
    datetime(2022, 1, 4, 20, 0, 18, tzinfo=UTC),
    datetime(2022, 1, 4, 20, 2, 20, tzinfo=UTC),
)

CHANNELS = {
    "CH_A": ChannelSpec(kind=ChannelKind.NUMERIC, unit="T"),
    "CH_B": ChannelSpec(kind=ChannelKind.NUMERIC, unit=None),
    "CH_C": ChannelSpec(kind=ChannelKind.CATEGORICAL, unit=None),
}


def write_segments(path: Path, segments: tuple[Segment, ...] = SEGMENTS) -> Path:
    rows = [
        f"{seg.channel},{stamp},{value},{seg.label},{seg.sampling},{seg.anomaly},{seg.id},{seg.train}\n"
        for seg in segments
        for stamp, value in seg.samples
    ]
    path.write_text(HEADER + "".join(rows), encoding="utf-8", newline="\n")
    return path


def expected_by_channel() -> dict[str, list[tuple[datetime, float]]]:
    """Per channel, the (mission_ts, value) pairs of the file, in time order."""
    per_channel: dict[str, list[tuple[datetime, float]]] = {}
    for seg in SEGMENTS:
        per_channel.setdefault(seg.channel, []).extend(
            (datetime.fromisoformat(stamp), float(value)) for stamp, value in seg.samples
        )
    return {channel: sorted(points) for channel, points in sorted(per_channel.items())}
