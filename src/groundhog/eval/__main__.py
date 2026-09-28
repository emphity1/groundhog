# SPDX-License-Identifier: Apache-2.0
"""Run the benchmark a configuration describes: ``python -m groundhog.eval --config FILE``.

    python -m groundhog.eval --config configs/eval/opssat_r0.yaml      (or: make evaluate)

Writes ``report.md`` and ``report.json`` to the configuration's output directory, with
the limits fitted and the events raised in every run next to them. Logs go to stderr;
stdout gets the paths of the two reports.

Exit status (``groundhog.cli``): 0 report written; 2 unusable configuration or data, or
an evaluation that would break the protocol, with the reason logged.
"""

from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import ValidationError

from groundhog.arbiter import ArbiterError
from groundhog.cli import EXIT_OK, EXIT_UNUSABLE_INPUT, log_to_stderr
from groundhog.detectors.base import DetectorError
from groundhog.eval.config import load_config
from groundhog.eval.harness import evaluate
from groundhog.eval.protocol import ProtocolError
from groundhog.eval.report import run_header, write
from groundhog.ingest import IngestError

log = logging.getLogger("groundhog.eval")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m groundhog.eval",
        description="Evaluate a detector on a replayed archive and write the report.",
    )
    parser.add_argument(
        "--config", type=Path, required=True, help="e.g. configs/eval/opssat_r0.yaml"
    )
    args = parser.parse_args(argv)
    log_to_stderr()
    started = datetime.now(UTC)

    try:
        config = load_config(args.config)
        for stale in ("report.md", "report.json"):  # a failed run leaves no report behind
            (config.output / stale).unlink(missing_ok=True)
        evaluation = evaluate(config)
    except ProtocolError as exc:
        log.error("refused: %s", exc)
        return EXIT_UNUSABLE_INPUT
    except (
        OSError,
        yaml.YAMLError,
        ValidationError,
        IngestError,
        DetectorError,
        ArbiterError,
    ) as exc:
        log.error("%s", exc)
        return EXIT_UNUSABLE_INPUT

    for path in write(evaluation, run_header(args.config, started), config.output):
        print(path.as_posix())
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
