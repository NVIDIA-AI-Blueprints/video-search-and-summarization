# Warehouse Blueprint Reference

Blueprint: VSS Warehouse — RT-DETR (2D) / Sparse4D (3D) / MV3DT (multi-view 3D tracking with BEV Fusion) perception + behavior analytics over multi-camera warehouse streams. Distinct from the core VSS profiles (`base`, `alerts`, `lvs`, `search`): it lives under `<repo>/deploy/docker/industry-profiles/warehouse-operations/` and is deployed from `<repo>/deploy/docker/` using the **three** env files `containers.env` + the warehouse `.env` + `generated.env`, with the compose file pair `-f compose.yml -f services/infra/compose-no-turn-tcp-relay.yml`.

The compose files ship **in-tree** in the `video-search-and-summarization` repo — no NGC compose bundle to download. App data supplies videos, playback, and calibration assets; RT-CV models are downloaded from versioned NGC model packages during ds-start phase 0 at perception startup. See [App Data](#app-data).

Work through **one path** under [Choose your path](#choose-your-path). Reference tables (variants, services, GPU layout, endpoints, artifacts) are in the top half; operational phases are in the bottom half.

---

## Deployment Variants

| Variant | `MODE` | `BP_PROFILE` | `SAMPLE_VIDEO_DATASET` | `NUM_STREAMS` | LLM | RTVI VLM |
|---|---|---|---|---|---|---|
| 2D Vision AI | `2d` | `bp_wh_kafka` or `bp_wh_redis` | `warehouse-loading-dock-3cams-synthetic` | 3 | none | none |
| 2D Vision AI with Agents | `2d` | `bp_wh` | `nv-warehouse-4cams` | 4 | `local` / `remote` / `none` | **always local** |
| 3D Vision AI | `3d` | `bp_wh_kafka` or `bp_wh_redis` | `warehouse-4cams-20mx20m-synthetic` | 4 | none | none |
| MV3DT Vision AI | `mv3dt` | `bp_wh_kafka` or `bp_wh_redis` | `warehouse-4cams-20mx20m-synthetic` | 4 | none | none |
| Warehouse Auto-Calibration | `2d` / `3d` / `mv3dt` | `bp_wh_auto_calib` | (same as mode default) | (same as mode default) | none | none |
| Standalone Auto-Calibration | any | n/a (standalone service list) | n/a | n/a | none | none |

`COMPOSE_PROFILES` is an explicit list of service-scoped Docker Compose **profile names** for the active variant. Each service carries its own `profiles: ["<service-profile-name>"]`, and the checked-in `overrides.env` template defines one `COMPOSE_PROFILES_WH_*` list per variant. Copy `overrides.env` to `generated.env`, apply the deployment overrides there, and point `COMPOSE_PROFILES` at the list matching `BP_PROFILE`, `MODE`, and the chosen deployment size. Do not invoke `blueprint-deploy.sh` from this skill. The `bp_wh` service list includes `rtvi-vlm` directly; warehouse does not use a separate `vlm_*` NIM slice.

## Deployment Size (Kafka/Redis Variants Only)

Applies to `bp_wh_kafka` and `bp_wh_redis` only (all modes: 2d, 3d, mv3dt).

> Minimal vs. extended is selected *only* by which `COMPOSE_PROFILES_WH_*` list `COMPOSE_PROFILES` points at (`…_MINIMAL` or not). This doc uses "minimal"/"extended" as shorthand for that choice; setting `MINIMAL_PROFILE` in `generated.env` changes nothing on its own.
>
> To genuinely deploy minimal, use this skill's path: set `COMPOSE_PROFILES` to the `…_MINIMAL` list in `generated.env` and bring the stack up with `docker compose` directly ([Lifecycle: Bring up](#lifecycle-bring-up)) — not through `blueprint-deploy.sh` or the launchable.

| Feature | Minimal (`…_MINIMAL` list) | Extended (plain list) |
|---|---|---|
| Perception (RT-DETR 2D / Sparse4D 3D) | ✅ | ✅ |
| Behavior Analytics | ✅ | ✅ |
| VST / NvStreamer / TURN server | ✅ | ✅ |
| Auto-Calibration | ❌ (use the auto-calibration variant) | ❌ (use the auto-calibration variant) |
| ELK (Elasticsearch/Logstash/Kibana) | ❌ | ✅ |
| Video Analytics API (`vss-video-analytics-api`, `VIDEO_ANALYTICS_API_HOST_PORT` 8081) | ❌ | ✅ |
| HAProxy ingress | ❌ | ✅ |
| Monitoring (`dcgm-exporter`, `prometheus`, `grafana`, plus `node-exporter` / `cadvisor`, which set no `container_name` and so run as `<COMPOSE_PROJECT_NAME>-node-exporter-1` / `-cadvisor-1`) | ❌ | ✅ for `2d` / `3d` — **not included in the MV3DT lists** |
| Bounding box overlays in VST | ❌ | ✅ (requires Elasticsearch) |

## Services Deployed

The selected warehouse variant boots the service set identified by `BP_PROFILE`, `MODE`, and deployment size. Only `BP_PROFILE=bp_wh` adds the agent, UI, and RTVI VLM to the warehouse CV pipeline. Perception, behavior analytics, nvstreamer, and most other services use the **same container names** in 2D and 3D — no `-2d` / `-3d` suffix.

**MV3DT naming — the `-mv3dt` suffix is not universal.** It comes from each service's own `container_name:`, not from which file defines the service. The deployed suffixed containers are exactly: `vss-vios-nvstreamer-mv3dt`, `vss-rtvi-cv-mv3dt`, `vss-configurator-mv3dt` (+ `-init`), `vss-behavior-analytics-mv3dt`, `vss-video-analytics-api-mv3dt`, `vss-kibana-init-mv3dt`, `vss-import-calibration-output-mv3dt`. Everything else in an MV3DT deployment keeps its unsuffixed name — including `vss-rtvi-cv-bev-fusion` (declared in `warehouse-mv3dt-app.yml`, which extends `services/rtvi/rtvi-cv/rtvi-cv-mv3dt/compose.yaml`) and `mosquitto` (defined in the shared `services/infra/compose.yml`, and referenced by `warehouse-mv3dt-app.yml` only via `depends_on`) — both are MV3DT-only in practice, since their profiles appear solely in the MV3DT Kafka/Redis lists. The VST stack, `vss-turnserver`, `kafka`/`redis`, and `vss-broker-health-check` are unsuffixed too.

### Warehouse CV core (2D and 3D variants)

| Container | Purpose |
|---|---|
| `vss-vios-nvstreamer` | Streams sample video files via RTSP |
| VST stack: `vss-vios-postgres`, `vss-vios-sensor`, `vss-vios-streamprocessing`, `vss-vios-ingress`, `sdr-controller` | Video ingestion, recording, stream management. `sdr-controller` (from `services/infra/sdrc/`) is the combined WDM controller + Envoy router on `:10000`; the old `vss-vios-sdr`, `vss-vios-mcp` and `vss-vios-envoy` containers no longer exist |
| `vss-turnserver` (+ `vss-turnserver-init`) | TURN / WebRTC relay for VST playback — in **every** warehouse service list |
| `vss-rtvi-cv` | DeepStream perception (RT-DETR for 2D, Sparse4D for 3D) |
| `vss-rtvi-cv-config-adaptor` | DeepStream config adaptor (3D only) |
| `vss-configurator` | Blueprint configurator — stream and hardware configs |
| `vss-configurator-2d-init` / `-3d-init` | One-shot **broker readiness gate**, despite the name — it polls Kafka/Redis and exits `0`; it renders no config |
| `vss-behavior-analytics` | Behavior analytics — ROI, tripwire, proximity events |
| `kafka` (`bp_wh`, `bp_wh_kafka`) | Message broker for CV metadata |
| `redis` | Deployed in **every** warehouse list — it backs `sdr-controller`, and is additionally the CV message broker when `STREAM_TYPE=redis` (`bp_wh_redis`) |
| `vss-broker-health-check` | Waits for broker readiness before starting dependent services |

One-shot init containers also appear in these lists and exit `0` when done: `sdrc-init-dirs`, `sdrc-render-config`, `sdrc-wdm-env-from-config`, `sdrc-wait-for-redis`, `sdrc-wait-for-workloads`, `sensor-bp-wait-bp-configurator`, `vss-kafka-topics`, `vss-elasticsearch-init`, `vss-kibana-init`, `vss-import-calibration-output`, and the per-mode `vss-configurator-<mode>-init` broker gate. In MV3DT the last three carry the suffix: `vss-kibana-init-mv3dt`, `vss-import-calibration-output-mv3dt`, `vss-configurator-mv3dt-init`. An `Exited (0)` here is success, not a failure.

> **There is no `vss-rtvi-cv-sdr` container.** Its service definition is commented out in `warehouse-3d-app.yml` and it appears in no `COMPOSE_PROFILES_WH_*` list. HAProxy still defines a `/perception-sdr` route pointing at that hostname, so that route answers 503 on warehouse deployments.

### MV3DT CV core (when `MODE=mv3dt` and `BP_PROFILE=bp_wh_kafka` or `bp_wh_redis`)

MV3DT adds MQTT-based cross-camera messaging and BEV Fusion on top of per-camera DeepStream perception. Only the containers listed in the MV3DT naming note above carry the `-mv3dt` suffix.

| Container | Purpose |
|---|---|
| `vss-vios-nvstreamer-mv3dt` | Streams sample video files via RTSP |
| VST stack: `vss-vios-postgres`, `vss-vios-sensor` (service `sensor-ms-mv3dt`), `vss-vios-streamprocessing`, `vss-vios-ingress`, `sdr-controller` | Video ingestion, recording, stream management. The VST containers keep their unsuffixed names in MV3DT — only the compose *service* names carry `-mv3dt` |
| `vss-turnserver` (+ `vss-turnserver-init`) | TURN / WebRTC relay for VST playback |
| `vss-rtvi-cv-mv3dt` | DeepStream perception (per-camera) |
| `vss-rtvi-cv-bev-fusion` | BEV Fusion — fuses per-camera detections into a unified 3D BEV frame. **CPU-only** (no GPU reservation); reads `mdx-raw` and writes `mdx-bev` |
| `mosquitto` | MQTT broker for cross-camera messaging between perception and BEV fusion |
| `vss-configurator-mv3dt` (+ `vss-configurator-mv3dt-init`) | Blueprint configurator — stream and hardware configs |
| `vss-behavior-analytics-mv3dt` | Behavior analytics — 3D spatial analytics |
| `kafka` (kafka variant) / `redis` (always; also the broker for `bp_wh_redis`) | Message broker for CV metadata and `sdr-controller` state |
| `vss-broker-health-check` | Waits for broker readiness before starting dependent services |

### Warehouse Auto-Calibration (select with `BP_PROFILE=bp_wh_auto_calib`)

Deploys only the minimum services needed for camera calibration — no perception, no behavior analytics, no agent stack. Set `BP_PROFILE=bp_wh_auto_calib`, choose `MODE=2d`, `3d`, or `mv3dt`, and select the matching `COMPOSE_PROFILES_WH_AUTO_CALIB_*` service list. This variant skips broker health check. It is the only warehouse variant that starts `vss-auto-calibration` and `vss-auto-calibration-ui`; regular `bp_wh`, `bp_wh_kafka`, and `bp_wh_redis` variants do not.

| Container | Purpose |
|---|---|
| `vss-vios-nvstreamer` / `vss-vios-nvstreamer-mv3dt` | Streams sample video files via RTSP |
| `vss-configurator` / `vss-configurator-mv3dt` | Blueprint configurator |
| `vss-auto-calibration` (+ `vss-auto-calibration-ui`) | Camera auto-calibration (`VSS_AUTO_CALIBRATION_HOST_PORT` 8010 / UI 5000) |
| VST stack (subset) + `redis` + `vss-turnserver` | Stream management for calibration |
| `vss-haproxy-ingress` | Included in all three `COMPOSE_PROFILES_WH_AUTO_CALIB_*` lists, though the auto-calibration UI has no ingress route — reach it on port 5000 |

### Agent + UI (only when `BP_PROFILE=bp_wh`)

| Container | Host port (`overrides.env`) |
|---|---|
| `vss-agent-ui` (Next.js, compose service `vss-ui`) | `VSS_UI_HOST_PORT` (default `3000`) |
| `vss-agent` | `VSS_AGENT_HOST_PORT` (default `8000`) |
| `vss-va-mcp` | `VSS_VA_MCP_HOST_PORT` (default `9901`) |
| `phoenix` (telemetry) | `PHOENIX_HOST_PORT` (default `6006`) |

### HAProxy ingress (conditional)

| Container | Port | Deployed when |
|---|---|---|
| `vss-haproxy-ingress` | `HAPROXY_HOST_PORT` (host, default `7777`) → `HAPROXY_PORT` (container, default `7777`) | `BP_PROFILE=bp_wh`; `BP_PROFILE=bp_wh_auto_calib`; or Kafka/Redis extended |

### Storage / observability (conditional)

| Container | Port | Deployed when |
|---|---|---|
| `elasticsearch` | `ELASTICSEARCH_HOST_PORT` (default `9200`) | `BP_PROFILE=bp_wh` (always — vss-agent storage), **or** kafka/redis extended (any mode — for `mdx-bev`, ELK, overlays, analytics API) |
| `kibana` / `logstash` / `vss-video-analytics-api` | `KIBANA_HOST_PORT` `5601` / — / `VIDEO_ANALYTICS_API_HOST_PORT` `8081` | Same condition as `elasticsearch` (MV3DT uses `vss-video-analytics-api-mv3dt`) |
| `dcgm-exporter`, `prometheus`, `grafana`, `node-exporter`, `cadvisor` | `9400` / `9090` / `GRAFANA_HOST_PORT` `35000` / `19100` / `18080` | `BP_PROFILE=bp_wh`, or **2D/3D** kafka/redis extended. The MV3DT service lists do not include monitoring. `node-exporter` and `cadvisor` set no `container_name` — in `docker ps` they appear as `<COMPOSE_PROJECT_NAME>-node-exporter-1` / `-cadvisor-1` |

> **`ELASTICSEARCH_MODE` is not read by the compose stack** — the same dead-knob trap as `MINIMAL_PROFILE`. `services/infra/compose.yml` always builds `Dockerfiles/elasticsearch.Dockerfile` (CPU); `elasticsearch-gpu.Dockerfile` exists but is referenced by nothing. Only `blueprint-deploy.sh` and the launchable validate the value and write it back. Leave it at `cpu`; setting `gpu` changes nothing on this skill's path.

> **3D / MV3DT `mdx-bev` index requires Elasticsearch — and ES is only deployed for kafka/redis in extended mode.** With a `…_MINIMAL` service list, the BEV-sync check cannot run because the index is never persisted.

### LLM + RTVI VLM (only when `BP_PROFILE=bp_wh`)

| Container | Port | When |
|---|---|---|
| LLM NIM — container name = `LLM_NAME_SLUG` (e.g. `nvidia-nemotron-nano-9b-v2`) | `LLM_PORT` (default `30081`) → container `8000` | `LLM_MODE=local`. The `COMPOSE_PROFILES_WH_2D` list ends in the token `llm_${LLM_MODE}_${LLM_NAME_SLUG}`, so `LLM_MODE=remote`/`none` simply selects a profile no local NIM carries |
| `vss-rtvi-vlm` (real-time VLM) | `RTVI_VLM_PORT` (default `8018`) → container `8000` | Always deployed for `BP_PROFILE=bp_wh` — `rtvi-vlm` is included directly in `COMPOSE_PROFILES_WH_2D` |
| `vss-alert-bridge` (compose service `alert-bridge`) | `ALERT_BRIDGE_HOST_PORT` (default `9080`) | Always deployed for `bp_wh` |

> **No VLM NIM container.** VSS has two VLM paths: a standalone **VLM NIM** (controlled by `VLM_MODE` / `VLM_NAME_SLUG`, used by base/alerts/lvs/search profiles) and an integrated **RTVI VLM** (`vss-rtvi-vlm`). The `bp_wh` warehouse variant uses **RTVI VLM only** — its service list includes the self-named `rtvi-vlm` profile, and `vss-agent` connects to it directly. Kafka/Redis and auto-calibration warehouse variants do not deploy a VLM. Because warehouse does not use the standalone VLM NIM path, keep `VLM_MODE=none` and `VLM_NAME_SLUG=none` in the active `generated.env`. There is no `vlm_*` slice in `COMPOSE_PROFILES`, so VLM NIM containers (e.g. `cosmos-reason2-8b` on port 30082) are never deployed.

## Perception Model

- **2D model:** RT-DETR with ResNet-50 backbone (`nvidia/tao/rtdetr_2d_warehouse:deployable_rn50_v1.0.2`) — the same package backs the MV3DT per-camera detector
- **3D model:** Sparse4D (depth-aware perception, requires 4-camera dataset)
- **MV3DT model:** Per-camera DeepStream perception + BEV Fusion (multi-view 3D tracking, fuses detections from multiple cameras into a unified BEV frame via MQTT)
- **Detects:** People, humanoid robots, forklifts, autonomous vehicles, warehouse equipment
- **Output (broker topic depends on mode):** **2D** — detections with tracked object IDs on `mdx-raw`. **3D** — Sparse4D publishes BEV frames directly to `mdx-bev`; `mdx-raw` stays empty, so do not use it to check whether 3D perception is alive. **MV3DT** — per-camera detections on `mdx-raw`, which `vss-rtvi-cv-bev-fusion` consumes and republishes as `mdx-bev`. Logstash indexes these into date-suffixed Elasticsearch indices (`mdx-bev-YYYY-MM-DD`), extended lists only

## GPU Layout

| Role | Device | Used by |
|---|---|---|
| RT-CV perception (DeepStream — RT-DETR for 2D, Sparse4D for 3D, per-camera MV3DT for mv3dt) — always local | `RT_CV_DEVICE_ID` (default: `0`) | All warehouse variants except `BP_PROFILE=bp_wh_auto_calib`. `vss-rtvi-cv-bev-fusion` takes no device id — it is CPU-only |
| RTVI VLM — always local | `RT_VLM_DEVICE_ID` (default: `1`) | `bp_wh` only |
| LLM NIM (dedicated) | `LLM_DEVICE_ID` (default: `2`) | `bp_wh` with `LLM_MODE=local` |

`LLM_MODE` accepts `local`, `remote`, or `none`. Only `MODE=2d` + `BP_PROFILE=bp_wh` uses anything other than `none`:
- `local` — LLM NIM on its own GPU (`LLM_DEVICE_ID`). Requires a sizing file at `services/nim/<LLM_NAME_SLUG>/hw-<HARDWARE_PROFILE>.env`; a missing one fails compose with an unhelpful "no such file". Sizing files exist only for a subset of profiles per model — `nvidia-nemotron-nano-9b-v2` ships `hw-H100`, `hw-L40S`, `hw-RTXPRO6000BW`, `hw-OTHER`.
- `remote` — point at an external OpenAI-compatible endpoint (no LLM NIM deployed). Set `LLM_BASE_URL` to the endpoint **root, without a trailing `/v1`** (e.g. `https://integrate.api.nvidia.com`) — `vss-agent/configs/config.yml` appends `/v1` itself, so including it yields a broken `/v1/v1`. Also set `LLM_MODEL_TYPE` (`nim` or `openai`), `LLM_NAME` to a model id the endpoint actually advertises, `NVIDIA_API_KEY` / `OPENAI_API_KEY`, and `LLM_NAME_SLUG=none` so no local NIM profile matches.
- `none` — no LLM, when `BP_PROFILE` is `bp_wh_kafka`, `bp_wh_redis`, or `bp_wh_auto_calib`

`overrides.env` ships `LLM_MODE=local` with `LLM_NAME=nvidia/nvidia-nemotron-nano-9b-v2`. Supported local models and their slugs: `nvidia/nvidia-nemotron-nano-9b-v2` → `nvidia-nemotron-nano-9b-v2`, `nvidia/NVIDIA-Nemotron-Nano-9B-v2-FP8` → `nvidia-nemotron-nano-9b-v2-fp8`, `nvidia/nemotron-3-nano` → `nemotron-3-nano`, `nvidia/llama-3.3-nemotron-super-49b-v1.5` → `llama-3.3-nemotron-super-49b-v1.5`, `openai/gpt-oss-20b` → `gpt-oss-20b`.

RTVI VLM has no equivalent mode setting — it is always deployed locally on `RT_VLM_DEVICE_ID` for `BP_PROFILE=bp_wh`. Keep `VLM_MODE=none` in `generated.env` because warehouse uses RTVI VLM instead of the standalone VLM NIM path.

## Access Points

**Prefer the HAProxy ingress (host port `7777`) when the selected service list includes it** — it gives a single browser-reachable origin and rewrites paths to internal services. `_MINIMAL` Kafka/Redis lists omit HAProxy, so use direct ports there. Routes confirmed against `deploy/docker/services/infra/haproxy/haproxy.cfg.template`.

### Via HAProxy ingress (`http://<EXTERNAL_IP>:<HAPROXY_HOST_PORT>` — default `<EXTERNAL_IP>:7777`)

| Path | Backend | Available when |
|---|---|---|
| `/` (catch-all) | `vss-ui` / `vss-agent-ui` (Next.js) | `BP_PROFILE=bp_wh` only; other ingress-enabled variants have no UI backend, so `/` returns 503 |
| `/vst`, `/vst/...` | `vst-ingress` | Any ingress-enabled warehouse variant — **VST is proxied**, this is the browser path to the VST UI |
| `/storage`, `/storage/...` | `vst-ingress` (compat rewrite → `/vst/storage/...`) | Any ingress-enabled warehouse variant |
| `/kibana`, `/kibana/...` | `kibana` | `BP_PROFILE=bp_wh`, or extended Kafka/Redis (any mode) |
| `/elasticsearch`, `.../...` | `elasticsearch` (path-stripped; `GET/HEAD/POST/OPTIONS` only, cluster-admin and bulk-mutating paths denied) | Same condition as `kibana` |
| `/video-analytics-api`, `.../...` | `vss-video-analytics-api` (path-stripped) | `BP_PROFILE=bp_wh`, or extended Kafka/Redis (any mode) |
| `/behavior-analytics`, `.../...` | `vss-behavior-analytics` | **Never** — the route is defined and the container runs, but `vss-behavior-analytics` publishes no HTTP listener (it is a broker consumer, and declares no ports), so `bk_behavior_analytics` never passes its `check` and every request logs `bk_behavior_analytics/<NOSRV>` and returns 503. Read behaviors from the `mdx-behavior` topic or the `mdx-behavior-*` Elasticsearch indices instead |
| `/rtvi-cv`, `.../...` | `vss-rtvi-cv` (path-stripped) | `BP_PROFILE=bp_wh`, or **2D/3D** extended Kafka/Redis. **Not MV3DT** — the backend resolves `${RTVI_CV_SERVICE_HOST:-vss-rtvi-cv}`, which no warehouse env file overrides, and `vss-rtvi-cv-mv3dt` is the one MV3DT service defining no unsuffixed compat alias (nvstreamer, configurator, behavior-analytics and video-analytics-api all do), so this route 503s there. Use the direct port `RTVI_CV_MV3DT_HOST_PORT` (default `9000`), or set `RTVI_CV_SERVICE_HOST=vss-rtvi-cv-mv3dt` in `generated.env` |
| `/rtvi-vlm`, `.../...` | `rtvi-vlm` (path-stripped) | `BP_PROFILE=bp_wh` only; 503 elsewhere |
| `/rtvi-embed`, `.../...` | `rtvi-embed` (path-stripped) | Never deployed by warehouse — always 503 |
| `/perception-sdr`, `.../...` | `vss-rtvi-cv-sdr` | **Never** — that container is not deployed by any warehouse list, so this route 503s |
| `/alert-bridge`, `.../...` | `alert-bridge` (path-stripped) | `BP_PROFILE=bp_wh` only |
| `/phoenix`, `.../...` | `phoenix` (path-stripped) | `BP_PROFILE=bp_wh` only |
| `/va-mcp`, `.../...` | `vss-va-mcp` | `BP_PROFILE=bp_wh` only |
| `/api`, `/api/...` | `vss-agent` | `BP_PROFILE=bp_wh` only |
| `/api/chat`, `.../...` | `vss-ui` (matched before `/api`) | `BP_PROFILE=bp_wh` only |
| `/chat`, `/static`, `/websocket` | `vss-agent` | `BP_PROFILE=bp_wh` only |

### Direct ports (diagnostics, or when no ingress is deployed)

| Service | URL | Available when |
|---|---|---|
| NvStreamer UI | `http://<HOST_IP>:31000` (`NVSTREAMER_HTTP_HOST_PORT`) | All warehouse variants — no HAProxy route |
| VST UI | `http://<HOST_IP>:30888/vst/` (`VST_INGRESS_HOST_PORT`) | All warehouse variants; prefer `/vst/` via HAProxy where it exists |
| Auto-Calibration UI | `http://<HOST_IP>:5000` (`VSS_AUTO_CALIBRATION_UI_HOST_PORT`) | Standalone `vss-auto-calibration,vss-auto-calibration-ui`, or `BP_PROFILE=bp_wh_auto_calib` — no HAProxy route |
| Auto-Calibration API | `http://<HOST_IP>:8010` (`VSS_AUTO_CALIBRATION_HOST_PORT`) | Same as above |
| Elasticsearch API | `http://<HOST_IP>:9200` (`ELASTICSEARCH_HOST_PORT`) | `BP_PROFILE=bp_wh`, or extended Kafka/Redis (any mode) |
| VSS Agent API (direct) | `http://<HOST_IP>:8000` (`VSS_AGENT_HOST_PORT`) | `BP_PROFILE=bp_wh` only (prefer `/api` via HAProxy) |
| Phoenix (direct) | `http://<HOST_IP>:6006` (`PHOENIX_HOST_PORT`) | `BP_PROFILE=bp_wh` only (prefer `/phoenix` via HAProxy) |
| Kibana (direct) | `http://<HOST_IP>:5601/kibana` (`KIBANA_HOST_PORT`) | `BP_PROFILE=bp_wh`, or extended Kafka/Redis (any mode); Kibana is served under the `/kibana` base path either way |
| Video Analytics API (direct) | `http://<HOST_IP>:8081` (`VIDEO_ANALYTICS_API_HOST_PORT`) | `BP_PROFILE=bp_wh`, or extended Kafka/Redis (any mode); prefer `/video-analytics-api` via HAProxy |
| Grafana | `http://<HOST_IP>:35000` (`GRAFANA_HOST_PORT`) | `BP_PROFILE=bp_wh`, or **2D/3D** extended Kafka/Redis — not in the MV3DT lists. No HAProxy route |
| SDR controller | `http://<HOST_IP>:10000` (`SDRC_PROXY_HOST_PORT`); controller `5003`, direct `8011`, Envoy admin `9902` | All warehouse variants |

> There is **no VST MCP container** (`vss-vios-mcp` was removed) — nothing listens on `8001`.

`EXTERNAL_IP` defaults to `${HOST_IP}` but should be set to the browser-reachable hostname/IP. On Brev, apply the [Brev secure link overrides](#brev-secure-link-overrides) in Phase 5 — the HAProxy ingress, agent, and UI all need `https`/`wss` on the secure-link domain. HAProxy first denies anything whose `Host` header is not in its `known_host` ACL (`VSS_PUBLIC_HOST[:VSS_PUBLIC_PORT]`, `EXTERNAL_IP`, `HOST_IP`, `localhost`, `127.0.0.1`, each with and without `:HAPROXY_PORT`) with a **404**, then routes matching traffic via the identical `h_main` ACL. A wrong `Host` header therefore looks like "every path 404s".

## Compose File Structure

Deployed from `<repo>/deploy/docker/` (the repo's compose root) using:
- `containers.env` — **first** `--env-file`; pins the first-party image registry/tags (`VSS_CONTAINER_REGISTRY`, `VSS_CONTAINER_TAG`, …). The compose files repeat these as inline `image:` defaults, so omitting it usually still boots — but any registry/tag override you make there is then silently ignored. Always pass it.
- `industry-profiles/warehouse-operations/.env` — profile-specific stable defaults
- `services/<service>/*.env` — shared service defaults loaded through compose include `env_file` entries
- `industry-profiles/warehouse-operations/overrides.env` — checked-in deployment/profile override defaults
- `industry-profiles/warehouse-operations/generated.env` — per-deploy working copy created from `overrides.env`; **last** `--env-file`, so it wins
- `compose.yml` — root top-level include (foundational, monitoring, vst, industry-profiles, etc.), which pulls in:
  - `industry-profiles/compose.yml` — industry sub-include
    - `industry-profiles/warehouse-operations/compose.yml` — warehouse sub-include
      - `industry-profiles/warehouse-operations/warehouse-2d-app/warehouse-2d-app.yml` — 2D app services
      - `industry-profiles/warehouse-operations/warehouse-3d-app/warehouse-3d-app.yml` — 3D app services
      - `industry-profiles/warehouse-operations/warehouse-mv3dt-app/warehouse-mv3dt-app.yml` — MV3DT app services
- `services/infra/compose-no-turn-tcp-relay.yml` — second `-f` overlay, applied on top of the tree above; it contains only a `turnserver` port override and includes nothing. Always pass it

## App Data

App data (sample videos, playback, and calibration assets) is **not** bundled with the repo. Pick one source:

| Source | When to use | `VSS_DATA_DIR` |
|---|---|---|
| `<repo>/data` | Quick start — drop assets into the repo's `data/` directory | `<repo>/data` |
| Custom local path | Existing dataset on a non-repo path (e.g. `/mnt/warehouse-data`) | user-provided path |
| NGC app-data resource | Reproducing the sample-video deployment | `<extract-dir>/vss-warehouse-app-data` — the **inner** directory (see [NGC app-data download](#ngc-app-data-download-optional)) |

Ask the user which source they want and whether they already have the assets on disk. Only run the NGC app-data download (next subsection) when they explicitly choose the NGC source. Perception models are independent of this choice and are downloaded by `ds-start.sh` phase 0 inside the perception container when a `models-download.json` manifest is mounted.

### NGC app-data download (optional)

| Artifact | NGC Resource | Local directory after extract |
|---|---|---|
| App data (videos, playback, calibration) | `nvstaging/vss-warehouse/vss-warehouse-app-data:v3.3.0-08052026` | `vss-warehouse-app-data_vv3.3.0-08052026/vss-warehouse-app-data/` — **this inner directory is `VSS_DATA_DIR`** |

`VSS_DATA_DIR` must be the directory that holds `videos/`, `playback/`, `models/` and `data_log/`, not its parent.

> **Create and permission `models/` and the `data_log/` subtree before bring-up — nothing creates them for you.** `elastic-data`, `elastic-logs` and `kafka-data` are bind-backed named volumes (`type: none, o: bind`) whose `device:` points inside `data_log/`, and Docker's local driver does **not** create a `device:` path — a missing subdirectory hard-fails the mount (`failed to mount local volume … no such file or directory`) on every variant that deploys Elasticsearch or Kafka. Docker *does* auto-create the short-syntax binds, but as `root:root 0755`, which containers still cannot write. So run this for **every** app-data source (repo `data/`, custom path, or the NGC bundle alike), even if the paths already exist:
>
> ```bash
> mkdir -p "$VSS_DATA_DIR"/models \
>   "$VSS_DATA_DIR"/data_log/{analytics_cache,calibration_toolkit,elastic/data,elastic/logs,kafka,redis/data,redis/log,nvstreamer/vst_data,vss_video_analytics_api}
> chmod -R 0777 "$VSS_DATA_DIR"/models "$VSS_DATA_DIR"/data_log
> ```
>
> Prefix both with `sudo` only if you do not own `$VSS_DATA_DIR`. Containers run as varying UIDs, which is why the mode is `0777`.
>
> `videos/` and `playback/` are **not** in the list: they are read-only inputs that come from the app data itself, and `mkdir` cannot substitute for missing content. `models/` is here because ds-start phase 0 *writes* into it — the `mkdir` is a no-op when the app data already ships it, but the `chmod` is not.

> **Org:** the bundle lives in the **`nvstaging`** org (team `vss-warehouse`). Set `NGC_CLI_ORG=nvstaging`, or just pass the fully-qualified `org/team/name:version` path as below. A `403 Access Denied` means the NGC key has no access to that org.

## Known Limitations

- Bounding box overlays do not appear with a `_MINIMAL` service list — Elasticsearch is required for overlay rendering. Metadata is available from the live Kafka/Redis stream only.
- Perception model for `warehouse-loading-dock-3cams-synthetic` is trained on synthetic data — accuracy may vary on custom real-world scenes.
- `nv-warehouse-4cams` dataset is only valid with `BP_PROFILE=bp_wh` and `MODE=2d`.
- `warehouse-4cams-20mx20m-synthetic` dataset is valid with `MODE=3d` or `MODE=mv3dt`.
- MV3DT mode (`MODE=mv3dt`) does not support `BP_PROFILE=bp_wh` (agents) — use `bp_wh_kafka`, `bp_wh_redis`, or `bp_wh_auto_calib`.
- The `BP_PROFILE=bp_wh`, `MODE=2d` variant is not supported on IGX-THOR or DGX-SPARK.

---

## Choose your path

| Goal | Where to start |
|------|----------------|
| **New machine / first install** | [Full deploy (Phases 1-9)](#full-deploy-phases-1-9). Run phases in order; each must pass before the next. |
| **Redeploy** (`generated.env` change, clean restart, broken stack) | [Redeploy](#redeploy). Skips Phases 1–4 — host is already set up and artifacts exist. |
| **Tear down only** (containers, network and volumes; **also wipes most of `$VSS_DATA_DIR/data_log/`** — kafka, elastic, redis, VST/nvstreamer recordings, calibration output; `analytics_cache` is left alone — **and** deletes rendered `sdrc/configs` files plus every `*.backup_*` under `$VSS_DATA_DIR`, `deploy/docker` and `$VSS_APPS_DIR`. `videos/`, `playback/` and `models/` are kept) | [Lifecycle: Tear down](#lifecycle-tear-down). |

**`<repo>`** — path to your `video-search-and-summarization` checkout. All compose commands run from `<repo>/deploy/docker/` with this file/env-file set:

```
-f compose.yml -f services/infra/compose-no-turn-tcp-relay.yml
--env-file containers.env
--env-file industry-profiles/warehouse-operations/.env
--env-file industry-profiles/warehouse-operations/generated.env
```

If `generated.env` does not exist yet, initialize it from `overrides.env` before editing. If you don't know the repo path, **ask explicitly** before running shell commands.

---

## Lifecycle (shared)

Use these sections for **redeploy**, **Phase 8–9**, and **tear down**. Default log file for bring up and monitor:

```bash
LOG=${LOG:-/tmp/warehouse-blueprint.log}
```

<a id="resolve-env"></a>
### Lifecycle: Resolve env

Run this **once per shell**, from `<repo>/deploy/docker`, before any `docker compose` command
below. Everything that follows refers back to it as **the resolve-env prelude**.

```bash
eval "$(
  set -a
  . industry-profiles/warehouse-operations/.env
  . industry-profiles/warehouse-operations/generated.env
  set +a
  printf 'COMPOSE_PROFILES=%q\nCOMPOSE_PROJECT_NAME=%q\n' \
    "$COMPOSE_PROFILES" "${COMPOSE_PROJECT_NAME:-}"
  # Emit the NGC key ONLY if the env files actually carry one — overrides.env ships
  # NGC_CLI_API_KEY='' , and exporting that empty value would wipe a key you already
  # have in your shell and hand `docker login` a zero-byte password.
  [ -n "${NGC_CLI_API_KEY:-}" ] && printf 'NGC_CLI_API_KEY=%q\n' "$NGC_CLI_API_KEY"
)"
export COMPOSE_PROFILES
[ -n "${COMPOSE_PROJECT_NAME:-}" ] && export COMPOSE_PROJECT_NAME
[ -n "${NGC_CLI_API_KEY:-}" ] && export NGC_CLI_API_KEY

# Fail loudly rather than deploying an empty stack: an unresolved COMPOSE_PROFILES
# matches no service profiles, so `up` would start almost nothing and still exit 0.
case "$COMPOSE_PROFILES" in
  ''|*'${'*) echo "COMPOSE_PROFILES did not resolve: '$COMPOSE_PROFILES'" >&2; exit 1 ;;
esac
```

Two reasons it is a subshell rather than a plain `set -a; . file`:

- The warehouse `.env` holds an **unquoted JSON** value that shell quote-removal mangles, and the
  shell environment outranks `--env-file` in Compose interpolation — so the mangled value would
  silently win over the correct one. Only the three variables above are exported.
- `overrides.env` (and therefore `generated.env`) ships `NGC_CLI_API_KEY=''`. Sourcing it wholesale
  **overwrites a key already exported in your shell**, and `docker login --password-stdin` then
  receives zero bytes. The guard above keeps whichever value is actually set.

### Lifecycle: Tear down

Hard teardown — removes all containers, the project network, and all volume belonging to this stack.

```bash
cd <repo>/deploy/docker

# Hard teardown — `-v` ensures named volumes are also removed.
# Containers + network + project's named volumes all go.
# The project name comes from COMPOSE_PROJECT_NAME in generated.env (default `vss`),
# which Compose reads out of the --env-file below — so do not drop those flags here.
# If you prefer the project-scoped form, run the resolve-env prelude first, then:
#   docker compose -p "${COMPOSE_PROJECT_NAME:?}" down -v --remove-orphans
docker compose -f compose.yml -f services/infra/compose-no-turn-tcp-relay.yml \
  --env-file containers.env \
  --env-file industry-profiles/warehouse-operations/.env \
  --env-file industry-profiles/warehouse-operations/generated.env \
  down -v --remove-orphans

# Sweep any leftover anonymous/dangling volumes from prior partial runs.
docker volume prune -f

# Reclaim disk: stopped containers, dangling images, unused networks.
docker system prune -f

# Wipe bind-mounted state under $VSS_DATA_DIR/data_log/* AND revert
# blueprint-configurator backups. Resolves VSS_DATA_DIR from generated.env.
bash ./scripts/cleanup_all_datalog.sh -e industry-profiles/warehouse-operations/generated.env
```

### Lifecycle: Bring up

Pulls images and builds the perception container (~10–15 min first run). If `docker compose` fails to pull from `nvcr.io`, confirm `NGC_CLI_API_KEY` is set and retry `docker login` as shown.

Run the [resolve-env prelude](#resolve-env) in this shell first.

```bash
LOG=${LOG:-/tmp/warehouse-blueprint.log}
cd <repo>/deploy/docker

# Run the resolve-env prelude first — see Lifecycle: Resolve env.

: "${NGC_CLI_API_KEY:?not set — export it, or put it in generated.env, before logging in}"
printf '%s' "$NGC_CLI_API_KEY" | docker login --username '$oauthtoken' --password-stdin nvcr.io

nohup docker compose -f compose.yml -f services/infra/compose-no-turn-tcp-relay.yml \
  --env-file containers.env \
  --env-file industry-profiles/warehouse-operations/.env \
  --env-file industry-profiles/warehouse-operations/generated.env \
  up --detach --pull always --force-recreate --build \
  > "$LOG" 2>&1 &
echo "Compose PID $! — logging to $LOG"
```

> **`--pull always` is intentional.** `containers.env` defaults to the moving tag `develop-latest`,
> so without it a redeploy silently reuses whatever was pulled the first time — the stale-image
> trap. Drop it only when you deliberately want the images already on the host (air-gapped host,
> or reproducing a known-good local state).

### Lifecycle: Monitor

Poll every ~60s:

```bash
LOG=${LOG:-/tmp/warehouse-blueprint.log}
tail -20 "$LOG"
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
```

**Stack is ready when these long-running containers show `Up`** (same container names in 2D and 3D; in MV3DT only the containers named in the [MV3DT naming note](#services-deployed) carry `-mv3dt`). The one-shot jobs listed at the end are expected to be `Exited (0)` — do not read a completed job as a missing container:

- 2D / 3D Kafka/Redis variants: `vss-vios-nvstreamer`, `vss-rtvi-cv`, `vss-configurator`, `vss-behavior-analytics`, `kafka` and/or `redis`, `vss-turnserver`, plus the VST stack (`vss-vios-postgres`, `vss-vios-sensor`, `vss-vios-streamprocessing`, `vss-vios-ingress`, `sdr-controller`)
- 3D extra: `vss-rtvi-cv-config-adaptor`
- MV3DT Kafka/Redis variants: `vss-vios-nvstreamer-mv3dt`, `vss-rtvi-cv-mv3dt`, `vss-rtvi-cv-bev-fusion`, `mosquitto`, `vss-configurator-mv3dt`, `vss-behavior-analytics-mv3dt`, broker, `vss-turnserver`, plus the same VST stack
- `bp_wh` extra: `vss-rtvi-vlm`, `vss-alert-bridge`, `vss-agent`, `vss-agent-ui`, `vss-va-mcp`, `vss-haproxy-ingress`, `phoenix`, monitoring (`grafana`, `prometheus`, `dcgm-exporter`, plus `<project>-node-exporter-1` / `<project>-cadvisor-1`), plus the LLM NIM container (named after `LLM_NAME_SLUG`) when `LLM_MODE=local`
- Extended extra (kafka/redis): `vss-haproxy-ingress`; monitoring in 2D/3D only
- `elasticsearch`, `logstash`, `kibana`, `vss-video-analytics-api` (MV3DT uses `vss-video-analytics-api-mv3dt`): `BP_PROFILE=bp_wh` (always), **or** kafka/redis extended (any mode)
- `BP_PROFILE=bp_wh_auto_calib`: only nvstreamer, configurator, auto-calibration (+ UI), `vss-haproxy-ingress`, `vss-turnserver`, `redis` and a VST subset — no broker health check, no perception, no analytics
- **Expected `Exited (0)`, not `Up`:** `vss-broker-health-check` (the broker gate — it polls, exits, and releases its dependents via `service_completed_successfully`), plus `sdrc-*`, `*-init`, `vss-kafka-topics`, `sensor-bp-wait-bp-configurator` and `vss-import-calibration-output`. A non-zero exit on any of these *is* a finding; `Exited (0)` is not

Check FPS (same container for 2D/3D; use `vss-rtvi-cv-mv3dt` for MV3DT):

```bash
# 2D / 3D:
docker logs --since 60s vss-rtvi-cv 2>&1 | grep -aE "stream_name" | tail -8
# MV3DT:
docker logs --since 60s vss-rtvi-cv-mv3dt 2>&1 | grep -aE "stream_name" | tail -8
```

Expect one line per stream, at roughly the source framerate:

```
29.80000 (30.00634)	source_id : 3 stream_name Camera_01
```

> **Do not grep for `fps`.** DeepStream prints only a *header* line containing that
> string — `**PERF:  FPS 3 (Avg)	FPS 2 (Avg)	FPS 1 (Avg)	FPS 0 (Avg)` — and the numeric
> per-stream lines do not contain `fps` at all. `grep -i fps` therefore returns header
> rows with no values in them, which reads as "FPS present" no matter how badly
> perception is doing. Match `stream_name` instead.
>
> The header is still useful for one thing: the number of `FPS N` columns is the live
> source count, so it changes as streams attach.

**Confirm the stream count matches `NUM_STREAMS`** — a short count is the signature of a
partial stream registration ([Key Log Patterns](warehouse-debug.md#key-log-patterns-and-root-causes)),
and every container can be healthy while it happens:

```bash
docker logs --since 60s vss-rtvi-cv 2>&1 | grep -a "Active sources" | tail -1
```

---

## Redeploy

**When to use:** The machine already satisfies [Phase 2](#phase-2-system-prerequisites); the repo is checked out and `VSS_DATA_DIR` is populated. You edited the warehouse `generated.env`, need a clean restart, or are recovering a bad state.

**Do not** re-run NGC CLI install, driver install, or NGC app-data download unless something is actually missing or broken.

1. Obtain **`<repo>`** path (ask if unknown — see [Choose your path](#choose-your-path)).
2. Run **[Lifecycle: Tear down](#lifecycle-tear-down)**.
3. Run **[Lifecycle: Bring up](#lifecycle-bring-up)** (same `LOG` as monitor).
4. Run **[Lifecycle: Monitor](#lifecycle-monitor)**.

---

## Full deploy (Phases 1-9)

Work through phases in order; each must pass before moving to the next.

### Phase 1: NGC CLI

#### 1.1 Check

```bash
ngc --version
echo "NGC_CLI_API_KEY: ${NGC_CLI_API_KEY:+SET}${NGC_CLI_API_KEY:-NOT SET}"
ngc config current 2>/dev/null | grep -q "apikey" && echo "NGC config: key present" || echo "NGC config: no key"
```

Both set → skip to Phase 2.

#### 1.2 Install (NGC CLI 4.10.0+)

See [`ngc.md` § Install NGC CLI](ngc.md#install-ngc-cli-if-missing) for the
AMD64 / ARM64 install commands. They are kept in `ngc.md` as the single
canonical reference.

#### 1.3 Configure API Key

Generate and export the key as in [`ngc.md` § Configure NGC API Key](ngc.md#configure-ngc-api-key) — the same `read -rs` handoff and security guidance apply. Or configure interactively: `ngc config set`.

> **Important:** NGC API keys may look like base64. Use the key exactly as provided — **do not base64-decode it.**

#### 1.4 Verify NGC Access

Warehouse first-party images resolve to **three different roots**, set in `containers.env` — an NGC check alone does not prove you can pull everything:

| `containers.env` variable | Default root | Used by |
|---|---|---|
| `VSS_CONTAINER_REGISTRY` | `ghcr.io/nvidia-ai-blueprints/vss` | agent, agent-ui, alert-ms, video-analytics-api, behavior-analytics, video-summarization |
| `VSS_CONTAINER_RELEASE_REGISTRY` | `nvcr.io/nvidia/vss-core` | configurator, rt-config-adaptor |
| `VSS_CONTAINER_STAGING_REGISTRY` | `nvcr.io/nvstaging/vss-core` | nvstreamer, auto-calibration (+ UI) |

Third-party and NIM images (`nvcr.io/nim/*`, postgres, redis, kafka, cadvisor, …) come from their own registries.

The authoritative check is to resolve the images Compose will actually use and confirm each is pullable, rather than listing one org:

```bash
# From <repo>/deploy/docker, after generated.env exists (see Phase 5):
docker compose -f compose.yml -f services/infra/compose-no-turn-tcp-relay.yml \
  --env-file containers.env \
  --env-file industry-profiles/warehouse-operations/.env \
  --env-file industry-profiles/warehouse-operations/generated.env \
  config --images | sort -u
```

For a quick NGC-side credential smoke test before that:

```bash
ngc registry image list "nvidia/vss-core/*" 2>&1 | head -10
```

**`Missing org` error** → run `ngc config set` (or write `~/.ngc/config` directly) and match the org to the one used when generating the key. Run `ngc org list` to see which orgs the current key has access to before guessing. GHCR images need no NGC credentials; a `403` on an `nvcr.io/nvstaging/...` image means the key lacks staging access.

---

### Phase 2: System Prerequisites

**Detect if this is a Brev-managed instance first:**

```bash
grep "BREV_ENV_ID" /etc/environment && echo "Brev instance — apply Brev-specific steps" \
  || echo "Not Brev — standard deployment"
```

If `BREV_ENV_ID` is present, also complete [§2.7 Brev-specific host setup](#27-brev-specific-host-setup-brev-deployments-only) below, and apply the [Brev Secure Link Overrides](#brev-secure-link-overrides) in Phase 5. No post-deploy Brev steps are required. For Brev architecture and secure-link troubleshooting, see [`brev.md`](brev.md) — warehouse uses the same generated-env pattern, with overrides written to `industry-profiles/warehouse-operations/generated.env`.

Run each check in order. **If a check fails, automatically install and re-verify — do not wait for the user.** Only stop if a requirement cannot be met automatically (unsupported hardware, insufficient RAM/CPU).

#### Supported Hardware

`HARDWARE_PROFILE` is a **blueprint setting**, not a string that `nvidia-smi` always prints verbatim. For **discrete GPUs**, match the GPU model from `nvidia-smi` / `lspci` to a row below. **IGX-THOR** and **DGX-SPARK** are **whole-system platforms** (kits/boards): set the profile from product/SKU or vendor docs if you already know the machine type; `nvidia-smi` shows the **on-board NVIDIA GPU name** (e.g. a Thor-class or Spark system GPU), not the text `IGX-THOR` or `DGX-SPARK`. On **DGX Spark**, unified memory can make some `nvidia-smi` memory fields show **Not Supported**; driver and device listing should still be checked per [DGX Spark user guide](https://docs.nvidia.com/dgx/dgx-spark/).

The profiles that actually carry perception tuning are the top-level sections of
`industry-profiles/warehouse-operations/blueprint-configurator/blueprint_config.yml`:
`H100, L4, L40S, RTXA6000, RTXA6000ADA, RTXPRO6000BW, RTXPRO6000BW-SE, RTXPRO4500BW, IGX-THOR,
DGX-SPARK`.
All of these define `max_streams_supported` for `2d`, `3d` and `mv3dt` **except `RTXPRO4500BW`,
which is tuned for `2d` (20) and `3d` (9) only**. The `overrides.env` comment does not match that set exactly — it
lists `L40`, which has no section, and omits `RTXA6000ADA`, which has one. A profile with no
section falls back to `NUM_STREAMS` and still gets the commons DeepStream/VST configuration; only
the profile-specific stream cap and per-profile tuning are skipped.

| Discrete GPU (typical `nvidia-smi` name) | HARDWARE_PROFILE |
|---|---|
| RTX PRO 6000 Blackwell | `RTXPRO6000BW` |
| RTX PRO 6000 Blackwell Server Edition | `RTXPRO6000BW-SE` — capped at 47 streams for 2D, 20 for 3D, and 39 for mv3dt. No `hw-RTXPRO6000BW-SE.env` ships for any LLM NIM, so `LLM_MODE=local` needs `HARDWARE_PROFILE=OTHER` or a new sizing file. |
| RTX PRO 4500 Blackwell | `RTXPRO4500BW` (32 GB) — 2D and 3D only, capped at 20 streams for 2D and 9 for 3D. When `COMPOSE_PROFILES=${COMPOSE_PROFILES_WH_2D}` deploys `vss-rtvi-vlm`, set `RTVI_VLM_MAX_MODEL_LEN=18000` to cap RT-VLM context and allow KV-cache allocation. |
| H100 (NVL, SXM HBM3) | `H100` |
| RTX A6000 Ada Generation | `RTXA6000ADA` |
| RTX A6000 (Ampere) | `RTXA6000` |
| L40S | `L40S` |
| L4 | `L4` |
| Platform: NVIDIA IGX Thor (kit / board) | `IGX-THOR` |
| Platform: NVIDIA DGX Spark | `DGX-SPARK` |

`HARDWARE_PROFILE=DGX-SPARK` also *requires* an SBSA-tagged `VSS_RT_CV_TAG` — the configurator
rejects the deployment otherwise (see the DGX-SPARK note in Phase 5).

> **Do NOT use a higher profile on lower-profile hardware** (e.g. `H100` on an `L4`) — the env file warns against this directly.

**GPUs not in the list above:** the warehouse blueprint may not have a tuned profile. Pick the closest match from the table or treat the deployment as unsupported on that GPU until the upstream list adds it.

#### 2.1 GPU Detection and NVIDIA Driver

**Detect GPUs and driver:**

```bash
nvidia-smi --query-gpu=index,name,driver_version,memory.total --format=csv,noheader
```

Use the **`name`** column to pick **`HARDWARE_PROFILE`** from the [Supported Hardware](#supported-hardware) list. For **IGX-THOR** or **DGX-SPARK**, set `HARDWARE_PROFILE` to that value when the deployment target is that platform, even though `name` will be a GPU part name, not `IGX-THOR` / `DGX-SPARK`.

`HARDWARE_PROFILE` is **not** validated against a list — `blueprint_config.yml` has no `allowed_values` for it, so an unrecognized string is accepted and simply matches no tuning section (see [above](#supported-hardware)).

Two independent things key off the value, and they do **not** cover the same set:

| | Sections that exist |
|---|---|
| Perception tuning (`blueprint_config.yml`) | `H100`, `L4`, `L40S`, `RTXA6000`, `RTXA6000ADA`, `RTXPRO6000BW`, `RTXPRO6000BW-SE`, `RTXPRO4500BW`, `IGX-THOR`, `DGX-SPARK` — **no `OTHER`** |
| LLM NIM sizing (`services/nim/<slug>/hw-<PROFILE>.env`) | Per model. Every model ships `hw-OTHER.env`; coverage of the named profiles is patchy |

So `OTHER` is a safe fallback for the **NIM sizing** half only — it still matches no tuning section, exactly like any unrecognized string.

Three ways `HARDWARE_PROFILE` hard-fails a deploy:

1. `BP_PROFILE=bp_wh` with `IGX-THOR` or `DGX-SPARK` — explicitly disallowed by the configurator.
2. `HARDWARE_PROFILE=DGX-SPARK` without an `sbsa`-tagged `VSS_RT_CV_TAG` — enforced in all three modes.
3. `LLM_MODE=local` when the selected model has no `hw-<HARDWARE_PROFILE>.env` — compose dies with an unhelpful "no such file". **This bites listed, tuned profiles too:** the default `nvidia-nemotron-nano-9b-v2` ships only `hw-H100`, `hw-L40S`, `hw-RTXPRO6000BW` and `hw-OTHER`, so `HARDWARE_PROFILE=L4` (or `RTXA6000`, `RTXA6000ADA`, `RTXPRO6000BW-SE`, `RTXPRO4500BW`, `IGX-THOR`, `DGX-SPARK`) fails with that model. Check `ls services/nim/<slug>/hw-*.env` before choosing `LLM_MODE=local`.

**Required driver versions:** see the canonical per-platform pins in [`prerequisites.md` § 1 GPU Detection](prerequisites.md#1-gpu-detection) and [§ Canonical version matrix](prerequisites.md#canonical-version-matrix) — that table also covers Ubuntu 22.04 and AGX-THOR, which the warehouse profile does not restrict. On x86 Ubuntu 24.04 the pin is **`580.105.08`**.

##### Install NVIDIA Driver (Ubuntu 24.04)

On **Ubuntu 24.04**, install **NVIDIA Driver 580.105.08**. Do not substitute an unpinned `nvidia-driver-580` unless it resolves to that exact build.

- **Download (580.105.08):** https://www.nvidia.com/en-us/drivers/details/257738/
- **Installation guide:** https://docs.nvidia.com/datacenter/tesla/driver-installation-guide/index.html
- **Driver search by GPU/platform:** https://www.nvidia.com/Download/index.aspx

If `nvidia-smi` fails → driver missing or wrong version. Detect hardware automatically — **do not ask the user what GPU they have**:

```bash
lspci | grep -i nvidia
```

Install matching kernel headers, then install the driver per the guides above (runfile or repository pin to **580.105.08** on Ubuntu 24.04). Example prep for apt-based installs:

```bash
sudo apt-get update
sudo apt-get install -y linux-headers-$(uname -r)
```

After installation, load the module if needed and verify:

```bash
sudo modprobe nvidia
nvidia-smi --query-gpu=index,name,driver_version,memory.total --format=csv,noheader
```

If `modprobe` exits non-zero, retry `nvidia-smi` anyway — modules may already be loaded. If `nvidia-smi` still fails, check loaded modules and retry:

```bash
lsmod | grep nvidia
nvidia-smi --query-gpu=index,name,driver_version,memory.total --format=csv,noheader
```

If it still fails → reboot (`sudo reboot`), then re-run the `nvidia-smi` query above.

**Verify:** `nvidia-smi` must report driver version **580.105.08** on Ubuntu 24.04 and list the GPU(s) correctly.

##### NVIDIA Fabric Manager (when required)

> **Single-GPU systems: SKIP THIS SECTION ENTIRELY.** Fabric Manager is not needed and `nvidia-fabricmanager-580` may even fail to install because it depends on `nvidia-kernel-common-580-server-*` (the server variant of the driver), which conflicts with the standard `nvidia-driver-580` you just installed. If you have one GPU and aren't on an NVLink/NVSwitch system, do not install Fabric Manager.

Fabric Manager is required only on systems where multiple GPUs are connected via **NVLink** or **NVSwitch** (e.g. DGX multi-GPU, HGX baseboards, NVSwitch servers, multi-GPU NVLink topologies, datacenter GPUs in NVLink layouts). It is **not** required for single-GPU systems or multi-GPU **PCIe-only** setups without NVLink/NVSwitch.

Docs: https://docs.nvidia.com/datacenter/tesla/fabric-manager-user-guide/index.html

On **Ubuntu 24.04**, use Fabric Manager **580.105.08** to match the driver (package version typically tracks the driver):

```bash
sudo apt-get update
sudo apt-get install -y nvidia-fabricmanager-580=580.105.08-1
sudo systemctl enable nvidia-fabricmanager
sudo systemctl start nvidia-fabricmanager
sudo systemctl status nvidia-fabricmanager
```

If that exact apt version is unavailable, use the NVIDIA archive for 580.105.08: https://developer.download.nvidia.com/compute/nvidia-driver/redist/fabricmanager/linux-x86_64/fabricmanager-linux-x86_64-580.105.08-archive.tar.xz

#### 2.2 Docker

Tested Docker Engine range: **[28.3.3, 29.5.0)** — the same range the warehouse launchable notebook (`deploy/docker/scripts/deploy_warehouse_launchable.ipynb`) enforces. **If the installed engine is already in that range, do not downgrade it** — just proceed to §2.3. Re-pinning to an exact epoch-versioned package the host's apt repo may not carry (DGX Spark / DGX-OS on arm64) fails with *version not found* for no benefit.

```bash
docker version --format '{{.Server.Version}}'   # need >= 28.3.3 and < 29.5.0
docker compose version                          # plugin shipped with that engine
docker ps                                       # must run without sudo
```

Optionally freeze the in-range packages so unattended-upgrades cannot drift the host mid-deploy:

```bash
sudo apt-mark hold docker-ce docker-ce-cli docker-buildx-plugin docker-compose-plugin containerd.io
```

**Install / pin Docker (Ubuntu 24.04) — only when the engine is outside [28.3.3, 29.5.0) or absent:**

The pinned Docker CE packages come from Docker's official apt repository. If `apt` says `docker-ce` or `containerd.io` is unavailable, the Docker apt source is missing; add it first, then install the pinned versions.

```bash
# Remove conflicting distro packages if present. It is okay if apt says none are installed.
sudo apt-get remove -y docker.io docker-doc docker-compose docker-compose-v2 podman-docker containerd runc || true

# Add Docker's official apt repository.
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo rm -f /etc/apt/sources.list.d/docker.list
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
sudo tee /etc/apt/sources.list.d/docker.sources > /dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
sudo apt-get update

# Install or downgrade to the known-good combination (the set the notebook pins:
# CE 29.4.3, buildx 0.33.0, compose 5.1.3, containerd 2.2.3 — bump these four together).
. /etc/os-release
DISTRO="${VERSION_ID}"; CODENAME="${UBUNTU_CODENAME:-${VERSION_CODENAME}}"
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --allow-downgrades \
  -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold \
  docker-ce="5:29.4.3-1~ubuntu.${DISTRO}~${CODENAME}" \
  docker-ce-cli="5:29.4.3-1~ubuntu.${DISTRO}~${CODENAME}" \
  docker-buildx-plugin="0.33.0-1~ubuntu.${DISTRO}~${CODENAME}" \
  docker-compose-plugin="5.1.3-1~ubuntu.${DISTRO}~${CODENAME}" \
  containerd.io="2.2.3-1~ubuntu.${DISTRO}~${CODENAME}"
sudo systemctl enable --now docker

# Hold so unattended-upgrades doesn't drift them back
sudo apt-mark hold docker-ce docker-ce-cli docker-buildx-plugin docker-compose-plugin containerd.io

docker version --format '{{.Server.Version}}'   # -> 29.4.3
docker compose version --short
```

##### When to re-pin Docker

Re-pin if the installed engine is **outside [28.3.3, 29.5.0)** — that is where compose/buildx
incompatibilities show up — or if you hit this failure during `docker compose up --pull always`:

```
error from registry: Incorrect Repository Format
```

Then re-run the bring-up command after the pinned install succeeds.

**Non-root Docker:**
```bash
sudo usermod -aG docker $USER
newgrp docker
sudo systemctl restart docker
```

**cgroupfs driver** — `/etc/docker/daemon.json` must contain `"exec-opts": ["native.cgroupdriver=cgroupfs"]`. If missing:
```bash
sudo bash -c 'cat > /etc/docker/daemon.json << EOF
{
    "exec-opts": ["native.cgroupdriver=cgroupfs"]
}
EOF'
sudo systemctl daemon-reload && sudo systemctl restart docker
```

#### 2.3 NVIDIA Container Toolkit

Canonical install + verify lives in [`prerequisites.md` § 3 NVIDIA Container Toolkit](prerequisites.md#3-nvidia-container-toolkit). Run that block and re-verify with `docker run --rm --gpus all ubuntu:24.04 nvidia-smi` before continuing.

#### 2.4 Linux Kernel Settings

```bash
sysctl net.ipv6.conf.all.disable_ipv6
sysctl net.core.rmem_max
sysctl vm.max_map_count
```

If not set — `vm.max_map_count` is required by Elasticsearch and Kafka, which every non-minimal warehouse variant deploys (canonical list: [`prerequisites.md` § Kernel Settings](prerequisites.md#kernel-settings)):
```bash
sudo mkdir -p /etc/sysctl.d
sudo bash -c "printf '%s\n' \
  'net.ipv6.conf.all.disable_ipv6 = 1' \
  'net.ipv6.conf.default.disable_ipv6 = 1' \
  'net.ipv6.conf.lo.disable_ipv6 = 1' \
  'net.core.rmem_max = 5242880' \
  'net.core.wmem_max = 5242880' \
  'net.ipv4.tcp_rmem = 4096 87380 16777216' \
  'net.ipv4.tcp_wmem = 4096 65536 16777216' \
  'vm.max_map_count = 262144' \
  > /etc/sysctl.d/99-vss.conf"
sudo sysctl --system
```

**DGX-SPARK / IGX-THOR / AGX-THOR only** — system cache cleaner and (IGX-Thor) VIC clock boost. These are platform prerequisites that apply to every profile on edge hardware, not just warehouse. Canonical install + verify block lives in [`edge.md` § Cache cleaner (every edge deploy)](edge.md#cache-cleaner-every-edge-deploy).

#### 2.5 IPv6 Localhost Entry

Both `/etc/hosts` and `/etc/cloud/templates/hosts.debian.tmpl` must use `localhost6` for the `::1` entry.

```bash
grep "^::1" /etc/hosts
grep "^::1" /etc/cloud/templates/hosts.debian.tmpl 2>/dev/null || echo "(template not present)"
```

Expected: `::1 localhost6 ip6-localhost ip6-loopback`

If it reads `::1 localhost ip6-localhost ip6-loopback`:
```bash
sudo sed -i 's/^::1 localhost ip6-localhost ip6-loopback/::1 localhost6 ip6-localhost ip6-loopback/' /etc/hosts
if [ -f /etc/cloud/templates/hosts.debian.tmpl ]; then
  sudo sed -i 's/^::1 localhost ip6-localhost ip6-loopback/::1 localhost6 ip6-localhost ip6-loopback/' \
    /etc/cloud/templates/hosts.debian.tmpl
fi
```

#### 2.6 Minimum System Resources

```bash
nproc    # 10+ cores (x86)
free -h  # 64 GB+ RAM
df -h /  # 500 GB+ SSD
```

Docker images and containerd layers alone need **~250 GB** (NIM models, DeepStream, ELK). If `/`
has less than ~350 GB free, relocate Docker's `data-root` and containerd's root to a larger mount
before deploying rather than running out of space mid-pull.

#### 2.7 Brev-specific host setup (Brev deployments only)

These steps are required on any Brev-provisioned instance and are not covered by the standard system prerequisites above.

**UFW — allow Docker bridge networks to reach host services**

`vss-rtvi-vlm` runs on the Compose Docker bridge (`<project>_default`, default `vss_default`; commonly subnet `172.18.0.0/16`) and needs to reach host-network services (HAProxy, VST). UFW blocks this by default:

```bash
sudo ufw allow from 172.17.0.0/16
sudo ufw allow from 172.18.0.0/16
```

**CDI spec — regenerate both locations**

The NVIDIA Container Toolkit writes CDI specs to two paths. The `/var/run/cdi/` copy can be stale (referencing `/dev/dri/cardN` devices that don't exist on headless GPU instances), causing all GPU containers to fail to start with `failed to stat CDI host device`. Always regenerate both:

```bash
sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
sudo nvidia-ctk cdi generate --output=/var/run/cdi/nvidia.yaml
```

The generated specs also mount `/run/nvidia-persistenced/socket`, so that daemon must be running or GPU containers fail with the same error:

```bash
sudo systemctl start nvidia-persistenced
```

**`/etc/hosts` — resolve Brev domains locally**

Maps the Brev secure-link hostnames to `HOST_IP` so requests originating **on the host** — including your own `curl` verification — resolve locally instead of going out to the secure-link edge, which only accepts 443. This is the *host's* `/etc/hosts`; bridge-network containers have their own and are unaffected — and must stay that way, since they reach the same names correctly through the Brev edge:

```bash
HOST_IP=$(hostname -I | awk '{print $1}')
BREV_ENV_ID=$(awk -F= '/^BREV_ENV_ID=/{gsub(/"/, "", $2); print $2; exit}' /etc/environment)
echo "${HOST_IP} 7777-${BREV_ENV_ID}.brevlab.com" | sudo tee -a /etc/hosts
echo "${HOST_IP} 30888-${BREV_ENV_ID}.brevlab.com" | sudo tee -a /etc/hosts
```

---

### Phase 3: Interactive Configuration

**Work through the questions below before creating or editing `generated.env`.** Q3, Q4 and Q6 carry skip conditions, so the number actually asked depends on `BP_PROFILE`: 3 for `bp_wh_auto_calib`, 4 for `bp_wh`, 5 for `bp_wh_kafka` / `bp_wh_redis`.

#### Q1 — Deployment Mode

> "Which mode?
> - **2d** — 2D detection/tracking with **RT-DETR**, no depth
> - **3d** — 3D perception with depth using **Sparse4D**, requires 4-camera dataset
> - **mv3dt** — Multi-View 3D Tracking: per-camera DeepStream perception + **BEV Fusion** across cameras via MQTT, requires 4-camera dataset"

#### Q2 — Blueprint variant (`BP_PROFILE`)

Refer to the [Deployment Variants table](#deployment-variants) above for the
`BP_PROFILE` / mode / dataset matrix instead of restating it here. The question
is just "which deployment variant from that table?".

#### Q3 — Stream Type

Skip when `BP_PROFILE` is `bp_wh` or `bp_wh_auto_calib`. For
`BP_PROFILE=bp_wh_kafka` or `BP_PROFILE=bp_wh_redis`:

> "Which broker — **kafka** or **redis**?"

`STREAM_TYPE` follows `BP_PROFILE` mechanically: `redis` for `bp_wh_redis`,
`kafka` for `bp_wh`, `bp_wh_kafka` and `bp_wh_auto_calib` (`kafka` is the
`overrides.env` default). Never leave it empty. `SAMPLE_VIDEO_DATASET` and
`NUM_STREAMS` come from the [Deployment Variants table](#deployment-variants) —
note `3d` and `mv3dt` intentionally share a dataset and stream count, differing
only at the perception layer (Sparse4D vs per-camera DeepStream + BEV Fusion).

#### Q4 — Deployment size

Skip when `BP_PROFILE` is `bp_wh` or `bp_wh_auto_calib`. For
`BP_PROFILE=bp_wh_kafka` or `BP_PROFILE=bp_wh_redis` (any mode):

> "Which deployment size?
> - **minimal** — excludes ELK, Video Analytics API, HAProxy ingress and monitoring. Recommended for IGX-THOR.
> - **extended** — full deployment."

The answer only decides which service list `COMPOSE_PROFILES` points at in Phase 5 —
`COMPOSE_PROFILES_WH_<KAFKA|REDIS>_<2D|3D|MV3DT>_MINIMAL` for minimal, the same name without the
suffix for extended. There is no `MINIMAL_PROFILE` variable in the deployed env files.

#### Q5 — Data Source & Calibration

> "Are you using the **sample dataset** or your **own data** (custom videos / live RTSP streams)?"

**Sample dataset** — calibration files ship with the app data. No extra step needed; proceed to Phase 4.

**Own data** — you need a calibration file before the analytics pipeline can produce meaningful results.

> "Do you already have a calibration JSON file, or do you need to generate one first?"

- **Already have a calibration file** — there is **no calibration-path env var**. The mount is hardcoded per mode to `$VSS_APPS_DIR/industry-profiles/warehouse-operations/warehouse-<mode>-app/calibration/sample-data/$SAMPLE_VIDEO_DATASET/calibration.json`. The only thing you vary is `SAMPLE_VIDEO_DATASET`: create a directory of that name under the mode's `calibration/sample-data/`, drop your `calibration.json` in it, and set `SAMPLE_VIDEO_DATASET` to match in Phase 5. For MV3DT also place the `camInfo/` files and `pub_sub_info_config.yml` alongside it (see [MV3DT-specific configuration updates](#mv3dt-specific-configuration-updates)).
- **Need to generate a calibration file** — pick a calibration path based on your video source:

  | You have… | Deployment selector | What it does |
  |---|---|---|
  | **Video files on disk** | `COMPOSE_PROFILES=vss-auto-calibration,vss-auto-calibration-ui` | Standalone auto-calibration. Upload videos directly to the calibration UI — no nvstreamer, no VST stack needed. |
  | **Live RTSP streams** (or want to use nvstreamer) | `BP_PROFILE=bp_wh_auto_calib`, plus `MODE` and the matching `COMPOSE_PROFILES_WH_AUTO_CALIB_*` list | Warehouse auto-calibration. Calibrate against RTSP streams served by nvstreamer + VST stack. |

  Deploy the chosen calibration variant first, then generate the calibration JSON via the Auto-Calibration UI (`http://<HOST_IP>:5000`).

  > **Note:** Post-calibration cleanup depends on mode — 2D requires removing blank fields, 3D / MV3DT requires camera clustering. See [Calibration Generation](#calibration-generation).

  Once the calibration file is ready, redeploy with the selected non-calibration warehouse variant.

#### Q6 — LLM Placement (only when `BP_PROFILE=bp_wh`)

Skip when `BP_PROFILE` is `bp_wh_kafka`, `bp_wh_redis`, or `bp_wh_auto_calib` (set `LLM_MODE=none` for those).

For `BP_PROFILE=bp_wh`, **always ask explicitly** — do not default to `local`:

> "How should the LLM be deployed?
> - **local** — LLM NIM on its own GPU (`LLM_DEVICE_ID`, default `2`). Requires a third GPU.
> - **remote** — point at an external LLM endpoint via `LLM_BASE_URL` (e.g. `https://integrate.api.nvidia.com`). No LLM NIM deployed. Requires `NVIDIA_API_KEY` — log in to the [NVIDIA NIM API catalog](https://build.nvidia.com) and get a NIM Catalog API key.
> - **none** — disable LLM entirely."

`vss-rtvi-vlm` (RTVI VLM) is **always** deployed locally for `BP_PROFILE=bp_wh`.

```bash
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader
```

| GPU count | Recommended LLM mode |
|---|---|
| ≥ 3 GPUs | `local` — dedicated GPU for LLM NIM |
| 2 GPUs, RTVI VLM uses > 50 % of GPU 1 VRAM | `remote` — RTVI VLM leaves insufficient room for LLM NIM |
| 1 GPU | `remote` or `none` |

If the user chooses `remote`, also confirm `LLM_BASE_URL` and `NVIDIA_API_KEY` are set.

---

### Phase 4: Acquire App Data (first run only)

Compose files ship in the repo. Only non-model app data may need to be acquired manually, and only for the source the user chose in [App Data](#app-data). RT-CV model packages are downloaded automatically when the selected perception profile starts.

**Option A — `<repo>/data`:** ensure assets are present at `<repo>/data` and proceed to Phase 5 (`VSS_DATA_DIR=<repo>/data`).

**Option B — custom local path:** confirm the path exists and has the expected `videos/` and other app-data subdirs, then set `VSS_DATA_DIR=<that path>` in Phase 5.

**Option C — NGC `vss-warehouse-app-data`:**

```bash
export NGC_CLI_API_KEY='<your-ngc-api-key>'

export NGC_CLI_ORG=nvstaging

APP_DATA_RESOURCE="nvstaging/vss-warehouse/vss-warehouse-app-data:v3.3.0-08052026"
ngc registry resource download-version "$APP_DATA_RESOURCE"

# NGC prefixes _v to the version, which already starts with v -> doubled _vv.
cd vss-warehouse-app-data_vv3.3.0-08052026
tar -xvf vss-warehouse-app-data.tar.gz

# The inner directory is VSS_DATA_DIR. Do not use the literal string
# "/path/to/vss-warehouse-app-data" — blueprint_config.yml rejects it as a disallowed value.
export VSS_DATA_DIR="$PWD/vss-warehouse-app-data"

mkdir -p "$VSS_DATA_DIR"/models \
  "$VSS_DATA_DIR"/data_log/{analytics_cache,calibration_toolkit,elastic/data,elastic/logs,kafka,redis/data,redis/log,nvstreamer/vst_data,vss_video_analytics_api}
chmod -R 0777 "$VSS_DATA_DIR"/models "$VSS_DATA_DIR"/data_log
```

`VSS_DATA_DIR` is then `vss-warehouse-app-data_vv3.3.0-08052026/vss-warehouse-app-data` — the **inner** directory. The bundle supplies the sample `videos/` and `playback/` assets.

`v3.3.0-08052026` extracts to exactly `agent_eval/`, `auto-calib/`, `data_log/`, `playback/`, `videos/` and a license PDF — it ships **no `models/`**. (Older bundles carried a legacy `models/` subtree that is no longer used.) The `mkdir` + `chmod` above is therefore **mandatory, not a no-op** on this version: ds-start phase 0 downloads the RT-CV weights into `models/` and builds the TensorRT engine there (~171 MB for 2D — `rtdetr_warehouse_v1.0.2.fp16.onnx` plus the generated `.engine`), writing as the container's UID, which is why the directory must exist and be `0777` first. `auto-calib/vggt/` is **not** bundle content: it is a user-created directory for the optional VGGT model.

---

### Phase 5: Configure the warehouse env files

Initialize `<repo>/deploy/docker/industry-profiles/warehouse-operations/generated.env` from `overrides.env`, then edit `generated.env` for deployment selectors, deployment-size overrides, credentials, host paths, hardware choices, and host-published port conflicts. Keep the checked-in `.env` and `overrides.env` unchanged; `generated.env` is the active per-deployment layer.

```bash
cd <repo>/deploy/docker
cp industry-profiles/warehouse-operations/overrides.env industry-profiles/warehouse-operations/generated.env
# Ensure blueprint-configurator reads the same generated override layer.
grep -q '^BP_CONFIGURATOR_ENV_FILE=' industry-profiles/warehouse-operations/generated.env \
  || printf '\nBP_CONFIGURATOR_ENV_FILE=%s/industry-profiles/warehouse-operations/generated.env\n' "$(pwd)" >> industry-profiles/warehouse-operations/generated.env
```

Keys below match the actual files — only the values listed need editing for a typical deploy; the rest have working defaults.

```bash
# --- Deployment selectors: generated.env (Phase 3 answers go here) ---
COMPOSE_PROJECT_NAME=vss            # volume/container namespace; change to run two stacks on one host
MODE=<2d|3d|mv3dt>
BP_PROFILE=<bp_wh|bp_wh_kafka|bp_wh_redis|bp_wh_auto_calib>
STREAM_TYPE=<kafka|redis>           # redis only for bp_wh_redis; kafka for bp_wh, bp_wh_kafka, bp_wh_auto_calib

# Deployment size is NOT an env var — pick the matching COMPOSE_PROFILES list below.

SAMPLE_VIDEO_DATASET="<dataset-name>"
NUM_STREAMS=<3|4>
ELASTICSEARCH_MODE=cpu              # inert on the compose path — leave at cpu

# --- Hardware ---
# Tuned in blueprint_config.yml: H100, L4, L40S, RTXA6000, RTXA6000ADA, RTXPRO6000BW,
# RTXPRO6000BW-SE, RTXPRO4500BW (2d and 3d only), IGX-THOR, DGX-SPARK
HARDWARE_PROFILE=H100

# GPU device IDs (defaults shown — change only if you need a non-default layout)
RT_CV_DEVICE_ID='0'                 # perception (always local)
RT_VLM_DEVICE_ID='1'                # RTVI VLM, bp_wh only (always local)
LLM_DEVICE_ID='2'                   # bp_wh + LLM_MODE=local

# --- LLM (bp_wh only; set LLM_MODE=none for bp_wh_kafka / bp_wh_redis / bp_wh_auto_calib) ---
# RTVI VLM has no mode — it is always deployed locally for bp_wh.
LLM_MODE=local                      # local | remote | none
LLM_NAME=nvidia/nvidia-nemotron-nano-9b-v2
LLM_NAME_SLUG=nvidia-nemotron-nano-9b-v2   # set to `none` for LLM_MODE=remote/none
LLM_BASE_URL=http://vss-llm-nim:8000       # local default; set the external endpoint for LLM_MODE=remote
LLM_MODEL_TYPE=nim                         # nim | openai (remote endpoints)

# --- RTVI VLM (bp_wh; always local — these are image/model selectors, not a mode toggle) ---
# vss-rtvi-vlm is always deployed for BP_PROFILE=bp_wh (rtvi-vlm is included in COMPOSE_PROFILES_WH_2D).
VLM_MODE=none                       # warehouse never uses the standalone VLM NIM path
VLM_NAME_SLUG=none
VLM_NAME=nim_nvidia_cosmos3-nano-reasoner_bf16-final
VLM_BASE_URL=http://rtvi-vlm:8000
VLM_MODEL_TYPE=rtvi
RTVI_VLM_MODEL_PATH=ngc:nim/nvidia/cosmos3-nano-reasoner:bf16-final
RTVI_VLM_MODEL_TO_USE=cosmos-reason3
RTVI_VLM_ENDPOINT=http://rtvi-vlm:8000/v1
RTVI_VLLM_GPU_MEMORY_UTILIZATION='0.8'
# RTVI_VLM_MAX_MODEL_LEN=18000      # uncomment for RTXPRO4500BW (32 GB)

# --- MQTT (mv3dt only — cross-camera messaging for BEV Fusion) ---
# These live in the warehouse .env, not overrides.env. MQTT_HOST is the compose
# service name — do NOT set it to localhost; the broker is a separate container.
MQTT_HOST=mosquitto
MQTT_PORT=1883

# --- Paths ---
VSS_APPS_DIR="<repo>/deploy/docker"
# One of: <repo>/data, a custom local path, or extracted NGC app-data dir (see Phase 4)
VSS_DATA_DIR="<repo>/data"

# --- Networking ---
HOST_IP='<HOST_IP>'
EXTERNAL_IP="${HOST_IP}"             # browser-reachable hostname/IP (Brev: secure-link domain)
HAPROXY_HOST_PORT=7777               # host-published ingress for VSS UI
HAPROXY_PORT=7777                    # HAProxy container listen port

# --- Credentials ---
NGC_CLI_API_KEY='<your-ngc-api-key>'           # required for RT-CV model downloads, local NIMs, and image pulls
NVIDIA_API_KEY=''                              # required for build.nvidia.com remote endpoints
OPENAI_API_KEY=''                              # required for OpenAI remote endpoints
```

#### Brev Secure Link Overrides

Brev secure links use a hostname of the form `<port>-<env>.<brev-domain>` (e.g. `7777-abc123.brevlab.com`) — the HAProxy port is prefixed directly to the Brev environment ID. **The domain is not always `brevlab.com`:** instances on Brev's Skybridge-managed NetBird network use `apps.run.brev.nvidia.com`. Confirm with `netbird status -d` (a `skybridge` / `brev.nvidia.com` marker means the latter) or from the Brev dashboard URL, and substitute it everywhere `brevlab.com` appears below. The Brev reverse proxy terminates TLS and forwards to the container's HAProxy port, so browser-facing URLs must use `https`/`wss` on port `443` (the standard HTTPS port, which can be omitted from URLs).

After editing the main `generated.env` values above, apply these overrides in the **same** `generated.env` file when deploying on Brev:

```ini
# --- Brev secure link overrides ---
# Replace <BREV_ENV_ID> with your Brev environment ID (e.g. vbi9qjb1x).
# Find it via: echo "$BREV_ENV_ID" or from the Brev dashboard URL.
HAPROXY_HOST_PORT=7777
HAPROXY_PORT=7777
VSS_PUBLIC_HTTP_PROTOCOL=https
VSS_PUBLIC_WS_PROTOCOL=wss
VSS_PUBLIC_HOST=7777-<BREV_ENV_ID>.brevlab.com
VSS_PUBLIC_PORT=443
```

##### Browser-facing URLs (automatically covered by VSS_PUBLIC_* overrides)

These compose template variables all use `${VSS_PUBLIC_HTTP_PROTOCOL}://${VSS_PUBLIC_HOST}:${VSS_PUBLIC_PORT}` (or the `wss` variant) and resolve correctly once the overrides above are applied:

| Compose variable | Resolves to (Brev) | Compose file |
|---|---|---|
| `VSS_AGENT_EXTERNAL_URL` | `https://7777-<BREV_ENV_ID>.brevlab.com` | `services/agent/compose.yml` |
| `VSS_AGENT_REPORTS_BASE_URL` | `https://7777-<BREV_ENV_ID>.brevlab.com/static/` | `services/agent/compose.yml` |
| `VST_EXTERNAL_URL` | `https://7777-<BREV_ENV_ID>.brevlab.com` | `services/agent/compose.yml` |
| `NEXT_PUBLIC_AGENT_API_URL_BASE` | `https://7777-<BREV_ENV_ID>.brevlab.com/api/v1` | `services/ui/compose.yml` |
| `NEXT_PUBLIC_SIDEBAR_CHAT_AGENT_API_URL_BASE` | `https://7777-<BREV_ENV_ID>.brevlab.com/api/v1` | `services/ui/compose.yml` |
| `NEXT_PUBLIC_VST_API_URL` | `https://7777-<BREV_ENV_ID>.brevlab.com/vst/api` | `services/ui/compose.yml` |
| `NEXT_PUBLIC_MDX_WEB_API_URL` | `https://7777-<BREV_ENV_ID>.brevlab.com/video-analytics-api` | `services/ui/compose.yml` |
| `NEXT_PUBLIC_ALERTS_API_URL` | `https://7777-<BREV_ENV_ID>.brevlab.com/alert-bridge/api/v1` | `services/ui/compose.yml` |
| `NEXT_PUBLIC_WEBSOCKET_CHAT_COMPLETION_URL` | `wss://7777-<BREV_ENV_ID>.brevlab.com/websocket` | `services/ui/compose.yml` |
| `NEXT_PUBLIC_SIDEBAR_CHAT_WEBSOCKET_CHAT_COMPLETION_URL` | `wss://7777-<BREV_ENV_ID>.brevlab.com/websocket` | `services/ui/compose.yml` |
| `NEXT_PUBLIC_DASHBOARD_TAB_KIBANA_BASE_URL` | `https://7777-<BREV_ENV_ID>.brevlab.com/kibana` | `services/ui/compose.yml` |

##### Internal service-to-service URLs (no Brev override needed)

These URLs stay on the internal host network — containers talk to each other via `HOST_IP` or `localhost`, never through the Brev reverse proxy:

| Variable | Template | Compose file |
|---|---|---|
| `VIDEO_ANALYSIS_MCP_URL` | `http://vss-va-mcp:${VSS_VA_MCP_PORT}` | `services/agent/agent.env` |
| `LLM_BASE_URL` | `http://vss-llm-nim:8000` | `overrides.env` (consumed in `services/agent/compose.yml`) |
| `VLM_BASE_URL` | `http://rtvi-vlm:8000` | `overrides.env` (consumed in `services/agent/compose.yml`) |
| `RTVI_VLM_BASE_URL` | `http://rtvi-vlm:8000` | `services/rtvi/rtvi.env` |
| `ALERT_BRIDGE_URL` | `http://alert-bridge:${ALERT_BRIDGE_PORT}` | `services/alert/alert.env` |
| `PHOENIX_ENDPOINT` | `http://phoenix:6006` | `services/agent/agent.env` |
| `VST_INTERNAL_URL` | `http://vst-ingress:${VST_PORT}` (30888) | `services/vios/vst.env` |
| `EVAL_LLM_JUDGE_BASE_URL` | `http://vss-llm-nim:8000` (compose default) | `services/agent/compose.yml` |
| `VST_INGRESS_ENDPOINT` | `${VST_INTERNAL_IP}/vst` (no scheme) | `services/vios/vst.env` |
| `KAFKA_BOOTSTRAP_SERVERS` | `kafka:29092` (compose-network listener, not the host-published `9092`) | `services/rtvi/rtvi-vlm/rtvi-vlm-docker-compose.yml` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://otel-collector:4318` | `services/rtvi/rtvi-vlm/rtvi-vlm-docker-compose.yml` |
| Healthcheck endpoints | `http://localhost:8000/...` | all compose files |

`vss-rtvi-vlm` (`services/rtvi/rtvi-vlm/rtvi-vlm-docker-compose.yml`) has **no browser-facing URLs** — it consumes RTSP streams and publishes to Kafka/Redis. All its URLs (Kafka bootstrap, OTEL, Redis, healthcheck) are internal.

##### HTTP chat completion URLs (use HOST_IP directly)

Two UI variables bypass the `VSS_PUBLIC_*` template and use `HOST_IP` directly:

| Variable | Template | Compose file |
|---|---|---|
| `NEXT_PUBLIC_HTTP_CHAT_COMPLETION_URL` | `http://${HOST_IP}:${VSS_AGENT_PORT:-8000}/chat/stream` | `services/ui/compose.yml` |
| `NEXT_PUBLIC_SIDEBAR_CHAT_HTTP_CHAT_COMPLETION_URL` | `http://${HOST_IP}:${VSS_AGENT_PORT:-8000}/chat/stream` | `services/ui/compose.yml` |

In HTTP chat mode, the browser posts to the UI's same-origin `/api/chat` route. The Next.js API handler then uses these `HOST_IP` URLs server-side to reach `vss-agent` on the host network. The `vss-agent-ui` container runs in bridge mode (`ports: 3000:3000`), so `HOST_IP` is the reachable route from UI server to agent. For browser-visible chat traffic, HAProxy routes `/api/chat` to `vss-agent-ui`, and routes `/chat` / `/websocket` to `vss-agent` (see [Access Points](#access-points)).

##### Map URL (disabled by default)

| Variable | Template | Compose file |
|---|---|---|
| `NEXT_PUBLIC_MAP_URL` | `${NEXT_PUBLIC_MAP_URL:-http://${EXTERNAL_IP}:3002}` | `services/ui/compose.yml` |

Uses `EXTERNAL_IP:3002` directly (not `VSS_PUBLIC_*`). The map tab is **disabled by default** for warehouse (`NEXT_PUBLIC_ENABLE_MAP_TAB=false`). If enabled on Brev, create a secure link for port `3002` and override explicitly: `NEXT_PUBLIC_MAP_URL=https://3002-<BREV_ENV_ID>.brevlab.com`.

> **Do not** use the old `http://7777-<BREV_ENV_ID>.brevlab.com:7777` form — the Brev reverse proxy does not expose the raw HAProxy port. Using `http` with `:7777` will fail with connection refused or mixed-content errors in the browser.

##### `COMPOSE_PROFILES` — select and resolve the service list

Under the profile-inversion model, `COMPOSE_PROFILES` is an explicit list of service-scoped Docker Compose **profile names** for the active variant — not a `${BP_PROFILE}_${MODE}` token. The checked-in `overrides.env` template defines one list per variant (`COMPOSE_PROFILES_WH_2D`, `COMPOSE_PROFILES_WH_KAFKA_2D`, …). After copying that template to `generated.env`, set its `COMPOSE_PROFILES` selector to the list matching the chosen `BP_PROFILE`, `MODE`, and deployment size:

| `BP_PROFILE` | `MODE` | Extended selector | Minimal selector |
|---|---|---|---|
| `bp_wh` | `2d` | `COMPOSE_PROFILES_WH_2D` | not supported |
| `bp_wh_kafka` | `2d` | `COMPOSE_PROFILES_WH_KAFKA_2D` | `COMPOSE_PROFILES_WH_KAFKA_2D_MINIMAL` |
| `bp_wh_redis` | `2d` | `COMPOSE_PROFILES_WH_REDIS_2D` | `COMPOSE_PROFILES_WH_REDIS_2D_MINIMAL` |
| `bp_wh_kafka` | `3d` | `COMPOSE_PROFILES_WH_KAFKA_3D` | `COMPOSE_PROFILES_WH_KAFKA_3D_MINIMAL` |
| `bp_wh_redis` | `3d` | `COMPOSE_PROFILES_WH_REDIS_3D` | `COMPOSE_PROFILES_WH_REDIS_3D_MINIMAL` |
| `bp_wh_kafka` | `mv3dt` | `COMPOSE_PROFILES_WH_KAFKA_MV3DT` | `COMPOSE_PROFILES_WH_KAFKA_MV3DT_MINIMAL` |
| `bp_wh_redis` | `mv3dt` | `COMPOSE_PROFILES_WH_REDIS_MV3DT` | `COMPOSE_PROFILES_WH_REDIS_MV3DT_MINIMAL` |
| `bp_wh_auto_calib` | `2d` / `3d` / `mv3dt` | `COMPOSE_PROFILES_WH_AUTO_CALIB_2D` / `COMPOSE_PROFILES_WH_AUTO_CALIB_3D` / `COMPOSE_PROFILES_WH_AUTO_CALIB_MV3DT` | not applicable; these lists are already minimal by composition |

```ini
# generated.env examples
# BP_PROFILE=bp_wh, MODE=2d
COMPOSE_PROFILES=${COMPOSE_PROFILES_WH_2D}

# BP_PROFILE=bp_wh_kafka, MODE=mv3dt, minimal
# COMPOSE_PROFILES=${COMPOSE_PROFILES_WH_KAFKA_MV3DT_MINIMAL}

# BP_PROFILE=bp_wh_auto_calib, MODE=3d
# COMPOSE_PROFILES=${COMPOSE_PROFILES_WH_AUTO_CALIB_3D}
```

Do not invoke `blueprint-deploy.sh` from this skill. `overrides.env` remains unchanged as the reusable template; `generated.env` is the active per-deployment file.

Some Docker Compose versions do not expand `${...}` references within `--env-file` values, leaving `COMPOSE_PROFILES` as a literal `${COMPOSE_PROFILES_WH_*}` string that matches **no** services. Resolve and export it by sourcing the stable defaults followed by the active deployment overrides, then run Compose from the same shell:

```bash
cd <repo>/deploy/docker
[ -f industry-profiles/warehouse-operations/generated.env ] \
  || cp industry-profiles/warehouse-operations/overrides.env industry-profiles/warehouse-operations/generated.env
# Now run the resolve-env prelude (Lifecycle: Resolve env).
# COMPOSE_PROFILES then holds the resolved service list, e.g.:
#   turnserver-init,turnserver,redis,...,vss-agent,rtvi-vlm,vss-ui,...,llm_local_nvidia-nemotron-nano-9b-v2
echo "$COMPOSE_PROFILES"
```

> **`COMPOSE_PROFILES` must be exported** before running any `docker compose` command with the warehouse env files. It resolves to an explicit **service-profile list** (defined by the `COMPOSE_PROFILES_WH_*` variables copied from `overrides.env`) and is not expanded by `--env-file` in all Docker Compose versions. Use the [resolve-env prelude](#resolve-env); it exports the resolved value before `docker compose up`.

> **DGX-SPARK (SBSA):** swap to the `-sbsa`-tagged image variant, which lives in the same GHCR repository. Comment the default `VSS_RT_CV_TAG` line and uncomment `VSS_RT_CV_TAG="develop-latest-sbsa"`. `VSS_RT_CV_TAG` is the only key with a commented `-sbsa` line in the warehouse `overrides.env` — there is nothing to uncomment for `RTVI_VLM_IMAGE_TAG`, and no warehouse variant deployable on DGX-SPARK includes `rtvi-vlm`.

---

### Phase 6: Pre-flight Check

**Do not proceed if any check fails. Never use `sudo` with `docker` — fix non-root setup (2.2) first.**

```bash
nvidia-smi --query-gpu=index,name --format=csv,noheader
docker info 2>/dev/null | grep -i "runtimes"
docker run --rm --gpus all ubuntu:24.04 nvidia-smi 2>&1 | head -5
echo "NGC_CLI_API_KEY: ${NGC_CLI_API_KEY:+SET}${NGC_CLI_API_KEY:-NOT SET}"
ngc config current 2>/dev/null | grep -q "apikey" && echo "NGC config: key present" || echo "NGC config: no key"
```

---

### Phase 7: Dry-Run

```bash
cd <repo>/deploy/docker

# Run the resolve-env prelude (Lifecycle: Resolve env) first — without it
# COMPOSE_PROFILES stays the literal ${COMPOSE_PROFILES_WH_*}, matches no services,
# and `config` returns a near-empty list that reads as "almost nothing will deploy".

docker compose -f compose.yml -f services/infra/compose-no-turn-tcp-relay.yml \
  --env-file containers.env \
  --env-file industry-profiles/warehouse-operations/.env \
  --env-file industry-profiles/warehouse-operations/generated.env \
  config | grep "container_name"

# Also confirm the resolved image coordinates before pulling:
docker compose -f compose.yml -f services/infra/compose-no-turn-tcp-relay.yml \
  --env-file containers.env \
  --env-file industry-profiles/warehouse-operations/.env \
  --env-file industry-profiles/warehouse-operations/generated.env \
  config --images | sort -u
```

Show container list to the user, then ask: **"Looks good — deploy now?"**

---

### Phase 8: Deploy

From `<repo>/deploy/docker`, run **[Lifecycle: Bring up](#lifecycle-bring-up)** after the user confirms Phase 7.

---

### Phase 9: Monitor Progress

Run **[Lifecycle: Monitor](#lifecycle-monitor)** using the same `LOG` as Phase 8.

---

## After deploy

See [Access Points](#access-points) for the full HAProxy route table and direct-port diagnostics table, and [`warehouse-debug.md` — Service Access Points](warehouse-debug.md#service-access-points) for the copy-pasteable standard and Brev URL blocks.

**Brev needs no post-deploy steps.** §2.7 (host setup) and the [Brev secure link overrides](#brev-secure-link-overrides) are sufficient.

---

## Calibration Generation

Two paths are available to generate calibration files depending on your video source:

| Path | Deployment selector | When to use |
|---|---|---|
| **Standalone Auto-Calibration** | `COMPOSE_PROFILES=vss-auto-calibration,vss-auto-calibration-ui` | You have video files on disk and want to upload them directly to the calibration UI. No nvstreamer or VST stack needed. |
| **Warehouse Auto-Calibration** (`BP_PROFILE=bp_wh_auto_calib`) | Select `COMPOSE_PROFILES_WH_AUTO_CALIB_2D`, `_3D`, or `_MV3DT` to match `MODE` | You want to calibrate against live RTSP streams served by nvstreamer (using the warehouse dataset and VST stack). |

Both paths deploy `vss-auto-calibration` + `vss-auto-calibration-ui` and produce calibration JSON files consumable by behavior-analytics.

### 2D calibration cleanup

In 2D, Auto-Calibration adds blank `group` and `region` fields to the generated `calibration.json`. These fields are not required for 2D calibration and should be removed before deploying the selected non-calibration warehouse variant.

### Camera Clustering (3D / MV3DT only)

After calibration is generated via Auto-Calibration, run camera clustering before deploying the selected non-calibration warehouse variant. For 3D/MV3DT, the required field lives directly on each camera sensor as `sensors[].group`. The warehouse blueprint docker compose setup uses one BEV group, so run the clustering tool with `--n_clusters 1` and then verify the group field is present.

```bash
CALIBRATION_JSON=/path/to/calibration.json
REPO_ROOT=/path/to/video-search-and-summarization
SDU_DIR="${REPO_ROOT}/libs/analytics/spatialai-data-utils"
SENSOR_COUNT=$(jq '.sensors | length' "${CALIBRATION_JSON}")

PYTHONPATH="${SDU_DIR}:${PYTHONPATH:-}" python3 \
  "${SDU_DIR}/tools/camera_grouping/create_camera_clusters.py" \
  "${CALIBRATION_JSON}" \
  --max_camera_per_group "${SENSOR_COUNT}" \
  --n_clusters 1 \
  --disable_param_tuning \
  --overwrite
```

Docs: the clustering procedure is on the 3D profile page — https://docs.nvidia.com/vss/latest/warehouse-docs/3D-profile.html#camera-clustering — and applies to MV3DT as well; the MV3DT page (https://docs.nvidia.com/vss/latest/warehouse-docs/3D-multi-camera-detection-and-tracking-MV3DT.html) covers the MV3DT-specific `camInfo` / pub-sub config updates. `/latest/` tracks the published release, so these do not need re-pinning for 3.3.0.

### MV3DT-specific configuration updates

When adding new cameras to an MV3DT deployment, run the MV3DT utility scripts under `tools/rtvi-cv-mv3dt-utils` after calibration and camera clustering are complete, and before deploying the selected non-calibration warehouse variant. These scripts generate the MV3DT-specific files consumed by the per-camera tracker and MQTT communication layer:

1. **Camera information files** (`camInfo/<sensor_id>.yml`) — each camera requires a `camInfo` file containing the 3x4 projection matrix and per-class object model dimensions, generated from `calibration.json`.
2. **MQTT publish/subscribe configuration** (`pub_sub_info_config.yml`) — defines the inter-camera communication graph for MV3DT by generating a vision-neighbor graph from camera calibration data.
3. **Tracker configuration** (`ds-mv3dt-tracker-config.yml`) — ensure the `ObjectModelProjection.cameraModelFilepath` section maps each sensor ID to its corresponding `camInfo` file.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ngc: command not found` | Run Phase 1.2 |
| `Missing org` NGC error | Run `ngc config set`, match org to API key |
| NGC auth / `docker login nvcr.io` fails | Re-export `NGC_CLI_API_KEY` and retry |
| `unknown or invalid runtime name: nvidia` | Install NVIDIA Container Toolkit — Phase 2.3 |
| Streams not appearing in VST | `docker logs vss-vios-nvstreamer` (2D/3D) or `docker logs vss-vios-nvstreamer-mv3dt` (MV3DT). If nvstreamer never started, check `vss-configurator` first — nvstreamer waits on it being healthy |
| Perception not starting | `docker logs vss-rtvi-cv` (2D/3D) or `docker logs vss-rtvi-cv-mv3dt` (MV3DT) — verify models in `$VSS_DATA_DIR/models/` |
| `vss-configurator` health check failing | Wait 60s and recheck (60s start period) |
| Low FPS | GPU oversaturated — reduce `NUM_STREAMS` and redeploy |
| Dataset/mode mismatch | `nv-warehouse-4cams` → `BP_PROFILE=bp_wh`, `MODE=2d`; `warehouse-4cams-20mx20m-synthetic` → `MODE=3d` or `MODE=mv3dt` |
| Brev: UI loads but API calls fail / mixed-content errors | `VSS_PUBLIC_*` overrides not applied — URLs still use `http://7777-<BREV_ENV_ID>.brevlab.com:7777` instead of `https://7777-<BREV_ENV_ID>.brevlab.com`. Apply [Brev secure link overrides](#brev-secure-link-overrides) and redeploy |
| Brev: HAProxy returns 404 | `Host:` header doesn't match `h_main` ACL — verify `VSS_PUBLIC_HOST` matches the Brev secure-link domain (`7777-<BREV_ENV_ID>.brevlab.com`) |
| Brev: WebSocket connection refused | `VSS_PUBLIC_WS_PROTOCOL` still set to `ws` instead of `wss`, or `VSS_PUBLIC_PORT` not set to `443` |
| Redeploy / reset without reinstall | [Redeploy](#redeploy) |
