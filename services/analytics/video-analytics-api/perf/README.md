# Video Analytics API performance benchmark

This package measures dump-backed HTTP query latency for the Video Analytics
API. It starts Elasticsearch, loads the Warehouse 2D integration-test dump,
starts the selected API image, and measures the supported OpenAPI requests.

Kafka remains disabled. Behavior Analytics Kafka playback is a separate
benchmark.

## Prerequisites

- Docker with the Compose plugin
- `uv` for the isolated host-side Python environment
- Access to the Video Analytics API and Elasticsearch images
- Git LFS content for the JSON files under
  `test/integration-test/elasticsearch_data_dump/`

Run these commands from `services/analytics/video-analytics-api`.

## Setup

Prepare the locked Python environment:

```bash
./perf/setup/setup_environment.sh
```

Set the promoted API image to benchmark:

```bash
export ARTIFACT_REF=<video-analytics-api-image>
```

## Run

Run the default dump-backed quick test:

```bash
./perf/scripts/run_benchmark.sh
```

Select the scenario, configuration, release, and output directory explicitly:

```bash
./perf/scripts/run_benchmark.sh \
  --scenario quick_test \
  --config-id warehouse-es-dump \
  --release 3.4.0 \
  --output-json /tmp/video-analytics-api-perf
```

The lifecycle performs pre-flight checks, deploys Elasticsearch and the API,
loads the Warehouse dump, measures the endpoint matrix, writes results, and
tears down resources belonging to the run.

Measurement inputs are defined in `perf/configs/config.yaml`. The endpoint
matrix is derived from `src/app/specification/openapi.json`. KPI thresholds are
defined in `perf/configs/kpis.yaml`.

## KPI behavior

Every required endpoint must meet its request-count, success-ratio, p50, and p99
limits. A violation makes the command fail while preserving the measured
latencies in the report.

The original five endpoint limits are estimates from an earlier Jenkins run.
Other GETs and calibration POSTs use provisional defaults until service owners
publish approved SLOs. They are intentionally enforced as regression gates in
this implementation.

## Results

By default, results are written beneath:

```text
perf/results/video-analytics-api_<scenario>_<config-id>/
```

The native `execution_summary.json` contains the unchanged service measurement.
The canonical dashboard report is written under `vss-perf-results/` with one
test case per API endpoint and service identity `Video-Analytics-API`.

Report publication is intentionally outside this service package.

## Cleanup safety

The benchmark uses a project-specific directory below `/tmp` whose first path
component starts with `va-api-perf-`. Teardown removes it only when its
ownership marker matches the active Compose project.

For a custom location, set both an allowed root and a child directory:

```bash
export VA_API_PERF_ALLOWED_ROOT=/mnt/perf-work
export VA_API_PERF_DATA_DIR=/mnt/perf-work/video-analytics-api-run
```

Repository roots, workspace roots, broad paths, symlinks, and directories
without a valid ownership marker are rejected.

## Tests

From the repository root:

```bash
PYTHONPATH=services/analytics/video-analytics-api/perf/benchmark:libs/vss-perf-reporting/src \
  uv run --project services/analytics/video-analytics-api/perf/setup --group dev \
  pytest services/analytics/video-analytics-api/perf/tests
```
