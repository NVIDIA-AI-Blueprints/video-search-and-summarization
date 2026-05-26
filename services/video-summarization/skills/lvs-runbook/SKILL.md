---
name: lvs-runbook
description: >
  Deploy, operate, debug, and tear down the LVS blueprint stack
  (image: nvcr.io/nv-metropolis-dev/vss-core/vss-video-summarization:3.2.0-rc3-d9c0e5f).
  Use this skill when asked to deploy LVS, bring up LVS, start LVS, restart
  LVS, recreate LVS, tear down LVS, stop LVS, check LVS logs, diagnose LVS
  failures, troubleshoot LVS crashing or healthcheck failing, swap the LVS
  model, swap the LVS database backend, or run a dry-run on the LVS compose
  stack. Also use it when the user references the lvs-rtvi-cr2-gptoss-20b
  blueprint, RT-VLM Cosmos-Reason2, GPT-OSS-20B NIM, or this image string.
argument-hint: <compose-path optional; defaults to the generated rtx-pro-6000 compose>
allowed-tools: Bash(docker *) Bash(docker compose *) Bash(curl *) Bash(jq *) Bash(mkdir *) Bash(chmod *) Read Write Glob
---

# LVS Blueprint Runbook

## Overview

**Service**: `lvs-rtvi-cr2-gptoss-20b`  
**Default compose**: `$VSS_REPO/deploy/blueprints/lvs/rtvi-cr2-gptoss-20b/rtx-pro-6000/docker-compose.yml`  
**Primary ports**: `38111` REST API, `38112` MCP  
**Hardware target**: RTX PRO 6000 class host; this compose pins RT-VLM to GPU `0` and GPT-OSS-20B NIM to GPU `1`.

LVS orchestrator delegating VLM inference to RT-VLM (Cosmos-Reason2-8B integrated), with GPT-OSS-20B NIM for CA-RAG summarization, Elasticsearch as the default storage backend, alternate DB backends (Milvus / Neo4j / Arango) pre-wired but default-off, and a Kafka -> Logstash -> Elasticsearch event pipeline for LVS structured events. The generated runbook deploys the compose directly; Blueprint Builder is not needed at runtime.

Services in the default stack:

| Service | Role |
|---|---|
| `lvs` | FastAPI REST server plus MCP server; delegates captioning and summarization. |
| `rt-vlm` | Real-Time VLM service using `nvcr.io/nv-metropolis-dev/rtvi/vss-rt-vlm:3.2.0-26.04.2`, `cosmos-reason2`, and `git:https://huggingface.co/nvidia/Cosmos-Reason2-8B`. |
| `gpt-oss-20b` | OpenAI-compatible NIM used by CA-RAG summarization. |
| `elasticsearch` | Default CA-RAG storage backend and Logstash output. |
| `kafka` | Broker for RT-VLM raw events and LVS structured summary events. |
| `logstash-lvs-kafka` | Consumes `nv.VisionLLM` protobuf messages from Kafka and indexes them into Elasticsearch in the LVS CA-RAG document shape. |
| `milvus-standalone`, `graph-db`, `arango-db` | Optional DB backends gated by compose profiles. |

## Related Skills

Use the `lvs-api` skill for calling the LVS API once this stack is running.

## Prerequisites

- Docker Engine 20.10+ and the Compose plugin: `docker compose version`.
- NVIDIA Container Toolkit installed and verified.
- Two visible GPUs if using the compose as generated. It pins `rt-vlm` to GPU `0` and `gpt-oss-20b` to GPU `1`.
- Free host ports `38111` and `38112`.
- Outbound access to `nvcr.io`, `huggingface.co`, `docker.elastic.co`, Docker Hub, and Confluent images.
- `jq` for the health wait snippets.
- Disk budget: assume 100+ GB for images, NIM cache, HuggingFace cache, and generated video assets. First boot can spend 15-45 minutes pulling model weights.

> **(scaffolded by microservice-runbook-generator)** - NVIDIA Container Toolkit, NGC auth, HF cache, Kafka notes, and model disk budgeting are inferred from `nvcr.io` images, GPU settings, `HF_TOKEN`, HuggingFace model paths, and Kafka services. Audit before following in a different deployment.

Verify GPU access:

```bash
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
```

## NGC / Registry Preflight

This stack pulls from `nvcr.io` and runs a NIM. Use an NGC key with access to every namespace in the compose.

```bash
export NGC_API_KEY="nvapi-..."
docker login nvcr.io -u '$oauthtoken' -p "$NGC_API_KEY"

docker pull nvcr.io/nv-metropolis-dev/vss-core/vss-video-summarization:3.2.0-rc3-d9c0e5f
docker pull nvcr.io/nv-metropolis-dev/rtvi/vss-rt-vlm:3.2.0-26.04.2
docker pull nvcr.io/nim/openai/gpt-oss-20b:1
```

Get an NGC key from <https://ngc.nvidia.com/setup/api-key>. If any pull returns unauthorized, verify the key's org/team membership for the exact namespace before starting the stack.

## Required Secrets & Credentials

| Env var | Required | Purpose | Where to get / notes |
|---|---:|---|---|
| `NGC_API_KEY` | yes | Pulls NGC images and authorizes the GPT-OSS-20B NIM. | NGC API key. Compose uses it without a fallback for `gpt-oss-20b`. |
| `NVIDIA_API_KEY` | yes for default CA-RAG config | Passed into `summarization_llm.api_key` and NVIDIA-hosted NIM clients. | NVIDIA API key. If your local NIM endpoint ignores auth, document that exception in `.env`. |
| `HF_TOKEN` | usually | HuggingFace token for gated model downloads, including NVIDIA model repos when gated. | <https://huggingface.co/settings/tokens>, read scope. |
| `OPENAI_API_KEY` | no | Only needed for OpenAI-compatible VLM/LLM swaps. | OpenAI or compatible provider. |
| `AZURE_OPENAI_API_KEY` | no | Only needed for Azure OpenAI swaps. | Azure OpenAI. |
| `GRAPH_DB_PASSWORD`, `NEO4J_AUTH` | profile-dependent | Neo4j credentials when enabling `db-neo4j`. | Override the weak defaults before enabling the profile. |
| `ARANGO_ROOT_PASSWORD`, `ARANGO_DB_PASSWORD` | profile-dependent | Arango credentials when enabling `db-arango`. | Override the weak defaults before enabling the profile. |
| `RIVA_ASR_SERVER_API_KEY` | feature-dependent | ASR auth when enabling audio with Riva ASR. | Riva/NIM endpoint owner. |

Kubernetes secret refs from the blueprint: `ngc-api-key/NGC_API_KEY`, `nvidia-api-key/NVIDIA_API_KEY`, `hf-token/HF_TOKEN`, `openai/OPENAI_API_KEY`, `graphdb-credentials`, and `arangodb-credentials`. They are blueprint provenance only; compose deployment uses `.env`.

## Required Volume Mounts

The compose creates these named volumes automatically. For the GPT-OSS-20B NIM,
prefer setting `LOCAL_NIM_CACHE` to an absolute host path before first boot so
model artifacts survive `docker compose down -v`, are inspectable, and can be
reused by another compose project on the same host.

```bash
export LOCAL_NIM_CACHE="${LOCAL_NIM_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/nim}"
mkdir -p "$LOCAL_NIM_CACHE" /tmp/rtvi-assets
[ -O "$LOCAL_NIM_CACHE" ] && chmod 755 "$LOCAL_NIM_CACHE"
test -w "$LOCAL_NIM_CACHE"

# RT-VLM runs as UID/GID 1001 in Docker Compose (image USER vss:1001).
# This bind mount must be writable by that container user.
sudo chown -R 1001:1001 /tmp/rtvi-assets
sudo chmod -R u+rwX,g+rwX /tmp/rtvi-assets
```

| Volume | Mounted at | Purpose | Survives `down` | Survives `down -v` |
|---|---|---|---:|---:|
| `via-ngc-model-cache` | `/root/.via/ngc_model_cache` | LVS NGC model cache. | yes | no |
| `via-hf-cache` | `/tmp/huggingface` | LVS HuggingFace cache. | yes | no |
| `rt-vlm-hf-cache` | `/tmp/huggingface` | RT-VLM HuggingFace cache. | yes | no |
| `rtvi-ngc-model-cache` or `${NGC_MODEL_CACHE}` | `/opt/nvidia/rtvi/.rtvi/ngc_model_cache` | RT-VLM NGC cache. | yes | no |
| `nim-cache` or `${LOCAL_NIM_CACHE}` | `/opt/nim/.cache` | GPT-OSS-20B NIM cache. Use `${LOCAL_NIM_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/nim}` for a host-backed cache. | yes | yes if host path, no if named volume |
| `kafka-data` | `/tmp/kafka-data` | Kafka KRaft data and persisted cluster ID. | yes | no |

Optional host bind mounts are activated only when their env vars are set:

```bash
mkdir -p /data/lvs/assets /data/lvs/example-streams /data/lvs/logs /data/lvs/rtvi-assets /data/lvs/rtvi-logs
chmod 755 /data/lvs/assets /data/lvs/example-streams /data/lvs/logs /data/lvs/rtvi-assets /data/lvs/rtvi-logs
```

| Env var | Container target | Notes |
|---|---|---|
| `ASSET_STORAGE_DIR` | `/tmp/assets` | Input/output assets for LVS. |
| `EXAMPLE_STREAMS_DIR` | `/opt/nvidia/via/streams:ro` | Read-only example streams. |
| `VIA_SRC_DIR` | `/opt/nvidia/via:ro` | Source override for development only. |
| `VIA_LOG_DIR` | `/tmp/via-logs` | LVS logs. |
| `RTVI_ASSET_DIR` | `/tmp/assets` | RT-VLM assets; default is `/tmp/rtvi-assets` as a Docker volume source. |
| `RTVI_LOG_DIR` | `/tmp/rt-vlm-logs` | RT-VLM logs. |

Writable bind mounts must be prepared for the UID/GID used inside the
container, not just the host user. For this compose, RT-VLM's Dockerfile sets
`USER vss:1001`, so `RTVI_ASSET_DIR` should be owned or ACL-writable by
`1001:1001`. The generated Helm chart has `runAsUser: 1000`, but Docker
Compose does not use that Helm setting.

The config bind mounts under `configmaps/` already exist beside the compose file. Edit them there, then run `docker compose up -d --force-recreate <service>`.

## Required Environment Variables

Create `.env` in the compose directory from this skill's `.env.example`. Required or operator-controlled values:

| Var | Required | Default | Provenance | Notes |
|---|---:|---|---|---|
| `NGC_API_KEY` | yes | none | from-compose, from-blueprint secret | Required for NGC image/model access. |
| `NVIDIA_API_KEY` | yes | empty / `NOAPIKEYSET` in RT-VLM | from-compose, from-blueprint secret, docs | CA-RAG config passes it to the summarization LLM client. |
| `HF_TOKEN` | usually | empty | from-compose, from-blueprint secret | Needed for gated HuggingFace repos. |
| `LVS_DATABASE_BACKEND` | no | `elasticsearch_db` | from-connection:lvs.elasticsearch | Set to `vector_db`, `graph_db`, or `graph_db_arango` only when enabling the matching profile and env vars. |
| `BACKEND_PORT` | no | `38111` | from-compose, docs | REST API port. |
| `LVS_MCP_PORT` | no | `38112` | from-compose, docs | MCP server port. |
| `RTVI_VLM_MODEL_TO_USE` | no | `cosmos-reason2` | from-compose, blueprint | RT-VLM backend selector. |
| `RTVI_VLM_MODEL_PATH` | no | `git:https://huggingface.co/nvidia/Cosmos-Reason2-8B` | from-compose, blueprint | Default model source. |
| `LVS_LLM_MODEL_NAME` | no | `openai/gpt-oss-20b` | from-connection:lvs.summarization-llm | Must match the local GPT-OSS-20B NIM unless using an external LLM. |
| `LVS_KAFKA_ENABLED` | no | `true` | from-connection:lvs.kafka | Enables LVS publishing of structured events and aggregate summaries to Kafka. |
| `KAFKA_STRUCTURED_SUMMARY_TOPIC` | no | `mdx-structured-events-summary` | from-service:lvs | LVS output topic for `structured_events` and `aggregated_summary` as `nv.VisionLLM`. |
| `KAFKA_TOPIC` | no | `mdx-vlm-captions` | from-connection:rt-vlm.kafka | RT-VLM raw event topic consumed by Logstash. |
| `LVS_DISABLE_DB_RESET_ON_REQUEST_DONE` | no | `true` | from-service:lvs | Keeps Kafka/Logstash-populated ES collections available across sequential requests. |

Connection-provided env vars such as `RTVI_VLM_HOST`, `RTVI_VLM_PORT`, `RTVI_VLM_URL`, `LVS_LLM_HOST`, `LVS_LLM_PORT`, `LVS_LLM_BASE_URL`, `ES_HOST`, `ES_PORT`, and `KAFKA_BOOTSTRAP_SERVERS` are set by the generated compose. Do not override them unless deliberately pointing to external services.

See `references/environment-variables.md` for the broader optional env matrix.

## Optional / Feature Flags

Common feature flags:

| Env var | Default | Effect |
|---|---|---|
| `LVS_ENABLE_MCP` | `true` | Starts the MCP server on `38112`. |
| `ENABLE_AUDIO` | `false` | Enables audio processing path. |
| `ENABLE_DENSE_CAPTION` | empty / `false` | Enables dense captioning. |
| `LVS_EMB_ENABLE` | `false` | Required for graph DB backends that use embeddings. |
| `FORCE_CA_RAG_RESET` | empty | Reset CA-RAG state at startup. |
| `VSS_LOG_LEVEL` | `DEBUG` | LVS logging verbosity. |
| `VIA_ENABLE_OTEL`, `VIA_CTX_RAG_ENABLE_OTEL` | `false` | OpenTelemetry toggles. |
| `RTVI_KAFKA_ENABLED` | `true` | Controls RT-VLM publishing to `mdx-vlm-captions`. |
| `LOGSTASH_API_PORT` | `9600` | Logstash monitoring API port; keep aligned with the service/probe port. |
| `KAFKA_CONSUMER_GROUP` | `logstash-vlm-es-writer` | Consumer group used by Logstash. |
| `LOGSTASH_CODEC_PROTOBUF_VERSION` | `1.3.0` | `logstash-codec-protobuf` plugin version installed on first Logstash boot. |
| `LVS_EMB_DIMENSIONS` | `1024` | Vector dimension used by the Logstash deterministic null-embedding writer. |
| `LVS_EMB_SEED` | `42` | Seed used for deterministic Logstash null embeddings. |

## GPU Selection & Hardware

Generated GPU placement:

| Service | GPU |
|---|---|
| `rt-vlm` | `device_ids: ["0"]`, `runtime: nvidia`, `NVIDIA_VISIBLE_DEVICES=${NVIDIA_VISIBLE_DEVICES:-all}` |
| `gpt-oss-20b` | `device_ids: ["1"]`, `runtime: nvidia`, `shm_size: 16g` |

To change placement, edit the compose `deploy.resources.reservations.devices.device_ids` entries. If sharing a single GPU, lower `VLLM_GPU_MEMORY_UTILIZATION`, `NIM_GPU_MEM_FRACTION`, and `NIM_KVCACHE_PERCENT`, then watch `nvidia-smi` during first boot.

```yaml
deploy:
  resources:
    reservations:
      devices:
        - driver: nvidia
          device_ids: ["0"]
          capabilities: [gpu]
```

## Port Conflict Map

| Service | Container port | Host port behavior | Notes |
|---|---:|---|---|
| `lvs` REST | 38111 | fixed `38111` | Main health/API port. |
| `lvs` MCP | 38112 | fixed `38112` | Only useful when `LVS_ENABLE_MCP=true`. |
| `rt-vlm` | 8000 | random host port | Use `docker compose port rt-vlm 8000` if host access is needed. |
| `gpt-oss-20b` | 9233 | random host port | Internal URL is `http://gpt-oss-20b:9233/v1`. |
| `elasticsearch` | 9200, 9300 | random host ports | Internal URL is `http://elasticsearch:9200`. |
| `kafka` | 9092 | random host port | Internal bootstrap is `kafka:9092`. |
| `logstash-lvs-kafka` | 9600 | random host port | Logstash monitoring API. |
| `milvus-standalone` | 19530, 9091 | random host ports, profile only | Enable with `db-milvus`. |
| `graph-db` | 7474, 7687 | random host ports, profile only | Enable with `db-neo4j`. |
| `arango-db` | 8529 | random host port, profile only | Enable with `db-arango`. |

Remap fixed LVS ports if needed:

```yaml
ports:
  - "38121:38111"
  - "38122:38112"
```

## Models Used & Swap Guide

| Component | Default | Source | Controlled by |
|---|---|---|---|
| RT-VLM | `cosmos-reason2` / `nvidia/Cosmos-Reason2-8B` | HuggingFace git URL | `RTVI_VLM_MODEL_TO_USE`, `RTVI_VLM_MODEL_PATH` |
| Summarization LLM | `openai/gpt-oss-20b` | Local NIM `nvcr.io/nim/openai/gpt-oss-20b:1` | `LVS_LLM_MODEL_NAME`, `LVS_LLM_BASE_URL` |
| Embedding | disabled | External OpenAI-compatible embedding NIM | `LVS_EMB_ENABLE`, `LVS_EMB_BASE_URL`, `LVS_EMB_MODEL_NAME` |

Swap RT-VLM to another HuggingFace model:

```bash
cat >> .env <<'EOF'
RTVI_VLM_MODEL_TO_USE=vllm-compatible
RTVI_VLM_MODEL_PATH=git:https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct
HF_TOKEN=<your-token-if-gated>
EOF
docker compose -f "$COMPOSE_FILE" up -d --force-recreate rt-vlm lvs
```

Point LVS at an external OpenAI-compatible LLM:

```bash
cat >> .env <<'EOF'
LVS_LLM_HOST=<external-host>
LVS_LLM_PORT=8000
LVS_LLM_BASE_URL=http://<external-host>:8000/v1
LVS_LLM_MODEL_NAME=<model-id>
NVIDIA_API_KEY=<api-key-or-provider-token>
EOF
docker compose -f "$COMPOSE_FILE" up -d --force-recreate lvs
```

## Deployment Topology

Connections from the blueprint:

| Connection | Env keys | Notes |
|---|---|---|
| `lvs.vlm -> rt-vlm` | `RTVI_VLM_HOST`, `RTVI_VLM_PORT`, `RTVI_VLM_URL` | Functional; LVS delegates video decode and VLM inference. |
| `lvs.summarization-llm -> gpt-oss-20b` | `LVS_LLM_HOST`, `LVS_LLM_PORT`, `LVS_LLM_BASE_URL`, `LVS_LLM_ENABLE`, `LVS_LLM_MODEL_NAME` | Functional CA-RAG summarization. |
| `lvs.elasticsearch -> elasticsearch` | `ES_HOST`, `ES_PORT`, `LVS_DATABASE_BACKEND` | Default storage backend. |
| `lvs.kafka -> kafka` | `KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_ENABLED`, `KAFKA_STRUCTURED_SUMMARY_TOPIC` | LVS publishes `structured_events` and `aggregated_summary` to `mdx-structured-events-summary`. |
| `rt-vlm.kafka -> kafka` | `KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_ENABLED`, `KAFKA_TOPIC` | RT-VLM publishes raw events to `mdx-vlm-captions`. |
| `logstash-lvs-kafka.kafka -> kafka` | `KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_CONSUMER_GROUP` | Consumes `mdx-vlm-captions` and `mdx-structured-events-summary` as `nv.VisionLLM`. |
| `logstash-lvs-kafka.elasticsearch -> elasticsearch` | `ES_HOST`, `ES_PORT` | Indexes consumed events into `default_<asset_or_stream_id>` CA-RAG indices. |
| `logstash-lvs-kafka.lvs -> lvs` | none | Dependency only: Logstash waits for `/v1/ready` so LVS/ctx-rag has registered the `visionllm` Elasticsearch index template before writes. |

Profiles:

| Profile | Service | When to use |
|---|---|---|
| `db-milvus` | `milvus-standalone` | Swap LVS storage to `vector_db`. |
| `db-neo4j` | `graph-db` | Swap LVS storage to `graph_db`; also requires embedding configuration. |
| `db-arango` | `arango-db` | Swap LVS storage to `graph_db_arango`; also requires embedding configuration. |

Run every compose command with the relevant `--profile` when enabling a profile.

## Deploy

```bash
export VSS_REPO=<path-to-nvidia-vss-repo>
export COMPOSE_DIR="$VSS_REPO/deploy/blueprints/lvs/rtvi-cr2-gptoss-20b/rtx-pro-6000"
export COMPOSE_FILE="$COMPOSE_DIR/docker-compose.yml"

cp <skill-path>/.env.example "$COMPOSE_DIR/.env"
$EDITOR "$COMPOSE_DIR/.env"

export LOCAL_NIM_CACHE="${LOCAL_NIM_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/nim}"
mkdir -p "$LOCAL_NIM_CACHE" /tmp/rtvi-assets
[ -O "$LOCAL_NIM_CACHE" ] && chmod 755 "$LOCAL_NIM_CACHE"
test -w "$LOCAL_NIM_CACHE"
sudo chown -R 1001:1001 /tmp/rtvi-assets
sudo chmod -R u+rwX,g+rwX /tmp/rtvi-assets

docker login nvcr.io -u '$oauthtoken' -p "$NGC_API_KEY"
docker compose --env-file "$COMPOSE_DIR/.env" -f "$COMPOSE_FILE" pull
docker compose --env-file "$COMPOSE_DIR/.env" -f "$COMPOSE_FILE" up -d
```

First boot health can take up to 20 minutes because `rt-vlm` has a 1200s start period and `gpt-oss-20b` has a 1040s start period.

```bash
for svc in rt-vlm gpt-oss-20b lvs; do
  until docker compose --env-file "$COMPOSE_DIR/.env" -f "$COMPOSE_FILE" ps --format json "$svc" \
    | jq -e 'if type == "array" then .[0].Health else .Health end | . == "healthy"' >/dev/null; do
    echo "waiting for $svc..."
    sleep 10
  done
done

curl -f http://localhost:38111/v1/ready
curl -f http://localhost:38112/ || true
```

Deploy with Milvus:

```bash
cat >> "$COMPOSE_DIR/.env" <<'EOF'
LVS_DATABASE_BACKEND=vector_db
MILVUS_DB_HOST=milvus-standalone
MILVUS_DB_GRPC_PORT=19530
EOF
docker compose --profile db-milvus --env-file "$COMPOSE_DIR/.env" -f "$COMPOSE_FILE" up -d
```

Deploy with Neo4j or Arango only after configuring an embedding endpoint:

```bash
# Neo4j
LVS_DATABASE_BACKEND=graph_db
GRAPH_DB_HOST=graph-db
GRAPH_DB_BOLT_PORT=7687
LVS_EMB_ENABLE=true

# ArangoDB
LVS_DATABASE_BACKEND=graph_db_arango
ARANGO_DB_HOST=arango-db
ARANGO_DB_PORT=8529
LVS_EMB_ENABLE=true
```

## Dry Run

```bash
docker compose --env-file "$COMPOSE_DIR/.env" -f "$COMPOSE_FILE" config
docker compose --env-file "$COMPOSE_DIR/.env" -f "$COMPOSE_FILE" config --quiet && echo "compose is valid"
docker compose --env-file "$COMPOSE_DIR/.env" -f "$COMPOSE_FILE" up --no-start
docker compose --env-file "$COMPOSE_DIR/.env" -f "$COMPOSE_FILE" convert
```

Use `--profile db-milvus`, `--profile db-neo4j`, or `--profile db-arango` on every command when auditing an alternate backend.

## Verify Deployment

This blueprint uses structured CA-RAG summarization (`vlm_structured_summarization`).
For `/v1/summarize`, keep `enable_vlm_structured_output` unset or set to
`true`; do not set it to `false` just because the desired output is a prose
`video_summary`. RT-VLM must emit structured events before LVS can aggregate
them into a summary.

```bash
docker compose --env-file "$COMPOSE_DIR/.env" -f "$COMPOSE_FILE" ps
curl -f http://localhost:38111/v1/ready

curl --location 'http://localhost:38111/v1/summarize' \
  --header 'Content-Type: application/json' \
  --data '{
    "url": "http://<video-server-ip>:<port>/your-video.mp4",
    "model": "Cosmos-Reason2-8B",
    "scenario": "site safety monitoring",
    "events": ["person enters scene", "object interaction", "unsafe behavior"],
    "objects_of_interest": ["person", "vehicle", "equipment"],
    "prompt": "Return only valid JSON events with start_time, end_time, type, and description. Use approximate timestamps in seconds and describe only visible actions.",
    "enable_vlm_structured_output": true,
    "chunk_duration": 60,
    "chunk_overlap_duration": 5
  }'
```

Expected steady-state signs:

- `lvs` is healthy and `curl http://localhost:38111/v1/ready` succeeds.
- `rt-vlm` healthcheck passes on `/v1/live`; the wait sidecar also sees `/v1/ready`.
- `gpt-oss-20b` healthcheck passes on `/v1/health/live`; the wait sidecar also sees `/v1/health/ready`.
- Kafka has a persisted `cluster_id` in the `kafka-data` volume.
- Logstash starts after Kafka, Elasticsearch, and LVS readiness; its monitoring API answers on port `9600`.
- Kafka topics used by this path are `mdx-vlm-captions` and `mdx-structured-events-summary`.

## Logs & Status

```bash
docker compose --env-file "$COMPOSE_DIR/.env" -f "$COMPOSE_FILE" ps
docker compose --env-file "$COMPOSE_DIR/.env" -f "$COMPOSE_FILE" logs -f lvs
docker compose --env-file "$COMPOSE_DIR/.env" -f "$COMPOSE_FILE" logs --tail 200 --since 10m rt-vlm
docker compose --env-file "$COMPOSE_DIR/.env" -f "$COMPOSE_FILE" logs --tail 200 gpt-oss-20b
docker stats
```

Set `VSS_LOG_LEVEL=DEBUG` or `LOG_LEVEL=DEBUG` before recreating services for more verbose logs. If `VIA_LOG_DIR` or `RTVI_LOG_DIR` are set, logs persist at those host paths; otherwise they are in Docker volumes/container logs.

## Debugging Common Failures

| Symptom | Likely cause | Fix |
|---|---|---|
| `pull access denied` or `unauthorized` | NGC key lacks namespace access. | Re-login and verify pulls for all three `nvcr.io` images. |
| `gpt-oss-20b` exits or stays starting | Missing `NGC_API_KEY`, insufficient VRAM, slow NIM model download. | Fill `NGC_API_KEY`, keep GPU `1` free, wait up to 1040s before declaring startup stuck. |
| `rt-vlm` unhealthy for a long time | First model download/compile, missing HF token, insufficient VRAM. | Set `HF_TOKEN`, lower `VLLM_GPU_MEMORY_UTILIZATION`, wait up to 1200s. |
| `lvs` unhealthy | RT-VLM, LLM, Elasticsearch, or Kafka dependency not ready. | Check `wait-for-*` logs, then `lvs` logs. |
| `curl localhost:38111` fails | LVS not running or host port conflict. | Check `docker compose ps`; remap `38111:38111` if the port is taken. |
| GPU not detected | NVIDIA Container Toolkit not wired to Docker daemon. | Run the `nvidia-smi` Docker test from Prerequisites. |
| Kafka topics disappear after restart | `kafka-data` volume was removed. | Do not use `down -v` unless intentionally resetting Kafka. |
| Logstash consumes nothing | RT-VLM or LVS Kafka publishing is disabled, topics have no messages yet, or Logstash is blocked on dependency readiness. | Check `RTVI_KAFKA_ENABLED`, `LVS_KAFKA_ENABLED`, `KAFKA_TOPIC`, `KAFKA_STRUCTURED_SUMMARY_TOPIC`, and `docker compose logs logstash-lvs-kafka kafka lvs rt-vlm`. |
| Logstash exits during first boot | Protobuf codec plugin install failed or the plugin cache volume is corrupt. | Check network access to RubyGems from the container, then recreate `logstash-lvs-kafka`; remove only the `logstash-plugins` volume if the cache is bad. |
| Bad or empty summaries | Prompt/model mismatch, too little video context, or plain-text VLM output fed into structured CA-RAG. | Keep `enable_vlm_structured_output=true`, use explicit events and a JSON event prompt, increase frame/chunk settings, retry. |
| MCP not reachable | `LVS_ENABLE_MCP=false` or port `38112` blocked. | Set `LVS_ENABLE_MCP=true`, recreate `lvs`, verify port mapping. |

Docs troubleshooting also calls out container startup logs, external-service connectivity, OOM, port conflicts, GPU detection, malformed event summaries, and MCP connectivity.

## Upgrade & Rollback

For image upgrades, edit the image tag in the compose or regenerate the compose from the blueprint, then:

```bash
docker compose --env-file "$COMPOSE_DIR/.env" -f "$COMPOSE_FILE" pull
docker compose --env-file "$COMPOSE_DIR/.env" -f "$COMPOSE_FILE" up -d
```

Rollback by restoring the prior image tag and running `up -d --force-recreate`. Named volumes keep NIM, HF, Kafka, and model caches across container recreation. Changing model IDs can trigger a fresh multi-GB download even without `down -v`.

## Tear Down

```bash
# Stop containers; keep named volumes and cached model weights.
docker compose --env-file "$COMPOSE_DIR/.env" -f "$COMPOSE_FILE" down

# Stop and delete named volumes. This removes model caches and Kafka data.
docker compose --env-file "$COMPOSE_DIR/.env" -f "$COMPOSE_FILE" down -v

# Also remove local images created by compose if any exist.
docker compose --env-file "$COMPOSE_DIR/.env" -f "$COMPOSE_FILE" down --rmi local
```

Avoid `down -v` during routine restarts. It deletes `nim-cache`, `rt-vlm-hf-cache`, `via-hf-cache`, NGC caches, and Kafka metadata.

## Gotchas & Known Issues

- Most services set `container_name`. Running two copies of this compose on one host will conflict unless names are changed.
- Services under `db-milvus`, `db-neo4j`, and `db-arango` do not start with plain `docker compose up`; include the profile on every command.
- `rt-vlm` and `gpt-oss-20b` have long healthcheck start periods. Do not kill first boot before 20 minutes unless logs show a fatal error.
- `KAFKA_CLUSTER_ID` has no default in the compose, but `configmaps/kafka-entrypoint.sh` generates and persists one in `kafka-data` when unset.
- Default DB passwords (`passneo4j`, `passroot`) are weak. Override them before enabling Neo4j or ArangoDB.
- `LVS_KAFKA_ENABLED` and `RTVI_KAFKA_ENABLED` must stay enabled for the Kafka/Logstash path; disabling either creates partial data flow.
- Logstash uses `nv.VisionLLM` protobuf bindings from `configmaps/pb_definitions/nv_pb.rb`. Regenerate that Ruby binding if `src/protos/nv.proto` changes upstream.
- Logstash waits for LVS `/v1/ready` before indexing so the `visionllm` Elasticsearch index template exists. `/v1/live` is not enough for this dependency.
- The LVS summarize path uses structured CA-RAG. Setting `enable_vlm_structured_output=false` can produce `total_events: 0` and `video_summary: ""` even when RT-VLM inference succeeds.
- `ports: - "8000"` style mappings allocate random host ports. Use `docker compose port <service> <port>` for host access.
- The docs describe standalone LVS with external Elasticsearch and LLM services; this compose embeds Elasticsearch, GPT-OSS-20B, RT-VLM, Kafka, and Logstash.

## References

- NVIDIA LVS docs: <https://docs.nvidia.com/vss/latest/long-video-summarization.html>
- NVIDIA LVS API docs: <https://docs.nvidia.com/vss/latest/long-video-summarization-api.html>
- Compose: `$VSS_REPO/deploy/blueprints/lvs/rtvi-cr2-gptoss-20b/rtx-pro-6000/docker-compose.yml`
- Blueprint: `$VSS_REPO/deploy/blueprints/lvs/rtvi-cr2-gptoss-20b/blueprint.py`
- Config: `$VSS_REPO/deploy/blueprints/lvs/rtvi-cr2-gptoss-20b/rtx-pro-6000/configmaps/config.yaml`
