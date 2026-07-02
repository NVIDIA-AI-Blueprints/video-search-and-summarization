# Analyzing Benchmark Results

This reference documents the output file structure, how to read XLSX reports, key metrics to watch, bottleneck identification patterns, and rule-based improvement suggestions with examples.

---

## Output File Structure

After a benchmark run, results are organized as follows:

```
vss-perf-report/                                    # output_dir from config.yaml
├── vss_perf_benchmark_log.txt                       # Full debug log for the entire run
├── single_file_test_single_file_report.xlsx         # XLSX report for single_file scenario
├── file_burst_test_file_burst_report.xlsx           # XLSX report for file_burst scenario
│
├── single_file_test/                               # single_file scenario directory
│   ├── execution_summary.json                       # Top-level summary with all test case results
│   ├── single_file_1min_10sec/                      # One directory per test case
│   │   ├── test_case_summary.json                   # Aggregated stats across iterations (mean ± std%)
│   │   ├── iteration_1/                             # Per-iteration data
│   │   │   ├── metrics.json                         # Server-side /metrics scrape (latency breakdown)
│   │   │   ├── summarize_response.json              # Raw /summarize API response
│   │   │   ├── summarize_response_formatted.json    # Parsed events content
│   │   │   ├── test_case_data.json                  # Input configuration for this iteration
│   │   │   ├── gpu_metrics_iter_1_stats.json        # GPU utilization/memory stats
│   │   │   └── cpu_metrics_iter_1_stats.json        # CPU/RAM stats
│   │   ├── iteration_2/
│   │   └── iteration_3/
│   └── single_file_1min_30sec/                      # Next test case (30s chunks)
│       └── ...
│
└── file_burst_test/                                 # file_burst scenario directory
    ├── execution_summary.json
    └── file_burst_5min_10sec/
        ├── file_burst_results.json                  # Full concurrency sweep + optimal search results
        ├── gpu_metrics_concurrency_1_stats.json     # GPU stats at concurrency=1
        ├── gpu_metrics_concurrency_2_stats.json     # GPU stats at concurrency=2
        └── ...
```

---

## Reading XLSX Reports

Each XLSX report has multiple sheets:

### Single File Report Sheets

| Sheet | Content |
|---|---|
| **Summary** | One row per test case; mean ± std% for all metrics across iterations |
| **GPU_Info** | Hardware specifications (GPU name, memory, compute capability, driver version) |
| **All Iterations** | One row per successful iteration across all test cases |
| **single_file_1min_10sec** | Rows for iterations of this specific test case only |
| *(one sheet per test case)* | ... |

The Summary sheet also contains embedded latency plots (generated with matplotlib) showing E2E, VLM pipeline, and CA-RAG latency trends.

### File Burst Report Sheets

| Sheet | Content |
|---|---|
| **Summary** | One row per concurrency level per test case |
| **GPU_Info** | Hardware specifications |

Each row in the Summary sheet has concurrency_level, E2E latency, throughput, avg/P90 individual latency, and GPU metrics.

---

## Key Metrics to Watch

### Single File Tests

**Latency breakdown (from `/metrics` scrape):**

```
E2E Latency = Decode Latency + VLM Pipeline Latency + CA-RAG Latency
```

| Metric Key | JSON Path | Normal Range |
|---|---|---|
| `e2e_latency` | `api_metrics.e2e_latency_seconds_latest` | Varies by video length and hardware |
| `vlm_pipeline_latency` | `api_metrics.vlm_pipeline_latency_seconds_latest` | 60–80% of E2E is typical |
| `ca_rag_latency` | `api_metrics.ca_rag_latency_seconds_latest` | < 20% of E2E is healthy |
| `decode_latency` | `api_metrics.decode_latency_seconds_latest` | Small — typically < 5% |

**GPU utilization (from NVML monitoring):**

| Metric Key | JSON Path | Healthy Range |
|---|---|---|
| `vlm_gpu_usage_mean` | `gpu_stats.GPU{n}.usage.mean` | 70–95% — utilization under 70% means VLM is under-utilized |
| `llm_gpu_usage_mean` | `gpu_stats.GPU{n}.usage.mean` | 40–80% — LLM waits on VLM output |
| `vlm_gpu_memory_mean` | `gpu_stats.GPU{n}.memory.mean` | Keep below 85% to avoid OOM risk |

### File Burst Tests

| Metric Key | Healthy Signal |
|---|---|
| `throughput_files_per_second` | Increasing with concurrency until hardware saturation |
| `avg_latency` | Approaches `target_latency_seconds` at optimal concurrency |
| `p90_latency` | Should be within ~20% of avg_latency; high variance = instability |
| `failed_files` | Should be 0; any failures indicate OOM or server overload |
| `optimal_target_concurrency.estimated_concurrency` | Use this as the production max concurrency setting |

---

## Bottleneck Identification Patterns

### Pattern 1: VLM-Bound (Most Common)

**Signals:**
- `vlm_pipeline_latency / e2e_latency > 70%`
- `vlm_gpu_usage_mean > 90%`
- `llm_gpu_usage_mean < 50%` (LLM is waiting)

**Example output:**
```
e2e_latency: 285s
vlm_pipeline_latency: 210s  (73.7% of E2E)
ca_rag_latency: 15s
vlm_gpu_usage_mean: 94%
llm_gpu_usage_mean: 35%
```

**Recommended actions:**
- Reduce `vlm_input_width`/`vlm_input_height` to lower vision token count (9k → 4k → 2k)
- Reduce `num_frames_per_chunk` (20 → 10)
- Add VLM GPU replicas if hardware permits

### Pattern 2: LLM-Bound

**Signals:**
- `ca_rag_latency / e2e_latency > 30%`
- `llm_gpu_usage_mean > 90%`
- `vlm_gpu_usage_mean < 60%`

**Example output:**
```
e2e_latency: 320s
vlm_pipeline_latency: 140s  (43.8% of E2E)
ca_rag_latency: 165s  (51.6% of E2E)
llm_gpu_usage_mean: 93%
```

**Recommended actions:**
- Add LLM GPU replicas
- Reduce `max_tokens` if output quality is acceptable
- Enable LLM quantization (FP8/INT4) in the deployment

### Pattern 3: Memory Pressure

**Signals:**
- `vlm_gpu_memory_mean > 85%` or `llm_gpu_memory_mean > 85%`
- High iteration-to-iteration variance in latency (std% > 20%)
- `failed_files > 0` in file_burst

**Example output:**
```
vlm_gpu_memory_mean: 91%
e2e_latency std%: 28%  (high variance across iterations)
```

**Recommended actions:**
- Reduce `num_frames_per_chunk`
- Reduce concurrency in file_burst tests
- Check for memory leaks — monitor memory trend across iterations

### Pattern 4: Elasticsearch Bottleneck

**Signals:**
- `ca_rag_latency > 20%` of E2E with short videos (where VLM overhead should be small)
- Elasticsearch container shows high CPU or I/O in `docker stats`

**Example output:**
```
e2e_latency (1min video): 45s
ca_rag_latency: 12s  (26.7% of E2E for a short video)
```

**Recommended actions:**
- Increase Elasticsearch heap: `ES_JAVA_OPTS="-Xms4g -Xmx4g"` in deployment
- Check Elasticsearch health: `curl http://localhost:9200/_cluster/health`
- Ensure fast storage (SSD/NVMe) for Elasticsearch data directory

### Pattern 5: Decode Bottleneck (Rare)

**Signals:**
- `decode_latency / e2e_latency > 10%`
- `vlm_nvdec_usage_mean > 80%`

**Recommended actions:**
- Enable hardware video decode (NVDEC) if not already — check container capabilities
- Reduce video resolution before benchmarking

---

## Rule-Based Improvement Suggestions

Use this table when presenting recommendations to the user after analyzing results:

| Observation | Rule | Recommended Setting Change |
|---|---|---|
| VLM GPU < 70% mean utilization | VLM under-utilized | Increase `chunk_duration` (e.g., 10s → 30s) or `num_frames_per_chunk` (20 → 40) |
| VLM pipeline > 70% of E2E | VLM dominates — resolution overhead | Reduce `vlm_input_width`/`vlm_input_height` (1312x736 → 896x504 for ~4k tokens) |
| GPU memory mean > 85% | Near OOM threshold | Reduce `num_frames_per_chunk`; avoid increasing concurrency further |
| File burst: throughput plateaus between C and C+1 | Hardware saturated | Concurrency C is the recommended production maximum |
| High iteration variance (std% > 20%) | Thermal or memory pressure | Allow longer cooldown between iterations; check GPU temps |
| CA-RAG > 20% of E2E | Elasticsearch or LLM I/O | Check Elasticsearch health; increase heap size |
| LLM GPU < 50% utilization with VLM > 80% | VLM is the bottleneck | Fix VLM settings before tuning LLM |
| `failed_files > 0` in burst | OOM or server overload | Reduce max concurrency level; check container memory limits |

---

## Reading execution_summary.json

The top-level structure for single_file:

```json
{
  "scenario_name": "single_file_test",
  "benchmark_mode": "single_file",
  "scenario_dir": "vss-perf-report/single_file_test",
  "test_cases": [
    {
      "test_case_id": "single_file_5min_10sec",
      "video_url": "http://HOST_IP:8888/videos/warehouse_5min.mp4",
      "chunk_size": 10,
      "iterations": 3,
      "successful_iterations": 3,
      "success": true,
      "iteration_results": [
        {
          "iteration": 1,
          "success": true,
          "wall_clock_seconds": 14.2,
          "api_metrics": {
            "e2e_latency_seconds_latest": 13.8,
            "vlm_pipeline_latency_seconds_latest": 9.1,
            "ca_rag_latency_seconds_latest": 2.1,
            "decode_latency_seconds_latest": 0.4
          },
          "gpu_metrics": {
            "vlm_gpu_usage_mean": 82.5,
            "llm_gpu_usage_mean": 45.2
          }
        }
      ]
    }
  ],
  "total_test_cases": 4,
  "successful_test_cases": 4,
  "failed_test_cases": 0
}
```

For file_burst, read `file_burst_results.json` inside each test case directory:

```json
{
  "test_case_id": "file_burst_5min_10sec",
  "concurrency_levels_tested": [1, 2, 4, 8, 12],
  "concurrency_results": [
    {
      "concurrency_level": 4,
      "e2e_latency_seconds": 285.4,
      "completed_files": 4,
      "failed_files": 0,
      "throughput_files_per_second": 0.014,
      "avg_latency": 272.3,
      "p90_latency": 289.1,
      "vlm_gpu_usage_mean": 88.2
    }
  ],
  "optimal_target_concurrency": {
    "target_latency_seconds": 300,
    "estimated_concurrency": 5,
    "actual_result": { "avg_latency": 294.2 }
  }
}
```
