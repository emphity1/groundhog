# SPDX-License-Identifier: Apache-2.0
"""Source adapters: one per dataset, all yielding readings in the common schema.

The replay engine depends only on :class:`Source`, never on a particular dataset.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import datetime
from typing import Any, Protocol

from groundhog.schema import Reading

__all__ = ["IngestError", "Source"]


class IngestError(Exception):
    """The source data is missing, malformed, or disagrees with its configuration."""


class Source(Protocol):
    """Where readings come from, one channel at a time."""

    def channels(self) -> tuple[str, ...]:
        """Every channel the source holds."""
        ...

    def readings(
        self, channel: str, start: datetime | None, end: datetime | None
    ) -> Iterator[Reading]:
        """Readings of ``channel`` with ``start <= mission_ts < end``, in strictly
        increasing mission time. ``None`` leaves that side of the window open."""
        ...

    def describe(self) -> Mapping[str, Any]:
        """What this source yields, as JSON-serialisable data: the data version and
        every setting that shapes a reading. Equal descriptions, equal readings."""
        ...
