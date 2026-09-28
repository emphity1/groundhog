# SPDX-License-Identifier: Apache-2.0
"""The evaluation harness: one command from the archive to the report (ARCHITECTURE §4.15).

Before anything is scored, the data is checked against the version in the lock, and
the embargo is computed from the detector configurations and the labels (ADR 0001).
Then, in every fold of the walk-forward:

1. R0's limits are derived from the fold's training data only, written next to the
   report, read back, and refused if their provenance reaches into the embargo or
   the test block;
2. the test block is replayed through the replay engine: the exact stream a live
   run would see, on a clock that never waits;
3. the detector and the arbiter run over that stream as ``groundhog.detect`` runs
   them, and the CPU they use is measured;
4. the events are scored against the labelled events of the block.

Last, the same detector on the dataset's official split, scored as segment
classification: the comparison with the literature, and nothing more (ADR 0001).
"""

from __future__ import annotations

import functools
import logging
import math
import statistics
import time
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from groundhog.arbiter import Arbiter, ArbiterSettings
from groundhog.detect.config import load_config as load_detect_config
from groundhog.detect.pipeline import Coverage, DetectPipeline
from groundhog.detectors.base import Detector
from groundhog.detectors.r0_derive import DerivationSpec, derive, limits_yaml, load_spec
from groundhog.detectors.r0_limits import (
    AlarmPolicy,
    LimitChecker,
    LimitsCheck,
    LimitsConfig,
    NoCheck,
    StatesCheck,
    load_limits,
    lookback_s,
)
from groundhog.eval.config import EvalConfig
from groundhog.eval.esa_adb import (
    Annotation,
    ChannelAware,
    EventWise,
    Timing,
    adtqc,
    channel_aware,
    detections,
    event_series,
    event_wise,
    logical_sum,
)
from groundhog.eval.ground_truth import LabelledEvent, Run, continuous_runs, labelled_events
from groundhog.eval.intervals import Span, to_ns
from groundhog.eval.literature import Classification, classification
from groundhog.eval.protocol import (
    Embargo,
    Fold,
    ProtocolError,
    check_boundaries,
    check_data_version,
    check_limits,
    check_usable,
    embargo,
    folds,
)
from groundhog.ingest.opssat import (
    LabelledSample,
    LabelledSegment,
    OpssatSource,
    labelled_samples,
    labelled_segments,
    nominal_values,
)
from groundhog.replay.config import Window
from groundhog.replay.config import load_config as load_replay_config
from groundhog.replay.offline import replay_offline
from groundhog.schema import ChannelKind, DetectorId, Event, Sample, Score

__all__ = [
    "ChannelResult",
    "Distribution",
    "Evaluation",
    "FoldResult",
    "GapResult",
    "OfficialSplitResult",
    "alarm_delays",
    "evaluate",
    "observed_seconds",
]

log = logging.getLogger(__name__)

_DAY_S = 86_400.0
_UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class Distribution:
    """A set of durations in seconds: the 90th percentile is the nearest rank."""

    count: int
    median: float | None
    p90: float | None
    max: float | None

    @classmethod
    def of(cls, values: Sequence[float]) -> Distribution:
        if not values:
            return cls(0, None, None, None)
        ordered = sorted(values)
        p90 = ordered[math.ceil(0.9 * len(ordered)) - 1]
        return cls(len(ordered), statistics.median(ordered), p90, ordered[-1])


@dataclass(frozen=True)
class ChannelResult:
    channel: str
    samples: int
    coverage: float
    """Samples R0 scored over samples it received (ADR 0005)."""
    check: str
    cannot_fire: bool
    """The values in the test block never leave the fitted limits: R0 cannot fire here."""
    labelled_events: int
    alarms: int
    event_wise: EventWise | None
    """Groundhog's own: this channel's alarms against this channel's labelled events."""


@dataclass(frozen=True)
class GapResult:
    """One of the runs that show what ``max_gap_s`` does to redundant alarms (ADR 0008)."""

    max_gap_s: float
    alarms: int
    true_positives: int
    redundant: int
    alarming_precision: float
    recall: float


@dataclass(frozen=True)
class FoldResult:
    number: int
    test_start: datetime
    test_end: datetime
    training_end: datetime
    channels: tuple[str, ...]
    samples: int
    labelled_events: int
    observed_s: float
    """Telemetry time in the block: the union over channels of the continuous runs."""
    alarms: int
    alarms_per_day: float
    event_wise: EventWise
    channel_aware: ChannelAware
    adtqc: Timing
    alarm_delay_s: Distribution
    """Groundhog's own: ``fired_at`` minus the annotated start, per detected event."""
    firing_delay_s: Distribution
    """``fired_at`` minus onset, per alarm: what persistence costs in time."""
    cpu_s: float
    millicores_per_channel: float
    per_channel: tuple[ChannelResult, ...]
    gap_sensitivity: tuple[GapResult, ...]
    limits_file: str

    @property
    def cannot_fire(self) -> tuple[str, ...]:
        return tuple(c.channel for c in self.per_channel if c.cannot_fire)


@dataclass(frozen=True)
class OfficialSplitResult:
    """R0 on the dataset's own split, per segment: comparable with the literature only."""

    test_segments: int
    anomalous_segments: int
    scores: Classification
    limits_file: str


@dataclass(frozen=True)
class Evaluation:
    data_file: str
    data_md5: str
    embargo: Embargo
    labelled_events: int
    headline_max_gap_s: float
    beta: float
    adtqc_exponent: float
    folds: tuple[FoldResult, ...]
    official_split: OfficialSplitResult


@dataclass(frozen=True)
class _Detection:
    events: list[Event]
    """The last record of every event: its final state (ADR 0006)."""
    coverage: dict[str, Coverage]
    cpu_s: float


class _Recording:
    """A detector, unchanged, with the value of every score it gives kept."""

    def __init__(self, detector: Detector) -> None:
        self._detector = detector
        self.values: dict[tuple[str, datetime], float] = {}

    @property
    def id(self) -> DetectorId:
        return self._detector.id

    @property
    def model_version(self) -> str | None:
        return self._detector.model_version

    @property
    def config_hash(self) -> str:
        return self._detector.config_hash

    @property
    def lookback_s(self) -> float:
        return self._detector.lookback_s

    def update(self, sample: Sample) -> Score | None:
        score = self._detector.update(sample)
        if score is not None:
            self.values[(sample.channel, sample.mission_ts)] = score.value
        return score

    def reset(self) -> None:
        self._detector.reset()


class _ArrivalClock:
    """The arbiter's wall clock offline: an event is created when its sample arrives,
    and the offline replay makes that moment the sample's mission time."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def arrived(self, sample: Sample) -> None:
        self._now = sample.wall_ts

    def __call__(self) -> datetime:
        return self._now


@dataclass(frozen=True)
class _Context:
    """What every fold shares."""

    config: EvalConfig
    spec: DerivationSpec
    spec_sha256: str
    source: OpssatSource
    seed: int
    arbiter: ArbiterSettings
    kinds: Mapping[str, ChannelKind]
    data_md5: str
    samples: Sequence[LabelledSample]
    runs: Sequence[Run]
    embargo: Embargo
    output: Path


def evaluate(config: EvalConfig) -> Evaluation:
    """Run the whole benchmark the configuration describes, writing the fitted limits
    and the events of every run under ``config.output``."""
    replay = load_replay_config(config.replay)
    arbiter = load_detect_config(config.detect).arbiter
    spec, spec_sha256 = load_spec(config.limits)
    source = OpssatSource(replay.source)
    data_md5 = str(source.describe()["md5"])
    check_data_version(config.lock, replay.source.type, replay.source.path.name, data_md5)

    samples = labelled_samples(replay.source.path)
    segments = labelled_segments(samples)
    rule = config.ground_truth.run_gap_periods
    events = labelled_events(segments, rule)

    gaps = config.metrics.gap_sensitivity_s
    if spec.defaults.max_gap_s not in gaps:
        raise ProtocolError(
            f"gap_sensitivity_s {gaps} must include the spec's max_gap_s, "
            f"{spec.defaults.max_gap_s:g} s: the headline run is one of the rows"
        )
    policies = [spec.defaults.model_copy(update={"max_gap_s": gap}) for gap in gaps]
    cut = embargo((lookback_s(p, rate_limited=False) for p in policies), events)
    blocks = [(block.start, block.end) for block in config.walk_forward.blocks]
    fold_list = folds(blocks, cut)
    check_boundaries(fold_list, events)
    log.info(
        "data md5 %s as locked; %d labelled events; embargo %.0f s (lookback %.0f s + "
        "longest event %.0f s)",
        data_md5,
        len(events),
        cut.seconds,
        cut.lookback_s,
        cut.longest_event_s,
    )

    context = _Context(
        config=config,
        spec=spec,
        spec_sha256=spec_sha256,
        source=source,
        seed=replay.replay.seed,
        arbiter=arbiter,
        kinds={name: channel.kind for name, channel in replay.source.channels.items()},
        data_md5=data_md5,
        samples=samples,
        runs=continuous_runs(segments, rule),
        embargo=cut,
        output=config.output,
    )
    results = []
    for fold in fold_list:
        block_events = [e for e in events if fold.test_start <= e.start < fold.test_end]
        results.append(_evaluate_fold(context, fold, block_events, policies))
    return Evaluation(
        data_file=replay.source.path.as_posix(),
        data_md5=data_md5,
        embargo=cut,
        labelled_events=len(events),
        headline_max_gap_s=spec.defaults.max_gap_s,
        beta=config.metrics.beta,
        adtqc_exponent=config.metrics.adtqc_exponent,
        folds=tuple(results),
        official_split=_official_split(context, segments),
    )


def _evaluate_fold(
    context: _Context,
    fold: Fold,
    block_events: Sequence[LabelledEvent],
    policies: Sequence[AlarmPolicy],
) -> FoldResult:
    metrics = context.config.metrics
    tested = sorted(
        {s.channel for s in context.samples if fold.test_start <= s.mission_ts < fold.test_end}
    )
    nominal = nominal_values(context.samples, None, fold.training_end)
    check_usable(fold, block_events, tested, nominal, context.config.walk_forward.min_events)

    stream: list[Sample] = []
    replay_offline(context.source, context.seed, fold.test, stream.append)
    channels = sorted({s.channel for s in stream})
    full_range = (to_ns(fold.test_start), to_ns(fold.test_end))
    annotations = [Annotation(e.id, e.channel, e.start, e.end) for e in block_events]

    directory = context.output / f"fold-{fold.number}"
    headline_gap = context.spec.defaults.max_gap_s
    runs: dict[float, tuple[LimitsConfig, _Detection, str]] = {}
    for policy in policies:
        name = f"limits.max_gap_{policy.max_gap_s:g}s.yaml"
        limits = _fitted_limits(
            context,
            nominal,
            policy,
            training=fold.training.model_dump(mode="json"),
            path=directory / name,
            note=f"fold {fold.number}",
        )
        check_limits(limits, context.data_md5, fold)  # as read back from the file
        if LimitChecker(limits).lookback_s > context.embargo.lookback_s:
            raise ProtocolError(
                f"fold {fold.number}: the detector looks back further than the embargo allows"
            )
        headline = policy.max_gap_s == headline_gap
        detection = _detect(
            functools.partial(LimitChecker, limits),
            context.arbiter,
            stream,
            cpu_min_s=metrics.cpu_min_s if headline else 0.0,
        )
        _write_events(directory / f"events.max_gap_{policy.max_gap_s:g}s.jsonl", detection.events)
        runs[policy.max_gap_s] = (limits, detection, name)

    limits, detection, limits_name = runs[headline_gap]
    by_channel = _by_channel(detection.events)
    spans = {
        c: detections(_series(by_channel.get(c, []), full_range), full_range) for c in channels
    }
    overall = event_wise(
        annotations, _combined(by_channel, channels, full_range), full_range, metrics.beta
    )
    observed = observed_seconds(context.runs, fold.test_start, fold.test_end)
    if observed <= 0:
        raise ProtocolError(f"fold {fold.number}: no telemetry time in the test block")
    values: dict[str, list[float]] = defaultdict(list)
    for sample in stream:
        values[sample.channel].append(sample.value)

    per_channel = []
    for channel in channels:
        own = [a for a in annotations if a.channel == channel]
        per_channel.append(
            ChannelResult(
                channel=channel,
                samples=len(values[channel]),
                coverage=detection.coverage[channel].ratio,
                check=_describe(limits.channels[channel]),
                cannot_fire=_cannot_fire(limits.channels[channel], values[channel]),
                labelled_events=len(own),
                alarms=len(by_channel.get(channel, [])),
                event_wise=(
                    event_wise(own, spans[channel], full_range, metrics.beta) if own else None
                ),
            )
        )

    gap_results = []
    for gap, (_, run, _) in sorted(runs.items()):
        runs_by_channel = _by_channel(run.events)
        scored = event_wise(
            annotations, _combined(runs_by_channel, channels, full_range), full_range, metrics.beta
        )
        gap_results.append(
            GapResult(
                max_gap_s=gap,
                alarms=len(run.events),
                true_positives=scored.true_positives,
                redundant=scored.redundant,
                alarming_precision=scored.alarming_precision,
                recall=scored.recall,
            )
        )

    result = FoldResult(
        number=fold.number,
        test_start=fold.test_start,
        test_end=fold.test_end,
        training_end=fold.training_end,
        channels=tuple(channels),
        samples=len(stream),
        labelled_events=len(block_events),
        observed_s=observed,
        alarms=len(detection.events),
        alarms_per_day=len(detection.events) / (observed / _DAY_S),
        event_wise=overall,
        channel_aware=channel_aware(annotations, spans, full_range, metrics.beta),
        adtqc=adtqc(annotations, spans, full_range, metrics.adtqc_exponent),
        alarm_delay_s=Distribution.of(alarm_delays(annotations, by_channel)),
        firing_delay_s=Distribution.of([e.detections[0].delay_s for e in detection.events]),
        cpu_s=detection.cpu_s,
        millicores_per_channel=1000 * detection.cpu_s / observed / len(channels),
        per_channel=tuple(per_channel),
        gap_sensitivity=tuple(gap_results),
        limits_file=f"{directory.name}/{limits_name}",
    )
    log.info(
        "fold %d: %d samples, %d labelled events, %d alarms, recall %.3f, precision %.3f",
        fold.number,
        result.samples,
        result.labelled_events,
        result.alarms,
        overall.recall,
        overall.precision,
    )
    return result


def _official_split(context: _Context, segments: Sequence[LabelledSegment]) -> OfficialSplitResult:
    """Limits fitted on the official training segments; a test segment is flagged when
    an alarm overlaps it, and ranked by the highest score R0 gave inside it."""
    nominal = nominal_values(context.samples, None, None, official_train=True)
    limits = _fitted_limits(
        context,
        nominal,
        context.spec.defaults,
        training={"split": "official", "segments": "train = 1, labelled nominal"},
        path=context.output / "official-split" / "limits.yaml",
        note="the official split",
    )
    if limits.provenance.get("data_md5") != context.data_md5:
        raise ProtocolError("the official split's limits were fitted on another data version")
    recording = _Recording(LimitChecker(limits))
    records: list[Event] = []
    start = min(s.mission_ts for s in context.samples)
    clock = _ArrivalClock(start)
    pipeline = DetectPipeline(
        recording, Arbiter(context.arbiter, {recording.id: recording.model_version}, clock)
    )

    def feed(sample: Sample) -> None:
        clock.arrived(sample)
        records.extend(pipeline.feed(sample))

    replay_offline(context.source, context.seed, Window(start=start, end=None), feed)
    records.extend(pipeline.finish())
    by_channel = _by_channel(_final(records))

    members: dict[int, list[datetime]] = defaultdict(list)
    for s in context.samples:
        members[s.segment].append(s.mission_ts)
    labels, flagged, scores = [], [], []
    test = [seg for seg in segments if not seg.train]
    for seg in test:
        try:
            peak = max(recording.values[(seg.channel, t)] for t in members[seg.id])
        except KeyError:
            raise ProtocolError(
                f"R0 has no opinion on segment {seg.id} of {seg.channel}: it cannot be classified"
            ) from None
        labels.append(seg.anomaly)
        flagged.append(
            any(
                e.t_start <= seg.end and (e.t_end is None or e.t_end >= seg.start)
                for e in by_channel.get(seg.channel, [])
            )
        )
        scores.append(peak)
    return OfficialSplitResult(
        test_segments=len(test),
        anomalous_segments=sum(labels),
        scores=classification(labels, flagged, scores),
        limits_file="official-split/limits.yaml",
    )


def _fitted_limits(
    context: _Context,
    nominal: Mapping[str, Sequence[float]],
    policy: AlarmPolicy,
    *,
    training: Mapping[str, Any],
    path: Path,
    note: str,
) -> LimitsConfig:
    """Derive, write, and read back: what is evaluated is what the file says."""
    limits = derive(
        mission=context.source.describe()["mission"],
        kinds=context.kinds,
        nominal=nominal,
        spec=context.spec,
        spec_path=context.config.limits,
        spec_sha256=context.spec_sha256,
        data_md5=context.data_md5,
        training=training,
        generator="groundhog.eval",
        defaults=policy,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    header = f"# Derived by groundhog.eval for {note}. Evaluation output, not configuration.\n"
    path.write_text(header + limits_yaml(limits), encoding="utf-8", newline="\n")
    return load_limits(path)


def _detect(
    make_detector: Callable[[], Detector],
    arbiter: ArbiterSettings,
    stream: Sequence[Sample],
    cpu_min_s: float,
) -> _Detection:
    """Detector and arbiter over the stream, as ``groundhog.detect`` runs them.

    Passes are repeated until their CPU time adds up to ``cpu_min_s`` and the mean
    per pass is reported, so the process clock's resolution (15.6 ms on Windows)
    does not show in the number. Every pass must give the same events.
    """
    timings: list[float] = []
    outcome: list[Event] | None = None
    coverage: dict[str, Coverage] = {}
    while not timings or sum(timings) < cpu_min_s:
        detector = make_detector()
        clock = _ArrivalClock(stream[0].wall_ts if stream else _UNIX_EPOCH)
        pipeline = DetectPipeline(
            detector, Arbiter(arbiter, {detector.id: detector.model_version}, clock)
        )
        records: list[Event] = []
        started = time.process_time()
        for sample in stream:
            clock.arrived(sample)
            records += pipeline.feed(sample)
        records += pipeline.finish()
        timings.append(time.process_time() - started)
        final = _final(records)
        if outcome is not None and final != outcome:
            raise RuntimeError("two passes over the same stream gave different events")
        outcome, coverage = final, pipeline.coverage_by_channel
    assert outcome is not None
    return _Detection(events=outcome, coverage=coverage, cpu_s=statistics.fmean(timings))


def _final(records: Sequence[Event]) -> list[Event]:
    """The last record published for each id is the event (ADR 0006)."""
    last: dict[str, Event] = {}
    for record in records:
        last[record.id] = record
    return sorted(last.values(), key=lambda e: (e.t_start, e.id))


def _by_channel(events: Sequence[Event]) -> dict[str, list[Event]]:
    grouped: dict[str, list[Event]] = defaultdict(list)
    for event in events:
        grouped[event.channels[0].channel].append(event)
    return grouped


def _series(events: Sequence[Event], full_range: tuple[int, int]) -> list[tuple[int, bool]]:
    return event_series(((e.t_start, e.t_end) for e in events), full_range)


def _combined(
    by_channel: Mapping[str, Sequence[Event]], channels: Sequence[str], full_range: tuple[int, int]
) -> Span:
    """Every channel's alarms as one detection series, as ESA-ADB scores events."""
    series = {c: _series(by_channel.get(c, []), full_range) for c in channels}
    return detections(logical_sum(series), full_range)


def alarm_delays(
    annotations: Sequence[Annotation], by_channel: Mapping[str, Sequence[Event]]
) -> list[float]:
    """Groundhog's own timing measure: for every labelled event an alarm overlaps,
    ``fired_at`` of the first such alarm on its channel minus the annotated start."""
    delays = []
    for a in annotations:
        overlapping = [
            e
            for e in by_channel.get(a.channel, [])
            if e.t_start <= a.end and (e.t_end is None or e.t_end >= a.start)
        ]
        if overlapping:
            first = min(overlapping, key=lambda e: e.t_start)
            delays.append((first.detections[0].fired_at - a.start).total_seconds())
    return delays


def observed_seconds(runs: Sequence[Run], start: datetime, end: datetime) -> float:
    """Telemetry time in ``[start, end)``: the union over channels of the continuous
    runs, so that alarms per day are counted on time the spacecraft was heard."""
    spans = sorted(
        (max(r.start, start), min(r.end, end)) for r in runs if r.start < end and r.end >= start
    )
    total = 0.0
    current: tuple[datetime, datetime] | None = None
    for lower, upper in spans:
        if current is not None and lower <= current[1]:
            current = (current[0], max(current[1], upper))
            continue
        if current is not None:
            total += (current[1] - current[0]).total_seconds()
        current = (lower, upper)
    if current is not None:
        total += (current[1] - current[0]).total_seconds()
    return total


def _cannot_fire(check: LimitsCheck | StatesCheck | NoCheck, values: Sequence[float]) -> bool:
    """Whether no persistence or hysteresis setting could make this check fire on these
    values: they never leave the limits, and no rate limit is set."""
    if isinstance(check, LimitsCheck):
        return check.max_rate is None and check.low <= min(values) and max(values) <= check.high
    if isinstance(check, StatesCheck):
        return set(values) <= set(check.allowed)
    return True


def _describe(check: LimitsCheck | StatesCheck | NoCheck) -> str:
    if isinstance(check, LimitsCheck):
        rate = "" if check.max_rate is None else f", rate <= {check.max_rate:.6g}/s"
        return f"limits [{check.low:.6g}, {check.high:.6g}]{rate}"
    if isinstance(check, StatesCheck):
        return "states " + ", ".join(f"{v:g}" for v in check.allowed)
    return f"none: {check.reason}"


def _write_events(path: Path, events: Sequence[Event]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(event.model_dump_json() + "\n" for event in events), encoding="utf-8", newline="\n"
    )
