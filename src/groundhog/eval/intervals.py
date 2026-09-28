# SPDX-License-Identifier: Apache-2.0
"""Time intervals with closed or open ends, on integer nanoseconds.

The ESA-ADB metrics are defined on intervals built with the ``portion`` library, and
their results depend on its boundary semantics: ``[a, b)`` and ``[b, c]`` do not
meet, ``[a, b]`` and ``[b, c]`` do, in the single point ``b``. This module
reproduces exactly the operations the metrics use (union with merging,
intersection, difference, emptiness, bounds and length), and the tests hold it to
``portion`` on random inputs.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

__all__ = ["Atomic", "Span", "to_ns"]

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def to_ns(moment: datetime) -> int:
    """Nanoseconds since the Unix epoch of a timezone-aware datetime, exactly."""
    return (moment - _EPOCH) // timedelta(microseconds=1) * 1000


@dataclass(frozen=True)
class Atomic:
    """One interval; each end is closed (included) or open (excluded)."""

    lower: int
    upper: int
    left_closed: bool
    right_closed: bool

    @property
    def empty(self) -> bool:
        return self.lower > self.upper or (
            self.lower == self.upper and not (self.left_closed and self.right_closed)
        )

    def __and__(self, other: Atomic) -> Atomic:
        if self.lower == other.lower:
            lower, left = self.lower, self.left_closed and other.left_closed
        elif self.lower > other.lower:
            lower, left = self.lower, self.left_closed
        else:
            lower, left = other.lower, other.left_closed
        if self.upper == other.upper:
            upper, right = self.upper, self.right_closed and other.right_closed
        elif self.upper < other.upper:
            upper, right = self.upper, self.right_closed
        else:
            upper, right = other.upper, other.right_closed
        return Atomic(lower, upper, left, right)

    def minus(self, other: Atomic) -> list[Atomic]:
        """What is left of this interval once ``other`` is taken away."""
        if (self & other).empty:
            return [self]
        pieces = [
            Atomic(self.lower, other.lower, self.left_closed, not other.left_closed),
            Atomic(other.upper, self.upper, not other.right_closed, self.right_closed),
        ]
        return [piece for piece in pieces if not piece.empty]


def _mergeable(first: Atomic, second: Atomic) -> bool:
    """Whether two intervals, ``first`` starting no later, overlap or touch."""
    return first.upper > second.lower or (
        first.upper == second.lower and (first.right_closed or second.left_closed)
    )


def _merge(first: Atomic, second: Atomic) -> Atomic:
    """``first`` keeps its lower end: on equal lowers, sorting put the closed one first."""
    if first.upper == second.upper:
        upper, right = first.upper, first.right_closed or second.right_closed
    elif first.upper > second.upper:
        upper, right = first.upper, first.right_closed
    else:
        upper, right = second.upper, second.right_closed
    return Atomic(first.lower, upper, first.left_closed, right)


class Span:
    """A union of disjoint intervals, kept sorted and merged: ``portion.Interval``."""

    __slots__ = ("atomics",)

    def __init__(self, *atomics: Atomic) -> None:
        ordered = sorted(
            (a for a in atomics if not a.empty), key=lambda a: (a.lower, not a.left_closed)
        )
        merged: list[Atomic] = []
        for atomic in ordered:
            if merged and _mergeable(merged[-1], atomic):
                merged[-1] = _merge(merged[-1], atomic)
            else:
                merged.append(atomic)
        self.atomics: tuple[Atomic, ...] = tuple(merged)

    @classmethod
    def closed(cls, lower: int, upper: int) -> Span:
        return cls(Atomic(lower, upper, True, True))

    @classmethod
    def closedopen(cls, lower: int, upper: int) -> Span:
        return cls(Atomic(lower, upper, True, False))

    @classmethod
    def union(cls, spans: Iterable[Span]) -> Span:
        return cls(*(atomic for span in spans for atomic in span.atomics))

    @property
    def empty(self) -> bool:
        return not self.atomics

    @property
    def lower(self) -> int:
        return self.atomics[0].lower

    @property
    def upper(self) -> int:
        return self.atomics[-1].upper

    def __iter__(self) -> Iterator[Atomic]:
        return iter(self.atomics)

    def __and__(self, other: Span) -> Span:
        return Span(*(a & b for a in self.atomics for b in other.atomics))

    def __sub__(self, other: Span) -> Span:
        remaining = list(self.atomics)
        for taken in other.atomics:
            remaining = [piece for atomic in remaining for piece in atomic.minus(taken)]
        return Span(*remaining)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Span) and self.atomics == other.atomics

    def __hash__(self) -> int:
        return hash(self.atomics)

    def __repr__(self) -> str:
        return (
            " | ".join(
                f"{'[' if a.left_closed else '('}{a.lower},{a.upper}{']' if a.right_closed else ')'}"
                for a in self.atomics
            )
            or "()"
        )

    def seconds(self) -> float:
        """Total length in seconds, whatever the ends, as the reference sums it."""
        total = 0.0
        for atomic in self.atomics:
            total += (atomic.upper - atomic.lower) / 1e9
        return total
