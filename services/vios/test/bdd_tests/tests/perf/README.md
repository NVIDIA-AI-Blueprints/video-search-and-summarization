# VST Performance Tests - API Latency Testing

Comprehensive latency testing for the VST API matching the original latency test script.

## Overview

The performance test suite provides comprehensive latency measurements across **37 test scenarios**:

- **Video Downloads:** 15s, 30s, 60s clips with various offsets (200ms to 10000ms)
- **Picture API:** Live pictures with/without overlay
- **Transcode Options:** With and without transcoding/overlay
- **Concurrent Load:** 5, 10, 20, 50 parallel requests

Each scenario runs multiple iterations (default: 10) for statistical accuracy.

## Test: `test_api_latency`

**File:** `test_latency.py`

A single comprehensive test that executes **37 internal test scenarios** measuring latency for:

### Video Download Tests (17 scenarios)

**8 without transcode + 9 with transcode = 17 total**

**Without Transcode:**
- 15s clips: 2000ms, 1000ms, 500ms, 200ms offsets (very recent)
- 30s clips: 1000ms offset (very recent), 10000ms offset (recent)
- 60s clips: 1000ms offset (very recent), 10000ms offset (recent)

**With Transcode:**
- 15s clips: 2000ms, 1000ms, 500ms, 200ms, 10000ms offsets
- 30s clips: 1000ms offset (very recent), 10000ms offset (recent)
- 60s clips: 1000ms offset (very recent), 10000ms offset (recent)

### Picture API Tests (4 scenarios)

- 1000ms offset (very recent), without overlay
- 10000ms offset (recent), without overlay
- 1000ms offset (very recent), with overlay
- 10000ms offset (recent), with overlay

### Concurrent Video Download Tests (8 scenarios)

**Without Transcode:**
- 5, 10, 20, 50 concurrent requests

**With Transcode:**
- 5, 10, 20, 50 concurrent requests

### Concurrent Picture API Tests (8 scenarios)

**Without Overlay:**
- 5, 10, 20, 50 concurrent requests

**With Overlay:**
- 5, 10, 20, 50 concurrent requests

## Running the Test

### Basic Execution

```bash
# Run with default iterations (10 per scenario, from config.json)
poetry run pytest tools/bdd_tests/tests/perf/test_latency.py -v

# Run with custom iterations (20 per scenario)
poetry run pytest tools/bdd_tests/tests/perf/test_latency.py --perf-iterations 20 -v

# Run with minimal iterations for quick test (5 per scenario)
poetry run pytest tools/bdd_tests/tests/perf/test_latency.py --perf-iterations 5 -v

# With detailed logging
poetry run pytest tools/bdd_tests/tests/perf/test_latency.py --perf-iterations 10 -v --log-cli-level=INFO

# Run all performance tests
poetry run pytest tools/bdd_tests/tests/perf/ -v
```

### Using Markers

```bash
# Run only performance tests
poetry run pytest -m perf -v

# Exclude performance tests
poetry run pytest -m "not perf" tests/
```

### Important: Iterations vs Repetitions

**Use `--perf-iterations` to control iterations per scenario:**
```bash
poetry run pytest tests/perf/test_latency.py --perf-iterations 20
# Runs 37 scenarios × 20 iterations = 740 total API calls
# Generates 1 CSV file
```

**Do NOT use `--count` with latency tests:**
```bash
poetry run pytest tests/perf/test_latency.py --count 3  # ❌ NOT RECOMMENDED
# Runs the entire test 3 times
# Generates 3 separate CSV files (duplicates)
```

The latency test already has built-in iterations for statistical accuracy. Use `--perf-iterations` to control how many iterations per scenario, not `--count`.

## Test Output

### Console Logging

Real-time progress for each test scenario:
```text
rtsp video-download 15s offset=2000ms Very recent without-transcode: avg=1234ms p50=1200ms p90=1500ms p99=1800ms max=2000ms (10/10 pass)
file picture-api offset=1000ms Very recent with-overlay: avg=234ms p50=220ms p90=280ms p99=320ms max=350ms (10/10 pass)
```

### CSV Export

Results saved to: `reports/latency/api_latency_comprehensive_YYYYMMDD_HHMMSS.csv`

**View Results:**
```bash
# Interactive HTML viewer (recommended)
xdg-open reports/latency/latency_viewer.html

# Or open CSV directly
libreoffice reports/latency/api_latency_comprehensive_*.csv
```

**CSV Columns:**
- Stream UUID
- Stream Type (rtsp/file)
- API (video-download/picture-api)
- Clip Duration
- Offset(ms)
- Recency pattern
- Transcode option
- Result (PASS/FAIL/PARTIAL)
- Avg Latency(ms)
- P50(ms)
- P90(ms)
- P99(ms)
- Max(ms)
- Pass Count
- Fail Count

**Latency Viewer Features:**
- Interactive charts showing latency trends
- Filter by API type or result status
- Sort by any column
- Summary statistics (pass rate, avg latency, P99)
- Export filtered results to CSV

**Example CSV Data:**
```csv
Stream UUID,Stream Type,API,Clip Duration,Offset(ms),Recency pattern,Transcode option,Result,Avg Latency(ms),P50(ms),P90(ms),P99(ms),Max(ms),Pass Count,Fail Count
abc-123...,rtsp,video-download,15s,2000,Very recent,without-transcode,PASS,1234,1200,1500,1800,2000,10,0
def-456...,file,picture-api,NA,1000,Very recent,with-overlay,PASS,234,220,280,320,350,10,0
```

### HTML Report

Integrated into pytest HTML report: `reports/report.html`

## Metrics Explained

### Response Times (Latency)

- **Avg (ms):** Average response time across all iterations
- **P50 (ms):** Median - 50% of requests faster than this
- **P90 (ms):** 90% of requests faster than this
- **P99 (ms):** 99% of requests faster than this
- **Max (ms):** Slowest request

All percentiles use **industry-standard linear interpolation** (same as numpy, Apache Bench, JMeter).

### Test Results

- **PASS:** All iterations succeeded (100% success rate for that scenario)
- **FAIL:** All iterations failed (0% success rate for that scenario)
- **PARTIAL:** Some iterations passed, some failed (mixed success rate)

### Pass Criteria (Informational)

The test **always passes** - it's designed to collect latency metrics, not enforce strict thresholds.

**Purpose:** Measure and track latency trends over time  
**Behavior:** Logs warnings for FAIL/PARTIAL scenarios but test always passes  
**Target:** All 37 scenarios PASS (100% success rate) - logged as warning if not met

When a scenario fails, the warning message shows:
- Which scenario failed (API type, duration, offset, transcode option)
- Stream type (rtsp/file)
- Pass/fail count (e.g., "passed: 7/10")
- Latency metrics (avg, p90, p99)

**Example warning message (test still passes):**
```text
============================================================
LATENCY TEST - SOME SCENARIOS DID NOT PASS
============================================================
Failed/Partial Scenarios:
  - video-download duration=15s offset=200ms with-transcode [file]: PARTIAL 
    (passed: 7/10, avg=2345ms, p90=2800ms, p99=3200ms)
  - picture-api duration=NA offset=1000ms with-overlay [rtsp]: FAIL 
    (passed: 0/10, avg=500ms, p90=550ms, p99=600ms)

Summary:
  Target: All 37 scenarios PASS (100% success rate)
  Actual: 32 PASS, 1 FAIL, 4 PARTIAL
  Pass Rate: 86.5%

Check CSV for full details: reports/latency/api_latency_comprehensive_*.csv
============================================================

Note: Test PASSED (latency tests collect metrics, not enforce pass/fail)
```

## Configuration

Test parameters are in `config.json` under `tests.performance_tests.test_parameters`:

```json
{
  "performance_tests": {
    "test_parameters": {
      "latency_test_iterations": 10,
      "timeout": 120,
      "temp_perf_dir": "/tmp/vst_perf_tests",
      "auto_cleanup_after_test": true
    }
  }
}
```

**Parameters:**
- `latency_test_iterations` - Number of iterations per scenario (default: 10)
  - Can be overridden with `--perf-iterations` flag
  - 37 scenarios × 10 iterations = 370 total API calls
- `timeout` - Request timeout in seconds
- `temp_perf_dir` - Temporary directory for test files
- `auto_cleanup_after_test` - Auto-cleanup temp files (default: true)
- `latency_limits` - Benchmark limits for each test category:
  - `video_download` - Limits for video download tests
    - `max_p99_ms`: Maximum acceptable P99 latency (default: 30000ms)
    - `max_avg_ms`: Maximum acceptable average latency (default: 15000ms)
    - `min_success_rate_per_scenario`: Minimum success rate (default: 90%)
  - `picture_api` - Limits for picture API tests
    - `max_p99_ms`: 5000ms
    - `max_avg_ms`: 2000ms
    - `min_success_rate_per_scenario`: 90%
  - `concurrent_video` - Limits for concurrent video tests
    - `max_p99_ms`: 60000ms
    - `max_avg_ms`: 30000ms
    - `min_success_rate_per_scenario`: 80%
  - `concurrent_picture` - Limits for concurrent picture tests
    - `max_p99_ms`: 10000ms
    - `max_avg_ms`: 5000ms
    - `min_success_rate_per_scenario`: 80%

## Stream Requirements

The test requires:
- **RTSP streams** or **file-based streams** with timelines
- File-based streams must have timeline ≥ 70 seconds
- At least one valid stream available

## Test Duration

Expected runtime: **15-30 minutes** depending on:
- Number of available streams
- Network latency
- Server response times
- Transcode processing time

The test runs 37 scenarios with 10 iterations each = 370 individual API calls.

## Cleanup

The test automatically cleans up:
- Temporary video files
- Temporary directories

Note: This test does NOT upload files (uses existing streams), so no stream cleanup needed.

## Troubleshooting

### No Streams Found

**Error:** "No valid streams found"

**Solutions:**
- Ensure RTSP streams are configured
- Or upload files to create file-based streams with timelines
- Check `/api/v1/sensor/streams` endpoint returns data

### Timeline Too Short

**Warning:** "Skipped: stream-id (file, timeline too short: 30000ms)"

**Solution:**
- Upload longer videos (≥ 70 seconds)
- Or use RTSP streams which don't require timelines

### Video Validation Failures

**Issue:** Downloaded videos fail mediainfo validation

**Check:**
- Offset values aren't beyond timeline bounds
- Server has sufficient recording history
- Video encoding is valid H.264/MP4

### Slow Test Execution

If the test is taking too long:
- Reduce iterations (modify test code)
- Test with fewer concurrent scenarios
- Check network bandwidth

## Sample Results

```
============================================================
Latency Test Summary:
  Total: 40 | Passed: 38 | Failed: 0 | Partial: 2
  Pass Rate: 95.0%
============================================================

CSV results saved to: reports/api_latency_comprehensive_20260122_143022.csv
```

## Comparing with Other Tests

| Test Type | Purpose | Duration |
|-----------|---------|----------|
| Functional Tests | Validate features work correctly | 1-5 min |
| Latency Test | Measure response times under various conditions | 15-30 min |

The latency test is **complementary** to functional tests - functional tests verify correctness, latency tests measure performance.
