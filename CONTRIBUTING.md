# Contributing

Groundhog is in early development. Issues, questions and pull requests are welcome.

## Developer Certificate of Origin

Every commit must be signed off:

```bash
git commit -s -m "feat(replay): deterministic ordering across restarts"
```

The `-s` adds a `Signed-off-by` line, which certifies that you wrote the change or have the right to submit it under the project's licence ([DCO 1.1](https://developercertificate.org/)). Commits without it will not be merged.

## Development setup

Requires GNU make and either [uv](https://docs.astral.sh/uv/) (recommended) or Python 3.12. uv installs exactly what `uv.lock` pins, on the interpreter pinned in `.python-version`, which it downloads if needed.

```bash
make setup               # create .venv and install everything
make lint                # ruff format --check + ruff check
make typecheck           # mypy on src/
make test                # pytest
```

Run all three (`make check`) before opening a pull request; CI runs the same commands.

The targets go through uv when it is on `PATH` and fall back to a plain virtualenv with `pip install -e ".[dev]"` otherwise. `make <target> UV=` forces the fallback, `PYTHON=/path/to/python3.12` picks its interpreter. The fallback does not read `uv.lock`, so versions can drift from CI: good enough to get started, not to reproduce a published number.

On Windows, run make from Git Bash. Git for Windows does not include make: `winget install ezwinports.make`.

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `perf:`, `chore:`, with an optional scope — `feat(detectors): add adaptive z-score baseline`.

## Code conventions

- `# SPDX-License-Identifier: Apache-2.0` as the first line of every source file.
- Type hints everywhere in `src/`; `mypy` must pass.
- Configuration lives in `configs/*.yaml`. No magic constants in modules.
- Integration tests use testcontainers against real Redpanda and Postgres, not mocks.

## Things that will get a pull request rejected

- **Committed data.** Datasets, derived Parquet files, model weights. The fetch scripts exist for this.
- **Temporal leakage.** Scalers or thresholds fitted outside the training period; shuffled splits on time series.
- **Non-reproducible results.** Numbers that `make evaluate` cannot regenerate from a pinned dataset version and a fixed seed.
- **Redefined metrics.** The event-wise metrics follow the ESA benchmark definitions so results stay comparable with published work. Adding a metric is fine; quietly changing one is not.
- **A model without its baseline.** Every detector is reported next to the adaptive statistical baseline.

## Architecture decisions

Anything that adds a service, a datastore or a heavy dependency, or that changes the sample or event schema, gets an ADR in `docs/adr/` first: context, options considered, decision, consequences. Open an issue to discuss before writing code.

## Reporting an issue

Include what you ran, what you expected, what happened, and the dataset version from `data/datasets.lock.json` if the problem involves data.
