# Behavior Analytics playback benchmark

This package measures the promoted Behavior Analytics image with the warehouse 2D Kafka playback stack used by `tests/integration`. It starts Kafka and `main_analytics_2d_app.py` first, starts the latency monitor, and only then starts `playback_frames.py` with `tests/data/warehouse_2d_playback_data.txt`.

The published Behavior Analytics latency for frames, behavior, events, and incidents is that topic's latency-monitor summary minus the raw-topic summary. The result keeps both values. A negative corrected value fails the run. Percentile correction subtracts the printed percentile columns; it does not recompute percentiles from individual messages.

`perf/configs/kpis.yaml` is the regression gate. After correction, the writer requires raw/frames/behavior/events/incidents and checks min counts plus Yun He's p50/p99 (frames 20/50 ms, incidents 30/60 ms, behavior and events 60/120 ms). A miss sets `success` false and records `kpis.violations` in `result.json`. Deploy overlays `behaviorEmitOnce=false` (product default) so the behavior KPI measures live updates; the integration fixture still has emit-once on.

Playback does not use a GPU. The result is the Behavior Analytics overhead on recorded frames, not warehouse blueprint end-to-end latency.

## Run

Set `ARTIFACT_REF` to the promoted image, then run:

```bash
./perf/run_benchmark.sh
```

The result is written to `perf/results/behavior-analytics_<scenario>_<config-id>/result.json`. Jenkins already passes `--upload` and MinIO keys; the writer PUTs a timestamped copy to `s3://perf-results-dev/behavior-analytics/...` (`10.111.52.248:9000`, same endpoint as compose-base). The dashboard still has to onboard this service name.

## Jenkins

The shared job checks out this service with
`SCM_FOLDER=services/analytics/behavior-analytics` and archives
`perf/results/...`. `perf/configs/perf-configs.yaml` declares the integration
and shared-infrastructure paths that the benchmark needs in addition to the
service-owned `perf/` package. It also declares the single playback fixture
that must be hydrated from Git LFS.

`generate_env.sh` resolves the shared infrastructure from
`deploy/docker/services/infra`. The benchmark continues to use the existing
`tests/integration` Compose files and does not require a duplicated root-level
`perf/` trampoline or `compose/docker/` mirror.

`deploy.sh` pulls the promoted Behavior Analytics image, `chmod a+rwX` on the kafka/elasticsearch bind mounts under `apps_data`, and runs `compose up --build` for `COMPOSE_PROFILES` plus `analytics-stream`. `--build` is required for local infra images (`mdx-broker-health-check`, elasticsearch, kafka-topic-init). The analytics service itself has no Compose `build:` key, so the pulled NGC tag is not rebuilt. Playback is not in that up list; `measure.sh` starts it after the latency monitor.

`perf-configs.yaml` sets `composePath` so Jenkins exports a non-empty `COMPOSE_FILE`. The job's benchmark wrapper uses `set -u` and otherwise dies before `vss_perf_benchmark.py`. Playback still uses `deploy.sh` / `measure.sh`, not that compose file, for start/stop.

The Jenkins entrypoint accepts the common flags (`--config-id`, `--scenario`,
`--output-json`, and `--upload`). When Jenkins has already run the stage
scripts it only measures. `PERF_SCENARIO=quick_test` selects the 60-second
playback window. Results are written to
`{checkout}/perf/results/behavior-analytics_<scenario>_<config-id>/`.
