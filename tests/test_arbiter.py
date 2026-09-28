# SPDX-License-Identifier: Apache-2.0
"""The minimal arbiter: firing scores in, events out, with a known answer every time."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from groundhog.arbiter import Arbiter, ArbiterError, ArbiterSettings, PassThrough, SeverityBands
from groundhog.schema import DetectorId, Event, EventStatus, Quality, Score, Severity

T0 = datetime(2022, 1, 4, 20, 0, tzinfo=UTC)
WALL = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
R0 = DetectorId.R0_LIMITS
SETTINGS = ArbiterSettings(severity={R0: SeverityBands(medium=0.1, high=0.5)})


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def score(
    t: float,
    value: float = 0.0,
    *,
    onset: float | None = None,
    channel: str = "A",
    quality: Quality = Quality.OK,
    detector: DetectorId = R0,
) -> Score:
    """A score at ``t`` seconds; firing exactly when an ``onset`` is given."""
    return Score(
        detector=detector,
        mission="m",
        channel=channel,
        mission_ts=at(t),
        value=value,
        firing=onset is not None,
        onset=None if onset is None else at(onset),
        quality=quality,
    )


def arbiter(**kw: object) -> Arbiter:
    return Arbiter(SETTINGS, {R0: None}, clock=lambda: WALL, **kw)  # type: ignore[arg-type]


def feed(arb: Arbiter, scores: list[Score]) -> list[list[Event]]:
    return [arb.update(s) for s in scores]


def flat(published: list[list[Event]]) -> list[Event]:
    return [event for batch in published for event in batch]


class TestLifecycle:
    def test_one_event_from_consecutive_firing_scores_published_on_open_and_on_close(
        self,
    ) -> None:
        published = feed(
            arbiter(),
            [
                score(0, 0.2),  # violating, not yet persistent
                score(1, 0.3, onset=0),  # fires, dated from the start of the violation
                score(2, 0.2, onset=0),
                score(3),  # back to nominal
            ],
        )
        assert [len(batch) for batch in published] == [0, 1, 0, 1]
        opened, closed = flat(published)
        assert opened.id == closed.id == "m:r0_limits:A:20220104T200000.000000Z"
        assert (opened.t_start, opened.t_end, closed.t_end) == (at(0), None, at(2))
        detection = closed.detections[0]
        assert (detection.detector, detection.model_version) == (R0, None)
        assert (detection.fired_at, detection.delay_s, detection.score) == (at(1), 1.0, 0.3)
        assert [(c.channel, c.weight) for c in closed.channels] == [("A", 1.0)]
        assert (closed.severity, closed.status, closed.verdict) == (
            Severity.MEDIUM,
            EventStatus.NEW,
            None,
        )
        assert closed.created_at == WALL

    def test_severity_follows_the_peak_and_every_rise_is_published(self) -> None:
        published = feed(
            arbiter(),
            [
                score(0, 0.05, onset=0),
                score(1, 0.2, onset=0),
                score(2, 0.1, onset=0),  # below the peak: nothing changes
                score(3, 0.7, onset=0),
                score(4),
            ],
        )
        assert [len(batch) for batch in published] == [1, 1, 0, 1, 1]
        events = flat(published)
        assert [e.severity for e in events] == [
            Severity.LOW,
            Severity.MEDIUM,
            Severity.HIGH,
            Severity.HIGH,
        ]
        assert len({e.id for e in events}) == 1
        assert [e.t_end for e in events] == [None, None, None, at(3)]

    def test_an_event_still_open_at_the_end_is_published_again_never_dropped(self) -> None:
        arb = arbiter()
        feed(arb, [score(0, 0.2, onset=0), score(1, 0.6, onset=0)])
        (final,) = arb.finish()
        assert final.t_end is None
        assert (final.severity, final.detections[0].score) == (Severity.HIGH, 0.6)
        assert arb.finish() == []

    def test_nominal_scores_publish_nothing(self) -> None:
        arb = arbiter()
        assert flat(feed(arb, [score(t) for t in range(5)])) == []
        assert arb.finish() == []

    def test_a_new_condition_closes_the_old_event_and_opens_another(self) -> None:
        published = feed(
            arbiter(), [score(0, 1, onset=0), score(1, 1, onset=0), score(2, 1, onset=2)]
        )
        closed, opened = published[2]
        assert (closed.t_start, closed.t_end) == (at(0), at(1))
        assert (opened.t_start, opened.t_end) == (at(2), None)
        assert closed.id != opened.id


class TestScope:
    def test_one_event_per_channel(self) -> None:
        arb = arbiter()
        events = flat(feed(arb, [score(0, 1, onset=0, channel=c) for c in ("A", "B")]))
        assert [e.channels[0].channel for e in events] == ["A", "B"]
        assert len({e.id for e in events}) == 2

    def test_quality_is_the_worst_the_event_has_seen(self) -> None:
        arb = arbiter()
        feed(
            arb,
            [
                score(0, 1, onset=0),
                score(1, 1, onset=0, quality=Quality.GAP_FILLED),
                score(2, 1, onset=0),
            ],
        )
        (closing,) = arb.update(score(3))
        assert closing.quality is Quality.GAP_FILLED


class TestIdempotence:
    def test_repeated_and_older_scores_change_nothing(self) -> None:
        clean = [score(0, 0.2), score(1, 0.3, onset=0), score(2, 0.2, onset=0), score(3)]
        repeated = [
            clean[0],
            clean[1],
            clean[1],
            clean[0],
            clean[2],
            clean[1],
            clean[2],
            clean[3],
            clean[3],
        ]
        assert flat(feed(arbiter(), repeated)) == flat(feed(arbiter(), clean))

    def test_the_same_scores_give_the_same_events(self) -> None:
        stream = [score(0, 0.2, onset=0), score(1, 0.9, onset=0), score(2)]
        assert flat(feed(arbiter(), stream)) == flat(feed(arbiter(), stream))


class TestExtensionPoint:
    def test_the_default_hook_publishes_everything(self) -> None:
        stream = [score(0, 1, onset=0), score(1)]
        assert flat(feed(arbiter(hook=PassThrough()), stream)) == flat(feed(arbiter(), stream))

    def test_a_hook_can_hold_events_back(self) -> None:
        class OnlyA:
            def admit(self, event: Event) -> Event | None:
                return event if event.channels[0].channel == "A" else None

        arb = arbiter(hook=OnlyA())
        events = flat(feed(arb, [score(0, 1, onset=0, channel=c) for c in ("A", "B")]))
        assert [e.channels[0].channel for e in events] == ["A"]


class TestConfiguration:
    def test_scores_from_an_unexpected_detector_are_refused(self) -> None:
        with pytest.raises(ArbiterError, match="does not expect"):
            arbiter().update(score(0, detector=DetectorId.R1_STATS))

    def test_every_expected_detector_needs_its_severity_bands(self) -> None:
        with pytest.raises(ArbiterError, match="no severity bands for r1_stats"):
            Arbiter(SETTINGS, {R0: None, DetectorId.R1_STATS: None}, clock=lambda: WALL)

    def test_bands_must_be_in_order(self) -> None:
        with pytest.raises(ValidationError):
            SeverityBands(medium=0.5, high=0.1)
