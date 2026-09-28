# SPDX-License-Identifier: Apache-2.0
"""The walk-forward protocol of ADR 0001, and the checks that refuse to break it.

Every check raises :class:`ProtocolError` before anything is scored: a number that
cannot be produced honestly is not produced at all.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import ValidationError

from groundhog.detectors.r0_limits import LimitsConfig
from groundhog.eval.ground_truth import LabelledEvent
from groundhog.replay.config import Window

__all__ = [
    "Embargo",
    "Fold",
    "ProtocolError",
    "check_boundaries",
    "check_data_version",
    "check_limits",
    "check_usable",
    "embargo",
    "folds",
]


class ProtocolError(Exception):
    """Evaluating as asked would break the protocol."""


@dataclass(frozen=True)
class Embargo:
    """The stretch of data between a fold's training data and its test block.

    Computed, never chosen (ADR 0001, condition 1): the longest lookback of the
    detectors under evaluation plus the longest labelled event, over all labels.
    """

    lookback_s: float
    longest_event_s: float

    @property
    def seconds(self) -> float:
        return self.lookback_s + self.longest_event_s


@dataclass(frozen=True)
class Fold:
    """One fold of the walk-forward: a test block, and the training data before it."""

    number: int
    test_start: datetime
    test_end: datetime
    training_end: datetime
    """Training data is everything before this instant: the test start minus the embargo."""

    @property
    def test(self) -> Window:
        return Window(start=self.test_start, end=self.test_end)

    @property
    def training(self) -> Window:
        return Window(start=None, end=self.training_end)


def embargo(lookbacks_s: Iterable[float], events: Sequence[LabelledEvent]) -> Embargo:
    """The embargo for detectors with these lookbacks, on these labels."""
    return Embargo(lookback_s=max(lookbacks_s), longest_event_s=max(e.duration_s for e in events))


def folds(blocks: Sequence[tuple[datetime, datetime]], embargo: Embargo) -> list[Fold]:
    """A rolling origin: each block tested after training on everything before it,
    minus the embargo."""
    gap = timedelta(seconds=embargo.seconds)
    return [
        Fold(number=n, test_start=start, test_end=end, training_end=start - gap)
        for n, (start, end) in enumerate(blocks, start=1)
    ]


def check_data_version(lock: Path, dataset: str, file: str, found_md5: str) -> None:
    """The data must be the version recorded in the lock when it was fetched."""
    try:
        entries = json.loads(lock.read_text(encoding="utf-8"))[dataset]["files"]
        recorded = next(str(e["checksum"]) for e in entries if e["file"] == file)
    except FileNotFoundError:
        raise ProtocolError(
            f"{lock} not found: fetch the data with `make data-fetch DS={dataset}`"
        ) from None
    except (KeyError, StopIteration, TypeError, ValueError):
        raise ProtocolError(f"{lock} records no checksum for {dataset}/{file}") from None
    algorithm, _, expected = recorded.partition(":")
    if algorithm != "md5":
        raise ProtocolError(f"{lock} records a {algorithm} checksum for {file}, not md5")
    if found_md5 != expected:
        raise ProtocolError(
            f"{file} is not the data version recorded in {lock}: "
            f"expected md5 {expected}, found md5 {found_md5}"
        )


def check_limits(limits: LimitsConfig, data_md5: str, fold: Fold) -> None:
    """Refuse limits fitted on other data, or on anything but this fold's training data.

    The limits are judged by their own provenance, as written in the limits file:
    the data version, and the training window they were fitted on. That window must
    end before the embargo: one reaching into the embargo or the test block
    overlaps what the fold tests, and one reaching past it fits on the future.
    """
    found = limits.provenance.get("data_md5")
    if found != data_md5:
        raise ProtocolError(
            f"fold {fold.number}: the limits were fitted on another data version: "
            f"expected md5 {data_md5}, found md5 {found}"
        )
    try:
        training = Window.model_validate(limits.provenance["training"])
    except (KeyError, ValidationError):
        raise ProtocolError(
            f"fold {fold.number}: the limits' provenance records no training window"
        ) from None
    if training.end is None or training.end > fold.training_end:
        raise ProtocolError(
            f"fold {fold.number}: the limits were fitted on "
            f"{_span(training.start, training.end)}, which overlaps the test block or the "
            f"embargo before it, {_span(fold.training_end, fold.test_end)}, or goes beyond"
        )


def check_boundaries(folds: Sequence[Fold], events: Sequence[LabelledEvent]) -> None:
    """No labelled event may straddle the start or the end of a test block: every
    event belongs whole to one block, or to none (ADR 0009)."""
    cut = [
        f"{event.id} ({_span(event.start, event.end)}) at {boundary.isoformat()}"
        for fold in folds
        for boundary in (fold.test_start, fold.test_end)
        for event in events
        if event.start < boundary <= event.end
    ]
    if cut:
        raise ProtocolError("test block boundaries cut labelled events: " + "; ".join(cut))


def check_usable(
    fold: Fold,
    events: Sequence[LabelledEvent],
    tested: Iterable[str],
    nominal: Mapping[str, Sequence[float]],
    min_events: int,
) -> None:
    """ADR 0001, condition 4: enough labelled events in the test block, and nominal
    training data for every channel it tests."""
    if len(events) < min_events:
        raise ProtocolError(
            f"fold {fold.number}: {len(events)} labelled events in the test block, "
            f"fewer than the {min_events} a fold needs"
        )
    if untrained := sorted(c for c in tested if not nominal.get(c)):
        raise ProtocolError(
            f"fold {fold.number}: no nominal training data for {', '.join(untrained)}"
        )


def _span(start: datetime | None, end: datetime | None) -> str:
    return f"[{'the beginning' if start is None else start.isoformat()}, " + (
        "the end)" if end is None else f"{end.isoformat()})"
    )
