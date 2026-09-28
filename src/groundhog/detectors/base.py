# SPDX-License-Identifier: Apache-2.0
"""The interface every detector implements, from limit checking to neural models.

A detector reads samples one at a time, in stream order, and answers each with a
:class:`~groundhog.schema.Score` or with nothing (docs/adr/0005-detector-scores.md):

* **No access to the future.** :meth:`Detector.update` receives one sample and
  decides with what it has seen so far. Nothing hands a detector the rest of the
  stream, and the tests check that no answer depends on what comes later.
* **State per channel.** A detector keeps whatever history it needs per
  ``(mission, channel)``; :meth:`Detector.reset` forgets all of it.
* **Nothing is not zero.** ``None`` means no opinion. A sample the detector
  evaluated and found nominal gets a score with ``value == 0``.

Nothing here assumes thresholds: a model answers with its own graded scores and
its own alarm decision, exactly as limit checking does.
"""

from __future__ import annotations

import hashlib
import json
from typing import Protocol

from pydantic import BaseModel

from groundhog.schema import DetectorId, Sample, Score

__all__ = ["Detector", "DetectorError", "config_sha256"]


class DetectorError(Exception):
    """The detector cannot handle this input as configured."""


class Detector(Protocol):
    @property
    def id(self) -> DetectorId: ...

    @property
    def model_version(self) -> str | None:
        """Registry version of a trained model; None for rule-based detectors."""
        ...

    @property
    def config_hash(self) -> str:
        """SHA-256 of everything that determines the scores, for the run header."""
        ...

    @property
    def lookback_s(self) -> float:
        """The longest stretch of mission time, before a sample, that the decision to
        fire on it can depend on. Part of the embargo between a training period and
        the test period after it (ADR 0001)."""
        ...

    def update(self, sample: Sample) -> Score | None:
        """Answer the next sample of the stream: a score, or None for no opinion."""
        ...

    def reset(self) -> None:
        """Forget all state, for every channel."""
        ...


def config_sha256(config: BaseModel) -> str:
    """Hash of a configuration's content, independent of key order and formatting."""
    canonical = json.dumps(config.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
