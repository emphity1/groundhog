# SPDX-License-Identifier: Apache-2.0
"""R0 limits from the nominal training envelope (ADR 0007): fitted on what training may see."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from groundhog.detectors.r0_limits import (
    AlarmPolicy,
    LimitsCheck,
    NoCheck,
    StatesCheck,
    derive_limits,
    load_limits,
    nominal_envelope,
)
from groundhog.ingest import IngestError
from groundhog.ingest.opssat import OpssatSourceConfig, nominal_values
from groundhog.schema import ChannelKind
from replay_doubles import write_replay_config

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "derive_limits.py"
POLICY = AlarmPolicy.model_validate(
    {"hysteresis": 0.05, "persistence": {"samples": 3}, "max_gap_s": 60}
)
BEFORE_THE_GAP = datetime(2022, 1, 4, 21, 0, tzinfo=UTC)


class TestEnvelope:
    def test_the_nominal_range_pushed_out_by_the_margin(self) -> None:
        assert nominal_envelope([1.0, 3.0, 2.0], 0.5) == (0.0, 4.0)
        assert nominal_envelope([1.0, 3.0], 0.0) == (1.0, 3.0)

    def test_a_constant_signal_has_no_envelope(self) -> None:
        with pytest.raises(ValueError, match="constant"):
            nominal_envelope([2.0, 2.0], 0.1)


class TestDerivation:
    def test_every_kind_of_channel_gets_a_check_or_a_reason(self) -> None:
        limits = derive_limits(
            mission="m",
            kinds={
                "number": ChannelKind.NUMERIC,
                "count": ChannelKind.COUNTER,
                "mode": ChannelKind.CATEGORICAL,
                "command": ChannelKind.TELECOMMAND,
                "unseen": ChannelKind.NUMERIC,
                "flat": ChannelKind.NUMERIC,
            },
            nominal={
                "number": [0.0, 10.0],
                "count": [5.0, 7.0],
                "mode": [2.0, 0.0, 2.0, 1.0],
                "command": [1.0],
                "flat": [4.0, 4.0],
            },
            margin=0.1,
            defaults=POLICY,
            provenance={"made": "for a test"},
        )
        checks = limits.channels
        assert checks["number"] == LimitsCheck(check="limits", low=-1.0, high=11.0, max_rate=None)
        assert isinstance(checks["count"], LimitsCheck)
        assert (checks["count"].low, checks["count"].high) == pytest.approx((4.8, 7.2))
        assert checks["mode"] == StatesCheck(check="states", allowed=[0.0, 1.0, 2.0])
        for name, reason in [
            ("command", "not a measurement"),
            ("unseen", "no nominal sample"),
            ("flat", "constant"),
        ]:
            check = checks[name]
            assert isinstance(check, NoCheck) and reason in check.reason


class TestNominalValues:
    def test_only_samples_labelled_nominal_inside_the_window(
        self, opssat_config: OpssatSourceConfig
    ) -> None:
        # CH_C's only segment is labelled anomalous; the window ends before 23:00.
        values = nominal_values(opssat_config.path, None, BEFORE_THE_GAP)
        assert values == {
            "CH_A": [1.0, 1.01, 1.02, 1.03, 1.04],
            "CH_B": [2.0, 2.01, 2.02, 2.03, 2.04],
        }

    def test_start_is_inclusive_and_end_exclusive(self, opssat_config: OpssatSourceConfig) -> None:
        start = datetime(2022, 1, 4, 20, 0, 1, tzinfo=UTC)
        end = datetime(2022, 1, 4, 20, 0, 3, tzinfo=UTC)
        assert nominal_values(opssat_config.path, start, end)["CH_A"] == [1.01, 1.02]

    def test_a_label_other_than_zero_or_one_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "segments.csv"
        path.write_text("channel,timestamp,value,anomaly\nA,2022-01-04T20:00:00Z,1,x\n")
        with pytest.raises(IngestError, match="neither 0 nor 1"):
            nominal_values(path, None, None)


class TestScript:
    def spec(self, tmp_path: Path, source: OpssatSourceConfig) -> Path:
        tree = {
            "source": str(write_replay_config(tmp_path, source)),
            "training": {"start": None, "end": BEFORE_THE_GAP.isoformat()},
            "margin": 0.1,
            "defaults": POLICY.model_dump(mode="json"),
            "output": str(tmp_path / "limits.yaml"),
        }
        path = tmp_path / "limits.derive.yaml"
        path.write_text(yaml.safe_dump(tree), encoding="utf-8")
        return path

    def derive(self, *args: str | Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *map(str, args)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

    def test_writes_the_limits_with_their_provenance(
        self, tmp_path: Path, opssat_config: OpssatSourceConfig
    ) -> None:
        done = self.derive(self.spec(tmp_path, opssat_config))
        assert done.returncode == 0, done.stderr
        limits = load_limits(tmp_path / "limits.yaml")
        assert limits.mission == "opssat-test"
        assert limits.channels["CH_A"] == LimitsCheck(
            check="limits", low=1.0 - 0.004, high=1.04 + 0.004, max_rate=None
        )
        assert isinstance(limits.channels["CH_C"], NoCheck)
        provenance = limits.provenance
        assert provenance["data_md5"] == hashlib.md5(opssat_config.path.read_bytes()).hexdigest()
        assert provenance["training"] == {"start": None, "end": "2022-01-04T21:00:00Z"}
        assert provenance["nominal_samples"] == {"CH_A": 5, "CH_B": 5, "CH_C": 0}

    def test_the_same_data_and_spec_give_the_same_bytes(
        self, tmp_path: Path, opssat_config: OpssatSourceConfig
    ) -> None:
        spec = self.spec(tmp_path, opssat_config)
        self.derive(spec)
        first = (tmp_path / "limits.yaml").read_bytes()
        self.derive(spec)
        assert (tmp_path / "limits.yaml").read_bytes() == first

    def test_check_catches_a_stale_or_edited_file_and_writes_nothing(
        self, tmp_path: Path, opssat_config: OpssatSourceConfig
    ) -> None:
        spec = self.spec(tmp_path, opssat_config)
        output = tmp_path / "limits.yaml"
        assert self.derive(spec, "--check").returncode == 1  # nothing derived yet
        self.derive(spec)
        assert self.derive(spec, "--check").returncode == 0
        output.write_text(output.read_text().replace("1.044", "1.5"), encoding="utf-8")
        edited = output.read_bytes()
        assert self.derive(spec, "--check").returncode == 1
        assert output.read_bytes() == edited


@pytest.mark.skipif(
    not (REPO / "data" / "raw" / "opssat" / "segments.csv").exists(),
    reason="OPSSAT-AD not fetched",
)
def test_the_shipped_opssat_limits_are_what_the_data_and_the_spec_derive() -> None:
    done = subprocess.run(
        [sys.executable, str(SCRIPT), "configs/limits/opssat.derive.yaml", "--check"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
