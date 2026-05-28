# Docker deployment (`deploy/docker`)

This tree is the Docker Compose packaging for **Video Search & Summarization**. The root **`compose.yml`** pulls three layers together:

| Include | Role |
|---------|------|
| **`services/compose.yml`** | Shared microservices (infra, VIOS, UI, RTVI, NIMs, etc.) |
| **`developer-profiles/compose.yml`** | Developer profiles: **base**, **lvs**, **alerts**, **search** |
| **`industry-profiles/compose.yml`** | Industry blueprints (e.g. **warehouse-operations**) |

Run Compose from **`deploy/docker`** so relative paths resolve correctly.

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

# Tear down (no profile flags — brings down the Compose project `mdx`)
./deploy/docker/scripts/dev-profile.sh down
```

**Full options** (models, remote LLM/VLM, device IDs, edge hardware, etc.):

```bash
./deploy/docker/scripts/dev-profile.sh --help
```

Each profile may also ship a **`.env`** under **`developer-profiles/<profile>/`** for defaults; the script generates or merges runtime env (e.g. **`generated.env`**) as documented in the script help.

### Direct Compose data directories

The helper scripts create and permission the data directories automatically. If you run
`docker compose -f compose.yml ...` directly, set **`VSS_DATA_DIR`** and create writable
host directories for the bind-mounted infrastructure volumes before starting the stack:

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
### LVS Compose notes

Docker Compose does not use Kubernetes secrets or the NIM Operator. For the LVS profile, local model bring-up uses the **`NGC_CLI_API_KEY`** environment variable directly for image pulls and NIM/RT-VLM model access.

Default LVS model wiring:

| Component | Local Compose behavior | Default model name |
|-----------|------------------------|--------------------|
| LLM | Starts the **`nvidia-nemotron-nano-9b-v2`** NIM container on **`LLM_PORT=30081`** when `LLM_MODE` is `local` or `local_shared`. | `nvidia/nvidia-nemotron-nano-9b-v2` |
| VLM / RT-VLM | Starts **`rtvi-vlm`** on **`RTVI_VLM_PORT=8018`**. The LVS profile sets **`VLM_NAME_SLUG=none`**, so Compose does not start a separate Cosmos VLM NIM by default; RT-VLM loads the integrated checkpoint. | `nim_nvidia_cosmos-reason2-8b_hf-1208` |

For external endpoints, use the helper flags instead of editing Compose files directly:

```bash
export LLM_ENDPOINT_URL='<REMOTE LLM SERVICE ROOT, no trailing /v1>'
export VLM_ENDPOINT_URL='<REMOTE VLM SERVICE ROOT, no trailing /v1>'

./deploy/docker/scripts/dev-profile.sh up \
  --profile lvs \
  --hardware-profile H100 \
  --use-remote-llm \
  --use-remote-vlm \
  --llm nvidia/nvidia-nemotron-nano-9b-v2 \
  --vlm nim_nvidia_cosmos-reason2-8b_hf-1208
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

The **warehouse** blueprint is driven by **`industry-profiles/warehouse-operations/`**

1. **Edit environment**  
   Update **`deploy/docker/industry-profiles/warehouse-operations/.env`** for your deployment:

   - **`MODE`**: `2d`, `3d`, or `mv3dt`
   - **`BP_PROFILE`**: `bp_wh`, `bp_wh_kafka`, `bp_wh_redis`, `bp_wh_auto_calib` (see comments in that file for 2d, 3d, and mv3dt combinations)
   - **`MINIMAL_PROFILE`**, GPU hosts, API keys, and any other variables described in the file header

2. **Start the stack**

```bash
cd /path/to/video-search-and-summarization/deploy/docker
docker compose -f compose.yml --env-file industry-profiles/warehouse-operations/.env up --detach \
--pull always \
--force-recreate \
--build
```

3. **Stop the stack**

```bash
# Stop the running deployment
docker compose -f compose.yml --env-file industry-profiles/warehouse-operations/.env down

# Alternatively to remove all the containers, images and volume
docker compose --env-file industry-profiles/warehouse-operations/.env down -v --rmi all

# Tear down all dangling volumes
docker volume ls -q -f "dangling=true" | xargs docker volume rm
```

4. **Data / backup cleanup**  
   To reset **`data_log`** volumes, calibration/VST data, and blueprint-configurator backups in a way that matches how you deployed, use **`deploy/docker/scripts/cleanup_all_datalog.sh`**.  
   Pass **`-e`** / **`--env-file`** with the **same env file** you used for **`docker compose --env-file …`**.

```bash
bash scripts/cleanup_all_datalog.sh -e industry-profiles/warehouse-operations/.env
```

Compose profiles for warehouse slices are defined under **`warehouse-operations/compose.yml`** and related **`warehouse-2d-app`** / **`warehouse-3d-app`** includes; the **`.env`** file selects **MODE** / **BP_PROFILE** behavior as documented there.

---

## Requirements

- **Docker** and **Docker Compose** (Compose v2: `docker compose`)
- **bash** (for **`dev-profile.sh`**)
- **NVIDIA GPU driver** on the host, at a version supported by your hardware and by the GPU containers you run (see NVIDIA release notes for CUDA / NIM images). Check with **`nvidia-smi`** before starting stacks that use GPUs.
- **NVIDIA Container Toolkit** (nvidia-docker) so containers can access the GPU; required alongside the driver for GPU-backed Compose services.
- Valid **NGC** credentials where images or NIMs require **`NGC_CLI_API_KEY`**


---
