<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

# VSS Performance Benchmark Tool

This tool provides comprehensive performance testing and benchmarking capabilities for Video Search and Summarization (VSS) deployments. It enables you to measure system throughput, latency characteristics, and resource utilization across different workload patterns including single file processing and concurrent file processing. The framework automatically collects detailed metrics on E2E latency, VLM/LLM pipeline performance, GPU and decoder utilization, and system behavior under load.



## Prerequisites

Before running benchmarks, you need to have VSS deployed and running with `VIA_DEV_API=true` set in the server environment. This enables the `/files` endpoint used by single-file mode to upload videos before benchmarking.

### Deploy VSS

Follow the official VSS documentation to deploy locally:
- **x86 platforms**: [x86 Installation Guide](https://docs.nvidia.com/vss/latest/content/x86_setup.html)
- **ARM platforms** (Jetson Thor, DGX Spark): [ARM Installation Guide](https://docs.nvidia.com/vss/latest/content/arm_setup.html)

1. **Set the backend URL:**
   ```bash
   export VIA_BACKEND=http://localhost:<port>
   ```
   Replace `<port>` with the actual port from your deployment (typically 8000 for local, or the NodePort/LoadBalancer port for Helm). See the [VSS API Reference](https://docs.nvidia.com/vss/latest/content/vss_dep_helm.html#launch-vss-ui) for details.

2. **Configure GPU assignment:**
   Edit your benchmark configuration file (e.g., `vss_sample_config.yaml`) to specify which GPUs are assigned to VLM and LLM workloads:
   ```yaml
     vlm_gpus: [0, 1]  # GPU indices for VLM processing
     llm_gpus: [2, 3]  # GPU indices for LLM processing
   ```
   Adjust the GPU indices based on your system's available GPUs. The benchmark will monitor these GPUs during test execution.

3. **Configure GPU monitoring (optional):**
   GPU monitoring is enabled by default but can be configured or disabled:
   ```yaml
   gpu_monitoring:
     enabled: true              # Set to false to disable GPU monitoring
     interval_seconds: 1        # Sampling interval (default: 1 second)
   ```
   **Note**: GPU monitoring must be enabled only when the benchmark tool is being executed locally on the same host as VSS. If running the tool remotely, disable GPU monitoring by setting `enabled: false`. GPU monitoring is also not supported on the Spark platform and should be disabled.

### EVS++ / Efficient Video Sampling Configuration

EVS++ is configured on the RTVI VLM service, not in the benchmark YAML. No EVS variable is read by the VSS server, RTVI Embed, or alert verification services. Set these variables in the `rtvi-vlm` container environment before starting the deployment.

Set `VIA_EVS_SESSION=true` for session mode, which adds per-stream video sessions, similarity-based pruning, event-gated generation, and optional streaming prefill. It also enables absolute-timestamp metadata internally, matching `RTVI_VIDEO_METADATA_ABSOLUTE_TIMESTAMPS=true`. Session mode is rejected on `NemotronH_Nano_Omni_Reasoning_V3` architectures, where it fails at startup rather than falling back.

Default EVS values:

- `VLM_VIDEO_PRUNING_RATE`: unset by default; **set it to `0.5` when enabling EVS++**. This is what activates pruning in vLLM — the engine only enables its EVS path when `video_pruning_rate` is passed at engine init, and this variable is its only source. With `VIA_EVS_SESSION=true` but the rate unset, sessions are created but nothing is pruned, so the run pays EVS++ overhead with no token reduction.
- `VIA_EVS_SESSION`: unset is `false`.
- `VLLM_EVS_SIMILARITY_THRESHOLD`: `0.2`.
- `VLLM_NUM_PREPROCESS_WORKERS`: `1`.
- `VIA_EVS_TOKEN_BUDGET`: `20000`.
- `VIA_EVS_MIN_CLIPS`: `3`.
- `VIA_EVS_EVENT_ONLY`: `true`.
- `VIA_EVS_EMA_MEMORY_S`: `0`.
- `VIA_EVS_SPIKE_STD_K`: `2.0`.
- `VIA_EVS_SETTLING_STD_K`: `1.5`.
- `VIA_EVS_STD_FLOOR_RATIO`: `0.1`.
- `VIA_EVS_DOWNWARD_BASELINE_MIN`: `0.10`.
- `VIA_EVS_STREAMING_PREFILL`: unset is `false`.
- `VLLM_IGNORE_EOS`: `false`.

**Note**: Redeploy the `rtvi-vlm` container for these settings to take effect.

For EVS++ perf runs, use this configuration:

```bash
# Master switch for EVS++ session mode.
export VIA_EVS_SESSION=true

# Required with EVS++: activates pruning in vLLM.
export VLM_VIDEO_PRUNING_RATE=0.5

# Cosine-dissimilarity threshold for pruning near-duplicate frames.
export VLLM_EVS_SIMILARITY_THRESHOLD=0.4

# Disable event gating so every chunk produces a caption.
export VIA_EVS_EVENT_ONLY=false

# Generate per clip instead of accumulating tokens toward the 20000 default.
export VIA_EVS_TOKEN_BUDGET=1

# Optional: let generations run to max_tokens for OSL/perf runs.
export VLLM_IGNORE_EOS=true
```

`VIA_EVS_EVENT_ONLY=false` and `VIA_EVS_TOKEN_BUDGET=1` together keep caption counts equal to a non-EVS run, so latency and throughput compare directly.

## Python Environment Setup (Recommended)

It's recommended to use a Python virtual environment to isolate dependencies:

```bash
# Create a virtual environment
python3 -m venv vss-perf-env

# Activate the virtual environment
source vss-perf-env/bin/activate

# Install required dependencies
pip install -r requirements.txt

# To deactivate the virtual environment when done:
deactivate
```

This benchmark suite provides comprehensive performance testing across two core operational modes:

## Benchmarking Modes

### 1. Single File Mode (`single_file`)

This mode tests the complete video processing workflow from file upload through summarization. Videos are uploaded via the `/files` API and processed using the `/summarize` API with configurable chunking. Each test case is repeated multiple times to enable statistical analysis with mean and standard deviation calculations. The benchmark tracks comprehensive metrics including E2E latency, VLM pipeline latency, decode latency, and GPU utilization across VLM/LLM GPUs and decoder.

**Mode-specific parameters:**
- `iterations`: Number of times to repeat the test (optional, default: 3)
- `videos`: List of video configurations
  - `filepath`: Path to video file (required)
  - `chunk_sizes`: List of chunk durations to test (required)
  - `summarize_api_params`: Override global summarize parameters (optional)
    - `events`: List of events to monitor
    - `scenario`: Scenario description

**Example configuration:**
```yaml
single_file_test:
  description: "Single file processing and summarization"
  benchmark_mode: "single_file"
  iterations: 2
  videos:
    - filepath: "/opt/nvidia/via/streams/its.mp4"
      chunk_sizes: [10, 15]
      summarize_api_params:
        events:
          - "traffic collision"
          - "unsafe maneuver"
          - "traffic violation"
          - "emergency vehicle present"
        scenario: "intelligent traffic monitoring system"
    - filepath: "/opt/nvidia/via/streams/warehouse.mp4"
      chunk_sizes: [10]
      summarize_api_params:
        events:
          - "worker dropping boxes"
          - "PPE violation"
          - "forklift safety violation"
        scenario: "warehouse safety monitoring"
```

### 2. File Burst Mode (`file_burst`)

This mode evaluates concurrent video processing throughput at different concurrency levels. For each concurrency level N, the benchmark:

1. **Pre-uploads** N copies of the video via `POST /files` (one-time cost, not timed)
2. **Fires** N concurrent `POST /summarize` requests using the pre-uploaded file IDs (pure inference, no download)
3. **Cleans up** all N assets via `DELETE /files/{id}`

This measures pure concurrent inference throughput without download latency noise. The benchmark also performs an automatic binary search to find the optimal concurrency level that achieves a target average latency (default 60 seconds). Metrics tracked include E2E latency for all concurrent requests, per-request average and P90 latencies, throughput in files per second, GPU usage statistics, and the optimal concurrency point.

**Mode-specific parameters:**
- `videos`: List of video configurations
  - `url`: URL to video file (RTVI downloads during upload), OR
  - `filepath`: Local path to video file (container-accessible)
  - `chunk_sizes`: List of chunk durations to test (required)
  - `concurrency_levels`: List of concurrent request counts to test (required)
  - `target_latency_seconds`: Target average latency (optional, default: 60.0)
  - `target_latency_tolerance`: Tolerance in seconds (optional, default: 5.0)
  - `summarize_api_params`: Override global summarize parameters (optional)
    - `events`: List of events to monitor
    - `scenario`: Scenario description

**Example configuration (local filepath):**
```yaml
file_burst_test:
  benchmark_mode: "file_burst"
  videos:
    - filepath: "/opt/nvidia/via/streams/its.mp4"
      chunk_sizes: [10]
      concurrency_levels: [1, 2, 4, 8]
      summarize_api_params:
        events:
          - "traffic collision"
          - "unsafe maneuver"
          - "traffic violation"
        scenario: "intelligent traffic monitoring system"
```

## Basic Commands

Run from the `perf/benchmark` directory (or from repo root: `python perf/benchmark/vss_perf_benchmark.py` with the same arguments).

```bash
# List available modes and scenarios
python vss_perf_benchmark.py --config vss_sample_config.yaml --list-modes
python vss_perf_benchmark.py --config vss_sample_config.yaml --list-scenarios

# Export  VSS backend IP:Port
export VIA_BACKEND=http://localhost:<port>

# Run scenarios using VSS sample videos config
python vss_perf_benchmark.py --config vss_sample_config.yaml --scenario single_file_test
python vss_perf_benchmark.py --config vss_sample_config.yaml --scenario file_burst_test

# Quick test with VSS sample videos
python vss_perf_benchmark.py --config vss_sample_config.yaml --scenario quick_test

# Run all scenarios
python vss_perf_benchmark.py --config vss_sample_config.yaml

# Run a benchmark against an EVS++-enabled deployment
VIA_BACKEND=http://localhost:<port> \
python vss_perf_benchmark.py --config vss_sample_config.yaml --scenario file_burst_test

# Enable debug logging
python vss_perf_benchmark.py --config vss_sample_config.yaml --scenario test_name --debug
```

**CI/Jenkins:** The same script is used by the perf pipeline (`ci/Jenkinsfile.perf`). The pipeline runs from `perf/benchmark` with `python vss_perf_benchmark.py`; see [ci/PERF_PIPELINE_GUIDE.md](../../ci/PERF_PIPELINE_GUIDE.md) for parameters and flow.

## Configuration File Structure

The YAML configuration file has two main sections:

**Global settings** (`global`):
- `vss_backend`: VSS API endpoint URL
- `output_dir`: Directory for test results
- `vlm_gpus` / `llm_gpus`: GPU assignments for VLM and LLM workloads
- `release` (optional): Release identifier embedded in schema `metadata.run_info.release` (e.g., `EA2`)
- `topology` (optional): GPU topology embedded in schema `metadata.platform.gpu.topology` (e.g., `4x4`); if omitted, inferred from `config_id` when possible
- `gpu_monitoring`: GPU monitoring settings (enabled by default, configurable interval_seconds, set `enabled: false` to disable)
- `summarize_api_params`: Parameters for `/summarize` API (temperature, max_tokens, events, scenario, and optional: vlm_input_width, vlm_input_height, chunk_overlap_duration)

**Test scenarios** (`test_scenarios`):
Each scenario can override global API parameters at the scenario level. Individual videos can further override parameters at the video level. The configuration follows a 4-level merge: defaults.yaml → global → scenario → video, where each level overrides the previous while preserving unspecified parameters.

## Test Results and Output

The benchmark tool generates comprehensive test results in multiple formats:

- **XLSX Reports**: Excel spreadsheets containing detailed performance metrics, latency statistics (mean, P50, P90, P95, P99), throughput measurements, and GPU utilization data. These reports provide an easy-to-analyze view of benchmark results with formatted tables and charts.

- **JSON Files**: Raw test data and intermediate results saved as JSON files during test execution. These files contain detailed API responses, and metadata useful for debugging or post-processing analysis.

All output files are saved to the directory specified in the `output_dir` configuration parameter (default location defined in your config YAML).
