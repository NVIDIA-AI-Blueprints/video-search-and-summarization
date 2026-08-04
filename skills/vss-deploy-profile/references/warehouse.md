# Warehouse Blueprint Reference

Blueprint: VSS Warehouse — RT-DETR (2D) / Sparse4D (3D) / MV3DT (multi-view 3D tracking with BEV Fusion) perception + behavior analytics over multi-camera warehouse streams. Distinct from the core VSS profiles (`base`, `alerts`, `lvs`, `search`): it lives under `<repo>/deploy/docker/industry-profiles/warehouse-operations/` and is deployed from `<repo>/deploy/docker/` using the warehouse `.env` plus `generated.env` env-file pair.

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

`COMPOSE_PROFILES` is an explicit list of service-scoped Docker Compose **profile names** for the active variant. Each service carries its own `profiles: ["<service-profile-name>"]`, and the checked-in `overrides.env` template defines one `COMPOSE_PROFILES_WH_*` list per variant. Copy `overrides.env` to `generated.env`, apply the deployment overrides there, and point `COMPOSE_PROFILES` at the list matching `BP_PROFILE`, `MODE`, and `MINIMAL_PROFILE`. Do not invoke `blueprint-deploy.sh` from this skill. The `bp_wh` service list includes `rtvi-vlm` directly; warehouse does not use a separate `vlm_*` NIM slice.

## Deployment Size (Kafka/Redis Variants Only)

Applies to `bp_wh_kafka` and `bp_wh_redis` only (all modes: 2d, 3d, mv3dt).

| Feature | Minimal (`MINIMAL_PROFILE="true"`) | Extended (`MINIMAL_PROFILE=""`) |
|---|---|---|
| Perception (RT-DETR 2D / Sparse4D 3D) | ✅ | ✅ |
| Behavior Analytics | ✅ | ✅ |
| VST / NvStreamer | ✅ | ✅ |
| Auto-Calibration | ❌ (use the auto-calibration variant) | ❌ (use the auto-calibration variant) |
| ELK (Elasticsearch/Logstash/Kibana) | ❌ | ✅ |
| Video Analytics API (`vss-video-analytics-api`, `MDX_PORT` 8081) | ❌ | ✅ |
| Monitoring | ❌ | ✅ |
| Bounding box overlays in VST | ❌ | ✅ (requires Elasticsearch) |

## Services Deployed

The selected warehouse variant boots the service set identified by `BP_PROFILE`, `MODE`, and deployment size. Only `BP_PROFILE=bp_wh` adds the agent, UI, and RTVI VLM to the warehouse CV pipeline. Perception, behavior analytics, nvstreamer, and most other services use the **same container names** in 2D and 3D — no `-2d` / `-3d` suffix. MV3DT uses a **`-mv3dt` suffix** on all its containers (e.g. `vss-vios-nvstreamer-mv3dt`, `vss-behavior-analytics-mv3dt`, `vss-rtvi-cv-mv3dt`, `vss-configurator-mv3dt`, `vss-video-analytics-api-mv3dt`).

### Warehouse CV core (2D and 3D variants)

| Container | Purpose |
|---|---|
| `vss-vios-nvstreamer` | Streams sample video files via RTSP |
| VST stack: `vss-vios-postgres`, `-sensor`, `-streamprocessing`, `-sdr`, `-mcp`, `-ingress`, `-envoy` | Video ingestion, recording, stream management |
| `vss-rtvi-cv` | DeepStream perception (RT-DETR for 2D, Sparse4D for 3D) |
| `vss-rtvi-cv-sdr` | Stream data router — manages DeepStream lifecycle |
| `vss-rtvi-cv-config-adaptor` | DeepStream config adaptor (3D only) |
| `vss-configurator` | Blueprint configurator — stream and hardware configs |
| `vss-behavior-analytics` | Behavior analytics — ROI, tripwire, proximity events |
| `kafka` or `redis` (`STREAM_TYPE`) | Message broker for CV metadata and control bus |
| `vss-broker-health-check` | Waits for broker readiness before starting dependent services |

### MV3DT CV core (when `MODE=mv3dt` and `BP_PROFILE=bp_wh_kafka` or `bp_wh_redis`)

MV3DT adds MQTT-based cross-camera messaging and BEV Fusion on top of per-camera DeepStream perception. All MV3DT containers carry a `-mv3dt` suffix.

| Container | Purpose |
|---|---|
| `vss-vios-nvstreamer-mv3dt` | Streams sample video files via RTSP |
| VST stack: `vss-vios-postgres`, `sensor-ms-mv3dt`, `-streamprocessing`, `-sdr`, `-mcp`, `-ingress`, `-envoy` | Video ingestion, recording, stream management |
| `vss-rtvi-cv-mv3dt` | DeepStream perception (per-camera) |
| `vss-rtvi-cv-bev-fusion` | BEV Fusion — fuses per-camera detections into a unified 3D BEV frame |
| `mosquitto` | MQTT broker for cross-camera messaging between perception and BEV fusion |
| `vss-configurator-mv3dt` | Blueprint configurator — stream and hardware configs |
| `vss-behavior-analytics-mv3dt` | Behavior analytics — 3D spatial analytics |
| `kafka` or `redis` (`STREAM_TYPE`) | Message broker for CV metadata and control bus |
| `vss-broker-health-check` | Waits for broker readiness before starting dependent services |

### Warehouse Auto-Calibration (select with `BP_PROFILE=bp_wh_auto_calib`)

Deploys only the minimum services needed for camera calibration — no perception, no behavior analytics, no agent stack. Set `BP_PROFILE=bp_wh_auto_calib`, choose `MODE=2d`, `3d`, or `mv3dt`, and select the matching `COMPOSE_PROFILES_WH_AUTO_CALIB_*` service list. This variant skips broker health check. It is the only warehouse variant that starts `vss-auto-calibration` and `vss-auto-calibration-ui`; regular `bp_wh`, `bp_wh_kafka`, and `bp_wh_redis` variants do not.

| Container | Purpose |
|---|---|
| `vss-vios-nvstreamer` / `vss-vios-nvstreamer-mv3dt` | Streams sample video files via RTSP |
| `vss-configurator` / `vss-configurator-mv3dt` | Blueprint configurator |
| `vss-auto-calibration` (+ `vss-auto-calibration-ui`) | Camera auto-calibration |
| VST stack (subset) | Stream management for calibration |

### Agent + UI (only when `BP_PROFILE=bp_wh`)

| Container | Port |
|---|---|
| `vss-agent-ui` (Next.js) | 3000 |
| `vss-agent` | `VSS_AGENT_PORT` (default `8000`) |
| `vss-va-mcp` | `VSS_VA_MCP_PORT` (default `9901`) |
| `phoenix` (telemetry) | 6006 |

### HAProxy ingress (conditional)

| Container | Port | Deployed when |
|---|---|---|
| `vss-haproxy-ingress` | `HAPROXY_HOST_PORT` (host, default `7777`) → `HAPROXY_PORT` (container, default `7777`) | `BP_PROFILE=bp_wh`; `BP_PROFILE=bp_wh_auto_calib`; or Kafka/Redis with `MINIMAL_PROFILE=""` |

### Storage / observability (conditional)

| Container | Port | Deployed when |
|---|---|---|
| `elasticsearch` | `VSS_ES_PORT` (default `9200`) | `BP_PROFILE=bp_wh` (always — vss-agent storage), **or** kafka/redis with `MINIMAL_PROFILE=""` (extended, any mode — for `mdx-bev`, ELK, overlays, analytics API) |
| `kibana` / `logstash` / `vss-video-analytics-api` | various | Same condition as `elasticsearch` (MV3DT uses `vss-video-analytics-api-mv3dt`) |

> **3D / MV3DT `mdx-bev` index requires Elasticsearch — and ES is only deployed for kafka/redis in extended mode** (`MINIMAL_PROFILE=""`). In minimal mode, the BEV-sync check cannot run because the index is never persisted.

### LLM + RTVI VLM (only when `BP_PROFILE=bp_wh`)

| Container | Port | When |
|---|---|---|
| LLM NIM — container name = `LLM_NAME_SLUG` (e.g. `nvidia-nemotron-nano-9b-v2`) | `LLM_PORT` (default `30081`) | `LLM_MODE=local` |
| `vss-rtvi-vlm` (real-time VLM) | `RTVI_VLM_PORT` (default `8018`) | Always deployed for `BP_PROFILE=bp_wh` — `rtvi-vlm` is included directly in `COMPOSE_PROFILES_WH_2D` |
| `vss-alert-bridge` | `ALERT_BRIDGE_PORT` (default `9080`) | Always deployed for `bp_wh` |

> **No VLM NIM container.** VSS has two VLM paths: a standalone **VLM NIM** (controlled by `VLM_MODE` / `VLM_NAME_SLUG`, used by base/alerts/lvs/search profiles) and an integrated **RTVI VLM** (`vss-rtvi-vlm`). The `bp_wh` warehouse variant uses **RTVI VLM only** — its service list includes the self-named `rtvi-vlm` profile, and `vss-agent` connects to it directly. Kafka/Redis and auto-calibration warehouse variants do not deploy a VLM. Because warehouse does not use the standalone VLM NIM path, keep `VLM_MODE=none` and `VLM_NAME_SLUG=none` in the active `generated.env`. There is no `vlm_*` slice in `COMPOSE_PROFILES`, so VLM NIM containers (e.g. `cosmos-reason2-8b` on port 30082) are never deployed.

## Perception Model

- **2D model:** RT-DETR with EfficientViT/L2 backbone
- **3D model:** Sparse4D (depth-aware perception, requires 4-camera dataset)
- **MV3DT model:** Per-camera DeepStream perception + BEV Fusion (multi-view 3D tracking, fuses detections from multiple cameras into a unified BEV frame via MQTT)
- **Detects:** People, humanoid robots, forklifts, autonomous vehicles, warehouse equipment
- **Output:** 2D bounding boxes (or 3D BEV frames) with tracked object IDs via Kafka/Redis `mdx-raw` topic; 3D / MV3DT BEV frames also land in the `mdx-bev` Elasticsearch index

## GPU Layout

| Role | Device | Used by |
|---|---|---|
| RT-CV perception (DeepStream — RT-DETR for 2D, Sparse4D for 3D, MV3DT for mv3dt) — always local | `RT_CV_DEVICE_ID` (default: `0`) | All warehouse variants except `BP_PROFILE=bp_wh_auto_calib` |
| RTVI VLM — always local | `RT_VLM_DEVICE_ID` (default: `1`) | `bp_wh` only |
| LLM NIM (dedicated) | `LLM_DEVICE_ID` (default: `2`) | `bp_wh` with `LLM_MODE=local` |

`LLM_MODE` accepts `local`, `remote`, or `none`:
- `local` — LLM NIM on its own GPU (`LLM_DEVICE_ID`)
- `remote` — point at an external LLM endpoint via `LLM_BASE_URL` (no LLM NIM deployed)
- `none` — no LLM, when `BP_PROFILE` is `bp_wh_kafka`, `bp_wh_redis`, or `bp_wh_auto_calib`

RTVI VLM has no equivalent mode setting — it is always deployed locally on `RT_VLM_DEVICE_ID` for `BP_PROFILE=bp_wh`. Keep `VLM_MODE=none` in `generated.env` because warehouse uses RTVI VLM instead of the standalone VLM NIM path.

## Access Points

**Prefer the HAProxy ingress (host port `7777`) when the selected service list includes it** — it gives a single browser-reachable origin and rewrites paths to internal services. `_MINIMAL` Kafka/Redis lists omit HAProxy, so use direct ports there. Routes confirmed against `deploy/docker/services/infra/haproxy/haproxy.cfg.template`.

### Via HAProxy ingress (`http://<EXTERNAL_IP>:<HAPROXY_HOST_PORT>` — default `<EXTERNAL_IP>:7777`)

| Path | Backend | Available when |
|---|---|---|
| `/` | `vss-agent-ui` (Next.js) | `BP_PROFILE=bp_wh` only; other ingress-enabled variants have no UI backend |
| `/storage`, `/storage/...` | `vst-storage` (compat → `/vst/storage/...`) | Any ingress-enabled warehouse variant |
| `/kibana`, `/kibana/...` | `kibana` | `BP_PROFILE=bp_wh`, or extended Kafka/Redis (any mode) |
| `/video-analytics-api`, `.../...` | `vss-video-analytics-api` | `BP_PROFILE=bp_wh`, or extended Kafka/Redis (any mode) |
| `/behavior-analytics`, `.../...` | `vss-behavior-analytics` | `BP_PROFILE=bp_wh`, or extended Kafka/Redis (any mode) |
| `/perception-sdr`, `.../...` | `vss-rtvi-cv-sdr` | `BP_PROFILE=bp_wh`, or extended Kafka/Redis (any mode) |
| `/alert-bridge`, `.../...` | `vss-alert-bridge` | `BP_PROFILE=bp_wh` only |
| `/phoenix`, `.../...` | `phoenix` | `BP_PROFILE=bp_wh` only |
| `/va-mcp`, `.../...` | `vss-va-mcp` | `BP_PROFILE=bp_wh` only |
| `/api`, `/api/...` | `vss-agent` | `BP_PROFILE=bp_wh` only |
| `/api/chat`, `.../...` | `vss-agent-ui` | `BP_PROFILE=bp_wh` only |
| `/chat`, `/static`, `/websocket` | `vss-agent` | `BP_PROFILE=bp_wh` only |

### Direct ports (no HAProxy route — diagnostics only)

| Service | URL | Available when |
|---|---|---|
| NvStreamer UI | `http://<HOST_IP>:31000` | All warehouse variants |
| Auto-Calibration UI | `http://<HOST_IP>:5000` | Standalone `vss-auto-calibration,vss-auto-calibration-ui`, or `BP_PROFILE=bp_wh_auto_calib` with the mode-specific `COMPOSE_PROFILES_WH_AUTO_CALIB_*` list |
| Elasticsearch API | `http://<HOST_IP>:9200` | `BP_PROFILE=bp_wh`, or extended Kafka/Redis (any mode) |
| VSS Agent API (direct) | `http://<HOST_IP>:8000` | `BP_PROFILE=bp_wh` only (prefer `/api` via HAProxy) |
| VST MCP (direct) | `http://<HOST_IP>:8001` | All warehouse variants |
| Phoenix (direct) | `http://<HOST_IP>:6006` | `BP_PROFILE=bp_wh` only (prefer `/phoenix` via HAProxy) |
| Kibana (direct) | `http://<HOST_IP>:5601` | `BP_PROFILE=bp_wh`, or extended Kafka/Redis (any mode); prefer `/kibana` via HAProxy |
| Video Analytics API (direct) | `http://<HOST_IP>:8081` (`MDX_PORT`) | `BP_PROFILE=bp_wh`, or extended Kafka/Redis (any mode); prefer `/video-analytics-api` via HAProxy |
| VST UI | `http://<HOST_IP>:30888/vst/` | All warehouse variants — direct port, not proxied via HAProxy |

`EXTERNAL_IP` defaults to `${HOST_IP}` but should be set to the browser-reachable hostname/IP. On Brev, apply the [Brev secure link overrides](#brev-secure-link-overrides) in Phase 5 — the HAProxy ingress, agent, and UI all need `https`/`wss` on the secure-link domain. The HAProxy `h_main` ACL routes browser traffic through `${VSS_PUBLIC_HOST}:${VSS_PUBLIC_PORT}`; for local defaults this is `${EXTERNAL_IP}:${HAPROXY_HOST_PORT}`. Wrong Host headers get a 404 from haproxy.

## Compose File Structure

Deployed from `<repo>/deploy/docker/` (the repo's compose root) using:
- `industry-profiles/warehouse-operations/.env` — profile-specific stable defaults
- `services/<service>/*.env` — shared service defaults loaded through compose include `env_file` entries
- `industry-profiles/warehouse-operations/overrides.env` — checked-in deployment/profile override defaults
- `industry-profiles/warehouse-operations/generated.env` — per-deploy working copy created from `overrides.env`
- `compose.yml` — root top-level include (foundational, monitoring, vst, industry-profiles, etc.)
  - `industry-profiles/compose.yml` — industry sub-include
    - `industry-profiles/warehouse-operations/compose.yml` — warehouse sub-include
      - `industry-profiles/warehouse-operations/warehouse-2d-app/warehouse-2d-app.yml` — 2D app services
      - `industry-profiles/warehouse-operations/warehouse-3d-app/warehouse-3d-app.yml` — 3D app services
      - `industry-profiles/warehouse-operations/warehouse-mv3dt-app/warehouse-mv3dt-app.yml` — MV3DT app services

## App Data

App data (sample videos, playback, and calibration assets) is **not** bundled with the repo. Pick one source:

| Source | When to use | `VSS_DATA_DIR` |
|---|---|---|
| `<repo>/data` | Quick start — drop assets into the repo's `data/` directory | `<repo>/data` |
| Custom local path | Existing dataset on a non-repo path (e.g. `/mnt/warehouse-data`) | user-provided path |
| NGC app-data resource | Reproducing the official sample-video deployment | extracted path of `nvidia/vss-warehouse/vss-warehouse-app-data:<version>` |

Ask the user which source they want and whether they already have the assets on disk. Only run the NGC app-data download (next subsection) when they explicitly choose the NGC source. Perception models are independent of this choice and are downloaded by `ds-start.sh` phase 0 inside the perception container when a `models-download.json` manifest is mounted.

### NGC app-data download (optional)

| Artifact | NGC Resource | Local directory after extract |
|---|---|---|
| App data (videos, playback, calibration) | `nvidia/vss-warehouse/vss-warehouse-app-data:<version>` | `vss-warehouse-app-data_v<version>/` |

> **Org:** use the canonical `nvidia/...` resource path for the published 3.2.0 bundle. If you get `403 Access Denied`, confirm the NGC key has access to the published VSS warehouse resource.

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
| **Tear down only** (stop and remove containers/volumes; keep files on disk) | [Lifecycle: Tear down](#lifecycle-tear-down). |

**`<repo>`** — path to your `video-search-and-summarization` checkout. All compose commands run from `<repo>/deploy/docker/`, with `--env-file industry-profiles/warehouse-operations/.env --env-file industry-profiles/warehouse-operations/generated.env`. If `generated.env` does not exist yet, initialize it from `overrides.env` before editing. If you don't know the repo path, **ask explicitly** before running shell commands.

---

## Lifecycle (shared)

Use these sections for **redeploy**, **Phase 8–9**, and **tear down**. Default log file for bring up and monitor:

```bash
LOG=${LOG:-/tmp/warehouse-blueprint.log}
```

### Lifecycle: Tear down

Hard teardown — removes all containers, the project network, and all volume belonging to this stack.

```bash
cd <repo>/deploy/docker

# Hard teardown — `-v` ensures named volumes are also removed.
# Containers + network + project's named volumes all go.
docker compose -f compose.yml \
  --env-file industry-profiles/warehouse-operations/.env \
  --env-file industry-profiles/warehouse-operations/generated.env \
  down -v

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

```bash
LOG=${LOG:-/tmp/warehouse-blueprint.log}
cd <repo>/deploy/docker

# Resolve COMPOSE_PROFILES from the active generated.env before Compose.
# generated.env is the per-deployment copy of the checked-in overrides.env template.
set -a
. industry-profiles/warehouse-operations/.env
. industry-profiles/warehouse-operations/generated.env
set +a

# Brev only: expose BREV_ENV_ID for variable substitution. Skip on non-Brev hosts.
export BREV_ENV_ID=$(awk -F= '/^BREV_ENV_ID=/{gsub(/"/, "", $2); print $2; exit}' /etc/environment 2>/dev/null)

printf '%s' "$NGC_CLI_API_KEY" | docker login --username '$oauthtoken' --password-stdin nvcr.io

nohup docker compose -f compose.yml \
  --env-file industry-profiles/warehouse-operations/.env \
  --env-file industry-profiles/warehouse-operations/generated.env \
  up --detach --pull always --force-recreate --build \
  > "$LOG" 2>&1 &
echo "Compose PID $! — logging to $LOG"
```

### Lifecycle: Monitor

Poll every ~60s:

```bash
LOG=${LOG:-/tmp/warehouse-blueprint.log}
tail -20 "$LOG"
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
```

**Stack is ready when these show `Up`** (same container names in 2D and 3D; MV3DT uses `-mv3dt` suffix):

- 2D / 3D Kafka/Redis variants: `vss-vios-nvstreamer`, `vss-rtvi-cv`, `vss-configurator`, `vss-behavior-analytics`, broker (`kafka` / `redis`), `vss-broker-health-check`, plus the `vss-vios-*` VST stack
- 3D extra: `vss-rtvi-cv-config-adaptor`
- MV3DT Kafka/Redis variants: `vss-vios-nvstreamer-mv3dt`, `vss-rtvi-cv-mv3dt`, `vss-rtvi-cv-bev-fusion`, `mosquitto`, `vss-configurator-mv3dt`, `vss-behavior-analytics-mv3dt`, broker (`kafka` / `redis`), `vss-broker-health-check`, plus VST stack
- `bp_wh` extra: `vss-rtvi-vlm`, `vss-alert-bridge`, `vss-agent`, `vss-agent-ui`, `vss-va-mcp`, `vss-haproxy-ingress`, `phoenix`, plus the LLM NIM container (named after `LLM_NAME_SLUG`) when `LLM_MODE=local`
- Extended extra (kafka/redis, any mode): `vss-haproxy-ingress`, `logstash`, `kibana`, `vss-video-analytics-api` (MV3DT uses `vss-video-analytics-api-mv3dt`)
- `elasticsearch`: `BP_PROFILE=bp_wh` (always), **or** kafka/redis with `MINIMAL_PROFILE=""` (extended, any mode)
- `BP_PROFILE=bp_wh_auto_calib`: only nvstreamer, configurator, auto-calibration, and a VST subset

Check FPS (same container for 2D/3D; use `vss-rtvi-cv-mv3dt` for MV3DT):

```bash
# 2D / 3D:
docker logs -f vss-rtvi-cv 2>&1 | grep -i fps | head -5
# MV3DT:
docker logs -f vss-rtvi-cv-mv3dt 2>&1 | grep -i fps | head -5
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

Image paths in `deploy/docker/` reference the published `nvcr.io/nvidia/vss-core/...` artifacts. Confirm the key can access those images and the warehouse resources before deploying.

```bash
ngc registry image list "nvidia/vss-core/*" 2>&1 | head -10
```

**`Missing org` error** → run `ngc config set` (or write `~/.ngc/config` directly) and match the org to the one used when generating the key. Run `ngc org list` to see which orgs the current key has access to before guessing.

---

### Phase 2: System Prerequisites

**Detect if this is a Brev-managed instance first:**

```bash
grep "BREV_ENV_ID" /etc/environment && echo "Brev instance — apply Brev-specific steps" \
  || echo "Not Brev — standard deployment"
```

If `BREV_ENV_ID` is present, also complete [§2.7 Brev-specific host setup](#27-brev-specific-host-setup-brev-deployments-only) below, apply the [Brev Secure Link Overrides](#brev-secure-link-overrides) in Phase 5, and run the [post-deploy Brev steps](#after-deploy-brev). For Brev architecture and secure-link troubleshooting, see [`brev.md`](brev.md) — warehouse uses the same generated-env pattern, with overrides written to `industry-profiles/warehouse-operations/generated.env`.

Run each check in order. **If a check fails, automatically install and re-verify — do not wait for the user.** Only stop if a requirement cannot be met automatically (unsupported hardware, insufficient RAM/CPU).

#### Supported Hardware

`HARDWARE_PROFILE` is a **blueprint setting**, not a string that `nvidia-smi` always prints verbatim. For **discrete GPUs**, match the GPU model from `nvidia-smi` / `lspci` to a row below. **IGX-THOR** and **DGX-SPARK** are **whole-system platforms** (kits/boards): set the profile from product/SKU or vendor docs if you already know the machine type; `nvidia-smi` shows the **on-board NVIDIA GPU name** (e.g. a Thor-class or Spark system GPU), not the text `IGX-THOR` or `DGX-SPARK`. On **DGX Spark**, unified memory can make some `nvidia-smi` memory fields show **Not Supported**; driver and device listing should still be checked per [DGX Spark user guide](https://docs.nvidia.com/dgx/dgx-spark/).

Valid values: `H100, L40, L40S, L4, A6000, RTXA6000, RTXA6000ADA, RTXPRO4500BW, RTXPRO6000BW, IGX-THOR, DGX-SPARK`. All profiles include tuned `max_streams_supported` for 2D, 3D, and MV3DT modes.

| Discrete GPU (typical `nvidia-smi` name) | HARDWARE_PROFILE |
|---|---|
| RTX PRO 6000 Blackwell | `RTXPRO6000BW` |
| RTX PRO 4500 Blackwell | `RTXPRO4500BW` (32 GB). When `COMPOSE_PROFILES=${COMPOSE_PROFILES_WH_2D}` deploys `vss-rtvi-vlm`, set `RTVI_VLM_MAX_MODEL_LEN=18000` to cap RT-VLM context and allow KV-cache allocation. |
| H100 (NVL, SXM HBM3) | `H100` |
| RTX A6000 Ada Generation | `RTXA6000ADA` |
| RTX A6000 (Ampere) | `RTXA6000` |
| A6000 (generic alias) | `A6000` |
| L40S | `L40S` |
| L40 | `L40` |
| L4 | `L4` |
| Platform: NVIDIA IGX Thor (kit / board) | `IGX-THOR` |
| Platform: NVIDIA DGX Spark | `DGX-SPARK` |

> **Do NOT use a higher profile on lower-profile hardware** (e.g. `H100` on an `L4`) — the env file warns against this directly.

**GPUs not in the list above:** the warehouse blueprint may not have a tuned profile. Pick the closest match from the table or treat the deployment as unsupported on that GPU until the upstream list adds it.

#### 2.1 GPU Detection and NVIDIA Driver

**Detect GPUs and driver:**

```bash
nvidia-smi --query-gpu=index,name,driver_version,memory.total --format=csv,noheader
```

Use the **`name`** column to pick **`HARDWARE_PROFILE`** from the [Supported Hardware](#supported-hardware) list. For **IGX-THOR** or **DGX-SPARK**, set `HARDWARE_PROFILE` to that value when the deployment target is that platform, even though `name` will be a GPU part name, not `IGX-THOR` / `DGX-SPARK`. The blueprint does not currently accept custom/free-form profile strings — if the host's GPU is not in the table, the deployment is unsupported on that hardware.

**Required driver versions (match the platform):**

| Platform | Driver version |
|---|---|
| x86 Ubuntu 24.04 | **580.105.08** (required) |
| DGX-SPARK | `580.95.05` |
| IGX-THOR | `580.00` |

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

Reference versions: **Docker Engine 28.3.3** and **Docker Compose plugin v2.39.1+**. If Docker Engine is already **28.3.3** and the Compose plugin is **v2.39.1 or newer**, proceed to §2.3.

```bash
docker --version        # need 28.3.3
docker compose version  # need v2.39.1+
docker ps               # must run without sudo
```

**Install / pin Docker (Ubuntu 24.04):**

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

# Optional sanity check: these should print available Docker repo versions.
apt-cache madison docker-ce | grep '28.3.3'
apt-cache madison docker-compose-plugin | grep '2.39.1'
apt-cache madison docker-ce-rootless-extras | grep '28.3.3'

# Install or downgrade to the known-good reference versions.
sudo systemctl stop docker docker.socket 2>/dev/null || true
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --allow-downgrades \
  docker-ce=5:28.3.3-1~ubuntu.24.04~noble \
  docker-ce-cli=5:28.3.3-1~ubuntu.24.04~noble \
  containerd.io=2.2.2-1~ubuntu.24.04~noble \
  docker-buildx-plugin \
  docker-compose-plugin=2.39.1-1~ubuntu.24.04~noble \
  docker-ce-rootless-extras=5:28.3.3-1~ubuntu.24.04~noble
sudo systemctl enable --now docker

# Optional: hold so unattended-upgrades doesn't move them back
sudo apt-mark hold docker-ce docker-ce-cli containerd.io docker-compose-plugin docker-ce-rootless-extras

docker version --format '{{.Server.Version}}'   # -> 28.3.3
docker compose version --short                  # -> 2.39.1+
```

##### When to pin to Docker 28.3.3 / Compose v2.39.1+

Pin Docker if you hit this specific failure during `docker compose up --pull always`:

```
error from registry: Incorrect Repository Format
```

Then re-run `docker compose up --pull always` after the pinned install succeeds.

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
```

If not set:
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

#### 2.7 Brev-specific host setup (Brev deployments only)

These steps are required on any Brev-provisioned instance and are not covered by the standard system prerequisites above.

**UFW — allow Docker bridge networks to reach host services**

`vss-rtvi-vlm` runs on the Docker bridge network (`mdx_default`, subnet `172.18.0.0/16`) and needs to reach host-network services (HAProxy, VST). UFW blocks this by default:

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

**`/etc/hosts` — resolve Brev domains locally**

Host-network containers (e.g. `vss-alert-bridge`) validate video clip URLs that contain the Brev domain. Without a local hosts entry, the request goes to Cloudflare which blocks non-443 ports:

```bash
HOST_IP=$(hostname -I | awk '{print $1}')
BREV_ENV_ID=$(awk -F= '/^BREV_ENV_ID=/{gsub(/"/, "", $2); print $2; exit}' /etc/environment)
echo "${HOST_IP} 7777-${BREV_ENV_ID}.brevlab.com" | sudo tee -a /etc/hosts
echo "${HOST_IP} 30888-${BREV_ENV_ID}.brevlab.com" | sudo tee -a /etc/hosts
```

---

### Phase 3: Interactive Configuration

**Ask these four questions before creating or editing `generated.env`.**

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

Variable combinations — pick one row matching the user's Vision-AI variant
and stream type:

| Vision AI | Stream type | `BP_PROFILE` | `STREAM_TYPE` | `SAMPLE_VIDEO_DATASET` | `NUM_STREAMS` |
|---|---|---|---|---|---|
| 2D Vision AI | kafka | `bp_wh_kafka` | `kafka` | `warehouse-loading-dock-3cams-synthetic` | 3 |
| 2D Vision AI | redis | `bp_wh_redis` | `redis` | `warehouse-loading-dock-3cams-synthetic` | 3 |
| 2D Vision AI with Agents | n/a | `bp_wh` | — | `nv-warehouse-4cams` | 4 (also set `LLM_MODE=local`; RTVI VLM is always local) |
| 3D Vision AI | kafka | `bp_wh_kafka` | `kafka` | `warehouse-4cams-20mx20m-synthetic` | 4 |
| 3D Vision AI | redis | `bp_wh_redis` | `redis` | `warehouse-4cams-20mx20m-synthetic` | 4 |
| MV3DT Vision AI | kafka | `bp_wh_kafka` | `kafka` | `warehouse-4cams-20mx20m-synthetic` | 4 |
| MV3DT Vision AI | redis | `bp_wh_redis` | `redis` | `warehouse-4cams-20mx20m-synthetic` | 4 |
| Warehouse Auto-Calibration | n/a | `bp_wh_auto_calib` | — | mode-specific default | mode-specific default (also set `LLM_MODE=none`) |

`3D Vision AI` and `MV3DT Vision AI` intentionally share the same dataset and
stream counts — they differ only at the perception layer (`Sparse4D` vs
per-camera DeepStream + BEV Fusion).

#### Q4 — Deployment size

Skip when `BP_PROFILE` is `bp_wh` or `bp_wh_auto_calib`. For
`BP_PROFILE=bp_wh_kafka` or `BP_PROFILE=bp_wh_redis` (any mode):

> "Which deployment size?
> - **minimal** — excludes ELK, Video Analytics API, monitoring. Recommended for IGX-THOR.
> - **extended** — full deployment."

```bash
MINIMAL_PROFILE="true"   # minimal
MINIMAL_PROFILE=""       # extended
```

#### Q5 — Data Source & Calibration

> "Are you using the **sample dataset** or your **own data** (custom videos / live RTSP streams)?"

**Sample dataset** — calibration files ship with the app data. No extra step needed; proceed to Phase 4.

**Own data** — you need a calibration file before the analytics pipeline can produce meaningful results.

> "Do you already have a calibration JSON file, or do you need to generate one first?"

- **Already have a calibration file** — proceed to Phase 4. You'll configure its deployment path in Phase 5 (`generated.env`).
- **Need to generate a calibration file** — pick a calibration path based on your video source:

  | You have… | Deployment selector | What it does |
  |---|---|---|
  | **Video files on disk** | `COMPOSE_PROFILES=vss-auto-calibration,vss-auto-calibration-ui` | Standalone auto-calibration. Upload videos directly to the calibration UI — no nvstreamer, no VST stack needed. |
  | **Live RTSP streams** (or want to use nvstreamer) | `BP_PROFILE=bp_wh_auto_calib`, plus `MODE` and the matching `COMPOSE_PROFILES_WH_AUTO_CALIB_*` list | Warehouse auto-calibration. Calibrate against RTSP streams served by nvstreamer + VST stack. |

  Deploy the chosen calibration variant first, then generate the calibration JSON via the Auto-Calibration UI (`http://<HOST_IP>:5000`).

  > **Note:** Post-calibration cleanup depends on mode. In 2D, Auto-Calibration adds blank `group` and `region` fields to `calibration.json`; they are not required for 2D and should be removed. For 3D / MV3DT, calibration files require camera clustering to populate `sensors[].group` — see [Calibration Generation](#calibration-generation).

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

ngc registry resource download-version "nvidia/vss-warehouse/vss-warehouse-app-data:<APP_DATA_VERSION>"
cd vss-warehouse-app-data_v<APP_DATA_VERSION>
tar -xvf vss-warehouse-app-data.tar.gz

sudo mkdir -p /path/to/vss-warehouse-app-data/models /path/to/vss-warehouse-app-data/data_log
sudo chmod 0777 /path/to/vss-warehouse-app-data/models /path/to/vss-warehouse-app-data/data_log
```

See [App Data → NGC app-data download](#ngc-app-data-download-optional) for the current version pin.

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
MODE=<2d|3d|mv3dt>
BP_PROFILE=<bp_wh|bp_wh_kafka|bp_wh_redis|bp_wh_auto_calib>
STREAM_TYPE=<kafka|redis>           # ignored by bp_wh and bp_wh_auto_calib; set for bp_wh_kafka / bp_wh_redis

# --- Deployment size override: generated.env ---
MINIMAL_PROFILE="true"              # or "" for extended (bp_wh_kafka / bp_wh_redis only)

SAMPLE_VIDEO_DATASET="<dataset-name>"
NUM_STREAMS=<3|4>

# --- Hardware ---
# Valid: H100, L40, L40S, L4, A6000, RTXA6000, RTXA6000ADA, RTXPRO6000BW, RTXPRO4500BW, IGX-THOR, DGX-SPARK
HARDWARE_PROFILE=H100

# GPU device IDs (defaults shown — change only if you need a non-default layout)
RT_CV_DEVICE_ID='0'                 # perception (always local)
RT_VLM_DEVICE_ID='1'                # RTVI VLM, bp_wh only (always local)
LLM_DEVICE_ID='2'                   # bp_wh + LLM_MODE=local

# --- LLM (bp_wh only; set LLM_MODE=none for bp_wh_kafka / bp_wh_redis / bp_wh_auto_calib) ---
# RTVI VLM has no mode — it is always deployed locally for bp_wh.
LLM_MODE=local                      # local | remote | none
LLM_NAME=nvidia/nvidia-nemotron-nano-9b-v2
LLM_NAME_SLUG=nvidia-nemotron-nano-9b-v2
# LLM_BASE_URL — only when LLM_MODE=remote

# --- RTVI VLM (bp_wh; always local — these are image/model selectors, not a mode toggle) ---
# vss-rtvi-vlm is always deployed for BP_PROFILE=bp_wh (rtvi-vlm is included in COMPOSE_PROFILES_WH_2D).
VLM_NAME=nim_nvidia_cosmos3-nano-reasoner_bf16-final
RTVI_VLM_MODEL_PATH=ngc:nim/nvidia/cosmos3-nano-reasoner:bf16-final
RTVI_VLM_MODEL_TO_USE=cosmos-reason3

# --- MQTT (mv3dt only — cross-camera messaging for BEV Fusion) ---
MQTT_HOST=localhost
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

Brev secure links use a hostname of the form `<port>-<env>.brevlab.com` (e.g. `7777-abc123.brevlab.com`) — the HAProxy port is prefixed directly to the Brev environment ID. The Brev reverse proxy terminates TLS and forwards to the container's HAProxy port, so browser-facing URLs must use `https`/`wss` on port `443` (the standard HTTPS port, which can be omitted from URLs).

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
| `LLM_BASE_URL` | `http://${HOST_IP}:${LLM_PORT}` | `services/agent/compose.yml` |
| `VLM_BASE_URL` | `http://${HOST_IP}:${VLM_PORT}` | `services/agent/compose.yml` |
| `RTVI_VLM_BASE_URL` | `http://rtvi-vlm:8000` | `services/rtvi/rtvi.env` |
| `ALERT_BRIDGE_URL` | `http://alert-bridge:${ALERT_BRIDGE_PORT}` | `services/alert/alert.env` |
| `PHOENIX_ENDPOINT` | `http://phoenix:6006` | `services/agent/agent.env` |
| `VST_INTERNAL_URL` | `http://vst-ingress:30888` | `services/vios/vst.env` |
| `EVAL_LLM_JUDGE_BASE_URL` | `http://${HOST_IP}:${LLM_PORT}` | `services/agent/compose.yml` |
| `VST_INGRESS_ENDPOINT` | `vst-ingress:30888/vst` (no scheme) | `services/vios/vst.env` |
| `KAFKA_BOOTSTRAP_SERVERS` | `${HOST_IP}:9092` | `services/rtvi/rtvi-vlm/rtvi-vlm-docker-compose.yml` |
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

Under the profile-inversion model, `COMPOSE_PROFILES` is an explicit list of service-scoped Docker Compose **profile names** for the active variant — not a `${BP_PROFILE}_${MODE}` token. The checked-in `overrides.env` template defines one list per variant (`COMPOSE_PROFILES_WH_2D`, `COMPOSE_PROFILES_WH_KAFKA_2D`, …). After copying that template to `generated.env`, set its `COMPOSE_PROFILES` selector to the list matching the chosen `BP_PROFILE`, `MODE`, and `MINIMAL_PROFILE`:

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

# BP_PROFILE=bp_wh_kafka, MODE=mv3dt, MINIMAL_PROFILE="true"
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
set -a
. industry-profiles/warehouse-operations/.env
. industry-profiles/warehouse-operations/generated.env
set +a
# COMPOSE_PROFILES now holds the resolved service list, e.g.:
#   turnserver-init,turnserver,redis,...,vss-agent,rtvi-vlm,vss-ui,...,llm_remote_nvidia-nemotron-nano-9b-v2
echo "$COMPOSE_PROFILES"
```

##### `vss-rtvi-vlm` bridge network access + socat proxy (Brev only)

`vss-rtvi-vlm` runs on the Docker bridge network and needs to resolve Brev secure-link domains to fetch video clips for VLM verification. These steps are applied **after the stack is up** — see [After deploy — Brev](#after-deploy-brev).

> **`COMPOSE_PROFILES` must be exported** before running any `docker compose` command with the warehouse env files. It resolves to an explicit **service-profile list** (defined by the `COMPOSE_PROFILES_WH_*` variables copied from `overrides.env`) and is not expanded by `--env-file` in all Docker Compose versions. Source the warehouse `.env` + active `generated.env` as shown above; `set -a` exports the resolved value before `docker compose up`.

> **DGX-SPARK (SBSA):** swap to the `-sbsa`-tagged image variants. Comment the default `PERCEPTION_TAG="3.3.0-26.07.2"` and uncomment `PERCEPTION_TAG="3.3.0-sbsa-26.07.2"`. Apply the same pattern to `RTVI_VLM_IMAGE_TAG`.

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
docker compose -f compose.yml \
  --env-file industry-profiles/warehouse-operations/.env \
  --env-file industry-profiles/warehouse-operations/generated.env \
  config | grep "container_name"
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

The deploy script prints the actual access points once the stack is up. For the full URL tables (standard and Brev), see [`warehouse-debug.md` — Service Access Points](warehouse-debug.md#service-access-points).

See [Access Points](#access-points) for the full HAProxy route table and direct-port diagnostics table.

---

## After deploy — Brev

Run these steps once the stack is healthy. Re-apply after any `vss-rtvi-vlm` restart.

```bash
BREV_ENV_ID=$(awk -F= '/^BREV_ENV_ID=/{gsub(/"/, "", $2); print $2; exit}' /etc/environment)
```

**1. Start socat TLS proxy** (create cert once per host, start after every host reboot):

```bash
# Create self-signed cert — once per host
sudo openssl req -x509 -newkey rsa:2048 \
  -keyout /etc/ssl/private/vst-proxy.key \
  -out /etc/ssl/certs/vst-proxy.crt \
  -days 3650 -nodes \
  -subj "/CN=30888-${BREV_ENV_ID}.brevlab.com" 2>/dev/null
sudo cat /etc/ssl/private/vst-proxy.key /etc/ssl/certs/vst-proxy.crt > /tmp/vst-proxy.pem

# Start proxy — re-run after every host reboot
sudo nohup socat OPENSSL-LISTEN:443,bind=172.18.0.1,cert=/tmp/vst-proxy.pem,verify=0,fork \
  TCP:127.0.0.1:30888 > /tmp/socat.log 2>&1 &
ss -tlnp | grep ':443'   # confirm listening
```

This TLS proxy allows `vss-rtvi-vlm` (Docker bridge network) to reach VST over `https://30888-<BREV_ENV_ID>.brevlab.com` via the bridge gateway `172.18.0.1:443`.

**2. Inject Brev domain entries into `vss-rtvi-vlm`** (re-apply after every container restart):

```bash
docker exec -u root vss-rtvi-vlm sh -c "
  echo '172.18.0.1 7777-${BREV_ENV_ID}.brevlab.com' >> /etc/hosts
  echo '172.18.0.1 30888-${BREV_ENV_ID}.brevlab.com' >> /etc/hosts
"

# Verify
docker exec vss-rtvi-vlm getent hosts 7777-${BREV_ENV_ID}.brevlab.com
# Expected: 172.18.0.1   7777-<BREV_ENV_ID>.brevlab.com
```

With both steps complete, `vss-rtvi-vlm` can resolve Brev secure-link domains to the bridge gateway and reach HAProxy (port 7777) and VST (port 30888) for clip downloads.

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

Docs: 3D https://docs.nvidia.com/vss/3.2.0/warehouse-docs/3D-profile.html#camera-clustering and for mv3dt, https://docs.nvidia.com/vss/3.2.0/warehouse-docs/mv3dt-profile.html#camera-clustering

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
| Streams not appearing in VST | `docker logs vss-vios-nvstreamer` |
| Perception not starting | `docker logs vss-rtvi-cv` (2D/3D) or `docker logs vss-rtvi-cv-mv3dt` (MV3DT) — verify models in `$VSS_DATA_DIR/models/` |
| `vss-configurator` health check failing | Wait 60s and recheck (60s start period) |
| Low FPS | GPU oversaturated — reduce `NUM_STREAMS` and redeploy |
| Dataset/mode mismatch | `nv-warehouse-4cams` → `BP_PROFILE=bp_wh`, `MODE=2d`; `warehouse-4cams-20mx20m-synthetic` → `MODE=3d` or `MODE=mv3dt` |
| Brev: UI loads but API calls fail / mixed-content errors | `VSS_PUBLIC_*` overrides not applied — URLs still use `http://7777-<BREV_ENV_ID>.brevlab.com:7777` instead of `https://7777-<BREV_ENV_ID>.brevlab.com`. Apply [Brev secure link overrides](#brev-secure-link-overrides) and redeploy |
| Brev: HAProxy returns 404 | `Host:` header doesn't match `h_main` ACL — verify `VSS_PUBLIC_HOST` matches the Brev secure-link domain (`7777-<BREV_ENV_ID>.brevlab.com`) |
| Brev: WebSocket connection refused | `VSS_PUBLIC_WS_PROTOCOL` still set to `ws` instead of `wss`, or `VSS_PUBLIC_PORT` not set to `443` |
| Redeploy / reset without reinstall | [Redeploy](#redeploy) |
