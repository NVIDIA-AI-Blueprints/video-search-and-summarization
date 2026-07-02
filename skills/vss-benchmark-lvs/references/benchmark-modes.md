# Benchmark Modes Reference

The LVS performance benchmark supports two modes: `single_file` and `file_burst`. Each mode is implemented as a separate class in `scripts/` and tests a different aspect of LVS performance.

---

## single_file Mode

**Class:** `SingleFileBenchmark` (`scripts/single_file_benchmark.py`)

**What it tests:** End-to-end summarization latency for a single video file at a time. One video is uploaded via `POST /files`, then the `/summarize` API is called multiple times (iterations) to measure latency stability.

**When to use:**
- Measuring baseline summarization latency for your hardware configuration
- Comparing different chunk sizes (`chunk_duration`) for their effect on latency
- Validating that E2E latency meets SLA requirements for your video lengths
- Identifying VLM vs. CA-RAG vs. decode pipeline bottlenecks

**How it works:**
1. Upload the test video via `POST /files` — returns a `file_id`.
2. For each iteration, call `POST /summarize` with the `file_id` and chunk size.
3. After all iterations, delete the uploaded file via `DELETE /files/{file_id}`.
4. Repeat for each video + chunk size combination.
5. Scrape `/metrics` after each summarization to get server-side latency breakdown.

**Key metrics produced:**

| Metric | Description |
|---|---|
| `e2e_latency` | Wall-clock time from request to response (seconds) |
| `vlm_pipeline_latency` | Time spent in the VLM vision pipeline (from `/metrics`) |
| `vlm_latency` | VLM model inference time only (subset of pipeline) |
| `decode_latency` | Video decode time (from `/metrics`) |
| `ca_rag_latency` | Context-aware RAG inference and Elasticsearch time (from `/metrics`) |
| `vlm_gpu_usage_mean` | Mean GPU compute utilization % on VLM GPU(s) during the test |
| `llm_gpu_usage_mean` | Mean GPU compute utilization % on LLM GPU(s) during the test |
| `vlm_gpu_memory_mean` | Mean GPU memory utilization % on VLM GPU(s) |
| `total_chunks_processed` | Number of video chunks the VLM processed |
| `events_detected` | Number of events found in the summarization response |

**Test case ID format:** `single_file_{name}_{chunk_size}sec`
Example: `single_file_1min_10sec`, `single_file_5min_30sec`

**Config example:**
```yaml
single_file_test:
  benchmark_mode: "single_file"
  iterations: 3
  videos:
    - url: "http://HOST_IP:8888/videos/warehouse_5min.mp4"
      name: "5min"
      chunk_sizes: [10, 30]
```

---

## file_burst Mode

**Class:** `FileBurstBenchmark` (`scripts/file_burst_benchmark.py`)

**What it tests:** Concurrent throughput — how many videos LVS can summarize simultaneously while keeping average latency below a target threshold. Finds the optimal concurrency level via a linear-estimation + binary-search algorithm.

**When to use:**
- Determining the maximum sustainable throughput for your hardware
- Finding the concurrency level where latency SLA is still met
- Stress-testing LVS under production-like load
- Comparing multi-GPU topologies for throughput capacity

**How it works:**
1. For each concurrency level in `concurrency_levels`:
   a. Pre-upload N copies of the video via `POST /files` (one per concurrent worker).
   b. Launch N concurrent `POST /summarize` requests using `ThreadPoolExecutor`.
   c. Record individual processing times and calculate E2E wall-clock latency.
   d. Clean up all N uploaded files.
2. After testing all explicit concurrency levels, run the optimal-concurrency search:
   - **Phase 1 (linear estimation):** Scale concurrency linearly toward `target_latency_seconds`.
   - **Phase 2 (binary search):** Converge to within `target_latency_tolerance` seconds of the target.
3. Report `optimal_target_concurrency` — the concurrency level that best matches the target latency.

**Why pre-upload?** Each worker gets its own pre-uploaded `file_id` so download latency does not contaminate the timed burst measurement — only pure inference throughput is measured.

**Key metrics produced:**

| Metric | Description |
|---|---|
| `e2e_latency_seconds` | Wall-clock time from first request launch to last completion |
| `avg_latency` | Mean individual file processing time across all workers |
| `p90_latency` | 90th percentile individual processing time |
| `throughput_files_per_second` | `completed_files / e2e_latency_seconds` |
| `completed_files` / `failed_files` | Count of successful vs. failed concurrent requests |
| `optimal_target_concurrency.estimated_concurrency` | Best concurrency level found for `target_latency_seconds` |
| GPU metrics | Same as single_file, collected per concurrency level |

**Test case ID format:** `file_burst_{name}_{chunk_size}sec`
Example: `file_burst_5min_10sec`

**Config example:**
```yaml
file_burst_test:
  benchmark_mode: "file_burst"
  videos:
    - url: "http://HOST_IP:8888/videos/warehouse_5min.mp4"
      name: "5min"
      chunk_sizes: [10]
      concurrency_levels: [1, 2, 4, 8]
      target_latency_seconds: 300
      target_latency_tolerance: 15.0
```

---

## Choosing a Mode

| Question | Recommended Mode |
|---|---|
| What is my baseline latency for a single video? | `single_file` |
| How does chunk size affect latency? | `single_file` with multiple `chunk_sizes` |
| What is the maximum concurrent throughput my hardware supports? | `file_burst` |
| What concurrency level meets a 5-minute average latency SLA? | `file_burst` with `target_latency_seconds: 300` |
| Is my VLM or LLM the bottleneck? | `single_file` (analyze VLM pipeline % of E2E) |
| Does adding GPUs improve throughput linearly? | `file_burst` (compare across hardware configs) |

---

## Running Both Modes Together

Both scenarios can be run in sequence in a single command:

```bash
python vss_perf_benchmark.py --config config.yaml \
  --scenario single_file_test file_burst_test
```

Or use `run_benchmark.sh` without `--scenario` to run all scenarios defined in `config.yaml`:

```bash
./scripts/run_benchmark.sh
```
