# SPDX-License-Identifier: Apache-2.0
"""The evaluation report, in Markdown for people and JSON for tools.

Both carry the run header: the commit, the configuration, the data version and the
machine, everything needed to regenerate the numbers or to judge the ones that
depend on the machine. Both carry the statements every reader must see before the
numbers: where the limits come from, what the folds are, which metrics are
ESA-ADB's and which are Groundhog's own.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from groundhog import __version__
from groundhog.eval.esa_adb import EventWise
from groundhog.eval.harness import Distribution, Evaluation, FoldResult

__all__ = ["DISCLAIMERS", "markdown", "run_header", "summary", "write"]

_NONE = "n/a"  # a number that is undefined, never a zero

DISCLAIMERS = {
    "limits": (
        "R0's limits do not come from mission documents. In every fold they are derived "
        "from the samples labelled nominal in that fold's training data (ADR 0007), so "
        "they are a reasonable approximation of an industrial baseline, not the "
        "baseline itself."
    ),
    "folds": (
        "Four of the five folds come from the same June night, one operating regime. "
        "The folds are not independent samples of the mission, and the dispersion "
        "across them is a lower bound of the real variance."
    ),
    "metrics": (
        "The event-wise scores, the channel-aware scores and ADTQC are ESA-ADB's, "
        "computed exactly as its reference implementation computes them (arXiv:2406.17826; "
        "src/groundhog/eval/esa_adb.py). Alarm delay, the delay from onset to firing, "
        "the per-channel table and the operational numbers are Groundhog's own."
    ),
    "official split": (
        "The official split is scored only for comparison with the literature. Its "
        "training and test segments interleave in time, so its numbers carry temporal "
        "leakage and are not Groundhog's result (ADR 0001)."
    ),
}


def run_header(config: Path, started_at: datetime) -> dict[str, Any]:
    """Which code, configuration and machine produced a report."""
    return {
        "tool": "groundhog.eval",
        "version": __version__,
        "command": f"python -m groundhog.eval --config {config.as_posix()}",
        "commit": _run("git", "rev-parse", "HEAD"),
        "uncommitted_changes": _run("git", "status", "--porcelain") not in ("", None),
        "config": config.as_posix(),
        "config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
        "started_at": started_at.astimezone(UTC).isoformat(),
        "machine": {
            "cpu": _cpu_name(),
            "logical_cpus": os.cpu_count(),
            "os": platform.platform(),
            "python": platform.python_version(),
        },
    }


def summary(evaluation: Evaluation) -> dict[str, dict[str, float | int | None]]:
    """The headline numbers over the folds: mean, minimum and maximum. A fold where a
    number is undefined, ADTQC with no detection for instance, is left out of it."""
    measures: dict[str, Callable[[FoldResult], float | None]] = {
        "event_wise_precision": lambda f: f.event_wise.precision,
        "event_wise_recall": lambda f: f.event_wise.recall,
        "event_wise_f_beta": lambda f: f.event_wise.f_beta,
        "alarming_precision": lambda f: f.event_wise.alarming_precision,
        "channel_aware_f_beta": lambda f: f.channel_aware.f_beta,
        "adtqc": lambda f: f.adtqc.score,
        "median_alarm_delay_s": lambda f: f.alarm_delay_s.median,
        "alarms_per_day": lambda f: f.alarms_per_day,
        "millicores_per_channel": lambda f: f.millicores_per_channel,
    }
    table: dict[str, dict[str, float | int | None]] = {}
    for name, measure in measures.items():
        values = [v for v in (measure(f) for f in evaluation.folds) if v is not None]
        table[name] = {
            "folds": len(values),
            "mean": statistics.fmean(values) if values else None,
            "min": min(values) if values else None,
            "max": max(values) if values else None,
        }
    return table


def write(evaluation: Evaluation, header: dict[str, Any], directory: Path) -> tuple[Path, Path]:
    """``report.md`` and ``report.json`` in ``directory``."""
    directory.mkdir(parents=True, exist_ok=True)
    document = {
        "run": header,
        "read_this_first": DISCLAIMERS,
        "summary": summary(evaluation),
        "evaluation": dataclasses.asdict(evaluation),
    }
    json_path = directory / "report.json"
    json_path.write_text(
        json.dumps(document, indent=2, default=_json) + "\n", encoding="utf-8", newline="\n"
    )
    md_path = directory / "report.md"
    md_path.write_text(markdown(evaluation, header), encoding="utf-8", newline="\n")
    return md_path, json_path


def markdown(evaluation: Evaluation, header: dict[str, Any]) -> str:
    e = evaluation
    machine = header["machine"]
    commit = header["commit"] or "unknown"
    state = "with uncommitted changes" if header["uncommitted_changes"] else "clean"
    lines = [
        "# Groundhog evaluation: R0 limit checking on OPSSAT-AD",
        "",
        f"Generated by `{header['command']}`.",
        "",
        "| | |",
        "|---|---|",
        f"| Commit | `{commit}` ({state}) |",
        f"| Configuration | `{header['config']}`, sha256 `{header['config_sha256'][:16]}…` |",
        f"| Data | `{e.data_file}`, md5 `{e.data_md5}`, as recorded in the lock |",
        f"| Labelled events | {e.labelled_events}, over the whole dataset |",
        f"| Embargo | {e.embargo.seconds:,.0f} s: lookback {e.embargo.lookback_s:,.0f} s + "
        f"longest labelled event {e.embargo.longest_event_s:,.0f} s |",
        f"| Metrics | F-beta with beta = {e.beta:g}; ADTQC exponent {e.adtqc_exponent:.6g}; "
        f"headline runs with `max_gap_s` = {e.headline_max_gap_s:g} s |",
        f"| Machine | {machine['cpu']}, {machine['logical_cpus']} logical CPUs, "
        f"{machine['os']}, Python {machine['python']} |",
        f"| Started | {header['started_at']} |",
        "",
        "## Read this first",
        "",
        *(f"- **{topic.capitalize()}.** {text}" for topic, text in DISCLAIMERS.items()),
        "",
        f"## Summary over the {len(e.folds)} folds",
        "",
        "Mean, and the range from the lowest to the highest fold. Dispersion across these "
        "folds is a lower bound of the real variance.",
        "",
        "| Measure | Whose | Mean | Min | Max | Folds |",
        "|---|---|---|---|---|---|",
    ]
    whose = {
        "event_wise_precision": ("Event-wise precision, corrected", "ESA-ADB", 3),
        "event_wise_recall": ("Event-wise recall", "ESA-ADB", 3),
        "event_wise_f_beta": (f"Event-wise F{e.beta:g}", "ESA-ADB", 3),
        "alarming_precision": ("Alarming precision", "ESA-ADB", 3),
        "channel_aware_f_beta": (f"Channel-aware F{e.beta:g}", "ESA-ADB", 3),
        "adtqc": ("ADTQC", "ESA-ADB", 3),
        "median_alarm_delay_s": ("Median alarm delay (s)", "Groundhog", 1),
        "alarms_per_day": ("Alarms per day of telemetry", "Groundhog", 1),
        "millicores_per_channel": ("CPU per channel (millicores)", "Groundhog", 4),
    }
    for name, row in summary(e).items():
        label, owner, digits = whose[name]
        lines.append(
            f"| {label} | {owner} | {_num(row['mean'], digits)} | {_num(row['min'], digits)} | "
            f"{_num(row['max'], digits)} | {row['folds']} |"
        )
    if silent := [str(f.number) for f in e.folds if f.alarms == 0]:
        lines += [
            "",
            f"No alarm at all in fold{'s' if len(silent) > 1 else ''} {', '.join(silent)}: "
            "there, precision and alarming "
            "precision are 0 by ESA-ADB's convention for no detection, not because of false "
            "alarms, and the timing measures are undefined.",
        ]
    for fold in e.folds:
        lines += _fold(fold, e.beta)
    lines += _official(e)
    return "\n".join(lines) + "\n"


def _fold(f: FoldResult, beta: float) -> list[str]:
    ew = f.event_wise
    lines = [
        "",
        f"## Fold {f.number}: {_when(f.test_start)} to {_when(f.test_end)}",
        "",
        f"Training data: everything before {_when(f.training_end)}. Limits: `{f.limits_file}`.",
        "",
        "| Channels | Samples | Labelled events | Telemetry time | Alarms | Alarms per day |",
        "|---|---|---|---|---|---|",
        f"| {len(f.channels)} | {f.samples:,} | {f.labelled_events} | "
        f"{f.observed_s / 3600:.2f} h | {f.alarms} | {f.alarms_per_day:.1f} |",
        "",
        "**ESA-ADB, event-wise** over the logical sum of the channels:",
        "",
        "| TP | FP | FN | Redundant | Precision (corrected) | TNR | Recall | "
        f"F{beta:g} | Alarming precision |",
        "|---|---|---|---|---|---|---|---|---|",
        f"| {ew.true_positives} | {ew.false_positives} | {ew.false_negatives} | {ew.redundant} "
        f"| {ew.precision:.3f} | {_num(ew.tnr, 4)} | {ew.recall:.3f} | {ew.f_beta:.3f} "
        f"| {ew.alarming_precision:.3f} |",
        *(
            ["", "No alarm in this block: both precisions are 0 by convention, not by error."]
            if f.alarms == 0
            else []
        ),
        "",
        f"**ESA-ADB, channel-aware:** precision {_num(f.channel_aware.precision, 3)}, "
        f"recall {_num(f.channel_aware.recall, 3)}, F{beta:g} {_num(f.channel_aware.f_beta, 3)}. "
        f"**ADTQC:** {_num(f.adtqc.score, 3)} over {f.adtqc.before + f.adtqc.after} detected "
        f"events, {f.adtqc.before} before their annotated start and {f.adtqc.after} after.",
        "",
        "**Groundhog's own timing:**",
        "",
        "| Measure | Count | Median | 90th percentile | Max |",
        "|---|---|---|---|---|",
        _dist("Alarm delay: `fired_at` minus annotated start (s)", f.alarm_delay_s),
        _dist("`fired_at` minus onset, every alarm (s)", f.firing_delay_s),
        "",
        f"**Operational cost:** {f.cpu_s:.3f} s of CPU for {f.observed_s / 3600:.2f} h of "
        f"telemetry on {len(f.channels)} channels: {f.millicores_per_channel:.4f} millicores "
        "per channel to keep up with real time.",
        "",
        f"**Channels on which the configured R0 check cannot fire over this block:** "
        f"{len(f.cannot_fire)} of {len(f.channels)}"
        + (f": {', '.join(f.cannot_fire)}." if f.cannot_fire else "."),
        "",
        "**Per channel** (Groundhog's own: each channel against its own labelled events):",
        "",
        f"| Channel | Samples | Coverage | Check | Can fire | Labelled | Alarms | TP | FP | FN "
        f"| Redundant | Precision | Recall | F{beta:g} |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in f.per_channel:
        lines.append(
            f"| {c.channel} | {c.samples:,} | {c.coverage:.1%} | {c.check} | "
            f"{'no' if c.cannot_fire else 'yes'} | {c.labelled_events} | {c.alarms} | "
            + _channel_scores(c.event_wise)
        )
    lines += [
        "",
        "**Events cut short by a gap** (ADR 0008): redundant alarms for each `max_gap_s`:",
        "",
        "| `max_gap_s` | Alarms | TP | Redundant | Alarming precision | Recall |",
        "|---|---|---|---|---|---|",
        *(
            f"| {g.max_gap_s:g} s | {g.alarms} | {g.true_positives} | {g.redundant} | "
            f"{g.alarming_precision:.3f} | {g.recall:.3f} |"
            for g in f.gap_sensitivity
        ),
    ]
    return lines


def _official(e: Evaluation) -> list[str]:
    o = e.official_split.scores
    return [
        "",
        "## Comparison with the literature: the official split",
        "",
        f"R0 with limits fitted on the official training segments (`{e.official_split.limits_file}`), "
        f"scored per test segment as the OPSSAT-AD paper scores its baselines: "
        f"{e.official_split.test_segments} test segments, "
        f"{e.official_split.anomalous_segments} anomalous. A segment is flagged when an alarm "
        "overlaps it and ranked by the highest score R0 gives inside it. Not Groundhog's "
        "result: see Read this first.",
        "",
        "| Accuracy | Precision | Recall | F1 | MCC | AUC-ROC | AUC-PR |",
        "|---|---|---|---|---|---|---|",
        f"| {o.accuracy:.3f} | {o.precision:.3f} | {o.recall:.3f} | {o.f1:.3f} | {o.mcc:.3f} "
        f"| {o.auc_roc:.3f} | {o.auc_pr:.3f} |",
        "",
        f"Confusion: {o.true_positives} TP, {o.false_positives} FP, {o.true_negatives} TN, "
        f"{o.false_negatives} FN.",
    ]


def _channel_scores(scores: EventWise | None) -> str:
    if scores is None:
        return " | ".join([_NONE] * 7) + " |"
    return (
        f"{scores.true_positives} | {scores.false_positives} | {scores.false_negatives} | "
        f"{scores.redundant} | {scores.precision:.3f} | {scores.recall:.3f} | "
        f"{scores.f_beta:.3f} |"
    )


def _dist(label: str, d: Distribution) -> str:
    return f"| {label} | {d.count} | {_num(d.median, 1)} | {_num(d.p90, 1)} | {_num(d.max, 1)} |"


def _num(value: float | int | None, digits: int) -> str:
    return _NONE if value is None else f"{value:,.{digits}f}"


def _when(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def _json(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return value.as_posix()
    raise TypeError(f"{type(value).__name__} is not JSON serialisable")


def _cpu_name() -> str:
    """The processor's marketing name where the platform tells it."""
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
            ) as key:
                return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except OSError:
            pass
    elif sys.platform == "linux":
        try:
            for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
        except OSError:
            pass
    elif sys.platform == "darwin":
        name = _run("sysctl", "-n", "machdep.cpu.brand_string")
        if name:
            return name
    return platform.processor() or "unknown"


def _run(*command: str) -> str | None:
    try:
        return subprocess.run(
            list(command), capture_output=True, text=True, timeout=30, check=True
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
