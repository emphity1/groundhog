# SPDX-License-Identifier: Apache-2.0
"""The configuration a detection run reads, ``configs/detect/*.yaml``. Every key is required."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from groundhog.arbiter import ArbiterSettings

__all__ = ["DetectConfig", "R0Settings", "load_config"]


class R0Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["r0_limits"]
    limits: Path = Field(description="configs/limits/<mission>.yaml")


class DetectConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    detector: R0Settings
    arbiter: ArbiterSettings
    max_wait_s: float = Field(
        gt=0, allow_inf_nan=False, description="Longest wait for input; bounds stop latency"
    )


def load_config(path: Path) -> DetectConfig:
    with path.open(encoding="utf-8") as fh:
        return DetectConfig.model_validate(yaml.safe_load(fh))
