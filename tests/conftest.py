# SPDX-License-Identifier: Apache-2.0
"""Fixtures shared across test modules."""

from __future__ import annotations

from pathlib import Path

import pytest

from groundhog.ingest.opssat import OpssatSourceConfig
from synthetic_opssat import CHANNELS, write_segments


@pytest.fixture
def opssat_config(tmp_path: Path) -> OpssatSourceConfig:
    """The synthetic OPSSAT-AD file, configured as the replay would read it."""
    return OpssatSourceConfig(
        type="opssat",
        path=write_segments(tmp_path / "segments.csv"),
        mission="opssat-test",
        channels=CHANNELS,
    )
