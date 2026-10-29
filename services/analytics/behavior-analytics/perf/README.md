# Behavior Analytics performance benchmark

This directory contains the Kafka playback performance benchmark for the
Behavior Analytics service. It starts the warehouse 2D analytics stack, starts
the latency monitor, and then replays the recorded warehouse frames.

The benchmark reports corrected latency for frames, behaviors, events, and
incidents. Each corrected value is the output-topic latency minus the raw-topic
latency. Playback does not use a GPU, so these results measure Behavior
Analytics processing overhead rather than full blueprint end-to-end latency.

## Prerequisites

- Docker with the Compose plugin
- `uv` for the host-side benchmark environment
- Access to the Behavior Analytics image and supporting container images
- Git LFS content for `tests/integration/docker_compose/apps_data/playback/warehouse_2d_playback_data.txt`

Run these commands from `services/analytics/behavior-analytics`.

## Setup

Prepare the locked Python environment used by the benchmark runner:

```bash
./perf/setup/setup_environment.sh
```

Set the Behavior Analytics image to test:

```bash
export ARTIFACT_REF=<behavior-analytics-image>
```

## Run

Run the default `quick_test` scenario:

```bash
./perf/scripts/run_benchmark.sh
```

Run an explicit scenario and configuration ID:

```bash
./perf/scripts/run_benchmark.sh \
  --scenario quick_test \
  --config-id warehouse_2d-kafka-playback
```

Use another scenario configuration file or choose the canonical output file:

```bash
./perf/scripts/run_benchmark.sh \
  --config perf/configs/config.yaml \
  --scenario quick_test \
  --output-json /tmp/behavior-analytics-result.json
```

The runner performs pre-flight checks, deploys the required services, runs the
playback measurement, writes the reports, and tears the stack down.

Scenario duration, warmup, topics, and percentiles are defined in
`perf/configs/config.yaml`. KPI thresholds are defined in
`perf/configs/kpis.yaml`.

## Results

By default, results are written beneath:

```text
perf/results/behavior-analytics_<scenario>_<config-id>/
```

The directory contains the native `execution_summary.json` and a canonical
report under `vss-perf-results/`. A failed KPI check sets `success` to `false`
and records the violations in the native summary.


## Cleanup safety

The benchmark uses a dedicated data directory beneath `/tmp`:

```text
/tmp/ba-perf-<compose-project>-<uid>
```

Directories under `/tmp` must start with `ba-perf-`. For a custom location,
set both `BA_PERF_ALLOWED_ROOT` and a child `BA_PERF_DATA_DIR`:

```bash
export BA_PERF_ALLOWED_ROOT=/mnt/perf-work
export BA_PERF_DATA_DIR=/mnt/perf-work/behavior-analytics-run
```

The runner rejects broad paths, symlinks, repository and workspace roots. It
creates a `.vss-ba-perf-owned` marker and teardown removes a directory only
when that marker belongs to the active Compose project.

Normal teardown removes volumes belonging to the active benchmark project only.
Removal of legacy global `mdx_*` volumes is disabled by default. Use
`BA_PERF_CLEAN_LEGACY_MDX_VOLUMES=true` only for an intentional one-time
cleanup; the runner prints a warning before doing so.


## Tests

Prepare the benchmark environment, then run the focused tests from the
repository root:

```bash
PYTHONPATH=services/analytics/behavior-analytics/perf/benchmark:libs/vss-perf-reporting/src \
  services/analytics/behavior-analytics/perf/setup/.venv/bin/python \
  -m unittest discover \
  -s services/analytics/behavior-analytics/perf/tests \
  -p 'test_*.py'
```
