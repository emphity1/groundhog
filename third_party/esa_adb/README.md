# ESA-ADB reference metrics (vendored, unchanged)

The reference implementation of the ESA Anomaly Detection Benchmark metrics, kept here so the tests can check Groundhog's own implementation (`src/groundhog/eval/esa_adb.py`) against it, number for number, on the same inputs. Nothing under `src/` imports it.

- Source: <https://github.com/kplabs-pl/ESA-ADB>, path `timeeval/metrics/`
- Commit: `67194d389c4aa33a9a61fa0715ebdac3002b11b2` (2026-09-23)
- Licence: MIT, see [`LICENSE`](LICENSE) (Airbus, KP Labs, Phillip Wenig and Sebastian Schmidl)
- Paper: *European Space Agency Benchmark for Anomaly Detection in Satellite Telemetry*, arXiv:2406.17826

| File | SHA-256 |
|---|---|
| `esa_adb_metrics/ESA_ADB_metrics.py` | `89458fcaf3642633c804ecd4285b9a39d80d9b694785f6e9c6d62827af3ab940` |
| `esa_adb_metrics/latency_metrics.py` | `f4accaa0119d114d21d8db60002947847a6944441d226e9ea0a6c9b19f37f988` |
| `esa_adb_metrics/metric.py` | `1b6835e8ee2667b7831653821a68fe3c83f92e9b6cf1a63ca4a4beb0ba491c22` |
| `esa_adb_metrics/ranking_metrics.py` | `0bbd1f6773561c8b0bc0cc7a6f8d3a3d16f4475440a422f865f7df96ef3d1ccd` |
| `esa_adb_metrics/utils.py` | `f786084f4ddf283f6cc57412e7ef86c2183854dd414e39f68ae4c79b7746805d` |
| `LICENSE` | `4104db33ae6c6d3864313f0e6e4087d1580cae0c04f85e271fd0e8511dd91129` |

The five modules are copied byte for byte. The only file written for Groundhog is `esa_adb_metrics/__init__.py`, which replaces the original package initialiser: that one imports every TimeEval metric and their dependencies.

The modules need `numpy`, `pandas` and `portion`, which are development dependencies of Groundhog, plus two imports the tests replace with stand-ins rather than installing:

- `sklearn.utils` is used only by input validation that the tests do not call.
- `affiliation_based_metrics_repo` is a git submodule of the original repository, used only for the affiliation-based score, which Groundhog does not report yet.
