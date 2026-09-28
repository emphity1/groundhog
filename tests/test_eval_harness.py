# SPDX-License-Identifier: Apache-2.0
"""The harness end to end, on a small archive whose every number is known in advance.

Two channels, 1 s sampling, values in [0.1, 0.9] when nominal. January 4 is training
data, and R0's limits fitted there are about [0.02, 0.98]. Two test blocks follow:

* block 1, 00:00-00:10 on January 5. A leaves its range for 30 s at 00:02. B holds
  one sample at 1.5, labelled nominal, at 00:00:30, and leaves its range at 00:05
  for the last 10 samples of the block, climbing: its alarm is still open when the
  block's data ends, and its peak keeps rising after it opened.
* block 2, 01:00-01:10. B leaves its range at 01:02 for 30 samples, with 100 s of
  silence after the first 15. R0 with ``max_gap_s`` 60 resets in that silence and
  alarms twice on the one labelled event; with 150 it alarms once (ADR 0008). Its
  training data now holds B's 1.5, so B's limits are wider than in block 1.

The archive ends at 01:20 with A out of range, outside both blocks: a test segment of
the official split whose alarm is still open when the stream ends.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from itertools import count
from pathlib import Path
from typing import Any

import pytest
import yaml

from groundhog.eval import harness
from groundhog.eval.__main__ import main
from groundhog.eval.config import EvalConfig
from groundhog.eval.harness import Distribution, Evaluation, evaluate
from groundhog.eval.protocol import ProtocolError
from groundhog.eval.report import DISCLAIMERS
from groundhog.ingest.opssat import ChannelSpec, OpssatSourceConfig
from groundhog.schema import ChannelKind
from replay_doubles import write_replay_config
from synthetic_opssat import HEADER

TRAINING = datetime(2022, 1, 4, 20, 0, tzinfo=UTC)
BLOCK_1 = datetime(2022, 1, 5, 0, 0, tzinfo=UTC)
BLOCK_2 = datetime(2022, 1, 5, 1, 0, tzinfo=UTC)
SECOND = timedelta(seconds=1)
CLIMB = [round(5.0 + 0.1 * i, 1) for i in range(10)]


def nominal(i: int) -> float:
    return round(0.5 + 0.4 * math.sin(i / 7), 6)


def archive() -> Iterator[str]:
    """segments.csv rows: channel, timestamp, value, label, sampling, anomaly, segment, train."""
    ids = count(1)

    def segment(
        channel: str, start: datetime, values: list[float], anomaly: int, train: int, at: Any = None
    ) -> Iterator[str]:
        seg = next(ids)
        for i, value in enumerate(values):
            moment = start + (at(i) if at else i * SECOND)
            stamp = moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            yield f"{channel},{stamp},{value},anomaly,1,{anomaly},{seg},{train}"

    def nominal_segments(channel: str, start: datetime, n: int, train: int = 0) -> Iterator[str]:
        for k in range(n):
            values = [nominal(60 * k + i) for i in range(60)]
            yield from segment(channel, start + 60 * k * SECOND, values, 0, train)

    for channel in ("A", "B"):
        yield from nominal_segments(channel, TRAINING, 10, train=1)

    yield from nominal_segments("A", BLOCK_1, 2)
    yield from segment("A", BLOCK_1 + 120 * SECOND, [5.0] * 30, 1, 0)
    yield from nominal_segments("A", BLOCK_1 + 150 * SECOND, 2)
    spiked = [1.5 if i == 30 else nominal(i) for i in range(60)]
    yield from segment("B", BLOCK_1, spiked, 0, 0)
    yield from nominal_segments("B", BLOCK_1 + 60 * SECOND, 4)
    yield from segment("B", BLOCK_1 + 300 * SECOND, CLIMB, 1, 0)

    yield from nominal_segments("A", BLOCK_2, 5)
    yield from nominal_segments("B", BLOCK_2, 2)
    silence = lambda i: (i + (99 if i >= 15 else 0)) * SECOND  # noqa: E731
    yield from segment("B", BLOCK_2 + 120 * SECOND, [5.0] * 30, 1, 0, silence)
    yield from nominal_segments("B", BLOCK_2 + 250 * SECOND, 2)

    yield from segment("A", BLOCK_2 + 20 * 60 * SECOND, [5.0] * 10, 1, 0)


@pytest.fixture
def setup(tmp_path: Path) -> EvalConfig:
    data = tmp_path / "segments.csv"
    data.write_text(HEADER + "\n".join(archive()) + "\n", encoding="utf-8", newline="\n")
    md5 = hashlib.md5(data.read_bytes()).hexdigest()
    lock = tmp_path / "datasets.lock.json"
    lock.write_text(
        json.dumps({"opssat": {"files": [{"file": "segments.csv", "checksum": f"md5:{md5}"}]}}),
        encoding="utf-8",
    )
    source = OpssatSourceConfig(
        type="opssat",
        path=data,
        mission="synthetic",
        channels={c: ChannelSpec(kind=ChannelKind.NUMERIC, unit=None) for c in ("A", "B")},
    )
    replay = write_replay_config(tmp_path, source)
    spec = tmp_path / "limits.derive.yaml"
    spec.write_text(
        yaml.safe_dump(
            {
                "source": str(replay),
                "training": {"start": None, "end": None},
                "margin": 0.1,
                "defaults": {"hysteresis": 0.05, "persistence": {"samples": 3}, "max_gap_s": 60},
                "output": str(tmp_path / "unused.yaml"),
            }
        ),
        encoding="utf-8",
    )
    detect = tmp_path / "detect.yaml"
    detect.write_text(
        yaml.safe_dump(
            {
                "detector": {"type": "r0_limits", "limits": str(tmp_path / "unused.yaml")},
                "arbiter": {"severity": {"r0_limits": {"medium": 0.1, "high": 0.5}}},
                "max_wait_s": 0.25,
            }
        ),
        encoding="utf-8",
    )
    return EvalConfig.model_validate(
        {
            "replay": replay,
            "detect": detect,
            "limits": spec,
            "lock": lock,
            "ground_truth": {"run_gap_periods": 2},
            "walk_forward": {
                "min_events": 1,
                "blocks": [
                    {"start": BLOCK_1, "end": BLOCK_1 + timedelta(minutes=10)},
                    {"start": BLOCK_2, "end": BLOCK_2 + timedelta(minutes=10)},
                ],
            },
            "metrics": {
                "beta": 0.5,
                "adtqc_exponent": math.e,
                "gap_sensitivity_s": [60, 150],
                "cpu_min_s": 0.05,
            },
            "output": tmp_path / "report",
        }
    )


@pytest.fixture
def evaluation(setup: EvalConfig) -> Evaluation:
    return evaluate(setup)


class TestNumbersKnownInAdvance:
    def test_the_protocol(self, evaluation: Evaluation) -> None:
        # Lookback (3 - 1) x 150 s for the longest max_gap_s; longest event 30 + 99 - 1 s.
        assert (evaluation.embargo.lookback_s, evaluation.embargo.longest_event_s) == (300, 128)
        first, second = evaluation.folds
        assert first.training_end == BLOCK_1 - timedelta(seconds=428)
        assert (first.labelled_events, second.labelled_events) == (2, 1)
        assert evaluation.labelled_events == 4  # the last one in no block

    def test_the_first_block(self, evaluation: Evaluation) -> None:
        fold = evaluation.folds[0]
        ew = fold.event_wise
        counts = (ew.true_positives, ew.false_positives, ew.false_negatives, ew.redundant)
        assert counts == (2, 0, 0, 0)
        assert ew.recall == 1.0 and fold.alarms == 2
        # B's alarm is open at the end of the data, so it runs to the end of the block:
        # 291 s of the 600 - 29 - 9 = 562 s of nominal time. (And 1 ns more: an alarm
        # covers its last firing instant, t_end, whole.)
        assert ew.tnr == pytest.approx(1 - 291 / 562, rel=1e-9)
        assert ew.precision == ew.tnr
        # Persistence of 3 samples at 1 s: each alarm fired 2 s after its onset, which
        # is the annotated start.
        assert (fold.alarm_delay_s.count, fold.alarm_delay_s.median) == (2, 2.0)
        assert fold.firing_delay_s.median == 2.0
        assert fold.adtqc.score == 1.0 and fold.adtqc.after == 2
        assert fold.channel_aware.f_beta == 1.0

    def test_operational_numbers(self, evaluation: Evaluation) -> None:
        fold = evaluation.folds[0]
        # A runs 00:00:00-00:04:29 and B 00:00:00-00:05:09: 309 s of telemetry.
        assert fold.observed_s == 309
        assert fold.alarms_per_day == pytest.approx(2 * 86_400 / 309)
        assert fold.millicores_per_channel == pytest.approx(1000 * fold.cpu_s / 309 / 2)
        assert fold.samples == (4 * 60 + 30) + (5 * 60 + 10)  # A, then B

    def test_coverage_and_what_cannot_fire(self, evaluation: Evaluation) -> None:
        first, second = evaluation.folds
        assert first.cannot_fire == () and second.cannot_fire == ("A",)
        assert all(c.coverage == 1.0 for c in first.per_channel)
        a, b = first.per_channel
        assert a.event_wise is not None and a.event_wise.precision == pytest.approx(1.0)
        assert b.event_wise is not None and b.event_wise.precision < 1.0
        assert (
            second.per_channel[0].labelled_events == 0 and second.per_channel[0].event_wise is None
        )

    def test_each_fold_fits_its_own_limits_on_its_own_training_data(
        self, setup: EvalConfig, evaluation: Evaluation
    ) -> None:
        def high(fold: int, channel: str) -> float:
            path = setup.output / f"fold-{fold}" / "limits.max_gap_60s.yaml"
            limits = yaml.safe_load(path.read_text(encoding="utf-8"))
            return float(limits["channels"][channel]["high"])

        # Block 1's 1.5 on B is test data for fold 1 and training data for fold 2.
        assert high(1, "B") == pytest.approx(0.9 + 0.08, abs=0.01)
        assert high(2, "B") == pytest.approx(1.5 + 0.1 * (1.5 - 0.1), abs=0.01)
        assert high(1, "A") == high(2, "A")

    def test_a_gap_inside_an_event_costs_a_redundant_alarm_only_below_its_length(
        self, evaluation: Evaluation
    ) -> None:
        second = evaluation.folds[1]
        gaps = {g.max_gap_s: g for g in second.gap_sensitivity}
        assert (gaps[60].alarms, gaps[60].redundant, gaps[60].alarming_precision) == (2, 1, 0.5)
        assert (gaps[150].alarms, gaps[150].redundant, gaps[150].alarming_precision) == (1, 0, 1)
        assert gaps[60].recall == gaps[150].recall == 1.0
        # The headline is the spec's max_gap_s: timing comes from the first alarm.
        assert second.event_wise.redundant == 1
        assert second.alarm_delay_s.median == 2.0

    def test_the_official_split(self, setup: EvalConfig, evaluation: Evaluation) -> None:
        official = evaluation.official_split
        assert (official.test_segments, official.anomalous_segments) == (22, 4)
        scores = official.scores
        assert (scores.accuracy, scores.mcc, scores.auc_roc, scores.auc_pr) == (1, 1, 1, 1)
        # Fitted on the segments the dataset marks for training, and on nothing else.
        path = setup.output / "official-split" / "limits.yaml"
        provenance = yaml.safe_load(path.read_text(encoding="utf-8"))["provenance"]
        assert provenance["nominal_samples"] == {"A": 600, "B": 600}


class TestTraceability:
    def test_every_run_leaves_its_limits_and_events(
        self, setup: EvalConfig, evaluation: Evaluation
    ) -> None:
        fold = setup.output / "fold-1"
        written = sorted(p.name for p in fold.iterdir())
        assert written == [
            "events.max_gap_150s.jsonl",
            "events.max_gap_60s.jsonl",
            "limits.max_gap_150s.yaml",
            "limits.max_gap_60s.yaml",
        ]
        limits = yaml.safe_load((fold / "limits.max_gap_60s.yaml").read_text(encoding="utf-8"))
        assert limits["provenance"]["training"] == {
            "start": None,
            "end": (BLOCK_1 - timedelta(seconds=428)).isoformat().replace("+00:00", "Z"),
        }
        assert evaluation.folds[0].limits_file == "fold-1/limits.max_gap_60s.yaml"
        assert (setup.output / "official-split" / "limits.yaml").exists()

    def test_the_events_kept_are_their_final_state(self, setup: EvalConfig) -> None:
        """An alarm still open at the end is kept as the end of the stream publishes it:
        with the peak it reached after it opened (ADR 0006)."""
        evaluate(setup)
        fold = setup.output / "fold-1"
        events = (fold / "events.max_gap_60s.jsonl").read_text(encoding="utf-8").splitlines()
        b = json.loads(events[-1])
        limits = yaml.safe_load((fold / "limits.max_gap_60s.yaml").read_text(encoding="utf-8"))
        low, high = limits["channels"]["B"]["low"], limits["channels"]["B"]["high"]
        assert (b["channels"][0]["channel"], b["t_end"]) == ("B", None)
        assert b["detections"][0]["score"] == pytest.approx((max(CLIMB) - high) / (high - low))

    def test_the_same_inputs_give_the_same_numbers(self, setup: EvalConfig) -> None:
        def scored(evaluation: Evaluation) -> list[Any]:
            return [
                (f.event_wise, f.adtqc, f.alarm_delay_s, f.per_channel) for f in evaluation.folds
            ]

        assert scored(evaluate(setup)) == scored(evaluate(setup))


class TestRefusals:
    def test_limits_whose_provenance_reaches_the_test_block_are_refused(
        self, setup: EvalConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """What the harness evaluates is what the limits file says it was fitted on."""
        derive = harness.derive

        def leaky(**kwargs: Any) -> Any:
            return derive(**(kwargs | {"training": {"start": None, "end": None}}))

        monkeypatch.setattr(harness, "derive", leaky)
        with pytest.raises(ProtocolError, match="fold 1: the limits were fitted on"):
            evaluate(setup)

    def test_another_data_version_stops_the_run_and_logs_both_hashes(
        self, setup: EvalConfig, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        lock = json.loads(setup.lock.read_text(encoding="utf-8"))
        expected = lock["opssat"]["files"][0]["checksum"]
        lock["opssat"]["files"][0]["checksum"] = "md5:" + "0" * 32
        setup.lock.write_text(json.dumps(lock), encoding="utf-8")
        config = tmp_path / "eval.yaml"
        config.write_text(yaml.safe_dump(setup.model_dump(mode="json")), encoding="utf-8")
        with caplog.at_level(logging.ERROR):
            assert main(["--config", str(config)]) == 2
        assert f"expected md5 {'0' * 32}, found md5 {expected[4:]}" in caplog.text
        assert not (setup.output / "report.md").exists()

    def test_a_boundary_through_a_labelled_event_is_refused(self, setup: EvalConfig) -> None:
        blocks = [{"start": BLOCK_2, "end": BLOCK_2 + timedelta(minutes=3)}]
        cut = EvalConfig.model_validate(
            setup.model_dump() | {"walk_forward": {"min_events": 1, "blocks": blocks}}
        )
        with pytest.raises(ProtocolError, match="cut labelled events: B:"):
            evaluate(cut)

    def test_a_block_with_too_few_events_is_refused(self, setup: EvalConfig) -> None:
        strict = EvalConfig.model_validate(
            setup.model_dump()
            | {"walk_forward": setup.walk_forward.model_dump() | {"min_events": 2}}
        )
        with pytest.raises(ProtocolError, match="fewer than the 2 a fold needs"):
            evaluate(strict)

    def test_the_headline_max_gap_must_be_one_of_the_rows(self, setup: EvalConfig) -> None:
        metrics = setup.metrics.model_dump() | {"gap_sensitivity_s": [150]}
        with pytest.raises(ProtocolError, match="must include the spec's max_gap_s"):
            evaluate(EvalConfig.model_validate(setup.model_dump() | {"metrics": metrics}))

    def test_a_detector_looking_back_further_than_the_embargo_is_refused(
        self, setup: EvalConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(harness, "lookback_s", lambda policy, rate_limited: 0.0)
        with pytest.raises(ProtocolError, match="looks back further than the embargo"):
            evaluate(setup)


def test_a_distribution_takes_the_nearest_rank() -> None:
    spread = Distribution.of([float(v) for v in range(10, 0, -1)])
    assert (spread.count, spread.median, spread.p90, spread.max) == (10, 5.5, 9.0, 10.0)
    assert Distribution.of([]) == Distribution(0, None, None, None)


def test_the_command_writes_both_reports_with_what_every_reader_must_see(
    setup: EvalConfig, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "eval.yaml"
    config.write_text(yaml.safe_dump(setup.model_dump(mode="json")), encoding="utf-8")
    assert main(["--config", str(config)]) == 0
    printed = capsys.readouterr().out.split()
    assert printed == [(setup.output / n).as_posix() for n in ("report.md", "report.json")]
    report = (setup.output / "report.md").read_text(encoding="utf-8")
    for text in DISCLAIMERS.values():
        assert text in report
    assert "Comparison with the literature" in report
    assert "cannot fire over this block:** 1 of 2: A." in report
    document = json.loads((setup.output / "report.json").read_text(encoding="utf-8"))
    assert document["run"]["config_sha256"] == hashlib.sha256(config.read_bytes()).hexdigest()
    assert document["summary"]["alarming_precision"] == {
        "folds": 2,
        "mean": 0.75,
        "min": 0.5,
        "max": 1.0,
    }
    assert document["read_this_first"] == DISCLAIMERS
