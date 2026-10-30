# RTVI Embed Performance Benchmark Tool

Performance benchmarking framework for RTVI Embed backend services.

## Prerequisites

Before running benchmarks, you need to have RTVI Embed deployed and running.

On Jetson/Thor, `perf/scripts/native_setup_perf_env.sh` automatically and
idempotently patches the
extracted VST launchers so their expected
`/usr/lib/aarch64-linux-gnu/libcuda.so` path resolves to Thor's host CUDA
library. The script accepts direct and one-level nested locations under
`/opt/nvidia/l4t-gpu-libs`, and stops with an actionable error if neither is
available.

### Deploy RTVI Embed

Follow the official RTVI Embed documentation to deploy locally.
For additional configurations for specific benchmark, see the [BCD Test Scenarios](#bcd-test-scenarios) section below.

1. **Deploy RTVI Embed with Request Profiling enabled:**
   Set the `ENABLE_REQUEST_PROFILING` environment variable to `true` during deployment.

2. **Set the backend URL:**
   ```bash
   export RTVI_BACKEND=http://localhost:<port>/v1
   ```
   Replace `<port>` with the actual port from your deployment (typically 8000 for local, or the NodePort/LoadBalancer port for Helm).

3. **Configure GPU assignment:**
   Edit your benchmark configuration file (e.g., `rtvi_embed_sample_config.yaml`) to specify which GPUs are assigned to embedding workloads:
   ```yaml
     vlm_gpus: [0]  # GPU used for embedding processing
   ```
   Adjust the GPU indices based on your system's available GPUs. The benchmark will monitor these GPUs during test execution.

4. **Copy required test videos to the container:**
   Ensure the copied streams directory and files are readable by the user that runs RTVI Embed inside the container (for example, via group membership or chmod o+r).
```bash
docker cp streams <rtvi_embed-server-container>:/opt/nvidia/rtvi/
```

## Python Environment Setup (Recommended)

It's recommended to use a Python virtual environment to isolate dependencies:

```bash
sudo apt install python3-venv

mkdir ~/rtvi-embed-perf-env

# Create a virtual environment
python3 -m venv ~/rtvi-embed-perf-env

# Activate the virtual environment
source ~/rtvi-embed-perf-env/bin/activate

# Install required dependencies
pip install -r perf/benchmark/requirements.txt

# Sync host system with NTP
sudo apt-get install sntp
sudo sntp -S pool.ntp.org
```

```bash
# To deactivate the virtual environment when done:
deactivate
```

This benchmark suite provides comprehensive performance testing across four core operational modes:

## Benchmarking Modes

### 1. Single File Mode (`single_file`)

This mode tests the complete video embedding generation workflow from file upload through embeddings output. Videos are uploaded via the `/files` API, processed using the `/generate_video_embeddings` API with configurable chunking. Each test case is repeated multiple times to enable statistical analysis with mean and standard deviation calculations. The benchmark tracks comprehensive metrics including E2E latency, embedding pipeline latency, decode latency, and GPU utilization across embedding GPUs and NVDEC.

**Mode-specific parameters:**
- `iterations`: Number of times to repeat the test (optional, default: 3)
- `videos`: List of video configurations
  - `filepath`: Path to video file (required)
  - `chunk_sizes`: List of chunk durations to test (required)
  - `model`: Override global model (optional, default: "cosmos-embed1-448p")

**Example configuration:**
```yaml
single_file_test:
  description: "Single file embedding generation"
  benchmark_mode: "single_file"
  iterations: 2
  videos:
    - filepath: "/opt/nvidia/rtvi/streams/its.mp4"
      chunk_sizes: [10, 15]
      model: "cosmos-embed1-448p"
    - filepath: "/opt/nvidia/rtvi/streams/warehouse.mp4"
      chunk_sizes: [10]
      model: "cosmos-embed1-448p"
```

### 2. Concurrent requests Mode (`concurrency`)

This mode evaluates concurrent video embedding processing throughput by uploading and generating embeddings for multiple videos simultaneously at different concurrency levels. The benchmark launches N concurrent requests (e.g., 1, 2, 4, 8, 16) to the `/generate_video_embeddings` API for pure throughput testing, measuring how the system handles parallel workloads. It performs an automatic binary search to find the optimal concurrency level that achieves a target average latency (default 60 seconds). Metrics tracked include E2E latency for all concurrent requests, per-request average and P90 latencies, throughput in files per second, GPU usage statistics, and the optimal concurrency point.

**Mode-specific parameters:**
- `videos`: List of video configurations
  - `filepath`: Path to video file (required)
  - `chunk_sizes`: List of chunk durations to test (required)
  - `concurrency_levels`: List of concurrent request counts to test (required)
  - `target_latency_seconds`: Target average latency (optional, default: 60.0)
  - `target_latency_tolerance`: Tolerance in seconds (optional, default: 5.0)
  - `model`: Embedding model to use (optional)

**Example configuration:**
```yaml
concurrency_test:
  description: "Test video embeddings generation performance with concurrent requests"
  benchmark_mode: "concurrency"
  videos:
    - filepath: "/opt/nvidia/rtvi/streams/its.mp4"
      chunk_sizes: [10]
      concurrency_levels: [1, 2, 4]    # Test values to derive optimal concurrency for target latency
      target_latency_seconds: 60.0
      target_latency_tolerance: 5.0
      model: "cosmos-embed1-448p"
```

### 3. Max Live Streams Mode (`max_live_streams`)

This mode determines the maximum number of concurrent live streams the system can sustain without performance degradation. The benchmark starts with a configured number of initial streams and gradually adds more streams while continuously monitoring embedding generation latencies via streaming SSE responses from the `/generate_video_embeddings` API. When the P95 latency exceeds the configured threshold, the system detects degradation and enters a stability verification phase where it systematically reduces streams to find the stable operating point. Real-time latency tracking uses timestamp correlation between when video chunks are generated and when embeddings are delivered.

**Mode-specific parameters:**
- `videos`: List of video configurations
  - `rtsp_url`: RTSP URL for live stream (required), continuous looping RTSP stream is needed.
  - `chunk_sizes`: List of chunk durations to test (required)
  - `latency_threshold_seconds`: Performance degradation threshold in seconds (required)
  - `name`: Identifier name for the stream (optional, default: "live_stream")
  - `initial_stream_count`: Starting number of streams (optional, default: 5)
  - `chunk_overlap_duration`: Overlap between chunks in seconds (optional)
  - `model`: Embedding model to use (optional)

**Example configuration:**
```yaml
max_live_streams_test:
  description: "Test maximum concurrent live streams without performance degradation"
  benchmark_mode: "max_live_streams"
  videos:
    - name: "warehouse_cam_1"
      rtsp_url: "rtsp://nv-wowza-pdc.nvidia.com:1935/vod/warehouse_1.mp4"
      chunk_sizes: [5]
      latency_threshold_seconds: 5
      chunk_overlap_duration: 2
      model: "cosmos-embed1-448p"
      stability_check_interval: 30  # Seconds between stability checks (default: 60)
      add_stream_count: 2  # Number of streams added per step
      initial_stream_count: 5
    - name: "warehouse_cam_2"
      rtsp_url: "rtsp://nv-wowza-pdc.nvidia.com:1935/vod/warehouse_1.mp4"
      chunk_sizes: [10]
      latency_threshold_seconds: 10
      initial_stream_count: 2
      model: "cosmos-embed1-448p"
```

### 4. Concurrent Live Streams Mode (`concurrent_live_streams`)

This mode measures end-to-end latencies for a **fixed, pre-configured number** of concurrent live streams. Unlike `max_live_streams` (which discovers the capacity ceiling by gradually adding streams until degradation), this benchmark starts all N streams simultaneously, runs them for a configured duration, records latencies per chunk via SSE events, and reports statistical summaries. No gradual escalation or degradation detection — the focus is pure latency measurement under a known load.

**Mode-specific parameters:**
- `videos`: List of video configurations
  - `rtsp_url`: RTSP URL for live stream (required), continuous looping RTSP stream is needed.
  - `chunk_sizes`: List of chunk durations to test (required)
  - `stream_count`: Number of concurrent streams to start simultaneously (required, positive integer)
  - `duration_seconds`: How long to run the test in seconds (optional, default: 120)
  - `name`: Identifier name for the stream (optional, default: "live_stream")
  - `chunk_overlap_duration`: Overlap between chunks in seconds (optional)
  - `generate_video_embeddings_params` / `generate_captions_params`: API parameter overrides (optional)


**Reported metrics:**
- `avg_latency`, `p95_latency`, `p99_latency`, `max_latency`, `min_latency`: Aggregate latency stats across all streams and chunks
- `total_measurements`: Total number of chunk latency samples collected
- `per_stream_stats`: Per-stream breakdown (avg, max, min, count)
- GPU utilization metrics (mean, p90) and NVDEC utilization
- `decode_latency_seconds_avg`: Average video decode latency from Prometheus metrics

**Example configuration:**
```yaml
concurrent_live_streams_test:
  description: "Test N concurrent live streams and measure latencies"
  benchmark_mode: "concurrent_live_streams"
  generate_video_embeddings_params:
    model: "cosmos-embed1-448p"
  videos:
    - name: "warehouse_cam_concurrent"
      rtsp_url: "rtsp://nv-wowza-pdc.nvidia.com:1935/vod/concat_wh_52.mp4"
      chunk_sizes: [10]
      stream_count: 5
      duration_seconds: 120
      chunk_overlap_duration: 2
```

### 5. Text Embedding Mode (`text_embedding`)

This mode tests the `/generate_text_embeddings` API by generating text embeddings for text inputs over multiple iterations. The benchmark measures inference latency and GPU utilization for text embedding generation. Each text input is processed multiple times to calculate average performance metrics with standard deviation.

**Mode-specific parameters:**
- `iterations`: Number of times to run each text input (optional, default: 3)
- `text_inputs`: List of text input configurations
  - `name`: Optional identifier for the text input (used in test case naming)
  - `text`: Text string or list of text strings to generate embeddings for (required)

**Example configuration with single text input:**
```yaml
text_embedding_quick_test:
  description: "Quick text embedding test with single input"
  benchmark_mode: "text_embedding"
  iterations: 5
  text_inputs:
    - name: "quick_test"
      text: "a person riding a motorcycle"
```

**Example configuration with text input list:**
```yaml
text_embedding_list_test:
  description: "Text embedding test with multiple text inputs"
  benchmark_mode: "text_embedding"
  iterations: 5
  text_inputs:
    - name: "traffic_scenarios"
      text:
        - "a person riding a motorcycle in the night"
        - "a car overtaking a white truck"
        - "warehouse workers loading boxes onto pallets"
        - "traffic congestion during rush hour"
        - "pedestrians crossing a busy intersection"
```

### 6. Text Embedding Concurrency Mode (`text_embedding_concurrency`)

This mode measures latency and throughput for concurrent batched text embedding requests to the `/generate_text_embeddings` endpoint. Unlike `text_embedding` (which tests a single API call per iteration), this benchmark fires multiple API calls simultaneously, with each call carrying a batch of texts.

**Key distinction:** `concurrency_level` specifies the total number of texts per test run. The benchmark automatically splits this into batches of ≤100 texts each (the API maximum) and fires all batches concurrently. E2E latency is the time from first to last batch completing.

Example: `concurrency_level=256` → 3 parallel API calls carrying [100, 100, 56] texts respectively.

**Mode-specific parameters:**

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `texts` | Yes | — | Pool of text strings; cycled/replicated to fill each concurrency level |
| `concurrency_levels` | Yes | — | Total texts per run (auto-batched ≤100/call, all batches concurrent) |
| `target_latency_seconds` | No | — | If set, binary search finds total text count achieving this avg batch latency |
| `target_latency_tolerance` | No | 5.0 | Tolerance in seconds for binary search convergence |

**Reported metrics:** `avg_latency`, `p90_latency`, `p99_latency`, `e2e_latency_seconds`, `throughput_texts_per_second`, `num_batches`, `completed_texts`, `failed_batches`, GPU utilization metrics.

**Example configuration:**
```yaml
text_embedding_concurrency_test:
  description: "Test concurrent batched text embedding requests"
  benchmark_mode: "text_embedding_concurrency"
  generate_text_embeddings_params:
    model: "cosmos-embed1-448p"
  text_inputs:
    - name: "mixed_scenarios"
      texts:
        - "a person riding a motorcycle on a city street"
        - "a car overtaking a white truck on the highway"
        - "warehouse workers loading boxes onto pallets"
      # concurrency_level = total texts; auto-batched into ≤100 texts per API call
      # e.g., 256 → 3 parallel calls: [100, 100, 56]
      concurrency_levels: [1, 32, 64, 128, 256]
      target_latency_seconds: 5.0
      target_latency_tolerance: 1.0
```

```bash
python rtvi_perf_benchmark.py --config rtvi_embed_sample_config.yaml --scenario text_embedding_concurrency_test
```

## Basic Commands

```bash

# List available modes and scenarios
cd perf/benchmark
python rtvi_perf_benchmark.py --config rtvi_embed_sample_config.yaml --list-modes
python rtvi_perf_benchmark.py --config rtvi_embed_sample_config.yaml --list-scenarios

# Export RTVI Embed backend IP:Port
export RTVI_BACKEND=http://localhost:<port>/v1

# Run scenarios using RTVI sample videos config
python rtvi_perf_benchmark.py --config rtvi_embed_sample_config.yaml --scenario single_file_test
python rtvi_perf_benchmark.py --config rtvi_embed_sample_config.yaml --scenario concurrency_test
python rtvi_perf_benchmark.py --config rtvi_embed_sample_config.yaml --scenario max_live_streams_test
python rtvi_perf_benchmark.py --config rtvi_embed_sample_config.yaml --scenario concurrent_live_streams_test
python rtvi_perf_benchmark.py --config rtvi_embed_sample_config.yaml --scenario text_embedding_quick_test
python rtvi_perf_benchmark.py --config rtvi_embed_sample_config.yaml --scenario single_live_stream_test

# Quick test with RTVI sample videos
python rtvi_perf_benchmark.py --config rtvi_embed_sample_config.yaml --scenario quick_test

# Run all scenarios
python rtvi_perf_benchmark.py --config rtvi_embed_sample_config.yaml

# Enable debug logging
python rtvi_perf_benchmark.py --config rtvi_embed_sample_config.yaml --scenario test_name --debug
```

## Configuration File Structure

The YAML configuration file has two main sections:

**Global settings** (`global`):
- `rtvi_backend`: RTVI Embed API endpoint URL
- `output_dir`: Directory for test results
- `vlm_gpus`: GPU assignments for embedding workloads
- `gpu_monitoring`: GPU monitoring settings (enabled, interval, export options)
- `generate_video_embeddings_params`: Parameters for `/generate_video_embeddings` API (chunk_duration, model, etc.)
- `generate_text_embeddings_params`: Parameters for `/generate_text_embeddings` API (model, etc.)
- `model`: Default embedding model (e.g., "cosmos-embed1-448p")

**Test scenarios** (`test_scenarios`):
Each scenario can override global API parameters at the scenario level. Individual videos or text inputs can further override parameters at the individual level. The configuration follows a 4-level merge: defaults.yaml → global → scenario → video/text, where each level overrides the previous while preserving unspecified parameters.

## Sample Configuration File

```yaml
global:
  rtvi_backend: "http://localhost:8017/v1"
  output_dir: "./benchmark_results"
  vlm_gpus: [0]

  gpu_monitoring:
    enabled: true
    interval: 1.0
    export_csv: true

  generate_video_embeddings_params:
    model: "cosmos-embed1-448p"
    chunk_duration: 10

  generate_text_embeddings_params:
    model: "cosmos-embed1-448p"

test_scenarios:
  quick_test:
    description: "Quick embedding test with single video"
    benchmark_mode: "single_file"
    iterations: 1
    videos:
      - filepath: "/opt/nvidia/rtvi/warmup_streams/its_264.mp4"
        chunk_sizes: [10]

  throughput_test:
    description: "Measure embedding generation throughput"
    benchmark_mode: "file_burst"
    videos:
      - filepath: "/opt/nvidia/rtvi/warmup_streams/its_264.mp4"
        chunk_sizes: [10]
        concurrency_levels: [1, 2, 4, 8]

  text_embedding_test:
    description: "Test text embedding generation"
    benchmark_mode: "text_embedding"
    iterations: 3
    text_inputs:
      - name: "traffic_and_warehouse"
        text:
          - "a person riding a motorcycle"
          - "a car on the highway"
          - "warehouse loading operations"
```

## Output and Results

### Result Files

The benchmark creates timestamped output directories with the following structure:

```text
benchmark_results/
└── YYYYMMDD_HHMMSS_<scenario_name>/
    ├── summary.json                      # Overall test summary
    ├── detailed_results.json             # Detailed test results
    ├── gpu_metrics.csv                   # GPU utilization data
    ├── latency_distribution.png          # Latency distribution plots
    └── throughput_vs_concurrency.png     # Throughput analysis plots
```

### Metrics Collected

**Video Embeddings:**
- End-to-end latency (file upload to embeddings delivery)
- Embedding pipeline latency
- Video decode latency
- Per-chunk embedding generation time
- GPU utilization (compute, memory, encoder/decoder)
- Throughput (files/chunks per second)
- Concurrency vs. latency relationship

**Text Embeddings:**
- Inference latency (API request to response)
- Embedding generation time per text input
- GPU utilization during embedding generation
- Statistics across multiple iterations (mean, standard deviation)
- Number of embeddings generated per request

**Live Streams:**
- Chunk-to-embedding latency
- Maximum sustainable stream count
- P95 latency tracking
- Stream stability metrics
- GPU utilization over time

## Troubleshooting

### Connection Issues
```bash
# Verify RTVI Embed is running
curl http://localhost:8000/v1/health

# Check backend URL
echo $RTVI_BACKEND

# Test with explicit backend
python rtvi_perf_benchmark.py --config config.yaml --backend http://localhost:8000/v1
```

### GPU Monitoring Issues
- Ensure `nvidia-smi` is accessible
- Verify GPU indices in config match your system
- Check GPU permissions for monitoring

### Memory Issues
- Reduce concurrency levels
- Decrease chunk sizes
- Monitor GPU memory usage during tests
- Adjust batch sizes in RTVI Embed configuration

### Model Loading Issues
- Verify model is available: "cosmos-embed1-448p"
- Check HuggingFace token if using private models
- Ensure sufficient disk space for model cache

## Performance Tuning Tips

1. **Chunk Size Selection:**
   - Smaller chunks (5-10s): Lower latency, higher overhead
   - Larger chunks (30-60s): Higher throughput, increased latency
   - Test range: [5, 10, 15, 30, 60]

2. **Concurrency Optimization:**
   - Start with low concurrency (1-2)
   - Gradually increase to find optimal point
   - Monitor GPU utilization to avoid saturation

3. **GPU Assignment:**
   - Dedicate specific GPUs for embedding generation
   - Avoid sharing GPUs with other workloads during benchmarking
   - Use multiple GPUs for higher throughput

4. **Batch Size Tuning:**
   - Configure `VLM_BATCH_SIZE` environment variable in RTVI Embed
   - Larger batches: Better GPU utilization, higher memory usage
   - Smaller batches: Lower latency, reduced throughput

## API Endpoints Tested

- `POST /v1/files` - Upload video/image files
- `POST /v1/generate_video_embeddings` - Generate embeddings for video/image
- `POST /v1/generate_text_embeddings` - Generate embeddings for text
- `POST /v1/live_streams` - Add live stream sources
- `GET /v1/health` - Health check
- `GET /v1/models` - List available models

## Supported Models

- **cosmos-embed1-448p**: NVIDIA Cosmos Embedding Model (448p resolution)

Additional models can be configured as they become available.

## Best Practices

1. **Baseline Establishment:**
   - Run single file tests first to establish baseline performance
   - Use consistent test videos for reproducibility
   - Document hardware configuration with results

2. **Incremental Testing:**
   - Test single video before burst modes
   - Start with low concurrency, increase gradually
   - Monitor system resources throughout testing

3. **Result Analysis:**
   - Compare P50, P90, P95 latencies
   - Identify bottlenecks (GPU, CPU, I/O, network)
   - Use GPU metrics to optimize resource allocation

4. **Reproducibility:**
   - Use same videos across test runs
   - Document environment variables
   - Save configuration files with results
   - Note any system changes between runs

## BCD Test Scenarios
Use following [rtvi_embed_sample_config.yaml](rtvi_embed_sample_config.yaml) as starting point for the tests.
Set `VSS_SKIP_INPUT_MEDIA_VERIFICATION=1` in .env for allowing faster stream addition.
Set `KAFKA_ENABLED=false` to avoid Kafka messaging overhead.

### 1. Maximum number of sustainable streams [Stream Processing]
The maximum number of concurrent RTSP streams processed at the target latency without dropping chunks:

* By default, RTVI Embed drops packets. Set `RTVI_RTSP_LATENCY` to a very high value > 100 seconds
 in [compose.yaml](docker/rtvi_embed/deploy/compose.yaml) to avoid packet dropping before MS deployment.
*  Start an input RTSP stream on the host system in loop mode and use it as input.
    ```bash
    sudo apt install vlc
    cvlc <input.mp4>  :sout=#gather:rtp{sdp=rtsp://:8554/file-stream} :network-caching=1500 :sout-all :sout-keep -L
    # This exposes an RTSP stream on port 8554
    ```
*  Update ulimits in the deployment [compose.yaml](docker/rtvi_embed/deploy/compose.yaml) file:
    ```yaml
        ulimits:
          memlock:
            soft: -1
            hard: -1
          stack: 67108864
          nofile:
            soft: 65535
            hard: 65535
    ```
*  Run the benchmark:
    ```bash
    python rtvi_perf_benchmark.py --config rtvi_embed_sample_config.yaml --scenario max_live_streams_test
    ```

### 2. Concurrent stream latency (fixed N streams)
Use `concurrent_live_streams_test` scenario from [rtvi_embed_sample_config.yaml](rtvi_embed_sample_config.yaml).
The benchmark starts all configured streams simultaneously, runs for `duration_seconds`, and reports avg/p95/p99/max/min latency, per-stream breakdowns, and GPU metrics.

*  Set up an RTSP stream in loop mode (see scenario 1 above for VLC command).
*  Update `stream_count` and `duration_seconds` in the scenario config as needed.
*  Run the benchmark:
    ```bash
    python rtvi_perf_benchmark.py --config rtvi_embed_sample_config.yaml --scenario concurrent_live_streams_test
    ```

### 3. Streaming chunk latency
Use `single_live_stream_embed_test` scenario from [rtvi_embed_sample_config.yaml](rtvi_embed_sample_config.yaml)
The benchmark saves inference latency (reported as vlm_latency) and decode latency (reported as decode_latency) for each chunk in the report.

```bash
python rtvi_perf_benchmark.py --config rtvi_embed_sample_config.yaml --scenario single_live_stream_embed_test
```

### 4. Offline chunk latency
Use `single_file_test` scenario from [rtvi_embed_sample_config.yaml](rtvi_embed_sample_config.yaml)
The benchmark saves inference latency (reported as vlm_latency) and decode latency (reported as decode_latency) for each chunk in the report.

```bash
python rtvi_perf_benchmark.py --config rtvi_embed_sample_config.yaml --scenario single_file_test
```

### 5. Offline text latency
Use `text_embedding_quick_test` scenario from [rtvi_embed_sample_config.yaml](rtvi_embed_sample_config.yaml).
The benchmark reports the time taken for the text embedding REST API request to complete.
```bash
python rtvi_perf_benchmark.py --config rtvi_embed_sample_config.yaml --scenario text_embedding_quick_test
```

### 6. Offline single file embedding generation
Use `single_file_test` scenario from [rtvi_embed_sample_config.yaml](rtvi_embed_sample_config.yaml).
The benchmark saves e2e latency per request in the report.
```bash
python rtvi_perf_benchmark.py --config rtvi_embed_sample_config.yaml --scenario single_file_test
```

### 7. Concurrent text embedding throughput
Use `text_embedding_concurrency_test` scenario from [rtvi_embed_sample_config.yaml](rtvi_embed_sample_config.yaml).
Reports throughput (texts/sec) and latency at each concurrency level. Tests multiple total-text counts; each count is automatically divided into concurrent batches of ≤100 texts per API call. Optionally performs binary search to find the total text count achieving a target average batch latency.
```bash
python rtvi_perf_benchmark.py --config rtvi_embed_sample_config.yaml --scenario text_embedding_concurrency_test
```
