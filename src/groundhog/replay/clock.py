# SPDX-License-Identifier: Apache-2.0
"""Wall time, injected: the engine never reads the system clock itself.

Tests pass a clock that jumps instead of waiting, so pacing and gap fidelity are
checked to the microsecond without spending real time.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Protocol

__all__ = ["Clock", "SystemClock"]


class Clock(Protocol):
    def now(self) -> datetime:
        """Current wall time, UTC."""
        ...

    def sleep_until(self, deadline: datetime, stop: threading.Event) -> bool:
        """Wait until ``deadline``. Return True, early, once ``stop`` is set."""
        ...


class SystemClock:
    """Real wall time: UTC read once, then advanced by the performance counter.

    A system clock adjustment during a replay therefore neither stalls nor rushes
    the stream. ``perf_counter`` rather than ``monotonic``: on Windows the latter
    ticks every 15.6 ms, coarser than the 1 ms steps of a 1000x replay.
    """

    def __init__(self, max_sleep_s: float) -> None:
        if max_sleep_s <= 0:
            raise ValueError("max_sleep_s must be positive")
        self._max_sleep_s = max_sleep_s
        self._wall0 = datetime.now(UTC)
        self._counter0 = time.perf_counter()

    def now(self) -> datetime:
        return self._wall0 + timedelta(seconds=time.perf_counter() - self._counter0)

    def sleep_until(self, deadline: datetime, stop: threading.Event) -> bool:
        # Short naps instead of one long wait: a stop request is noticed within
        # max_sleep_s even where a signal does not cut a sleep short.
        while not stop.is_set():
            remaining = (deadline - self.now()).total_seconds()
            if remaining <= 0:
                return False
            time.sleep(min(remaining, self._max_sleep_s))
        return True
