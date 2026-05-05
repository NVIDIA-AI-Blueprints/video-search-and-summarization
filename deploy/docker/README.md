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

---

## Warehouse industry profile

The **warehouse** blueprint is driven by **`industry-profiles/warehouse-operations/`**

1. **Edit environment**  
   Update **`deploy/docker/industry-profiles/warehouse-operations/.env`** for your deployment:

   - **`MODE`**: `2d` or `3d`
   - **`BP_PROFILE`**: `bp_wh`, `bp_wh_kafka`, or `bp_wh_redis` (see comments in that file for 2d vs 3d combinations)
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
docker compose -f compose.yml --env-file industry-profiles/warehouse-operations/.env down
```

4. **Data / backup cleanup**  
   To reset **`data_log`** volumes, calibration/VST data, and blueprint-configurator backups in a way that matches how you deployed, use **`deploy/docker/scripts/cleanup_all_datalog.sh`**.  
   Pass **`-e`** / **`--env-file`** with the **same env file** you used for **`docker compose --env-file …`**.

```bash
cd /path/to/video-search-and-summarization/deploy/docker
./scripts/cleanup_all_datalog.sh --env-file industry-profiles/warehouse-operations/.env
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

