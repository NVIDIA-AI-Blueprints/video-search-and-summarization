# Docker deployment (`deploy/docker`)

This tree is the Docker Compose packaging for **Video Search & Summarization**. The root **`compose.yml`** pulls three layers together:

| Include | Role |
|---------|------|
| **`services/compose.yml`** | Shared microservices (infra, VIOS, UI, RTVI, NIMs, etc.) |
| **`developer-profiles/compose.yml`** | Developer profiles: **base**, **lvs**, **alerts**, **search** |
| **`industry-profiles/compose.yml`** | Industry blueprints (e.g. **warehouse-operations**) |

Run Compose from **`deploy/docker`** so relative paths resolve correctly.

---

## Environment file model

The Docker deployment uses layered env files. Profile directories split values by ownership:

| File | Purpose | Edit when |
|------|---------|-----------|
| **`developer-profiles/dev-profile-*/.env`** / **`industry-profiles/*/.env`** | Stable profile constants passed directly to Compose. | You are changing a profile default that should be the same across machines. |
| **`overrides.env`** next to the profile `.env` | Runtime defaults, host paths, host-published ports, hardware/model selections, ingress values, credentials placeholders, and profile-specific toggles. | You are preparing a deployment, changing hardware/model behavior, resolving a port conflict, or setting local paths. |
| **`generated.env`** next to the profile `.env` | Local overlay consumed by the Compose commands below. Start by copying `overrides.env`, then edit the copied file for the current machine/run. | You are about to start a profile. This file is ignored by git. |
| **`services/**/*.env`** | Shared include-level service defaults, such as image tags, UI defaults, VST defaults, RTVI defaults, LVS defaults, and hardware-specific NIM settings. | You are changing a shared service default used by more than one profile. |

Pass profile env files to Compose in this order:

```bash
--env-file <profile>/.env --env-file <profile>/generated.env
```

The second file wins when the same key appears in both layers. Compose include files also load the relevant **`services/**/*.env`** defaults at include time so shared service values do not have to be duplicated in every profile.

Before starting any profile, review the profile's **`overrides.env`** and copy it to **`generated.env`**. Replace placeholder values such as **`/path/to/...`**, **`<HOST_IP>`**, empty credential values, hardware profile selections, and host-published ports in **`generated.env`**. You may also export the same values in the shell, but keeping them in **`generated.env`** makes `docker compose config` and repeat runs easier to review.

---

## Developer profiles

Developer profiles are selected by profile env files under **`developer-profiles/dev-profile-<profile>/`**. Supported profile names are **`base`**, **`lvs`**, **`search`**, and **`alerts`**.

### Prepare the overlay

```bash
cd /path/to/video-search-and-summarization/deploy/docker

PROFILE=base  # base, lvs, search, or alerts
PROFILE_DIR="developer-profiles/dev-profile-${PROFILE}"

cp "${PROFILE_DIR}/overrides.env" "${PROFILE_DIR}/generated.env"
```

Edit **`${PROFILE_DIR}/generated.env`** before starting. At minimum, review these values:

| Setting area | Common keys |
|--------------|-------------|
| Host paths and ingress | `VSS_APPS_DIR`, `VSS_DATA_DIR`, `HOST_IP`, `EXTERNAL_IP`, `VSS_PUBLIC_HOST`, `VSS_PUBLIC_PORT` |
| Credentials | `NGC_CLI_API_KEY`, and optional `NVIDIA_API_KEY` / `OPENAI_API_KEY` for remote endpoints |
| Hardware and model placement | `HARDWARE_PROFILE`, `LLM_MODE`, `VLM_MODE`, `LLM_DEVICE_ID`, `VLM_DEVICE_ID`, `LLM_NAME`, `VLM_NAME`, `NIM_MODEL_SIZE` where present |
| Local port conflicts | `*_HOST_PORT`, `HAPROXY_HOST_PORT`, `VST_INGRESS_HOST_PORT`, `RTVI_VLM_PORT` |
| Profile toggles | `MODE` and `NEXT_PUBLIC_APP_SUBTITLE` for alerts, `ENABLE_CRITIC` for search, `LLM_ENV_FILE` / `VLM_ENV_FILE` for extra model env overrides |

For **DGX-SPARK** or SBSA image runs, review the commented `*-sbsa` image tag lines in **`generated.env`** and switch the relevant tags before starting Compose.

### Prepare host directories

Create the bind-mounted data directories referenced by **`VSS_DATA_DIR`** in **`generated.env`**. If you keep the path in the shell instead, export the same value before running these commands.

```bash
# Match VSS_DATA_DIR in ${PROFILE_DIR}/generated.env.
export VSS_DATA_DIR=/path/to/vss-apps-data

mkdir -p \
  "$VSS_DATA_DIR/data_log/analytics_cache" \
  "$VSS_DATA_DIR/data_log/calibration_toolkit" \
  "$VSS_DATA_DIR/data_log/elastic/data" \
  "$VSS_DATA_DIR/data_log/elastic/logs" \
  "$VSS_DATA_DIR/data_log/kafka" \
  "$VSS_DATA_DIR/data_log/redis/data" \
  "$VSS_DATA_DIR/data_log/redis/log" \
  "$VSS_DATA_DIR/agent_eval/dataset" \
  "$VSS_DATA_DIR/agent_eval/results"

chmod -R 777 "$VSS_DATA_DIR/data_log" "$VSS_DATA_DIR/agent_eval"
```

### Profile-specific preparation

| Profile | Preparation before `docker compose up` |
|---------|----------------------------------------|
| **base** | No sample data is downloaded by the profile setup. Local NIM containers pull/load models at container startup using `NGC_CLI_API_KEY`. Review `NIM_MODEL_SIZE` when `VLM_NAME=nvidia/cosmos3-reasoner`. |
| **lvs** | No sample data is downloaded by the profile setup. Local NIM/RT-VLM containers pull/load models at container startup using `NGC_CLI_API_KEY`. Review `generated.env` for host paths, credentials, hardware placement, local/remote model mode, and port overrides. |
| **search** | Create `"$VSS_DATA_DIR/data_log/vss_video_analytics_api"` and pre-stage the warehouse RT-DETR ONNX model in `"$VSS_DATA_DIR/models"` with the command below. Review `ENABLE_CRITIC`; setting it to `false` skips the critic VLM. |
| **alerts** | Create `"$VSS_DATA_DIR/data_log/vss_video_analytics_api"`, `"$VSS_DATA_DIR/videos/dev-profile-alerts"`, and engine directories under `deploy/docker/engines`. Pre-stage the TAO RT-DETR and GDINO ONNX models with the command below. `MODE=2d_cv` corresponds to verification/CV and `MODE=2d_vlm` corresponds to real-time/VLM; keep `NEXT_PUBLIC_APP_SUBTITLE` aligned with `MODE`. |

Search profile model pre-stage:

```bash
export NGC_CLI_API_KEY="<your-ngc-key>"
export VSS_DATA_DIR=/path/to/vss-apps-data

mkdir -p "$VSS_DATA_DIR/data_log/vss_video_analytics_api" "$VSS_DATA_DIR/models"

NGC_CLI_API_KEY="$NGC_CLI_API_KEY" ngc registry model download-version \
  nvidia/tao/rtdetr_2d_warehouse:deployable_rn50_v1.0.2 \
  --org nvidia

mv rtdetr_2d_warehouse_vdeployable_rn50_v1.0.2/rtdetr_warehouse_v1.0.2.fp16.onnx \
  "$VSS_DATA_DIR/models/rtdetr_warehouse_v1.0.2.fp16.onnx"
rm -rf rtdetr_2d_warehouse_vdeployable_rn50_v1.0.2
chmod -R 777 "$VSS_DATA_DIR/models"
```

Alerts profile model pre-stage:

```bash
export NGC_CLI_API_KEY="<your-ngc-key>"
export VSS_DATA_DIR=/path/to/vss-apps-data

mkdir -p \
  "$VSS_DATA_DIR/data_log/vss_video_analytics_api" \
  "$VSS_DATA_DIR/videos/dev-profile-alerts" \
  "$VSS_DATA_DIR/models/rtdetr-its" \
  "$VSS_DATA_DIR/models/gdino" \
  "$(pwd)/engines/gdino" \
  "$(pwd)/engines/rtdetr-its"

NGC_CLI_API_KEY="$NGC_CLI_API_KEY" ngc registry model download-version \
  nvidia/tao/trafficcamnet_transformer_lite:deployable_resnet50_v2.0
mv trafficcamnet_transformer_lite_vdeployable_resnet50_v2.0/resnet50_trafficcamnet_rtdetr.fp16.onnx \
  "$VSS_DATA_DIR/models/rtdetr-its/model_epoch_035.fp16.onnx"
rm -rf trafficcamnet_transformer_lite_vdeployable_resnet50_v2.0

NGC_CLI_API_KEY="$NGC_CLI_API_KEY" ngc registry model download-version \
  nvidia/tao/mask_grounding_dino:mask_grounding_dino_swin_tiny_commercial_deployable_v2.1_wo_mask_arm
mv mask_grounding_dino_vmask_grounding_dino_swin_tiny_commercial_deployable_v2.1_wo_mask_arm/mgdino_mask_head_pruned_dynamic_batch.onnx \
  "$VSS_DATA_DIR/models/gdino/mgdino_mask_head_pruned_dynamic_batch.onnx"
rm -rf mask_grounding_dino_vmask_grounding_dino_swin_tiny_commercial_deployable_v2.1_wo_mask_arm

chmod -R 777 "$VSS_DATA_DIR/models" "$(pwd)/engines"
```

### Start and stop

Log in to NGC when the selected images require access to **`nvcr.io`**:

```bash
docker login --username '$oauthtoken' --password "$NGC_CLI_API_KEY" nvcr.io
```

Start the selected developer profile:

```bash
docker compose \
  --env-file "${PROFILE_DIR}/.env" \
  --env-file "${PROFILE_DIR}/generated.env" \
  up --detach --force-recreate --build
```

Review the effective configuration before starting, or when changing **`generated.env`**:

```bash
docker compose \
  --env-file "${PROFILE_DIR}/.env" \
  --env-file "${PROFILE_DIR}/generated.env" \
  config
```

Stop the stack:

```bash
docker compose \
  --env-file "${PROFILE_DIR}/.env" \
  --env-file "${PROFILE_DIR}/generated.env" \
  down -v --remove-orphans
```

### TURN / WebRTC relay

The warehouse VST UI uses WebRTC for live playback. When VST containers run on the Compose bridge network, browsers cannot reach Docker-only media candidates directly, so `services/infra/compose.yml` includes a coturn-based `turnserver` service for warehouse profiles. It exposes the TURN listener and relay range on the host. Developer profiles do not start this TURN service.

Default ports:

| Variable | Default | Purpose |
|----------|---------|---------|
| `TURN_HOST_PORT` / `TURN_PORT` | `3478` | TURN UDP/TCP listener |
| `TURN_MIN_RELAY_HOST_PORT` / `TURN_MAX_RELAY_HOST_PORT` | `49160` / `49200` | Host relay port range |
| `TURN_MIN_RELAY_PORT` / `TURN_MAX_RELAY_PORT` | `49160` / `49200` | Container relay port range |

Set `TURN_PUBLIC_HOST` to the DNS name or IP address that browser clients use to reach the deployment, and set `TURN_EXTERNAL_IP` to the host IP coturn should advertise. The warehouse profile uses a non-secret default `TURN_USERNAME` and starts a `turnserver-init` job that generates a random password once in the `vss-turn-password` Docker volume. Coturn and VST mount that same generated file; the VST startup logic derives the static TURN URL in the format `user:password@host:port` from `TURN_USERNAME`, the generated password file, `TURN_PUBLIC_HOST`, and `TURN_HOST_PORT`.

For the bundled turnserver, leave `VST_STATIC_TURNURL_LIST` empty:

```env
TURN_HOST_PORT=3478
TURN_PORT=3478
TURN_USERNAME=vss
TURN_PASSWORD_BYTES=32
VST_STATIC_TURNURL_LIST=
```

Remove the Compose-created `vss-turn-password` Docker volume and restart the warehouse profile to rotate the generated password. Only set `VST_STATIC_TURNURL_LIST` for external or multiple TURN endpoints; treat it as sensitive because it embeds TURN credentials.

The warehouse VST streamprocessing startup logic also forces `network.use_coturn_auth_secret=false` and `network.coturn_turnurl_list_with_secret=[]`, matching the static username/password mode. Developer VST streamprocessing and NvStreamer services do not apply this WebRTC/TURN patch.

### LVS Compose notes

Docker Compose does not use Kubernetes secrets or the NIM Operator. For the LVS profile, local model bring-up uses the **`NGC_CLI_API_KEY`** environment variable directly for image pulls and NIM/RT-VLM model access.

Default LVS model wiring:

| Component | Local Compose behavior | Default model name |
|-----------|------------------------|--------------------|
| LLM | Starts the **`nvidia-nemotron-nano-9b-v2`** NIM container on **`LLM_PORT=30081`** when `LLM_MODE` is `local` or `local_shared`. | `nvidia/nvidia-nemotron-nano-9b-v2` |
| VLM / RT-VLM | Starts **`rtvi-vlm`** on **`RTVI_VLM_PORT=8018`**. The LVS profile sets **`VLM_NAME_SLUG=none`**, so Compose does not start a separate Cosmos VLM NIM by default; RT-VLM loads the integrated checkpoint. | `nim_nvidia_cosmos3-nano-reasoner_bf16-final` |

For external endpoints, set the endpoint and model keys in **`developer-profiles/dev-profile-lvs/generated.env`** before starting Compose:

```env
LLM_MODE=remote
LLM_BASE_URL=<REMOTE LLM SERVICE ROOT, no trailing /v1>
LLM_NAME=<remote-llm-model-name>
LLM_NAME_SLUG=none
LLM_MODEL_TYPE=nim

VLM_MODE=remote
VLM_BASE_URL=<REMOTE VLM SERVICE ROOT, no trailing /v1>
VLM_NAME=<remote-vlm-model-name>
VLM_NAME_SLUG=none
VLM_MODEL_TYPE=nim
VLM_PORT=30082
RTVI_VLM_ENDPOINT=${VLM_BASE_URL}/v1
RTVI_VLM_MODEL_PATH=none
RTVI_VLM_MODEL_TO_USE=openai-compat
```

The agent config appends **`/v1`** to **`LLM_BASE_URL`** / **`VLM_BASE_URL`**. Do not include **`/v1`** in the endpoint base URL values.

Post-deploy checks for the default local LVS ports:

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
curl -f http://127.0.0.1:38111/v1/ready
curl -f http://127.0.0.1:8018/v1/health/ready
curl -f http://127.0.0.1:30081/v1/health/ready
curl -f http://127.0.0.1:38111/models
curl -f http://127.0.0.1:30081/v1/models
```

If a local NIM container keeps restarting and logs include **`No available memory for the cache blocks`**, reduce the NIM max model length and/or sequence count for the active hardware profile. One non-destructive option is to set `LLM_ENV_FILE` in **`generated.env`** to an env file such as:

```env
# /tmp/lvs-nim-low-memory.env
NIM_MAX_MODEL_LEN=65536
NIM_MAX_NUM_SEQS=2
```

Those numeric values are only an example shape for reducing cache pressure; validate the final values on your GPU and workload.

---

## Warehouse industry profile

The **warehouse** blueprint is driven by **`industry-profiles/warehouse-operations/`**.

### Download warehouse app data

```bash
ngc registry resource download-version \
  nvidia/vss-warehouse/vss-warehouse-app-data:3.2.0

# Or manually download the tar file from NGC:
# https://catalog.ngc.nvidia.com/orgs/nvidia/teams/vss-warehouse/resources/vss-warehouse-app-data?version=3.2.0

cd vss-warehouse-app-data_v3.2.0
tar -xvf vss-warehouse-app-data.tar.gz
sudo chmod -R 777 /path/to/vss-warehouse-app-data
```

Set **`VSS_DATA_DIR`** in **`industry-profiles/warehouse-operations/generated.env`** to the extracted data directory. The data directory is expected to contain the warehouse package contents such as **`models`**, **`videos`**, **`data_log`**, and **`playback`**.

### Prepare the overlay

```bash
cd /path/to/video-search-and-summarization/deploy/docker

WAREHOUSE_DIR="industry-profiles/warehouse-operations"
cp "${WAREHOUSE_DIR}/overrides.env" "${WAREHOUSE_DIR}/generated.env"
```

Edit **`${WAREHOUSE_DIR}/generated.env`** before starting. At minimum, review:

| Setting area | Common keys |
|--------------|-------------|
| Profile selection | `MODE`, `BP_PROFILE`, `ELASTICSEARCH_MODE`, `SAMPLE_VIDEO_DATASET`, `NUM_STREAMS`, `HARDWARE_PROFILE`, `STREAM_TYPE` |
| Host paths and ingress | `VSS_APPS_DIR`, `VSS_DATA_DIR`, `HOST_IP`, `EXTERNAL_IP`, `VSS_PUBLIC_HOST`, `TURN_PUBLIC_HOST`, `TURN_EXTERNAL_IP` |
| Credentials | `NGC_CLI_API_KEY`, and optional `NVIDIA_API_KEY` / `OPENAI_API_KEY` for remote endpoints |
| 2D agent/model settings | `LLM_MODE`, `VLM_MODE`, `LLM_NAME`, `VLM_NAME`, `LLM_DEVICE_ID`, `VLM_DEVICE_ID`, `LLM_ENV_FILE`, `VLM_ENV_FILE` |
| Local port conflicts | `*_HOST_PORT`, `TURN_*_HOST_PORT`, `HAPROXY_HOST_PORT`, `VST_INGRESS_HOST_PORT`, `RTVI_VLM_PORT` |

The default dataset selection mirrors the deployment mode/profile:

| `MODE` / `BP_PROFILE` | Default `SAMPLE_VIDEO_DATASET` | Default `NUM_STREAMS` |
|-----------------------|--------------------------------|-----------------------|
| `MODE=2d`, `BP_PROFILE=bp_wh` | `nv-warehouse-4cams` | `4` |
| `MODE=2d`, other warehouse profiles | `warehouse-loading-dock-3cams-synthetic` | `3` |
| `MODE=3d` or `MODE=mv3dt` | `warehouse-4cams-20mx20m-synthetic` | `4` |

For **DGX-SPARK** or SBSA image runs, review the commented `*-sbsa` image tag lines in **`generated.env`** and switch the relevant tags before starting Compose.

### Prepare host directories

```bash
export VSS_DATA_DIR=/path/to/vss-warehouse-app-data
export MODE=2d
export BP_PROFILE=bp_wh
export SAMPLE_VIDEO_DATASET=nv-warehouse-4cams

mkdir -p \
  "$VSS_DATA_DIR/data_log/analytics_cache" \
  "$VSS_DATA_DIR/data_log/calibration_toolkit" \
  "$VSS_DATA_DIR/data_log/elastic/data" \
  "$VSS_DATA_DIR/data_log/elastic/logs" \
  "$VSS_DATA_DIR/data_log/kafka" \
  "$VSS_DATA_DIR/data_log/redis/data" \
  "$VSS_DATA_DIR/data_log/redis/log" \
  "$VSS_DATA_DIR/data_log/nvstreamer/vst_data" \
  "$VSS_DATA_DIR/data_log/vss_video_analytics_api" \
  "$VSS_DATA_DIR/videos/$SAMPLE_VIDEO_DATASET" \
  "$VSS_DATA_DIR/playback"

# Required only for MODE=mv3dt.
mkdir -p "$VSS_DATA_DIR/models/mv3dt/BodyPose3DNet"

chmod -R 777 "$VSS_DATA_DIR/data_log"
```

### Start and stop

Log in to NGC when the selected images require access to **`nvcr.io`**:

```bash
docker login --username '$oauthtoken' --password "$NGC_CLI_API_KEY" nvcr.io
```

Start warehouse:

```bash
docker compose \
  -f compose.yml \
  --env-file "${WAREHOUSE_DIR}/.env" \
  --env-file "${WAREHOUSE_DIR}/generated.env" \
  up --detach --force-recreate --build
```

Review the effective configuration before starting, or when changing **`generated.env`**:

```bash
docker compose \
  -f compose.yml \
  --env-file "${WAREHOUSE_DIR}/.env" \
  --env-file "${WAREHOUSE_DIR}/generated.env" \
  config
```

Stop warehouse:

```bash
docker compose \
  -f compose.yml \
  --env-file "${WAREHOUSE_DIR}/.env" \
  --env-file "${WAREHOUSE_DIR}/generated.env" \
  down
```

Remove containers, images, and volumes:

```bash
docker compose \
  -f compose.yml \
  --env-file "${WAREHOUSE_DIR}/.env" \
  --env-file "${WAREHOUSE_DIR}/generated.env" \
  down -v --rmi all
```

To reset **`data_log`** volumes, calibration/VST data, and blueprint-configurator backups, use **`deploy/docker/scripts/cleanup_all_datalog.sh`** with the overlay env file that contains the effective **`VSS_DATA_DIR`**:

```bash
bash scripts/cleanup_all_datalog.sh \
  -e industry-profiles/warehouse-operations/generated.env
```

Compose profiles for warehouse slices are defined under **`warehouse-operations/compose.yml`** and related **`warehouse-2d-app`** / **`warehouse-3d-app`** includes. The **`.env`** and **`generated.env`** files together select **MODE** / **BP_PROFILE** behavior as documented there.

---
## Requirements

- **Docker** and **Docker Compose** (Compose v2: `docker compose`)
- **bash** or another POSIX-compatible shell for the command examples
- **NVIDIA GPU driver** on the host, at a version supported by your hardware and by the GPU containers you run (see NVIDIA release notes for CUDA / NIM images). Check with **`nvidia-smi`** before starting stacks that use GPUs.
- **NVIDIA Container Toolkit** (nvidia-docker) so containers can access the GPU; required alongside the driver for GPU-backed Compose services.
- Valid **NGC** credentials where images or NIMs require **`NGC_CLI_API_KEY`**


---
