# Warehouse Blueprint Reference

Blueprint: VSS Warehouse — RT-DETR (2D) / Sparse4D (3D) / MV3DT (multi-view 3D tracking with BEV Fusion) perception + behavior analytics over multi-camera warehouse streams. Distinct from the core VSS profiles (`base`, `alerts`, `lvs`, `search`): it lives under `<repo>/deploy/docker/industry-profiles/warehouse-operations/` and is deployed from `<repo>/deploy/docker/` using `industry-profiles/warehouse-operations/.env`.

The compose files ship **in-tree** in the `video-search-and-summarization` repo — no NGC compose bundle to download. App data (videos and models) is the only artifact you may need to acquire; see [App Data](#app-data).

Work through **one path** under [Choose your path](#choose-your-path). Reference tables (variants, services, GPU layout, endpoints, artifacts) are in the top half; operational phases are in the bottom half.

---

## Profile Variants

| Profile Name | MODE | BP_PROFILE | SAMPLE_VIDEO_DATASET | NUM_STREAMS | LLM | RTVI VLM |
|---|---|---|---|---|---|---|
| 2D Vision AI Profile | `2d` | `bp_wh_kafka` or `bp_wh_redis` | `warehouse-loading-dock-3cams-synthetic` | 3 | none | none |
| 2D Vision AI with Agents Profile | `2d` | `bp_wh` | `nv-warehouse-4cams` | 4 | `local` / `local_shared` / `remote` / `none` | **always local** |
| 3D Vision AI Profile | `3d` | `bp_wh_kafka` or `bp_wh_redis` | `warehouse-4cams-20mx20m-synthetic` | 4 | none | none |
| MV3DT Vision AI Profile | `mv3dt` | `bp_wh_kafka` or `bp_wh_redis` | `warehouse-4cams-20mx20m-synthetic` | 4 | none | none |
| Warehouse Auto-Calibration | `2d` / `3d` / `mv3dt` | `bp_wh_auto_calib` | (same as mode default) | (same as mode default) | none | none |
| Standalone Auto-Calibration | any | `auto_calib` | n/a | n/a | none | none |

`COMPOSE_PROFILES` is computed automatically: `${BP_PROFILE}_${MODE},llm_${LLM_MODE}_${LLM_NAME_SLUG}`. No `vlm_*` slice — `vss-rtvi-vlm` is always deployed for `bp_wh` and there is no VLM NIM.

## Minimal vs Extended Profile

Applies to `bp_wh_kafka` and `bp_wh_redis` only (all modes: 2d, 3d, mv3dt).

| Feature | Minimal (`MINIMAL_PROFILE="true"`) | Extended (`MINIMAL_PROFILE=""`) |
|---|---|---|
| Perception (RT-DETR 2D / Sparse4D 3D) | ✅ | ✅ |
| Behavior Analytics | ✅ | ✅ |
| VST / NvStreamer | ✅ | ✅ |
| Auto-Calibration | ✅ | ✅ |
| ELK (Elasticsearch/Logstash/Kibana) | ❌ | ✅ |
| Video Analytics API (`vss-video-analytics-api`, `MDX_PORT` 8081) | ❌ | ✅ |
| Monitoring | ❌ | ✅ |
| Bounding box overlays in VST | ❌ | ✅ (requires Elasticsearch) |

## Services Deployed

The warehouse blueprint boots the **full VSS stack** (agent + UI + VST + RTVI behind HAProxy) on top of the warehouse CV pipeline. Service set varies by `BP_PROFILE` and `MODE`. Perception, behavior analytics, nvstreamer, and most other services use the **same container names** in 2D and 3D — no `-2d` / `-3d` suffix. MV3DT uses a **`-mv3dt` suffix** on all its containers (e.g. `vss-vios-nvstreamer-mv3dt`, `vss-behavior-analytics-mv3dt`, `vss-rtvi-cv-mv3dt`, `vss-configurator-mv3dt`, `vss-video-analytics-api-mv3dt`).

### Warehouse CV core (2D and 3D profiles)

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
| `vss-auto-calibration` (+ `vss-auto-calibration-ui`) | Camera auto-calibration |

### MV3DT CV core (`bp_wh_kafka_mv3dt` / `bp_wh_redis_mv3dt`)

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
| `vss-auto-calibration` (+ `vss-auto-calibration-ui`) | Camera auto-calibration |

### Warehouse Auto-Calibration (`bp_wh_auto_calib`)

Deploys only the minimum services needed for camera calibration — no perception, no behavior analytics, no agent stack. Available for all modes (`bp_wh_auto_calib_2d`, `bp_wh_auto_calib_3d`, `bp_wh_auto_calib_mv3dt`). Skips broker health check.

| Container | Purpose |
|---|---|
| `vss-vios-nvstreamer` / `vss-vios-nvstreamer-mv3dt` | Streams sample video files via RTSP |
| `vss-configurator` / `vss-configurator-mv3dt` | Blueprint configurator |
| `vss-auto-calibration` (+ `vss-auto-calibration-ui`) | Camera auto-calibration |
| VST stack (subset) | Stream management for calibration |

### Agent + UI + ingress (`bp_wh` only)

| Container | Port |
|---|---|
| `vss-haproxy-ingress` | `HAPROXY_PORT` (default `7777`) |
| `vss-agent-ui` (Next.js) | 3000 |
| `vss-agent` | `VSS_AGENT_PORT` (default `8000`) |
| `vss-va-mcp` | `VSS_VA_MCP_PORT` (default `9901`) |
| `phoenix` (telemetry) | 6006 |

### Storage / observability (conditional)

| Container | Port | Deployed when |
|---|---|---|
| `elasticsearch` | `VSS_ES_PORT` (default `9200`) | `BP_PROFILE=bp_wh` (always — vss-agent storage), **or** kafka/redis with `MINIMAL_PROFILE=""` (extended, any mode — for `mdx-bev`, ELK, overlays, analytics API) |
| `kibana` / `logstash` / `vss-video-analytics-api` | various | Same condition as `elasticsearch` (MV3DT uses `vss-video-analytics-api-mv3dt`) |

> **3D / MV3DT `mdx-bev` index requires Elasticsearch — and ES is only deployed for kafka/redis in extended mode** (`MINIMAL_PROFILE=""`). In minimal mode, the BEV-sync check cannot run because the index is never persisted.

### LLM + RTVI VLM (`bp_wh` only)

| Container | Port | When |
|---|---|---|
| LLM NIM — container name = `LLM_NAME_SLUG` (e.g. `nvidia-nemotron-nano-9b-v2`) | `LLM_PORT` (default `30081`) | `LLM_MODE=local` or `local_shared` |
| `vss-rtvi-vlm` (real-time VLM) | 8018 | **Always** deployed for `bp_wh` — hardcoded in compose profile `bp_wh_2d` |
| `vss-alert-bridge` | `ALERT_BRIDGE_PORT` (default `9080`) | Always deployed for `bp_wh` |

> **No VLM NIM container.** VSS has two VLM paths: a standalone **VLM NIM** (controlled by `VLM_MODE` / `VLM_NAME_SLUG`, used by base/alerts/lvs/search profiles) and an integrated **RTVI VLM** (`vss-rtvi-vlm`). The warehouse blueprint uses **RTVI VLM only** — `vss-rtvi-vlm` is always deployed via the hardcoded compose profile `bp_wh_2d`, and `vss-agent` connects to it directly. Because warehouse does not use the standalone VLM NIM path, `VLM_MODE=none` and `VLM_NAME_SLUG=none` in the warehouse `.env`. There is no `vlm_*` slice in `COMPOSE_PROFILES`, so VLM NIM containers (e.g. `cosmos-reason2-8b` on port 30082) are never deployed.

## Perception Model

- **2D model:** RT-DETR with EfficientViT/L2 backbone
- **3D model:** Sparse4D (depth-aware perception, requires 4-camera dataset)
- **MV3DT model:** Per-camera DeepStream perception + BEV Fusion (multi-view 3D tracking, fuses detections from multiple cameras into a unified BEV frame via MQTT)
- **Detects:** People, humanoid robots, forklifts, autonomous vehicles, warehouse equipment
- **Output:** 2D bounding boxes (or 3D BEV frames) with tracked object IDs via Kafka/Redis `mdx-raw` topic; 3D / MV3DT BEV frames also land in the `mdx-bev` Elasticsearch index

## GPU Layout

| Role | Device | Used by |
|---|---|---|
| RT-CV perception (DeepStream — RT-DETR for 2D, Sparse4D for 3D, MV3DT for mv3dt) — always local | `RT_CV_DEVICE_ID` (default: `0`) | All warehouse profiles |
| RTVI VLM — always local | `RT_VLM_DEVICE_ID` (default: `1`) | `bp_wh` only |
| LLM NIM (dedicated) | `LLM_DEVICE_ID` (default: `2`) | `bp_wh` with `LLM_MODE=local` |
| LLM NIM sharing the RTVI VLM device | `SHARED_LLM_VLM_DEVICE_ID` (default: `2`) | `bp_wh` with `LLM_MODE=local_shared` |

`LLM_MODE` accepts `local`, `local_shared`, `remote`, or `none`:
- `local` — LLM NIM on its own GPU (`LLM_DEVICE_ID`)
- `local_shared` — LLM NIM colocated with RTVI VLM on `SHARED_LLM_VLM_DEVICE_ID` (use when GPU count is limited)
- `remote` — point at an external LLM endpoint via `LLM_BASE_URL` (no LLM NIM deployed)
- `none` — no LLM, for `bp_wh_kafka` / `bp_wh_redis` / `bp_wh_auto_calib`

RTVI VLM has no equivalent mode setting — it is always deployed locally on `RT_VLM_DEVICE_ID` for `bp_wh`. `VLM_MODE` in the warehouse `.env` is set to `none` because warehouse uses RTVI VLM instead of the standalone VLM NIM path.

## Access Points

**Prefer the HAProxy ingress (port `7777`)** — it gives a single browser-reachable origin and rewrites paths to internal services. Direct ports are only useful for diagnostics from the host. Routes confirmed against `deploy/docker/services/infra/haproxy/haproxy.cfg.template`.

### Via HAProxy ingress (`http://<EXTERNAL_IP>:<HAPROXY_PORT>` — default `<EXTERNAL_IP>:7777`)

| Path | Backend | Profile |
|---|---|---|
| `/` | `vss-agent-ui` (Next.js) | `bp_wh` (returns 503 in `bp_wh_kafka`/`bp_wh_redis` — no UI backend) |
| `/vst`, `/vst/...` | `vss-vios-ingress` (VST / VIOS UI) | All |
| `/storage`, `/storage/...` | `vst-storage` (compat → `/vst/storage/...`) | All |
| `/kibana`, `/kibana/...` | `kibana` | `bp_wh`, or kafka/redis extended (2D or 3D) |
| `/video-analytics-api`, `.../...` | `vss-video-analytics-api` | `bp_wh`, or kafka/redis extended |
| `/behavior-analytics`, `.../...` | `vss-behavior-analytics` | All |
| `/perception-sdr`, `.../...` | `vss-rtvi-cv-sdr` | All |
| `/alert-bridge`, `.../...` | `vss-alert-bridge` | `bp_wh` only |
| `/phoenix`, `.../...` | `phoenix` | `bp_wh` only |
| `/va-mcp`, `.../...` | `vss-va-mcp` | `bp_wh` only |
| `/api`, `/api/...` | `vss-agent` | `bp_wh` only |
| `/api/chat`, `.../...` | `vss-agent-ui` | `bp_wh` only |
| `/chat`, `/static`, `/websocket` | `vss-agent` | `bp_wh` only |

### Direct ports (no HAProxy route — diagnostics only)

| Service | URL | Profile |
|---|---|---|
| NvStreamer UI | `http://<HOST_IP>:31000` | All |
| Auto-Calibration UI | `http://<HOST_IP>:5000` | All |
| Elasticsearch API | `http://<HOST_IP>:9200` | `bp_wh`, or kafka/redis extended |
| VSS Agent API (direct) | `http://<HOST_IP>:8000` | `bp_wh` only (prefer `/api` via HAProxy) |
| VST MCP (direct) | `http://<HOST_IP>:8001` | All |
| Phoenix (direct) | `http://<HOST_IP>:6006` | `bp_wh` only (prefer `/phoenix` via HAProxy) |
| Kibana (direct) | `http://<HOST_IP>:5601` | Prefer `/kibana` via HAProxy |
| Video Analytics API (direct) | `http://<HOST_IP>:8081` (`MDX_PORT`) | Prefer `/video-analytics-api` via HAProxy |
| VST UI (direct) | `http://<HOST_IP>:30888/vst` | Prefer `/vst` via HAProxy |

`EXTERNAL_IP` defaults to `${HOST_IP}` but should be set to the browser-reachable hostname/IP. On Brev, follow the same secure-link pattern as the other VSS profiles (`SKILL.md` Step 1c). The HAProxy `h_main` ACL only routes when the `Host:` header matches `${VSS_PUBLIC_HOST}`, `${EXTERNAL_IP}`, `${HOST_IP}`, `localhost`, or `127.0.0.1` (with or without `:${HAPROXY_PORT}`) — wrong Host headers get a 404 from haproxy.

## Compose File Structure

Deployed from `<repo>/deploy/docker/` (the repo's compose root) using:
- `industry-profiles/warehouse-operations/.env` — all configuration
- `compose.yml` — root top-level include (foundational, monitoring, vst, industry-profiles, etc.)
  - `industry-profiles/compose.yml` — industry sub-include
    - `industry-profiles/warehouse-operations/compose.yml` — warehouse sub-include
      - `industry-profiles/warehouse-operations/warehouse-2d-app/warehouse-2d-app.yml` — 2D app services
      - `industry-profiles/warehouse-operations/warehouse-3d-app/warehouse-3d-app.yml` — 3D app services
      - `industry-profiles/warehouse-operations/warehouse-mv3dt-app/warehouse-mv3dt-app.yml` — MV3DT app services

## App Data

App data (sample videos, perception models) is **not** bundled with the repo. Pick one source:

| Source | When to use | `VSS_DATA_DIR` |
|---|---|---|
| `<repo>/data` | Quick start — drop assets into the repo's `data/` directory | `<repo>/data` |
| Custom local path | Existing dataset on a non-repo path (e.g. `/mnt/warehouse-data`) | user-provided path |
| NGC app-data resource | Reproducing the official sample-video deployment | extracted path of `nvidia/vss-warehouse/vss-warehouse-app-data:<version>` **or** `nvstaging/vss-warehouse/vss-warehouse-app-data:<version>` (staging keys land here) |

Ask the user which source they want and whether they already have the assets on disk. Only run the NGC download (next subsection) when they explicitly choose the NGC source.

### NGC app-data download (optional)

| Artifact | NGC Resource | Local directory after extract |
|---|---|---|
| App data (videos, models) | `nvidia/vss-warehouse/vss-warehouse-app-data:<version>` **or** `nvstaging/vss-warehouse/vss-warehouse-app-data:<version>` | `vss-warehouse-app-data_v<version>/` |

> **Org may be `nvidia` or `nvstaging`.** Production keys access the canonical `nvidia/...` path; staging / NVIDIAN keys typically only see `nvstaging/...`. If you get `403 Access Denied` on one, retry with the other before assuming the resource is missing. Confirm by running `ngc org list` to see which orgs the current key belongs to.

## Known Limitations

- Bounding box overlays do not appear in VST in the minimal profile — Elasticsearch is required for overlay rendering. Metadata is available from the live Kafka/Redis stream only.
- Perception model for `warehouse-loading-dock-3cams-synthetic` is trained on synthetic data — accuracy may vary on custom real-world scenes.
- `nv-warehouse-4cams` dataset is only valid with `BP_PROFILE=bp_wh` and `MODE=2d`.
- `warehouse-4cams-20mx20m-synthetic` dataset is valid with `MODE=3d` or `MODE=mv3dt`.
- MV3DT mode (`MODE=mv3dt`) does not support `bp_wh` (agents) — only `bp_wh_kafka`, `bp_wh_redis`, and `bp_wh_auto_calib`.
- `bp_wh` profile in 2D mode is not supported on IGX-THOR or DGX-SPARK.

---

## Choose your path

| Goal | Where to start |
|------|----------------|
| **New machine / first install** | [Full deploy (Phases 1-9)](#full-deploy-phases-1-9). Run phases in order; each must pass before the next. |
| **Redeploy** (`.env` change, clean restart, broken stack) | [Redeploy](#redeploy). Skips Phases 1–4 — host is already set up and artifacts exist. |
| **Tear down only** (stop and remove containers/volumes; keep files on disk) | [Lifecycle: Tear down](#lifecycle-tear-down). |

**`<repo>`** — path to your `video-search-and-summarization` checkout. All compose commands run from `<repo>/deploy/docker/`, with `--env-file industry-profiles/warehouse-operations/.env`. If you don't know the repo path, **ask explicitly** before running shell commands.

---

## Lifecycle (shared)

Use these sections for **redeploy**, **Phase 8–9**, and **tear down**. Default log file for bring up and monitor:

```bash
LOG=${LOG:-/tmp/warehouse-blueprint.log}
```

### Lifecycle: Tear down

```bash
cd <repo>/deploy/docker
docker compose -f compose.yml --env-file industry-profiles/warehouse-operations/.env down
docker volume prune -f
docker system prune -f

# Pass the SAME env file you used with `docker compose --env-file ...`
bash ./scripts/cleanup_all_datalog.sh -e industry-profiles/warehouse-operations/.env
```

### Lifecycle: Bring up

Pulls images and builds the perception container (~10–15 min first run). If `docker compose` fails to pull from `nvcr.io`, confirm `NGC_CLI_API_KEY` is set and retry `docker login` as shown.

```bash
LOG=${LOG:-/tmp/warehouse-blueprint.log}
cd <repo>/deploy/docker

docker login --username '$oauthtoken' --password "${NGC_CLI_API_KEY}" nvcr.io

nohup docker compose -f compose.yml \
  --env-file industry-profiles/warehouse-operations/.env \
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

- 2D / 3D profiles: `vss-vios-nvstreamer`, `vss-rtvi-cv`, `vss-configurator`, `vss-behavior-analytics`, `vss-auto-calibration`, `vss-auto-calibration-ui`, broker (`kafka` / `redis`), `vss-broker-health-check`, plus the `vss-vios-*` VST stack
- 3D extra: `vss-rtvi-cv-config-adaptor`
- MV3DT profiles: `vss-vios-nvstreamer-mv3dt`, `vss-rtvi-cv-mv3dt`, `vss-rtvi-cv-bev-fusion`, `mosquitto`, `vss-configurator-mv3dt`, `vss-behavior-analytics-mv3dt`, `vss-auto-calibration`, `vss-auto-calibration-ui`, broker (`kafka` / `redis`), `vss-broker-health-check`, plus VST stack
- `bp_wh` extra: `vss-rtvi-vlm`, `vss-alert-bridge`, `vss-agent`, `vss-agent-ui`, `vss-va-mcp`, `vss-haproxy-ingress`, `phoenix`, plus the LLM NIM container (named after `LLM_NAME_SLUG`) when `LLM_MODE=local` / `local_shared`
- Extended extra (kafka/redis, any mode): `vss-haproxy-ingress`, `logstash`, `kibana`, `vss-video-analytics-api` (MV3DT uses `vss-video-analytics-api-mv3dt`)
- `elasticsearch`: `BP_PROFILE=bp_wh` (always), **or** kafka/redis with `MINIMAL_PROFILE=""` (extended, any mode)
- `bp_wh_auto_calib`: only nvstreamer, configurator, auto-calibration, and VST subset

Check FPS (same container for 2D/3D; use `vss-rtvi-cv-mv3dt` for MV3DT):

```bash
# 2D / 3D:
docker logs -f vss-rtvi-cv 2>&1 | grep -i fps | head -5
# MV3DT:
docker logs -f vss-rtvi-cv-mv3dt 2>&1 | grep -i fps | head -5
```

---

## Redeploy

**When to use:** The machine already satisfies [Phase 2](#phase-2-system-prerequisites); the repo is checked out and `VSS_DATA_DIR` is populated. You edited the warehouse `.env`, need a clean restart, or are recovering a bad state.

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

**AMD64:**
```bash
curl -sLo /tmp/ngccli.zip \
  https://api.ngc.nvidia.com/v2/resources/nvidia/ngc-apps/ngc_cli/versions/4.10.0/files/ngccli_linux.zip
sudo mkdir -p /usr/local/lib
sudo unzip -qo /tmp/ngccli.zip -d /usr/local/lib
sudo chmod +x /usr/local/lib/ngc-cli/ngc
sudo ln -sfn /usr/local/lib/ngc-cli/ngc /usr/local/bin/ngc
ngc --version
```

**ARM64 (DGX-SPARK, IGX-THOR):** use `ngccli_arm64.zip`, then same install steps.

#### 1.3 Configure API Key

If no key: go to https://ngc.nvidia.com → **Setup → API Keys → Generate Personal Key** (set **NGC Catalog** permission). Copy immediately.

> **Important:** NGC API keys may look like base64. Use the key exactly as provided — **do not base64-decode it.**

```bash
export NGC_CLI_API_KEY='<key>'
echo "export NGC_CLI_API_KEY='<key>'" >> ~/.bashrc
```

Or configure interactively: `ngc config set`

> Never commit the NGC API key to version control.

#### 1.4 Verify NGC Access

Image paths in `deploy/docker/` reference **both** `nvcr.io/nvidia/vss-core/...` (public org) and `nvcr.io/nvstaging/vss-core/...` (staging org). Which one your key resolves depends on your NGC org membership — list both teams and the warehouse resources visible to it. Confirm the actual paths against `<repo>/deploy/docker/services/**/compose*.{yml,yaml}` and the warehouse `.env` (e.g. `PERCEPTION_IMAGE`, `BEV_FUSION_MV3DT_IMAGE`).

```bash
# Probe both orgs — at least one should succeed for warehouse to deploy
ngc registry image list "nvidia/vss-core/*"     2>&1 | head -10
ngc registry image list "nvstaging/vss-core/*"  2>&1 | head -10
```

**`Missing org` error** → run `ngc config set` (or write `~/.ngc/config` directly) and match the org to the one used when generating the key. Run `ngc org list` to see which orgs the current key has access to before guessing.

---

### Phase 2: System Prerequisites

Run each check in order. **If a check fails, automatically install and re-verify — do not wait for the user.** Only stop if a requirement cannot be met automatically (unsupported hardware, insufficient RAM/CPU).

#### Supported Hardware

`HARDWARE_PROFILE` is a **blueprint setting**, not a string that `nvidia-smi` always prints verbatim. For **discrete GPUs**, match the GPU model from `nvidia-smi` / `lspci` to a row below. **IGX-THOR** and **DGX-SPARK** are **whole-system platforms** (kits/boards): set the profile from product/SKU or vendor docs if you already know the machine type; `nvidia-smi` shows the **on-board NVIDIA GPU name** (e.g. a Thor-class or Spark system GPU), not the text `IGX-THOR` or `DGX-SPARK`. On **DGX Spark**, unified memory can make some `nvidia-smi` memory fields show **Not Supported**; driver and device listing should still be checked per [DGX Spark user guide](https://docs.nvidia.com/dgx/dgx-spark/).

Valid values: `H100, L40, L40S, L4, A6000, RTXA6000, RTXA6000ADA, RTXPRO6000BW, IGX-THOR, DGX-SPARK`. All profiles include tuned `max_streams_supported` for 2D, 3D, and MV3DT modes.

| Discrete GPU (typical `nvidia-smi` name) | HARDWARE_PROFILE |
|---|---|
| RTX PRO 6000 Blackwell | `RTXPRO6000BW` |
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

Minimum required: **Docker Engine ≥ 27.2.0** and **Compose plugin ≥ v2.29.1**. If both are already installed and at or above those versions, **leave them alone** — proceed to §2.3.

```bash
docker --version        # need 27.2.0+
docker compose version  # need v2.29.1+
docker ps               # must run without sudo
```

**Install Docker (if missing):**
```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg lsb-release
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

##### When to downgrade to exactly 27.2.0 / 2.29.x

**Only downgrade if you hit this specific failure during `docker compose up --pull always`:**

```
error from registry: Incorrect Repository Format
```

If you see that, downgrade to the known-good combination matching the reference rig (Docker 27.2.0):

```bash
sudo systemctl stop docker docker.socket 2>/dev/null || true
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --allow-downgrades \
  docker-ce=5:27.2.0-1~ubuntu.24.04~noble \
  docker-ce-cli=5:27.2.0-1~ubuntu.24.04~noble \
  containerd.io=2.2.2-1~ubuntu.24.04~noble \
  docker-compose-plugin=2.29.2-1~ubuntu.24.04~noble
sudo systemctl start docker

# Optional: hold so unattended-upgrades doesn't move them back
sudo apt-mark hold docker-ce docker-ce-cli containerd.io docker-compose-plugin

docker version --format '{{.Server.Version}}'   # → 27.2.0
docker compose version --short                  # → 2.29.2
```

Then re-run `docker compose up --pull always` — the tag-pull will succeed.

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

```bash
docker run --rm --gpus all ubuntu:24.04 nvidia-smi 2>&1 | head -8
```

If it fails:
```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

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

**DGX-SPARK / IGX-THOR only** — cache cleaner:
```bash
sudo tee /usr/local/bin/sys-cache-cleaner.sh << 'EOF'
#!/bin/bash
set -e
echo 0 | tee /proc/sys/vm/nr_hugepages
echo "Starting cache cleaner"
while true; do
  sync && echo 3 | tee /proc/sys/vm/drop_caches > /dev/null
  sleep 3
done
EOF
sudo chmod +x /usr/local/bin/sys-cache-cleaner.sh
sudo -b /usr/local/bin/sys-cache-cleaner.sh
```

**IGX-THOR only** — boost VIC clocks:
```bash
sudo nvpmodel -m 0
sudo jetson_clocks
sudo su -c 'echo performance > /sys/class/devfreq/8188050000.vic/governor'
```

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

---

### Phase 3: Interactive Configuration

**Ask these four questions before touching `.env`.**

#### Q1 — Deployment Mode

> "Which mode?
> - **2d** — 2D detection/tracking with **RT-DETR**, no depth
> - **3d** — 3D perception with depth using **Sparse4D**, requires 4-camera dataset
> - **mv3dt** — Multi-View 3D Tracking: per-camera DeepStream perception + **BEV Fusion** across cameras via MQTT, requires 4-camera dataset"

#### Q2 — Blueprint Profile

**MODE=2d:**
> - **2D Vision AI** — CV-only, no LLM and no VLM. Profile: `bp_wh_kafka` or `bp_wh_redis`. Dataset: `warehouse-loading-dock-3cams-synthetic` (3 streams).
> - **2D Vision AI with Agents** — LLM NIM (local/local_shared/remote) + RTVI VLM (always local). Profile: `bp_wh`. Dataset: `nv-warehouse-4cams` (4 streams).
> - **Warehouse Auto-Calibration** — calibration workflow with warehouse stream/config support, no perception or analytics. Profile: `bp_wh_auto_calib`.

**MODE=3d:**
> - **3D Vision AI** — `bp_wh_kafka` or `bp_wh_redis`. Dataset: `warehouse-4cams-20mx20m-synthetic` (4 streams).
> - **Warehouse Auto-Calibration** — calibration workflow with warehouse stream/config support, no perception or analytics. Profile: `bp_wh_auto_calib`.
>
> **Note:** 3D deployments require calibration files with BEV origin and camera group data. See [Calibration Generation](#calibration-generation) before deploying.

**MODE=mv3dt:**
> - **MV3DT Vision AI** — `bp_wh_kafka` or `bp_wh_redis`. Dataset: `warehouse-4cams-20mx20m-synthetic` (4 streams). No agents profile (`bp_wh`) available.
> - **Warehouse Auto-Calibration** — calibration workflow with warehouse stream/config support, no perception or analytics. Profile: `bp_wh_auto_calib`.
>
> **Note:** MV3DT deployments require calibration files with BEV origin and camera group data. See [Calibration Generation](#calibration-generation) before deploying.

#### Q3 — Stream Type

Skip for `bp_wh` and `bp_wh_auto_calib`. For `bp_wh_kafka` / `bp_wh_redis`:

> "Which broker — **kafka** or **redis**?"

Variable combinations:

```bash
# 2D Vision AI — kafka:
BP_PROFILE=bp_wh_kafka; STREAM_TYPE=kafka; SAMPLE_VIDEO_DATASET="warehouse-loading-dock-3cams-synthetic"; NUM_STREAMS=3

# 2D Vision AI — redis:
BP_PROFILE=bp_wh_redis; STREAM_TYPE=redis; SAMPLE_VIDEO_DATASET="warehouse-loading-dock-3cams-synthetic"; NUM_STREAMS=3

# 2D Vision AI with Agents (RTVI VLM is always local; only LLM_MODE is selectable):
BP_PROFILE=bp_wh; SAMPLE_VIDEO_DATASET="nv-warehouse-4cams"; NUM_STREAMS=4; LLM_MODE=local

# 3D Vision AI — kafka:
BP_PROFILE=bp_wh_kafka; STREAM_TYPE=kafka; SAMPLE_VIDEO_DATASET="warehouse-4cams-20mx20m-synthetic"; NUM_STREAMS=4

# 3D Vision AI — redis:
BP_PROFILE=bp_wh_redis; STREAM_TYPE=redis; SAMPLE_VIDEO_DATASET="warehouse-4cams-20mx20m-synthetic"; NUM_STREAMS=4

# MV3DT Vision AI — kafka:
BP_PROFILE=bp_wh_kafka; STREAM_TYPE=kafka; SAMPLE_VIDEO_DATASET="warehouse-4cams-20mx20m-synthetic"; NUM_STREAMS=4

# MV3DT Vision AI — redis:
BP_PROFILE=bp_wh_redis; STREAM_TYPE=redis; SAMPLE_VIDEO_DATASET="warehouse-4cams-20mx20m-synthetic"; NUM_STREAMS=4

# Warehouse Auto-Calibration (mode-specific — dataset/streams match the mode default):
BP_PROFILE=bp_wh_auto_calib; LLM_MODE=none
```

#### Q4 — Deployment Profile

Skip for `bp_wh` and `bp_wh_auto_calib`. For `bp_wh_kafka` / `bp_wh_redis` (any mode):

> "Which profile?
> - **minimal** — excludes ELK, Video Analytics API, monitoring. Recommended for IGX-THOR.
> - **extended** — full deployment."

```bash
MINIMAL_PROFILE="true"   # minimal
MINIMAL_PROFILE=""       # extended
```

---

### Phase 4: Acquire App Data (first run only)

Compose files ship in the repo — **nothing to download for compose**. Only app data may need to be acquired, and only for the source the user chose in [App Data](#app-data).

**Option A — `<repo>/data`:** ensure assets are present at `<repo>/data` and proceed to Phase 5 (`VSS_DATA_DIR=<repo>/data`).

**Option B — custom local path:** confirm the path exists and has the expected `models/` and `videos/` subdirs, then set `VSS_DATA_DIR=<that path>` in Phase 5.

**Option C — NGC `vss-warehouse-app-data`:**

```bash
export NGC_CLI_API_KEY='<your-ngc-api-key>'

ngc registry resource download-version "nvidia/vss-warehouse/vss-warehouse-app-data:<APP_DATA_VERSION>"
cd vss-warehouse-app-data_v<APP_DATA_VERSION>
tar -xvf vss-warehouse-app-data.tar.gz

sudo chmod -R 777 /path/to/vss-warehouse-app-data
```

See [App Data → NGC app-data download](#ngc-app-data-download-optional) for the current version pin.

---

### Phase 5: Configure the warehouse .env

Edit `<repo>/deploy/docker/industry-profiles/warehouse-operations/.env`. Keys below match the actual file — only the values listed need editing for a typical deploy; the rest have working defaults.

```bash
# --- Deployment selectors (Phase 3 answers go here) ---
MODE=<2d|3d|mv3dt>
BP_PROFILE=<bp_wh|bp_wh_kafka|bp_wh_redis|bp_wh_auto_calib>
STREAM_TYPE=<kafka|redis>           # ignored by bp_wh and bp_wh_auto_calib; set for bp_wh_kafka / bp_wh_redis
MINIMAL_PROFILE="true"              # or "" for extended (bp_wh_kafka / bp_wh_redis only)

SAMPLE_VIDEO_DATASET="<dataset-name>"
NUM_STREAMS=<3|4>

# --- Hardware ---
# Valid: H100, L40, L40S, L4, A6000, RTXA6000, RTXA6000ADA, RTXPRO6000BW, IGX-THOR, DGX-SPARK
HARDWARE_PROFILE=H100

# GPU device IDs (defaults shown — change only if you need a non-default layout)
RT_CV_DEVICE_ID='0'                 # perception (always local)
RT_VLM_DEVICE_ID='1'                # RTVI VLM, bp_wh only (always local)
LLM_DEVICE_ID='2'                   # bp_wh + LLM_MODE=local
SHARED_LLM_VLM_DEVICE_ID='2'        # bp_wh + LLM_MODE=local_shared (LLM colocated with RTVI VLM)

# --- LLM (bp_wh only; set LLM_MODE=none for bp_wh_kafka / bp_wh_redis / bp_wh_auto_calib) ---
# RTVI VLM has no mode — it is always deployed locally for bp_wh.
LLM_MODE=local                      # local | local_shared | remote | none
LLM_NAME=nvidia/nvidia-nemotron-nano-9b-v2
LLM_NAME_SLUG=nvidia-nemotron-nano-9b-v2
# LLM_BASE_URL — only when LLM_MODE=remote

# --- RTVI VLM (bp_wh; always local — these are image/model selectors, not a mode toggle) ---
# vss-rtvi-vlm is always deployed for bp_wh (hardcoded in compose profile bp_wh_2d).
VLM_NAME=nim_nvidia_cosmos-reason2-8b_hf-1208
RTVI_VLM_MODEL_PATH=ngc:nim/nvidia/cosmos-reason2-8b:hf-1208
RTVI_VLM_MODEL_TO_USE=cosmos-reason2

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
HAPROXY_PORT=7777                    # ingress for VSS UI

# --- Credentials ---
NGC_CLI_API_KEY='<your-ngc-api-key>'           # required for local NIMs + image pulls
NVIDIA_API_KEY=''                              # required for build.nvidia.com remote endpoints
OPENAI_API_KEY=''                              # required for OpenAI remote endpoints
```

> **DGX-SPARK (SBSA):** swap to the `-sbsa`-tagged image variants. Comment the default `PERCEPTION_TAG="3.2.0-26.05.1"` and uncomment `PERCEPTION_TAG="3.2.0-sbsa-26.05.1"`. Apply the same pattern to `BEV_FUSION_MV3DT_TAG` (mv3dt only), `RTVI_VLM_IMAGE_TAG`, `VST_*_IMAGE_TAG`, and `NVSTREAMER_IMAGE_TAG`.

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

See [Access Points](#access-points) for service URLs.

---

## Calibration Generation

### BEV Group Origin Calculation

When generating calibration files for multi-camera deployments (3D / MV3DT), use `calculate_origin.py` to compute BEV origin, dimensions, and FOV visualizations for camera groups. This is integrated into the Blueprint configurator but can be run standalone for debugging or batch processing.

Docs: https://docs.nvidia.com/vss/3.1.0/warehouse-docs/3D-profile.html#camera-grouping-utilities

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
| Dataset/mode mismatch | `nv-warehouse-4cams` → `bp_wh` + `MODE=2d`; `warehouse-4cams-20mx20m-synthetic` → `MODE=3d` or `MODE=mv3dt` |
| Redeploy / reset without reinstall | [Redeploy](#redeploy) |
