# VSS Performance Benchmark Tool

This tool provides comprehensive performance testing and benchmarking capabilities for Video Search and Summarization (VSS) deployments. It enables you to measure system throughput, latency characteristics, and resource utilization across different workload patterns including single file processing, concurrent files and live stream processing, alert review, and chat/RAG performance. The framework automatically collects detailed metrics on E2E latency, VLM/LLM pipeline performance, GPU and decoder utilization, and system behavior under load.



## Prerequisites

Before running benchmarks, you need to have VSS deployed and running.

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

This benchmark suite provides comprehensive performance testing across six core operational modes:

## Benchmarking Modes

### 1. Single File Mode (`single_file`)

This mode tests the complete video processing workflow from file upload through summarization to interactive Q&A. Videos are uploaded via the `/files` API, processed using the `/summarize` API with configurable chunking, and then a series of chat questions are executed via the `/chat/completions` API. Each test case is repeated multiple times to enable statistical analysis with mean and standard deviation calculations. The benchmark tracks comprehensive metrics including E2E latency, VLM pipeline latency, decode latency, CA-RAG latency, chat response times, and GPU utilization across VLM/LLM GPUs and decoder.

**Mode-specific parameters:**
- `iterations`: Number of times to repeat the test (optional, default: 3)
- `videos`: List of video configurations
  - `filepath`: Path to video file (required)
  - `chunk_sizes`: List of chunk durations to test (required)
  - `chat_questions`: Override global chat questions (optional)
  - `prompts`: Override global prompts (optional)

**Example configuration:**
```yaml
single_file_test:
  description: "Single file processing with chat completions"
  benchmark_mode: "single_file"
  iterations: 2
  videos:
    - filepath: "/opt/nvidia/via/streams/its.mp4"
      chunk_sizes: [10, 15]
      prompts:
        caption: "Monitor traffic events, violations, and vehicle types."
      chat_questions:
        - "Were there any traffic violations?"
        - "What types of vehicles were observed?"
    - filepath: "/opt/nvidia/via/streams/warehouse.mp4"
      chunk_sizes: [10]
      prompts:
        caption: "Describe warehouse events and look for any anomalies."
```

### 2. File Burst Mode (`file_burst`)

This mode evaluates concurrent video processing throughput by uploading and summarizing multiple videos simultaneously at different concurrency levels. The benchmark launches N concurrent requests (e.g., 1, 2, 4, 8, 16) to the `/summarize` API with chat disabled for pure throughput testing, measuring how the system handles parallel workloads. It performs an automatic binary search to find the optimal concurrency level that achieves a target average latency (default 60 seconds). Metrics tracked include E2E latency for all concurrent requests, per-request average and P90 latencies, throughput in files per second, GPU usage statistics, and the optimal concurrency point.

**Mode-specific parameters:**
- `videos`: List of video configurations
  - `filepath`: Path to video file (required)
  - `chunk_sizes`: List of chunk durations to test (required)
  - `concurrency_levels`: List of concurrent request counts to test (required)
  - `target_latency_seconds`: Target average latency (optional, default: 60.0)
  - `target_latency_tolerance`: Tolerance in seconds (optional, default: 5.0)

**Example configuration:**
```yaml
file_burst_test:
  description: "Test different concurrency levels and measure latency statistics"
  benchmark_mode: "file_burst"
  summarize_api_params:
    enable_chat: false
  videos:
    - filepath: "/opt/nvidia/via/streams/its.mp4"
      chunk_sizes: [10]
      concurrency_levels: [1, 2, 4, 8]
      prompts:
        caption: "Monitor traffic events, violations, and vehicle types."
    - filepath: "/opt/nvidia/via/streams/bridge.mp4"
      chunk_sizes: [10]
      concurrency_levels: [1, 2, 4]
      prompts:
        caption: "Describe the condition of the bridge infrastructure."
```

### 3. Max Live Streams Mode (`max_live_streams`)

This mode determines the maximum number of concurrent live streams the system can sustain without performance degradation. The benchmark starts with a configured number of initial streams and gradually adds more streams while continuously monitoring summarization latencies via streaming SSE responses from the `/summarize` API. When the P95 latency exceeds the configured threshold, the system detects degradation and enters a stability verification phase where it systematically reduces streams to find the stable operating point. Real-time latency tracking uses timestamp correlation between when video chunks are generated and when summaries are delivered.

**Mode-specific parameters:**
- `videos`: List of video configurations
  - `rtsp_url`: RTSP URL for live stream (required)
  - `chunk_sizes`: List of chunk durations to test (required)
  - `latency_threshold_seconds`: Performance degradation threshold in seconds (required)
  - `summary_duration`: Duration between summaries in seconds (required)
  - `name`: Identifier name for the stream (optional, default: "live_stream")
  - `initial_stream_count`: Starting number of streams (optional, default: 5)
  - `chunk_overlap_duration`: Overlap between chunks in seconds (optional)

**Example configuration:**
```yaml
max_live_streams_test:
  description: "Test maximum concurrent live streams without performance degradation"
  benchmark_mode: "max_live_streams"
  summarize_api_params:
    enable_chat: false
    vlm_input_width: 756
    vlm_input_height: 392
  videos:
    - name: "traffic_cam_1"
      rtsp_url: "rtsp://localhost:8554/traffic/cam1"
      chunk_sizes: [10]
      latency_threshold_seconds: 12
      summary_duration: 30
      initial_stream_count: 3
      chunk_overlap_duration: 2
      prompts:
        caption: "Monitor traffic events, violations, and vehicle types."
    - name: "warehouse_cam_1"
      rtsp_url: "rtsp://localhost:8554/warehouse/cam1"
      chunk_sizes: [15]
      latency_threshold_seconds: 20
      summary_duration: 60
      initial_stream_count: 2
      prompts:
        caption: "Describe warehouse events and look for any anomalies."
```

### 4. Alert Review Burst Mode (`alert_review_burst`)

This mode benchmarks the `/reviewAlert` API by sending multiple concurrent alert verification requests with randomly selected prompts from a configured list. The benchmark tests various concurrency levels to measure how many alerts can be processed simultaneously while tracking latency, success rates, and verification accuracy (true/false positives). Like file burst mode, it performs binary search to automatically find the optimal concurrency level for a target average latency (configurable, default 60 seconds). Each alert review request includes VLM parameters (prompt, temperature, max_tokens) and optional VSS parameters (frames per chunk, CV metadata overlay, reasoning/verification settings) to simulate real alert processing scenarios.

**Mode-specific parameters:**
- `videos`: List of video configurations
  - `filepath`: Path to video file
  - `concurrency_levels`: List of concurrent request counts to test
  - `alert_prompts`: List of alert questions to randomly select from
  - `system_prompt`: Override system prompt for the alert review (optional)
  - `target_latency_seconds`: Target average latency for optimal concurrency search (optional, default: 60.0)

**Example configuration:**
```yaml
alert_review_burst_test:
  description: "Test alert review API performance with concurrent requests"
  benchmark_mode: "alert_review_burst"
  videos:
    - filepath: "/opt/nvidia/via/streams/its.mp4"
      concurrency_levels: [1, 2, 4]
      alert_prompts:
        - "Is there a traffic collision?"
        - "Are vehicles following traffic rules?"
        - "Is there an emergency vehicle present?"
        - "Is traffic flow obstructed?"
      system_prompt: "You are a traffic monitoring AI. Answer with YES or NO only."
    - filepath: "/opt/nvidia/via/streams/warehouse.mp4"
      concurrency_levels: [1, 2]
      alert_prompts:
        - "Did a worker drop any boxes?"
        - "Is proper PPE being worn?"
        - "Are workers in restricted areas?"
      system_prompt: "You are a warehouse safety monitor. Answer with YES or NO only."
      target_latency_seconds: 45.0
```

### 5. VLM Captions Burst Mode (`vlm_captions_burst`)

This mode tests the `/generate_vlm_captions` API under concurrent load by generating VLM captions for multiple videos simultaneously at various concurrency levels. Unlike the summarize API which includes caption aggregation and RAG, this mode focuses purely on VLM caption generation performance for individual video chunks. The benchmark uploads multiple videos and requests VLM captions concurrently, testing different concurrency levels and automatically finding the optimal concurrency for a target average latency using binary search. Metrics include per-request latency statistics, throughput, GPU utilization, and the relationship between concurrency and response times.

**Mode-specific parameters:**
- `videos`: List of video configurations
  - `filepath`: Path to video file (required)
  - `chunk_sizes`: List of chunk durations to test (required)
  - `concurrency_levels`: List of concurrent request counts to test (required)
- `vlm_prompts`: VLM-specific prompts (prompt, system_prompt)
- `target_latency_seconds`: Target latency for optimal search (optional, default: 60.0)
- `target_latency_tolerance`: Tolerance in seconds (optional, default: 5.0)

**Example configuration:**
```yaml
vlm_captions_burst_test:
  description: "Test VLM caption generation performance"
  benchmark_mode: "vlm_captions_burst"
  vlm_prompts:
    prompt: "Describe the key events in this video with timestamps."
    system_prompt: "You are a video analysis assistant."
  videos:
    - filepath: "/opt/nvidia/via/streams/its.mp4"
      chunk_sizes: [10]
      concurrency_levels: [1, 2, 4, 8]
      target_latency_seconds: 60.0
    - filepath: "/opt/nvidia/via/streams/warehouse.mp4"
      chunk_sizes: [10]
      concurrency_levels: [1, 2, 4]
      vlm_captions_params:
        temperature: 0.5
        max_tokens: 100
```

### 6. Chat Completions Burst Mode (`chat_completions_burst`)

This mode measures the performance of the `/chat/completions` API by first ingesting a long video (with summarization disabled), then running multiple concurrent queries as a burst test. After video ingestion, the system waits a configurable period (default 3 seconds) before launching all queries simultaneously. The concurrency level determines how many queries are executed in parallel, with questions cycling through the configured list. The benchmark tracks latency statistics (avg, p50, p90, p95, p99) and throughput (queries per second). This mode is ideal for testing chat/RAG performance under load independently from video ingestion.

**Mode-specific parameters:**
- `iterations`: Number of times to repeat each test configuration (optional, default: 3)
- `ingestion_wait_seconds`: Wait time after ingestion before starting queries (optional, default: 3)
- `videos`: List of video configurations
  - `filepath`: Path to video file (required)
  - `chunk_sizes`: List of chunk durations for video ingestion (required)
  - `concurrency_levels`: List of concurrent query counts to test - determines total queries executed (required)
  - `chat_questions`: Override global chat questions (optional)
- `chat_questions`: List of questions to send to chat API (questions repeat to fill concurrency level)
- `summarize_api_params`: Configure ingestion behavior
  - `summarize: false` for ingestion-only mode
  - `enable_chat: true` to enable RAG functionality

**Example configuration:**
```yaml
chat_completions_burst_test:
  description: "Test chat/completions API performance with concurrent queries"
  benchmark_mode: "chat_completions_burst"
  iterations: 3
  ingestion_wait_seconds: 3
  summarize_api_params:
    summarize: false  # Ingestion only
    enable_chat: true
    enable_chat_history: true
  chat_questions:
    - "Did a worker drop any boxes?"
    - "Is the worker operating the forklift wearing a safety vest?"
    - "When did the forklift first arrive in the scene?"
    - "Did anyone cross the restricted area?"
    - "Was there any breach of safety protocol?"
  videos:
    - filepath: "/opt/nvidia/via/streams/perf/82min.mp4"
      chunk_sizes: [10]
      concurrency_levels: [1, 2, 5, 10, 20, 50]
```

## Basic Commands

```bash

# List available modes and scenarios
python vss_perf_benchmark.py --config vss_sample_config.yaml --list-modes
python vss_perf_benchmark.py --config vss_sample_config.yaml --list-scenarios

# Export  VSS backend IP:Port
export VIA_BACKEND=http://localhost:<port>

# Run scenarios using VSS sample videos config
python vss_perf_benchmark.py --config vss_sample_config.yaml --scenario single_file_test
python vss_perf_benchmark.py --config vss_sample_config.yaml --scenario file_burst_test
python vss_perf_benchmark.py --config vss_sample_config.yaml --scenario max_live_streams_test
python vss_perf_benchmark.py --config vss_sample_config.yaml --scenario alert_review_test
python vss_perf_benchmark.py --config vss_sample_config.yaml --scenario vlm_captions_burst_test
python vss_perf_benchmark.py --config vss_sample_config.yaml --scenario chat_completions_burst_test

# Quick test with VSS sample videos
python vss_perf_benchmark.py --config vss_sample_config.yaml --scenario quick_test

# Run all scenarios
python vss_perf_benchmark.py --config vss_sample_config.yaml

# Enable debug logging
python vss_perf_benchmark.py --config vss_sample_config.yaml --scenario test_name --debug
```

## Configuration File Structure

The YAML configuration file has two main sections:

**Global settings** (`global`):
- `vss_backend`: VSS API endpoint URL
- `output_dir`: Directory for test results
- `vlm_gpus` / `llm_gpus`: GPU assignments for VLM and LLM workloads
- `gpu_monitoring`: GPU monitoring settings (enabled by default, configurable interval_seconds, set `enabled: false` to disable)
- `summarize_api_params`: Parameters for `/summarize` API (temperature, max_tokens, enable_chat, enable_audio, etc.)
- `chat_api_params`: Parameters for `/chat/completions` API (temperature, max_tokens, top_p, top_k, seed)
- `vlm_captions_params`: Parameters for `/generate_vlm_captions` API (temperature, max_tokens, enable_reasoning, etc.)
- `alert_review_params`: Parameters for `/reviewAlert` API (VLM params, VSS params, verification settings)
- `prompts`: Default prompts for caption, summarization, and aggregation
- `chat_questions`: Default chat questions for testing

**Test scenarios** (`test_scenarios`):
Each scenario can override global API parameters at the scenario level. Individual videos can further override parameters at the video level. The configuration follows a 4-level merge: defaults.yaml → global → scenario → video, where each level overrides the previous while preserving unspecified parameters.

## Test Results and Output

The benchmark tool generates comprehensive test results in multiple formats:

- **XLSX Reports**: Excel spreadsheets containing detailed performance metrics, latency statistics (mean, P50, P90, P95, P99), throughput measurements, and GPU utilization data. These reports provide an easy-to-analyze view of benchmark results with formatted tables and charts.

- **JSON Files**: Raw test data and intermediate results saved as JSON files during test execution. These files contain detailed API responses, and metadata useful for debugging or post-processing analysis.

All output files are saved to the directory specified in the `output_dir` configuration parameter (default location defined in your config YAML).