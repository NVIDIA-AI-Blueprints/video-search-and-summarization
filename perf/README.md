# Warehouse industry-example BA / VA-API performance

This package deploys the **warehouse operations industry example** from
`deploy/docker/industry-profiles/warehouse-operations/` and measures **Behavior
Analytics** and **Video Analytics API** inside that stack.

It is not the playback-only BA job and not the dump-backed VA-API job. Those
stay on their own pull requests. This workflow starts the product 2D Kafka
blueprint (`MODE=2d`, `BP_PROFILE=bp_wh_kafka`,
`COMPOSE_PROFILES_WH_KAFKA_2D`): DeepStream / RT-CV, VST, NvStreamer, Kafka,
Behavior Analytics, Elasticsearch, Logstash, and Video Analytics API.

## What is measured

1. **Behavior Analytics** — Yun He's `latency_monitor.py` from the BA image,
   attached to the warehouse Kafka network (`kafka:29092`). Topic-minus-raw
   correction on the last Statistics Report, same as the playback job.
2. **Video Analytics API** — HTTP GETs against the live API after the industry
   example has ingested frames. The query window comes from
   `/metrics/last-processed-timestamp`, not the frozen dump timestamps.

`perf/configs/kpis.yaml` gates both. BA p50/p99 numbers are Yun's playback
table applied to **corrected** live latency; counts are lowered for a 60 s
window. API p50/p99 numbers are the dump-job estimates until an owner publishes
live-stack SLOs.

## Run

Needs a GPU host, Docker Compose v2, NGC CLI, and
`NGC_CLI_API_KEY` (warehouse app data + RT-DETR model download).

```bash
export NGC_CLI_API_KEY="<ngc-key>"
# Optional NGC image pin (Jenkins GPU cannot pull GHCR by default):
export VSS_CONTAINER_REGISTRY=nvcr.io/nvstaging/vss-core
export VSS_CONTAINER_TAG=develop-latest

./perf/run_benchmark.sh
```

The result is `perf/results/warehouse-bp_<scenario>_<config-id>/result.json`.
Jenkins already passes `--upload`; the writer PUTs a timestamped copy to
`s3://perf-results-dev/warehouse-bp/...` (`10.111.52.248:9000`).

## Jenkins

Shared job `benchmark-perf-test` with `PERF_SERVICE=warehouse-bp`,
`PERF_FOLDER=perf`, `SCM_FOLDER` empty, `ARTIFACT_TYPE=compose` on
`vss-blueprints-compose-warehouse`. Unique service name so GPU `deleteDir`
does not reuse the BA playback or VA-API dump checkouts.

GPU sparse-checkout must include `deploy/` (`checkoutPaths` in
`perf-configs.yaml`) so warehouse-operations compose files are present.
Host ports are overlaid away from 9200/8081/9092 so a leftover playback
or dump job on the node does not bind-conflict.

`COMPOSE_PROJECT_NAME=warehouse-bp-perf`. Elastic/Kafka bind mounts live under
`/tmp/warehouse-bp-perf-*` so uid-1000 Lucene files do not block `deleteDir`.
The extracted warehouse app data and model cache are kept across teardowns on
the same node.

## Not in this package

- 3D / MV3DT variants (same industry-example tree; add configs later)
- Schema v2.0 dashboard envelope
- Mixing this tree into GitHub #2704 / #2795 or GitLab !3113 / !3142
