# Deploy Reference: VS (Video Summarization)

Deployment-time runbook for the `lvs-server` service. For the integration contract (API schema,
Kafka topics, peer services, env-var semantics) see `integrate-lvs.md`. For full profile deployment
use `vss-deploy-profile`; this file is the VS-specific service deploy reference.

## Image

| Item | Value |
|---|---|
| Service key | `lvs-server` |
| Container name | `vss-lvs` |
| Image | `${CONTAINER_IMAGE:-nvcr.io/nvidia/vss-core/vss-video-summarization:3.2.0}` (repo `${LVS_IMAGE}` + tag `${LVS_TAG}`) |
| Compose file | `deploy/docker/services/video-summarization/compose.yml` |
| Upstream profile | `bp_developer_lvs_2d` |
| Registry | `nvcr.io` — requires NGC login (`NGC_CLI_API_KEY`) |

DGX-SPARK / SBSA uses the `3.2.0-sbsa` tag; x86 and Jetson-Tegra use `3.2.0`.

## GPU

**VS is CPU-only.** The `lvs-server` container performs CA-RAG orchestration, ES reads/writes, and
Kafka I/O on CPU; it does not run a model itself. It does NOT request a GPU in compose (no
`deploy.resources.reservations.devices`), and the `GPU_DEVICES` / `NVIDIA_VISIBLE_DEVICES` vars in
the VS `.env` are vestigial defaults that the service compose does not consume.

GPU is consumed instead by the **services VS depends on**:

- **RT-VLM** — VLM serving for caption generation (1 GPU; cosmos-reason2 in-process).
- **Summarization LLM** — if served by a **local** LLM NIM, that NIM needs a GPU (the dev-profile-lvs
  `local_shared` default co-locates the LLM NIM with the VLM on a single GPU; `local` uses a
  dedicated GPU). If served **remotely** (NVIDIA-hosted, `NVIDIA_API_KEY`), no local GPU is used for
  the LLM.

So adding VS to a dense-captioning baseline adds **zero** GPU pressure if the summarization LLM is
remote, and one shared/dedicated GPU's worth if the LLM is local.

## CPU + Memory

No explicit CPU/memory reservations in the compose. In practice the container is lightweight
(orchestration + HTTP). Memory scales with batch size (`max_events_per_batch: 50` in `config.yaml`)
and concurrent summarize requests. The service rejects concurrent file-summarize requests with `503`
(one file at a time).

## Storage

| Mount | Container path | Purpose |
|---|---|---|
| `…/services/video-summarization/configs/config.yaml` | `/app/config.yaml:ro` | CA-RAG config (read-only) |
| `${MODEL_ROOT_DIR:-/tmp/model_cache}` | same path | Model cache (only used if VS pulls a local embedding model; inert for `elasticsearch_db` + remote LLM) |

No named Docker volumes are required for the default `elasticsearch_db` backend. Structured summaries
persist in Elasticsearch (collection `lvs-events`), whose data lives in the shared ELK volumes /
host bind-mounts — VS adds no new persistence surface.

## Startup Behavior

- Healthcheck: `curl -f http://localhost:${BACKEND_PORT:-38111}/v1/ready`, `interval 30s`,
  `timeout 10s`, `retries 10`, `start_period 120s`. `restart: always`.
- `/v1/ready` returns 503 while warming or when a dependency (LLM / RT-VLM / ES) is unreachable, 200
  when ready. Check HTTP status only — the body may be empty on 200.
- On boot VS loads `config.yaml`, connects to ES, registers the `summarization` +
  `summarization_online` CA-RAG functions, and (when `KAFKA_ENABLED=true`) opens a producer to
  `mdx-structured-events-summary`. It does not download a VLM/LLM (those are served by peers).
- Typical warm readiness: well under the 120 s `start_period` once ES, RT-VLM, and the LLM endpoint
  are reachable. If the LLM endpoint is unreachable, readiness can stall — verify the LLM first.

## Known Deployment Issues

| Symptom | Cause | Fix |
|---|---|---|
| `/v1/ready` stuck at 503 | Summarization LLM endpoint unreachable, or ES/RT-VLM not up | Verify `curl ${LVS_LLM_BASE_URL%/v1}/v1/models` (local NIM) or remote endpoint reachability; verify ES `:9200/_cluster/health` and RT-VLM `:8018/v1/health/ready` |
| Summaries never written to ES `lvs-events` | `KAFKA_BOOTSTRAP_SERVERS=kafka:9092` left at compose default under host networking | Set `KAFKA_BOOTSTRAP_SERVERS=${HOST_IP}:9092` |
| `POST /aggregate_live_stream` raises KeyError | `summarization_online` not registered | Ensure `config.yaml context_manager.functions` lists `summarization_online` (it does in the shipped config) |
| `422` on `/v1/summarize` | Missing required `model`/`scenario`/`events` or extra fields | Supply all three; do not add non-spec fields (`additionalProperties: false`) |
| `BadParameters: No such model …` | Friendly model name passed instead of runtime id | Resolve `VLM_NAME` from RT-VLM `/v1/models` (`nim_nvidia_cosmos-reason2-8b_hf-1208`) |
| `503` on `/v1/summarize` | Service busy processing another file | Serialize file-summarize requests |
| Empty `video_summary` | Clip lacks the requested events | Re-run with broader `scenario`/`events` |

## Prerequisites

- Docker Engine + Compose v2; NGC login for `nvcr.io`.
- A running ELK stack (Elasticsearch + Kafka + Logstash `mdx-lvs` pipeline) — supplied by the
  baseline this VS layers on.
- A running RT-VLM endpoint on `:8018`.
- A reachable summarization LLM endpoint: a local LLM NIM (GPU + `NGC_CLI_API_KEY`) OR a remote
  NVIDIA-hosted endpoint (`LLM_BASE_URL=https://integrate.api.nvidia.com/v1` + `NVIDIA_API_KEY`).
- `.env` values: `HOST_IP`, `VSS_APPS_DIR`, `VSS_DATA_DIR`, `ES_HOST`/`ES_PORT`,
  `LVS_LLM_MODEL_NAME`, `LVS_LLM_BASE_URL` (or `LLM_BASE_URL`), `KAFKA_ENABLED=true`,
  `KAFKA_BOOTSTRAP_SERVERS=${HOST_IP}:9092`, `RTVI_VLM_URL=http://${HOST_IP}:8018`.

## Verify Deployment

```bash
# Container up
docker ps --filter name=vss-lvs --format '{{.Names}} {{.Status}}'

# Readiness (status code only)
curl -sf -o /dev/null -w '%{http_code}\n' http://${HOST_IP}:38111/v1/ready    # expect 200

# Models advertised
curl -sf http://${HOST_IP}:38111/models | jq '.data[].id'

# Structured-summary topic exists and advances after a summarize
docker exec kafka kafka-get-offsets --bootstrap-server localhost:9092 \
  --topic mdx-structured-events-summary

# ES collection receives summary docs
curl -sf 'http://${HOST_IP}:9200/lvs-events/_count' | jq '.count'            # expect > 0 after a summarize

# Logs
docker logs --tail 100 vss-lvs
```

## Tear Down

VS adds no named volumes, so tearing it down is non-destructive to model caches. As part of the
combined build it is removed with the rest of the stack:

```bash
docker compose -p mdx --env-file <BUILD_DIR>/.env -f <BUILD_DIR>/compose.yml \
  --profile <invented-flag> down
```

Structured summaries in the ES `lvs-events` collection persist in the shared ELK storage; a full
`down -v` (or wiping the ELK host bind-mounts) clears them along with the caption indices. To drop
only the VS summaries without touching captions:
`curl -X DELETE 'http://${HOST_IP}:9200/lvs-events'`.

---

*Sources: `deploy/docker/services/video-summarization/compose.yml` (image, healthcheck, mounts,
no-GPU), `deploy/docker/services/video-summarization/.env`,
`deploy/docker/developer-profiles/dev-profile-lvs/.env` (LLM/VLM modes, image tags),
`deploy/docker/services/video-summarization/configs/config.yaml` (CA-RAG functions, collection
name), `skills/vss-summarize-video/references/video-summarization-deployment.md` and
`…-api.md` (verify recipes, error table). Upstream RST `performance-lvs.rst` /
`long-video-summarization.rst` identified as authority but not readable this session — cross-check
GPU/sizing claims against them in a follow-up.*
