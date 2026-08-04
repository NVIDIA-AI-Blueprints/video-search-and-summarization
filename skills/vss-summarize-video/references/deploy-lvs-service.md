# Deployment Reference: Long Video Summarization (LVS)

## Container Image

- **Image name:** `ghcr.io/nvidia-ai-blueprints/vss/vss-video-summarization`
- **Tag pattern:** `develop-latest` tracks the newest `develop` build. Use a
  `pr-<N>-latest` or commit-stamped tag for branch-specific testing.
- **Registry:** `ghcr.io`
- **Pull requirements:** The package is public; authenticated pulls use a
  GitHub token with `read:packages` rather than an NGC key.
- **Architecture support:** the tag is a multi-arch manifest covering
  `linux/amd64` and `linux/arm64`, so the same tag works on x86, SBSA, and
  Jetson/Thor targets.

The source-of-truth upstream service key is `lvs` in
`services/video-summarization/docker/deploy/compose.yaml`; the default container
name is `lvs`. Older developer-profile wrapper composes may expose
`lvs-server` / `vss-lvs`, but build-vision-agent generated allow-lists must use
the source-of-truth `lvs` key and compose path.

## GPU Requirements

- **GPU required?** Conditional. `lvs` itself does not reserve a GPU, but
  the generated profile must provide a VLM backend and usually an LLM backend.
- **Minimum VRAM:** Determined by the selected VLM/LLM peers. The default
  integrated RT-VLM Cosmos Reason 8B path needs a VLM-capable GPU; the local
  Nemotron LLM NIM needs GPU memory per its hardware-profile env file.
- **Supported GPU architectures:** Follow the selected RT-VLM and LLM NIM
  deployment references.
- **GPU count per instance:** `lvs` needs 0; default local peers usually
  need 1 GPU for RT-VLM plus either a dedicated or shared GPU for the LLM.
- **Can share GPU with other services?** `lvs` can share because it has no
  GPU reservation. LLM and VLM sharing is controlled by the selected NIM and
  RT-VLM profile modes.
- **Compose snippet for device reservation:** Not applicable to `lvs`.
  Use the selected RT-VLM and LLM deploy references for GPU reservation blocks.

## CPU & Memory

- **Minimum CPU cores:** 8 cores recommended for the LVS API plus shared VSS
  infra.
- **Minimum RAM:** 32 GB recommended for the LVS API plus infra; add memory for
  local RT-VLM/LLM model servers.
- **`shm_size`:** Not set on `lvs`; model-serving peers may set `shm_size`.
- **`ulimits`:** `lvs` may set service-level limits such as `nofile`; model-serving peers may set their own limits.

## Storage

| Mount Path | Purpose | Type | Size estimate | Required permissions |
|---|---|---|---|---|
| `${VSS_APPS_DIR}/services/video-summarization/configs/config.yaml:/app/config.yaml:ro` | CA-RAG/LVS service configuration | bind mount | small | Source must be an existing readable file |
| `${MODEL_ROOT_DIR:-/tmp/model_cache}:${MODEL_ROOT_DIR:-/tmp/model_cache}` | Optional model/cache directory used by LVS and clients | bind mount | variable | Directory must be readable and writable by the container user |
| VIOS video storage under `${VSS_DATA_DIR}` | Uploaded files and recorded clips that LVS summarizes through VIOS URLs | bind mount owned by VIOS peers | depends on video set | Follow VIOS deploy reference permissions |
| RT-VLM and LLM model cache volumes | Peer model weights and runtime caches | named volumes | tens of GB | Managed by Docker; survive normal `down` without `-v` |

Do not let Docker create a directory for `config.yaml`. If the source path is
wrong, Docker may create an empty directory and LVS will fail at startup.

## Startup Behavior

- **Expected startup time:** `lvs` usually becomes ready within 2 minutes
  after its peers are available. First boot of local RT-VLM or LLM peers can take
  up to 20 minutes due to image/model download and model warmup.
- **Startup ordering dependencies:** `lvs` declares peer `depends_on` entries
  for services such as Elasticsearch and LLM readiness helpers. Generated
  standalone builds must keep included peers and strip only undefined optional
  peers when a wrapper or selected deployment shape declares them.
- **Health check endpoint:** `GET http://localhost:38111/v1/ready`
- **Health check tuning:** `interval: 30s`, `timeout: 10s`, `retries: 10`,
  `start_period: 120s`
- **Log signatures of healthy startup:** `lvs` accepts requests on
  `BACKEND_PORT` and `curl -sf http://localhost:38111/v1/ready` exits 0.

## Known Deployment Issues

| Symptom | Root cause | Fix |
|---|---|---|
| `docker compose config` fails with an undefined `depends_on` service | `lvs` or an older wrapper references peers that were not included in a standalone build | Strip undefined optional peers in the patched copy or add the selected peer service key to the allow-list |
| `GET /v1/ready` returns 503 | RT-VLM, LLM, Elasticsearch, or config initialization is not ready | Check `lvs`, `vss-rtvi-vlm`, selected LLM NIM, and Elasticsearch logs; retry until the `start_period` budget expires |
| `POST /v1/summarize` returns 422 | Request is missing required `model`, `scenario`, or `events`, or includes a non-schema field | Build requests from `video-summarization-api.md` and the OpenAPI schema |
| `POST /v1/summarize` returns 400 for model errors | `model` does not match the id advertised by `GET /models` | Query `/models` and copy the exact id into the request and `VLM_NAME` |
| `POST /v1/summarize` returns 503 busy | LVS is already processing a file | Run one smoke-test summarize request at a time and retry after the current request completes |
| LVS starts with config-file errors | The `config.yaml` bind source resolved to a missing file or Docker-created directory | Point `VSS_APPS_DIR` to `deploy/docker` or materialize `config.yaml` into the build output and rewrite the bind source |
| LVS cannot reach Kafka or Elasticsearch | Host-networked service has wrong `HOST_IP`, `KAFKA_BOOTSTRAP_SERVERS`, `ES_HOST`, or `ES_PORT` | Use host-reachable addresses such as `${HOST_IP}:9092` and `localhost:9200`/`${HOST_IP}:9200` according to the generated env |
| `lvs` pulls or starts the wrong image | A wrapper or generated compose image value does not match the selected LVS release | Use the source-of-truth LVS compose image contract or patch the image line to an LVS-specific `LVS_IMAGE` / `LVS_TAG` value |

## Prerequisites

- NVIDIA driver and NVIDIA Container Toolkit when local RT-VLM or LLM NIM peers
  are selected.
- Docker Compose version new enough to support the upstream compose syntax.
- `NGC_CLI_API_KEY` for NGC image pulls and local model downloads.
- `NVIDIA_API_KEY` or `OPENAI_API_KEY` when remote LLM/VLM endpoints require
  auth.
- Enough disk for VSS images, model caches, VIOS stored video, and Elasticsearch
  indices.
- Free host ports: `38111` for LVS, `38112` only when MCP is enabled, plus peer
  ports such as RT-VLM `8018`, Kafka `9092`, Elasticsearch `9200`, and VIOS
  `30888`.
- Network reachability to `nvcr.io` and any remote LLM/VLM endpoint.

## Dry Run

From the generated build directory:

```bash
docker compose --env-file .env -f compose.yml --profile <invented-flag> config >/tmp/lvs-resolved.yml
```

Then verify no real unexpanded variables remain:

```bash
if grep -nE '(^|[^$])\${[A-Za-z_]' /tmp/lvs-resolved.yml; then
  echo "unexpanded variables remain"
  exit 1
fi
```

## Verify Deployment

```bash
curl -sf --max-time 15 "http://${HOST_IP}:38111/v1/ready" >/dev/null
curl -sf --max-time 15 "http://${HOST_IP}:38111/models" | jq '.data[0].id'
```

Stored-video API smoke test:

```bash
curl -s -X POST "http://${HOST_IP}:38111/v1/summarize" \
  -H "Content-Type: application/json" \
  -d "$(jq -n \
    --arg model "${VLM_NAME:-nim_nvidia_cosmos-reason2-8b_hf-1208}" \
    --arg url "<VIOS_HTTP_VIDEO_OR_CLIP_URL>" \
    --arg scenario "warehouse safety review" \
    --argjson events '["person activity","forklift interaction"]' \
    '{
      model: $model,
      url: $url,
      scenario: $scenario,
      events: $events,
      chunk_duration: 10,
      num_frames_per_second_or_fixed_frames_chunk: 20,
      use_fps_for_chunking: false
    }')" | jq -e '.choices[0].message.content | length > 0'
```

## Logs & Status

```bash
docker ps --filter name=lvs --format '{{.Names}} {{.Status}}'
docker logs --tail 100 lvs
curl -sf "http://${HOST_IP}:38111/metrics" | head
```

## Tear Down

Use the generated build profile and generated env:

```bash
docker compose --env-file .env -f compose.yml --profile <invented-flag> down
```

Use `down -v` only when you intentionally want to remove Docker named volumes
such as model caches and Elasticsearch data.
