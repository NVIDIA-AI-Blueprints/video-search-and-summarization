# Video Summarization Deployment Reference

Use `vss-deploy-profile` for full deployment. This file is the video summarization-specific
service reference for the VSS 3.2.0 `lvs` profile.

## Current VSS Docker Compose Shape

Source files:

- `deploy/docker/developer-profiles/dev-profile-lvs/.env`
- `deploy/docker/services/video-summarization/compose.yml`
- `deploy/docker/services/video-summarization/configs/config.yaml`
- `deploy/docker/services/rtvi/rtvi-vlm/rtvi-vlm-docker-compose.yml`
- `deploy/docker/services/infra/compose.yml`

Key service signals in the current develop branch:

| Item | Value |
|---|---|
| Compose profile | `bp_developer_lvs_2d` |
| video summarization service | `lvs-server` |
| video summarization container | `vss-lvs` |
| video summarization image | `${LVS_IMAGE:-nvcr.io/nvstaging/vss-core/vss-video-summarization}:${LVS_TAG:-3.2.0-rc10-6f75390}` |
| REST API | `http://<HOST_IP>:38111` |
| Readiness | `GET /v1/ready` |
| MCP port | `38112`, disabled by default in the developer profile |
| RT-VLM | `http://<HOST_IP>:8018` |
| Kafka captions topic | `mdx-vlm-captions` |
| Kafka structured summary topic | `mdx-structured-events-summary` |

## Verify Running Service

```bash
curl -sf --max-time 15 "${LVS_BACKEND_URL:-http://localhost:38111}/v1/ready" >/dev/null
curl -sf --max-time 15 "${LVS_BACKEND_URL:-http://localhost:38111}/models" | jq '.data[0].id'
```

Non-destructive Docker checks:

```bash
docker ps --filter name=vss-lvs --format '{{.Names}} {{.Status}}'
docker logs --tail 100 vss-lvs
```

## Deploy Or Recreate

Prefer the profile deploy skill:

```text
/vss-deploy-profile -p lvs
```

If you are already operating the resolved Docker Compose stack, include the
profile that owns the video summarization service:

```bash
docker compose --profile bp_developer_lvs_2d ps lvs-server
docker compose --profile bp_developer_lvs_2d logs -f lvs-server
```

## Required Inputs

The VSS developer profile expects users to edit
`deploy/docker/developer-profiles/dev-profile-lvs/.env` rather than the service
compose directly.

Core required values:

| Var | Purpose |
|---|---|
| `VSS_APPS_DIR` | Absolute path to `deploy/docker`. |
| `VSS_DATA_DIR` | Data root for models, videos, and logs. |
| `HOST_IP` | Host-reachable IP used by services and clients. |
| `NGC_CLI_API_KEY` | Required for local image/model pulls. |
| `NVIDIA_API_KEY` or `OPENAI_API_KEY` | Required when selected remote endpoints enforce auth. |
| `LLM_MODE`, `VLM_MODE` | `local_shared`, `local`, or `remote`. |
| `LLM_NAME`, `LLM_NAME_SLUG` | LLM model and deployment slug. |
| `VLM_NAME` | Must match the id returned by RT-VLM `/v1/models`. |

Video summarization service values:

| Var | Default / Example | Purpose |
|---|---|---|
| `LVS_BACKEND_URL` | `http://${HOST_IP}:38111` | Agent-facing video summarization URL. |
| `LVS_IMAGE` | `nvcr.io/nvstaging/vss-core/vss-video-summarization` | video summarization image repository. |
| `LVS_TAG` | `3.2.0-rc10-6f75390` | video summarization image tag in current develop. |
| `LVS_ENABLE_MCP` | `false` | Enable MCP/SSE endpoint only when needed. |
| `LVS_DATABASE_BACKEND` | `elasticsearch_db` | Default event database backend. |
| `KAFKA_ENABLED` | `true` in dev-profile-lvs | Enables RTVI -> Kafka -> Logstash -> ES integration. |
| `KAFKA_BOOTSTRAP_SERVERS` | `${HOST_IP}:9092` | Broker address from the video summarization container. |
| `KAFKA_STRUCTURED_SUMMARY_TOPIC` | `mdx-structured-events-summary` | Structured summary publish topic. |
| `LVS_ENABLE_LLM_MERGING` | `true` in dev-profile-lvs | Merge duplicate or overlapping events with the LLM. |

RT-VLM values:

| Var | Default / Example | Purpose |
|---|---|---|
| `RTVI_VLM_BASE_URL` | `http://${HOST_IP}:8018` | Agent-facing RT-VLM URL. |
| `RTVI_VLM_URL` | `http://${HOST_IP}:${RTVI_VLM_PORT}` | video summarization-facing RT-VLM URL. |
| `RTVI_VLM_MODEL_TO_USE` | `cosmos-reason2` | RT-VLM backend selector for default integrated mode. |
| `RTVI_VLM_MODEL_PATH` | `ngc:nim/nvidia/cosmos-reason2-8b:hf-1208` | Default integrated checkpoint. |
| `RTVI_VLM_KAFKA_ENABLED` | `true` | Publish raw captions to Kafka. |
| `RTVI_VLM_KAFKA_TOPIC` | `mdx-vlm-captions` | Raw captions topic. |

## Model Id Rule

For the default integrated RT-VLM path:

```bash
VLM_NAME=nim_nvidia_cosmos-reason2-8b_hf-1208
RTVI_VLM_MODEL_PATH=ngc:nim/nvidia/cosmos-reason2-8b:hf-1208
```

`VLM_NAME` must match the id returned by:

```bash
curl -sf "http://${HOST_IP}:8018/v1/models" | jq -r '.data[].id'
```

Do not replace it with the friendly model name unless the endpoint advertises
that exact id.

## Helm Notes

The Helm service chart lives at `deploy/helm/services/video-summarization`.
Important 3.2 values:

- `image.repository: nvcr.io/nvstaging/vss-core/vss-video-summarization`
- `image.tag: "3.2.0-rc10-6f75390"`
- `service.backendPort: 38111`
- `service.mcpPort: 38112`
- `KAFKA_ENABLED: "true"`
- `KAFKA_STRUCTURED_SUMMARY_TOPIC: mdx-structured-events-summary`
- `LVS_ENABLE_MCP: "false"`

The Helm template computes `LVS_LLM_BASE_URL`, `LVS_LLM_MODEL_NAME`,
`VIA_VLM_ENDPOINT`, and `VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME` from profile or
global values.

## Common Checks

```bash
# video summarization health
curl -sf "http://${HOST_IP}:38111/v1/ready" >/dev/null

# RT-VLM model id
curl -sf "http://${HOST_IP}:8018/v1/models" | jq -r '.data[].id'

# Kafka topic traffic, when kafka is enabled
docker exec kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic mdx-vlm-captions \
  --max-messages 1

# Shared Logstash pipeline
docker logs --tail 100 logstash
```
