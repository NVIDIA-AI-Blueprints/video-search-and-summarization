# Behavior Analytics playback benchmark

This package measures the promoted Behavior Analytics image with the warehouse 2D Kafka playback stack used by `tests/integration`. It starts Kafka and `main_analytics_2d_app.py` first, starts the latency monitor, and only then starts `playback_frames.py` with `tests/data/warehouse_2d_playback_data.txt`.

The published Behavior Analytics latency for frames, behavior, events, and incidents is that topic's latency-monitor summary minus the raw-topic summary. The result keeps both values. A negative corrected value fails the run. Percentile correction subtracts the printed percentile columns; it does not recompute percentiles from individual messages.

`perf/configs/kpis.yaml` is the regression gate. After correction, the writer requires raw/frames/behavior/events/incidents and checks min counts plus Yun He's p50/p99 (frames 20/50 ms, incidents 30/60 ms, behavior and events 40/100 ms). A miss sets `success` false and records `kpis.violations` in `result.json`. Warehouse playback currently reports ~6 s behavior latency from `behaviorWatermarkSec`, which fails those behavior gates.

Playback does not use a GPU. The result is the Behavior Analytics overhead on recorded frames, not warehouse blueprint end-to-end latency.

## Run

Set `ARTIFACT_REF` to the promoted image, then run:

```bash
./perf/run_benchmark.sh
```

The result is written to `perf/results/behavior-analytics_<scenario>_<config-id>/result.json`. Jenkins already passes `--upload` and MinIO keys; the writer PUTs a timestamped copy to `s3://perf-results-dev/behavior-analytics/...` (`10.111.52.248:9000`, same endpoint as compose-base). The dashboard still has to onboard this service name.

## Jenkins

The shared job requires `{checkout}/perf/{configs,scripts,benchmark/core}` and archives `{checkout}/perf/results/...`. A trampoline at the VSS repo root `perf/` exposes that layout and delegates to this package (`SCM_FOLDER` empty, `PERF_FOLDER=perf`).

GPU nodes sparse-checkout `perf/`, `services/`, `ci/`, `compose/`, and `VERSION` — not `deploy/`. `generate_env.sh` therefore uses `deploy/docker` when it is present and `compose/docker` (a playback infra mirror) otherwise, so `${INFRA_DIR}/compose.yml` resolves.

`deploy.sh` pulls the promoted Behavior Analytics image, `chmod a+rwX` on the kafka/elasticsearch bind mounts under `apps_data`, and runs `compose up --build` for `COMPOSE_PROFILES` plus `analytics-stream`. `--build` is required for local infra images (`mdx-broker-health-check`, elasticsearch, kafka-topic-init). The analytics service itself has no Compose `build:` key, so the pulled NGC tag is not rebuilt. Playback is not in that up list; `measure.sh` starts it after the latency monitor.

`perf-configs.yaml` sets `composePath` so Jenkins exports a non-empty `COMPOSE_FILE`. The job's benchmark wrapper uses `set -u` and otherwise dies before `vss_perf_benchmark.py`. Playback still uses `deploy.sh` / `measure.sh`, not that compose file, for start/stop.

The trampoline accepts the compose-base flags (`--config-id`, `--scenario`, `--output-json`, `--upload`). When Jenkins has already run the stage scripts it only measures. `PERF_SCENARIO=quick_test` selects the 60-second playback window. Results are written to `{checkout}/perf/results/behavior-analytics_<scenario>_<config-id>/`.
