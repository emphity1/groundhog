# Data

Groundhog runs entirely on publicly available data. **No dataset is committed to this repository.** Everything is fetched with `scripts/fetch_data.py`, which verifies each file against the checksum published by the source and records what it downloaded in `data/datasets.lock.json`.

```bash
make data-list              # what is registered and where it comes from
make data-fetch DS=opssat   # ~20 MB, the development dataset
make data-fetch DS=esa_adb  # ~12 GB, the primary dataset
```

Files land under `data/raw/<dataset>/` and stay out of git.

---

## Registered datasets

### `opssat` — OPSSAT-AD

Annotated telemetry from ESA's OPS-SAT CubeSat, with segments and derived features. Small enough to iterate on a laptop, which makes it the dataset every component is built against first.

- Record: <https://doi.org/10.5281/zenodo.15108715>
- Paper: *The OPS-SAT benchmark for detecting anomalies in satellite telemetry*, Scientific Data (2025) — <https://arxiv.org/abs/2407.04730>
- Licence: **CC BY 4.0**
- Attribution: credit the European Space Agency and the dataset authors as listed on the Zenodo record; cite the Scientific Data paper in any published result.

### `esa_adb` — ESA Anomaly Dataset

176 channels from two ESA missions across 17.5 years, with 844 events annotated by spacecraft operations engineers: 148 anomalies, 690 rare nominal events, 4 communication gaps. The primary dataset and the benchmark Groundhog reports against.

- Record: <https://doi.org/10.5281/zenodo.12528696>
- Paper: *European Space Agency Benchmark for Anomaly Detection in Satellite Telemetry* — <https://arxiv.org/abs/2406.17826>
- Benchmark code: <https://github.com/kplabs-pl/ESA-ADB> (MIT)
- Licence: **CC BY 3.0 IGO**
- Attribution: credit the European Space Agency, Airbus Defence and Space and KP Labs as listed on the record; cite the paper in any published result. The licence also requires that nothing implies endorsement by the licensor.

### `mars_express` — Mars Express Power

Three Martian years of telemetry and context from Mars Express, with a fourth year held out. Used for the forecasting use case, not for anomaly detection.

- Record: <https://doi.org/10.5281/zenodo.6327379>
- Paper: *Machine-learning ready data on the thermal power consumption of the Mars Express Spacecraft*, Scientific Data (2022)
- Licence: **CC BY 4.0**

### `smap_msl` — NASA SMAP / MSL

The older labelled telemetry benchmark from the telemanom paper. Kept only for comparison with earlier literature; its small size and unrealistic anomaly density are documented limitations.

- Source: <https://github.com/khundman/telemanom> (code Apache 2.0)
- Licence: NASA open data

### Context sources (fetched at runtime, not archived)

| Source | Use | Terms |
|---|---|---|
| [CelesTrak](https://celestrak.org/) / [Space-Track](https://www.space-track.org/) | Orbital elements for propagation | Free; Space-Track requires a free account. Respect their rate limits and usage policy. |
| [NOAA SWPC](https://services.swpc.noaa.gov/json/) | Kp index, proton flux, GOES data | Public JSON endpoints |
| IGRF via `ppigrf` | Geomagnetic field, South Atlantic Anomaly | Open source library |
| JPL ephemerides via `skyfield` | Sun position, eclipse geometry | Public |

---

## Rules

1. **Nothing downloaded is redistributed.** Groundhog ships code that fetches data, never the data itself, and never derived files (windows, scalers, model weights trained on it) unless their licence has been checked for that specific case.
2. **Every published result names its dataset version.** The lockfile records file names, sizes and checksums; the evaluation report embeds that version.
3. **Attribution travels with results.** Any table, plot or talk built on these datasets carries the attribution above.
4. **No endorsement is implied.** See `NOTICE`.

If you intend to build something commercial on top of this, read each licence yourself. CC BY 3.0 IGO in particular has terms that differ from plain CC BY, and nothing in this file is legal advice.
