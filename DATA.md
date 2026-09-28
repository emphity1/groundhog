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

What the files actually contain (checked against the fetched files, 2026-09-28):

- **`segments.csv`** (19 MB, 303,493 rows): the telemetry, one sample per row, columns `channel, timestamp, value, label, sampling, anomaly, segment, train`. No missing values, no duplicate `(channel, timestamp)`.
  - `channel`: nine opaque ids, `CADC0872` to `CADC0894`. No units anywhere in the files. `CADC0872/0873/0874` stay within ±1e-4, magnitudes consistent with a magnetometer in tesla (unconfirmed); the other six range from 0 to 1.5708.
  - `timestamp`: ISO 8601 in UTC, e.g. `2022-06-01T23:42:54.000Z`; the milliseconds are always zero.
  - `sampling`: 1 or 5 seconds, set per segment, not per channel. The 1 s segments are perfectly regular. The 5 s segments carry ±1–2 s jitter and 36 internal gaps of 15–130 s.
  - `anomaly` (0/1) is the label, one per segment: 434 of 2,123 segments, a third of the samples. There is no position inside a segment and no rare-nominal class. **`label` is the constant string `anomaly` on every row, normal ones included, and carries no information.**
  - `segment` ids do not follow time. `train` is the official split, see below.
- **`dataset.csv`** (2,123 rows × 23 columns): per-segment statistics computed by the authors' `dataset_generator.ipynb`. This is derived data. Groundhog computes its own features with its preprocessing module and uses this file at most as a cross-check.
- **Coverage.** The data spans 4 January to 2 June 2022, but each channel covers only 0.4–1.2 % of that span: January, early February and the night of 1–2 June, with a 125-day hole in between. The segments are back-to-back windows cut from 243 continuous runs (typically ~12 minutes, the longest 14.5 hours), separated by 234 gaps lasting from seconds to months. Channels are sampled at the same instants (86–99 % shared timestamps), but segment boundaries never coincide across channels.
- **Official split.** `train` marks 1,594 segments for training and 529 for testing. It is stratified by label but not chronological: the two sets interleave over the whole period, and adjacent windows of one run often land on opposite sides. See [ADR 0001](docs/adr/0001-opssat-train-test-split.md).
- **Licence discrepancy.** The record's metadata declares CC BY 4.0, while the `LICENSE` file shipped inside the record is an MIT licence (KP Labs, 2024). Follow the attribution above until the authors clarify.

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
