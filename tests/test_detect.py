# SPDX-License-Identifier: Apache-2.0
"""Detection end to end: the pipeline in process, then ``replay | detect`` for real.

The limits below are built around the synthetic file (tests/synthetic_opssat.py), so
every event is known in advance:

* CH_A reads 1.00-1.04, then 1.10, 1.11, 1.12 after the gap: above 1.05 three times.
  It fires on the second (persistence 2), dated from the first, and its severity
  rises on the third. Still open when the stream ends.
* CH_B reads 2.00-2.04, then about -2e-05 three times: below 1.95. Fires on the
  second, dated from the first. Still open when the stream ends.
* CH_C is categorical and takes codes 0 1 2 1 0 2, with 0 and 1 allowed and no
  persistence: one event closed by the next allowed code, one still open at the end.
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from groundhog.arbiter import Arbiter, ArbiterSettings
from groundhog.detect.pipeline import DetectPipeline
from groundhog.detectors.r0_limits import LimitChecker, LimitsConfig
from groundhog.ingest.opssat import OpssatSource, OpssatSourceConfig
from groundhog.replay.config import CheckpointSettings, ReplaySettings, Window
from groundhog.replay.engine import ReplayEngine
from groundhog.schema import Event, Sample, Severity
from replay_doubles import WALL0, FakeClock, MemorySink, MemoryStore, write_replay_config

LIMITS: dict[str, Any] = {
    "mission": "opssat-test",
    "provenance": {"made": "by hand, around the synthetic file"},
    "defaults": {"hysteresis": 0.0, "persistence": {"samples": 2}, "max_gap_s": 3600},
    "channels": {
        "CH_A": {"check": "limits", "low": 0.95, "high": 1.05, "max_rate": None},
        "CH_B": {"check": "limits", "low": 1.95, "high": 2.05, "max_rate": None},
        "CH_C": {"check": "states", "allowed": [0, 1], "persistence": {"samples": 1}},
    },
}
BANDS = {"medium": 0.5, "high": 0.65}


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def event_id(channel: str, onset: str) -> str:
    return f"opssat-test:r0_limits:{channel}:{onset}.000000Z"


# Per event, the (t_end, severity) of each record published, in order.
EXPECTED = {
    event_id("CH_C", "20220104T200013"): [
        (None, Severity.HIGH),
        (utc("2022-01-04T20:00:13"), Severity.HIGH),
    ],
    event_id("CH_C", "20220104T200225"): [(None, Severity.HIGH), (None, Severity.HIGH)],
    event_id("CH_A", "20220104T230000"): [
        (None, Severity.MEDIUM),
        (None, Severity.HIGH),
        (None, Severity.HIGH),
    ],
    event_id("CH_B", "20220104T230001"): [(None, Severity.HIGH), (None, Severity.HIGH)],
}


def by_event(events: list[Event]) -> dict[str, list[tuple[datetime | None, Severity]]]:
    records: dict[str, list[tuple[datetime | None, Severity]]] = {}
    for event in events:
        records.setdefault(event.id, []).append((event.t_end, event.severity))
    return records


def replayed(config: OpssatSourceConfig) -> list[Sample]:
    """The synthetic file as the replay publishes it."""
    stop = threading.Event()
    sink = MemorySink()
    ReplayEngine(
        OpssatSource(config),
        sink,
        FakeClock(),
        MemoryStore(),
        ReplaySettings(speed=1000, seed=0, window=Window(start=None, end=None), max_sleep_s=1),
        CheckpointSettings(path=Path("unused"), every_samples=5, min_idle_s=1),
    ).run(stop)
    return sink.samples


def pipeline(limits: dict[str, Any] = LIMITS) -> DetectPipeline:
    detector = LimitChecker(LimitsConfig.model_validate(limits))
    settings = ArbiterSettings.model_validate({"severity": {"r0_limits": BANDS}})
    return DetectPipeline(detector, Arbiter(settings, {detector.id: None}, clock=lambda: WALL0))


def run(pipe: DetectPipeline, samples: list[Sample]) -> list[list[Event]]:
    return [pipe.feed(s) for s in samples]


def flat(batches: list[list[Event]]) -> list[Event]:
    return [event for batch in batches for event in batch]


class TestPipeline:
    def test_the_events_built_into_the_data(self, opssat_config: OpssatSourceConfig) -> None:
        pipe = pipeline()
        events = flat(run(pipe, replayed(opssat_config))) + pipe.finish()
        assert by_event(events) == EXPECTED
        detections = {e.id: e.detections[0] for e in events}
        assert detections[event_id("CH_A", "20220104T230000")].fired_at == utc(
            "2022-01-04T23:00:01"
        )
        assert detections[event_id("CH_A", "20220104T230000")].delay_s == 1.0
        assert detections[event_id("CH_C", "20220104T200013")].delay_s == 0.0

    def test_no_event_depends_on_a_later_sample(self, opssat_config: OpssatSourceConfig) -> None:
        samples = replayed(opssat_config)
        whole = run(pipeline(), samples)
        for k in range(len(samples) + 1):
            assert run(pipeline(), samples[:k]) == whole[:k]

    def test_repeated_samples_change_nothing(self, opssat_config: OpssatSourceConfig) -> None:
        samples = replayed(opssat_config)
        clean, resumed = pipeline(), pipeline()
        once = flat(run(clean, samples)) + clean.finish()
        # What an at-least-once replay sends after resuming from an older checkpoint.
        twice = flat(run(resumed, samples[:15] + samples[10:])) + resumed.finish()
        assert twice == once
        assert resumed.coverage.repeats == 5

    def test_coverage_counts_what_was_actually_monitored(
        self, opssat_config: OpssatSourceConfig
    ) -> None:
        unchecked = LIMITS | {
            "channels": LIMITS["channels"] | {"CH_B": {"check": "none", "reason": "for the test"}}
        }
        pipe = pipeline(unchecked)
        samples = replayed(opssat_config)
        events = flat(run(pipe, samples)) + pipe.finish()
        channel_b = sum(s.channel == "CH_B" for s in samples)
        assert pipe.coverage.samples == len(samples)
        assert pipe.coverage.scored == len(samples) - channel_b
        assert pipe.coverage.ratio == pytest.approx(1 - channel_b / len(samples))
        assert all(e.channels[0].channel != "CH_B" for e in events)


def write_detect_config(tmp_path: Path, limits: dict[str, Any] = LIMITS, **extra: Any) -> Path:
    limits_path = tmp_path / "limits.yaml"
    limits_path.write_text(yaml.safe_dump(limits), encoding="utf-8")
    tree = {
        "detector": {"type": "r0_limits", "limits": str(limits_path)},
        "arbiter": {"severity": {"r0_limits": BANDS}},
        "max_wait_s": 0.05,
    } | extra
    path = tmp_path / "detect.yaml"
    path.write_text(yaml.safe_dump(tree), encoding="utf-8")
    return path


def detect(config: Path, **popen: Any) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-m", "groundhog.detect", "--config", str(config)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        **popen,
    )


def lines(samples: list[Sample]) -> str:
    return "".join(s.model_dump_json() + "\n" for s in samples)


class TestCommandLine:
    def test_replay_piped_into_detect(
        self, tmp_path: Path, opssat_config: OpssatSourceConfig
    ) -> None:
        replay = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "groundhog.replay",
                "--config",
                str(write_replay_config(tmp_path, opssat_config)),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        detection = detect(write_detect_config(tmp_path), stdin=replay.stdout)
        assert replay.stdout is not None
        replay.stdout.close()  # detect alone reads it now
        out, err = detection.communicate(timeout=120)
        assert (replay.wait(timeout=120), detection.returncode) == (0, 0), err

        header, *records = out.splitlines()
        assert json.loads(header)["run"]["tool"] == "groundhog.detect"
        assert by_event([Event.model_validate_json(r) for r in records]) == EXPECTED
        assert "coverage 100.0 %" in err

    def test_the_run_header_names_the_detector_configuration(self, tmp_path: Path) -> None:
        proc = detect(write_detect_config(tmp_path), stdin=subprocess.PIPE)
        out, _ = proc.communicate("", timeout=60)
        run = json.loads(out.splitlines()[0])["run"]
        assert set(run) == {"tool", "version", "config", "detectors", "arbiter_sha256"}
        (detector,) = run["detectors"]
        expected = LimitChecker(LimitsConfig.model_validate(LIMITS)).config_hash
        assert detector == {"id": "r0_limits", "model_version": None, "config_sha256": expected}

    def test_each_sample_is_answered_before_the_next_one_arrives(
        self, tmp_path: Path, opssat_config: OpssatSourceConfig
    ) -> None:
        proc = detect(write_detect_config(tmp_path), stdin=subprocess.PIPE)
        assert proc.stdin is not None and proc.stdout is not None
        received: queue.Queue[str] = queue.Queue()
        threading.Thread(
            target=lambda: [received.put(line) for line in proc.stdout],
            daemon=True,  # type: ignore[union-attr]
        ).start()
        assert "run" in json.loads(received.get(timeout=60))

        samples = replayed(opssat_config)
        trigger = next(i for i, s in enumerate(samples) if s.channel == "CH_C" and s.value == 2)
        proc.stdin.write(lines(samples[: trigger + 1]))  # nothing after the sample that fires
        proc.stdin.flush()
        opened = Event.model_validate_json(received.get(timeout=60))
        assert opened.id == event_id("CH_C", "20220104T200013") and opened.t_end is None

        proc.stdin.write(lines(samples[trigger + 1 :]))
        proc.stdin.close()
        assert proc.wait(timeout=60) == 0

    def test_a_line_that_is_not_a_sample_stops_the_run(self, tmp_path: Path) -> None:
        proc = detect(write_detect_config(tmp_path), stdin=subprocess.PIPE)
        _, err = proc.communicate('{"hello": "world"}\n', timeout=60)
        assert proc.returncode == 2 and "stdin line 1 is not a sample" in err

    def test_a_channel_without_limits_stops_the_run(
        self, tmp_path: Path, opssat_config: OpssatSourceConfig
    ) -> None:
        without_b = LIMITS | {
            "channels": {k: v for k, v in LIMITS["channels"].items() if k != "CH_B"}
        }
        proc = detect(write_detect_config(tmp_path, without_b), stdin=subprocess.PIPE)
        _, err = proc.communicate(lines(replayed(opssat_config)), timeout=60)
        assert proc.returncode == 2 and "CH_B has no entry in the limits" in err

    def test_a_reader_that_goes_away_ends_detect_cleanly(
        self, tmp_path: Path, opssat_config: OpssatSourceConfig
    ) -> None:
        proc = detect(write_detect_config(tmp_path), stdin=subprocess.PIPE)
        assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
        proc.stdout.readline()  # the run header
        proc.stdout.close()
        proc.stdin.write(lines(replayed(opssat_config)))  # events to write, nobody to read them
        proc.stdin.close()
        err = proc.stderr.read()
        assert proc.wait(timeout=60) == 1
        assert "cannot write the stream" in err and "Traceback" not in err

    def test_an_incomplete_configuration_is_refused(self, tmp_path: Path) -> None:
        config = write_detect_config(tmp_path)
        tree = yaml.safe_load(config.read_text(encoding="utf-8"))
        del tree["max_wait_s"]
        config.write_text(yaml.safe_dump(tree), encoding="utf-8")
        proc = detect(config, stdin=subprocess.PIPE)
        _, err = proc.communicate("", timeout=60)
        assert proc.returncode == 2 and "max_wait_s" in err
