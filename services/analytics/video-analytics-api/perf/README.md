# Video Analytics API query benchmark

This package measures the promoted Video Analytics API image with the warehouse 2D Elasticsearch dump used by `test/integration`. Deploy, Elasticsearch init, dump load, readiness, failure logs, and cleanup are the integration-test scripts (`generate_env.sh`, `lifecycle.sh`, `cleanup.sh`, `scripts/load_elasticsearch_data_dump.js`). Perf only adds Jenkins host ports, unique compose names, the mmap/bind-mount overlay, and HTTP timing.

Kafka stays off (`brokers: []`). Behavior Analytics playback is a different job. Kafka-dependent `POST /config/update/{docType}` and `GET /config/update/status/{docType}/{referenceId}` stay out of this iteration.

The measure step times every **OpenAPI GET** this warehouse dump can actually HTTP 200 (`endpoints: spec_gets`, 40 of 48 GETs in `src/app/specification/openapi.json`) plus two P0 writes the Kafka-off stack can HTTP 201: `POST /config/upload-file/calibration` and `POST /config/calibration/images` (warehouse fixtures). That matrix also includes `/metrics/average-speed`, `/metrics/flowrate`, `/frames/alerts`, `/clustering/behavior`, and `/events/roi`. `GET /config/calibration/image` uses `view=plan-view` and the fixture **place** (not Camera `sensorId`). It skips other mutating methods, path-parameter GETs, IDs we do not have (`segmentInfo`, `endFrameId`, `behaviorTimeInterval`, `referenceId`), and dump-incompatible GETs: `/metrics/average-speed-with-travel-time` (needs `city/corridor`), `/tracker/behavior-locations` (needs `behaviorIds` or `globalId`), `/frames/pts` (nvstreamer only), `/config/road-network`, `/config/usd-assets`. Routes that list both `sensorId` and `place` get **one** of them: Camera `sensorId` when both are optional (same as Jenkins #69); `place` when the spec requires it or the fixture is place-keyed. Sending both 400s (`oneOf`).

Each timed URL records `query.params`, `query.query_window_s`, and `query.maxResultSize` (OpenAPI default, usually 25; 100 for `/frames/high-confidence-objects`). The warehouse range is 60 s (`2026-02-14T10:16:00.000Z`–`10:17:00.000Z`). `/metrics/flowrate` has no from/to range; it sends `timeWindowSizeInSec=5`. `/clustering/behavior` sends `maxClusterSampleSize=100` instead of `maxResultSize`.

`perf/configs/kpis.yaml` is the regression gate. Each required GET must return enough HTTP 200s; P0 POSTs count HTTP 201. The original five use Jenkins `benchmark-perf-test/69` estimates. New GET endpoints use a default 100 / 200 ms p50/p99 until an owner publishes SLOs. P0 POSTs use 500 / 1000 ms.

| Endpoint | max p50 | max p99 |
| --- | ---: | ---: |
| `/livez` | 5 ms | 20 ms |
| `/frames` | 50 ms | 100 ms |
| `/behavior`, `/events/tripwire`, `/incidents` | 40 ms | 80 ms |
| Other spec GETs (default) | 100 ms | 200 ms |
| P0 calibration POSTs | 500 ms | 1000 ms |

Playback does not use a GPU. The Jenkins node label matches the shared pool.

## Run

Set `ARTIFACT_REF` to the promoted image, then run:

```bash
./perf/run_benchmark.sh
```

The result is written to `perf/results/video-analytics-api_<scenario>_<config-id>/result.json`. Jenkins already passes `--upload` and MinIO keys; the writer PUTs a timestamped copy to `s3://perf-results-dev/video-analytics-api/...` (`10.111.52.248:9000`, same endpoint as compose-base). The dashboard still has to onboard this service name.

## Jenkins

The shared job requires `{checkout}/perf/{configs,scripts,benchmark/core}` and archives `{checkout}/perf/results/...`. A trampoline at the VSS repo root `perf/` exposes that layout and delegates to this package (`SCM_FOLDER` empty, `PERF_FOLDER=perf`, `PERF_SERVICE=va-api`). Root `perf/configs/*.yaml` are symlinks to this package so Jenkins and the service tree share one copy.

GPU nodes sparse-checkout `perf/`, `services/`, `ci/`, `compose/`, and `VERSION` — not `deploy/`. `common.sh` therefore uses `deploy/docker` when it is present and `compose/docker` (an infra mirror) otherwise. The mirror ships empty `agent.env` / `alert.env` / `vst.env` so Compose can parse the unused HAProxy include; `write_compose_env` also creates those files if they are missing.

`deploy.sh` pulls the promoted API image, starts Elasticsearch alone (bind-mounted data/logs under `/tmp/va-api-perf-*`, mmap disabled), waits until `:19200` answers, then runs the init container and the API. Unique container names (`va-api-perf-*`) and host ports (19200 / 18081) keep a Behavior Analytics playback job on the same node from colliding. `post_deploy.sh` calls the integration-test dump loader (`load_elasticsearch_data_dump.js`, via host `node` or the API image) and waits for `/livez`. `--build` is required for the local Elasticsearch image.

`perf-configs.yaml` sets `composePath` so Jenkins exports a non-empty `COMPOSE_FILE`. Dummy `vlmGpus` / `llmGpus` values keep the LVS wrapper from dying on `set -u`.
