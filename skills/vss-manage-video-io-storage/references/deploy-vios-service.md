# Deployment Reference: VIOS

## Container Image

VIOS is a **multi-image microservice**. Source: `vst.env` lines 64–66 (canonical image names + tag-var convention).

| Image | Tag pattern | Registry | Role |
|---|---|---|---|
| `nvcr.io/nvstaging/vss-core/vss-vios-sensor:${VST_SENSOR_IMAGE_TAG}` | `3.2.0-26.04.1` style (date-coded) | `nvcr.io` (staging in 3.2.0) | sensor-ms |
| `nvcr.io/nvstaging/vss-core/vss-vios-streamprocessing:${VST_STREAM_PROCESSOR_IMAGE_TAG}` | same | `nvcr.io` | streamprocessing-ms |
| `nvcr.io/nvstaging/vss-core/vss-vios-ingress:${VST_INGRESS_IMAGE_TAG}` | same | `nvcr.io` | vst-ingress |
| `nvcr.io/nvidia/vss-core/sdr:3.1.0` | pinned | `nvcr.io` | SDR (Sensor Distribution & Routing) |
| `nvcr.io/nvidia/vss-core/envoy-proxy:3.1.0` | pinned | `nvcr.io` | Envoy L7 proxy |
| `postgres:17.9-alpine` | upstream Postgres tag | Docker Hub | centralizedb |

- **NGC pull:** the four `nvcr.io/...vss-core/...` images require `docker login nvcr.io` with `NGC_CLI_API_KEY` (`$oauthtoken` username).
- **Architecture support:** x86_64 + aarch64 (Jetson Thor / IGX Thor / AGX Thor). SBSA Grace/Spark uses a separate suffix when applicable (the VIOS rst note is "see canonical `vios-microservices.rst` § VIOS Microservices table" for per-arch container-name suffixes `-smc`, `-2d`, `-3d`, `-dev`).
- **Canonical naming (Finding 2 — IMPORTANT):** the legacy `vss-vst-*` image names are **deprecated**. Always use the `vss-vios-*` names from `vst.env`.

## GPU Requirements

- **GPU required?** **No.** VIOS core (sensor, streamprocessing, ingress, sdr, envoy, centralizedb) is pure CPU. Source: `vios-microservices.rst` — no GPU notes in the VIOS Microservices table.
- **Minimum VRAM:** not applicable.
- **Supported GPU architectures:** not applicable; the hardware-acceleration features (HW WebRTC, HW decode/encode) use platform-native APIs on the host where applicable, not a discrete-GPU device allocation.
- **GPU count per instance:** 0.
- **Can share GPU with other services?** N/A — VIOS does not reserve a GPU device.
- **Compose snippet for device reservation:** none. The VIOS service blocks have no `deploy.resources.reservations.devices` clause.

This is a notable IN-1 property: VIOS imposes no GPU contention, leaving all GPU planning to RT-VLM (and any future RTVI / NIM peer).

## CPU & Memory

- **Minimum CPU cores:** 4 cores recommended for a single-stream IN-1 deployment; scale with `NUM_STREAMS`-like provisioning (the RTSP Server and Recorder services support 1–5 horizontally-scaled instances). Source: `vios-microservices.rst` § VIOS MS Horizontal Scaling.
- **Minimum RAM:** 8 GB for the VIOS stack baseline. Recording-heavy deployments add proportionally with concurrent streams and bitrate (see Storage formula below).
- **`shm_size`:** not set in `vst.env` defaults — relies on Docker default. Set explicitly only if WebRTC or large clip downloads OOM the default shared memory.
- **`ulimits`:** none required for the VIOS containers.

## Storage

| Mount Path (host → container) | Purpose | Type | Size estimate | Required permissions |
|---|---|---|---|---|
| `${CLIP_STORAGE_PATH}` → `/opt/clip_storage` | Clip storage; **shared bind mount with RT-VLM** for IN-1 on-demand path | bind | grows with on-demand uploads (typical: 10–50 GB) | writable by UID 1001 — `chmod 777` on the leaf dir (not recursive on the parent); `chown -R 1001:1001` is the cleanest approach |
| `${VST_VIDEO_STORAGE_PATH}` → `/opt/vst_video` | Long-term continuous recording storage | bind | capped at `${VST_VIDEO_STORAGE_SIZE_MB}` (default 100 GB) | writable by UID 1001 |
| `${VST_TEMP_FILES_PATH}` → `/opt/temp_files` | Temp files (transcode scratch, etc.) | bind | low (< 5 GB) | writable by UID 1001 |
| `${VST_DATA_PATH}` → `/opt/vst_data` | Internal data + DB seed + logs | bind | < 5 GB | writable by UID 1001 |
| `${VST_CONFIG_PATH}` → `/opt/vst_config` (ro) | VIOS configs (JSON, scripts) | bind (ro) | minimal | readable by container |

**Storage capacity formula** (per `vios-microservices.rst` § Storage Calculation):
- `Storage (GB/day) = Bitrate (Mbps) × 10.546875`
- For 8 Mbps stream: ~84.4 GB/day per stream.

**Persistent vs. wiped:** all VIOS storage is host-bind, so `docker compose down -v` does NOT wipe them. Hand-rm `${VST_VOLUME}/` only when you intentionally want to lose recorded video. The PostgreSQL container `vss-vios-postgres` may use a named volume — confirm in the live compose; on `down -v` that volume IS wiped, taking sensor configuration with it.

**Required host-path setup before first `up`:**

```bash
mkdir -p ${VSS_DATA_DIR}/data_log/vst/{clip_storage,vst_video,temp_files,vst_data}
sudo chown -R 1001:1001 ${VSS_DATA_DIR}/data_log/vst
# Alternatively, if sudo unavailable:
# sudo setfacl -R -m u:1001:rwx ${VSS_DATA_DIR}/data_log/vst
```

## Startup Behavior

- **Expected startup time:**
  - First boot: 60–120 s — PostgreSQL initialization + sensor-ms boot + Ingress NGINX.
  - Warm cache: 30–60 s.
- **Startup ordering dependencies:** uses explicit wait-poller containers (`sensor-bp-wait-bp-configurator`, `sensor-bp-wait-storage`) instead of `depends_on` on external services. PostgreSQL must be healthy before sensor-ms / streamprocessing-ms start (compose declares this with `depends_on: vss-vios-postgres: condition: service_healthy`).
- **Health check endpoint:** `GET http://localhost:${VST_INGRESS_HTTP_PORT}/vst/api/v1/sensor/version`. Expect HTTP 200 + version JSON.
- **Health check tuning:** `interval: 10s, timeout: 5s, retries: 20, start_period: 30s` (per `integrate-vios-service.md` snippet).
- **Log signatures of healthy startup:**
  - `vss-vios-ingress`: `nginx: ready` (per the NGINX boot log) and the healthcheck flipping to healthy.
  - `vss-vios-postgres`: `database system is ready to accept connections`.
  - `vss-vios-sensor`: `Sensor Management Service started on :30000` (or equivalent).
  - `streamprocessing-ms-dev`: `Stream Processing Service started`.

## Environment Variables — Required for Upload-to-Caption Path

These env vars MUST be set in the consumer `.env` (or `vst.env` must be loaded into the patched VIOS compose include) before deploying — they affect runtime correctness, not just configuration. The skill's Step 6 `.env` generation must emit them.

| Variable | Required value | Why required | Source |
|---|---|---|---|
| `VST_INSTALL_ADDITIONAL_PACKAGES` | `true` | The `vss-vios-streamprocessing:2.1.0-26.05.2` image ships WITHOUT `libavcodec` / `libavformat` / `libavutil`. The container's entrypoint runs `apt install` to install them at startup ONLY when this env var is `true`. Without it, **PUT video uploads fail with `InvalidParameterError: Failed to get media information`** because both the primary (libav) and fallback (GStreamer discoverer) extraction paths fail inside the container. Finding 9, 2026-05-25. | `vst.env:28` (upstream default `true`); live verification 2026-05-25 |
| `VST_INGRESS_IMAGE_TAG` | `2.1.0-26.05.2` (not the RT-VLM tag `3.2.0-26.05.3`) | VIOS components ship on a separate version line from RT-VLM; the `3.2.0-26.05.3` tag does NOT exist in `nvcr.io/nvstaging/vss-core/vss-vios-ingress`. Manifest probe returns `no such manifest`. | `dev-profile-base/.env:220`; live verification 2026-05-25 |
| `VST_SENSOR_IMAGE_TAG` | `2.1.0-26.05.2` | Same | `dev-profile-base/.env:218` |
| `VST_STREAM_PROCESSOR_IMAGE_TAG` | `2.1.0-26.05.2` | Same | `dev-profile-base/.env:217` |
| `NVSTREAMER_IMAGE_TAG` | `2.1.0-26.05.2` | Same | `dev-profile-base/.env:219` |
| `CENTRALIZE_DB_PASSWORD` | non-empty (any value) | PostgreSQL password — `vst.env` has no default; deploy hangs in `password authentication failed` on first init without this set | `vst.env` |
| `KAFKA_BOOTSTRAP_URL` | `kafka:9092` (compose-internal hostname) | Used by streamprocessing-ms for `camera_streaming` event publication. Wrong value → silent caption-pipeline break | `vst.env` |
| `REDIS_HOSTADDR` / `REDIS_PORT` | `redis` / `6379` (compose-internal) | SDR watches `vst.event` on this Redis. Wrong value → SDR never registers streamprocessing-ms with envoy → 503 on `/record/*` calls | `vst.env` |

> **Image registry path:** all VIOS 3.2.0 / 2.1.0-26.05.2 components ship under `nvcr.io/nvstaging/vss-core/*` (NOT `nvcr.io/nvidia/vss-core/*`). The `nvstaging` org gates access to the staging registry path — ensure the deploying NGC key has staging-registry access. Source: `vst.env` lines 70–72 + manifest probe 2026-05-25.

## Known Deployment Issues

| Symptom | Root cause | Fix |
|---|---|---|
| `invalid spec: :/opt/clip_storage: empty section between colons` (or similar mount-spec error) on dry-run | `CLIP_STORAGE_PATH` empty — `vst.env` not loaded into the include | Ensure the patched VIOS compose has `env_file: [..., vst.env]` on its `include:` directive; this was Finding 1 of the IN-1 first run |
| Containers loop on restart with `Permission denied` writing to `/opt/clip_storage` | Host bind dir not writable by UID 1001 | `sudo chown -R 1001:1001 ${VSS_DATA_DIR}/data_log/vst` (or use ACL grant) |
| Containers boot but `sensor/version` returns 502 / connection refused | Ingress (`vss-vios-ingress`) ready but `vss-vios-sensor` still booting → 502 from NGINX | Wait for `vss-vios-sensor` healthcheck; the Ingress start_period (`30s`) is shorter than sensor-ms boot — give it 60–120 s on first boot |
| `vss-vios-postgres` healthcheck fails with `password authentication failed` | `CENTRALIZE_DB_PASSWORD` unset or rotated since last init | Set explicitly in `.env`; on first-time init Postgres adopts whatever was passed; subsequent runs require the same value or a volume reset |
| Camera RTSP add returns HTTP 500 with `unable to connect to RTSP` | Camera RTSP credentials missing or wrong; or the camera is unreachable from the VIOS host | Provide `username`/`password` in `POST /sensor/add`; confirm L3 reachability from the VIOS host to the camera |
| Compose rejects `vst.env`-style image variables as empty (`vss-vios-sensor:`) | `VST_*_IMAGE_TAG` env vars unset — no default in `vst.env` for the tag halves | Set `VST_SENSOR_IMAGE_TAG=3.2.0-26.04.1` etc. in the consumer `.env`; do not rely on the `vst.env` providing them |
| Image-name typo `vss-vst-sensor` (legacy) fails to pull | Catalog or env using deprecated legacy image names | Use the canonical `vss-vios-*` names from `vst.env` lines 64–66 — Finding 2 |
| `port already allocated` for `30888` | Other service binding the Ingress port | Override `VST_INGRESS_HTTP_PORT` to an unused port |
| `POST /vst/api/v1/sensor/add` returns `{"error_code":"InvalidParameterError","error_message":"Invalid Parameters"}` instantly, no validator field cited in `vss-vios-sensor` logs | `vss-vios-envoy` or `vss-vios-sdr` not running. `vss-vios-sensor` env contains `STREAM_PROCESSOR_MODULE_ENDPOINT=http://localhost:10000` (the envoy front-end); without envoy listening, the adaptor pre-check fails. Finding 5, 2026-05-23. | Confirm all four `vss-vios-sensor`, `vss-vios-streamprocessing`, `vss-vios-sdr`, `vss-vios-envoy` are running (`docker ps \| grep vios`); confirm `nc -z localhost 10000` succeeds; the skill's Step 6.5 Patch 1 must add the invented profile flag to all four services |
| `POST /vst/api/v1/sensor/add` rejects payload with field name `url` | The in-container OpenAPI YAML (`/home/vst/vst_release/webroot/doc/sensor_management_ms.yaml`) is stale — declares `url` but the binary requires `sensorUrl`. Finding 6, 2026-05-23. | Use `sensorUrl` instead of `url`; cross-check against `services/agent/src/vss_agents/tools/vst/utils.py` for the authoritative payload shape |
| `vss-vios-envoy` exits immediately printing usage banner; or `vss-vios-sdr` logs `Cluster config file (/wdm-configs/docker_cluster_config.json) does not exist` | Patched compose copy doesn't include the relative-path bind-mount source files (`envoy.yaml` and `sdr-config/`). Docker silently creates them as empty directories on bind. Finding 7, 2026-05-23. | Step 6.5 Patch 3 must `cp -r` the upstream `services/vios/sdr/streamprocessing/envoy.yaml` + `services/vios/sdr/streamprocessing/sdr-config/` into the build-output's patched tree alongside the patched `docker-compose.yaml` |
| Envoy on port 10000 returns 503 `Service Unavailable` for `/record/*` or `/replay/*` calls | SDR hasn't registered `streamprocessing-ms` with envoy's CDS yet (logs show `cds: add 0 cluster(s), remove 2 cluster(s)`) | Wait 30 s; check `docker logs vss-vios-sdr` for `add operation success updating the Route mapping`; if persistent, restart SDR (`docker restart vss-vios-sdr`) — the WDM agent needs to see streamprocessing-ms as healthy before it adds the route |
| Sensor registers (`state: online`) but VOD URL `rtsp://<host>:30564/vod/<id>` returns 404 | Recording is active (state=2) but no segment has rolled to disk yet | Wait for the segment-rotation interval (default 5 min); confirm `SELECT * FROM video_record_details` in `vss-vios-postgres` shows non-zero rows; explicitly trigger via `POST /vst/api/v1/record/<sensorId>/start` if recording was not auto-started |
| `GET /vst/api/v1/sensor/list` or `/sensor/<id>/streams` returns **HTTP 502 Bad Gateway** or stale results | Leftover `*-smc` containers from a prior alerts-profile deploy survived teardown and lose the port-bind race against the new `*-dev` containers (both use `network_mode: host` on ports 30000 / 30888). See issue [#151](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization/issues/151). | Re-run `/vss-deploy-profile` (its Step 0 teardown grep now covers `sensor-ms-*`, `vst-ingress-*`, `centralizedb-*`, `storage-ms-*`, `sdr-*`, `envoy-*`, `rtspserver-ms-*`) or manually `docker rm -f` any surviving `*-smc` containers before re-deploying. Other VIOS paths (`storage/file/*`, `replay/stream/*/picture/url`) are unaffected. |
| `POST /vst/api/v1/files` returns 404 or 503 | Wrong endpoint — VIOS does NOT expose a generic `POST /files` upload route. The supported endpoint is `PUT /vst/api/v1/storage/file/<filename>?timestamp=<iso>` (new v2) or `PUT /vst/api/v1/storage/file/<filename>/<timestamp>` (legacy v1). | Switch the client to the PUT API; see `integrate-vios-service.md § Integration Interfaces > Inputs > Upload video file` and `references/api-reference.md § 8`. |
| `PUT /vst/api/v1/storage/file/<name>?timestamp=<iso>` returns `{"error_code":"InvalidParameterError","error_message":"Failed to get media information"}` and uploads are immediately deleted (`fs_utils.cpp: Deleting File`) | The `vss-vios-streamprocessing:2.1.0-26.05.2` image ships WITHOUT bundled libav (`libavcodec`/`libavformat`/`libavutil`). Both primary (`LibavWrapper: Failed to load libav libraries dynamically`) and fallback (`gst_discoverer_discover_uri failed`) media-information paths fail. The container's entrypoint apt-installs these libs only when `VST_INSTALL_ADDITIONAL_PACKAGES=true`. Finding 9, 2026-05-25. | Set `VST_INSTALL_ADDITIONAL_PACKAGES=true` in `.env` (upstream `vst.env:28` default — gets clobbered if the consumer `.env` declares it empty). After fix, container takes ~30 s extra on first boot for the apt-install step; verify with `docker exec vss-vios-streamprocessing ls /usr/lib/x86_64-linux-gnu/libavformat.so.60`. |
| `/url`-variant snapshot or clip responses contain `"imageUrl":"http://http://localhost:30888/..."` (double `http://`) and `curl $url` fails with `Could not resolve host: http` | Upstream URL-construction defect in `vss-vios-streamprocessing:2.1.0-26.05.2` — VIOS prepends `http://` to a value that already contains the scheme. Finding 8, 2026-05-25. | (a) Client-side: strip the leading `http://http://` → `http://` before issuing the secondary GET; OR (b) preferred — use the binary direct endpoints (`/storage/file/<id>?...`, `/replay/stream/<id>/picture?...`, `/storage/stream/<id>/picture?...`). The binary endpoints return the actual bytes correctly. See `integrate-vios-service.md § Integration Interfaces > Inputs > VST Storage Management API`. |
| `docker compose up -d` hangs indefinitely with no container creation, no error printed | Compose detected named-volume `driver_opts` drift between prior deploy and current `.env` (typical for `mdx_mdx-elastic-data`, `mdx_mdx-elastic-logs`, `mdx_mdx-kafka` when host bind paths shift). Compose prompts `Volume "X" exists but doesn't match configuration in compose file. Recreate (data will be lost)?` — but stdout is buffered and the prompt is invisible. Finding 10, 2026-05-25. | Run `docker volume rm mdx_mdx-elastic-data mdx_mdx-elastic-logs mdx_mdx-kafka` BEFORE re-deploy; OR pass `--yes` to `docker compose up` (auto-accepts the recreate prompt). The host data dirs they bind into (`${MDX_DATA_DIR}/data_log/elastic/{data,logs,kafka}`) survive the volume removal. The skill's generated `deploy-<flag-slug>` skill should default to `--yes` on `up -d`. |

## Prerequisites

- **Docker Engine:** 28.2+
- **Docker Compose plugin:** 2.36+ (the upstream compose uses `${VAR:+:path}` conditional-bind syntax that older Compose rejects on `config`)
- **NVIDIA Driver:** not required (CPU-only)
- **NVIDIA Container Toolkit:** not required for VIOS itself
- **API keys:**
  - `NGC_CLI_API_KEY` — for `docker login nvcr.io` to pull the four `vss-core/*` images
- **OS packages:** standard Linux base; `curl`, `jq` for smoke tests.
- **Disk space:** ≥ 50 GB for clip storage + recorded video at modest stream counts; scale per the storage formula.
- **Network reachability:** `nvcr.io` for image pulls; camera RTSP endpoints from the VIOS host; the configured Kafka broker + Redis at the addresses in `vst.env`.
- **Filesystem setup:** the `${VSS_DATA_DIR}/data_log/vst/{clip_storage,vst_video,temp_files,vst_data}` host tree must exist and be writable by UID 1001 before the first `up`.

## Dry Run

```bash
# Resolve the VIOS compose (must pre-set VSS_APPS_DIR + VSS_DATA_DIR + VST_*_IMAGE_TAG + HOST_IP)
docker compose --env-file <consumer.env> -f deploy/docker/services/vios/compose.yml config --no-interpolate
```

When build-vision-agent generates IN-1, it uses the **patched** copy at `build-output/patched/services/vios/compose.yml` and resolves against `build-output/.env`; never against the upstream tree directly (per `feedback_build_output_self_contained`).

## Verify Deployment

```bash
# Ingress + sensor-ms healthy
curl -f http://localhost:30888/vst/api/v1/sensor/version

# Sensor enumeration (empty array on a fresh deploy is fine)
curl http://localhost:30888/vst/api/v1/sensor/list

# PostgreSQL liveness
docker exec vss-vios-postgres pg_isready -U vst

# Confirm the clip-storage shared bind is wired correctly
docker exec vss-vios-sensor ls -la /opt/clip_storage
ls -la ${VSS_DATA_DIR}/data_log/vst/clip_storage  # same dir from host side
```

## Tear Down

```bash
# Stop, preserve everything on disk (clip storage, video storage, DB volume)
docker compose -f deploy/docker/services/vios/compose.yml --profile bp_developer_in_1 down

# Stop + wipe named volumes (centralizedb may live in one — kills sensor configs)
docker compose -f deploy/docker/services/vios/compose.yml --profile bp_developer_in_1 down -v

# Host-side cleanup (DESTRUCTIVE — removes all recorded video)
# rm -rf ${VSS_DATA_DIR}/data_log/vst
```