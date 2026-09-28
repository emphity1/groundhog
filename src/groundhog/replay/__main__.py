# SPDX-License-Identifier: Apache-2.0
"""Replay a mission window as a live stream: ``python -m groundhog.replay --config FILE``.

Samples go to stdout as JSON lines, logs to stderr. Ctrl+C or SIGTERM stops
gracefully and leaves a checkpoint that the next run resumes from; a second Ctrl+C
stops at once.

Exit status (``groundhog.cli``): 0 complete, 130 stopped early (resumable), 1 output
not writable (usually: its reader went away), 2 unusable configuration, data or
checkpoint.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
from pathlib import Path

import yaml
from pydantic import ValidationError

from groundhog.cli import (
    EXIT_OK,
    EXIT_OUTPUT_CLOSED,
    EXIT_STOPPED,
    EXIT_UNUSABLE_INPUT,
    log_to_stderr,
    silence_stdout,
    stop_on_signals,
)
from groundhog.ingest import IngestError
from groundhog.ingest.opssat import OpssatSource
from groundhog.replay.checkpoint import CheckpointError, JsonFileCheckpointStore
from groundhog.replay.clock import SystemClock
from groundhog.replay.config import load_config
from groundhog.replay.engine import ReplayEngine
from groundhog.replay.sinks import JsonlSink, SinkError

log = logging.getLogger("groundhog.replay")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m groundhog.replay",
        description="Replay a mission window as a live stream of JSON lines on stdout.",
    )
    parser.add_argument(
        "--config", type=Path, required=True, help="e.g. configs/replay/opssat.yaml"
    )
    parser.add_argument(
        "--restart",
        action="store_true",
        help="discard the checkpoint of an unfinished replay and start from the beginning",
    )
    args = parser.parse_args(argv)
    log_to_stderr()

    try:
        config = load_config(args.config)
        source = OpssatSource(config.source)
    except (OSError, yaml.YAMLError, ValidationError, IngestError) as exc:
        log.error("%s", exc)
        return EXIT_UNUSABLE_INPUT

    store = JsonFileCheckpointStore(config.checkpoint.path)
    if args.restart:
        store.clear()
    sink = JsonlSink(sys.stdout.buffer)
    engine = ReplayEngine(
        source,
        sink,
        SystemClock(config.replay.max_sleep_s),
        store,
        config.replay,
        config.checkpoint,
    )
    log.info("config %s; source %s", args.config, json.dumps(source.describe(), sort_keys=True))

    stop = threading.Event()
    stop_on_signals(stop)
    try:
        result = engine.run(stop)
        sink.close()
    except CheckpointError as exc:
        log.error("checkpoint %s: %s; pass --restart to start over", store.path, exc)
        return EXIT_UNUSABLE_INPUT
    except SinkError as exc:
        silence_stdout()
        log.warning("%s; the checkpoint stays at its last save", exc)
        return EXIT_OUTPUT_CLOSED
    except KeyboardInterrupt:
        log.warning("interrupted twice: stopped at once, the checkpoint stays at its last save")
        return EXIT_STOPPED
    return EXIT_OK if result.completed else EXIT_STOPPED


if __name__ == "__main__":
    raise SystemExit(main())
