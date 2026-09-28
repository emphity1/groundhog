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

Every number here is produced by `make evaluate` on a pinned dataset version. The report it writes has the ESA-ADB metrics in full, fold by fold, and the comparison with the literature ([ADR 0009](docs/adr/0009-evaluation-protocol.md)).

**OPSSAT-AD, walk-forward over 5 folds**, with an embargo of 6,845 s computed from the detector and the labels ([ADR 0001](docs/adr/0001-opssat-train-test-split.md)). Each cell gives the mean over the folds, then the lowest and highest fold in brackets. Four of the five folds come from one June night, so the spread is a lower bound of the real variance. Produced at commit `d26905f`.

| Detector | Event recall | Precision | Median alarm delay | Alarms / day | CPU per channel |
|---|---|---|---|---|---|
| Limit checking (R0) | 0.038 [0.000–0.143] | 0.400 [0.000–1.000]¹ | 327 s [39–615]² | 2.7 [0.0–7.2]³ | 0.0029 millicores [0.0005–0.0042]⁴ |
| Adaptive statistical baseline | — | — | — | — | — |
| Predictive model | — | — | — | — | — |
| Reconstruction model | — | — | — | — | — |

1. Corrected event-wise precision, as ESA-ADB defines it. R0 raised 3 alarms over the 5 folds, and none of them was false. In the 3 folds with no alarm at all, ESA-ADB counts precision as 0.
2. Groundhog's own measure: `fired_at` minus the annotated start. It is averaged over the 2 folds in which R0 detected anything.
3. Per day of telemetry actually received, not per calendar day.
4. Measured on an AMD Ryzen 7 7700X. This is the only number that depends on the machine.

What the first row says:

- R0's limits come from nominal data, not mission documents ([ADR 0007](docs/adr/0007-r0-limits-from-nominal-envelope.md)).
- With those limits, limit checking detects 4 of the 231 labelled events in the test blocks.
- In every fold, the configured check cannot fire at all on 1 to 5 of the channels: their values never leave the limits.
- On the dataset's official split, scored for comparison with published baselines only, R0 flags no test segment (AUC-ROC 0.544).

The statistical baseline is not a formality. If a moving average with an adaptive threshold matches the neural models, that result gets published here as prominently as any other.

## Running it

Replay, limit checking and the benchmark run today, on the development dataset:

```bash
make setup                   # uv if installed, otherwise a virtualenv with pip
make data-fetch DS=opssat    # ~20 MB, checksums verified
make replay                  # OPSSAT-AD as a live stream, 1000x real time
make replay | make detect    # R0 limit checking on that stream: events as JSON lines
make evaluate                # the benchmark: the report goes to reports/opssat_r0/
```

`make replay` writes samples to stdout as JSON lines and logs to stderr. Ctrl+C leaves a checkpoint that the next run resumes from.

`make detect` reads those samples on stdin and writes a run header, then events, as JSON lines. Its limits come from `make limits`, which derives them from nominal training data because OPSSAT-AD ships none.

`make evaluate` runs the whole benchmark in under a minute. In every fold it derives R0's limits from that fold's training data only. It refuses to run on data that is not the version recorded when it was fetched.

The other entry points arrive with their milestones:

```bash
make dev-up        # k3s + Redpanda + TimescaleDB + MinIO, locally
```

## Roadmap

- [x] **M0** — repository skeleton, data schema, dataset download scripts
- [x] **M1** — replay engine with gap fidelity and deterministic output: same data, window and seed give the same stream
- [x] **M3** — limit checking end to end on files and pipes (`replay | detect`, no bus, no database)
- [x] **M4** — evaluation harness and the first metrics on real data
- [ ] **M2** — ingest, stream transport, hot and cold storage
- [ ] **M5** — statistical baseline, then the ML detectors
- [ ] **M6** — orbital and space weather context enrichment
- [ ] **M7** — operator console, feedback loop, shadow-mode model promotion

M3 and M4 come before M2. The replay already emits JSON lines, so a detector can read them from stdin with no bus and no database. That brings the first real numbers much sooner. Milestones keep their numbers; only the order changed.

## Licence and attribution

Code is licensed under the [Apache Licence 2.0](LICENSE). Datasets keep their own licences, listed above; attribution requirements are recorded in `NOTICE` and `DATA.md`.

Groundhog is an independent project. It is **not affiliated with, endorsed by, or connected to** the European Space Agency, NASA, or any other space agency or operator. It uses their openly published data under the terms those agencies set.
