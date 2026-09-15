# RTVI VLM Performance Benchmarking - Perflab Guide

Performance benchmarking suite for VSS RTVI VLM Microservice video caption and alert generation.

> **Working directory:** All commands in this guide must be run from the **repository root**
> (the directory that contains `perf/`, `docker/`, `src/`, etc.).
> This file lives at `perf/benchmark/PERF_GUIDE.RTVI_VLM.md` — navigate up two levels before running anything:
> ```bash
> cd /path/to/video-search-and-summarization/services/rtvi/rt-vlm
> bash perf/setup_perf_env.sh
> ```

---

## Hardware Requirements

### Supported GPUs

| GPU Family | Variants |
|------------|----------|
| **H100** | h100, h100-nvl, h100-pcie |
| **RTX Pro SE** | rtx6000-ada, rtx6000-server |
| **L40S** | l40s |
| **Jetson Thor** | jetson-thor |
| **DGX Spark (GB10)** | dgx-spark, gb10 |

### System Requirements

- **OS**: Ubuntu 24.04/22.04 or compatible Linux distribution
- **Memory**: 64GB+ system RAM recommended
- **Storage**: SSD with sufficient space for test videos and datasets
- **Docker**: Version 28.2+ — current user must be in the `docker` group (no `sudo` needed):
  ```bash
  sudo usermod -aG docker ${USER}
  newgrp docker   # apply without full re-login (current shell only)
  ```
- **Docker Compose**: Version 2.36+
- **NVIDIA Driver**: 580+
- **NVIDIA Container Toolkit**: Latest version — and the `nvidia` runtime must be registered
  with Docker. If VST containers fail with `unknown or invalid runtime name: nvidia`, run:
  ```bash
  sudo nvidia-ctk runtime configure --runtime=docker
  sudo systemctl restart docker
  ```

---

## Quick Start (3-Step Workflow)

This is the minimal path from bare hardware to running benchmarks.
`setup_perf_env.sh` handles everything end-to-end: VST download, nvstreamer + VST startup,
`.env.perf` generation, RTVI VLM deployment, Python venv creation, and RTSP URL injection.

### Step 1 — Export required variables, then run the setup script

#### Artifactory Access (one-time setup)

`ARTIFACTORY_USER` and `ARTIFACTORY_TOKEN` are required when setup must download
the VST package or any benchmark video. If you check in `perf/vst_package.tar.gz`
and already have all benchmark videos in `PERF_VIDEOS_DIR`, setup can run without
Artifactory credentials. If any video is missing, credentials are required. If
you do need access:

1. **Request DL membership** — join the `it-aws-artifactory-users` distribution
   list at <https://dlrequest.nvidia.com>. Access is typically granted within one
   business day.
2. **Log in to Artifactory** — once the DL is approved, visit
   <https://artifactory.nvidia.com/ui/repos/tree/General/sw-ds-generic-bld-local>
   and sign in with NVIDIA SSO.
3. **Generate an API token** — click **Set Me Up** (top-right corner), then
   click **Generate Token & Create Instructions**.
4. **Set the variables** — use your NVIDIA username as `ARTIFACTORY_USER` and
   the generated token as `ARTIFACTORY_TOKEN`.

The setup script patches the extracted VST package to run
`nvcr.io/rxczgrvsg8nx/vst-dev/vst-streamprocessing:2.1.0-26.04.1`,
`nvcr.io/rxczgrvsg8nx/vst-dev/vst-sensor:2.1.0-26.04.1`,
`nvcr.io/rxczgrvsg8nx/vst-dev/vst-ingress:2.1.0-26.04.1`, and
`nvcr.io/rxczgrvsg8nx/vst-dev/nvstreamer:2.1.0-26.04.1` by default. To test a
different VST build, override `VST_IMAGE_REGISTRY`, `VST_IMAGE_TAG`, or one of
the full-image variables shown by `bash perf/setup_perf_env.sh -h`.

For pinned package testing, check in the VST tarball as `perf/vst_package.tar.gz`
or set `VST_LOCAL_PACKAGE=/path/to/vst_package.tar.gz`. Setup stages that local
tarball before considering the cached `VST_DIR/vst_package.tar.gz` or downloading
`VST_PKG_URL`. Missing benchmark videos require Artifactory credentials so setup
can download them.

The script reads an existing `docker/.env.perf` as defaults
before validation, including `export KEY=value` lines. Exported shell variables
override the file. If a required value is missing from both places, setup exits
with a clear error before starting services. For benchmark correctness, stale
`.env.perf` values for `VLLM_ENABLE_PREFIX_CACHING` and
`VLLM_DISABLE_MM_PREPROCESSOR_CACHE` are ignored unless the caller explicitly
exports those variables in the shell for a non-standard experiment.

```bash
# ── Required ─────────────────────────────────────────────────────────────────
export ARTIFACTORY_USER=your_username          # Artifactory username
export ARTIFACTORY_TOKEN=your_token            # Artifactory API token
export NGC_API_KEY=nvapi-XXXXXX                # NGC API key for model download
export NVIDIA_VISIBLE_DEVICES=0                # GPU index

# ── Optional (override defaults if needed) ───────────────────────────────────
export RTVI_IMAGE=ghcr.io/nvidia-ai-blueprints/vss/vss-rt-vlm:develop-latest
export BACKEND_PORT=8010          # RTVI VLM host port          (default: 8010)
export REDIS_PORT=6379            # VST Redis port               (default: 6379)
export VLM_MODEL_TO_USE=cosmos-reason2
export MODEL_PATH=ngc:nim/nvidia/cosmos-reason2-8b:0303-fp8-static-kv8
# Or use a setup preset:
# export VLM_MODEL_PRESET=cr3-nano-reasoner-fp8
# export VLM_MODEL_PRESET=cr3-nano-reasoner-nvfp4  # Blackwell platforms
# export HF_TOKEN=hf_...          # only needed for private HuggingFace repos


# Install system dependencies and chrony for ntp service
sudo apt update
sudo apt install -y python3-venv sntp chrony

# Check if service is running
systemctl status chrony

# Check timedatectl if NTP service is active
timedatectl  # "NTP service: active" should be present

# Check its sources
chronyc sources -v  # At least one source should show "*" (selected) or "+" (acceptable)

# Check the tracking error
chronyc tracking  # "System time" offset should be < 1ms for accurate latency measurements

# Apply VSS kernel socket buffer tuning (persistent across reboots; run with sudo)
sudo bash perf/apply_vss_sysctl.sh

bash perf/setup_perf_env.sh
# Runs all 12 setup steps:
#   Downloads VST package → starts nvstreamer → starts VST →
#   downloads benchmark videos → auto-generates .env.perf →
#   starts RTVI VLM + DCGM + Node Exporter + Prometheus →
#   creates Python venv → injects live RTSP URL into config
```

> **Tip — inline GPU selection:** you can pass `NVIDIA_VISIBLE_DEVICES` directly on the command line
> without a separate `export`, which is convenient when switching GPUs between runs:
> ```bash
> NVIDIA_VISIBLE_DEVICES=3 bash perf/setup_perf_env.sh
> ```

### Step 2 — Run benchmarks

```bash
source ~/rtvi-vlm-perf-env/bin/activate
cd <repo-root>

# Select the config for your GPU platform:
CONFIG=perf/benchmark/rtvi_vlm_config_h100.yaml      # H100 / H100-NVL / H100-PCIe
# CONFIG=perf/benchmark/rtvi_vlm_config_rtx_pro.yaml  # RTX Pro SE
# CONFIG=perf/benchmark/rtvi_vlm_config_l40s.yaml     # L40S (set VLLM_GPU_MEMORY_UTILIZATION=0.45 in .env.perf)
# CONFIG=perf/benchmark/rtvi_vlm_config_jetson.yaml   # Jetson Thor
# CONFIG=perf/benchmark/rtvi_vlm_config_spark.yaml    # DGX Spark (GB10)
BENCH="python3 perf/benchmark/rtvi_perf_benchmark.py --config $CONFIG"


# Runs all the BCD 1-4 benchmarks in one go, for running specific benchmarks select scenarios below
$BENCH

# Run only if need specific scenario above command runs everything
# ── BCD 1: Maximum streams per GPU [Stream Processing] ──────────────────────
$BENCH --scenario max_live_streams_test_1_token_448    # 1-token OSL,   448×448
$BENCH --scenario max_live_streams_test_100_token_448  # 100-token OSL, 448×448
$BENCH --scenario max_live_streams_test_1_token        # 1-token OSL,   372×372
$BENCH --scenario max_live_streams_test_100_token      # 100-token OSL, 372×372

# Optional BCD 1 CLI overrides (override YAML config values at runtime):
#   --initial-stream-count N        starting stream count  (see platform config for per-GPU defaults)
#   --add-stream-count N            streams added per step (H100/RTX PRO: 3, Jetson/Spark: 2)
#   --binary-search-refinement      enable Phase 2 fine-tune  (default: on)
#   --no-binary-search-refinement   disable Phase 2 fine-tune
# Example — fast ramp then Phase 2 refinement:
$BENCH --scenario max_live_streams_test_1_token_448 --initial-stream-count 10 --add-stream-count 5

# ── BCD 2: VLM E2E Latency [Stream Processing] ──────────────────────────────
# Single-stream baseline (primary BCD 2 data point):
$BENCH --scenario single_live_stream_test_1_token      # 1-token OSL,   1 stream
$BENCH --scenario single_live_stream_test_100_token    # 100-token OSL, 1 stream
# Concurrency sweep at fixed stream counts (additional BCD 2 data points):
$BENCH --scenario concurrency_test_1_token             # 1-token OSL,   [1,10,20] streams
$BENCH --scenario concurrency_test_100_token           # 100-token OSL, [1,10,20] streams
# Jetson Thor / DGX Spark: use lower stream counts (see Platform-Specific Concurrency Levels below)
# $BENCH --scenario concurrency_test_1_token    --concurrency-levels 1 5 10
# $BENCH --scenario concurrency_test_100_token  --concurrency-levels 1 5 10

# ── BCD 3: Request Throughput [Non-Streaming] ───────────────────────────────
# RPS (requests/s) across concurrency [1,2,4,8,16,32,64], 10 s video (1 chunk/request)
# Jetson Thor / DGX Spark: use lower concurrency (see Platform-Specific Concurrency Levels below)
# ── BCD 4: VLM E2E Request Latency [Non-Streaming] ──────────────────────────
# concurrency [1,2,4,8,16,32] at 10 s; concurrency [1] at 600 s and 3600 s
$BENCH --scenario e2e_latency_1_token    # 1-token OSL
$BENCH --scenario e2e_latency_100_token  # 100-token OSL
# Jetson Thor / DGX Spark: use lower concurrency (see Platform-Specific Concurrency Levels below)
# $BENCH --scenario e2e_latency_1_token    --concurrency-levels 1 2 4 8
# $BENCH --scenario e2e_latency_100_token  --concurrency-levels 1 2 4 8
```

### Step 3 — View results

```bash
ls rtvi-vlm-perf-report/   # relative to repo root (where the benchmark script is run from)
# Prometheus: http://localhost:9090
# DCGM metrics: http://localhost:9400/metrics
```

> **Note:** `setup_perf_env.sh` modifies `rtvi_vlm_config_test.yaml` **and** all five
> platform-specific configs in-place (injects RTSP URL, backend port, GPU list). To reset
> them before re-running on a different machine or with a different RTSP stream:
> ```bash
> git checkout perf/benchmark/rtvi_vlm_config_test.yaml \
>              perf/benchmark/rtvi_vlm_config_h100.yaml \
>              perf/benchmark/rtvi_vlm_config_rtx_pro.yaml \
>              perf/benchmark/rtvi_vlm_config_l40s.yaml \
>              perf/benchmark/rtvi_vlm_config_jetson.yaml \
>              perf/benchmark/rtvi_vlm_config_spark.yaml
> ```

---

## Service Deployment

> **If you used `perf/setup_perf_env.sh` (Quick Start):** the service is already running.
> This entire section is for **manual deployment only** — skip it unless you need to
> deploy or reconfigure the service without the setup script.

For detailed deployment instructions, refer to the release documentation:
- **Release Documentation**: [README.RTVI_VLM.release.md](../../README.RTVI_VLM.release.md)

### Container Images

| Image | Registry |
|-------|----------|
| **RTVI VLM Microservice** | `ghcr.io/nvidia-ai-blueprints/vss/vss-rt-vlm:develop-latest` |

### VLM Deployment (Recommended: compose.perf.yaml)

`compose.perf.yaml` is a standalone Compose file optimised for benchmarking:
- **No Kafka or Redis** — `KAFKA_ENABLED=false`, `ENABLE_REDIS_ERROR_MESSAGES=false`
- **No otel-collector or Jaeger** — reduces container overhead
- **DCGM Exporter, Node Exporter, Prometheus always start** — no `--profile` flag needed
- **Uses `prometheus.perf.yml`** — scrape config without the otel-collector target

#### 1. Prepare Deployment Directory

```bash
cd docker
```

#### 2. Configure Environment

`.env.perf` is auto-generated by `perf/setup_perf_env.sh` from the environment variables
you export before running the script. On later runs, the script also reads the existing
file as defaults before validation, so persistent credentials and perf knobs can live in
`docker/.env.perf`; exported shell variables still override the file.
To customise manual compose deployment further, edit the generated file before running
`docker compose up`.

For **manual** creation:

```bash
cat > .env.perf << 'EOF'
# Service Configuration
BACKEND_PORT=8010
RTVI_IMAGE=ghcr.io/nvidia-ai-blueprints/vss/vss-rt-vlm:develop-latest
NVIDIA_VISIBLE_DEVICES=0

# Model Configuration
VLM_MODEL_TO_USE=cosmos-reason2
MODEL_PATH=ngc:nim/nvidia/cosmos-reason2-8b:0303-fp8-static-kv8
# For CR3 Nano Reasoner FP8, use:
# VLM_MODEL_TO_USE=cosmos-reason3
# MODEL_PATH=ngc:nim/nvidia/cosmos3-nano-reasoner:modelopt-fp8-final_format_fix
# For CR3 Nano Reasoner NVFP4 on Blackwell platforms, use:
# VLM_MODEL_TO_USE=cosmos-reason3
# MODEL_PATH=ngc:nim/nvidia/cosmos3-nano-reasoner:modelopt-nvfp4-full-quantize-final_format_fix
NGC_API_KEY=nvapi-XXXXXX

# BCD benchmark required settings
VLLM_IGNORE_EOS=true
VSS_SKIP_INPUT_MEDIA_VERIFICATION=1
VLLM_ENABLE_PREFIX_CACHING=false
VLLM_DISABLE_MM_PREPROCESSOR_CACHE=true
VLLM_MM_TENSOR_IPC=
VLLM_MM_ENCODER_ATTN_BACKEND=
VLLM_ATTENTION_BACKEND=                  # CR3 Super NVFP4 on GB300 defaults to TRITON_ATTN
# RTSP jitter buffer in milliseconds. BCD latency uses last-frame NTP
# timestamp to caption output, so buffer delay counts in the metric.
RTVI_RTSP_LATENCY=300
RTVI_RTPJITTERBUFFER_DROP_ON_LATENCY=false
RTVI_RTPJITTERBUFFER_FASTSTART_MIN_PACKETS=2
RTVI_EMPTY_CUDA_CACHE_ON_RESULT=false

VLM_DEFAULT_NUM_FRAMES_PER_SECOND_OR_FIXED_FRAMES_CHUNK=80
RTVI_ADD_TIMESTAMP_TO_VLM_PROMPT=false
LOG_LEVEL=warning
EOF
```

**Key environment variables for benchmarking:**
- `VLLM_IGNORE_EOS=true`: Forces exactly N-token generation (ignores end-of-sequence token)
- `VSS_SKIP_INPUT_MEDIA_VERIFICATION=1`: Allows faster stream addition for max-streams tests
- `RTVI_RTSP_LATENCY`: RTSP jitter buffer in **milliseconds**. Perf compose defaults to `300` so jitter-buffer delay does not dominate the last-frame-NTP-to-caption latency metric.
- `RTVI_RTPJITTERBUFFER_DROP_ON_LATENCY=false`: Keeps `rtspsrc`/`rtpjitterbuffer` from dropping late packets to enforce the latency window. Keep false for BCD no-drop max-stream runs.
- `RTVI_RTPJITTERBUFFER_FASTSTART_MIN_PACKETS`: `rtpjitterbuffer.faststart-min-packets`. Perf compose defaults to `2` so high-latency no-drop RTSP streams can start forwarding after two consecutive packets instead of waiting for the full jitter-buffer latency window.
- `RTVI_EMPTY_CUDA_CACHE_ON_RESULT=false`: Keeps per-result `torch.cuda.empty_cache()` off during perf so every decoded chunk does not force allocator synchronization. Set true only for diagnostic runs.
- `VLLM_ENABLE_PREFIX_CACHING=false`: Prefix cache disabled for true perf measurement. `setup_perf_env.sh` regenerates this value even if an older `.env.perf` had it enabled.
- `VLLM_DISABLE_MM_PREPROCESSOR_CACHE=true`: MM preprocessor cache disabled for true perf measurement. `setup_perf_env.sh` regenerates this value even if an older `.env.perf` had it enabled.
- `VLLM_MM_TENSOR_IPC`: Optional patched-vLLM multimodal tensor IPC experiment. Leave empty for BCD perf runs; set `torch_shm` only when explicitly validating that path.
- `VLLM_MM_ENCODER_ATTN_BACKEND`: Optional vLLM multimodal encoder attention backend override. Leave empty for the patched default path; set `XFORMERS` only for experiments or emergency workaround runs.
- `VLLM_ATTENTION_BACKEND`: Optional decoder attention backend override. CR3 Super NVFP4 on GB300 defaults to `TRITON_ATTN`; explicit values take precedence.
- `VLLM_MAX_NUM_BATCHED_TOKENS`: Optional — cap token budget per scheduler step

#### 3. Launch RTVI VLM Service (manual / re-deploy)

```bash
# Start VLM + DCGM + Node Exporter + Prometheus (all-in-one)
docker compose -f compose.perf.yaml --env-file .env.perf up -d

# Verify all services started
docker compose -f compose.perf.yaml ps
```

### Health Check

```bash
curl -X GET http://localhost:8010/v1/health/ready -H 'accept: application/json'
```

Expected response:
```json
{
  "object": "health.response",
  "message": "Service is ready."
}
```

---

## Test Data

### Prepare Test Videos

#### Benchmark Videos (downloaded automatically by setup_perf_env.sh)

`perf/setup_perf_env.sh` downloads the four benchmark videos to `PERF_VIDEOS_DIR`
(default: `~/rtvi-perf/vst_package/videos/`).
`compose.perf.yaml` mounts that directory into the container at `/opt/nvidia/rtvi/streams/perf/`.

| File | Duration | Used by |
|------|----------|---------|
| `warehouse_gopro_10s.mp4` | 10 s | BCD 3 (file_burst) + BCD 4 (e2e_latency 10 s baseline) |
| `warehouse_gopro_1m.mp4` | 60 s | nvstreamer source for VST live RTSP stream |
| `warehouse_gopro_10m.mp4` | 600 s | BCD 4 (e2e_latency 10-min point) |
| `warehouse_gopro_60m.mp4` | 3600 s | BCD 4 (e2e_latency 60-min point) |

If `warehouse_gopro_10s.mp4` is not available on Artifactory, extract it locally:

```bash
ffmpeg -i "${PERF_VIDEOS_DIR}/warehouse_gopro_1m.mp4" \
       -t 10 -c copy "${PERF_VIDEOS_DIR}/warehouse_gopro_10s.mp4"
```

#### RTSP Stream Setup via VST (For Live Stream Tests)

Use `perf/setup_perf_env.sh` to automate this entirely (see **Quick Start** above).
All live-stream benchmarks use VST-managed streams at `rtsp://<HOST>/live/<stream_id>`.

```bash
# Run setup script (downloads VST, starts it, polls for streams, injects URL)
export ARTIFACTORY_USER=your_username
export ARTIFACTORY_TOKEN=your_token
bash perf/setup_perf_env.sh

# Or manually start VST if already installed:
cd ~/rtvi-perf/vst_package
REDIS_PORT=${REDIS_PORT:-6379} bash deploy.sh up vst
```

The setup script discovers available streams via the VST API:

```bash
VST_API="http://localhost:30888/vst/api/v1/sensor/streams"
# Poll until streams with /live/ paths are available
RTSP_URLS=$(curl -sf "$VST_API" \
    | jq -r '.. | strings | select(startswith("rtsp://")) | select(contains("/live/"))' \
    | paste -sd ';')
echo "Detected streams: $RTSP_URLS"
```

The first detected URL is automatically injected into `rtvi_vlm_config_test.yaml`,
replacing the `RTSP_STREAM_URL` placeholder in all scenarios.

**VST RTSP URL format:**
```text
rtsp://<HOST_IP>/live/<stream_id>
```

**Redis Port Conflict Resolution:**

If port 6379 is already in use on your system:
```bash
export REDIS_PORT=6380   # or any free port
bash perf/setup_perf_env.sh
# Script patches VST's deploy.sh and vst_config.json to use the new port
```

---

## Performance Test Tooling

### Benchmark Framework

The RTVI VLM repository includes a comprehensive benchmarking framework:

**Location**: `perf/benchmark/`

**Key Files**:
- `rtvi_perf_benchmark.py`: Main benchmark script
- `rtvi_vlm_config_test.yaml`: BCD test configuration file
- `rtvi_sample_config.yaml`: Sample test configuration
- `README.RTVI_VLM.md`: Detailed benchmark documentation
- `requirements.txt`: Python dependencies

### Benchmark Capabilities

The framework supports four core operational modes:

1. **Single File Mode** - Complete video caption generation workflow (E2E latency testing)
2. **File Burst Mode** - Concurrent video processing throughput testing
3. **Max Live Streams Mode** - Maximum concurrent RTSP streams capacity testing
4. **VLM Captions Burst Mode (Concurrency)** - VLM caption generation under concurrent load

### Python Environment Setup

```bash
# Install system dependencies
sudo apt install python3-venv sntp

# Install chrony for ntp service
sudo apt update && sudo apt install chrony -y

# Check if service is running
systemctl status chrony

# Check timedatectl if NTP service is active
timedatectl

# Check its sources
chronyc sources -v

# Check the tracking error
chronyc tracking

# Optional: Add sources and iburst mode
# sudo nano /etc/chrony/chrony.conf
# you can add your own at the top: server ntp.your-provider.com iburst (The iburst keyword is recommended as it speeds up the initial sync.)
# Restart the service: sudo systemctl restart chrony

# Create virtual environment (setup_perf_env.sh does this automatically at ~/rtvi-vlm-perf-env)
python3 -m venv ~/rtvi-vlm-perf-env
source ~/rtvi-vlm-perf-env/bin/activate

# Install benchmark dependencies
cd /path/to/video-search-and-summarization/services/rtvi/rt-vlm
pip install -r perf/benchmark/requirements.txt

# Install CLI client dependencies
pip install sseclient-py requests tabulate tqdm pyyaml protobuf
```

---

## Benchmark Configuration

### Platform-Specific Configuration Files

Choose the config that matches your GPU. Each file is a copy of `rtvi_vlm_config_test.yaml`
with `initial_stream_count` and `add_stream_count` tuned per platform for BCD 1.

| File | Platform | 372×372 initial | 448×448 initial | add | Notes |
|------|----------|-----------------|-----------------|-----|-------|
| `rtvi_vlm_config_h100.yaml` | H100 / H100-NVL / H100-PCIe | 100 | 25 / 20 (1T/100T) | 3 | |
| `rtvi_vlm_config_rtx_pro.yaml` | RTX Pro SE | 80 | 20 / 10 (1T/100T) | 3 | |
| `rtvi_vlm_config_l40s.yaml` | L40S | 20 | 20 | 1 | Set `VLLM_GPU_MEMORY_UTILIZATION=0.45` in `.env.perf` |
| `rtvi_vlm_config_jetson.yaml` | Jetson Thor | 5 | 1 | 2 | |
| `rtvi_vlm_config_spark.yaml` | DGX Spark (GB10) | 5 | 1 | 2 | |
| `rtvi_vlm_config_test.yaml` | Generic / base | 5 | 5 | 3 | |

`setup_perf_env.sh` patches all six files with the live RTSP URL and port values.

### Configuration File: `rtvi_vlm_config_test.yaml`

The benchmark uses YAML configuration to define test scenarios. Key sections:

#### Global Settings

```yaml
global:
  rtvi_backend: "http://localhost:8010/v1"
  output_dir: "rtvi-vlm-perf-report"
  vlm_gpus: [0]  # GPUs to monitor during benchmarking

  gpu_monitoring:
    enabled: true
    interval_seconds: 1
    # Optional: Prometheus/DCGM exporter (runs alongside legacy pynvml monitoring)
    prometheus:
      enabled: true
      dcgm_exporter_url: "http://localhost:9400/metrics"
      node_exporter_enabled: true  # Enable CPU and memory metrics collection
      node_exporter_url: "http://localhost:9100/metrics"  # injected by setup_perf_env.sh from NODE_EXPORTER_PORT
      scrape_interval_seconds: 1  # defaults to gpu_monitoring.interval_seconds

  # /generate_captions API parameters Global
  generate_captions_params:
    temperature: 0.7
    max_tokens: 100
    enable_audio: false

  # Default prompts
  prompt: "Describe the key events in this video with timestamps."
```

#### Test Scenarios

The config file defines multiple test scenarios aligned with BCD requirements:

| Scenario | Mode | Resolution | max_tokens | Source | Notes |
|---|---|---|---|---|---|
| `single_live_stream_test_1_token` | single_live_stream | 372×372 | 1 | VST `/live/` | BCD 2 — 1-token |
| `single_live_stream_test_100_token` | single_live_stream | 372×372 | 100 | VST `/live/` | BCD 2 — 100-token |
| `max_live_streams_test_1_token_448` | max_live_streams | **448×448** | 1 | VST `/live/` | BCD 1 — 448 px, 1-token |
| `max_live_streams_test_100_token_448` | max_live_streams | **448×448** | 100 | VST `/live/` | BCD 1 — 448 px, 100-token |
| `concurrency_test_1_token` | concurrent_live_streams | **448×448** | 1 | VST `/live/` | BCD 2 — stream_count [1,10,20] |
| `concurrency_test_100_token` | concurrent_live_streams | **448×448** | 100 | VST `/live/` | BCD 2 — stream_count [1,10,20] |
| `file_burst_1_token` | **file_burst** | **448×448** | 1 | in-container file | BCD 3 — RPS, concurrency [1,2,4,8,16,32,64] |
| `file_burst_100_token` | **file_burst** | **448×448** | 100 | in-container file | BCD 3 — RPS, concurrency [1,2,4,8,16,32,64] |
| `e2e_latency_1_token` | **file_burst** | **448×448** | 1 | in-container file | BCD 4 — concurrency [1,2,4,8,16,32] at 10 s; [1] at 10 min/60 min |
| `e2e_latency_100_token` | **file_burst** | **448×448** | 100 | in-container file | BCD 4 — concurrency [1,2,4,8,16,32] at 10 s; [1] at 10 min/60 min |
| `max_live_streams_test_1_token` | max_live_streams | 372×372 | 1 | VST `/live/` | legacy — 372 px |
| `max_live_streams_test_100_token` | max_live_streams | 372×372 | 100 | VST `/live/` | legacy — 372 px |
| `single_file_test` | single_file | default | 100 | in-container file | legacy sample |

All stream scenarios use VST-managed RTSP streams at `rtsp://<HOST>/live/<stream_id>`.
The `RTSP_STREAM_URL` placeholder in the config is replaced by `setup_perf_env.sh`.

The `concurrent_live_streams` mode starts all N streams simultaneously and measures
per-chunk latency across stream counts of [1, 10, 20] — unlike `max_live_streams`
which ramps linearly then fine-tunes to find the exact capacity ceiling.

---

## Running Benchmarks

### Basic Usage

```bash
# Activate virtual environment
source ~/rtvi-vlm-perf-env/bin/activate

# Set backend URL (if different from config)
export RTVI_BACKEND=http://localhost:8010/v1

# List available modes and scenarios
python3 perf/benchmark/rtvi_perf_benchmark.py \
  --config perf/benchmark/rtvi_vlm_config_test.yaml \
  --list-modes

python3 perf/benchmark/rtvi_perf_benchmark.py \
  --config perf/benchmark/rtvi_vlm_config_test.yaml \
  --list-scenarios
```

### Run Specific Test Scenarios

```bash
CONFIG=perf/benchmark/rtvi_vlm_config_test.yaml

# Run any scenario by name:
python3 perf/benchmark/rtvi_perf_benchmark.py --config $CONFIG --scenario <scenario_name>

# Enable debug logging:
python3 perf/benchmark/rtvi_perf_benchmark.py --config $CONFIG --scenario <scenario_name> --debug
```

For the full list of BCD scenario commands, see **[Complete Benchmark Flow](#complete-benchmark-flow)** below.

---

## BCD Test Scenarios

The following scenarios are designed to measure key BCD (Benchmark Configuration Details) metrics:

### BCD 1: Maximum Number of Streams Per GPU

**Metric**: The maximum number of concurrent RTSP streams processed at the target token budget per GPU without dropping chunks.

**Test Scenarios**: `max_live_streams_test_1_token_448` (1-token) and `max_live_streams_test_100_token_448` (100-token)

**Configuration**:
```yaml
max_live_streams_test_1_token_448:
  benchmark_mode: "max_live_streams"
  generate_captions_params:
    vlm_input_width: 448
    vlm_input_height: 448
  videos:
    - name: "warehouse_cam_448_single_token"
      rtsp_url: "RTSP_STREAM_URL"   # injected by setup_perf_env.sh Step 11
      chunk_sizes: [10]
      latency_threshold_seconds: 10
      initial_stream_count: 5    # base config default; platform configs set higher values (see rtvi_vlm_config_h100/rtx_pro/jetson/spark.yaml)
      stability_check_interval: 45
      add_stream_count: 1
      required_stable_windows: 2
      required_unstable_windows: 3
      min_stable_stream_coverage: 0.9
      min_fresh_measurements_per_stream: 1
      generate_captions_params:
        temperature: 0.5
        max_tokens: 1
      prompt: "You are a warehouse monitoring system. Look for anomalies. Detect for a box being dropped, or for any property being damaged, or for anyone entering a restricted area. Answer Yes or No only."
```

**Prerequisites**:
- For BCD max-stream runs, keep `RTVI_RTSP_LATENCY=300` (perf compose default). The benchmark uses `media_info.end_timestamp` (last-frame NTP timestamp) to caption SSE receive time by default; set `latency_measurement_source: processing_latency` only for server-processing diagnostics.
- Set `VLLM_IGNORE_EOS=true` in `.env.perf` for N-token generation performance testing
- Set `VSS_SKIP_INPUT_MEDIA_VERIFICATION=1` in `.env.perf` for faster stream addition

`setup_perf_env.sh` writes the cache and skip-verification settings.

**Phase 2 Fine-Tune** (enabled by default):

After the linear ramp-up (Phase 1) detects degradation at N_u streams with the last confirmed
stable count at N_s, Phase 2 finds the exact maximum via a clean restart:

1. **Tear-down**: all N_u streams are deleted in parallel so the server is fully quiescent.
2. **Clean restart at N_s**: streams are re-added with uniform spacing
   (`chunk_size / N_s` seconds between additions) for a reproducible request rate.
3. **Linear fine-tune**: after confirming stability at N_s, one stream is added at a time
   (each with correct spacing), clearing latency history between additions, until
   degradation is detected. The last confirmed stable count is returned.

This approach guarantees reproducible results — each count sees an evenly-spaced load
profile identical to what Phase 1 used — without back-and-forth deletions.

Recommended: set `add_stream_count: 5` or higher for Phase 1 speed; Phase 2 will refine.
Each stability window also requires fresh latency samples from at least
`min_stable_stream_coverage` active streams (default: 0.9) with at least
`min_fresh_measurements_per_stream` new sample(s) per stream (default: 1). This prevents
stale readings from older streams from marking a higher stream count stable when newly added
streams are not producing fresh SSE latency samples. Low fresh coverage is treated as
inconclusive while queued responses are still arriving; it only counts as an unstable
window after `fresh_latency_timeout_seconds` (default:
`stability_check_interval * (required_stable_windows + required_unstable_windows)`). By
default each window also requires no chunk-ID gaps (`require_no_dropped_chunks: true`).
RTVI VLM latency uses `media_info.end_timestamp` by default to match BCD. Optional
per-chunk `processing_latency_s` / `chunk_latency_ms` fields remain useful diagnostics
when `latency_measurement_source: processing_latency` is set for experiments.

**CLI overrides** (applied per-run without editing the YAML config):

```bash
# Fast ramp (5 streams/step) with Phase 2 refinement enabled (default):
python3 perf/benchmark/rtvi_perf_benchmark.py --config $CONFIG \
  --scenario max_live_streams_test_1_token_448 \
  --initial-stream-count 5 --add-stream-count 5

# Disable Phase 2 (report Phase 1 result only):
python3 perf/benchmark/rtvi_perf_benchmark.py --config $CONFIG \
  --scenario max_live_streams_test_1_token_448 \
  --no-binary-search-refinement
```

To disable Phase 2 permanently via the YAML config instead:

```yaml
binary_search_refinement: false   # inside the video config block
```

Results include `phase2_fine_tune_applied`, `phase2_stable_start`, and
`phase2_unstable_ceiling` fields in `max_live_streams_results.json`.

**Metrics Reported**:
- Maximum stable stream count
- Per-stream latency at capacity
- GPU utilization at max capacity
- Chunk drop rate

### BCD 2: VLM E2E Latency (Stream Processing)

**Metric**: The total time elapsed from the moment a chunk was created until the corresponding VLM caption is generated and posted to an endpoint. This includes all delays: preprocess, queue, infer, and post.

**Aggregation**: min, max, average, p90, and p99

**Test Scenario**: `single_live_stream_test_1_token` or `single_live_stream_test_100_token`

**Configuration**:
```yaml
single_live_stream_test_100_token:
  benchmark_mode: "single_live_stream"
  iterations: 2
  duration_seconds: 100
  generate_captions_params:
    vlm_input_width: 448
    vlm_input_height: 448
    temperature: 0.5
    max_tokens: 100
  videos:
    - name: "video_stream"
      rtsp_url: "RTSP_STREAM_URL"   # injected by setup_perf_env.sh Step 11
      chunk_sizes: [10]
```

**Metrics Reported**:
- Stream processing latency (min, max, avg, p90, p99)
- Caption generation rate
- Stream stability metrics
- GPU utilization
- CPU utilization (when Node Exporter enabled)
- Memory usage (when Node Exporter enabled)

### BCD 3: Request Throughput (Non-Streaming)

**Metric**: The average number of requests that the microservice can successfully complete per second. This is the same as aiperf RPS metric.

**Test Scenarios**: `file_burst_1_token` and `file_burst_100_token`

**Configuration**: `file_burst` mode sweeps `concurrency_levels` [1, 2, 4, 8, 16, 32, 64] using a 10 s video (1 chunk per request). Aggregation: min, max, average, p90, and p99.

```yaml
file_burst_1_token:
  benchmark_mode: "file_burst"
  generate_captions_params:
    vlm_input_width: 448
    vlm_input_height: 448
    max_tokens: 1
  videos:
    - filepath: "/opt/nvidia/rtvi/streams/perf/warehouse_gopro_10s.mp4"
      chunk_sizes: [10]
      concurrency_levels: [1, 2, 4, 8, 16, 32, 64]
```

**Metrics Reported**:
- Per-concurrency RPS: min, max, average, p90, p99
- E2E latency at each concurrency: min, max, average, p90, p99
- GPU utilization across concurrency sweep

### BCD 4: VLM E2E Request Latency (Non-Streaming)

**Metric**: The duration between the initiation of a request and completion of the response. This is the same as the aiperf e2e_latency.

**Aggregation**: min, max, average, p90, and p99.

**Concurrency sweep**: [1, 2, 4, 8, 16, 32] at 10 s; concurrency [1] at 600 s and 3600 s.

**Test Scenarios**: `e2e_latency_1_token` and `e2e_latency_100_token`

**Configuration**:
```yaml
e2e_latency_1_token:
  benchmark_mode: "file_burst"
  generate_captions_params:
    vlm_input_width: 448
    vlm_input_height: 448
    max_tokens: 1
  videos:
    - name: "warehouse_10s"       # 10 s — 1 chunk per request
      filepath: "/opt/nvidia/rtvi/streams/perf/warehouse_gopro_10s.mp4"
      chunk_sizes: [10]
      concurrency_levels: [1, 2, 4, 8, 16, 32]
    - name: "warehouse_10min"     # 600 s — 60 chunks per request
      filepath: "/opt/nvidia/rtvi/streams/perf/warehouse_gopro_10m.mp4"
      chunk_sizes: [10]
      concurrency_levels: [1]
    - name: "warehouse_60min"     # 3600 s — 360 chunks per request
      filepath: "/opt/nvidia/rtvi/streams/perf/warehouse_gopro_60m.mp4"
      chunk_sizes: [10]
      concurrency_levels: [1]
```

**Metrics Reported**:
- E2E latency per video duration × concurrency level: min, max, average, p90, and p99
- VLM pipeline latency and decode latency
- GPU utilization
- NVDEC utilization

---

## Parameters to Sweep

### Model Variants

Test different configurations by updating environment variables in `.env`:

**Token Generation**:
- `max_tokens: 1` - Single token generation (Yes/No responses)
- `max_tokens: 100` - Standard caption generation
- `max_tokens: 512` - Extended caption generation

**Input Resolution**:
```yaml
generate_captions_params:
  vlm_input_width: 448   # High quality
  vlm_input_height: 448
```
```yaml
generate_captions_params:
  vlm_input_width: 640   # 4K optimized
  vlm_input_height: 320
```

**VLLM Configuration**:
```bash
# Restart service after changing environment variables (run from docker/)
docker compose -f compose.perf.yaml --env-file .env.perf down
docker compose -f compose.perf.yaml --env-file .env.perf up -d
```

### GPU Assignment

Test with different GPU configurations:

```bash
# Single GPU (run from docker/)
NVIDIA_VISIBLE_DEVICES=0 docker compose -f compose.perf.yaml --env-file .env.perf up -d

# Multi-GPU (if supported)
NVIDIA_VISIBLE_DEVICES=0,1 docker compose -f compose.perf.yaml --env-file .env.perf up -d
```

### Concurrency Parameters

Modify concurrency test in config file:

```yaml
file_burst_test:
  videos:
    - filepath: "/opt/nvidia/rtvi/streams/its.mp4"
      chunk_sizes: [10]
      concurrency_levels: [1, 2, 4, 8, 16, 32]  # Parallel requests
```

### Chunk Duration Sweep

Test different chunk sizes:

```yaml
videos:
  - filepath: "/opt/nvidia/rtvi/streams/warehouse.mp4"
    chunk_sizes: [5, 10, 15, 30, 60]  # Different chunk durations
```

### EVS++ Configuration

EVS++ is configured on the service rather than per benchmark request, so it takes a service restart to apply. Exported values override `.env.perf`. `perf/setup_perf_env.sh` writes all of these keys into `.env.perf`, but EVS is **off** by default — both `VIA_EVS_SESSION=false` and an empty `VLM_VIDEO_PRUNING_RATE` — so a fresh setup run is a clean non-EVS baseline. The remaining values are pre-populated at their EVS++ settings (`VLLM_EVS_SIMILARITY_THRESHOLD=0.4`, `VIA_EVS_TOKEN_BUDGET=1`). Export `VIA_EVS_SESSION=true` and `VLM_VIDEO_PRUNING_RATE=0.5` before running setup to generate an EVS++-enabled `.env.perf`, or apply them to an already-running service:

```bash
# Run from docker/
export VIA_EVS_SESSION=true
export VLM_VIDEO_PRUNING_RATE=0.5          # required: activates pruning in vLLM
export VLLM_EVS_SIMILARITY_THRESHOLD=0.4
export VIA_EVS_TOKEN_BUDGET=1
export VLLM_IGNORE_EOS=true                # hold OSL fixed against the baseline

docker compose -f compose.perf.yaml --env-file .env.perf down
docker compose -f compose.perf.yaml --env-file .env.perf up -d
```

`VIA_EVS_TOKEN_BUDGET=1` is the default; it forces generation on each clip instead of accumulating visual tokens across clips, which keeps caption counts aligned with the non-EVS baseline so latency and throughput compare directly instead of needing per-caption normalization. Confirm the caption counts actually match before comparing — if the EVS++ run emitted materially fewer captions, something is still gating or accumulating generation and the numbers are not comparable.

All benchmark modes exercise EVS++ when session mode is on. The routing decision is made on `VIA_EVS_SESSION` alone, so the file-based modes (single file, file burst) go through the session path just as the live-stream modes do, despite the "per-stream session" naming.

---

## Benchmark Modes Deep Dive

### 1. Single File Mode

Tests complete E2E workflow: file upload + caption generation.

**Configuration**:
```yaml
single_file_test:
  benchmark_mode: "single_file"
  iterations: 5
  videos:
    - filepath: "/opt/nvidia/rtvi/streams/its.mp4"
      chunk_sizes: [10, 30, 60]
      prompt: "Monitor traffic events, violations, and vehicle types."
```

**Metrics Reported**:
- E2E latency (upload + processing)
- VLM pipeline latency
- Decode latency
- GPU utilization
- NVDEC utilization

### 2. File Burst Mode (Concurrency)

Tests throughput under concurrent load with binary search for optimal concurrency.

**Configuration**:
```yaml
file_burst_test:
  benchmark_mode: "file_burst"
  videos:
    - filepath: "/opt/nvidia/rtvi/streams/warehouse.mp4"
      chunk_sizes: [10]
      concurrency_levels: [1, 2, 4, 8, 16]
      target_latency_seconds: 60.0
```

**Metrics Reported**:
- Per-concurrency throughput
- Average request latency
- P90/P95/P99 latencies
- Optimal concurrency point

### 3. Single Live Stream Mode

Tests caption generation from RTSP stream.

**Configuration**:
```yaml
single_live_stream_test:
  benchmark_mode: "single_live_stream"
  iterations: 2
  duration_seconds: 180
  videos:
    - name: "test_stream"
      rtsp_url: "rtsp://host:8554/stream"
      chunk_sizes: [10]
```

**Metrics Reported**:
- Stream processing latency (min, max, avg, p90, p99)
- Caption generation rate
- Stream stability metrics
- GPU utilization

### 4. Max Live Streams Mode

Finds maximum number of concurrent RTSP streams the system can handle.

**Configuration**:
```yaml
max_live_streams_test:
  benchmark_mode: "max_live_streams"
  videos:
    - name: "capacity_test"
      rtsp_url: "rtsp://host:8554/stream"
      chunk_sizes: [10]
      latency_threshold_seconds: 10
      initial_stream_count: 3
      add_stream_count: 1
      stability_check_interval: 45
```

**Metrics Reported**:
- Maximum stable stream count
- Per-stream latency at capacity
- GPU utilization at max capacity
- Chunk processing statistics

---

## Complete Benchmark Flow

```bash
# Select the config for your GPU platform:
CONFIG=perf/benchmark/rtvi_vlm_config_h100.yaml      # H100 / H100-NVL / H100-PCIe
# CONFIG=perf/benchmark/rtvi_vlm_config_rtx_pro.yaml  # RTX Pro SE
# CONFIG=perf/benchmark/rtvi_vlm_config_l40s.yaml     # L40S (set VLLM_GPU_MEMORY_UTILIZATION=0.45 in .env.perf)
# CONFIG=perf/benchmark/rtvi_vlm_config_jetson.yaml   # Jetson Thor
# CONFIG=perf/benchmark/rtvi_vlm_config_spark.yaml    # DGX Spark (GB10)
BENCH="python3 perf/benchmark/rtvi_perf_benchmark.py --config $CONFIG"
```

### Phase 1: BCD 1 — Maximum Streams per GPU

```bash
# Primary (448×448×80 — 8K Vision Tokens):
$BENCH --scenario max_live_streams_test_1_token_448    # 1-token OSL
$BENCH --scenario max_live_streams_test_100_token_448  # 100-token OSL

# Legacy (372×372×30 — 2K Vision Tokens):
$BENCH --scenario max_live_streams_test_1_token        # 1-token OSL
$BENCH --scenario max_live_streams_test_100_token      # 100-token OSL

# Optional CLI overrides (override YAML config values at runtime):
#   --initial-stream-count N        starting stream count  (see platform config for per-GPU defaults)
#   --add-stream-count N            streams added per step (H100/RTX PRO: 3, Jetson/Spark: 2)
#   --binary-search-refinement / --no-binary-search-refinement
# Example — fast ramp then Phase 2 refinement:
$BENCH --scenario max_live_streams_test_1_token_448 --initial-stream-count 10 --add-stream-count 5
```

**Binary Search Refinement (Phase 2)**

After Phase 1's linear ramp detects degradation at N_u streams with N_s last stable,
Phase 2 binary-searches between N_s and N_u to find the exact maximum.

Each probe is independent:
1. All active streams are deleted.
2. The server waits `binary_search_probe_cooldown_seconds` (default: 15s) to drain.
3. Exactly `mid = (low + high) // 2` streams are added fresh.
4. A stability check runs with full Phase 1 criteria:
   - Requires `required_stable_windows` consecutive stable windows to confirm stable.
   - Requires `max(required_unstable_windows, 3)` consecutive unstable windows to confirm unstable.
   - Requires fresh latency samples from at least `min_stable_stream_coverage` active streams.
   - Waits up to `fresh_latency_timeout_seconds` for fresh samples before low coverage counts as unstable.
5. On stable: `low = mid` and a latency snapshot is recorded.
   On unstable: `high = mid`.

Config:
```yaml
binary_search_probe_cooldown_seconds: 15   # seconds to wait after full stream deletion
binary_search_refinement: false            # set to disable Phase 2 entirely
binary_search_linear_extension: true       # after binary search, probe upward one stream at a time
binary_search_linear_extension_cap: 300    # report null unstable ceiling if this cap stays stable
```

Latency snapshots from binary search probes appear in the `Latency_Snapshots` XLS sheet
alongside Phase 1 snapshots, tagged with the probe's `stream_count`.

### Phase 2: BCD 2 — VLM E2E Latency (Stream Processing)

```bash
# Single-stream baseline (primary BCD 2 data point):
$BENCH --scenario single_live_stream_test_1_token      # 1-token OSL, 1 stream
$BENCH --scenario single_live_stream_test_100_token    # 100-token OSL, 1 stream

# Concurrency sweep at fixed stream counts [1, 10, 20] (additional BCD 2 data points):
$BENCH --scenario concurrency_test_1_token             # 1-token OSL
$BENCH --scenario concurrency_test_100_token           # 100-token OSL
```

### Phase 3: BCD 3 — Request Throughput (Non-Streaming)

```bash
# RPS across concurrency [1, 2, 4, 8, 16, 32, 64], 10 s video (1 chunk/request):
$BENCH --scenario file_burst_1_token    # 1-token OSL
$BENCH --scenario file_burst_100_token  # 100-token OSL
```

### Phase 4: BCD 4 — VLM E2E Request Latency (Non-Streaming)

```bash
# Concurrency [1,2,4,8,16,32] at 10 s; concurrency [1] at 10 min and 60 min:
$BENCH --scenario e2e_latency_1_token    # 1-token OSL
$BENCH --scenario e2e_latency_100_token  # 100-token OSL
```

### Platform-Specific Concurrency Levels

The default concurrency levels in `rtvi_vlm_config_test.yaml` are sized for high-end server
GPUs (H100, RTX Pro SE). **Jetson Thor** and **DGX Spark (GB10)** have lower GPU memory
and fewer SM cores, so the upper concurrency levels will saturate the GPU and inflate latency
beyond what is useful to measure. Use the `--concurrency-levels` flag to reduce the sweep
without editing the YAML.

| Platform | BCD 2 stream counts | BCD 3 concurrency | BCD 4 concurrency |
|----------|---------------------|-------------------|-------------------|
| H100 / RTX Pro SE / L40S (default) | 1, 10, 20 | 1, 2, 4, 8, 16, 32, 64 | 1, 2, 4, 8, 16, 32 |
| **Jetson Thor** | 1, 5, 10 | 1, 2, 4, 8 | 1, 2, 4, 8 |
| **DGX Spark (GB10)** | 1, 5, 10 | 1, 2, 4, 8 | 1, 2, 4, 8 |

```bash
# BCD 2 — Jetson Thor / DGX Spark
$BENCH --scenario concurrency_test_1_token    --concurrency-levels 1 5 10
$BENCH --scenario concurrency_test_100_token  --concurrency-levels 1 5 10

# BCD 3 — Jetson Thor / DGX Spark
$BENCH --scenario file_burst_1_token    --concurrency-levels 1 2 4 8
$BENCH --scenario file_burst_100_token  --concurrency-levels 1 2 4 8

# BCD 4 — Jetson Thor / DGX Spark
$BENCH --scenario e2e_latency_1_token    --concurrency-levels 1 2 4 8
$BENCH --scenario e2e_latency_100_token  --concurrency-levels 1 2 4 8
```

`--concurrency-levels` overrides `stream_count` for `concurrent_live_streams` scenarios and
`concurrency_levels` for `file_burst` / `concurrency` scenarios — all videos in the scenario
are affected. If a scenario has a different name, combine with `--scenario`.

---

## Metrics Reported

### Video Caption Generation Metrics

| Metric | Description | Unit |
|--------|-------------|------|
| `e2e_latency` | End-to-end latency (upload + processing) | seconds |
| `vlm_pipeline_latency` | VLM caption generation pipeline time | seconds |
| `decode_latency` | Video decode time | seconds |
| `throughput` | Videos processed per second | videos/sec |
| `captions_per_second` | Caption chunks generated per second | chunks/sec |
| `chunk_processing_time` | Time to process each video chunk | seconds |

### GPU Metrics (when monitoring enabled)

| Metric | Description |
|--------|-------------|
| GPU Utilization | Average GPU compute utilization % |
| Memory Utilization | Average GPU memory utilization % |
| NVDEC Utilization | Hardware decoder utilization % |
| Power Usage | Average power consumption (watts) |
| Temperature | Average GPU temperature (°C) |

### CPU and System Metrics (when Node Exporter enabled)

CPU and memory metrics are collected via Node Exporter when `node_exporter_enabled: true` is set in the configuration.

| Metric | Description |
|--------|-------------|
| `nodeexporter_cpu_util_mean` | Average CPU utilization across all cores (%) |
| `nodeexporter_cpu_util_p90` | 90th percentile CPU utilization (%) |
| `nodeexporter_memory_used_pct_mean` | Average memory usage percentage (%) |
| `nodeexporter_memory_used_pct_p90` | 90th percentile memory usage (%) |
| `nodeexporter_load1_mean` | Average 1-minute load average |
| `nodeexporter_load5_mean` | Average 5-minute load average |

**Prerequisites for CPU Metrics:**
- Node Exporter must be running and accessible at the configured URL (default: `http://localhost:9100/metrics`)
- For Docker deployments, ensure Node Exporter container is running and port 9100 is exposed
- Metrics are collected by directly scraping Node Exporter's `/metrics` endpoint

### Per-Request Metrics

| Metric | Description |
|--------|-------------|
| Mean Latency | Average request latency |
| Median Latency | 50th percentile latency |
| P90 Latency | 90th percentile latency |
| P95 Latency | 95th percentile latency |
| P99 Latency | 99th percentile latency |

### Live Stream Metrics

| Metric | Description |
|--------|-------------|
| Stream Stability | Percentage of successful chunk processing |
| Chunk Drop Rate | Percentage of dropped chunks |
| Max Concurrent Streams | Maximum stable stream count |
| Latency Threshold Violations | Count of latency threshold breaches |

---

## Output Files

Benchmark results are written to the output directory specified in config (`output_dir`):

| File | Description |
|------|-------------|
| `summary.yaml` | Overall benchmark summary |
| `<scenario>_results.yaml` | Per-scenario detailed results |
| `<scenario>_results.xlsx` | Excel report with charts |
| `gpu_metrics.csv` | GPU monitoring data (if enabled) |
| `detailed_metrics.json` | Raw per-request metrics |
| `stream_latencies.csv` | Live stream latency data |
| `node_exporter_gpu_metrics_*.json` | CPU and memory metrics from Node Exporter (if enabled) |
| `CPU_Per_Core` sheet | Per-core CPU utilization data in Excel reports (max live streams tests) |

### Sample Output Structure

```text
rtvi-vlm-perf-report/
├── summary.yaml
├── single_file_test_results.yaml
├── single_file_test_results.xlsx
├── single_live_stream_test_1_token_results.yaml
├── max_live_streams_test_1_token_results.yaml
├── gpu_metrics.csv
├── stream_latencies.csv
└── detailed_metrics.json
```

### Sample Results YAML

```yaml
test_scenario: single_live_stream_test_100_token
timestamp: "2026-02-12T10:00:00Z"
config:
  rtsp_url: rtsp://10.24.219.112:8554/file-stream
  backend: http://localhost:8010/v1
  chunk_duration: 10
  max_tokens: 100

results:
  iterations: 2
  duration_seconds: 180
  metrics:
    mean_latency: 8.34
    std_latency: 0.52
    p90_latency: 9.12
    p95_latency: 9.45
    p99_latency: 9.87
    chunks_processed: 36
    chunks_dropped: 0
    stream_stability: 100.0

  gpu_metrics:
    avg_utilization: 85.2
    avg_memory_utilization: 62.3
    avg_power: 312.5

  cpu_metrics:
    nodeexporter_cpu_util_mean: 45.3
    nodeexporter_cpu_util_p90: 67.8
    nodeexporter_memory_used_pct_mean: 56.2
    nodeexporter_memory_used_pct_p90: 62.1
    nodeexporter_load1_mean: 2.34
    nodeexporter_load5_mean: 2.12
```

---

## Monitoring and Observability

### Access Monitoring Dashboards

`compose.perf.yaml` always starts Prometheus, DCGM Exporter, and Node Exporter — no `--profile` flag needed:

| Service | URL | Description |
|---------|-----|-------------|
| Prometheus | http://localhost:9090 | Metrics storage and querying |
| DCGM Exporter | http://localhost:9400/metrics | GPU metrics |
| Node Exporter | http://localhost:9100/metrics | CPU and system metrics |

### View Real-Time Metrics

```bash
# Service metrics
curl http://localhost:8010/metrics

# GPU metrics without plain nvidia-smi polling
nvidia-smi dmon -s pucvmet -d 1

# Container logs (service is named rtvi-server in compose.perf.yaml)
docker compose -f docker/compose.perf.yaml logs -f rtvi-server

# Enable detailed profiling
# Set ENABLE_REQUEST_PROFILING=true in .env.perf and restart
```

---

## Environment Variables Reference

### Critical Performance Variables

| Variable | Recommended Values | Impact |
|----------|-------------------|--------|
| `NUM_VLM_PROCS` | 10, 20, 30 | More processes = higher concurrency |
| `NVIDIA_VISIBLE_DEVICES` | 0, "0,1", "all" | GPU allocation |
| `ENABLE_REQUEST_PROFILING` | true | Detailed per-request traces |
| `VLLM_IGNORE_EOS` | 1 | For N-token generation testing |
| `VSS_SKIP_INPUT_MEDIA_VERIFICATION` | 1 | Faster stream addition |
| `RTVI_VLM_GENERATE_CAPTIONS_ENDPOINT` | `/generate_captions_alerts` for legacy RT-VLM images | Overrides the max-live-streams caption endpoint; default is `/generate_captions` |
| `RTVI_RTSP_LATENCY` | `300` in perf compose | RTSP jitter buffer in **milliseconds**. BCD max-stream stability measures last-frame NTP timestamp to caption output by default, so this is kept low enough that buffer delay does not dominate. |
| `RTVI_RTPJITTERBUFFER_DROP_ON_LATENCY` | `false` in perf compose | Disables jitter-buffer packet dropping to enforce latency. Keep false for BCD no-drop max-stream runs. |
| `RTVI_RTPJITTERBUFFER_FASTSTART_MIN_PACKETS` | `2` in perf compose | Enables `rtpjitterbuffer.faststart-min-packets` for high-latency no-drop RTSP runs. Set `0` to disable. |
| `RTVI_EMPTY_CUDA_CACHE_ON_RESULT` | `false` in perf compose | Disables per-result `torch.cuda.empty_cache()` on the hot path; set true only for memory diagnostics |
| `VLLM_GPU_MEMORY_UTILIZATION` | 0.7 (default); **0.45 for L40S** | GPU memory fraction |
| `RTVI_LIVE_STREAM_GPU_MEMORY_GUARD` | `true` (default) | Rejects a new live generation request with HTTP 503 `ServerBusy` once visible free GPU memory reaches the configured watermark. A CUDA OOM encountered during stream setup is also rolled back and returned as `ServerBusy`. |
| `RTVI_LIVE_STREAM_GPU_MEMORY_HEADROOM_MB` | `1024` (default) | Hard free-memory watermark for new independent live streams. It is intentionally not combined with an inferred per-stream cost because allocator-retained memory is not a reliable prediction of the next stream allocation. |
| `VLLM_MAX_NUM_SEQS` | 1024 | Maximum sequences in batch |
| `VLLM_MAX_NUM_BATCHED_TOKENS` | 8192, 16384 | Cap token budget per scheduler step; tune for OOM vs throughput |
| `REDIS_PORT` | 6379 (default), 6380 | Change if port 6379 conflicts with another service |

### Model Configuration

| Variable | Example | Description |
|----------|---------|-------------|
| `VLM_MODEL_PRESET` | `cr3-nano-reasoner-fp8` or `cr3-nano-reasoner-nvfp4` | Optional `perf/setup_perf_env.sh` preset. The FP8 preset fills `VLM_MODEL_TO_USE=cosmos-reason3` and `MODEL_PATH=ngc:nim/nvidia/cosmos3-nano-reasoner:modelopt-fp8-final_format_fix`; the Blackwell NVFP4 preset fills `MODEL_PATH=ngc:nim/nvidia/cosmos3-nano-reasoner:modelopt-nvfp4-full-quantize-final_format_fix`. Explicit exports take precedence. |
| `VLM_MODEL_TO_USE` | `cosmos-reason3` | Model name |
| `MODEL_PATH` | `ngc:nim/nvidia/cosmos3-nano-reasoner:modelopt-fp8-final_format_fix` or `ngc:nim/nvidia/cosmos3-nano-reasoner:modelopt-nvfp4-full-quantize-final_format_fix` | Model path |
| `VLM_MAX_MODEL_LEN` | `32768` | Maximum model context length; raise only if the prompt/video token budget requires it |
| `NGC_API_KEY` | `nvapi-XXXXXX` | NGC API key for model download |

### VLLM Performance Tuning

| Variable | Recommended | Description |
|----------|-------------|-------------|
| `VLLM_ENABLE_PREFIX_CACHING` | false | Disable prefix caching for true perf measurement |
| `VLLM_DISABLE_MM_PREPROCESSOR_CACHE` | true | Disable MM preprocessor cache for true perf measurement |
| `VLLM_MM_TENSOR_IPC` | empty | Optional patched-vLLM tensor IPC path for multimodal tensors; set `torch_shm` only for experiments |
| `VLLM_MM_ENCODER_ATTN_BACKEND` | empty | Optional MM encoder attention backend override, e.g. `XFORMERS` |
| `VLLM_ATTENTION_BACKEND` | empty | Optional decoder attention backend override; CR3 Super NVFP4 on GB300 defaults to `TRITON_ATTN` |
| `VLLM_HAS_FLASHINFER_CUBIN` | 1 | FlashInfer optimizations |
| `VLLM_NUM_SCHEDULER_STEPS` | 8 | Scheduler steps |
| `VLLM_MAX_NUM_BATCHED_TOKENS` | (unset) | Maximum tokens per scheduler step; set to control memory vs throughput |

### EVS / EVS++ Configuration

EVS (Efficient Video Sampling) prunes redundant video tokens before inference. There are two modes: fixed-rate pruning, set with `VLM_VIDEO_PRUNING_RATE` alone, and session mode ("EVS++"), enabled with `VIA_EVS_SESSION=true`. Every variable below is read by `rtvi_vlm` only and is already wired through `compose.yaml` and `compose.perf.yaml`, so exported shell values override `.env.perf`. See `claude/EVS_REFERENCE.md` for the full reference.

| Variable | Default | Description |
|----------|---------|-------------|
| `VIA_EVS_SESSION` | unset (false) | Master switch for EVS++ session mode. Auto-enables absolute-timestamp metadata internally. Rejected on `NemotronH_Nano_Omni_Reasoning_V3`, where it raises `ValueError` at startup rather than falling back. |
| `VLM_VIDEO_PRUNING_RATE` | unset; **set `0.5` when EVS++ is enabled** | Pruning fraction, valid range `0 < r < 1`. Required for EVS++, because passing `video_pruning_rate` at engine init is what activates pruning inside vLLM and this variable is its only source. With session mode on but this unset, sessions are created and the handler still carries its internal `0.5`, yet the engine never prunes — the run pays EVS++ overhead with no token reduction. |
| `VLLM_EVS_SIMILARITY_THRESHOLD` | `0.4` | Cosine-dissimilarity threshold for pruning near-duplicate frames on the encode path (session mode). Server default only; the livestream caption APIs can override it per request or stream. |
| `VLLM_NUM_PREPROCESS_WORKERS` | `16` | Fixed vLLM frontend executor ceiling. Changing it requires an engine restart. |
| `RTVI_VLLM_ADAPTIVE_PREPROCESS` | `disabled` | GPU-memory-aware admission beneath the fixed vLLM executor ceiling. Supported presets are `disabled`, `shadow`, and `enforced`. Advanced tuning uses a JSON object in this same variable. |
| `VIA_EVS_TOKEN_BUDGET` | `1` | Max visual-token budget accumulated across clips before generation is forced. |
| `VIA_EVS_MAX_SESSIONS` | `256` | Max concurrent video sessions held by the handler. |
| `VLLM_IGNORE_EOS` | `false`; `true` in perf compose | EVS session generations run to `max_tokens` instead of stopping at EOS. Required for OSL/perf runs, otherwise EVS runs stop at the natural EOS and produce far fewer tokens than the non-EVS path. |

Use a named preset for normal deployment:

```bash
RTVI_VLLM_ADAPTIVE_PREPROCESS=enforced
```

Use the same variable with a strict JSON object only when tuning is required:

```bash
RTVI_VLLM_ADAPTIVE_PREPROCESS='{"mode":"enforced","max_workers":8,"gpu_headroom_mb":2048}'
```

Supported JSON fields are `mode`, `min_workers`, `max_workers`, `gpu_headroom_mb`,
`initial_estimated_request_mb`, `estimate_safety_factor`,
`admission_timeout_seconds`, `healthy_completions_for_increase`,
`scale_up_cooldown_seconds`, `scale_up_gpu_utilization_threshold_percent`,
`calibration_samples_required`, `estimate_ewma_alpha`, and
`poll_interval_seconds`. Unknown fields and invalid types fail at startup.
`max_workers` is capped at `VLLM_NUM_PREPROCESS_WORKERS`.

The enforced controller starts at `min_workers` and increases only when upstream
work has been blocked by the current limit, the learned request estimate leaves
an additional request of GPU-memory margin, and the scale-up cooldown has
elapsed. Scale-up is deferred while GPU utilization is at or above the default
90% threshold because additional preprocessing cannot relieve a saturated
inference engine. A memory-pressure event halves the effective limit and remains latched
until memory recovers for the configured number of healthy completions. The
default 30-second cooldown prevents short-lived free-memory rebounds from
rapidly restoring an unsafe worker count.

Adaptive admission classifies observed multimodal workloads by modality, tensor
shape and dtype, raw payload size, processor options, and bucketed prompt/output
lengths. It serially calibrates unseen workload classes from observed GPU memory,
then reserves memory atomically so concurrent requests cannot all admit from the
same free-memory snapshot. On pressure it halves the effective limit and recovers
additively after healthy completions. On the default RPC path, admission remains
held until the first EngineCore output proves that the visual encoder consumed the
request. The CUDA-IPC residency limit remains separate and is also released after
the first EngineCore output.

The model worker logs each effective-limit change. Controller OTEL instruments are
also registered in that process; deployments exposing metrics only from the API
server process should use the worker log until cross-process metric aggregation is
configured.

### File-Burst Asset Reuse

File-burst scenarios pre-upload a reusable `/files` pool by default. The pool size defaults to the tested concurrency level, so the measured steady-state window excludes `/files` upload/delete overhead while avoiding a single shared file ID becoming a server-side serialization point. Use these YAML fields only when you need to override the default:

```yaml
reuse_uploaded_files: true
preupload_file_pool_size: 128  # optional; default is the tested concurrency
```

---

## Troubleshooting

### Service Not Starting

```bash
# Check logs
docker compose -f docker/compose.perf.yaml logs rtvi-server

# Verify GPU activity without plain nvidia-smi polling
nvidia-smi dmon -s pucvmet -c 1

# Check container runtime
docker info | grep -i runtime

# Verify NGC API key
echo $NGC_API_KEY
```

### Low Stream Capacity

1. **Check the latency source first**: BCD max-stream runs use `RTVI_RTSP_LATENCY=300`. The default BCD metric is last-frame NTP timestamp to caption output, so backlog or real buffering delay is counted.
2. **Skip input verification**: Set `VSS_SKIP_INPUT_MEDIA_VERIFICATION=1`
3. **Increase worker processes**: Set `NUM_VLM_PROCS=20`
4. **Reduce token generation**: Use `max_tokens: 1` for capacity testing

### High Latency

1. **Reduce max tokens**: Set `max_tokens: 100` or lower
2. **Reduce input resolution**: Use smaller `vlm_input_width/height`
3. **Check GPU utilization**: Should be >70% during processing
4. **Verify NVDEC usage**: Check hardware decode is active
5. **Check cache mode**: For published benchmark measurements, keep `VLLM_ENABLE_PREFIX_CACHING=false` and `VLLM_DISABLE_MM_PREPROCESSOR_CACHE=true`; enable caches only for throughput experiments.

### Out of Memory Errors

1. **Keep admission protection enabled**: `RTVI_LIVE_STREAM_GPU_MEMORY_GUARD=true`. Live benchmarks classify its HTTP 503 response as `resource_bound`, clean up, and continue to later test cases.
2. **Reduce GPU memory utilization**: Try `VLLM_GPU_MEMORY_UTILIZATION=0.6`
3. **Reduce max sequences**: Lower `VLLM_MAX_NUM_SEQS=512`
4. **Reduce concurrent processes**: Lower `NUM_VLM_PROCS`
5. **Check max model length**: Keep `VLM_MAX_MODEL_LEN=32768` for apples-to-apples perf unless the request token budget requires a larger context.

### 422 Unprocessable Entity on `/v1/streams/add` — `RTSP_STREAM_URL` placeholder not replaced

**Symptom:**
```
ERROR - API call POST http://localhost:8010/v1/streams/add returned 422:
{'code': 'InvalidParameters', 'message': "String should match pattern '^rtsp://' (input: \"RTSP_STREAM_URL\")"}
```

**Cause:** The benchmark config (`rtvi_vlm_config_test.yaml`) still contains the literal
placeholder `RTSP_STREAM_URL` instead of a real `rtsp://` URL. This happens when:
- The config was pulled/reset from git after `setup_perf_env.sh` had already patched it, or
- `setup_perf_env.sh` was never run on this machine.

**Fix:** Tear down and re-run setup to regenerate the patched config:
```bash
bash perf/teardown_perf_env.sh && bash perf/setup_perf_env.sh
```

The setup script replaces `RTSP_STREAM_URL` with the live RTSP URL discovered from the VST API.

---

### nvstreamer / VST Fails or Stops Responding

nvstreamer and VST can occasionally fail to start, crash mid-run, or stop
serving RTSP streams. The recommended recovery is a clean teardown followed by
a fresh setup:

```bash
bash perf/teardown_perf_env.sh   # stops all services started by setup_perf_env.sh
bash perf/setup_perf_env.sh      # re-runs all 12 setup steps
```

The setup script is **idempotent** — tarballs and videos already on disk are
skipped, VST patches are not applied twice, and any existing containers are
stopped before new ones are started. A re-run is always safe.

If nvstreamer fails on the first attempt without a full teardown, re-running
the setup script alone is also sufficient:

```bash
bash perf/setup_perf_env.sh
```

The script will re-download nothing (tarballs and videos are cached), re-patch VST
(the patch markers prevent double-patching), stop any dangling containers, and retry
the full nvstreamer → VST → RTVI VLM startup sequence.

If nvstreamer fails repeatedly, check its logs before re-running:

```bash
# View nvstreamer container logs (container is typically named nvstreamer-1)
docker logs nvstreamer-1

# Check nvstreamer health endpoint directly
curl -v http://localhost:31000

# Verify the video files nvstreamer serves are present
ls -lh ~/rtvi-perf/vst_package/videos/

# Manually stop everything, then re-run the setup script
bash perf/teardown_perf_env.sh
bash perf/setup_perf_env.sh
```

If the VST stream poll times out (Step 9), increase the timeout and re-run:

```bash
export STREAM_POLL_TIMEOUT=600   # wait up to 10 minutes for streams
bash perf/setup_perf_env.sh
```

### Prometheus / Node Exporter / DCGM Exporter Fail to Start

#### Port already in use

The most common cause. The setup script detects this automatically and prints which
ports conflict before attempting to start. Fix by exporting an alternate port and
re-running:

```bash
# Check which ports are occupied
ss -tlnp | grep -E "9100|9400|9090"

# Override whichever port(s) are in use and re-run
export NODE_EXPORTER_PORT=9101   # default 9100
export DCGM_EXPORTER_PORT=9401   # default 9400
export PROMETHEUS_PORT=9091      # default 9090
bash perf/setup_perf_env.sh
```

The overridden ports are written into `.env.perf` automatically and picked up by
`compose.perf.yaml`.

#### Check container logs

```bash
# List all compose services and their status
docker compose -f docker/compose.perf.yaml ps

# View logs for each monitoring service
docker compose -f docker/compose.perf.yaml logs node-exporter
docker compose -f docker/compose.perf.yaml logs dcgm-exporter
docker compose -f docker/compose.perf.yaml logs prometheus
```

#### Verify metrics endpoints are reachable

```bash
# Node Exporter (CPU / memory / disk metrics)
curl -sf http://localhost:9100/metrics | head -5

# DCGM Exporter (GPU metrics)
curl -sf http://localhost:9400/metrics | head -5

# Prometheus (query UI)
curl -sf http://localhost:9090/-/healthy
```

If you used custom ports, replace 9100 / 9400 / 9090 with your overrides.

#### DCGM Exporter requires SYS_ADMIN capability

DCGM Exporter needs `cap_add: SYS_ADMIN` (already set in `compose.perf.yaml`).
If it fails with a permissions error, ensure the Docker daemon allows this capability
and that `--privileged` restrictions are not enforced by your Docker policy.

```bash
# Confirm SYS_ADMIN is present in the container
docker inspect deploy-dcgm-exporter-1 | grep -A5 CapAdd
```

#### Prometheus cannot scrape targets

```bash
# Open Prometheus targets page to see scrape status
# http://localhost:9090/targets

# Or query via API
curl -s http://localhost:9090/api/v1/targets | python3 -m json.tool | grep -E "health|lastError"
```

All three targets (`dcgm-exporter:9400`, `node-exporter:9100`, `localhost:9090`) should
show `"health": "up"`. If a target is down, check the corresponding container logs above.

### RTSP Stream Issues

```bash
# List streams available from VST
curl -sf http://localhost:30888/vst/api/v1/sensor/streams | \
    jq -r '.. | strings | select(startswith("rtsp://"))'

# Probe a VST live stream (replace with URL from above)
ffprobe "rtsp://<HOST_IP>/live/<stream_id>"

# Test playback
ffplay "rtsp://<HOST_IP>/live/<stream_id>"

# BCD latency uses last-frame NTP timestamp to caption output; keep the
# jitter buffer low enough that buffering does not dominate the metric.
RTVI_RTSP_LATENCY=300
RTVI_RTPJITTERBUFFER_DROP_ON_LATENCY=false
```

### NTP Clock Sync Issues

Latency measurements depend on accurate system time. If clocks are unsynchronised across
hosts, e2e latency values will be skewed. Verify and fix NTP sync before running benchmarks.

#### Check current sync status

```bash
# Overall sync status and estimated error
chronyc tracking

# List configured sources and their reach/offset
chronyc sources -v
```

`System time` offset in `chronyc tracking` output should be well under 1 ms for reliable
latency measurements. If it is in the seconds range, force an immediate correction.

#### Force immediate time step correction

```bash
# Apply a one-time step correction regardless of offset size
sudo chronyc makestep

# Verify offset is now small
chronyc tracking
```

`makestep` jumps the clock instantly instead of slewing gradually — safe to run once
before a benchmark run to remove large accumulated drift.

#### Add NTP sources with iburst for faster sync

If the default sources are unreachable or slow to converge, add reliable sources with
`iburst` mode (sends a burst of packets on first contact for faster initial sync):

```bash
sudo nano /etc/chrony/chrony.conf
```

Add at the top of the file (before any existing `pool` or `server` lines):

```text
server time.google.com iburst
server time.cloudflare.com iburst
server ntp.ubuntu.com iburst
```

Then restart chrony and verify:

```bash
sudo systemctl restart chrony
chronyc sources -v    # wait ~30 s for sources to appear as reachable (*)
chronyc tracking      # confirm System time offset is small
```

Sources marked `*` in `chronyc sources` output are currently selected; `+` are acceptable
alternatives. If no source shows `*` after 60 s, check firewall rules — UDP port 123 must
be open outbound.

#### Persistent makestep on large offsets

To automatically step the clock on startup when the offset exceeds 1 second (common after
a long powered-off period), add to `/etc/chrony/chrony.conf`:

```text
makestep 1.0 3
```

This allows up to 3 step corrections during the first 3 clock updates; after that, chrony
slews normally. Restart chrony after editing:

```bash
sudo systemctl restart chrony
```

### Benchmark Script Errors

```bash
# Verify Python environment
source ~/rtvi-vlm-perf-env/bin/activate
pip list | grep -E "requests|pyyaml|tqdm"

# Verify backend connectivity
curl http://localhost:8010/v1/health/ready

# Check video file paths in config (perf videos are mounted at /perf/)
ls -la /opt/nvidia/rtvi/streams/perf/

# Enable debug logging
python3 perf/benchmark/rtvi_perf_benchmark.py \
  --config config.yaml \
  --scenario test_name \
  --debug
```

---

## Best Practices

### For Latency Benchmarking (BCD 2, BCD 4)

- Use **single file mode** or **single live stream mode** with high iteration count (5+)
- **Disable GPU monitoring** to avoid measurement interference — set `gpu_monitoring.enabled: false` in `rtvi_vlm_config_test.yaml`
- Use **consistent video input** across runs
- Run **warmup iterations** before measurement
- Test with **production-representative chunk sizes** (10-30 seconds)
- Use **fixed token count** for consistent measurements

### For Throughput Benchmarking (BCD 3)

- Use **file burst mode** or **concurrency mode**
- Set **target latency threshold** appropriate for use case
- Monitor **GPU utilization** to identify bottlenecks
- Test with **realistic video sizes and codecs**
- Use **H.264/H.265** for hardware decode acceleration

### For Stream Capacity Testing (BCD 1)

- Use **max live streams mode**
- Keep `RTVI_RTSP_LATENCY=300` for BCD max-stream runs and use last-frame NTP timestamp to caption output for the threshold.
- Enable **skip input verification**: `VSS_SKIP_INPUT_MEDIA_VERIFICATION=1`
- Set **VLLM_IGNORE_EOS=true** for N-token testing
- Monitor **chunk-ID drop gaps** and **latency threshold violations**
- Start with **low initial stream count** (3-5)
- Use **stability check interval**: 45-60 seconds

### General Best Practices

- **Document test environment**: GPU model, driver version, system specs
- **Use version-controlled configs**: Track changes to test scenarios
- **Archive results**: Keep historical data for regression analysis
- **Run multiple iterations**: Statistical significance requires N ≥ 5
- **Control variables**: Change one parameter at a time
- **Use consistent RTSP streams**: Loop same video for reproducibility

---

## Notes

- **Warmup**: Always run warmup iterations to exclude model loading and cache population overhead
- **GPU Monitoring Overhead**: GPU monitoring adds ~1-2% latency overhead - run separate from peak throughput tests
- **RTSP Stability**: For live stream tests, ensure RTSP server can handle target stream count with continuous looping
- **Video Codecs**: H.264/H.265 videos use NVDEC hardware decode; other codecs may use CPU
- **Chunk Duration**: Shorter chunks = more frequent captions but higher overhead. Recommended: 10-30 seconds
- **Token Budget**: 1-token tests are for Yes/No alerts; 100-token tests are for descriptive captions
- **Packet Dropping**: Perf compose uses `RTVI_RTSP_LATENCY=300` and `RTVI_RTPJITTERBUFFER_DROP_ON_LATENCY=false`; the benchmark still fails stream counts with chunk-ID drop gaps.
- **EOS Handling**: Set `VLLM_IGNORE_EOS=true` to force exactly N-token generation for consistent benchmarking
- **Input Verification**: Skip with `VSS_SKIP_INPUT_MEDIA_VERIFICATION=1` for faster stream addition in capacity tests
