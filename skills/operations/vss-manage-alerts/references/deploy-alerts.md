# Deployment Reference: Alert Microservice

Deployment-time contract for the `alert-bridge` microservice (the **Alert Microservice**, formerly "Alert Verification" / "Alert Bridge"; default image `vss-alert-ms`, container `vss-alert-bridge`). Pairs with [integrate-alerts.md](integrate-alerts.md). The canonical service requirements and generated-build guidance live in [the Alerts capability-owner reference](../../../vss-build-vision-ai/references/services/alerts.md).

## Container Image

> **Source of truth — resolve it from Compose.** The `alert-bridge` image in
> `deploy/docker/services/alert/compose.yml` is parameterized by
> `VSS_ALERT_IMAGE`, `VSS_CONTAINER_TAG`, and `VSS_ALERT_TAG`. The image
> name inherits `VSS_CONTAINER_REGISTRY`; the shared container tag takes
> precedence, with `VSS_ALERT_TAG` as its fallback. The
> `deploy/docker/containers.env` supplies their defaults. A generated build
> must preserve those variables rather than copying an obsolete literal image.

- **Default image:** `ghcr.io/nvidia-ai-blueprints/vss/vss-alert-ms`
- **Default development tag:** `develop-latest`; a versioned deployment sets the shared `VSS_CONTAINER_TAG`.
- **Overrides:** `VSS_ALERT_IMAGE` overrides the shared registry-derived name. `VSS_CONTAINER_TAG` is the managed-set override; `VSS_ALERT_TAG` applies when the shared tag is unset.
- **Registry authentication:** determined by the resolved registry. Other services in the Alerts profile can still require `NGC_CLI_API_KEY` and `docker login nvcr.io`.
- **Architecture support:** x86_64 and aarch64. For DGX-SPARK / IGX-THOR / AGX-THOR with a non-remote VLM, set `VLM_AS_VERIFIER_CONFIG_FILE_PREFIX=EDGE-LOCAL-VLM-` so the edge-tuned verifier config is mounted.

## GPU Requirements

- **GPU required?** **No.** `alert-bridge` has no `deploy.resources.reservations.devices` block — it is a CPU-bound orchestrator that delegates all inference to a **VLM peer** over HTTP. GPU pressure lives on that peer (RT-VLM or a sibling NIM), not on `alert-bridge`.
- **Minimum VRAM:** n/a for `alert-bridge`. The verification VLM peer needs its own VRAM (e.g. Cosmos-Reason-2-8B ≈ 16–24 GB). When `VLM_MODE=local_shared`, the VLM may co-reside with the LLM on one GPU (`VLM_DEVICE_ID`).
- **Supported GPU architectures:** governed by the VLM peer, not `alert-bridge`.
- **GPU count per instance:** 0 for `alert-bridge`.
- **Can share GPU with other services?** Not applicable — no GPU reservation. Plan GPU placement on the VLM peer (`RT_VLM_DEVICE_ID` / `VLM_DEVICE_ID`).
- **Compose snippet for device reservation:** none — `alert-bridge` declares no `devices` block.

## CPU & Memory

- **Minimum CPU cores:** ~2 cores. The worker pool defaults to `alert_agent.num_workers: 10` threads; size cores to the expected verification throughput.
- **Minimum RAM:** ~2 GB.
- **`shm_size`:** default (not set).
- **`ulimits`:** default (not set).

## Storage

| Mount Path | Purpose | Type | Size estimate | Required permissions |
|---|---|---|---|---|
| `/app/configs/config.yml` | Verifier runtime config (VST/Kafka/VLM/sinks) | bind (ro) | < 1 MB | readable by container |
| `/app/configs/realtime-config.yml` | Optional always-on / realtime rule config source; required when always-on is enabled | bind (ro) | < 1 MB | readable by container |
| `/app/alert_type_config.json` | CV `category` → VLM verifier prompt map (CV mode) | bind (ro) | < 1 MB | readable by container |
| `/app/env-substitute.py` | Entrypoint that renders the required main YAML and optional realtime YAML | bind (ro) | < 1 MB | readable by container |
| `/app/runtime` | Resolved `config.yml` and `realtime-config.yml` outputs (`CONFIG_PATH` / `ALWAYS_ON_RULES_CONFIG`) | tmpfs | 10 MB | `mode=1777` (compose-set) |

No large or persistent volumes are required by `alert-bridge` itself. Durable state (alert configs, prompts, realtime rules) is stored in **Elasticsearch** (`persistence.backend: elasticsearch`, index prefix `ab-`), so it survives `docker compose down` independently of this container.

## Startup Behavior

- **Expected startup time:** fast — ~30 s (`start_period: 30s`). No model download; the entrypoint resolves the required main YAML and renders the realtime YAML when present, including `${VLM_NAME}` in always-on rules. Profiles without a realtime config continue to start while always-on is disabled. When `ALERT_AGENT_ALWAYS_ON=true` (real-time / `MODE=2d_vlm`), `alert_agent.always_on` is enabled and the rendered `ALWAYS_ON_RULES_CONFIG` is required and validated at boot (a missing or malformed file fails startup).
- **Startup ordering dependencies:**
  - `kafka` — `service_healthy` (must be able to subscribe to candidate topics)
  - `elasticsearch` — `service_healthy` (verified sink + persistence)
  - `kafka-topic-init-container` — `service_completed_successfully` (topics pre-created)
  - `rtvi-vlm`, `cosmos3-reasoner`, `cosmos3-reasoner-shared-gpu`, and `nvstreamer-alerts` — `required: false`
- **Health check endpoint:** `GET http://localhost:9080/health` → HTTP 200. The stock service compose has no container healthcheck; the standalone development compose uses the probe below.
- **Health check tuning (from the standalone dev compose):** `interval: 30s`, `timeout: 10s`, `retries: 3`, `start_period: 30s`.

```yaml
healthcheck:
  test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:9080/health', timeout=5)"]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 30s
```

- **Log signatures of healthy startup:** config resolution by `env-substitute.py`, then `enhance_alert_with_vlm.py` starting the FastAPI app on `:9080` and the Kafka consumer joining group `alert-bridge-vlm-group` (`auto_offset_reset: latest`). VLM warmup probes the configured backend (`vlm.warmup`).

## Known Deployment Issues

| Symptom | Root cause | Fix |
|---|---|---|
| Container boots then exits / `config.yml not found` | A `VLM_AS_VERIFIER_*` bind-mount source or `env-substitute.py` path does not resolve | Point the mount variables at the checked-in files under `deploy/docker/developer-profiles/dev-profile-alerts/vlm-as-verifier/configs/` and keep the script mount at `deploy/docker/services/alert/scripts/env-substitute.py`. |
| Consumer idle, no verifications happen | Candidate topics (`mdx-incidents` / `mdx-alerts`) not created, or no detector producing them | Keep `kafka-topic-init-container` in the allow-list; in `cv-verification` ensure RT-CV + Behavior Analytics are deployed and emitting. |
| Every verdict is `unverified`, `verification_response_code != 200` | VLM endpoint unreachable or wrong model id | Confirm `VLM_BASE_URL` / `RTVI_VLM_BASE_URL` reachable and `VLM_NAME` matches what the backend advertises at `GET /v1/models` (mismatch → HTTP 400 "No such model"). |
| `unverified` with VST/clip errors | Sensor has no retrievable stream, or VST unreachable on `:30888` | Verify VIOS is up and the sensor is registered/online; clip window is end-anchored `segment_duration_seconds`. |
| Incidents present but never verified | CV `category` not present in `alert_type_config.json` | Add the `alert_type` entry (mapping `category` → prompts) and restart `alert-bridge`. |
| `POST /api/v1/realtime/always-on` returns `503 ALWAYS_ON_DISABLED` | `ALERT_AGENT_ALWAYS_ON=false` / `alert_agent.always_on: false` (verification / 2d_cv) | Redeploy alerts with `-m real-time` so `dev-profile.sh` sets `ALERT_AGENT_ALWAYS_ON=true`, or confirm `generated.env` has that value and the verifier config substitutes the gate. |
| Name conflict `/vss-alert-bridge already in use` / port `9080` in use | The fixed `container_name` and published host port must be unique on the Docker host | Stop and remove the prior `vss-alert-bridge`, or choose an unused `ALERT_BRIDGE_HOST_PORT`, before bring-up. |
| (`cv-verification`) `vss-rtvi-cv` exits: `mkdir: cannot create directory '/opt/engines/gdino': Permission denied` | The host `${VSS_APPS_DIR}/engines/` bind-mounted at `/opt/engines/` was created root-owned / non-writable; RT-CV builds TensorRT engines into `/opt/engines/{gdino,rtdetr-its}` at first run | Pre-create + world-write the engine dirs **before** bring-up: `mkdir -p ${VSS_APPS_DIR}/engines/{gdino,rtdetr-its} && chmod -R 777 ${VSS_APPS_DIR}/engines`. This mirrors the Alerts host preparation in `deploy/docker/scripts/dev-profile.sh`. |
| (`cv-verification`) `vss-rtvi-cv` fails loading the GDINO/RT-DETR ONNX, or detections never appear | ds-start phase 0 failed to download the detector models — missing `NGC_CLI_API_KEY`, unwritable `${VSS_DATA_DIR}/models`, or NGC network error | Confirm `NGC_CLI_API_KEY` is exported, `mkdir -p ${VSS_DATA_DIR}/models && chmod -R 777 ${VSS_DATA_DIR}/models`, restart `vss-rtvi-cv`, and check its logs for phase-0 download output. |
| Explicitly selected `vss-agent` / legacy `vss-va-mcp` exits: config / template file not found | A generated `.env` rewrote `VSS_AGENT_CONFIG_FILE` / `VSS_VA_MCP_CONFIG_FILE` / `VSS_AGENT_TEMPLATE_PATH` to a **host-absolute** path; these must stay **container-relative** (`./deploy/docker/...`), resolved inside the container via the `${VSS_APPS_DIR}:/vss-agent/deploy/docker:ro` mount + `/vss-agent` workdir | Keep those variables verbatim as `./deploy/docker/...` when their owning service is selected — do not prefix `${VSS_APPS_DIR}`. Normal host-CLI/NemoClaw Alerts selects neither service and does not require `VSS_VA_MCP_CONFIG_FILE`. |

## Prerequisites

- **Driver / Container Toolkit:** required only for the GPU-bearing VLM peer, not for `alert-bridge` itself.
- **Docker / Compose:** Compose v2.36+.
- **API keys:** `NGC_CLI_API_KEY` (image pull). `NVIDIA_API_KEY` if the VLM peer uses a remote build.nvidia.com endpoint.
- **Free ports:** `ALERT_BRIDGE_HOST_PORT` (default `9080`) on the host interface.
- **Reachable peers:** Kafka `kafka:29092`, Elasticsearch `elasticsearch:9200`, VIOS/VST `vst-ingress:30888`, and a verification VLM (`rtvi-vlm:8000` by default).
- **Network reachability:** the registries resolved by the selected profile, commonly `ghcr.io` for Alert Bridge and `nvcr.io` for NGC-hosted peer images.

## Verify Deployment

```bash
# 1. Service health
curl -sf --connect-timeout 5 "http://${HOST_IP}:${ALERT_BRIDGE_HOST_PORT:-9080}/health" && echo "alert-bridge OK"

# 2. (vlm-realtime) realtime rules endpoint reachable — empty array is success
curl -s "http://${HOST_IP}:${ALERT_BRIDGE_HOST_PORT:-9080}/api/v1/realtime" | jq .

# 3. Verified records land in Elasticsearch after a detection
curl -sf "http://${HOST_IP}:${ELASTICSEARCH_HOST_PORT:-9200}/mdx-vlm-incidents/_count" | jq '.count'
```

## Tear Down

From the repository root, `./deploy/docker/scripts/dev-profile.sh down` stops
the active developer-profile deployment. Durable alert configs, prompts, and
realtime rules persist in Elasticsearch (`ab-*` indices) and are NOT removed
by stopping the container; removing the Elasticsearch volume wipes persisted
rules.
