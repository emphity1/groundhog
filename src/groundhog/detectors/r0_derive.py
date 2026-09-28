# SPDX-License-Identifier: Apache-2.0
"""How R0's limits are derived from nominal training data (docs/adr/0007).

One procedure, two callers: ``scripts/derive_limits.py`` writes the limits file the
detect configuration ships with, and the evaluation harness derives fresh limits in
every fold, from that fold's training data only (ADR 0001).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from groundhog.detectors.r0_limits import AlarmPolicy, LimitsConfig, derive_limits
from groundhog.replay.config import Window
from groundhog.schema import ChannelKind

__all__ = ["RULE", "DerivationSpec", "derive", "limits_yaml", "load_spec"]

RULE = (
    "min and max of the samples labelled nominal in the training window, "
    "each side pushed out by margin x (max - min)"
)


class DerivationSpec(BaseModel):
    """``configs/limits/<mission>.derive.yaml``: how a limits file is derived."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: Path = Field(description="Replay configuration naming the data and channel kinds")
    training: Window
    margin: float = Field(ge=0, allow_inf_nan=False)
    defaults: AlarmPolicy
    output: Path


def load_spec(path: Path) -> tuple[DerivationSpec, str]:
    """The spec, and the SHA-256 of its bytes for the provenance."""
    raw = path.read_bytes()
    return DerivationSpec.model_validate(yaml.safe_load(raw)), hashlib.sha256(raw).hexdigest()


def derive(
    *,
    mission: str,
    kinds: Mapping[str, ChannelKind],
    nominal: Mapping[str, Sequence[float]],
    spec: DerivationSpec,
    spec_path: Path,
    spec_sha256: str,
    data_md5: str,
    training: Mapping[str, Any],
    generator: str,
    defaults: AlarmPolicy | None = None,
) -> LimitsConfig:
    """Limits fitted on ``nominal``, with the provenance that says where they come from.

    ``training`` describes the data ``nominal`` was taken from, as JSON; ``defaults``
    replaces the spec's alarm policy, for the harness's sensitivity runs.
    """
    return derive_limits(
        mission=mission,
        kinds=kinds,
        nominal=nominal,
        margin=spec.margin,
        defaults=spec.defaults if defaults is None else defaults,
        provenance={
            "generator": generator,
            "spec": spec_path.as_posix(),
            "spec_sha256": spec_sha256,
            "data_md5": data_md5,
            "training": dict(training),
            "rule": RULE,
            "margin": spec.margin,
            "nominal_samples": {name: len(nominal.get(name, [])) for name in sorted(kinds)},
        },
    )


def limits_yaml(limits: LimitsConfig) -> str:
    """The limits as the YAML a limits file holds, without its comment header."""
    return yaml.safe_dump(
        limits.model_dump(mode="json", exclude_unset=True), sort_keys=False, default_flow_style=None
    )
