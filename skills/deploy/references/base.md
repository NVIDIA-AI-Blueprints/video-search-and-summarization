# Base Profile Reference

Profile: `base` | Blueprint: `bp_developer_base` | Mode: `2d`

Video upload, Q&A, and report generation with HITL (Human-in-the-Loop) feedback.

## Services Deployed

| Service | Container | Port | Purpose |
|---|---|---|---|
| VSS Agent | mdx-vss-agent-1 | 8000 | Orchestrates tool calls and model inference |
| VSS UI | mdx-vss-ui-1 | 3000 | Web UI — chat, video upload, views |
| VST | mdx-vst-1 | 30888 | Video Storage Tool — ingest, record, playback |
| VST MCP | mdx-vst-mcp-1 | 8001 | VST management API |
| LLM NIM | mdx-nim-llm-1 | 30081 | Nemotron LLM for reasoning |
| VLM NIM | mdx-nim-vlm-1 | 30082 | Cosmos Reason VLM for vision |
| Elasticsearch | mdx-elasticsearch-1 | 9200 | Analytics data store |
| Kafka | mdx-kafka-1 | 9092 | Message broker |
| Redis | mdx-redis-1 | 6379 | Cache |
| Phoenix | mdx-phoenix-1 | 6006 | Observability / telemetry |

## Default Models

| Role | Model | Slug | Type |
|---|---|---|---|
| LLM | `nvidia/nvidia-nemotron-nano-9b-v2` | `nvidia-nemotron-nano-9b-v2` | nim |
| VLM | `nvidia/cosmos-reason2-8b` | `cosmos-reason2-8b` | nim |

**Alternate LLMs:** `nvidia/NVIDIA-Nemotron-Nano-9B-v2-FP8`, `nvidia/nemotron-3-nano`, `nvidia/llama-3.3-nemotron-super-49b-v1.5`, `openai/gpt-oss-20b`

**Alternate VLMs:** `nvidia/cosmos-reason1-7b`, `Qwen/Qwen3-VL-8B-Instruct`

## GPU Layout

| LLM/VLM Mode | LLM_DEVICE_ID | VLM_DEVICE_ID | Description |
|---|---|---|---|
| `local_shared` (default) | 0 | 0 | Both models share one GPU |
| `local` | 0 | 1 | Dedicated GPU per model |
| `remote` | — | — | No local GPU needed for inference |

## Env Overrides — Common Scenarios

### Minimal deploy (auto-detect hardware)

```json
{
  "HARDWARE_PROFILE": "<detected>",
  "MDX_SAMPLE_APPS_DIR": "<repo>/deployments",
  "MDX_DATA_DIR": "<repo>/data",
  "HOST_IP": "<detected>",
  "NGC_CLI_API_KEY": "<from env>"
}
```

> **Note on base URLs**: `LLM_BASE_URL` / `VLM_BASE_URL` must NOT end in `/v1`.
> The agent config appends `/v1` automatically. If the user gives you a URL
> with `/v1`, strip it before writing to the env.

### Remote LLM + local VLM

```json
{
  "HARDWARE_PROFILE": "<detected>",
  "MDX_SAMPLE_APPS_DIR": "<repo>/deployments",
  "MDX_DATA_DIR": "<repo>/data",
  "HOST_IP": "<detected>",
  "NGC_CLI_API_KEY": "<from env>",
  "LLM_MODE": "remote",
  "LLM_BASE_URL": "https://integrate.api.nvidia.com",
  "NVIDIA_API_KEY": "<key>"
}
```

### Remote LLM + remote VLM (no local GPU for inference)

```json
{
  "HARDWARE_PROFILE": "<detected>",
  "MDX_SAMPLE_APPS_DIR": "<repo>/deployments",
  "MDX_DATA_DIR": "<repo>/data",
  "HOST_IP": "<detected>",
  "LLM_MODE": "remote",
  "LLM_BASE_URL": "https://integrate.api.nvidia.com",
  "VLM_MODE": "remote",
  "VLM_BASE_URL": "https://integrate.api.nvidia.com",
  "NVIDIA_API_KEY": "<key>"
}
```

### Dedicated GPUs (2-GPU system)

```json
{
  "HARDWARE_PROFILE": "<detected>",
  "MDX_SAMPLE_APPS_DIR": "<repo>/deployments",
  "MDX_DATA_DIR": "<repo>/data",
  "HOST_IP": "<detected>",
  "NGC_CLI_API_KEY": "<from env>",
  "LLM_MODE": "local",
  "VLM_MODE": "local",
  "LLM_DEVICE_ID": "0",
  "VLM_DEVICE_ID": "1"
}
```

### Different LLM model

```json
{
  "LLM_NAME": "nvidia/llama-3.3-nemotron-super-49b-v1.5",
  "LLM_NAME_SLUG": "llama-3.3-nemotron-super-49b-v1.5"
}
```

## COMPOSE_PROFILES (computed — do not set directly)

The `.env` file computes this from other variables:

```
COMPOSE_PROFILES=${BP_PROFILE}_${MODE},${BP_PROFILE}_${MODE}_${HARDWARE_PROFILE},${BP_PROFILE}_${MODE}_${PROXY_MODE},llm_${LLM_MODE}_${LLM_NAME_SLUG},vlm_${VLM_MODE}_${VLM_NAME_SLUG}
```

Example resolved value:
```
bp_developer_base_2d,bp_developer_base_2d_DGX-SPARK,bp_developer_base_2d_no_proxy,llm_local_shared_nvidia-nemotron-nano-9b-v2,vlm_local_shared_cosmos-reason2-8b
```

The agent sets the upstream variables — `COMPOSE_PROFILES` is derived automatically.

## Endpoints (after deploy)

| Service | URL |
|---|---|
| Agent UI | `http://<HOST_IP>:3000/` |
| Agent REST API | `http://<HOST_IP>:8000/` |
| Swagger UI | `http://<HOST_IP>:8000/docs` |
| Reports | `http://<HOST_IP>:8000/static/agent_report_<DATE>.md` |
| Phoenix telemetry | `http://<HOST_IP>:6006/` |

## Env File Location

```
<repo>/deployments/developer-workflow/dev-profile-base/.env
```

## Debugging

After a base deploy is up, use `scripts/test_base.py` to confirm the
full pipeline (VST upload → VLM → agent report) is working end-to-end.

```bash
# From the repo root, against a locally deployed agent:
python skills/deploy/scripts/test_base.py http://localhost:8000 \
    --profile base

# If the agent is behind a Brev secure link:
python skills/deploy/scripts/test_base.py \
    https://80-<BREV_ENV_ID>.brevlab.com --profile base

# Use a different sample video instead of the default Pexels download:
python skills/deploy/scripts/test_base.py http://localhost:8000 \
    --video-path /path/to/warehouse.mp4 --profile base
```

What it does, in order:

1. Waits on `http://<agent>:8000/health`.
2. `POST /api/v1/videos` → gets a signed VST upload URL.
3. `PUT` the bytes to that VST URL (bypasses `/videos-for-search` → avoids RTVI-CV).
4. Confirms the video appears in `GET /vst/api/v1/sensor/streams`.
5. Sends the two base-profile queries over the agent WebSocket:
   - `"What videos are available?"`
   - `"Generate a report for video <video_name>"`
6. Auto-responds to the VLM-prompt HITL with the default prompt.
7. Exits 0 if both queries return non-empty content.

Common failure modes and what they mean for base:

| Symptom | Likely cause |
|---|---|
| `POST /api/v1/videos` HTTP 500 | Agent not finished starting — poll `/health` longer |
| VST `sensor/streams` stays empty | VST container unhealthy — check `docker logs vst-ingress-dev` |
| WebSocket query returns `error_message` | LLM or VLM NIM not healthy — `docker logs nvidia-nemotron-nano-9b-v2-shared-gpu` / `cosmos-reason2-8b-shared-gpu` |
| HITL prompt never arrives | `vss-agent` misconfigured HITL config — check `config.yml` |
| Empty report | VLM unreachable from inside `vss-agent` container — check `VLM_BASE_URL` in resolved compose env |

For remote LLM/VLM deploys, add `--vst-url` if VST isn't on localhost:

```bash
python skills/deploy/scripts/test_base.py http://<host>:8000 \
    --vst-url http://<host>:30888 --profile base
```

## Known Issues

- `cosmos-reason2-8b` NIM cannot restart after stop/crash — must redeploy full stack
- Reports are in-memory by default — lost on container restart (mount a volume to persist)
- `VLM_NIM_KVCACHE_PERCENT` defaults to `0.7` — may need tuning on memory-constrained GPUs
