# Deployment Reference: Alert Microservice

Deployment-time contract for the `alert-bridge` microservice (the **Alert
Microservice**, formerly "Alert Verification" / "Alert Bridge"; image
`vss-alert-ms`, container `vss-alert-bridge`). Pairs with
`integrate-alerts.md`; deployment composition guidance lives in
[`vss-build-vision-ai/references/services/alerts.md`](../../../vss-build-vision-ai/references/services/alerts.md).

## Container Image

> **Source of truth — read it from the compose, do not hardcode.** The
> `alert-bridge` image in `deploy/docker/services/alert/compose.yml` uses
> `VSS_ALERT_IMAGE` as a full-reference override. Otherwise it composes the
> repository from `VSS_CONTAINER_REGISTRY`; the tag resolves as
> `${VSS_CONTAINER_TAG:-${VSS_ALERT_TAG:-3.3.0}}`. The global
> `VSS_CONTAINER_TAG` wins when set, and `VSS_ALERT_TAG` is used only when the
> global tag is unset. To read the current expression:
>
> ```bash
> # authoritative image:tag for this deployment
> grep -E '^\s*image:' deploy/docker/services/alert/compose.yml          # upstream
> grep -E '^\s*image:' <BUILD_DIR>/patched/services/alert/compose.yml    # the patched copy actually deployed
> ```
>
> The values below are the VSS 3.3.0 defaults; environment overrides may select
> another promoted image.

- **Image name:** `nvcr.io/nvidia/vss-core/vss-alert-ms`
- **Tag:** `3.3.0`
- **Registry:** `nvcr.io`
- **NGC pull requirements:** yes — `NGC_CLI_API_KEY` + `docker login nvcr.io`.
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

No large or persistent volumes are required by `alert-bridge` itself. Durable state (alert configs, prompts, realtime rules, and confirmed-verdict protection) is stored in **Elasticsearch** (`persistence.backend: elasticsearch`, index prefix `ab-`), so it survives `docker compose down` independently of this container. Event deduplication is TTL-bounded in process.

## Startup Behavior

- **Expected startup time:** fast — ~30 s (`start_period: 30s`). No model download; the entrypoint resolves the required main YAML and renders the realtime YAML when present, including `${VLM_NAME}` in always-on rules. Profiles without a realtime config continue to start while always-on is disabled. When `ALERT_AGENT_ALWAYS_ON=true` (real-time / `MODE=2d_vlm`), `alert_agent.always_on` is enabled and the rendered `ALWAYS_ON_RULES_CONFIG` is required and validated at boot (a missing or malformed file fails startup).
- **Startup ordering dependencies:**
  - `kafka` — `service_healthy` (must be able to subscribe to candidate topics)
  - `elasticsearch` — `service_healthy` (verified sink + persistence)
  - `kafka-topic-init-container` — `service_completed_successfully` (topics pre-created)
  - `rtvi-vlm` / sibling NIMs / `nvstreamer-alerts` — `required: false` (strip if undefined; see Known Deployment Issues)
- **Health check endpoint:** `GET http://localhost:9080/health` → HTTP 200. (Upstream blueprint compose declares no healthcheck on `alert-bridge`; the standalone dev compose uses a Python `urllib` probe against `/health`. A generated deploy should add the `/health` healthcheck below so the bring-up loop can gate on it.)
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
| `invalid compose project` / unresolved `depends_on` at `docker compose config` | Standalone copy still references undefined optional peers (`nvstreamer-alerts`, `cosmos3-reasoner`, `cosmos3-reasoner-shared-gpu`, `rtvi-vlm`) | Strip only undefined optional peers in the patched copy. Keep `kafka`, `elasticsearch`, and `kafka-topic-init-container`; keep `rtvi-vlm` when selected. See [`vss-build-vision-ai/references/services/alerts.md`](../../../vss-build-vision-ai/references/services/alerts.md). |
| Container boots then exits / `config.yml not found` | The three config bind-mounts (or `env-substitute.py`) were not materialized into the patched tree | Materialize the verifier config files (Step 6.5 Patch 3): copy `developer-profiles/dev-profile-alerts/vlm-as-verifier/configs/{config.yml,alert_type_config.json}` + the realtime config + `services/alert/scripts/env-substitute.py` into the patched tree, and set the `VLM_AS_VERIFIER_*` env to those paths. |
| Consumer idle, no verifications happen | Candidate topics (`mdx-incidents` / `mdx-alerts`) not created, or no detector producing them | Keep `kafka-topic-init-container` in the allow-list; in `cv-verification` ensure RT-CV + Behavior Analytics are deployed and emitting. |
| Every verdict is `unverified`, `verification_response_code != 200` | VLM endpoint unreachable or wrong model id | Confirm `VLM_BASE_URL` / `RTVI_VLM_BASE_URL` reachable and `VLM_NAME` matches what the backend advertises at `GET /v1/models` (mismatch → HTTP 400 "No such model"). |
| `unverified` with VST/clip errors | Sensor has no retrievable stream, or VST unreachable on `:30888` | Verify VIOS is up and the sensor is registered/online; clip window is end-anchored `segment_duration_seconds`. |
| Incidents present but never verified | CV `category` not present in `alert_type_config.json` | Add the `alert_type` entry (mapping `category` → prompts) and restart `alert-bridge`. |
| `POST /api/v1/realtime/always-on` returns `503 ALWAYS_ON_DISABLED` | `ALERT_AGENT_ALWAYS_ON=false` / `alert_agent.always_on: false` (verification / 2d_cv) | Redeploy alerts with `-m real-time` so `dev-profile.sh` sets `ALERT_AGENT_ALWAYS_ON=true`, or confirm `generated.env` has that value and the verifier config substitutes the gate. |
| Name conflict `/vss-alert-bridge already in use` / port `9080` in use | The explicit `container_name` and published host port are singleton resources | Tear down the prior generation's `vss-alert-bridge` before bring-up (Step 6.5 Patch 0 orphan-container check). |
| (`cv-verification`) `vss-rtvi-cv` exits: `mkdir: cannot create directory '/opt/engines/gdino': Permission denied` | The host `${VSS_APPS_DIR}/engines/` bind-mounted at `/opt/engines/` was created root-owned / non-writable; RT-CV builds TensorRT engines into `/opt/engines/{gdino,rtdetr-its}` at first run | Pre-create the engine directories with the ownership expected by RT-CV before bring-up. Follow [`vss-build-vision-ai/references/services/alerts.md`](../../../vss-build-vision-ai/references/services/alerts.md) and `deploy/docker/scripts/dev-profile.sh`. |
| (`cv-verification`) `vss-rtvi-cv` fails loading the GDINO/RT-DETR ONNX, or detections never appear | ds-start phase 0 failed to download the detector models — missing `NGC_CLI_API_KEY`, unwritable `${VSS_DATA_DIR}/models`, or NGC network error | Confirm `NGC_CLI_API_KEY` is exported, `mkdir -p ${VSS_DATA_DIR}/models && chmod -R 777 ${VSS_DATA_DIR}/models`, restart `vss-rtvi-cv`, and check its logs for phase-0 download output. |
| Explicitly selected `vss-agent` / legacy `vss-va-mcp` exits: config / template file not found | A generated `.env` rewrote `VSS_AGENT_CONFIG_FILE` / `VSS_VA_MCP_CONFIG_FILE` / `VSS_AGENT_TEMPLATE_PATH` to a **host-absolute** path; these must stay **container-relative** (`./deploy/docker/...`), resolved inside the container via the `${VSS_APPS_DIR}:/vss-agent/deploy/docker:ro` mount + `/vss-agent` workdir | Keep those variables verbatim as `./deploy/docker/...` when their owning service is selected — do not prefix `${VSS_APPS_DIR}`. Normal host-CLI/NemoClaw Alerts selects neither service and does not require `VSS_VA_MCP_CONFIG_FILE`. |

## Prerequisites

- **Driver / Container Toolkit:** required only for the GPU-bearing VLM peer, not for `alert-bridge` itself.
- **Docker / Compose:** Compose v2.36+ (the same `depends_on` strip requirement as the rest of VSS — undefined `required: false` peers are rejected at project-load).
- **API keys:** `NGC_CLI_API_KEY` (image pull). `NVIDIA_API_KEY` if the VLM peer uses a remote build.nvidia.com endpoint.
- **Free ports:** `9080` (REST realtime API) on the host interface.
- **Reachable peers:** from `alert-bridge`, Kafka at `kafka:29092`, Elasticsearch at `elasticsearch:9200`, VIOS/VST at `vst-ingress:30888`, and the selected verification VLM (by default `rtvi-vlm:8000`).
- **Network reachability:** `nvcr.io` for the image pull.

## Verify Deployment

```bash
# 1. Service health
curl -sf --connect-timeout 5 http://${HOST_IP}:9080/health && echo "alert-bridge OK"

# 2. (vlm-realtime) realtime rules endpoint reachable — an empty .rules array is success
curl -s http://${HOST_IP}:9080/api/v1/realtime | jq .

# 3. Verified records land in Elasticsearch after a detection
curl -sf --connect-timeout 10 --max-time 15 \
  "http://${HOST_IP}:9200/mdx-vlm-incidents/_count" | jq '.count'
```

## Tear Down

`docker compose ... --profile <flag> down` stops `alert-bridge`. Durable alert configs / prompts / realtime rules persist in Elasticsearch (`ab-*` indices) and are NOT removed by stopping the container; `down -v` removes the ES volume and therefore wipes persisted rules and verdict state. Warn the operator before using `-v`.
