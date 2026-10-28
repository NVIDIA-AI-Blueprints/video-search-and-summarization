# LVS performance benchmark package

State: **service-owned; common-pipeline validation and hardware smoke pending**

This is the canonical LVS performance benchmark package owned beside the
`video-summarization` service. It was migrated from the finalized
`benchmarking-perf-eval` POC into the standard service package categories
without changing benchmark behavior.

## Contents

- `configs/`: scenario, platform, defaults, KPI, and override YAML files.
- `benchmark/core/`: LVS benchmark client, modes, adapters, and CPU/GPU monitors.
- `scripts/`: existing end-to-end runner, uploader, environment generator, and
  RTSP helper.
- `setup/pyproject.toml` and `setup/uv.lock`: reproducible host-side benchmark
  dependencies, separate from the deployed LVS image.
- `setup/setup_environment.sh`: synchronizes the locked benchmark environment.
- `setup/compose/`: Compose recipes and their transitive local dependencies.
- `setup/monitoring/`: Prometheus, Grafana, DCGM, and dashboard assets.
- `setup/scripts/`: existing setup script.
- `../tests/perf/`: LVS perf tests and expected-result fixture.

## Pipeline integration

The common `benchmark-perf-test` pipeline checks out the requested VSS revision
and invokes `perf/scripts/run_benchmark.sh` directly from the
`video-summarization` service directory. The runner resolves benchmark configs,
Python modules, Compose recipes, the locked uv environment, and reports from
this service-owned `perf/` tree.

The existing `vss_perf_common` API now acts as a compatibility facade over
`libs/vss-perf-reporting` for the shared schema, run naming, JSON writing, and
MinIO publication. LVS scenario execution, KPI calculation, and monitoring
remain service-owned and unchanged.

When publication is enabled, Jenkins validates and uploads the canonical report
after the benchmark exits. The benchmark process does not receive MinIO
credentials. Local manual uploads must set `MINIO_ENDPOINT` and credentials
explicitly.

The promotion trigger supplies:

```text
PERF_ROOT=services/video-summarization/perf
PERF_ENTRYPOINT=scripts/run_benchmark.sh
```

Run the offline unit tests from this service directory:

```bash
bash tests/perf/run_tests.sh
```

Runtime verification requires a Jenkins validation-only run followed by a
shared-GPU smoke run. The `artifacts-promotion` triggers remain disabled until
that POC validation is complete.

### Optional monitoring network

The monitoring and OpenTelemetry Compose files attach to the network of an
already-running benchmark stack. Set `BENCHMARK_NETWORK` to that Docker network
before starting either optional stack. For example:

```bash
export BENCHMARK_NETWORK=x86-rtvi-cr-nemo3-nano-2gpu_app-network
docker compose -f perf/setup/monitoring/compose.yaml up -d
```

Use `docker network ls` to confirm the project-specific network name.
