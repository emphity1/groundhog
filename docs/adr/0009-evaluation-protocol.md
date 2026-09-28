# 0009 — The evaluation protocol, as the harness runs it

- Status: **accepted**
- Date: 2026-09-28

## Context

ADR 0001 fixed the walk-forward and the official split, ADR 0008 the rule for events cut short by a gap, and ARCHITECTURE §4.15 the requirements set before M4. Building the harness raised the remaining choices, recorded here. Each of them changes a number the README publishes.

One choice was not covered anywhere. With test blocks on the hour, as in ADR 0001's table, five labelled events straddle a boundary: four at 07:00 on 2 June, among them the longest (`CADC0874`, 06:32–08:21), and one at 11:00 (`CADC0874`, 10:54–12:02). Three options:

1. **Boundaries where no labelled event is under way.** Each event belongs whole to one block, and timing is always measured from its true start.
2. **Hourly boundaries, annotations clipped to each block.** The five events count in two blocks. Their tails start at the block start, not at their true start, so they must be kept out of the timing measures.
3. **Hourly boundaries, each event in the block where it starts.** That block's evaluated window stretches to the event's end, and the next block masks the stretch: windows overlap.

Option 1 was chosen before any result was computed.

## Decision

### Folds

- **Test blocks** are set in `configs/eval/opssat_r0.yaml`, mission time, start inclusive and end exclusive:

  | Block | Labelled events | Channels |
  |---|---|---|
  | 1 Feb 00:00 – 12 Feb 00:00 | 14 | 2 |
  | 1 Jun 23:00 – 2 Jun 03:00 | 44 | 7 |
  | 2 Jun 03:00 – 06:29 | 68 | 7 |
  | 2 Jun 06:29 – 10:49 | 75 | 7 |
  | 2 Jun 10:49 – 16:00 | 30 | 6 |

  06:29 and 10:49 are the last whole minutes before 07:00 and 11:00 at which no labelled event is under way on any channel. The harness refuses a configuration whose boundaries cut a labelled event.
- **Training data** is everything before the test start minus the embargo: the origin rolls, the window grows.
- **The embargo** is computed, never typed: the longest lookback over every configuration evaluated, plus the longest labelled event over all labels. R0's lookback is (N − 1) × `max_gap_s` for persistence over N samples, T + `max_gap_s` for persistence over T seconds, and one `max_gap_s` more with a rate limit. On OPSSAT-AD, with the 150 s run below, it is 300 s + 6,545 s = 6,845 s. Every run of a fold uses the same embargo, so the runs differ only in what they are meant to vary.
- **Limits.** In every fold R0's limits are derived by the same procedure as `make limits` (`src/groundhog/detectors/r0_derive.py`). They are written next to the report, read back, and refused unless the data MD5 in their provenance matches the lock and their training window ends no later than the embargo's start.
- **A fold is usable** with at least 10 labelled events and nominal training data for every channel it tests. Otherwise the harness refuses to run.
- **Cold start.** The detector starts at the first sample of the block, with no warm-up on the embargo.

### The stream

- **The offline replay** runs the replay engine itself, same readings and same order (ADR 0003), on a clock that never waits. At speed 1 from the block start, `wall_ts` equals `mission_ts`, so the whole run is deterministic.
- **Alarms** are events as `groundhog.detect` publishes them. The last record of each id is the alarm (ADR 0006).

### Metrics

- **ESA-ADB's scores** are a port of the reference implementation, which is vendored unchanged in `third_party/esa_adb` (commit 67194d3). Tests run both on the same inputs, 300 random cases plus the reference's own examples, and require agreement within 1e-12. Where the reference fails (no nominal time left for the TNR correction), the port refuses too.
  - An alarm is a detection over `[t_start, t_end]`: the series is flagged at `t_start` and cleared 1 ns after `t_end`, so its last firing instant belongs to it. An alarm still open when the stream ends runs to the end of the block.
  - The event-wise scores use the logical sum of every channel in the block. The channel-aware score and ADTQC use the channels one by one, as ESA-ADB defines them.
  - Not ported: the affiliation-based score, which is for later, and the label-type filters, since OPSSAT-AD has no label types.
- **Groundhog's own measures**, marked as such wherever they appear:
  - **Alarm delay.** For each labelled event an alarm on its channel overlaps: `fired_at` of the earliest such alarm, minus the annotated start. The README carries its median.
  - **`fired_at` minus onset,** over every alarm: what persistence and hysteresis cost in time.
  - **The per-channel table:** each channel's alarms against its own labelled events, with ESA-ADB's event-wise definitions.
  - **Alarms per day:** distinct alarms over telemetry time in days. Telemetry time is the union, over channels, of the block's continuous runs (ADR 0001's rule). Silence therefore does not dilute the rate.
  - **CPU per channel:** process CPU time of detector and arbiter over the block's stream, with the samples already in memory. Passes repeat until 1 s of CPU has accumulated and the mean per pass is taken, so the process clock's resolution (15.6 ms on Windows) does not show. The result, 1000 × CPU seconds / telemetry seconds / channels, is in millicores per channel needed to keep up with real time, on the machine the report names.
  - **Coverage** per channel: samples scored over samples received.
- **Summary over the folds:** mean, minimum and maximum. A fold where a measure is undefined, such as a timing measure with no detection, is left out, and the number of folds counted is shown. A fold with no alarm has precision 0 by ESA-ADB's convention, and the report says so wherever that happens.
- **Events cut short by a gap:** every fold also runs with each `max_gap_s` in `gap_sensitivity_s` (60 s and 150 s). The headline numbers come from the spec's value, 60 s.

### The official split

- **Segment classification**, for comparison with the literature only.
  - Limits are fitted on the segments the dataset marks for training and labels nominal. The whole archive is replayed through R0.
  - A test segment is flagged when an alarm on its channel overlaps it, and ranked by the highest score R0 gives inside it.
- **The seven metrics** are computed as the OPSSAT-AD paper computes them with scikit-learn. They are reimplemented without the dependency and checked against scikit-learn 1.9.1: on 3,000 random cases the discrete metrics agree exactly and the areas within 5e-16.

### The report

- `make evaluate` writes `reports/<name>/report.md` and `report.json`, with the limits and the alarms of every run next to them. `reports/` is not versioned.
- The run header names:
  - the commit, and whether the tree had uncommitted changes;
  - the configuration hash, the data MD5 and the embargo;
  - the machine.
- The README's numbers come from a report produced on a clean tree.

## Consequences

- Every published number is regenerated by one command. `tests/test_eval_real_data.py` pins the counts behind them on the real data, so a change that moves them fails a test and has to be decided in the open.
- The block boundaries were chosen from the labels, not from any detector output. The numbers of labelled events per block differ from ADR 0001's table, which used hourly boundaries.
- Every number is deterministic except CPU, which depends on the machine and varies by a few percent between runs.
- A detector with a longer lookback, or a model in M5, lengthens the embargo for every fold. That is the intended behaviour: the protocol adapts to what is evaluated, and nobody retypes a number.
