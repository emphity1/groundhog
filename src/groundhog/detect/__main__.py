# SPDX-License-Identifier: Apache-2.0
"""Detect anomalies in a stream of samples: ``python -m groundhog.detect --config FILE``.

Samples arrive on stdin as JSON lines, exactly as the replay writes them:

    python -m groundhog.replay --config configs/replay/opssat.yaml |
      python -m groundhog.detect --config configs/detect/opssat_r0.yaml

stdout carries a run header, ``{"run": {...}}``, then events as JSON lines; logs go
to stderr (docs/adr/0006-event-stream.md). Ctrl+C or SIGTERM stops gracefully: the
events still open are published, with ``t_end`` null, before the command exits.

Exit status (``groundhog.cli``): 0 input exhausted, 130 stopped by a signal, 1 output
not writable, 2 unusable configuration or input.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from queue import Empty, Queue
from typing import BinaryIO

import yaml
from pydantic import ValidationError

from groundhog import __version__
from groundhog.arbiter import Arbiter, ArbiterError
from groundhog.cli import (
    EXIT_OK,
    EXIT_OUTPUT_CLOSED,
    EXIT_STOPPED,
    EXIT_UNUSABLE_INPUT,
    log_to_stderr,
    silence_stdout,
    stop_on_signals,
)
from groundhog.detect.config import load_config
from groundhog.detect.pipeline import DetectorRun, DetectPipeline, RunHeader, RunInfo
from groundhog.detectors.base import DetectorError, config_sha256
from groundhog.detectors.r0_limits import LimitChecker, load_limits
from groundhog.jsonlines import JsonLinesWriter, OutputError
from groundhog.schema import Sample

log = logging.getLogger("groundhog.detect")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m groundhog.detect",
        description="Detect anomalies in a stream of samples: JSON lines in, events out.",
    )
    parser.add_argument(
        "--config", type=Path, required=True, help="e.g. configs/detect/opssat_r0.yaml"
    )
    args = parser.parse_args(argv)
    log_to_stderr()

    try:
        config = load_config(args.config)
        detector = LimitChecker(load_limits(config.detector.limits))
        arbiter = Arbiter(
            config.arbiter, {detector.id: detector.model_version}, clock=lambda: datetime.now(UTC)
        )
    except (OSError, yaml.YAMLError, ValidationError, ArbiterError) as exc:
        log.error("%s", exc)
        return EXIT_UNUSABLE_INPUT

    pipeline = DetectPipeline(detector, arbiter)
    header = RunHeader(
        run=RunInfo(
            tool="groundhog.detect",
            version=__version__,
            config=args.config.as_posix(),
            detectors=(
                DetectorRun(
                    id=detector.id,
                    model_version=detector.model_version,
                    config_sha256=detector.config_hash,
                ),
            ),
            arbiter_sha256=config_sha256(config.arbiter),
        )
    )
    out = JsonLinesWriter(sys.stdout.buffer)
    stop = threading.Event()
    stop_on_signals(stop)
    try:
        out.write(header)
        out.flush()
        for number, line in _lines(sys.stdin.buffer, stop, config.max_wait_s):
            if not line.strip():
                continue
            try:
                events = pipeline.feed(Sample.model_validate_json(line))
            except ValidationError as exc:
                log.error("stdin line %d is not a sample: %s", number, exc)
                return EXIT_UNUSABLE_INPUT
            except DetectorError as exc:
                log.error("stdin line %d: %s", number, exc)
                return EXIT_UNUSABLE_INPUT
            if events:
                for event in events:
                    out.write(event)
                out.flush()
        for event in pipeline.finish():
            out.write(event)
        out.flush()
    except OutputError as exc:
        silence_stdout()
        log.warning("%s", exc)
        return EXIT_OUTPUT_CLOSED
    except OSError as exc:
        log.error("cannot read stdin: %s", exc)
        return EXIT_UNUSABLE_INPUT
    except KeyboardInterrupt:
        log.warning("interrupted twice: stopped at once, open events not published")
        return EXIT_STOPPED

    seen = pipeline.coverage
    log.info(
        "%d samples, %d repeats dropped, %d scored (coverage %.1f %%), %d firing",
        seen.samples,
        seen.repeats,
        seen.scored,
        100 * seen.ratio,
        seen.firing,
    )
    if stop.is_set():
        log.warning("stopped before the end of the input; open events published, t_end null")
        return EXIT_STOPPED
    return EXIT_OK


def _lines(
    stream: BinaryIO, stop: threading.Event, max_wait_s: float
) -> Iterator[tuple[int, bytes]]:
    """The lines of ``stream`` as they arrive, until its end or until ``stop`` is set.

    A reader thread does the blocking reads, so a stop request never waits for input
    that may not come: it is noticed within ``max_wait_s``, between two samples.
    """
    queue: Queue[bytes | OSError | None] = Queue()

    def pump() -> None:
        try:
            for line in stream:
                queue.put(line)
        except OSError as exc:
            queue.put(exc)
        queue.put(None)

    threading.Thread(target=pump, name="stdin", daemon=True).start()
    number = 0
    while not stop.is_set():
        try:
            item = queue.get(timeout=max_wait_s)
        except Empty:
            continue
        if isinstance(item, OSError):
            raise item
        if item is None:
            return
        number += 1
        yield number, item


if __name__ == "__main__":
    raise SystemExit(main())
