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
| **`developer-profiles/dev-profile-*/overrides.env`** / **`industry-profiles/*/overrides.env`** | Mutable deployment-specific defaults such as hardware, model placement, endpoint URLs, host paths, credentials, public ingress, host-published ports, and active `COMPOSE_PROFILES`. |
| **`generated.env`** | Developer-profile runtime overlay created by `dev-profile.sh` from `overrides.env`. It also receives derived values such as `VSS_APPS_DIR`, `VSS_DATA_DIR`, `HOST_IP`, API keys, model slugs, and compose-wide defaults. Do not edit or commit this file. |

`dev-profile.sh` starts developer stacks with env files in this order:

```bash
--env-file containers.env \
--env-file developer-profiles/dev-profile-<profile>/.env \
--env-file developer-profiles/dev-profile-<profile>/generated.env
```

When running Compose directly, pass `containers.env`, the profile `.env`, and
then the profile `overrides.env`:

```bash
--env-file containers.env \
--env-file <profile>/.env \
--env-file <profile>/overrides.env
```

Before direct Compose bring-up, update the deployment-specific placeholders in
`overrides.env`, especially `VSS_APPS_DIR`, `VSS_DATA_DIR`, `HOST_IP`,
`EXTERNAL_IP`, credentials, and the active `COMPOSE_PROFILES`.

---

## Developer profiles (recommended path)

Use the **`dev-profile`** helper instead of hand-editing Compose for day-to-day developer stacks (**base**, **lvs**, **search**, **alerts**).

**Script:** `deploy/docker/scripts/dev-profile.sh`

**Examples:**

```bash
cd /path/to/video-search-and-summarization

# Required for bring-up: NGC CLI API key (pull + NIM)
export NGC_CLI_API_KEY="<your-key>"

# Base profile — minimal developer stack (hardware profile required)
./deploy/docker/scripts/dev-profile.sh up \
  --profile base \
  --hardware-profile H100

# LVS profile — video summarization / LVS-oriented bundle (hardware profile required)
./deploy/docker/scripts/dev-profile.sh up \
  --profile lvs \
  --hardware-profile H100

# Alerts profile — set --mode to verification or real-time
./deploy/docker/scripts/dev-profile.sh up \
  --profile alerts \
  --mode verification \
  --hardware-profile H100

# Search profile
./deploy/docker/scripts/dev-profile.sh up \
  --profile search \
  --hardware-profile H100

# Tear down (no profile flags — cleans the managed Compose project and data dir)
./deploy/docker/scripts/dev-profile.sh down
```

**Full options** (models, remote LLM/VLM, device IDs, edge hardware, etc.):

```bash
./deploy/docker/scripts/dev-profile.sh --help
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
`developer-profiles/dev-profile-lvs/overrides.env` if port 9400 is already in
use. The exporter requires the NVIDIA driver, NVIDIA Container Toolkit, and a
GPU supported by DCGM. Its `/metrics` endpoint can be scraped directly by
Prometheus, Dynatrace, or another Prometheus-compatible monitoring system.

Each developer profile ships a stable **`.env`** and a mutable
**`overrides.env`** under **`developer-profiles/dev-profile-<profile>/`**. On
`up`, the helper reads both, copies `overrides.env` to **`generated.env`**, adds
derived runtime values, and starts Compose with `containers.env`, the profile
`.env`, and `generated.env` in that order.

The helper resets its managed state before every `up`: it stops the Compose
project **`mdx`**, removes Compose volumes, deletes old `generated.env` files,
cleans generated SDRC artifacts, and deletes the developer data directory
(default: **`deploy/docker/data-dir`**) before recreating it. Use `--dry-run` to
preview the commands and generated environment without starting containers.

### RTVI CV startup policy

- Docker uses one canonical RTVI CV startup entrypoint: `services/rtvi/rtvi-cv/ds-start.sh`.
- Developer profiles (**alerts**, **search**) and warehouse **2D/3D** use the shared startup path selected by env/config data.
- Per-profile startup wrapper scripts are not used.
- **MV3DT is the documented exception** and keeps its dedicated `ds-start-mv3dt.sh` command override.
- Model acquisition for **developer profiles** (alerts, search) and **warehouse RT-CV profiles** (2D, 3D, MV3DT) runs as phase 0 of the perception startup script (`ds-start.sh` / MV3DT `ds-start-mv3dt.sh`) when a per-profile `models-download.json` is mounted. There is no separate download init service. Warehouse still uses the pre-extracted `VSS_DATA_DIR` bundle for videos, playback, and calibration (see the warehouse section below).

### Direct Compose usage and data directories

`dev-profile.sh` creates and permissions developer-profile data directories automatically. If you run
`docker compose` directly, you are responsible for both the env-file order and the
host directories.

For a developer profile, use a helper-created `generated.env` when possible:

```bash
cd /path/to/video-search-and-summarization/deploy/docker

docker compose -f compose.yml \
  --env-file containers.env \
  --env-file developer-profiles/dev-profile-base/.env \
  --env-file developer-profiles/dev-profile-base/generated.env \
  config
```

If you choose to pass `overrides.env` directly instead of `generated.env`, first
replace its placeholder values for `VSS_APPS_DIR`, `VSS_DATA_DIR`, `HOST_IP`,
credentials, ports, and model settings.

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
| `TURN_MIN_RELAY_HOST_PORT` / `TURN_MAX_RELAY_HOST_PORT` | `49160` / `49200` | Host relay port range |
| `TURN_MIN_RELAY_PORT` / `TURN_MAX_RELAY_PORT` | `49160` / `49200` | Container relay port range |

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

### LVS Compose notes

Docker Compose does not use Kubernetes secrets or the NIM Operator. For the LVS profile, local model bring-up uses the **`NGC_CLI_API_KEY`** environment variable directly for image pulls and NIM/RT-VLM model access.

Default LVS model wiring:

| Component | Local Compose behavior | Default model name |
|-----------|------------------------|--------------------|
| LLM | Starts the **`nemotron-3.5-lightning-30b-a3b`** NIM container on **`LLM_PORT=30081`** when `LLM_MODE` is `local` or `local_shared`. | `nvidia/nemotron-3.5-lightning-30b-a3b` |
| VLM / RT-VLM | Starts **`rtvi-vlm`** on **`RTVI_VLM_PORT=8018`**. The LVS profile sets **`VLM_NAME_SLUG=none`**, so Compose does not start a separate Cosmos VLM NIM by default; RT-VLM loads the integrated checkpoint. | `nim_nvidia_cosmos3-nano-reasoner_bf16-final` |

For external endpoints, use the helper flags instead of editing Compose files directly:

```bash
export LLM_ENDPOINT_URL='<REMOTE LLM SERVICE ROOT, no trailing /v1>'
export VLM_ENDPOINT_URL='<REMOTE VLM SERVICE ROOT, no trailing /v1>'

./deploy/docker/scripts/dev-profile.sh up \
  --profile lvs \
  --hardware-profile H100 \
  --use-remote-llm \
  --use-remote-vlm \
  --llm nvidia/nemotron-3.5-lightning-30b-a3b \
  --vlm nim_nvidia_cosmos3-nano-reasoner_bf16-final
```

The helper probes **`${LLM_ENDPOINT_URL}/v1/models`** and **`${VLM_ENDPOINT_URL}/v1/models`**, and the agent config appends **`/v1`** to **`LLM_BASE_URL`** / **`VLM_BASE_URL`**. Do not include **`/v1`** in the endpoint environment variables.

Post-deploy checks for the default local LVS ports:

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
curl -f http://127.0.0.1:38111/v1/ready
curl -f http://127.0.0.1:8018/v1/health/ready
curl -f http://127.0.0.1:30081/v1/health/ready
curl -f http://127.0.0.1:38111/models
curl -f http://127.0.0.1:30081/v1/models
```

If a local NIM container keeps restarting and logs include **`No available memory for the cache blocks`**, reduce the NIM max model length and/or sequence count for the active hardware profile. One non-destructive way is to pass an override env file through **`--llm-env-file`**:

```env
# /tmp/lvs-nim-low-memory.env
NIM_MAX_MODEL_LEN=65536
NIM_MAX_NUM_SEQS=2
```

```bash
./deploy/docker/scripts/dev-profile.sh up \
  --profile lvs \
  --hardware-profile RTXPRO6000BW \
  --llm-env-file /tmp/lvs-nim-low-memory.env
```

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
   nvidia/vss-warehouse/vss-warehouse-app-data:3.2.0

# OR manually download the tar file from NGC:
# https://catalog.ngc.nvidia.com/orgs/nvidia/teams/vss-warehouse/resources/vss-warehouse-app-data?version=3.2.0

cd vss-warehouse-app-data_v3.2.0
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
- **bash** (for **`dev-profile.sh`** and cleanup scripts)
- **NVIDIA GPU driver** on the host, at a version supported by your hardware and by the GPU containers you run (see NVIDIA release notes for CUDA / NIM images). Check with **`nvidia-smi`** before starting stacks that use GPUs.
- **NVIDIA Container Toolkit** (nvidia-docker) so containers can access the GPU; required alongside the driver for GPU-backed Compose services.
- Valid **NGC** credentials where images or NIMs require **`NGC_CLI_API_KEY`**


---
