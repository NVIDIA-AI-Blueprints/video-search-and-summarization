# LVS source record

| Field | Value |
|---|---|
| Source repository | `ssh://git@gitlab-master.nvidia.com:12051/via/long-video-summarization.git` |
| Source branch inspected | `main` |
| Source commit | `46720ff15883d9a92228a680247822ebffee5e14` |
| Port date | 2026-10-29 |
| Jira | AAAI-1222 |
| POC package repository | `a3ie-infra/benchmarking-perf-eval` |
| POC package commit | `02bb6fd955520cd8288583a536d0237e173637f3` |

## Mapping

| Original LVS source | Service-owned destination |
|---|---|
| `ci/perf-configs.yaml` | `configs/perf-configs.yaml` |
| `perf/benchmark/*.yaml` | `configs/` |
| `perf/benchmark/*.py` | `benchmark/core/` |
| `perf/benchmark/requirements.txt` | Dependencies migrated to `setup/pyproject.toml`; exact resolution in `setup/uv.lock` |
| N/A | `setup/setup_environment.sh` standardizes locked environment creation |
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
- Unreferenced benchmark overrides, the standalone MinIO example, and legacy
  Cosmos/Nemotron NGINX configs.
- Application source, image-build files, general functional/integration tests,
  videos, model artifacts, caches, virtual environments, and generated results.

Use the pinned source commit and mapping above when reviewing later upstream
synchronizations. Compare benchmark behavior separately from the documented
VSS repository adaptations so service-owned integration changes are preserved.

## Upstream synchronization

The 2026-10-29 synchronization applied the benchmark-relevant upstream range
`60c40b9b8beb61bf1cc6865e510a498677060483..46720ff15883d9a92228a680247822ebffee5e14`.
Native benchmark behavior was ported hunk-for-hunk; service-owned paths, locked
`uv` setup, shared reporting, centralized publication, and hardened cleanup remain
VSS repository adaptations.
