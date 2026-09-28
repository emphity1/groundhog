# SPDX-License-Identifier: Apache-2.0
"""The configuration a replay runs from, ``configs/replay/*.yaml``.

Every setting is required: nothing falls back to a value hidden in code, so the
YAML file alone describes the run.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from groundhog.ingest.opssat import OpssatSourceConfig

__all__ = [
    "CheckpointSettings",
    "JsonlStdoutSinkConfig",
    "ReplayConfig",
    "ReplaySettings",
    "Window",
    "load_config",
]


class Window(BaseModel):
    """The span of mission time to replay: start inclusive, end exclusive, null for open."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start: AwareDatetime | None
    end: AwareDatetime | None

    @field_validator("start", "end")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else value.astimezone(UTC)

    @model_validator(mode="after")
    def _ordered(self) -> Window:
        if self.start is not None and self.end is not None and self.end <= self.start:
            raise ValueError("window end must come after its start")
        return self


class ReplaySettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    speed: float = Field(gt=0, description="Mission seconds per wall second")
    seed: int = Field(description="Orders channels whose samples share a mission_ts")
    window: Window
    max_sleep_s: float = Field(gt=0, description="Longest single sleep; bounds stop latency")


class CheckpointSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: Path
    every_samples: int = Field(gt=0, description="Checkpoint after this many samples")
    min_idle_s: float = Field(ge=0, description="Checkpoint before any wait at least this long")


class JsonlStdoutSinkConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["jsonl_stdout"]


class ReplayConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source: OpssatSourceConfig
    replay: ReplaySettings
    checkpoint: CheckpointSettings
    sink: JsonlStdoutSinkConfig


def load_config(path: Path) -> ReplayConfig:
    with path.open(encoding="utf-8") as fh:
        return ReplayConfig.model_validate(yaml.safe_load(fh))
