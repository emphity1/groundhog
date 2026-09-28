# SPDX-License-Identifier: Apache-2.0
"""Records as JSON lines, the way every Groundhog stream is written."""

from __future__ import annotations

from typing import BinaryIO

from pydantic import BaseModel

__all__ = ["JsonLinesWriter", "OutputError"]


class OutputError(Exception):
    """The output cannot be written: its reader went away, or its medium failed."""


class JsonLinesWriter:
    """One record per line as JSON: UTF-8, LF-terminated on every platform."""

    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream

    def write(self, record: BaseModel) -> None:
        try:
            self._stream.write(record.model_dump_json().encode() + b"\n")
        except OSError as exc:
            raise OutputError(f"cannot write the stream: {exc}") from exc

    def flush(self) -> None:
        # A reader that has gone away shows up as EPIPE on POSIX and EINVAL on Windows.
        try:
            self._stream.flush()
        except OSError as exc:
            raise OutputError(f"cannot write the stream: {exc}") from exc
