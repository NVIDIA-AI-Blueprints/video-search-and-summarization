# Warehouse Blueprint Reference

Blueprint: VSS Warehouse — RT-DETR perception + behavior analytics over multi-camera warehouse streams. Distinct from the core VSS profiles (`base`, `alerts`, `lvs`, `search`): it has its own compose bundle, app-data bundle, and `.env` layout, deployed from `<deployments_dir>/` with `warehouse/.env`.

Work through **one path** under [Choose your path](#choose-your-path). Reference tables (variants, services, GPU layout, endpoints, artifacts) are in the top half; operational phases are in the bottom half.

---

## Profile Variants

| Profile Name | MODE | BP_PROFILE | SAMPLE_VIDEO_DATASET | NUM_STREAMS | LLM/VLM |
|---|---|---|---|---|---|
| 2D Vision AI Profile | `2d` | `bp_wh_kafka` or `bp_wh_redis` | `warehouse-loading-dock-3cams-synthetic` | 3 | none |
| 2D Vision AI with Agents Profile | `2d` | `bp_wh` | `nv-warehouse-4cams` | 4 | local / remote |
| 3D Vision AI Profile | `3d` | `bp_wh_kafka` or `bp_wh_redis` | `warehouse-4cams-20mx20m-synthetic` | 4 | none |

`COMPOSE_PROFILES` is computed automatically: `${BP_PROFILE}_${MODE},llm_${LLM_MODE}_${LLM_NAME_SLUG},vlm_${VLM_MODE}_${VLM_NAME_SLUG}`

## Minimal vs Extended Profile

Applies to `bp_wh_kafka` and `bp_wh_redis` only.

| Feature | Minimal (`MINIMAL_PROFILE="true"`) | Extended (`MINIMAL_PROFILE=""`) |
|---|---|---|
| RT-DETR Perception | ✅ | ✅ |
| Behavior Analytics | ✅ | ✅ |
| VST / NvStreamer | ✅ | ✅ |
| Auto-Calibration | ✅ | ✅ |
| ELK (Elasticsearch/Logstash/Kibana) | ❌ | ✅ |
| Video Analytics API | ❌ | ✅ |
| Video Analytics UI | ❌ | ✅ |
| Monitoring | ❌ | ✅ |
| Bounding box overlays in VST | ❌ | ✅ (requires Elasticsearch) |

## Services Deployed (2D — `bp_wh_kafka` / `bp_wh_redis`)

| Service | Purpose |
|---|---|
| NvStreamer | Streams sample video files via RTSP |
| VIOS (VST) | Video ingestion, recording, stream management |
| perception-2d | RT-DETR DeepStream container — 2D object detection and tracking |
| perception-sdr-2d | Stream data router — manages DeepStream lifecycle |
| bp-configurator-2d | Blueprint configurator — sets up stream and hardware configs |
| ds-configurator-2d | DeepStream config adaptor |
| vss-behavior-analytics-2d | Behavior analytics — ROI, tripwire, proximity events |
| Kafka or Redis | Message broker for CV metadata and control bus |
| broker-health-check | Waits for broker readiness before starting dependent services |

## Perception Model

- **Model:** RT-DETR with EfficientViT/L2 backbone
- **Detects:** People, humanoid robots, forklifts, autonomous vehicles, warehouse equipment
- **Output:** 2D bounding boxes with tracked object IDs via Kafka/Redis `mdx-raw` topic

## GPU Layout

| Role | Device |
|---|---|
| RT-CV perception (RT-DETR DeepStream) | `RT_CV_DEVICE_ID` (default: `0`) |
| LLM NIM | `LLM_DEVICE_ID` (default: `1`) — only for `bp_wh` |
| VLM NIM | `VLM_DEVICE_ID` (default: `2`) — only for `bp_wh` |

## Access Points

| Service | URL | Profile |
|---|---|---|
| VST / VIOS UI | `http://<HOST_IP>:30888/vst` | All |
| NvStreamer UI | `http://<HOST_IP>:31000` | All |
| Auto-Calibration UI | `http://<HOST_IP>:5000` | All |
| Kibana | `http://<HOST_IP>:5601` | Extended only |
| Video Analytics UI | `http://<HOST_IP>:3002` | Extended only |

## Compose File Structure

Deployed from `<deployments_dir>/` (the extracted `deployments/` root) using:
- `warehouse/.env` — all configuration
- `compose.yml` — root top-level include (includes foundational, monitoring, vst, warehouse, etc.)
  - `warehouse/compose.yml` — warehouse sub-include
    - `warehouse-2d-app/warehouse-2d-app.yml` — 2D app services
    - `warehouse-3d-app/warehouse-3d-app.yml` — 3D app services
  - `foundational/mdx-foundational.yml` — Kafka/Redis, broker health check, centralizedb

## NGC Artifacts

| Artifact | NGC Resource | Local directory after extract |
|---|---|---|
| Compose package | `nvidia/vss-warehouse/vss-warehouse-compose:3.1.0` | `vss-warehouse-compose_v3.1.0/` |
| App data (videos, models) | `nvidia/vss-warehouse/vss-warehouse-app-data:3.1.0` | `vss-warehouse-app-data_v3.1.0/` |

## Known Limitations

- Bounding box overlays do not appear in VST in the minimal profile — Elasticsearch is required for overlay rendering. Metadata is available from the live Kafka/Redis stream only.
- Perception model for `warehouse-loading-dock-3cams-synthetic` is trained on synthetic data — accuracy may vary on custom real-world scenes.
- `nv-warehouse-4cams` dataset is only valid with `BP_PROFILE=bp_wh` and `MODE=2d`.
- `warehouse-4cams-20mx20m-synthetic` dataset is only valid with `MODE=3d`.

---

## Choose your path

| Goal | Where to start |
|------|----------------|
| **New machine / first install** | [Full deploy (Phases 1-9)](#full-deploy-phases-1-9). Run phases in order; each must pass before the next. |
| **Redeploy** (`.env` change, clean restart, broken stack) | [Redeploy](#redeploy). Skips Phases 1–4 — host is already set up and artifacts exist. |
| **Tear down only** (stop and remove containers/volumes; keep files on disk) | [Lifecycle: Tear down](#lifecycle-tear-down). |

**`<deployments_dir>`** — directory that contains `compose.yml` and `warehouse/.env`. If unknown, **ask explicitly**: *"What is the full path to your `deployments/` directory?"* before running shell commands for redeploy or tear down.

---

## Lifecycle (shared)

Use these sections for **redeploy**, **Phase 8–9**, and **tear down**. Default log file for bring up and monitor:

```bash
LOG=${LOG:-/tmp/warehouse-blueprint.log}
```

### Lifecycle: Tear down

```bash
cd <deployments_dir>
docker compose --env-file warehouse/.env down
docker volume prune -f
docker system prune -f
bash ./cleanup_all_datalog.sh -b warehouse
```

### Lifecycle: Bring up

Pulls images and builds the perception container (~10–15 min first run). If `docker compose` fails to pull from `nvcr.io`, confirm `NGC_CLI_API_KEY` is set and retry `docker login` as shown.

```bash
LOG=${LOG:-/tmp/warehouse-blueprint.log}
cd <deployments_dir>

docker login --username '$oauthtoken' --password "${NGC_CLI_API_KEY}" nvcr.io

nohup docker compose \
  --env-file warehouse/.env \
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

**Stack is ready when these show `Up`:**

- 2D: `mdx-nvstreamer-2d`, `perception-2d`, `vss-behavior-analytics-2d`, `bp-configurator-2d`, `perception-sdr-2d`
- 3D: `mdx-nvstreamer-3d`, `perception-3d`, `vss-behavior-analytics-3d`, `bp-configurator-3d`, `perception-sdr-3d`

Check FPS:

```bash
docker logs -f perception-2d 2>&1 | grep -i fps | head -5   # 2d
docker logs -f perception-3d 2>&1 | grep -i fps | head -5   # 3d
```

---

## Redeploy

**When to use:** The machine already satisfies [Phase 2](#phase-2-system-prerequisites); compose bundle and app data are already on disk. You edited `warehouse/.env`, need a clean restart, or are recovering a bad state.

**Do not** re-run NGC CLI install, driver install, or artifact download unless something is actually missing or broken.

1. Obtain **`<deployments_dir>`** (ask if unknown — see [Choose your path](#choose-your-path)).
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

```bash
ngc registry resource list "nvidia/vss-warehouse/*"
ngc registry image list "nvidia/vss-core/*"
```

**`Missing org` error** → run `ngc config set` and match the org to the one used when generating the key.

---

### Phase 2: System Prerequisites

Run each check in order. **If a check fails, automatically install and re-verify — do not wait for the user.** Only stop if a requirement cannot be met automatically (unsupported hardware, insufficient RAM/CPU).

#### Supported Hardware

`HARDWARE_PROFILE` is a **blueprint setting**, not a string that `nvidia-smi` always prints verbatim. For **discrete GPUs**, match the GPU model from `nvidia-smi` / `lspci` to a row below. **IGX-THOR** and **DGX-SPARK** are **whole-system platforms** (kits/boards): set the profile from product/SKU or vendor docs if you already know the machine type; `nvidia-smi` shows the **on-board NVIDIA GPU name** (e.g. a Thor-class or Spark system GPU), not the text `IGX-THOR` or `DGX-SPARK`. On **DGX Spark**, unified memory can make some `nvidia-smi` memory fields show **Not Supported**; driver and device listing should still be checked per [DGX Spark user guide](https://docs.nvidia.com/dgx/dgx-spark/).

| Discrete GPU (typical `nvidia-smi` name) | HARDWARE_PROFILE |
|---|---|
| RTX PRO 6000 Blackwell | `RTXPRO6000BW` |
| H100 (NVL, SXM HBM3) | `H100` |
| RTX A6000 Ada Generation | `RTXA6000ADA` |
| RTX A6000 | `RTXA6000` |
| L40S | `L40S` |
| L40 | `L40` |
| L4 | `L4` |
| Platform: NVIDIA IGX Thor (kit / board) | `IGX-THOR` |
| Platform: NVIDIA DGX Spark | `DGX-SPARK` |

**GPUs not in the table:** set a **custom** `HARDWARE_PROFILE` by taking the GPU **`name`** from `nvidia-smi` (same field as the query above) and **removing all spaces** — e.g. `NVIDIA RTX 5000 Blackwell` → `NVIDIARTX5000Blackwell`. Use that string as `HARDWARE_PROFILE`; deployment may still depend on blueprint support for that GPU class.

#### 2.1 GPU Detection and NVIDIA Driver

**Detect GPUs and driver:**

```bash
nvidia-smi --query-gpu=index,name,driver_version,memory.total --format=csv,noheader
```

Use the **`name`** column to pick **`HARDWARE_PROFILE`**: if it matches a **discrete-GPU** row in [Supported Hardware](#supported-hardware), use that profile; **otherwise** use a **custom** profile (GPU `name` with all spaces removed). For **IGX-THOR** or **DGX-SPARK**, set `HARDWARE_PROFILE` to that value when the deployment target is that platform, even though `name` will be a GPU part name, not `IGX-THOR` / `DGX-SPARK`.

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

Fabric Manager is required on systems where multiple GPUs are connected via **NVLink** or **NVSwitch** (e.g. DGX multi-GPU, HGX baseboards, NVSwitch servers, multi-GPU NVLink topologies, datacenter GPUs in NVLink layouts). It is **not** required for single-GPU systems or multi-GPU **PCIe-only** setups without NVLink/NVSwitch.

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

```bash
docker --version        # need 27.2.0+
docker compose version  # need v2.29.0+
docker ps               # must run without sudo
```

**Install Docker if missing:**
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
docker run --rm --gpus all ubuntu:22.04 nvidia-smi 2>&1 | head -8
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
> - **2d** — 2D detection/tracking (RT-DETR), no depth
> - **3d** — 3D perception with depth, requires 4-camera dataset"

#### Q2 — Blueprint Profile

**MODE=2d:**
> - **2D Vision AI** — CV-only, no LLM/VLM. Profile: `bp_wh_kafka` or `bp_wh_redis`. Dataset: `warehouse-loading-dock-3cams-synthetic` (3 streams).
> - **2D Vision AI with Agents** — LLM + VLM NIMs. Profile: `bp_wh`. Dataset: `nv-warehouse-4cams` (4 streams).

**MODE=3d:** Profile fixed to **3D Vision AI** — `bp_wh_kafka` or `bp_wh_redis`. Dataset: `warehouse-4cams-20mx20m-synthetic` (4 streams).

#### Q3 — Stream Type

Skip for `bp_wh`. For `bp_wh_kafka` / `bp_wh_redis`:

> "Which broker — **kafka** or **redis**?"

Variable combinations:

```bash
# 2D Vision AI — kafka:
BP_PROFILE=bp_wh_kafka; STREAM_TYPE=kafka; SAMPLE_VIDEO_DATASET="warehouse-loading-dock-3cams-synthetic"; NUM_STREAMS=3

# 2D Vision AI — redis:
BP_PROFILE=bp_wh_redis; STREAM_TYPE=redis; SAMPLE_VIDEO_DATASET="warehouse-loading-dock-3cams-synthetic"; NUM_STREAMS=3

# 2D Vision AI with Agents:
BP_PROFILE=bp_wh; SAMPLE_VIDEO_DATASET="nv-warehouse-4cams"; NUM_STREAMS=4; LLM_MODE=local; VLM_MODE=local

# 3D Vision AI — kafka:
BP_PROFILE=bp_wh_kafka; STREAM_TYPE=kafka; SAMPLE_VIDEO_DATASET="warehouse-4cams-20mx20m-synthetic"; NUM_STREAMS=4

# 3D Vision AI — redis:
BP_PROFILE=bp_wh_redis; STREAM_TYPE=redis; SAMPLE_VIDEO_DATASET="warehouse-4cams-20mx20m-synthetic"; NUM_STREAMS=4
```

#### Q4 — Deployment Profile

> "Which profile?
> - **minimal** — excludes ELK, Video Analytics API/UI, monitoring. Recommended for IGX-THOR.
> - **extended** — full deployment."

```bash
MINIMAL_PROFILE="true"   # minimal
MINIMAL_PROFILE=""       # extended
```

---

### Phase 4: Download Artifacts (first run only)

> **Versions:** See [NGC Artifacts](#ngc-artifacts) above for current versions and extracted directory names.

```bash
export NGC_CLI_API_KEY='<your-ngc-api-key>'

ngc registry resource download-version "nvidia/vss-warehouse/vss-warehouse-compose:<COMPOSE_VERSION>"
cd vss-warehouse-compose_v<COMPOSE_VERSION>
tar -xvf deploy-warehouse-compose.tar.gz

ngc registry resource download-version "nvidia/vss-warehouse/vss-warehouse-app-data:<APP_DATA_VERSION>"
cd vss-warehouse-app-data_v<APP_DATA_VERSION>
tar -xvf vss-warehouse-app-data.tar.gz

sudo chmod -R 777 /path/to/vss-warehouse-app-data
```

---

### Phase 5: Configure warehouse/.env

Edit `<deployments_dir>/warehouse/.env`:

```bash
MODE=<2d|3d>
BP_PROFILE=<bp_wh_kafka|bp_wh_redis|bp_wh>
STREAM_TYPE=<kafka|redis>
MINIMAL_PROFILE=<"true"|"">

SAMPLE_VIDEO_DATASET="<dataset-name>"
NUM_STREAMS=<3|4>

LLM_MODE=none          # local/remote for bp_wh only
VLM_MODE=none

MDX_SAMPLE_APPS_DIR="/path/to/deployments"
MDX_DATA_DIR="/path/to/vss-warehouse-app-data"

HOST_IP='<HOST_IP>'
NGC_CLI_API_KEY='<your-ngc-api-key>'

# HARDWARE_PROFILE: see Supported Hardware table (H100, RTXA6000ADA, RTXA6000, …), or IGX-THOR / DGX-SPARK,
# or custom: nvidia-smi GPU name with all spaces removed (e.g. NVIDIARTX5000Blackwell).
HARDWARE_PROFILE=H100
```

> DGX-SPARK (SBSA): also uncomment `-sbsa` tagged image variables for `PERCEPTION_TAG`, `VST_*_IMAGE_TAG`, and `NVSTREAMER_IMAGE_TAG`.

---

### Phase 6: Pre-flight Check

**Do not proceed if any check fails. Never use `sudo` with `docker` — fix non-root setup (2.2) first.**

```bash
nvidia-smi --query-gpu=index,name --format=csv,noheader
docker info 2>/dev/null | grep -i "runtimes"
docker run --rm --gpus all ubuntu:22.04 nvidia-smi 2>&1 | head -5
echo "NGC_CLI_API_KEY: ${NGC_CLI_API_KEY:+SET}${NGC_CLI_API_KEY:-NOT SET}"
ngc config current 2>/dev/null | grep -q "apikey" && echo "NGC config: key present" || echo "NGC config: no key"
```

---

### Phase 7: Dry-Run

```bash
cd <deployments_dir>
source warehouse/.env
docker compose --env-file warehouse/.env config | grep "container_name"
```

Show container list to the user, then ask: **"Looks good — deploy now?"**

---

### Phase 8: Deploy

From `<deployments_dir>`, run **[Lifecycle: Bring up](#lifecycle-bring-up)** after the user confirms Phase 7.

---

### Phase 9: Monitor Progress

Run **[Lifecycle: Monitor](#lifecycle-monitor)** using the same `LOG` as Phase 8.

---

## After deploy

See [Access Points](#access-points) for service URLs.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ngc: command not found` | Run Phase 1.2 |
| `Missing org` NGC error | Run `ngc config set`, match org to API key |
| NGC auth / `docker login nvcr.io` fails | Re-export `NGC_CLI_API_KEY` and retry |
| `unknown or invalid runtime name: nvidia` | Install NVIDIA Container Toolkit — Phase 2.3 |
| Streams not appearing in VST | `docker logs mdx-nvstreamer-2d` (or `-3d`) |
| Perception not starting | `docker logs perception-2d` — verify models in `$MDX_DATA_DIR/models/mtmc/` |
| `bp-configurator` health check failing | Wait 60s and recheck (60s start period) |
| Low FPS | GPU oversaturated — reduce `NUM_STREAMS` and redeploy |
| Dataset/mode mismatch | `nv-warehouse-4cams` → `bp_wh` + `MODE=2d`; `warehouse-4cams-20mx20m-synthetic` → `MODE=3d` |
| Redeploy / reset without reinstall | [Redeploy](#redeploy) |
