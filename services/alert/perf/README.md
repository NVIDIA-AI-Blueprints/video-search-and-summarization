# Alerts verification latency benchmarks

Runs the native ci-vss-oss alerts evaluator using Docker Compose. The alert image
under test is supplied explicitly; supporting image versions live in
`setup/configs/images.yaml`. Measurement logic and evaluator configuration are
ported unchanged from `munjalp/alerts-eval-helm` at `617ccaf` (see
`benchmark/native/SOURCE.md`). Accuracy, Redis and Helm are outside this package's
supported scenarios.

## Run locally

Requirements: an exclusively reserved NVIDIA GPU node, Python >=3.10, uv, Docker
Compose v2, curl, jq, iproute2, and network access to NGC and the private
NVDataset package index. The current deployment profile supports the native
H100, L40S, RTX PRO 6000, Spark and Thor profile names; actual image/architecture
compatibility must be validated before enabling additional configurations.

```bash
export NGC_API_KEY='<runtime credential>'
export IMAGE_TAG='<registry>/vss-alert-ms:<version>'
export PERF_OUTPUT_DIR="$PWD/alerts-perf-results"
bash services/alert/perf/scripts/run_benchmark.sh --scenario synthetic_alerts

# Run all enabled scenarios (currently synthetic_alerts only):
bash services/alert/perf/scripts/run_benchmark.sh --scenario all
```

Each invocation runs one scenario. `synthetic_alerts` is synthetic Kafka alert
injection; `e2e_alerts_eval` keeps upstream alert generation enabled but is
currently disabled (`enabled: false`). Explicitly requesting the disabled scenario
is rejected before deployment. `all` selects only enabled scenarios and currently
runs `synthetic_alerts` only. Both
use the verification workflow and Prometheus latency measurements.

Both scenarios publish the agent on host port `8001`, forwarding to its internal
port `8000`. Set `global.deployment.agent_host_port` in `configs/config.yaml` to
use another available host port. The runner checks availability before dataset
preparation and deployment; this check does not reserve the port. It applies the
setting only to the private runtime profile's `overrides.env`, which the native
deployment copies into `generated.env`. An inherited `VSS_AGENT_HOST_PORT` does
not override this benchmark setting. Internal routing, health checks, and model
server ports remain unchanged.

Configure sensors, duration, source video, alert category, worker count,
synthetic event rate and coverage settings in `configs/config.yaml`. The initial
workload uses one sensor and five minutes. Five synthetic submissions provide a
smoke check, not a statistically strong p99 baseline. No extra latency threshold
is introduced by the adapter.

The default dataset is `Metropolis-Short-Video-Event-Verification`, tenant
`0573334707593577`, group `vss-bp-team`; videos are under `warehouse_chunks` and
annotations in `test_annotation.json`. The source video is
`warehouse_1_540_7.mp4`, category `FOV Count Violation`. Setup downloads the
dataset with the same NVDataset CLI/index used by run_eval.sh. Override
`PERF_DATASET_ROOT` to choose the cache parent directory. Incomplete existing
caches fail validation rather than silently being used. Dataset installation is
isolated in a helper venv; the host adapter environment is locked with uv.

The deployment uses a private runtime tree so latency-profile mutations never
change checked-in deployment files. Staging restores only the required Alerts
profile and NVStreamer `.env` templates, sanitizes credential assignments, and
writes the copies with `0600` permissions. Other `.env` files and stale
`generated.env` files remain excluded. Missing required templates fail staging
with the relative path in the error.

Native fixed container identities require
exclusive node access; an existing `vss` Compose project is rejected. Diagnostics
are collected before project teardown on success or failure. Failed runtime
trees are retained privately on the node for investigation and are not reports.

## Results

`PERF_OUTPUT_DIR` contains `native/` evaluator JSON/CSV files,
`vss-perf-results/` canonical `Alerts-Verification` reports, and `diagnostics/`.
Native aggregate latency values, coverage and percentile censoring flags are
preserved. Verification failures, dropped/unknown events and incomplete data are
reported as test failures. Completed evaluations with failed tests exit zero;
operational failures emit a failure report and exit nonzero. The service scripts
do not upload reports or require object-storage credentials.

Supporting blueprint defaults initially use `develop-latest`, matching the VSS
checkout's deployment baseline. Replace those values with pinned release/commit
tags before using results for release comparisons. The alert image itself is
always the explicit supplied reference, including digest references.

## Supporting container images

Edit `setup/configs/images.yaml` to select this benchmark's supporting images.
It contains a flat map of image variable names to complete references:

```yaml
schema_version: 1
images:
  PERF_PROMETHEUS_IMAGE: prom/prometheus:latest
```

Use the variable names already present in the service's file. The benchmark runner
loads and validates them before deployment; no separate image setup command is
needed. Explicit nonempty environment overrides take precedence. Clear an override
to use the file's value.

Compose files refer directly to these variables. Shared Dockerfiles receive base
images through build arguments and retain their existing defaults for ordinary
blueprint deployments. Image selection does not copy or rewrite those templates.
The service under test still uses the artifact selected for the run.

`support-images.json` records the effective support image variables alongside the
run output. Image values preserve existing references and do not certify OSRB
approval or record the immutable digests pulled by Docker.

Alerts still makes a private deployment copy for the native latency-profile
configurator. That copy is not used to rewrite image references; Compose receives
the target artifact and supporting images through environment variables.

### Local latency evaluation

Both synthetic Kafka and full-flow latency scenarios run evaluation from the
locked `setup/` Python environment. No Alert Bridge Evaluator or SURF Trigger Eval
image is required. Actual Alerts and support services still run through Compose.
The entrypoint and output-directory option are unchanged. Native evaluation JSON
and diagnostic CSV files are saved under the selected output's `native/ab-eval/`
directory; canonical benchmark reports retain the existing shared format.
