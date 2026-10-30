# RTVI Embed Performance Benchmarking - Perflab Guide

Performance benchmarking suite for VSS RTVI Embed Microservice video-text embedding generation.

---

## Quick Start (4-Step Workflow)

This is the minimal path from bare hardware to running benchmarks.

### Step 1 — Set up RTSP source + Python environment

By default rtvi_embed_bcd_config.yaml (BCD 3.1) is used for the benchmark setup.
Set the `CONFIG=perf/benchmark/rtvi_embed_bcd_3_2_config.yaml` to change the config file
 to be used.

```bash
export ARTIFACTORY_USER=<your_username>
export ARTIFACTORY_TOKEN=<your_token>
export RTVI_IMAGE="nvcr.io/nv-metropolis-dev/rtvi/vss-rt-embed:3.1.0-26.02.3"
export HF_TOKEN=<HF Token for model download>
export NVIDIA_VISIBLE_DEVICES=0
export BACKEND_PORT=8017
export REDIS_PORT=6380  # Optional, set if port 6379 is already used
export INITIAL_STREAM_COUNT=130 # Optional, set to override platform specific max streams test start count

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

# Benchmark setup script:
#   Downloads input videos from Artifactory
#   Uses perf/vst_package.tar.gz when present, otherwise downloads VST package
#   Patches VST images to nvcr.io/rxczgrvsg8nx/vst-dev with tag 2.1.0-26.04.1
#   Starts nvstreamer, VST
#   Creates Python venv,
#   Injects host IP into the selected config file

# For x86 dgpu like H100 etc
CONFIG=perf/benchmark/rtvi_embed_bcd_3_2_config.yaml  bash perf/setup_perf_env_rtvi_embed.sh

# Or For Thor/Spark
# CONFIG=perf/benchmark/rtvi_embed_bcd_3_2_thor_config.yaml bash perf/setup_perf_env_rtvi_embed.sh
```

Override `VST_IMAGE_REGISTRY`, `VST_IMAGE_TAG`, or the per-image variables from
`bash perf/setup_perf_env_rtvi_embed.sh -h` when testing a different VST build.
Check in `perf/vst_package.tar.gz` or set `VST_LOCAL_PACKAGE` to use a local VST
tarball instead of downloading the package. Artifactory credentials are required
when setup must download the VST tarball or any missing benchmark video.

#### Workaround for error in docker pull:

Newer docker version may show error: `error from registry: Incorrect Repository Format`

If `docker pull` fails with that message on a host using Docker’s containerd image store, disable the containerd snapshotter so Docker reverts to the classic overlay2 image store.

Edit `/etc/docker/daemon.json`:

```json
"features": { "containerd-snapshotter": false }
```

Then restart Docker:

```bash
# Close any running tmux sessions
sudo systemctl restart docker
```

- **Pros:** Restores plain `docker pull` for private `nvcr.io` paths.
- **Cons:** Stops every running container. Images in the containerd store are hidden until you switch back (they are not deleted).

### Step 2 — Run benchmarks

```bash
source ~/rtvi-embed-perf-env/bin/activate
cd <repo-root>

bash perf/run_rtvi_embed_benchmarks.sh all
```

By default rtvi_embed_bcd_config.yaml (BCD 3.1) is used for the benchmark.
Use a different benchmark YAML (e.g. BCD 3.2) without editing the script:

```bash
CONFIG=perf/benchmark/rtvi_embed_bcd_3_2_config.yaml bash perf/run_rtvi_embed_benchmarks.sh all
CONFIG=perf/benchmark/rtvi_embed_bcd_3_2_thor_config.yaml bash perf/run_rtvi_embed_benchmarks.sh all
```

or run individual benchmark:
```bash
bash perf/run_rtvi_embed_benchmarks.sh max_live_streams_test
bash perf/run_rtvi_embed_benchmarks.sh concurrent_live_streams_test
bash perf/run_rtvi_embed_benchmarks.sh single_file_test
bash perf/run_rtvi_embed_benchmarks.sh concurrency_test
bash perf/run_rtvi_embed_benchmarks.sh text_embedding_concurrency_test
```

### Step 3 — View results

```bash
ls perf/benchmark/rtvi-embed-perf-report/
# Prometheus: http://localhost:9090
# DCGM metrics: http://localhost:9400/metrics
```

### Step 4 - Stop Deployment

```bash
bash perf/teardown_perf_env_rtvi_embed.sh
```

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
- **Memory**: 32GB+ system RAM recommended
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

## RTVI Embed Microservice Container Images

| Image | Registry |
|-------|----------|
| **MultiArch (x86, igpu)** | `nvcr.io/nv-metropolis-dev/rtvi/vss-rt-embed:3.1.0-26.02.3` |
| **SBSA (Spark)** | `nvcr.io/nv-metropolis-dev/rtvi/vss-rt-embed:3.1.0-26.02.3-sbsa` |

---

## Benchmark Configuration

### Configuration files

| File | Purpose |
|------|---------|
| `perf/benchmark/rtvi_embed_bcd_config.yaml` | Default scenarios and `output_dir` |
| `perf/benchmark/rtvi_embed_bcd_3_2_config.yaml` | BCD 3.2 scenario matrix; `output_dir: rtvi-embed-perf-report-bcd-3-2` |
| `perf/benchmark/rtvi_embed_bcd_thor_config.yaml` | Thor AGX (T5000) / DGX Spark BCD ladder: non-streaming and text concurrency `[1, 8, 16]`; `output_dir: rtvi-embed-perf-report-bcd-thor` |

**Perf video paths inside the container:** `docker/rtvi_embed/deploy/compose.perf.yaml` bind-mounts **`PERF_VIDEOS_DIR` → `/opt/nvidia/rtvi/streams/perf`** (same tree nvstreamer uses for RTSP). **`rtvi_embed_bcd_config.yaml`** warehouse clips use that path; **`rtvi_embed_bcd_3_2_config.yaml`** / **`rtvi_embed_bcd_thor_config.yaml`** add BCD 10 FPS filenames under the same directory.

Override which file `perf/run_rtvi_embed_benchmarks.sh` passes to the harness:
For x86 dGPU H100 etc:
```bash
CONFIG=perf/benchmark/rtvi_embed_bcd_3_2_config.yaml bash perf/run_rtvi_embed_benchmarks.sh all
```
Or For Thor/Spark:
```bash
CONFIG=perf/benchmark/rtvi_embed_bcd_3_2_thor_config.yaml bash perf/run_rtvi_embed_benchmarks.sh all
```

Or call the harness directly: `python3 perf/benchmark/rtvi_perf_benchmark.py --config <path> [--scenario <name>]`.

---

## Metrics Reported

### 1. Max Live Streams Mode

Finds maximum number of concurrent RTSP streams the system can handle.
Scenario: max_live_streams_test
Report file: `rtvi-embed-perf-report/max_live_streams_test_max_live_streams_report.xlsx`

```text
**Metrics Reported**:
- max_sustainable_streams: Maximum stable stream count
- avg_latency: E2E latency for chunk
- GPU utilization
- NVDEC utilization
```

### 2. concurrent_live_streams_test

Reports chunk E2E latency for specified concurrent live stream processing.
1, 16, 32 concurrent live streams.

Scenario: concurrent_live_streams_test
Report file: `rtvi-embed-perf-report/concurrent_live_streams_test_concurrent_live_streams_report.xlsx`

```text
**Metrics Reported**:
- avg_latency, P90, P95, P99, min, max: E2E latency for chunk
- decode_latency: Decode latency per chunk
- GPU utilization
- NVDEC utilization
```

### 3. Single File E2E latency Mode

E2E request latency for file input (Non-streaming)

Scenario: single_file_test
Report file: `rtvi-embed-perf-report/single_file_test_single_file_report.xlsx`

```text
**Metrics Reported**:
- e2e_latency: E2E processing latency for complete file
- inference_latency: Average inference latency per chunk
- decode_latency: Average decode latency per chunk
- GPU utilization
- NVDEC utilization
```


### 4. Request Throughput (Concurrent requests) Non streaming

Numbers of non-streaming concurrent requests of specified concurrency level.

Scenario: concurrency_test
Report file: `rtvi-embed-perf-report/concurrency_test_concurrency_report.xlsx`

```text
**Metrics Reported**:
- concurrency_level: Number of concurrent requests within the e2e latency threshold
- e2e_latency: E2E processing latency for the set of files.
- throughput_files_per_second: Number of videos processed per second.
- avg_latency: Average e2e processing latency per video
- GPU utilization
- NVDEC utilization
```

### 5. Text Input Request Throughput (Concurrent requests)

Measures text embedding generation latency with concurrent text embedding requests.

Scenario: text_embedding_concurrency_test
Report file: `rtvi-embed-perf-report/text_embedding_concurrency_test_text_embedding_concurrency_report.xlsx`

```text
**Metrics Reported**:
- throughput_texts_per_second: Number of texts processed per second.
- avg_latency, P90, P95, P99: Average processing latency per text
- GPU utilization
- NVDEC utilization
```

---

## Environment Variables Reference

| Variable | Example | Description |
|----------|---------|-------------|
| `ARTIFACTORY_USER` | `<your_username>` | Artifactory username for package downloads |
| `ARTIFACTORY_TOKEN` | `<your_token>` | Artifactory token for package downloads |
| `RTVI_IMAGE` | `nvcr.io/nv-metropolis-dev/rtvi/vss-rt-embed:3.1.0-26.02.3` | RTVI Embed container image |
| `HF_TOKEN` | `<HF Token for model download>` | Hugging Face token for model download |
| `NVIDIA_VISIBLE_DEVICES` | `0` | GPU device(s) to use |
| `BACKEND_PORT` | `8017` | Backend service port |
| `REDIS_PORT` | 6379 (default), 6380 | Change if port 6379 conflicts with another service |
| `INITIAL_STREAM_COUNT` | `50` | Sets the starting number of parallel RTSP streams for max_live_streams/concurrent_live_streams perf benchmarks. Overrides the GPU specific config value in `perf/benchmark/rtvi_embed_gpu_initial_stream_counts.json`. |
| `CONFIG` | `perf/benchmark/rtvi_embed_bcd_3_2_config.yaml` | **Only for `run_rtvi_embed_benchmarks.sh`:** overrides the default `rtvi_embed_bcd_config.yaml` path. Use `rtvi_embed_bcd_thor_config.yaml` for the Thor / DGX Spark `[1, 8, 16]` matrix. |


---
