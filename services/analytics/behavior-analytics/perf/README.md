# Behavior Analytics playback benchmark

This package measures the promoted Behavior Analytics image with the warehouse 2D Kafka playback stack used by `tests/integration`. It starts Kafka and `main_analytics_2d_app.py` first, starts the latency monitor, and only then starts `playback_frames.py` with `tests/data/warehouse_2d_playback_data.txt`.

The published Behavior Analytics latency for frames, behavior, events, and incidents is that topic's latency-monitor summary minus the raw-topic summary. The result keeps both values. A negative corrected value fails the run. Percentile correction subtracts the printed percentile columns; it does not recompute percentiles from individual messages.

`perf/configs/kpis.yaml` is the regression gate. After correction, the writer requires raw/frames/behavior/events/incidents and checks min counts plus Yun He's p50/p99 (frames 20/50 ms, incidents 30/60 ms, behavior and events 60/120 ms). A miss sets `success` false and records `kpis.violations` in the native `execution_summary.json`. Deploy overlays `behaviorEmitOnce=false` (product default) so the behavior KPI measures live updates; the integration fixture still has emit-once on.

Playback does not use a GPU. The result is the Behavior Analytics overhead on recorded frames, not warehouse blueprint end-to-end latency.

## Run

Set `ARTIFACT_REF` to the promoted image, then run:

```bash
./perf/scripts/run_benchmark.sh
```

Benchmark-runner dependencies are separate from the deployed Behavior Analytics
image and are locked under `perf/setup/`:

```bash
./perf/setup/setup_environment.sh
```

Change `perf/setup/pyproject.toml` and regenerate `perf/setup/uv.lock` together.

The canonical result is written beneath
`perf/results/behavior-analytics_<scenario>_<config-id>/vss-perf-results/` as
`behavior-analytics_<config-id>_<timestamp>.json`. It uses the same versioned
`metadata`, `summary`, and `test_cases` contract as LVS. The native
`execution_summary.json` remains in the parent run directory for diagnostics.
The dashboard-facing `metadata.service` value and case-sensitive MinIO prefix are
`Behavior-Analytics`. Lowercase `behavior-analytics` remains the stable local
filename and Jenkins artifact-directory slug.

Jenkins validates and uploads the canonical file unchanged to
`s3://<bucket>/Behavior-Analytics/<same-filename>`. MinIO credentials are bound
only around the Jenkins-owned publisher and are not passed to this benchmark.

## Jenkins

The shared job checks out the VSS repository with
`PERF_ROOT=services/analytics/behavior-analytics/perf` and invokes the common
`scripts/run_benchmark.sh` entrypoint beneath that root. The wrapper delegates
to the unchanged service-native benchmark implementation. Jenkins archives the
configured `PERF_REPORT_DIR`. `perf/configs/perf-configs.yaml` declares the
integration and shared-infrastructure paths that the benchmark needs in
addition to the service-owned `perf/` package. It also declares the single
playback fixture that must be hydrated from Git LFS.

`generate_env.sh` resolves the shared infrastructure from
`deploy/docker/services/infra`. The benchmark continues to use the existing
`tests/integration` Compose files and does not require a duplicated root-level
`perf/` trampoline or `compose/docker/` mirror.

`deploy.sh` pulls the promoted Behavior Analytics image, `chmod a+rwX` on the kafka/elasticsearch bind mounts under `apps_data`, and runs `compose up --build` for `COMPOSE_PROFILES` plus `analytics-stream`. `--build` is required for local infra images (`mdx-broker-health-check`, elasticsearch, kafka-topic-init). The analytics service itself has no Compose `build:` key, so the pulled NGC tag is not rebuilt. Playback is not in that up list; `measure.sh` starts it after the latency monitor.

`perf-configs.yaml` sets `composePath` so Jenkins exports a non-empty `COMPOSE_FILE`. The job's benchmark wrapper uses `set -u` and otherwise dies before `vss_perf_benchmark.py`. Playback still uses `deploy.sh` / `measure.sh`, not that compose file, for start/stop.

The Jenkins entrypoint accepts the common flags (`--config-id`, `--scenario`,
and `--output-json`) and owns deployment, measurement, and teardown.
`PERF_SCENARIO=quick_test` selects the 60-second playback window. Jenkins passes
the run-specific output directory, then validates and archives the complete
directory, including the canonical report and native diagnostic evidence.

## Shared reporting

`perf/benchmark/vss_perf_ba_adapter.py` is the only Behavior Analytics-specific
report mapping. It preserves the benchmark's native topic latency and KPI data
under a canonical test case. Schema validation, naming, platform discovery, and
atomic writes come from `libs/vss-perf-reporting`; pipeline publication is
owned centrally by Jenkins.
