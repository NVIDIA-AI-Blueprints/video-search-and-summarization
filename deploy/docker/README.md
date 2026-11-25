# Docker deployment (`deploy/docker`)

This tree is the Docker Compose packaging for **Video Search & Summarization**. The root **`compose.yml`** pulls three layers together:

| Include | Role |
|---------|------|
| **`services/compose.yml`** | Shared microservices (infra, VIOS, UI, RTVI, NIMs, etc.) |
| **`developer-profiles/compose.yml`** | Developer profiles: **base**, **lvs**, **alerts**, **search** |
| **`industry-profiles/compose.yml`** | Industry blueprints (e.g. **warehouse-operations**) |

Run Compose from **`deploy/docker`** so relative paths resolve correctly.

---

## Environment files and precedence

The deployment files use a layered environment model. Later `--env-file`
arguments override earlier ones, so order matters.

| File | Role |
|------|------|
| **`containers.env`** | Shared first-party container registry and tag defaults. Pass this before profile env files when running Compose directly. |
| **`developer-profiles/dev-profile-*/.env`** / **`industry-profiles/*/.env`** | Stable profile defaults. These files should not carry machine-specific paths, host ports, credentials, or generated runtime values. |
| **`developer-profiles/dev-profile-*/overrides.env`** | Checked-in developer-profile template. Copy it to `user-overrides.env` before direct Compose deployment; do not edit the template. |
| **`developer-profiles/dev-profile-*/user-overrides.env`** | Ignored user-managed direct-Compose overlay for hardware, model placement, endpoint URLs, host paths, credentials, public ingress, host-published ports, and `COMPOSE_PROFILES`. |
| **`industry-profiles/*/overrides.env`** | Mutable deployment-specific defaults for industry profiles. |

When running Compose directly, first create the ignored user-managed overlay from the
checked-in template (repeat the copy to discard prior layout choices):

```bash
cp <profile>/overrides.env <profile>/user-overrides.env
```

Then pass `containers.env`, the profile `.env`, and the user overlay:

```bash
--env-file containers.env \
--env-file <profile>/.env \
--env-file <profile>/user-overrides.env
```

Before direct Compose bring-up, update the deployment-specific placeholders in
`user-overrides.env`, especially `VSS_APPS_DIR`, `VSS_DATA_DIR`, `HOST_IP`,
`EXTERNAL_IP`, credentials, and the active `COMPOSE_PROFILES`.

---

## Developer profiles

For agent-driven deployment, use
[`/vss-build-vision-ai`](../../skills/vss-build-vision-ai/SKILL.md). Follow its
build workflow: write `_builds/<name>/override.env` and `compose.yml`, resolve,
normalize and validate `resolved.yml`, then deploy that exact resolved file.
The manual commands below use a profile's `user-overrides.env` instead; they do
not replace the skill's workflow.

For manual deployment, select `base`, `lvs`, `search`, or `alerts` and copy its
checked-in template once:

```bash
cd /path/to/video-search-and-summarization/deploy/docker
profile=search  # base, lvs, search, or alerts
profile_dir="developer-profiles/dev-profile-${profile}"
cp "$profile_dir/overrides.env" "$profile_dir/user-overrides.env"
```

Edit `user-overrides.env` before running Compose. Set concrete host paths,
addresses and credentials, and select hardware, GPU placement, local or remote
models, and the full `COMPOSE_PROFILES` service set. Compose does not derive
these settings from `HARDWARE_PROFILE` or `MODE`. The checked-in template's
comments describe the settings; prepare the writable data directories described
below before bring-up.

For Alerts, keep `MODE`, `COMPOSE_PROFILES`, notification configuration and
RT-VLM publishing settings aligned. The template defaults to `2d_cv`
(verification); `2d_vlm` (real-time) uses `COMPOSE_PROFILES_VLM` and the
accompanying settings in the
[Alerts mode reference](../../skills/vss-build-vision-ai/references/profiles/alerts.md#mode-selected-backend-configuration).
Changing `MODE` alone is insufficient.

Validate the effective configuration, then start the selected profile with the
same env layers:

```bash
docker compose -f compose.yml \
  --env-file containers.env \
  --env-file "$profile_dir/.env" \
  --env-file "$profile_dir/user-overrides.env" \
  config

docker compose -f compose.yml \
  --env-file containers.env \
  --env-file "$profile_dir/.env" \
  --env-file "$profile_dir/user-overrides.env" \
  up -d --pull always --build
```

### LVS GPU hardware metrics

The LVS developer profile starts NVIDIA DCGM Exporter alongside the application.
It uses NVIDIA Data Center GPU Manager to expose host GPU telemetry in Prometheus
format at `http://localhost:9400/metrics`, including GPU and decoder utilization,
frame-buffer usage, power draw, and temperature when supported by the GPU and
driver.

```bash
# Verify the exporter is ready and inspect its GPU metrics.
curl --fail http://localhost:9400/metrics |
  grep -E 'DCGM_FI_DEV_(GPU_UTIL|DEC_UTIL|FB_USED|POWER_USAGE|GPU_TEMP)'
```

Set `DCGM_EXPORTER_HOST_PORT` in
`developer-profiles/dev-profile-lvs/user-overrides.env` if port 9400 is already in
use. The exporter requires the NVIDIA driver, NVIDIA Container Toolkit, and a
GPU supported by DCGM. Its `/metrics` endpoint can be scraped directly by
Prometheus, Dynatrace, or another Prometheus-compatible monitoring system.

### RTVI CV startup policy

- Docker uses one canonical RTVI CV startup entrypoint: `services/rtvi/rtvi-cv/ds-start.sh`.
- Developer profiles (**alerts**, **search**) and warehouse **2D/3D** use the shared startup path selected by env/config data.
- Per-profile startup wrapper scripts are not used.
- **MV3DT is the documented exception** and keeps its dedicated `ds-start-mv3dt.sh` command override, invoked with `--tracker-reid` since this mode runs appearance-based ReID.
- Model acquisition for **developer profiles** (alerts, search) and **warehouse RT-CV profiles** (2D, 3D, MV3DT) runs as phase 0 of the perception startup script (`ds-start.sh` / MV3DT `ds-start-mv3dt.sh`) when a per-profile `models-download.json` is mounted. There is no separate download init service for detection/pose models. Warehouse still uses the pre-extracted `VSS_DATA_DIR` bundle for videos, playback, and calibration (see the warehouse section below).
- **ReID/embedding assets are the exception to that rule.** MV3DT runs a one-shot `vss-reid-embed-init-mv3dt` container that populates `$VSS_DATA_DIR/models/reid` (CLIP-ReID tracker ONNX + SigLIP2) before the ReID service and perception start. See the warehouse MV3DT reference for the full pipeline.

### Direct Compose usage and data directories

For manual Compose deployment, use the env-file order and edited
`user-overrides.env` described above. Host directories must exist and be
writable before bring-up.

Create writable host directories for the bind-mounted infrastructure volumes
before starting a direct Compose stack:

```bash
export VSS_DATA_DIR=/path/to/vss-apps-data

mkdir -p \
  "$VSS_DATA_DIR/data_log/elastic/data" \
  "$VSS_DATA_DIR/data_log/elastic/logs" \
  "$VSS_DATA_DIR/data_log/kafka" \
  "$VSS_DATA_DIR/data_log/redis/data" \
  "$VSS_DATA_DIR/data_log/redis/log"

chmod -R 777 "$VSS_DATA_DIR/data_log"
```

The root compose maps Elasticsearch data/log volumes to
`$VSS_DATA_DIR/data_log/elastic/{data,logs}`, Kafka data to
`$VSS_DATA_DIR/data_log/kafka`, and Redis data/logs to
`$VSS_DATA_DIR/data_log/redis`. Missing or non-writable host directories can cause
startup failures such as Kafka being unable to write `/tmp/kafka-data/cluster_id` or
Elasticsearch being unable to open `gc.log`.

### TURN / WebRTC relay

The warehouse VST UI uses WebRTC for live playback. When VST containers run on the Compose bridge network, browsers cannot reach Docker-only media candidates directly, so `services/infra/compose.yml` includes a coturn-based `turnserver` service for warehouse profiles. It exposes the TURN listener and relay range on the host. Developer profiles do not start this TURN service.

Default ports:

| Variable | Default | Purpose |
|----------|---------|---------|
| `TURN_HOST_PORT` / `TURN_PORT` | `3478` | TURN UDP/TCP listener |
| `TURN_MIN_RELAY_HOST_PORT` / `TURN_MAX_RELAY_HOST_PORT` | `20000` / `20040` | Host relay port range |
| `TURN_MIN_RELAY_PORT` / `TURN_MAX_RELAY_PORT` | `20000` / `20040` | Container relay port range |

Docker binds every published relay port before the container starts, so keep the host relay range outside the kernel ephemeral port range (`sysctl net.ipv4.ip_local_port_range`, `32768-60999` by default). Inside that range, any short-lived outbound socket can take a relay port and fail `docker compose up` with `address already in use`. Also avoid the Kubernetes NodePort range (`30000-32767`) on hosts that run Kubernetes.

Set `TURN_PUBLIC_HOST` to the DNS name or IP address that browser clients use to reach the deployment, and set `TURN_EXTERNAL_IP` to the host IP coturn should advertise. The warehouse profile uses a non-secret default `TURN_USERNAME` and starts a `turnserver-init` job that generates a random password once in the `vss-turn-password` Docker volume. Coturn and VST mount that same generated file; the VST startup helper derives the static TURN URL in the format `user:password@host:port` from `TURN_USERNAME`, the generated password file, `TURN_PUBLIC_HOST`, and `TURN_HOST_PORT`.

For the bundled turnserver, leave `VST_STATIC_TURNURL_LIST` empty:

```env
TURN_HOST_PORT=3478
TURN_PORT=3478
TURN_USERNAME=vss
TURN_PASSWORD_BYTES=32
VST_STATIC_TURNURL_LIST=
```

Remove the Compose-created `vss-turn-password` Docker volume and restart the warehouse profile to rotate the generated password. Only set `VST_STATIC_TURNURL_LIST` for external or multiple TURN endpoints; treat it as sensitive because it embeds TURN credentials.

The warehouse VST streamprocessing startup helper also forces `network.use_coturn_auth_secret=false` and `network.coturn_turnurl_list_with_secret=[]`, matching the static username/password mode. Developer VST streamprocessing and NvStreamer services do not apply this WebRTC/TURN patch.

### Check deployment status

List running and completed containers for the configured Compose project (the
developer-profile default is `vss`):

```bash
project="${COMPOSE_PROJECT_NAME:-vss}"
docker ps -a \
  --filter "label=com.docker.compose.project=${project}" \
  --format 'table {{.Names}}\t{{.Status}}'
```

Do not treat every exited container as a deployment failure. One-time initialization
and dependency-check containers normally finish with status `Exited (0)` after they
complete successfully. Investigate containers that exit with a nonzero status, are
`unhealthy`, or repeatedly restart.

The expected one-time containers below are derived from the active profiles in
`deploy/docker/services/` and `deploy/docker/developer-profiles/`. Model overrides
can add, remove, or rename model-specific initialization containers.

| DevX page / profile | Containers that may normally show `Exited (0)` |
|---|---|
| Base (`base`) | `llm-nim-init` for the default local LLM. |
| Video Summarization (`lvs`) | `llm-nim-init`, `vss-elasticsearch-init`, `vss-kafka-topics`, `vss-kibana-init`, `vss-broker-health-check`, `sdrc-init-dirs`, `sdrc-render-config`, `sdrc-wdm-env-from-config`, and `sdrc-wait-for-redis`. |
| Search (`search`) | `llm-nim-init`, `vss-elasticsearch-init`, `vss-kafka-topics`, `vss-kibana-init`, and `vss-broker-health-check`. Perception models are downloaded by the perception startup script, not a separate init container. |
| Alert Verification (`alerts`, `MODE=2d_cv`) | The same one-time containers as Search. Perception models are downloaded by the perception startup script. |
| Real-Time Alerts (`alerts`, `MODE=2d_vlm`) | The same one-time containers as Search. This mode does not start the CV perception service. |

### Stop or reset a developer profile

Run these commands from `deploy/docker`, with `profile_dir` set to the same
profile directory used for bring-up. Both remove project containers, orphans,
and the Compose network. The project defaults to `vss`, but respects an explicit
`COMPOSE_PROJECT_NAME`:

```bash
project="${COMPOSE_PROJECT_NAME:-vss}"

# Stop the deployment and preserve named volumes.
docker compose -p "${project}" -f compose.yml \
  --env-file containers.env \
  --env-file "$profile_dir/.env" \
  --env-file "$profile_dir/user-overrides.env" \
  down --remove-orphans

# Reset the deployment and remove named and anonymous volumes.
docker compose -p "${project}" -f compose.yml \
  --env-file containers.env \
  --env-file "$profile_dir/.env" \
  --env-file "$profile_dir/user-overrides.env" \
  down -v --remove-orphans
```

The `-v` option removes Docker-managed volumes, including VIOS PostgreSQL data,
Phoenix telemetry, Logstash libraries, and local NIM/RTVI model caches. It also
removes the Docker volume objects for bind-backed local volumes, but it does not
erase their host directories. Direct bind mounts likewise survive Compose teardown.

The default local deployments use the following Compose volume keys. At runtime,
Compose prefixes each key with the project name; for example, `kafka-data` becomes
`vss_kafka-data` under the default project. Model and local/remote mode selections
can change the model-cache set.

| DevX page / profile | Compose volume keys used by the default local deployment |
|---|---|
| All profiles | `agent-eval` (bind-backed by `${VSS_DATA_DIR}/agent_eval`), `phoenix-data`, `vios_pg_data`, `nemotron_3_5_lightning_cache`, `rtvi-hf-cache`, and `rtvi-ngc-model-cache`. |
| Base (`base`) | The shared volumes above. |
| Video Summarization (`lvs`) | Shared volumes plus bind-backed `elastic-data`, `elastic-logs`, and `kafka-data`, and Docker-managed `logstash-libs`. RT-VLM can also create anonymous `/dummy` volumes when optional host paths are unset. |
| Search (`search`) | LVS volumes plus `rtvi-triton-model-repo`; RT-Embed shares the RTVI cache volumes. |
| Alert Verification and Real-Time Alerts (`alerts`) | The LVS volume set. |

The deployment also uses host-backed local volumes and direct bind mounts. Their
host data survives both Compose commands above, including `down -v`:

| DevX page / profile | Mutable host-mounted data |
|---|---|
| All profiles | `${VSS_DATA_DIR}/agent_eval`, `${VSS_DATA_DIR}/data_log/redis/{data,log}`, and the VST paths below `${VSS_DATA_DIR}/data_log/vst` (`clip_storage`, `temp_files`, `vst_data`, and `vst_video`). |
| Video Summarization, Search, and both Alerts modes | `${VSS_DATA_DIR}/data_log/elastic/{data,logs}` and `${VSS_DATA_DIR}/data_log/kafka`, through bind-backed local volumes, in addition to the shared paths. |
| Video Summarization (`lvs`) | `${VSS_DATA_DIR}/data_log/nvstreamer/vst_data` and `${VSS_DATA_DIR}/videos/dev-profile-lvs`. |
| Search (`search`) | `${VSS_DATA_DIR}/data_log/nvstreamer/{streamer_videos,vst_data}`, `${VSS_DATA_DIR}/data_log/vss_video_analytics_api`, and `${VSS_DATA_DIR}/models`. |
| Alert Verification | `${VSS_DATA_DIR}/data_log/nvstreamer/vst_data`, `${VSS_DATA_DIR}/data_log/vss_video_analytics_api`, `${VSS_DATA_DIR}/models`, and `${VSS_DATA_DIR}/videos/dev-profile-alerts`. |
| Real-Time Alerts | `${VSS_DATA_DIR}/data_log/nvstreamer/vst_data`, `${VSS_DATA_DIR}/data_log/vss_video_analytics_api`, and `${VSS_DATA_DIR}/videos/dev-profile-alerts`. |

Compose files and generated SDRC runtime files are also bind-mounted from this source
tree. The LVS profile uses `services/infra/sdrc/log`,
`services/infra/sdrc/.wdm-env`, and rendered profile configuration files. A normal
Compose `down`, with or without `-v`, does not delete them.

Neither teardown command deletes `user-overrides.env` or resets host data.

### LVS Compose notes

Docker Compose does not use Kubernetes secrets or the NIM Operator. For the LVS profile, local model bring-up uses the **`NGC_CLI_API_KEY`** environment variable directly for image pulls and NIM/RT-VLM model access.

Default LVS model wiring:

| Component | Local Compose behavior | Default model name |
|-----------|------------------------|--------------------|
| LLM | Starts the **`nemotron-3.5-lightning-30b-a3b`** NIM container on **`LLM_PORT=30081`** when `LLM_MODE` is `local` or `local_shared`. | `nvidia/nemotron-3.5-lightning-30b-a3b` |
| VLM / RT-VLM | Starts **`rtvi-vlm`** on **`RTVI_VLM_PORT=8018`**. The LVS profile sets **`VLM_NAME_SLUG=none`**, so Compose does not start a separate Cosmos VLM NIM by default; RT-VLM loads the integrated checkpoint. | `nim_nvidia_cosmos3-nano-reasoner_bf16-final` |

For external endpoints, configure `user-overrides.env` before bring-up. Set the
remote modes, endpoint URLs, model names and slugs, and RT-VLM proxy settings
together using the [remote model recipe](../../skills/vss-build-vision-ai/references/env-overrides.md#common-override-sets).
`LLM_BASE_URL` and `VLM_BASE_URL` omit the trailing `/v1`;
`RTVI_VLM_ENDPOINT` includes it. Setting `VLM_MODE=remote` alone is insufficient.

Post-deploy checks for the default local LVS ports:

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
curl -f http://127.0.0.1:38111/v1/ready
curl -f http://127.0.0.1:8018/v1/health/ready
curl -f http://127.0.0.1:30081/v1/health/ready
curl -f http://127.0.0.1:38111/models
curl -f http://127.0.0.1:30081/v1/models
```

If a local NIM container keeps restarting and logs include **`No available memory for the cache blocks`**, reduce the NIM max model length and/or sequence count for the active hardware profile. Put model-specific settings in an env file:

```env
# /tmp/lvs-nim-low-memory.env
NIM_MAX_MODEL_LEN=65536
NIM_MAX_NUM_SEQS=2
```

Set `LLM_ENV_FILE=/tmp/lvs-nim-low-memory.env` in the LVS
`user-overrides.env`, then rerun the same Compose bring-up command.

Those numeric values are only an example shape for reducing cache pressure; validate the final values on your GPU and workload.

---

## Warehouse industry profile

The **warehouse** blueprint is driven by
**`industry-profiles/warehouse-operations/`** and is started with direct Docker
Compose from **`deploy/docker`**.

1. **Model and app-data inputs**

Warehouse uses two acquisition paths:

- The `vss-warehouse-app-data` NGC resource remains the source for videos, playback, and calibration data.
- Each RT-CV profile mounts its `models-download.json` on perception and downloads versioned NGC model packages into the flattened `$VSS_DATA_DIR/models/` tree during `ds-start` phase 0.

Download and extract the warehouse app data:

```bash
ngc \
   registry \
   resource \
   download-version \
   nvstaging/vss-warehouse/vss-warehouse-app-data:v3.3.0-09152026

# OR manually download the tar file from NGC:
# https://catalog.ngc.nvidia.com/orgs/nvstaging/teams/vss-warehouse/resources/vss-warehouse-app-data?version=v3.3.0-09152026

cd vss-warehouse-app-data_vv3.3.0-09152026
tar -xvf vss-warehouse-app-data.tar.gz

# Prepare the writable model destination used by ds-start phase-0 download

sudo mkdir -p /path/to/vss-warehouse-app-data/models
sudo chmod 0777 /path/to/vss-warehouse-app-data/models

# This is the path to the data directory. It is set in the industry-profiles/warehouse-operations/.env file for VSS_DATA_DIR.
#VSS_DATA_DIR="/path/to/vss-warehouse-app-data"
```

2. **Edit deployment overrides**

Keep stable profile defaults in
**`industry-profiles/warehouse-operations/.env`**. Update
**`industry-profiles/warehouse-operations/overrides.env`** for the target
machine and selected warehouse scenario:

- **`VSS_APPS_DIR`**: absolute path to this repository's `deploy/docker` directory
- **`VSS_DATA_DIR`**: extracted warehouse app data directory
- **`HOST_IP`** / **`EXTERNAL_IP`**: host address and externally reachable address
- **`NGC_CLI_API_KEY`**: an NGC key with access to the RT-DETR warehouse, Sparse4D, and BodyPose3DNet model packages required by the selected mode; also **`NVIDIA_API_KEY`**, **`OPENAI_API_KEY`** as needed
- **`MODE`**: `2d`, `3d`, or `mv3dt`
- **`BP_PROFILE`**: `bp_wh`, `bp_wh_kafka`, `bp_wh_redis`, or `bp_wh_auto_calib`
- **`HARDWARE_PROFILE`**, model settings, public ingress settings, and host-published ports
- **`COMPOSE_PROFILES`**: one of the warehouse or playback profile lists defined in `overrides.env`

`bp_wh` is valid only with `MODE=2d`. For `MODE=3d` or `MODE=mv3dt`, use
`bp_wh_kafka`, `bp_wh_redis`, or `bp_wh_auto_calib`. Keep `MODE`,
`BP_PROFILE`, `STREAM_TYPE`, sample dataset settings, and `COMPOSE_PROFILES`
aligned with the comments in `overrides.env`.

   Model destinations are shared across profiles: RT-DETR is stored at `models/rtdetr_warehouse_v1.0.2.fp16.onnx`, Sparse4D at `models/sparse4d/sparse4d_warehouse_v2.2.onnx`, and BodyPose3DNet at `models/BodyPose3DNet/bodypose3dnet_accuracy.onnx`.

3. **Start the stack**

```bash
cd /path/to/video-search-and-summarization/deploy/docker

docker compose -f compose.yml \
  --env-file containers.env \
  --env-file industry-profiles/warehouse-operations/.env \
  --env-file industry-profiles/warehouse-operations/overrides.env \
  up --detach --pull always --force-recreate --build
```

4. **Stop the stack**

```bash
docker compose -f compose.yml \
  --env-file containers.env \
  --env-file industry-profiles/warehouse-operations/.env \
  --env-file industry-profiles/warehouse-operations/overrides.env \
  down -v --remove-orphans
```

5. **Data / backup cleanup**

To reset **`data_log`** volumes, calibration/VST data, and
blueprint-configurator backups in a way that matches how you deployed, use
**`deploy/docker/scripts/cleanup_all_datalog.sh`**. Pass the same final env
overlay used for direct Compose:

```bash
bash scripts/cleanup_all_datalog.sh -e industry-profiles/warehouse-operations/overrides.env
```

Compose profiles for warehouse slices are defined in
**`industry-profiles/warehouse-operations/overrides.env`** and selected by
`COMPOSE_PROFILES`.

---

## Requirements

- **Docker** and **Docker Compose** (Compose v2: `docker compose`)
- **bash** (for cleanup scripts)
- **NVIDIA GPU driver** on the host, at a version supported by your hardware and by the GPU containers you run (see NVIDIA release notes for CUDA / NIM images). Check with **`nvidia-smi`** before starting stacks that use GPUs.
- **NVIDIA Container Toolkit** (nvidia-docker) so containers can access the GPU; required alongside the driver for GPU-backed Compose services.
- Valid **NGC** credentials where images or NIMs require **`NGC_CLI_API_KEY`**


---
