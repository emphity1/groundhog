# SPDX-License-Identifier: Apache-2.0
"""The arbiter: detector scores in, events out (ARCHITECTURE §4.8, docs/adr/0006-event-stream.md)."""

from groundhog.arbiter.arbiter import (
    Arbiter,
    ArbiterError,
    ArbiterSettings,
    EventHook,
    PassThrough,
    SeverityBands,
)

__all__ = [
    "Arbiter",
    "ArbiterError",
    "ArbiterSettings",
    "EventHook",
    "PassThrough",
    "SeverityBands",
]
