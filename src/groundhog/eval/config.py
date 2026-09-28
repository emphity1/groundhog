# SPDX-License-Identifier: Apache-2.0
"""The configuration an evaluation reads, ``configs/eval/*.yaml``. Every key is required."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    "Block",
    "EvalConfig",
    "GroundTruthSettings",
    "MetricSettings",
    "WalkForward",
    "load_config",
]


class Block(BaseModel):
    """A test block: mission time, UTC, start inclusive, end exclusive."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start: AwareDatetime
    end: AwareDatetime

    @field_validator("start", "end")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _ordered(self) -> Block:
        if self.end <= self.start:
            raise ValueError("a block must end after it starts")
        return self


class WalkForward(BaseModel):
    """ADR 0001, condition 4: a rolling origin over these test blocks."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    min_events: int = Field(ge=1, description="Labelled events a test block needs")
    blocks: list[Block] = Field(min_length=1)

    @model_validator(mode="after")
    def _in_order(self) -> WalkForward:
        for earlier, later in zip(self.blocks, self.blocks[1:], strict=False):
            if later.start < earlier.end:
                raise ValueError("test blocks must come in order and must not overlap")
        return self


class GroundTruthSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_gap_periods: float = Field(
        gt=0, description="A silence longer than this many sampling periods breaks a run"
    )


class MetricSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    beta: float = Field(gt=0, description="F-beta of the event-wise and channel-aware scores")
    adtqc_exponent: float = Field(gt=0)
    gap_sensitivity_s: list[Annotated[float, Field(gt=0, allow_inf_nan=False)]] = Field(
        min_length=1, description="R0's max_gap_s values for which redundant alarms are reported"
    )
    cpu_min_s: float = Field(
        ge=0,
        allow_inf_nan=False,
        description="Detection passes repeat until their CPU time adds up to this; the mean is reported",
    )


class EvalConfig(BaseModel):
    """``configs/eval/<name>.yaml``: one benchmark run, from data to report."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    replay: Path = Field(description="Replay configuration: data, channels, seed")
    detect: Path = Field(description="Detect configuration: the arbiter's severity bands")
    limits: Path = Field(description="Derivation spec: margin and alarm policy of R0's limits")
    lock: Path = Field(description="data/datasets.lock.json, the data version fetched")
    ground_truth: GroundTruthSettings
    walk_forward: WalkForward
    metrics: MetricSettings
    output: Path = Field(description="Directory the report is written to")


def load_config(path: Path) -> EvalConfig:
    with path.open(encoding="utf-8") as fh:
        return EvalConfig.model_validate(yaml.safe_load(fh))
