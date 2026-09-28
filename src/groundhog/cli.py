# SPDX-License-Identifier: Apache-2.0
"""What every Groundhog command shares: exit status, logging, signals, a closed stdout.

Exit status: 0 done, 1 output not writable (usually: its reader went away),
2 unusable configuration or input, 130 stopped early by a signal.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
from types import FrameType

__all__ = [
    "EXIT_OK",
    "EXIT_OUTPUT_CLOSED",
    "EXIT_STOPPED",
    "EXIT_UNUSABLE_INPUT",
    "log_to_stderr",
    "silence_stdout",
    "stop_on_signals",
]

EXIT_OK = 0
EXIT_OUTPUT_CLOSED = 1
EXIT_UNUSABLE_INPUT = 2
EXIT_STOPPED = 130


def log_to_stderr() -> None:
    """Logs on stderr: stdout carries the stream and nothing else."""
    logging.basicConfig(
        stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )


def stop_on_signals(stop: threading.Event) -> None:
    """First Ctrl+C or SIGTERM: set ``stop`` for a graceful stop. A second: at once."""

    def graceful(signum: int, frame: FrameType | None) -> None:
        stop.set()
        signal.signal(
            signum, signal.default_int_handler if signum == signal.SIGINT else signal.SIG_DFL
        )

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):  # SIGBREAK: Ctrl+Break, Windows only
        signum = getattr(signal, name, None)
        if signum is not None:
            signal.signal(signum, graceful)


def silence_stdout() -> None:
    """Point stdout at devnull once its reader has gone, so that the interpreter's
    final flush does not fail a second time."""
    os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
