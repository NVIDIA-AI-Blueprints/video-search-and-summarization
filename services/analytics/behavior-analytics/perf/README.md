# Behavior Analytics playback benchmark

This package measures the promoted Behavior Analytics image with the warehouse 2D Kafka playback stack used by `tests/integration`. It starts Kafka and `main_analytics_2d_app.py` first, starts the latency monitor, and only then starts `playback_frames.py` with `tests/data/warehouse_2d_playback_data.txt`.

The published Behavior Analytics latency for frames, behavior, events, and incidents is that topic's latency-monitor summary minus the raw-topic summary. The result keeps both values. A negative corrected value fails the run. Percentile correction subtracts the printed percentile columns; it does not recompute percentiles from individual messages.

Playback does not use a GPU. The result is the Behavior Analytics overhead on recorded frames, not warehouse blueprint end-to-end latency.

## Run

Set `ARTIFACT_REF` to the promoted image, then run:

```bash
./perf/run_benchmark.sh
```

The result is written to `perf/results/behavior-analytics_<scenario>_<config-id>/result.json`.

## Jenkins

The shared job requires `{checkout}/perf/{configs,scripts,benchmark/core}` and archives `{checkout}/perf/results/...`. A trampoline at the VSS repo root `perf/` exposes that layout and delegates to this package (`SCM_FOLDER` empty, `PERF_FOLDER=perf`).

GPU nodes sparse-checkout `perf/`, `services/`, `ci/`, `compose/`, and `VERSION` — not `deploy/`. `generate_env.sh` therefore uses `deploy/docker` when it is present and `compose/docker` (a playback infra mirror) otherwise, so `${INFRA_DIR}/compose.yml` resolves.

`deploy.sh` pulls the promoted Behavior Analytics image, `chmod a+rwX` on the kafka/elasticsearch bind mounts under `apps_data`, and runs `compose up --build` for `COMPOSE_PROFILES` plus `analytics-stream`. `--build` is required for local infra images (`mdx-broker-health-check`, elasticsearch, kafka-topic-init). The analytics service itself has no Compose `build:` key, so the pulled NGC tag is not rebuilt. Playback is not in that up list; `measure.sh` starts it after the latency monitor.

The trampoline accepts the compose-base flags (`--config-id`, `--scenario`, `--output-json`, `--upload`). When Jenkins has already run the stage scripts it only measures. `PERF_SCENARIO=quick_test` selects the 60-second playback window. Results are written to `{checkout}/perf/results/behavior-analytics_<scenario>_<config-id>/`.
