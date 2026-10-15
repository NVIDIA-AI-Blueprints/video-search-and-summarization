# LVS source record

| Field | Value |
|---|---|
| Source repository | `ssh://git@gitlab-master.nvidia.com:12051/via/long-video-summarization.git` |
| Source branch inspected | `main` |
| Source commit | `60c40b9b8beb61bf1cc6865e510a498677060483` |
| Port date | 2026-10-13 |
| Jira | AAAI-1222 |
| POC package repository | `a3ie-infra/benchmarking-perf-eval` |
| POC package commit | `02bb6fd955520cd8288583a536d0237e173637f3` |

## Mapping

| Original LVS source | Service-owned destination |
|---|---|
| `ci/perf-configs.yaml` | `configs/perf-configs.yaml` |
| `perf/benchmark/*.yaml` | `configs/` |
| `perf/benchmark/overrides/` | `configs/overrides/` |
| `perf/benchmark/*.py` | `benchmark/core/` |
| `perf/benchmark/examples/` | `benchmark/examples/` |
| `perf/benchmark/requirements.txt` | `benchmark/requirements.txt` |
| `perf/benchmark/README.md` | `benchmark/README.md` |
| `perf/tests/` | `../tests/perf/` |
| `compose/run_benchmark.sh`, `compose/upload_perf_results.sh` | `scripts/` |
| `perf/gen_env.sh`, `perf/tools/stream_rtsp.sh` | `scripts/` |
| Root Compose YAML, `configmaps/`, and `overlays/` | `setup/compose/` |
| `compose/setup.sh` | `setup/scripts/setup.sh` |
| `perf/compose.yaml`, dashboards, DCGM, Grafana, Prometheus | `setup/monitoring/` |

## Intentional exclusions

- LVS Jenkinsfiles and the full LVS CI helper library: the common pipeline owns
  orchestration.
- `compose/BlueprintBuilderGenerated/`: contains generated profile content and
  tracked `.env` files and is not referenced by `ci/perf-configs.yaml`.
- Application source, image-build files, general functional/integration tests,
  videos, model artifacts, caches, virtual environments, and generated results.

The verification utility compares the ported files with this pinned working
tree and reports missing, extra, mode-changed, or content-changed files.
