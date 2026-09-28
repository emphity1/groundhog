# Groundhog

**Streaming anomaly detection for spacecraft housekeeping telemetry.**
Replays years of real ESA telemetry as a live feed, runs classic limit checking and ML detectors side by side, and measures what actually matters in a control room: how many alarms an operator gets per day, how late the detection is, and what it costs to run.

> **Status: early work in progress.** Nothing here detects anything yet. This README describes what is being built and how it will be measured. No results are published until they are reproducible with one command.

---

## Why

A satellite cannot be repaired. Once it is in orbit, the only defence against a failing component is noticing the degradation early enough to change how the spacecraft is operated.

Today most ground segments rely on **limit checking**: every telemetry parameter has fixed thresholds, and an alarm fires when a value leaves its band. That catches hard failures, and it misses three things that matter:

1. **Slow drift.** A battery loses capacity over years. The thresholds set at launch are still the thresholds ten years later.
2. **Correlations.** Temperature slightly high, current slightly high, wheel speed slightly off — each parameter inside its limits, together the signature of a failing bearing.
3. **Signal buried in noise.** Tighten the thresholds and the operator gets hundreds of alarms a day, which is the same as getting none.

Groundhog is a reference implementation of the alternative: a system that learns what "normal" looks like for each spacecraft, keeps the classic limit checks as a safety net, explains every event with its orbital context, and lets the operator's judgement feed back into the next model.

## What it does

- **Replays** historical telemetry as a live stream, preserving data gaps, irregular sampling rates and mission clock semantics — with a configurable time compression factor.
- **Detects** with five detectors running in parallel: fixed limits (OOL), an adaptive statistical baseline, a predictive model, a multivariate reconstruction model, and a low-cost unsupervised detector.
- **Explains** every event at creation time with its orbital context: eclipse, South Atlantic Anomaly crossing, ground station visibility, space weather conditions, recent telecommands.
- **Learns** from the operator: confirmed events and false positives go into a label store that feeds retraining. New models run in shadow mode before they are promoted.
- **Measures** itself: event-wise detection metrics from the ESA benchmark, plus the operational metrics nobody publishes — alarms per operator per day, detection delay, CPU and memory per monitored channel.

## Why the name

Groundhog is the **ground segment**, and it is also *Groundhog Day*: the core of the system is a replay engine that lives the same stretch of mission time over and over (same data, same window, same seed: same stream) until the detectors get it right.

## Architecture

Two planes that share one preprocessing module: an **offline plane** for research, training and reproducible evaluation, and an **online plane** where telemetry streams, detectors run and the operator console lives.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design — components, data model, the journey of a single sample, and the technology choices with their rationale.

An interactive mockup of the operator console (simulated data) is available separately; it shows the intended end state of the online plane.

## Data

All datasets are public and free. **None of them are committed to this repository** — the download script in `scripts/` verifies checksums, and `DATA.md` has the full citations.

| Dataset | What it is | Licence | Role here |
|---|---|---|---|
| [ESA Anomaly Dataset](https://zenodo.org/records/12528696) | 176 channels from two ESA missions, 17.5 years, 844 annotated events | CC BY 3.0 IGO | Primary dataset and benchmark |
| [OPSSAT-AD](https://zenodo.org/records/15108715) | Annotated telemetry from the ESA OPS-SAT CubeSat | CC BY 4.0 | Fast development loop |
| [Mars Express Power](https://zenodo.org/records/6327379) | 3 Martian years of telemetry and context | CC BY 4.0 | Second use case: forecasting |
| [NASA SMAP / MSL](https://github.com/khundman/telemanom) | Labelled spacecraft channels | NASA open | Comparison with earlier literature |
| [NASA PCoE](https://data.phmsociety.org/nasa/) | Li-ion battery ageing, bearing run-to-failure | NASA open | Degradation and remaining-useful-life models |
| [Celestrak](https://celestrak.org/) / Space-Track | Orbital elements | free | Orbital context |
| [NOAA SWPC](https://services.swpc.noaa.gov/json/) | Kp index, proton flux, GOES | public JSON | Space weather context |

What is **not** available: telemetry from a commercial operator in service. That data is proprietary. Groundhog is a reference implementation on public data, not a replica of any operational system.

## Metrics

Results go here once the evaluation harness runs end to end. The table below is the shape of what will be reported — every number produced by `make evaluate` on a pinned dataset version.

| Detector | Event recall | Precision | Median detection delay | Alarms / day | CPU per channel |
|---|---|---|---|---|---|
| Limit checking (OOL) | — | — | — | — | — |
| Adaptive statistical baseline | — | — | — | — | — |
| Predictive model | — | — | — | — | — |
| Reconstruction model | — | — | — | — | — |

The statistical baseline is not a formality. If a moving average with an adaptive threshold matches the neural models, that result gets published here as prominently as any other.

## Running it

The replay engine runs today, on the development dataset:

```bash
make setup                   # uv if installed, otherwise a virtualenv with pip
make data-fetch DS=opssat    # ~20 MB, checksums verified
make replay                  # OPSSAT-AD as a live stream, 1000x real time
```

`make replay` writes samples to stdout as JSON lines and logs to stderr. Ctrl+C leaves a checkpoint that the next run resumes from. The other entry points arrive with their milestones:

```bash
make dev-up        # k3s + Redpanda + TimescaleDB + MinIO, locally
make evaluate      # reproducible benchmark run, produces the metrics table
```

## Roadmap

- [x] **M0** — repository skeleton, data schema, dataset download scripts
- [x] **M1** — replay engine with gap fidelity and deterministic output: same data, window and seed give the same stream
- [ ] **M3** — limit checking end to end on files and pipes (`replay | detect`, no bus, no database)
- [ ] **M4** — evaluation harness and the first metrics on real data
- [ ] **M2** — ingest, stream transport, hot and cold storage
- [ ] **M5** — statistical baseline, then the ML detectors
- [ ] **M6** — orbital and space weather context enrichment
- [ ] **M7** — operator console, feedback loop, shadow-mode model promotion

M3 and M4 come before M2. The replay already emits JSON lines, so a detector can read them from stdin with no bus and no database. That brings the first real numbers much sooner. Milestones keep their numbers; only the order changed.

## Licence and attribution

Code is licensed under the [Apache Licence 2.0](LICENSE). Datasets keep their own licences, listed above; attribution requirements are recorded in `NOTICE` and `DATA.md`.

Groundhog is an independent project. It is **not affiliated with, endorsed by, or connected to** the European Space Agency, NASA, or any other space agency or operator. It uses their openly published data under the terms those agencies set.
