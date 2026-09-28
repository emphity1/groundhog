# SPDX-License-Identifier: Apache-2.0
"""The shipped benchmark on the real OPSSAT-AD files: the numbers the README publishes.

A change that moves any of these numbers changes a published result. That may be the
right change, but it has to be a decision, made in the open: this test makes sure it
cannot happen by accident. Skipped when the dataset has not been fetched, as in CI.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from groundhog.eval.config import load_config
from groundhog.eval.ground_truth import labelled_events
from groundhog.eval.harness import Evaluation, evaluate
from groundhog.ingest.opssat import labelled_samples, labelled_segments

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "raw" / "opssat" / "segments.csv"

pytestmark = pytest.mark.skipif(not DATA.exists(), reason="OPSSAT-AD not fetched")


def test_the_labelled_events_of_adr_0001() -> None:
    events = labelled_events(labelled_segments(labelled_samples(DATA)), run_gap_periods=2)
    assert len(events) == 320
    assert sum(len(e.segments) for e in events) == 434
    longest = max(events, key=lambda e: e.duration_s)
    assert (longest.channel, longest.duration_s, len(longest.segments)) == ("CADC0874", 6545, 14)


@pytest.fixture(scope="module")
def evaluation(tmp_path_factory: pytest.TempPathFactory) -> Evaluation:
    shipped = load_config(REPO / "configs" / "eval" / "opssat_r0.yaml")
    config = shipped.model_copy(
        update={
            "output": tmp_path_factory.mktemp("report"),
            "metrics": shipped.metrics.model_copy(update={"cpu_min_s": 0.0}),
        }
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.chdir(REPO)  # the shipped configuration's paths are relative to the root
        return evaluate(config)


def test_the_protocol(evaluation: Evaluation) -> None:
    assert (evaluation.embargo.lookback_s, evaluation.embargo.longest_event_s) == (300, 6545)
    assert [f.labelled_events for f in evaluation.folds] == [14, 44, 68, 75, 30]


def test_what_r0_raises(evaluation: Evaluation) -> None:
    assert [f.alarms for f in evaluation.folds] == [2, 1, 0, 0, 0]
    assert [f.event_wise.true_positives for f in evaluation.folds] == [2, 2, 0, 0, 0]
    assert all(f.event_wise.false_positives == 0 for f in evaluation.folds)
    assert [f.alarm_delay_s.median for f in evaluation.folds[:2]] == [615, 39]
    assert [g.redundant for f in evaluation.folds for g in f.gap_sensitivity] == [0] * 10


def test_what_r0_cannot_see(evaluation: Evaluation) -> None:
    assert [f.cannot_fire for f in evaluation.folds] == [
        ("CADC0873",),
        ("CADC0874", "CADC0884", "CADC0888", "CADC0892", "CADC0894"),
        ("CADC0884", "CADC0888", "CADC0892", "CADC0894"),
        ("CADC0873", "CADC0884", "CADC0888", "CADC0892", "CADC0894"),
        ("CADC0884", "CADC0888", "CADC0892"),
    ]


def test_the_official_split(evaluation: Evaluation) -> None:
    official = evaluation.official_split
    assert (official.test_segments, official.anomalous_segments) == (529, 113)
    scores = official.scores
    assert (scores.true_positives, scores.false_positives) == (0, 0)
