# SPDX-License-Identifier: Apache-2.0
"""``python -m groundhog.replay`` end to end, in a real subprocess with the real clock.

The configurations use a speed of 1e9, so the three-hour gap of the synthetic file
lasts about 11 microseconds of wall time.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

import pytest
import yaml

from groundhog.ingest.opssat import OpssatSource, OpssatSourceConfig
from groundhog.replay.checkpoint import JsonFileCheckpointStore
from groundhog.replay.config import load_config
from groundhog.replay.engine import ReplayEngine
from groundhog.schema import Sample
from replay_doubles import FakeClock, MemorySink
from replay_doubles import write_replay_config as write_config
from synthetic_opssat import TOTAL


def cli(*args: str | Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "groundhog.replay", *map(str, args)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )


def stream(stdout: str) -> list[tuple[str, str, float]]:
    samples = [Sample.model_validate_json(line) for line in stdout.splitlines()]
    return [(s.channel, s.mission_ts.isoformat(), s.value) for s in samples]


def interrupt_after(config_path: Path, k: int) -> None:
    """Leave the checkpoint an interrupted run would: k samples published."""
    config = load_config(config_path)
    stop = threading.Event()
    engine = ReplayEngine(
        OpssatSource(config.source),
        MemorySink(stop, stop_after=k),
        FakeClock(),
        JsonFileCheckpointStore(config.checkpoint.path),
        config.replay,
        config.checkpoint,
    )
    assert not engine.run(stop).completed


@pytest.fixture
def config_path(tmp_path: Path, opssat_config: OpssatSourceConfig) -> Path:
    return write_config(tmp_path, opssat_config)


def test_streams_every_sample_as_a_json_line_and_leaves_no_checkpoint(
    config_path: Path,
) -> None:
    done = cli("--config", config_path)
    assert done.returncode == 0, done.stderr
    assert len(stream(done.stdout)) == TOTAL
    assert "complete" in done.stderr
    assert not load_config(config_path).checkpoint.path.exists()


def test_two_runs_publish_the_same_stream(config_path: Path) -> None:
    first, second = cli("--config", config_path), cli("--config", config_path)
    assert stream(first.stdout) == stream(second.stdout)


def test_resumes_where_an_interrupted_run_stopped(config_path: Path) -> None:
    whole = stream(cli("--config", config_path).stdout)
    interrupt_after(config_path, 7)
    resumed = cli("--config", config_path)
    assert resumed.returncode == 0, resumed.stderr
    assert stream(resumed.stdout) == whole[7:]
    assert "resuming after 7 samples" in resumed.stderr


def test_restart_discards_an_unfinished_checkpoint(config_path: Path) -> None:
    interrupt_after(config_path, 7)
    assert len(stream(cli("--config", config_path, "--restart").stdout)) == TOTAL


def test_refuses_the_checkpoint_of_another_stream(
    tmp_path: Path, opssat_config: OpssatSourceConfig
) -> None:
    interrupt_after(write_config(tmp_path, opssat_config, seed=0), 3)
    done = cli("--config", write_config(tmp_path, opssat_config, seed=1))
    assert done.returncode == 2
    assert "another stream" in done.stderr and "--restart" in done.stderr
    assert done.stdout == ""


def test_a_reader_that_goes_away_ends_the_replay_cleanly(
    tmp_path: Path, opssat_config: OpssatSourceConfig
) -> None:
    # At 5400x the three-hour gap lasts about two seconds: the reader closes the
    # pipe during it, and the next write finds nobody listening.
    config = write_config(tmp_path, opssat_config, speed=5400)
    proc = subprocess.Popen(
        [sys.executable, "-m", "groundhog.replay", "--config", str(config)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    assert proc.stdout is not None and proc.stderr is not None
    Sample.model_validate_json(proc.stdout.readline())
    proc.stdout.close()
    stderr = proc.stderr.read()
    assert proc.wait(timeout=60) == 1
    assert "cannot write the stream" in stderr and "Traceback" not in stderr


def test_missing_data_points_to_the_fetcher(
    tmp_path: Path, opssat_config: OpssatSourceConfig
) -> None:
    absent = opssat_config.model_copy(update={"path": tmp_path / "absent.csv"})
    done = cli("--config", write_config(tmp_path, absent))
    assert done.returncode == 2
    assert "make data-fetch DS=opssat" in done.stderr


def test_refuses_an_incomplete_configuration(tmp_path: Path, config_path: Path) -> None:
    tree = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    del tree["replay"]["seed"]
    config_path.write_text(yaml.safe_dump(tree), encoding="utf-8")
    done = cli("--config", config_path)
    assert done.returncode == 2
    assert "seed" in done.stderr
