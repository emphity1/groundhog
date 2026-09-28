# SPDX-License-Identifier: Apache-2.0
"""Where published samples go. M1 writes JSON lines; Redpanda arrives in M2."""

from __future__ import annotations

from typing import BinaryIO, Protocol

from groundhog.schema import Sample

__all__ = ["JsonlSink", "Sink", "SinkError"]


class SinkError(Exception):
    """The sink cannot deliver: its reader went away, or its medium failed."""


class Sink(Protocol):
    def write(self, sample: Sample) -> None:
        """Take one sample. It may stay buffered until :meth:`flush`."""
        ...

    def flush(self) -> None:
        """Deliver everything written so far. The engine checkpoints only after this."""
        ...

    def close(self) -> None:
        """Flush and release whatever the sink owns."""
        ...


class JsonlSink:
    """One sample per line as JSON: UTF-8, LF-terminated on every platform."""

    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream

    def write(self, sample: Sample) -> None:
        try:
            self._stream.write(sample.model_dump_json().encode() + b"\n")
        except OSError as exc:
            raise SinkError(f"cannot write the stream: {exc}") from exc

    def flush(self) -> None:
        # A reader that has gone away shows up as EPIPE on POSIX and EINVAL on Windows.
        try:
            self._stream.flush()
        except OSError as exc:
            raise SinkError(f"cannot write the stream: {exc}") from exc

    def close(self) -> None:
        # The stream belongs to the caller (stdout, usually): flush it, never close it.
        self.flush()
