# SPDX-License-Identifier: Apache-2.0
"""Where a replay stands, kept so that an interrupted replay resumes exactly.

The delivery guarantees built on it are in docs/adr/0004-replay-delivery-and-checkpoints.md.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

__all__ = [
    "Checkpoint",
    "CheckpointError",
    "CheckpointStore",
    "JsonFileCheckpointStore",
    "Position",
]


class CheckpointError(Exception):
    """A checkpoint that cannot be used to resume."""


class Position(BaseModel):
    """The last sample published.

    Stream order is (mission_ts, channel rank), unique for every sample, so these
    two fields pin one point of the stream.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    mission_ts: AwareDatetime
    channel: str = Field(min_length=1)


class Checkpoint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    stream: str = Field(min_length=1, description="Fingerprint of the stream it belongs to")
    published: int = Field(ge=0, description="Samples published so far, across resumes")
    last: Position | None = Field(description="Last sample published; null before the first")
    mission_clock: AwareDatetime = Field(
        description="Mission time reached when saved; a resumed replay starts its clock here"
    )


class CheckpointStore(Protocol):
    def load(self) -> Checkpoint | None:
        """The saved checkpoint, or None when there is none."""
        ...

    def save(self, checkpoint: Checkpoint) -> None:
        """Replace the saved checkpoint, atomically."""
        ...

    def clear(self) -> None:
        """Forget the saved checkpoint, if any."""
        ...


class JsonFileCheckpointStore:
    """A checkpoint as a local JSON file, replaced atomically on every save."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> Checkpoint | None:
        try:
            raw = self._path.read_bytes()
        except FileNotFoundError:
            return None
        try:
            return Checkpoint.model_validate_json(raw)
        except ValidationError as exc:
            raise CheckpointError(f"not a valid checkpoint: {exc}") from None

    def save(self, checkpoint: Checkpoint) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        partial = self._path.with_name(self._path.name + ".tmp")
        with partial.open("wb") as fh:
            fh.write(checkpoint.model_dump_json(indent=2).encode() + b"\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(partial, self._path)

    def clear(self) -> None:
        self._path.unlink(missing_ok=True)
