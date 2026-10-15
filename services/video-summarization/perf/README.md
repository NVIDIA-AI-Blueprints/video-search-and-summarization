# LVS performance benchmark package

State: **service-owned; common-pipeline validation and hardware smoke pending**

This is the canonical LVS performance benchmark package owned beside the
`video-summarization` service. It was migrated from the finalized
`benchmarking-perf-eval` POC into the standard service package categories
without changing benchmark behavior.

## Contents

- `configs/`: scenario, platform, defaults, KPI, and override YAML files.
- `benchmark/core/`: LVS benchmark client, modes, adapters, and CPU/GPU monitors.
- `benchmark/requirements.txt`: benchmark Python dependencies.
- `scripts/`: existing end-to-end runner, uploader, environment generator, and
  RTSP helper.
- `setup/compose/`: Compose recipes and their transitive local dependencies.
- `setup/monitoring/`: Prometheus, Grafana, DCGM, and dashboard assets.
- `setup/scripts/`: existing setup script.
- `../tests/perf/`: LVS perf tests and expected-result fixture.

## Compatibility note

The existing LVS code assumes its original `ci/`, `perf/benchmark/`, and
`compose/` runtime locations. The common `benchmark-perf-test` pipeline uses
its `tools/materialize_lvs_package.sh` adapter to create a build-specific
compatibility workspace before invoking the unchanged runner.

The promotion trigger supplies:

```text
PERF_SOURCE=external
SCM_FOLDER=services/video-summarization
PERF_FOLDER=perf
```

Run the offline unit tests from this service directory:

```bash
bash tests/perf/run_tests.sh
```

Runtime verification requires a Jenkins validation-only run followed by a
shared-GPU smoke run. The `artifacts-promotion` triggers remain disabled until
that POC validation is complete.

