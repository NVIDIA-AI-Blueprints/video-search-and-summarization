# Deployment Reference: Alert Microservice

Deployment-time contract for the `alert-bridge` microservice (the **Alert Microservice**, formerly "Alert Verification" / "Alert Bridge"; image/container identifiers unchanged — `vss-alert-verification` / `vss-alert-bridge`). Pairs with `integrate-alerts.md`. The component-services allow-list and Step 6.5 patch specifics live in `vss-build-vision-agent/references/patch-alerts.md`.

## Container Image

> **Source of truth — read it from the compose, do not hardcode.** The `alert-bridge` image is declared **inline** in the `image:` field of the `alert-bridge` service in `deploy/docker/services/alert/compose.yml` — it is **not** parameterized by an env var (there is no `ALERT_*_IMAGE_TAG` in `dev-profile-alerts/.env`). When `build-vision-agent` copies/patches that compose into `<BUILD_DIR>/patched/`, the image string comes along verbatim, so there is nothing to fold into the generated `.env`. To read the current value:
>
> ```bash
> # authoritative image:tag for this deployment
> grep -E '^\s*image:' deploy/docker/services/alert/compose.yml          # upstream
> grep -E '^\s*image:' <BUILD_DIR>/patched/services/alert/compose.yml    # the patched copy actually deployed
> ```
>
> The values below are a **snapshot for reference only** (as of VSS 3.2.0) — if upstream bumps the tag, the compose is the truth and this doc may lag. **Future-proofing:** if upstream later parameterizes the tag (e.g. introduces `ALERT_VERIFICATION_IMAGE_TAG`), switch to resolving that var from `dev-profile-alerts/.env` (the same way RT-VLM resolves `RTVI_VLM_IMAGE_TAG` from `dev-profile-base/.env`) and fold it into the generated `.env`.

- **Image name (snapshot):** `nvcr.io/nvidia/vss-core/vss-alert-verification`
- **Tag (snapshot):** `3.2.0`, declared inline at `services/alert/compose.yml` (the `alert-bridge` service). The standalone dev compose (`services/alert/deploy_docker-compose.yml`)
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
| `/app/configs/config.yml` | Verifier runtime config (VST/Kafka/Redis/VLM/sinks) | bind (ro) | < 1 MB | readable by container |
| `/app/configs/realtime-config.yml` | Always-on / realtime rule config (`ALWAYS_ON_RULES_CONFIG`) | bind (ro) | < 1 MB | readable by container |
| `/app/alert_type_config.json` | CV `category` → VLM verifier prompt map (CV mode) | bind (ro) | < 1 MB | readable by container |
| `/app/env-substitute.py` | Entrypoint that folds `${...}` env into `config.yml` | bind (ro) | < 1 MB | readable by container |
| `/app/runtime` | Resolved config output (`CONFIG_PATH`) | tmpfs | 10 MB | `mode=1777` (compose-set) |

No large or persistent volumes are required by `alert-bridge` itself. Durable state (alert configs, prompts, realtime rules) is stored in **Elasticsearch** (`persistence.backend: elasticsearch`, index prefix `ab-`), so it survives `docker compose down` independently of this container. Dedup state lives in **Redis** (TTL-bounded).

## Startup Behavior

- **Expected startup time:** fast — ~30 s (`start_period: 30s`). No model download; the container only needs its peers reachable. When `ALERT_AGENT_ALWAYS_ON=true` (real-time / `MODE=2d_vlm`), `alert_agent.always_on` is enabled and `ALWAYS_ON_RULES_CONFIG` is validated at boot (a malformed file fails startup).
- **Startup ordering dependencies:**
  - `kafka` — `service_healthy` (must be able to subscribe to candidate topics)
  - `elasticsearch` — `service_healthy` (verified sink + persistence)
  - `redis` — `service_started` (dedup / rule state)
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
| `invalid compose project` / unresolved `depends_on` at `docker compose config` | Standalone copy still references undefined `required: false` peers (`rtvi-vlm`, the 8 sibling NIMs, `nvstreamer-alerts`) | Strip undefined `depends_on` peers in the patched copy (Step 6.5 Patch 2 / `patch-alerts.md`). Keep `kafka`, `redis`, `elasticsearch`, `kafka-topic-init-container` (defined via ELK) and `rtvi-vlm` when RT-VLM is in the allow-list. |
| Container boots then exits / `config.yml not found` | The three config bind-mounts (or `env-substitute.py`) were not materialized into the patched tree | Materialize the verifier config files (Step 6.5 Patch 3): copy `developer-profiles/dev-profile-alerts/vlm-as-verifier/configs/{config.yml,alert_type_config.json}` + the realtime config + `services/alert/scripts/env-substitute.py` into the patched tree, and set the `VLM_AS_VERIFIER_*` env to those paths. |
| Consumer idle, no verifications happen | Candidate topics (`mdx-incidents` / `mdx-alerts`) not created, or no detector producing them | Keep `kafka-topic-init-container` in the allow-list; in `cv-verification` ensure RT-CV + Behavior Analytics are deployed and emitting. |
| Every verdict is `unverified`, `verification_response_code != 200` | VLM endpoint unreachable or wrong model id | Confirm `VLM_BASE_URL` / `RTVI_VLM_BASE_URL` reachable and `VLM_NAME` matches what the backend advertises at `GET /v1/models` (mismatch → HTTP 400 "No such model"). |
| `unverified` with VST/clip errors | Sensor has no retrievable stream, or VST unreachable on `:30888` | Verify VIOS is up and the sensor is registered/online; clip window is end-anchored `segment_duration_seconds`. |
| Incidents present but never verified | CV `category` not present in `alert_type_config.json` | Add the `alert_type` entry (mapping `category` → prompts) and restart `alert-bridge`. |
| `POST /api/v1/realtime/always-on` returns `503 ALWAYS_ON_DISABLED` | `ALERT_AGENT_ALWAYS_ON=false` / `alert_agent.always_on: false` (verification / 2d_cv) | Redeploy alerts with `-m real-time` so `dev-profile.sh` sets `ALERT_AGENT_ALWAYS_ON=true` and uncomments `VST_NOTIFICATION_CONFIG_PATH`, or confirm `generated.env` has those values and the verifier config substitutes the gate. |
| Name conflict `/vss-alert-bridge already in use` / port `9080` in use | `container_name` and host port are singleton under `network_mode: host` | Tear down the prior generation's `vss-alert-bridge` before bring-up (Step 6.5 Patch 0 orphan-container check). |
| (`cv-verification`) `vss-rtvi-cv` exits: `mkdir: cannot create directory '/opt/engines/gdino': Permission denied` | The host `${VSS_APPS_DIR}/engines/` bind-mounted at `/opt/engines/` was created root-owned / non-writable; RT-CV builds TensorRT engines into `/opt/engines/{gdino,rtdetr-its}` at first run | Pre-create + world-write the engine dirs **before** bring-up: `mkdir -p ${VSS_APPS_DIR}/engines/{gdino,rtdetr-its} && chmod -R 777 ${VSS_APPS_DIR}/engines`. Mirrors `deploy/docker/scripts/dev-profile.sh` (alerts branch, lines 1435–1443). See `patch-alerts.md § cv-verification host-prep`. |
| (`cv-verification`) `vss-rtvi-cv` fails loading the GDINO/RT-DETR ONNX, or detections never appear | ds-start phase 0 failed to download the detector models — missing `NGC_CLI_API_KEY`, unwritable `${VSS_DATA_DIR}/models`, or NGC network error | Confirm `NGC_CLI_API_KEY` is exported, `mkdir -p ${VSS_DATA_DIR}/models && chmod -R 777 ${VSS_DATA_DIR}/models`, restart `vss-rtvi-cv`, and check its logs for phase-0 download output. |
| `vss-agent` / `vss-va-mcp` exits: config / template file not found | A generated `.env` rewrote `VSS_AGENT_CONFIG_FILE` / `VSS_VA_MCP_CONFIG_FILE` / `VSS_AGENT_TEMPLATE_PATH` to a **host-absolute** path; these must stay **container-relative** (`./deploy/docker/...`), resolved inside the container via the `${VSS_APPS_DIR}:/vss-agent/deploy/docker:ro` mount + `/vss-agent` workdir | Keep those three vars verbatim as `./deploy/docker/...` — do not prefix `${VSS_APPS_DIR}` (Finding F-I; see `patch-alerts.md` env-overrides + `env-file-enumeration.md § Do NOT absolutize container-relative path vars`). |

## Prerequisites

- **Driver / Container Toolkit:** required only for the GPU-bearing VLM peer, not for `alert-bridge` itself.
- **Docker / Compose:** Compose v2.36+ (the same `depends_on` strip requirement as the rest of VSS — undefined `required: false` peers are rejected at project-load).
- **API keys:** `NGC_CLI_API_KEY` (image pull). `NVIDIA_API_KEY` if the VLM peer uses a remote build.nvidia.com endpoint.
- **Free ports:** `9080` (REST realtime API) on the host interface.
- **Reachable peers:** Kafka `:9092`, Redis `:6379`, Elasticsearch `:9200`, VIOS/VST `:30888`, and a verification VLM (`:8018` RT-VLM or `:30082` NIM).
- **Network reachability:** `nvcr.io` for the image pull.

## Verify Deployment

```bash
# 1. Service health
curl -sf --connect-timeout 5 http://${HOST_IP}:9080/health && echo "alert-bridge OK"

# 2. (vlm-realtime) realtime rules endpoint reachable — empty array is success
curl -s http://${HOST_IP}:9080/api/v1/realtime | jq .

# 3. Verified records land in Elasticsearch after a detection
curl -sf "http://${HOST_IP}:9200/mdx-vlm-incidents/_count" | jq '.count'
```

## Tear Down

`docker compose ... --profile <flag> down` stops `alert-bridge`. Durable alert configs / prompts / realtime rules persist in Elasticsearch (`ab-*` indices) and are NOT removed by stopping the container; `down -v` removes Redis/ES volumes and therefore wipes dedup state and persisted rules. Warn the operator before using `-v`.
