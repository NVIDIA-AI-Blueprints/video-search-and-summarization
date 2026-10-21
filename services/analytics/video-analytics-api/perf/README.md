# Video Analytics API query benchmark

This package measures the promoted Video Analytics API image with the warehouse 2D Elasticsearch dump used by `test/integration`. It starts product Elasticsearch and `elasticsearch-init-container`, bulk-loads `test/integration-test/elasticsearch_data_dump/`, waits for `GET /livez`, then times HTTP queries.

Kafka stays off (`brokers: []`). Behavior Analytics playback is a different job. This result is query latency against a frozen snapshot, not ingest latency.

`perf/configs/kpis.yaml` is the regression gate. Until an API owner publishes p50/p99 SLOs, the writer requires successful `/livez`, `/frames`, `/behavior`, `/events`, and `/incidents` samples plus a 1 s `/livez` p99 smoke limit. Query endpoints record percentiles but are not gated on them.

Playback does not use a GPU. The Jenkins node label matches the shared pool.

## Run

Set `ARTIFACT_REF` to the promoted image, then run:

```bash
./perf/run_benchmark.sh
```

The result is written to `perf/results/video-analytics-api_<scenario>_<config-id>/result.json`. Jenkins already passes `--upload` and MinIO keys; the writer PUTs a timestamped copy to `s3://perf-results-dev/video-analytics-api/...` (`10.111.52.248:9000`, same endpoint as compose-base). The dashboard still has to onboard this service name.

## Jenkins

The shared job requires `{checkout}/perf/{configs,scripts,benchmark/core}` and archives `{checkout}/perf/results/...`. A trampoline at the VSS repo root `perf/` exposes that layout and delegates to this package (`SCM_FOLDER` empty, `PERF_FOLDER=perf`, `PERF_SERVICE=va-api`).

GPU nodes sparse-checkout `perf/`, `services/`, `ci/`, `compose/`, and `VERSION` — not `deploy/`. `common.sh` therefore uses `deploy/docker` when it is present and `compose/docker` (an infra mirror) otherwise.

`deploy.sh` pulls the promoted API image, starts Elasticsearch with unique container names (`va-api-perf-*`) and host ports (19200 / 18081) so a Behavior Analytics playback job on the same node does not collide, loads the LFS dumps with `dump_loader.py`, and waits for `/livez`. `--build` is required for the local Elasticsearch image.

`perf-configs.yaml` sets `composePath` so Jenkins exports a non-empty `COMPOSE_FILE`. Dummy `vlmGpus` / `llmGpus` values keep the LVS wrapper from dying on `set -u`.
