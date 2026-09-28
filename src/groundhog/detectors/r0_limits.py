# SPDX-License-Identifier: Apache-2.0
"""R0: classic out-of-limit checking, configured in ``configs/limits/<mission>.yaml``.

Per channel, one of three checks, never an implicit fourth:

* ``limits``, for numeric and counter channels: a low and a high limit, and
  optionally a limit on the rate of change per second of mission time.
* ``states``, for categorical channels: the codes the channel may take. A code has
  no magnitude, so numeric limits would be meaningless.
* ``none``, with the reason: a channel left unchecked on purpose. R0 has no opinion
  on it and emits no score, which is exactly what coverage then reports.

A violation becomes an alarm only after it persists (N consecutive samples, or T
seconds of mission time), and an alarm clears only once the value is back inside
by the hysteresis margin: one noisy sample does not start an alarm storm. Both are
configuration, per channel with defaults, because M4 weighs them against noise.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from groundhog.detectors.base import DetectorError, config_sha256
from groundhog.schema import ChannelKind, DetectorId, Quality, Sample, Score

__all__ = [
    "AlarmPolicy",
    "LimitChecker",
    "LimitsCheck",
    "LimitsConfig",
    "NoCheck",
    "PersistenceSamples",
    "PersistenceSeconds",
    "StatesCheck",
    "derive_limits",
    "load_limits",
    "lookback_s",
    "nominal_envelope",
]


class PersistenceSamples(BaseModel):
    """An alarm fires once a violation has lasted this many consecutive samples."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    samples: int = Field(ge=1)


class PersistenceSeconds(BaseModel):
    """An alarm fires once a violation has lasted this long in mission time."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    seconds: float = Field(ge=0, allow_inf_nan=False)


class AlarmPolicy(BaseModel):
    """How a violation becomes an alarm, and how an alarm ends."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    hysteresis: float = Field(
        ge=0,
        lt=0.5,
        description="Fraction of the limit span by which a value must be back inside to clear",
    )
    persistence: PersistenceSamples | PersistenceSeconds
    max_gap_s: float = Field(
        gt=0,
        allow_inf_nan=False,
        description="A longer silence on a channel resets its state: a gap proves nothing",
    )


class LimitsCheck(BaseModel):
    """Numeric and counter channels: low and high limits, optionally a rate limit."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    check: Literal["limits"]
    low: float = Field(allow_inf_nan=False)
    high: float = Field(allow_inf_nan=False)
    max_rate: float | None = Field(
        gt=0, allow_inf_nan=False, description="Units per second of mission time; null for none"
    )
    hysteresis: float | None = Field(default=None, ge=0, lt=0.5)
    persistence: PersistenceSamples | PersistenceSeconds | None = None
    max_gap_s: float | None = Field(default=None, gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _ordered(self) -> LimitsCheck:
        if self.low >= self.high:
            raise ValueError("low must be below high")
        return self


class StatesCheck(BaseModel):
    """Categorical channels: the codes the channel may take."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    check: Literal["states"]
    allowed: list[float] = Field(min_length=1)
    persistence: PersistenceSamples | PersistenceSeconds | None = None
    max_gap_s: float | None = Field(default=None, gt=0, allow_inf_nan=False)


class NoCheck(BaseModel):
    """A channel left unchecked on purpose. The reason is required: nothing is skipped in silence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    check: Literal["none"]
    reason: str = Field(min_length=1)


ChannelCheck = Annotated[LimitsCheck | StatesCheck | NoCheck, Field(discriminator="check")]


class LimitsConfig(BaseModel):
    """``configs/limits/<mission>.yaml``: R0's whole configuration for one mission."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mission: str = Field(min_length=1)
    provenance: dict[str, Any] = Field(description="Where the numbers come from")
    defaults: AlarmPolicy
    channels: dict[str, ChannelCheck] = Field(min_length=1)


def load_limits(path: Path) -> LimitsConfig:
    with path.open(encoding="utf-8") as fh:
        return LimitsConfig.model_validate(yaml.safe_load(fh))


@dataclass
class _ChannelState:
    previous: tuple[datetime, float, Quality] | None = None  # last sample: time, value, quality
    run_start: datetime | None = None  # first sample of the ongoing violation
    run_samples: int = 0
    onset: datetime | None = None  # set while the alarm holds


class LimitChecker:
    """R0 as a :class:`~groundhog.detectors.base.Detector`."""

    def __init__(self, config: LimitsConfig) -> None:
        self._config = config
        self._policies = {
            name: _effective_policy(config.defaults, check)
            for name, check in config.channels.items()
            if not isinstance(check, NoCheck)
        }
        self._allowed = {
            name: frozenset(check.allowed)
            for name, check in config.channels.items()
            if isinstance(check, StatesCheck)
        }
        self._state: dict[tuple[str, str], _ChannelState] = {}
        self._hash = config_sha256(config)
        self._lookback = max(
            (
                lookback_s(
                    self._policies[name],
                    rate_limited=isinstance(check, LimitsCheck) and check.max_rate is not None,
                )
                for name, check in config.channels.items()
                if not isinstance(check, NoCheck)
            ),
            default=0.0,
        )

    @property
    def id(self) -> DetectorId:
        return DetectorId.R0_LIMITS

    @property
    def model_version(self) -> str | None:
        return None

    @property
    def config_hash(self) -> str:
        return self._hash

    @property
    def lookback_s(self) -> float:
        return self._lookback

    def reset(self) -> None:
        self._state.clear()

    def update(self, sample: Sample) -> Score | None:
        check = self._check_for(sample)
        if isinstance(check, NoCheck) or not math.isfinite(sample.value):
            return None  # no opinion

        policy = self._policies[sample.channel]
        key = (sample.mission, sample.channel)
        state = self._state.setdefault(key, _ChannelState())
        if state.previous is not None:
            elapsed = (sample.mission_ts - state.previous[0]).total_seconds()
            if elapsed <= 0:
                raise DetectorError(
                    f"{sample.channel} at {sample.mission_ts.isoformat()} does not follow "
                    f"{state.previous[0].isoformat()}: samples must arrive in mission-time order"
                )
            if elapsed > policy.max_gap_s:
                state = self._state[key] = _ChannelState()

        if isinstance(check, LimitsCheck):
            value, back_inside, quality = _against_limits(check, policy, state, sample)
        else:
            violating = sample.value not in self._allowed[sample.channel]
            value, back_inside, quality = float(violating), not violating, sample.quality

        _advance(state, policy, value > 0, back_inside, sample.mission_ts)
        state.previous = (sample.mission_ts, sample.value, sample.quality)
        return Score(
            detector=self.id,
            mission=sample.mission,
            channel=sample.channel,
            mission_ts=sample.mission_ts,
            value=value,
            firing=state.onset is not None,
            onset=state.onset,
            quality=quality,
        )

    def _check_for(self, sample: Sample) -> LimitsCheck | StatesCheck | NoCheck:
        if sample.mission != self._config.mission:
            raise DetectorError(
                f"these limits are for mission {self._config.mission!r}, not {sample.mission!r}"
            )
        check = self._config.channels.get(sample.channel)
        if check is None:
            raise DetectorError(
                f"channel {sample.channel} has no entry in the limits: declare its limits, "
                "its allowed states, or check: none with a reason"
            )
        if isinstance(check, LimitsCheck) and sample.kind not in (
            ChannelKind.NUMERIC,
            ChannelKind.COUNTER,
        ):
            raise DetectorError(
                f"channel {sample.channel} is {sample.kind}: numeric limits do not apply"
            )
        if isinstance(check, StatesCheck) and sample.kind is not ChannelKind.CATEGORICAL:
            raise DetectorError(
                f"channel {sample.channel} is {sample.kind}: allowed states apply to "
                "categorical channels only"
            )
        return check


def _effective_policy(defaults: AlarmPolicy, check: LimitsCheck | StatesCheck) -> AlarmPolicy:
    return AlarmPolicy(
        hysteresis=(
            check.hysteresis
            if isinstance(check, LimitsCheck) and check.hysteresis is not None
            else defaults.hysteresis
        ),
        persistence=check.persistence if check.persistence is not None else defaults.persistence,
        max_gap_s=check.max_gap_s if check.max_gap_s is not None else defaults.max_gap_s,
    )


def _against_limits(
    check: LimitsCheck, policy: AlarmPolicy, state: _ChannelState, sample: Sample
) -> tuple[float, bool, Quality]:
    """How far outside the limits (0 inside, in units of the limit span), whether the
    value is back inside by the hysteresis margin, and the quality of what was used."""
    span = check.high - check.low
    margin = policy.hysteresis * span
    value = max(check.low - sample.value, sample.value - check.high, 0.0) / span
    back_inside = check.low + margin <= sample.value <= check.high - margin
    quality = sample.quality
    if check.max_rate is not None and state.previous is not None:
        before, previous_value, previous_quality = state.previous
        rate = abs(sample.value - previous_value) / (sample.mission_ts - before).total_seconds()
        value = max(value, max(rate - check.max_rate, 0.0) / check.max_rate)
        back_inside = back_inside and rate <= check.max_rate * (1 - policy.hysteresis)
        quality = Quality.worst(quality, previous_quality)
    return value, back_inside, quality


def _advance(
    state: _ChannelState,
    policy: AlarmPolicy,
    violating: bool,
    back_inside: bool,
    mission_ts: datetime,
) -> None:
    """Persistence starts an alarm; hysteresis ends it."""
    if violating:
        if state.run_start is None:
            state.run_start, state.run_samples = mission_ts, 0
        state.run_samples += 1
        if state.onset is None and _persisted(
            policy.persistence, state.run_samples, mission_ts - state.run_start
        ):
            state.onset = state.run_start
    else:
        state.run_start, state.run_samples = None, 0
        if state.onset is not None and back_inside:
            state.onset = None


def lookback_s(policy: AlarmPolicy, rate_limited: bool) -> float:
    """How far back, in mission time, R0's decision to fire can reach under ``policy``.

    Consecutive samples of a violation are at most ``max_gap_s`` apart: a longer
    silence resets the channel. Persistence over N samples therefore spans at most
    N - 1 such steps. Persistence over T seconds fires at the first sample at least
    T after the violation began, which the step before it had not reached: less
    than T + ``max_gap_s``. A rate limit reads the sample before the violation too.
    """
    if isinstance(policy.persistence, PersistenceSamples):
        span = (policy.persistence.samples - 1) * policy.max_gap_s
    else:
        span = policy.persistence.seconds + policy.max_gap_s
    return span + (policy.max_gap_s if rate_limited else 0.0)


def _persisted(
    persistence: PersistenceSamples | PersistenceSeconds, samples: int, lasted: timedelta
) -> bool:
    if isinstance(persistence, PersistenceSamples):
        return samples >= persistence.samples
    return lasted.total_seconds() >= persistence.seconds


def nominal_envelope(values: Sequence[float], margin: float) -> tuple[float, float]:
    """Low and high limits: the range of ``values``, each side pushed out by
    ``margin`` times its width."""
    low, high = min(values), max(values)
    if high <= low:
        raise ValueError("a constant signal has no envelope")
    pad = margin * (high - low)
    return low - pad, high + pad


def derive_limits(
    *,
    mission: str,
    kinds: Mapping[str, ChannelKind],
    nominal: Mapping[str, Sequence[float]],
    margin: float,
    defaults: AlarmPolicy,
    provenance: dict[str, Any],
) -> LimitsConfig:
    """Limits for a dataset that has none (docs/adr/0007-r0-limits-from-nominal-envelope.md).

    ``nominal`` holds, per channel, the values labelled nominal in the training
    window, and nothing else: fitting never sees the test period. A channel with
    nothing to fit is left unchecked, with the reason written into the file.
    """
    channels: dict[str, LimitsCheck | StatesCheck | NoCheck] = {}
    for name, kind in sorted(kinds.items()):
        values = nominal.get(name, ())
        if kind is ChannelKind.TELECOMMAND:
            channels[name] = NoCheck(check="none", reason="a telecommand is not a measurement")
        elif not values:
            channels[name] = NoCheck(
                check="none", reason="no nominal sample in the training window"
            )
        elif kind is ChannelKind.CATEGORICAL:
            channels[name] = StatesCheck(check="states", allowed=sorted(set(values)))
        elif max(values) <= min(values):
            channels[name] = NoCheck(check="none", reason="constant in the training window")
        else:
            low, high = nominal_envelope(values, margin)
            channels[name] = LimitsCheck(check="limits", low=low, high=high, max_rate=None)
    return LimitsConfig(
        mission=mission, provenance=provenance, defaults=defaults, channels=channels
    )
