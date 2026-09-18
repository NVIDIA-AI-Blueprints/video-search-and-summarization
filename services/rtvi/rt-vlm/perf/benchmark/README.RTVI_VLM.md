# RTVI Performance Benchmark Tool Instructions for Perf evaluation

Performance benchmarking framework for RTVI VLM services.

---

## Quick Start (BCD benchmarks)

> **Recommended:** Use `perf/setup_perf_env.sh` (see
> [PERF_GUIDE.RTVI_VLM.md](PERF_GUIDE.RTVI_VLM.md)) which automates all
> steps below end-to-end — VST download, nvstreamer/VST startup, `.env.perf`
> generation, RTVI VLM deployment, Python venv creation, and RTSP URL
> injection.

### Step 1 — Run the environment setup script

The script downloads VST + benchmark videos, starts nvstreamer/VST, creates a
Python venv, and injects the live RTSP URL into the benchmark config. It reads
the service-root `.env` and existing `docker/.env.perf` as defaults
before validation, including `export KEY=value` lines. `.env.perf` can override
`.env`; exported shell variables override both files.
It always regenerates benchmark cache settings as `VLLM_ENABLE_PREFIX_CACHING=false`
and `VLLM_DISABLE_MM_PREPROCESSOR_CACHE=true` unless those keys are explicitly
exported in the shell for a non-standard experiment.

```bash
export ARTIFACTORY_USER=<your-username>  # required when VST must download
export ARTIFACTORY_TOKEN=<your-api-token>

# Optional overrides (defaults shown):
# export REDIS_PORT=6379          # change if 6379 is already in use
# export VST_LOCAL_PACKAGE=perf/vst_package.tar.gz
# export VST_IMAGE_TAG=2.1.0-26.04.1
# export PERF_VIDEOS_DIR=~/rtvi-perf/vst_package/videos
# export VLM_MODEL_PRESET=cr3-nano-reasoner-fp8
# export VLM_MODEL_PRESET=cr3-nano-reasoner-nvfp4  # Blackwell platforms
# export MODEL_PATH=ngc:nim/nvidia/cosmos-reason2-8b:0303-fp8-dynamic-kv8

bash perf/setup_perf_env.sh
```

If `perf/vst_package.tar.gz` exists, the setup script uses that checked-in
package instead of downloading VST from Artifactory. Set `VST_LOCAL_PACKAGE` to
point at a different local tarball. Artifactory credentials are only required
when the VST package must be downloaded.

By default, the setup script patches the extracted VST package to use
`nvcr.io/rxczgrvsg8nx/vst-dev` images tagged `2.1.0-26.04.1` for
`vst-streamprocessing`, `vst-sensor`, `vst-ingress`, and `nvstreamer`. Override
`VST_IMAGE_REGISTRY`, `VST_IMAGE_TAG`, or the per-image variables printed by
`bash perf/setup_perf_env.sh -h` when testing a different VST build.

The script prints a summary at the end with the RTSP URL and the value
of `PERF_VIDEOS_DIR` — note both for the next step.

### Platform selection

Use the same setup script on all platforms. Select the GPU with
`NVIDIA_VISIBLE_DEVICES`, choose the benchmark config for the target platform,
then run scenarios through `rtvi_perf_benchmark.py`.

| Platform | Config | Notes |
| --- | --- | --- |
| BCD 3.2 dGPU profile | `perf/benchmark/rtvi_vlm_bcd_3_2_config.yaml` | Use this for official BCD 3.2 VLM runs. It covers 640x640 input, 10/20/40 frame tiers, OSL 1/100, and dGPU sweeps `[1, 16, 32, 64, 128]`. |
| H100 / H100-NVL / H100-PCIe | `perf/benchmark/rtvi_vlm_config_h100.yaml` | Legacy platform-tuned config for H100 max-stream and sample perf scenarios. |
| RTX PRO / RTX 6000 Ada | `perf/benchmark/rtvi_vlm_config_rtx_pro.yaml` | Legacy platform-tuned config with lower stream counts than H100. |
| L40S | `perf/benchmark/rtvi_vlm_config_l40s.yaml` | Set `VLLM_GPU_MEMORY_UTILIZATION=0.45` before setup, or in `.env.perf`, to leave enough room for decode and service memory. |
| Jetson Thor | `perf/benchmark/rtvi_vlm_config_jetson.yaml` | Edge config with smaller stream/concurrency sweeps. For BCD 3.2 `concurrency_test_*` scenarios, add `--concurrency-levels 1 8 16`. |
| DGX Spark | `perf/benchmark/rtvi_vlm_config_spark.yaml` | Edge config with smaller stream/concurrency sweeps. For BCD 3.2 `concurrency_test_*` scenarios, add `--concurrency-levels 1 8 16`. |

Common run pattern:

```bash
export NVIDIA_VISIBLE_DEVICES=0
# L40S only:
# export VLLM_GPU_MEMORY_UTILIZATION=0.45

bash perf/setup_perf_env.sh
source ~/rtvi-vlm-perf-env/bin/activate

CONFIG=perf/benchmark/rtvi_vlm_bcd_3_2_config.yaml
# CONFIG=perf/benchmark/rtvi_vlm_config_h100.yaml
# CONFIG=perf/benchmark/rtvi_vlm_config_rtx_pro.yaml
# CONFIG=perf/benchmark/rtvi_vlm_config_l40s.yaml
# CONFIG=perf/benchmark/rtvi_vlm_config_jetson.yaml
# CONFIG=perf/benchmark/rtvi_vlm_config_spark.yaml

BENCH="python3 perf/benchmark/rtvi_perf_benchmark.py --config $CONFIG"
$BENCH --scenario file_burst_1_token_2k
```

### Changing the model

Set `VLM_MODEL_PRESET` or `MODEL_PATH` before running `perf/setup_perf_env.sh`.
The script writes the resolved model settings into
`docker/.env.perf` and restarts the RTVI service with that
model.

```bash
# Choose one preset or explicit MODEL_PATH.

# CR3 Nano Reasoner FP8:
export VLM_MODEL_PRESET=cr3-nano-reasoner-fp8
# Equivalent explicit values:
# export VLM_MODEL_TO_USE=cosmos-reason3
# export MODEL_PATH=ngc:nim/nvidia/cosmos3-nano-reasoner:modelopt-fp8-final_format_fix

# CR3 Nano Reasoner NVFP4 for Blackwell platforms:
# export VLM_MODEL_PRESET=cr3-nano-reasoner-nvfp4
# Equivalent explicit values:
# export VLM_MODEL_TO_USE=cosmos-reason3
# export MODEL_PATH=ngc:nim/nvidia/cosmos3-nano-reasoner:modelopt-nvfp4-full-quantize-final_format_fix

# Default BCD setup model:
export MODEL_PATH=ngc:nim/nvidia/cosmos-reason2-8b:0303-fp8-static-kv8

# CR2 FP8 dynamic KV8:
# export MODEL_PATH=ngc:nim/nvidia/cosmos-reason2-8b:0303-fp8-dynamic-kv8

# CR2 NVFP4 dynamic KV8:
# export MODEL_PATH=ngc:nim/nvidia/cosmos-reason2-8b:0303-fp4-dynamic-kv8

# For CR2 paths:
# export VLM_MODEL_TO_USE=cosmos-reason2
bash perf/setup_perf_env.sh
```

For NVFP4 runs, leave `VLLM_ENFORCE_EAGER` unset so `compose.perf.yaml` uses its
default `false` value. Do not add an eager-mode override for standard BCD perf
runs; it changes the vLLM execution path and makes the result non-comparable to
the normal CUDA graph/compile path.

If setup has already run, either rerun `perf/setup_perf_env.sh` with the new
`MODEL_PATH`, or edit `docker/.env.perf` and restart:

```bash
cd docker
docker compose -f compose.perf.yaml --env-file .env.perf up -d --force-recreate rtvi-server
```

### Step 2 — Create `.env.perf`

> **Note:** `setup_perf_env.sh` auto-generates this file. Only follow this
> step for manual deployment.

Create `docker/.env.perf`:

```bash
# Required
BACKEND_PORT=8010
NVIDIA_VISIBLE_DEVICES=0                   # GPU index(es)
NGC_API_KEY=nvapi-XXXXXX
VLM_MODEL_TO_USE=cosmos-reason2
MODEL_PATH=ngc:nim/nvidia/cosmos-reason2-8b:0303-fp8-static-kv8
# Optional image override; defaults to GHCR develop-latest
# RTVI_IMAGE=ghcr.io/nvidia-ai-blueprints/vss/vss-rt-vlm:develop-latest
# DGX Spark is detected automatically and defaults to develop-latest-sbsa.
# For CR3 Nano Reasoner FP8, use:
# VLM_MODEL_TO_USE=cosmos-reason3
# MODEL_PATH=ngc:nim/nvidia/cosmos3-nano-reasoner:modelopt-fp8-final_format_fix
# For CR3 Nano Reasoner NVFP4 on Blackwell platforms, use:
# VLM_MODEL_TO_USE=cosmos-reason3
# MODEL_PATH=ngc:nim/nvidia/cosmos3-nano-reasoner:modelopt-nvfp4-full-quantize-final_format_fix

# Perf videos — set to the PERF_VIDEOS_DIR value printed by setup_perf_env.sh
PERF_VIDEOS_DIR=~/rtvi-perf/vst_package/videos

# BCD benchmark required settings
VLLM_IGNORE_EOS=true
VSS_SKIP_INPUT_MEDIA_VERIFICATION=1
VLLM_ENABLE_PREFIX_CACHING=false
VLLM_DISABLE_MM_PREPROCESSOR_CACHE=true
VLLM_MM_TENSOR_IPC=                     # optional patched-vLLM tensor IPC experiment
VLLM_MM_ENCODER_ATTN_BACKEND=
VLLM_ATTENTION_BACKEND=                  # CR3 Super NVFP4 on GB300 defaults to TRITON_ATTN

# BCD max-stream no-drop RTSP buffer in milliseconds. BCD latency uses the
# last-frame NTP timestamp to caption output, so real buffering delay counts.
RTVI_RTSP_LATENCY=300
RTVI_RTPJITTERBUFFER_DROP_ON_LATENCY=false
RTVI_RTPJITTERBUFFER_FASTSTART_MIN_PACKETS=2
RTVI_EMPTY_CUDA_CACHE_ON_RESULT=false

# VLLM_MAX_NUM_BATCHED_TOKENS=    # tune for throughput

# EVS++ (optional, off by default). Uncomment to benchmark session mode.
# All EVS variables are already passed through by compose.perf.yaml.
# See "EVS / EVS++ Configuration" in PERF_GUIDE.RTVI_VLM.md for the full list.
# VIA_EVS_SESSION=true
# VLM_VIDEO_PRUNING_RATE=0.5          # required: activates pruning in vLLM
# VLLM_EVS_SIMILARITY_THRESHOLD=0.4
```

### Step 3 — Deploy the VLM service + monitoring stack

```bash
cd docker
docker compose -f compose.perf.yaml --env-file .env.perf up -d

# Wait for the service to be ready (may take a few minutes for model load)
watch curl -s http://localhost:8010/v1/health/ready
```

`compose.perf.yaml` starts **RTVI VLM + DCGM Exporter + Node Exporter + Prometheus** (no Kafka/Redis/Jaeger).
- Prometheus UI: http://localhost:9090
- DCGM metrics: http://localhost:9400/metrics

### Step 4 — Activate the Python environment

```bash
source ~/rtvi-vlm-perf-env/bin/activate
cd <repo-root>
```

### Step 5 — Run BCD benchmark scenarios

Use `perf/benchmark/rtvi_vlm_bcd_3_2_config.yaml` for VSS 3.2 BCD runs. This
profile uses 640x640 model input and fixed frame-count sweeps of 10, 20, and 40
frames per 10 second chunk for approximately 2K, 4K, and 8K vision tokens.
For BCD 3 non-streaming throughput and latency, setup fetches the public LVS
warehouse source from NGC and derives the required 1080p, 10 FPS clips:

```bash
bash perf/setup_perf_env.sh
```

Setup reuses the LVS `fetch-videos.sh` script and converts its
`warehouse_10min.mp4` source into `FPS10_Res1080p_Dur10sec_1.mp4`,
`warehouse_gopro_10m_10fps.mp4`, and `warehouse_gopro_60m_10fps.mp4`; the
one-hour clip loops the source. The older
`warehouse_gopro_10s.mp4`, `warehouse_gopro_10m.mp4`, and
`warehouse_gopro_60m.mp4` assets are legacy 29.97 FPS clips and should not be
used for BCD 3 10 FPS results.

If same-named local files already exist and should be regenerated, run setup
with:

```bash
REFRESH_BCD_VIDEOS=true bash perf/setup_perf_env.sh
```

```bash
CONFIG=perf/benchmark/rtvi_vlm_bcd_3_2_config.yaml
BENCH="python3 perf/benchmark/rtvi_perf_benchmark.py --config $CONFIG"

# ── BCD 1: Maximum streams per GPU [Stream Processing] ──────────────────────
$BENCH --scenario max_live_streams_test_1_token_2k
$BENCH --scenario max_live_streams_test_100_token_2k
$BENCH --scenario max_live_streams_test_1_token_4k
$BENCH --scenario max_live_streams_test_100_token_4k
$BENCH --scenario max_live_streams_test_1_token_8k
$BENCH --scenario max_live_streams_test_100_token_8k

# ── BCD 2: VLM E2E Latency [Stream Processing] ──────────────────────────────
# dGPU stream counts are [1,16,32,64,128]. For AGX Thor / DGX Spark, add:
#   --concurrency-levels 1 8 16
$BENCH --scenario concurrency_test_1_token_2k
$BENCH --scenario concurrency_test_100_token_2k
$BENCH --scenario concurrency_test_1_token_4k
$BENCH --scenario concurrency_test_100_token_4k
$BENCH --scenario concurrency_test_1_token_8k
$BENCH --scenario concurrency_test_100_token_8k

# ── BCD 3: Request Throughput [Non-Streaming] ───────────────────────────────
# RPS across concurrency [1,16,32,64,128], 10 s video, 5 minute steady-state window.
$BENCH --scenario file_burst_1_token_2k
$BENCH --scenario file_burst_100_token_2k
$BENCH --scenario file_burst_1_token_4k
$BENCH --scenario file_burst_100_token_4k
$BENCH --scenario file_burst_1_token_8k
$BENCH --scenario file_burst_100_token_8k

# ── BCD 4: VLM E2E Request Latency [Non-Streaming] ──────────────────────────
# concurrency [1] at 10 s, 600 s, and 3600 s for each vision-token tier.
$BENCH --scenario e2e_latency_1_token_2k
$BENCH --scenario e2e_latency_100_token_2k
$BENCH --scenario e2e_latency_1_token_4k
$BENCH --scenario e2e_latency_100_token_4k
$BENCH --scenario e2e_latency_1_token_8k
$BENCH --scenario e2e_latency_100_token_8k
```

Results are written to `rtvi-vlm-bcd-3-2-report/` from the repo root.

### Step 6 — View results

```bash
ls perf/benchmark/rtvi-vlm-perf-report/

# Prometheus for GPU / CPU / memory metrics during the run:
# http://localhost:9090
```

---

## Prerequisites

Before running benchmarks, you need to have RTVI deployed and running.

### Deploy RTVI

Follow the official RTVI documentation to deploy locally:

Refer to [README.RTVI_VLM.release.md](../../README.RTVI_VLM.release.md)

1. **Deploy RTVI with Request Profiling enabled:**
   Set the `ENABLE_REQUEST_PROFILING` environment variable to `true` during deployment.

2. **Set the backend URL:**
   ```bash
   export RTVI_BACKEND=http://localhost:<port>
   ```
   Replace `<port>` with the actual port from your deployment (typically 8000 for local, or the NodePort/LoadBalancer port for Helm).

3. **Configure GPU assignment:**
   Edit your benchmark configuration file (e.g., `rtvi_vlm_config.yaml`) to specify which GPUs are assigned to VLM workloads:
   ```yaml
     vlm_gpus: [0]  # GPU indices for VLM processing
   ```
   Adjust the GPU indices based on your system's available GPUs. The benchmark will monitor these GPUs during test execution.

## Python Environment Setup (Recommended)

It's recommended to use a Python virtual environment to isolate dependencies:

```bash
# Create a virtual environment
python3 -m venv rtvi-perf-env

# Activate the virtual environment
source rtvi-perf-env/bin/activate

# Install required dependencies
pip install -r requirements.txt

# Sync host system with NTP
sudo apt-get install sntp
sudo sntp -S pool.ntp.org

# Alternatively use chrony
sudo apt install chrony

sudo nano /etc/chrony.conf
# or
# sudo nano /etc/chrony/chrony.conf

#Add the following in the file chrony.conf and save
pool 2.pool.ntp.org iburst
driftfile /var/lib/chrony/drift
makestep 1.0 3
rtcsync
logdir /var/log/chrony

# Enable and start the service
sudo systemctl enable chrony
sudo systemctl start chrony



# To deactivate the virtual environment when done:
deactivate
```

This benchmark suite provides comprehensive performance testing across six core operational modes:

## Benchmarking Modes

### 1. Single File Mode (`single_file`) Post EA2

This mode tests the complete video processing workflow from file upload through captions generation. Videos are uploaded via the `/v1/files` API, processed using the `/v1/generate_captions` API with configurable chunking. Each test case is repeated multiple times to enable statistical analysis with mean and standard deviation calculations. The benchmark tracks comprehensive metrics including E2E latency, VLM pipeline latency, decode latency, and GPU utilization across VLM GPUs and NVDEC.

**Mode-specific parameters:**
- `iterations`: Number of times to repeat the test (optional, default: 3)
- `videos`: List of video configurations
  - `filepath`: Path to video file (required)
  - `chunk_sizes`: List of chunk durations to test (required)
  - `prompt`: Override global prompt (optional)

**Example configuration:**
```yaml
single_file_test:
  description: "Single file processing"
  benchmark_mode: "single_file"
  iterations: 2
  videos:
    - filepath: "/opt/nvidia/rtvi/streams/its.mp4"
      chunk_sizes: [10, 15]
      prompt: "Monitor traffic events, violations, and vehicle types."
    - filepath: "/opt/nvidia/rtvi/streams/warehouse.mp4"
      chunk_sizes: [10]
      prompt: "Describe warehouse events and look for any anomalies."
```

### 2. File Burst Mode (`file_burst`) Post EA2

This mode evaluates concurrent video processing throughput by uploading and generating captions for multiple videos simultaneously at different concurrency levels. The benchmark launches N concurrent requests (e.g., 1, 2, 4, 8, 16) to the `/v1/generate_captions` API for pure throughput testing, measuring how the system handles parallel workloads. It performs an automatic binary search to find the optimal concurrency level that achieves a target average latency (default 60 seconds). Metrics tracked include E2E latency for all concurrent requests, per-request average and P90 latencies, throughput in files per second, GPU usage statistics, and the optimal concurrency point.

**Mode-specific parameters:**
- `videos`: List of video configurations
  - `filepath`: Path to video file (required)
  - `chunk_sizes`: List of chunk durations to test (required)
  - `concurrency_levels`: List of concurrent request counts to test (required)
  - `steady_state_duration_seconds`: Optional duration for each concurrency level. When set, the benchmark repeats waves of `concurrency_level` requests until this many seconds has elapsed, then reports throughput over the full elapsed window.
  - `target_latency_seconds`: Target average latency (optional, default: 60.0)
  - `target_latency_tolerance`: Tolerance in seconds (optional, default: 5.0)

**Example configuration:**
```yaml
file_burst_test:
  description: "Test different concurrency levels and measure latency statistics"
  benchmark_mode: "file_burst"
  videos:
    - filepath: "/opt/nvidia/rtvi/streams/its.mp4"
      chunk_sizes: [10]
      concurrency_levels: [1, 2, 4, 8]
      prompt: "Monitor traffic events, violations, and vehicle types."
    - filepath: "/opt/nvidia/rtvi/streams/bridge.mp4"
      chunk_sizes: [10]
      concurrency_levels: [1, 2, 4]
      prompt: "Describe the condition of the bridge infrastructure."
```

### 3. Max Live Streams Mode (`max_live_streams`)

This mode determines the maximum number of concurrent live streams the system can sustain without performance degradation or dropped chunks. The benchmark starts with a configured number of initial streams and gradually adds more streams while continuously monitoring caption generation latencies via streaming SSE responses from the configured VLM API (`/generate_captions` or `/chat/completions`). When the P95 latency exceeds the configured threshold, fresh stream coverage is too low, or chunk IDs skip, the system detects degradation and enters a stability verification phase where it systematically reduces streams to find the stable operating point. For RTVI VLM, latency tracking uses `media_info.end_timestamp` (last frame NTP timestamp) to caption SSE receive time by default.

**Mode-specific parameters:**
- `videos`: List of video configurations
  - `rtsp_url`: RTSP URL for live stream (required)
  - `chunk_sizes`: List of chunk durations to test (required)
  - `latency_threshold_seconds`: Performance degradation threshold in seconds (required)
  - `name`: Identifier name for the stream (optional, default: "live_stream")
  - `initial_stream_count`: Starting number of streams (optional, default: 5)
  - `chunk_overlap_duration`: Overlap between chunks in seconds (optional)

**Example configuration:**
```yaml
max_live_streams_test:
  description: "Test maximum concurrent live streams without performance degradation"
  benchmark_mode: "max_live_streams"
  generate_captions_params:
    vlm_input_width: 448
    vlm_input_height: 448
  videos:
    - name: "traffic_cam_1"
      rtsp_url: "rtsp://localhost:8554/traffic/cam1"
      chunk_sizes: [10]
      latency_threshold_seconds: 12
      initial_stream_count: 3
      chunk_overlap_duration: 2
      prompts:
        caption: "Monitor traffic events, violations, and vehicle types."
    - name: "warehouse_cam_1"
      rtsp_url: "rtsp://localhost:8554/warehouse/cam1"
      chunk_sizes: [15]
      latency_threshold_seconds: 20
      initial_stream_count: 2
      prompts:
        caption: "Describe warehouse events and look for any anomalies."
```

### 4. VLM Captions Burst Mode (`concurrency`)

This mode tests the `/v1/generate_captions` API under concurrent load by generating VLM captions for multiple videos simultaneously at various concurrency levels. The benchmark uploads multiple videos and requests VLM captions concurrently, testing different concurrency levels and automatically finding the optimal concurrency for a target average latency using binary search. Metrics include per-request latency statistics, throughput, GPU utilization, and the relationship between concurrency and response times.

**Mode-specific parameters:**
- `videos`: List of video configurations
  - `filepath`: Path to video file (required)
  - `chunk_sizes`: List of chunk durations to test (required)
  - `concurrency_levels`: List of concurrent request counts to test (required)
- `target_latency_seconds`: Target latency for optimal search (optional, default: 60.0)
- `target_latency_tolerance`: Tolerance in seconds (optional, default: 5.0)

**Example configuration:**
```yaml
concurrency
  description: "Test VLM caption generation performance"
  benchmark_mode: "concurrency"
  videos:
    - filepath: "/opt/nvidia/rtvi/streams/its.mp4"
      chunk_sizes: [10]
      concurrency_levels: [1, 2, 4, 8]
      target_latency_seconds: 60.0
    - filepath: "/opt/nvidia/rtvi/streams/warehouse.mp4"
      chunk_sizes: [10]
      concurrency_levels: [1, 2, 4]
      generate_captions_params:
        temperature: 0.5
        max_tokens: 100
        prompt: "Describe the key events in this video with timestamps."
        system_prompt: "You are a video analysis assistant."
```

## Basic Commands

```bash

# List available modes and scenarios
python rtvi_perf_benchmark.py --config rtvi_sample_config.yaml --list-modes
python rtvi_perf_benchmark.py --config rtvi_sample_config.yaml --list-scenarios

# Export  RTVI backend IP:Port
export RTVI_BACKEND=http://localhost:<port>

# Run scenarios using RTVI sample videos config
python rtvi_perf_benchmark.py --config rtvi_sample_config.yaml --scenario single_file_test
python rtvi_perf_benchmark.py --config rtvi_sample_config.yaml --scenario file_burst_test
python rtvi_perf_benchmark.py --config rtvi_sample_config.yaml --scenario max_live_streams_test
python rtvi_perf_benchmark.py --config rtvi_sample_config.yaml --scenario concurrency_test

# Quick test with RTVI sample videos
python rtvi_perf_benchmark.py --config rtvi_sample_config.yaml --scenario quick_test

# Run all scenarios
python rtvi_perf_benchmark.py --config rtvi_sample_config.yaml

# Enable debug logging
python rtvi_perf_benchmark.py --config rtvi_sample_config.yaml --scenario test_name --debug
```

## Configuration File Structure

The YAML configuration file has two main sections:

**Global settings** (`global`):
- `rtvi_backend`: RTVI API endpoint URL
- `output_dir`: Directory for test results
- `vlm_gpus`: GPU assignments for VLM workloads
- `gpu_monitoring`: GPU monitoring settings (enabled, interval, export options)
- `vlm_api_mode`: Optional VLM benchmark API selector. Use `generate_captions` (default) or `chat_completions`.
- `chat_completions_params`: Optional generation parameter overrides for `/v1/chat/completions`; defaults inherit from `generate_captions_params`.
- `chat_messages`: Optional explicit OpenAI-format messages for chat-completion benchmarks. If omitted, the benchmark converts `system_prompt` and `prompt` into system/user messages.
- `generate_captions_endpoint`: Optional max-live-streams endpoint override; use `/generate_captions_alerts` for legacy RT-VLM images that do not expose `/generate_captions`
- `chat_completions_endpoint`: Optional chat endpoint override; default is `/chat/completions`
- `generate_captions_params`: Parameters for `/v1/generate_captions` API (temperature, max_tokens, enable_audio, etc.)
- `prompt`: Default prompt for caption generation
- `require_no_dropped_chunks`: Optional max-live-streams stability requirement; default `true`. Any chunk-ID gap in a stability window marks that window unstable.
- `latency_measurement_source`: Optional latency source; default `ntp_timestamp` for BCD. Set `processing_latency` only for server-processing diagnostics.
- `binary_search_linear_extension`: Optional max-live-streams Phase 2 refinement toggle. When enabled, the benchmark probes upward one stream at a time after binary search; if the cap stays stable, `phase2_unstable_ceiling` is reported as `null`.

**Test scenarios** (`test_scenarios`):
Each scenario can override global API parameters at the scenario level. Individual videos can further override parameters at the video level. The configuration follows a 4-level merge: defaults.yaml → global → scenario → video, where each level overrides the previous while preserving unspecified parameters.

To benchmark the OpenAI-compatible chat API instead of `/v1/generate_captions`, set:

```yaml
global:
  vlm_api_mode: chat_completions
  chat_completions_params:
    temperature: 0.5
    max_tokens: 1
  chat_messages:
    - role: system
      content: You are a concise video safety assistant.
    - role: user
      content: Does this video contain a safety incident? Answer yes or no.
```

`vlm_api_mode` can also be set on a scenario or individual video. Existing
`generate_captions_params` blocks are reused when `chat_completions_params` is
not provided, so one scenario can switch APIs without duplicating all generation
settings.


## BCD Test Scenarios
Use [rtvi_vlm_bcd_3_2_config.yaml](rtvi_vlm_bcd_3_2_config.yaml) for BCD 3.2
results. The quickstart above lists the exact commands; run all 2K, 4K, and 8K
scenarios for OSL 1 and OSL 100.

### 1. Maximum number of streams per GPU [Stream Processing]
The maximum number of concurrent RTSP streams processed at the target token budget per GPU without dropping chunks.

>> For BCD max-stream runs, keep the perf compose default `RTVI_RTSP_LATENCY=300` and `RTVI_RTPJITTERBUFFER_DROP_ON_LATENCY=false`. The benchmark measures from last-frame NTP timestamp to caption output, so any real buffering delay is counted and chunk-ID gaps still fail no-drop stability. Perf setup also sets `RTVI_RTPJITTERBUFFER_FASTSTART_MIN_PACKETS=2` so RTSP startup can begin after two consecutive packets instead of waiting for the full jitter-buffer window. Perf setup and `compose.perf.yaml` set `VLLM_IGNORE_EOS=true` for fixed-length N-token generation, especially OSL=100. Perf setup also sets `RTVI_DISABLE_LIVESTREAM_PREVIEW=true`, `RTVI_ENABLE_LIVE_TIMESTAMP_FILTER=false`, and `RTVI_EMPTY_CUDA_CACHE_ON_RESULT=false`; keep these defaults for max-stream runs. Set `VSS_SKIP_INPUT_MEDIA_VERIFICATION=1` in `.env.perf` for faster stream addition when finding the maximum stream count.

>> File-burst scenarios pre-upload a reusable `/files` pool by default (`reuse_uploaded_files: true`). The pool size defaults to the tested concurrency level, so BCD non-streaming throughput excludes upload/delete overhead while avoiding a single shared file ID becoming a serialization point.

###  2. VLM E2E Latency [Stream Processing]- The total time elapsed from the moment a chunk was created until the corresponding VLM caption is generated and posted to an endpoint. This shall include all delays: preprocess, queue, infer, and post.
Aggregation: min, max, average, p50, p75, p90, p95, and p99. The XLSX also
reports per-chunk stage latency for decode, queue, VLM inference, server
processing, and server E2E when `ENABLE_REQUEST_PROFILING=true`. Use the
`concurrency_test_*_{2k,4k,8k}` scenarios. The BCD 3.2 dGPU stream-count sweep is
`[1, 16, 32, 64, 128]`; for AGX Thor / DGX Spark use
`--concurrency-levels 1 8 16`.

### 3. Request Throughput [Non-Streaming] - The average number of requests that the microservice can successfully complete per second. This is the same as aiperf RPS metric.
Aggregation: min, max, average, p50, p75, p90, p95, and p99. Use the
`file_burst_*_{2k,4k,8k}` scenarios with the BCD 10 s, 10 FPS, H.264, 1080p
clip. The BCD 3.2 dGPU concurrency sweep is `[1, 16, 32, 64, 128]`, measured
over a 5 minute steady-state window.

### 4. VLM E2E Request Latency [Non-Streaming] - The duration between the initiation of a request and completion of the response. This is the same as the aiperf e2e_latency.
Aggregation: min, max, average, p50, p75, p90, p95, and p99. Use the
`e2e_latency_*_{2k,4k,8k}` scenarios at concurrency `[1]` for the 10 s, 600 s,
and 3600 s 10 FPS video durations.
