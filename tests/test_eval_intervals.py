# SPDX-License-Identifier: Apache-2.0
"""Groundhog's intervals against ``portion``, the library the ESA-ADB reference uses."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import portion
import pytest

from groundhog.eval.intervals import Atomic, Span, to_ns

ROUNDS = 3000


def random_atomic(rng: random.Random) -> Atomic:
    lower = rng.randint(0, 12)
    return Atomic(lower, lower + rng.randint(-1, 6), rng.random() < 0.5, rng.random() < 0.5)


def random_span(rng: random.Random) -> Span:
    return Span(*(random_atomic(rng) for _ in range(rng.randint(0, 4))))


def as_portion(span: Span) -> portion.Interval:
    return portion.Interval(
        *(
            portion.Interval.from_atomic(
                portion.CLOSED if a.left_closed else portion.OPEN,
                a.lower,
                a.upper,
                portion.CLOSED if a.right_closed else portion.OPEN,
            )
            for a in span
        )
    )


def atomics_of(interval: portion.Interval) -> list[tuple[int, int, bool, bool]]:
    return [
        (i.lower, i.upper, i.left == portion.CLOSED, i.right == portion.CLOSED)
        for i in interval
        if not i.empty
    ]


def atomics(span: Span) -> list[tuple[int, int, bool, bool]]:
    return [(a.lower, a.upper, a.left_closed, a.right_closed) for a in span]


@pytest.fixture
def rng() -> random.Random:
    return random.Random(20260928)


def test_a_union_merges_exactly_as_portion_does(rng: random.Random) -> None:
    for _ in range(ROUNDS):
        parts = [random_atomic(rng) for _ in range(rng.randint(0, 5))]
        ours = Span(*parts)
        theirs = portion.Interval(*(as_portion(Span(p)) for p in parts))
        assert atomics(ours) == atomics_of(theirs), parts


def test_intersection_and_emptiness(rng: random.Random) -> None:
    for _ in range(ROUNDS):
        a, b = random_span(rng), random_span(rng)
        ours, theirs = a & b, as_portion(a) & as_portion(b)
        assert atomics(ours) == atomics_of(theirs), (a, b)
        assert ours.empty == theirs.empty


def test_difference_from_a_closed_range(rng: random.Random) -> None:
    for _ in range(ROUNDS):
        outer = Span.closed(rng.randint(0, 4), rng.randint(8, 16))
        taken = random_span(rng)
        assert atomics(outer - taken) == atomics_of(as_portion(outer) - as_portion(taken))


def test_bounds_and_length(rng: random.Random) -> None:
    for _ in range(ROUNDS):
        span = random_span(rng)
        theirs = as_portion(span)
        if not span.empty:
            assert (span.lower, span.upper) == (theirs.lower, theirs.upper)
        # The reference accumulates with a plain loop; sum() compensates since Python 3.12.
        expected = 0.0
        for i in theirs:
            expected += (i.upper - i.lower) / 1e9
        assert span.seconds() == expected


def test_touching_ends() -> None:
    assert (Span.closedopen(0, 5) & Span.closed(5, 9)).empty
    assert atomics(Span.closed(0, 5) & Span.closed(5, 9)) == [(5, 5, True, True)]
    assert atomics(Span.union([Span.closedopen(0, 5), Span.closed(5, 9)])) == [(0, 9, True, True)]


def test_nanoseconds_are_exact() -> None:
    moment = datetime(2022, 6, 1, 23, 43, 31, 123456, tzinfo=UTC)
    assert to_ns(moment) == 1654127011123456000
    assert to_ns(moment + timedelta(microseconds=1)) - to_ns(moment) == 1000
