# Behavior Analytics playback benchmark

This package measures the promoted Behavior Analytics image with the warehouse 2D Kafka playback stack used by `tests/integration`. It starts Kafka and `main_analytics_2d_app.py` first, starts the latency monitor, and only then starts `playback_frames.py` with `tests/data/warehouse_2d_playback_data.txt`.

The published Behavior Analytics latency for frames, behavior, events, and incidents is that topic's latency-monitor summary minus the raw-topic summary. The result keeps both values. A negative corrected value fails the run. Percentile correction subtracts the printed percentile columns; it does not recompute percentiles from individual messages.

Playback does not use a GPU. The result is the Behavior Analytics overhead on recorded frames, not warehouse blueprint end-to-end latency.

## Run

Set `ARTIFACT_REF` to the promoted image, then run:

```bash
./perf/run_benchmark.sh
```

The result is written to `perf/output/result.json`.

## Jenkins

`PERF_FOLDER` is `perf` and `PERF_USE_RUN_BENCHMARK_SCRIPT` is `true`. `run_benchmark.sh` is the entrypoint. `PERF_SCENARIO=quick_test` selects the 60-second playback window.
