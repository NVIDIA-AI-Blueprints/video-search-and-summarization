# P1 Functional Tests

Controlled functional tests for Alert Bridge. Each test sends a crafted incident through a local simulator stack and validates the output document in Elasticsearch.

These run against simulators with known inputs — not a live deployment.

## Quick Start

```bash
# Run all tests (setup + run + cleanup)
./run_p1.sh

# Run a single test
./run_p1.sh --test test_redis_dedup

# Skip setup if simulators are already running
./run_p1.sh --skip-setup

# Keep simulators running after tests
./run_p1.sh --skip-cleanup
```

## Tests

| Test | Description | What It Validates |
|------|-------------|-------------------|
| `test_document_parity` | Send an incident; verify output doc is in the correct dated ES index with `sensorId` preserved | Field pass-through and index routing |
| `test_verdict_distribution` | Send 3 incidents with unique timestamps (unique fingerprints); verify all receive non-null VLM verdicts | VLM classifies each independently (not deduped) |
| `test_redis_dedup` | Send the same incident twice with identical ID; verify only 1 doc is indexed | Redis deduplication is active |
| `test_async_smoke` | Restart AB with async external I/O guardrails enabled; send one incident; verify output doc and async guardrail log | Async guardrails can be enabled without breaking end-to-end flow |
| `test_async_verdict_parity` | Run one sync incident + one async incident with same payload shape; compare verdict/status signature | Async mode preserves sync verdict/status behavior |
| `test_async_dedup_parity` | Run duplicate-incident dedup check in sync then async mode; compare indexed document count | Async Redis wrapper preserves dedup semantics |
| `test_async_kafka_non_blocking` | Inject fixed VLM delay, send a burst of incidents, and verify async dispatch queueing continues before first delayed response | Kafka consume/scheduling is decoupled from slow VLM I/O in async mode |
| `test_vst_video_url` | Send an incident; verify output doc contains a `videoUrl`/`videoSource` referencing the VST simulator | VST integration and URL extraction |
| `test_document_schema` | Send an incident; verify output doc has all required fields | Full schema completeness |
| `test_vlm_error_codes` | Stop the NIM simulator; send an incident; verify non-200 `verificationResponseCode` | Error handling when VLM is unavailable |
| `test_verdict_protection` | Send incident → confirmed; wait for dedup TTL (3s) to expire; resend same payload (same fingerprint); verify protection blocks re-verification | `protect_confirmed_verdicts` flag behavior |
| `test_http_json_payload` | POST JSON incident to REST API; verify HTTP 202 and document in ES with matching sensorId | REST API accepts JSON; full pipeline processes it |
| `test_http_protobuf_payload` | POST protobuf-serialized incident to REST API; verify HTTP 202 and document in ES with matching sensorId | REST API accepts protobuf; full pipeline processes it |
| `test_enrichment_processing` | Send collision incident with enrichment enabled; verify `info.enrichment` field appears in ES doc | Post-verification async enrichment VLM call runs when enabled |
| `test_end_time_delta_filter` | Send incident #1 (end=T); wait for dedup TTL; send #2 (end=T+2s, delta < 5s threshold); verify only 1 doc | End time delta filter blocks trivial end-time updates |
| `test_isComplete_flag_filtering` | Send incident with `isComplete=false`, then one with `isComplete=true`; verify only the complete one appears in ES | `verify_only_finished_events` skips non-finished events |
| `test_custom_category_mapping` | Send collision incident; verify ES doc `category` shows mapped value "Vehicle Collision" | `output_category` in alert_type_config.json transforms ES category field |
| `test_vlm_warmup` | Read AB log; verify "Starting VLM warmup" appears before "Starting anomaly processing loop" | Warmup runs before alert processing begins |
| `test_vst_segment_start` | Send incident with 30s window; verify VST is requested with startTime=T, endTime=T+10s | `segment_anchor=start` window computation |
| `test_vst_segment_end` | Send incident with 30s window; verify VST is requested with startTime=T+20s, endTime=T+30s | `segment_anchor=end` window computation |
| `test_vst_segment_middle` | Send incident with 30s window; verify VST is requested with startTime=T+10s, endTime=T+20s | `segment_anchor=middle` window computation |
| `test_json_response_format` | Set `response_format: "json"`; NIM returns flat JSON (`prediction_answer`/`reasoning`); verify ES doc has `verdict=confirmed` and correct `vlm_response` | JSON response parsing with default field names |
| `test_json_cookbook_format` | Set `response_format: "json"` with `json_parser` (dot-notation `verdict_field`, boolean `verdict_mapping`); NIM returns cookbook-style nested JSON; verify ES doc | JSON response parsing with CR2 cookbook nested fields |
| `test_direct_media_download` | Send incident with `info.media_urls`; verify AB downloads media and processes via Mode 3 | Direct media URL download bypasses VST |
| `test_http_ondemand_verification` | POST to `/api/v1/verification/ondemand`: (1) valid request → 200; (2) unknown alert_type → 400; (3) NIM down → 503 | On-demand verification API contract, error handling, and VLM fault tolerance |
| `test_kafka_sink_vlm` | Send incident with `info.video_path`; verify VLM result published to Kafka sink | Base64 encode + VLM + Kafka sink pipeline |
| `test_realtime_replay` | 8 sub-tests for `POST /api/v1/realtime/replay`: happy-path, partial RTVI failure, concurrent 409, POST/DELETE blocked 503, GET available during replay, persistence-disabled 501, AB restart state survival | Replay API contract, concurrency guards, persistence fallback, durability |

## Structure

Each test lives in its own directory with two files:

```
test/functional/p1/
├── run_p1.sh                    # Orchestrator (setup / run / cleanup)
├── shared/                      # Shared helpers (produce_incident, poll_es_sim, etc.)
├── test_document_parity/
│   ├── config.yaml              # Alert Bridge config for this test
│   └── run.sh                   # Test logic
├── test_redis_dedup/
│   ├── config.yaml
│   └── run.sh
└── ...
```

The orchestrator starts Alert Bridge fresh with each test's `config.yaml` before calling its `run.sh`.

## Flags

| Flag | Description |
|------|-------------|
| `--test <name>` | Run only the named test directory (e.g. `test_redis_dedup`) |
| `--skip-setup` | Skip Phase 1 — assume simulators and Kafka are already running |
| `--skip-cleanup` | Skip Phase 3 — leave simulators and containers running after tests |
| `--help` | Show usage |

## Per-Test Details

### test_document_parity

**Purpose:** Verify that a Kafka incident flows through Alert Bridge and lands in the correct date-partitioned Elasticsearch index with `sensorId` preserved from the input payload.

**Trigger:** One incident produced to `mdx-incidents` topic with a timestamped ID suffix.

**Check:** Poll ES simulator until a document appears. Assert `sensorId` matches the input payload. Assert document is in `mdx-vlm-incidents-<YYYY-MM-DD>` index.

**Pass:** `sensorId` matches and index name is correct.
**Fail:** `sensorId` missing, mismatched, or document not in expected index within 60s.

---

### test_verdict_distribution

**Purpose:** Verify the VLM runs and returns a non-null verdict for each incident processed.

**Trigger:** 3 incidents each with a unique timestamp (+1s, +2s, +3s) creating unique fingerprints to avoid dedup. Different ID suffixes (`p1_verdict_1`, `p1_verdict_2`, `p1_verdict_3`).

**Check:** After 15s, retrieve all documents from ES simulator. Assert the last 3 documents all have non-null `info.verdict`.

**Pass:** All 3 docs have a non-empty verdict value.
**Fail:** Any verdict is null or missing.

---

### test_redis_dedup

**Purpose:** Verify the Redis deduplication layer drops a duplicate incident that carries an identical ID suffix.

**Trigger:** The same incident produced twice with the fixed ID suffix `p1_dedup_fixed`.

**Check:** Compare ES document count before and after. Assert exactly 1 new document was added (not 2).

**Pass:** Exactly 1 new document indexed.
**Fail:** 0 documents (both dropped) or 2+ documents (dedup did not fire).

---

### test_vst_video_url

**Purpose:** Verify Alert Bridge calls the VST simulator and writes the returned video URL into the output document.

**Trigger:** One incident produced to Kafka.

**Check:** Poll ES simulator until a document appears. Extract `info.videoUrl` or `info.videoSource`. Assert the URL contains `127.0.0.1:30888` (VST simulator address).

**Pass:** Video URL is present and references the VST simulator host.
**Fail:** Video URL field is missing or does not reference the VST simulator.

---

### test_document_schema

**Purpose:** Verify the output document contains all required fields after full pipeline processing.

**Required fields:** `sensorId`, `category`, `timestamp`, `info.verdict`, `info.vlm_response`, `info.verificationResponseCode`, `info.verificationResponseStatus`

**Trigger:** One incident produced to Kafka.

**Check:** Poll ES simulator until a document appears. Assert all required fields are non-null and non-empty.

**Pass:** All required fields present.
**Fail:** Output lists the specific missing field names.

---

### test_vlm_error_codes

**Purpose:** Verify that when the VLM (NIM) service is unavailable, Alert Bridge records a non-200 `verificationResponseCode` rather than silently dropping or misrepresenting the failure.

**Trigger:** NIM simulator is stopped. One incident is produced.

**Check:** Poll ES simulator (up to 90s, extended for retries). If a document appears, assert `info.verificationResponseCode` is not 200. Null and non-numeric codes also pass.

**Pass:** Document has a non-200 response code, OR no document appeared (AB may drop on VLM failure — skip).
**Fail:** Document appeared with `verificationResponseCode: 200` while NIM was down.
**Skip:** NIM sim PID file not found (not managed by this harness), or no document appeared within 90s.

NIM simulator is automatically restarted after this test regardless of outcome.

---

### test_verdict_protection

**Purpose:** Verify the `protect_confirmed_verdicts` feature prevents re-processing when a confirmed incident is resubmitted after dedup TTL expires.

**Trigger:** Send incident #1 → wait for confirmed verdict. Wait 5s for dedup TTL (3s) to expire. Resend same payload (same fingerprint) as incident #2. Dedup key expired → passes dedup → hits verdict protection → should be blocked.

**Check:** Count ES docs before and after second send. Assert no new document appeared (protection blocked it). Verified via AB log: "Skipping processing: confirmed verdict exists".

**Pass:** Second incident blocked or preserved without a new `info.vlm_response`.
**Fail:** Second incident was re-processed with a new VLM verdict.
**Skip:** First incident did not resolve to `confirmed` with the current simulator configuration.

---

### test_http_json_payload

**Purpose:** Verify the Alert Bridge REST API accepts a JSON incident and processes it end-to-end, producing a document in Elasticsearch with the correct `sensorId`.

**Trigger:** `POST http://localhost:9080/api/v1/incidents` with `Content-Type: application/json` and the sample incident payload.

**Check:** Assert HTTP 202 response. Poll ES simulator until a document appears. Assert `sensorId` matches the input payload.

**Pass:** HTTP 202 received AND document found in ES with matching `sensorId`.
**Fail:** Non-202 response OR no document in ES within 60s OR `sensorId` mismatch.

---

### test_http_protobuf_payload

**Purpose:** Verify the Alert Bridge REST API accepts a protobuf-serialized incident and processes it end-to-end, producing a document in Elasticsearch with the correct `sensorId`.

**Trigger:** `POST http://localhost:9080/api/v1/incidents` with `Content-Type: application/x-protobuf` and the sample incident serialized as protobuf bytes.

**Check:** Assert HTTP 202 response. Poll ES simulator until a document appears. Assert `sensorId` matches the input payload.

**Pass:** HTTP 202 received AND document found in ES with matching `sensorId`.
**Fail:** Non-202 response OR protobuf serialization error OR no document in ES within 60s OR `sensorId` mismatch.

---

### test_enrichment_processing

**Purpose:** Verify the post-verification enrichment VLM call runs and writes `info.enrichment` into the ES document when `alert_agent.enrichment.enabled: true`.

**Config:** `alert_agent.enrichment.enabled: true`. Uses `collision` alert type which has an `enrichment` prompt defined in `alert_type_config.json`.

**Trigger:** One collision incident produced to Kafka.

**Check:** Wait 20s (extra time for async post-publish enrichment), then poll ES. Assert `info.enrichment` field is present in the document. Any `responseCode` value (200, 500, etc.) counts as present.

**Pass:** `info.enrichment` field exists in the ES document.
**Fail:** `info.enrichment` field is completely absent after verification completed.

---

### test_end_time_delta_filter

**Purpose:** Verify the end time delta filter blocks incident updates where the `end` timestamp has not changed significantly (below threshold).

**Config:** `event_bridge.redis_source.end_time_delta_filter.enabled: true`, `threshold_seconds: 5`.

**Trigger:**
1. Incident #1 sent with `end=T` — first seen, passes delta filter and dedup, stored in Redis.
2. Wait 10s — incident #1 is processed and the dedup TTL (3s) expires.
3. Incident #2 sent with identical payload except `end=T+2s` — delta (2s) is below the 5s threshold.

**Check:** Incident #2 passes dedup (TTL expired) but is blocked by the delta filter. Count docs before and after incident #2. Assert count is unchanged.

**Pass:** Doc count unchanged after incident #2 (delta filter blocked it).
**Fail:** New doc appeared after incident #2 (delta filter did not fire).

---

### test_isComplete_flag_filtering

**Purpose:** Verify that when `verify_only_finished_events: true`, incidents with `info.isComplete=false` are skipped and only incidents with `info.isComplete=true` are processed.

**Config:** `alert_agent.verify_only_finished_events: true`.

**Trigger:**
1. Incident #1 with `info.isComplete: false` and timestamp T1 — should be skipped.
2. Incident #2 with `info.isComplete: true` and timestamp T2 (T1+60s, distinct fingerprint) — should be processed.

**Check:** Assert no new doc appeared after incident #1. Assert at least one new doc appeared after incident #2.

**Pass:** Incomplete incident filtered (no doc added); complete incident processed (doc added).
**Fail:** Incomplete incident was processed OR complete incident was not processed.

---

### test_custom_category_mapping

**Purpose:** Verify that `output_category` in `alert_type_config.json` transforms the `category` field in the ES output document.

**Config:** No special config — `alert_type_config.json` already maps `collision` → `"Vehicle Collision"` via the `output_category` field.

**Trigger:** One collision incident produced to Kafka.

**Check:** Poll ES simulator until a document appears. Extract the `category` field.

**Pass:** `category` field equals `"Vehicle Collision"` (mapped value).
**Skip:** `category` field equals `"collision"` (mapping not applied — check that `alert_type_config.json` is being loaded).
**Fail:** `category` field is missing or has an unexpected value.

---

### test_vlm_warmup

**Purpose:** Verify the VLM warmup runs before alert processing begins, confirming correct startup sequencing.

**Config:** No special config. Warmup is enabled by default (`VLM_WARMUP_ENABLED=true`).

**Trigger:** None — log-based test only. AB is started by the orchestrator as usual; this test reads the log.

**Check:** Read `$PID_DIR/alert_bridge.log`. Find the line number of `"Starting VLM warmup"` and `"Starting anomaly processing loop"`. Assert warmup line precedes processing loop line.

**Pass:** `"Starting VLM warmup"` log line appears before `"Starting anomaly processing loop"` line.
**Fail:** Processing loop log line appears before warmup log line (wrong startup order).
**Skip:** Either log message is absent (warmup may be disabled via `VLM_WARMUP_ENABLED=false`, warmup video not found, or AB log not available).

---

### test_vst_segment_start

**Purpose:** Verify that when `segment_anchor=start` and `segment_duration_seconds=10`, Alert Bridge requests a VST video window starting at the incident start time and ending 10 seconds later.

**Config:** `vst_config.segment_anchor: "start"`, `vst_config.segment_duration_seconds: 10`.

**Trigger:** One incident produced with `timestamp=T` and `end=T+30s` (both 2 minutes in the past to avoid future-clamping).

**Check:** After 15s, grep `alert_bridge.log` for the `"Requesting VST video URL"` debug line (or `"VST effective window"` fallback). Parse `start=` and `end=` values. Assert `actual_start ≈ T` and `actual_end ≈ T+10s` (±2s tolerance).

**Pass:** Both timestamps within 2s of expected values.
**Fail:** Timestamps outside tolerance.
**Skip:** Log line absent or timestamps unparseable.

---

### test_vst_segment_end

**Purpose:** Verify that when `segment_anchor=end` and `segment_duration_seconds=10`, Alert Bridge requests a VST video window ending at the incident end time and starting 10 seconds before it.

**Config:** `vst_config.segment_anchor: "end"`, `vst_config.segment_duration_seconds: 10`.

**Trigger:** One incident produced with `timestamp=T` and `end=T+30s` (both 2 minutes in the past to avoid future-clamping).

**Check:** After 15s, grep `alert_bridge.log` for the `"Requesting VST video URL"` debug line. Parse `start=` and `end=` values. Assert `actual_start ≈ T+20s` and `actual_end ≈ T+30s` (±2s tolerance).

**Pass:** Both timestamps within 2s of expected values.
**Fail:** Timestamps outside tolerance.
**Skip:** Log line absent or timestamps unparseable.

---

### test_vst_segment_middle

**Purpose:** Verify that when `segment_anchor=middle` and `segment_duration_seconds=10`, Alert Bridge requests a VST video window centered on the incident midpoint, spanning ±5s around it.

**Config:** `vst_config.segment_anchor: "middle"`, `vst_config.segment_duration_seconds: 10`.

**Trigger:** One incident produced with `timestamp=T` and `end=T+30s` (both 2 minutes in the past to avoid future-clamping). Midpoint = T+15s; expected window = [T+10s, T+20s].

**Check:** After 15s, grep `alert_bridge.log` for the `"Requesting VST video URL"` debug line. Parse `start=` and `end=` values. Assert `actual_start ≈ T+10s` and `actual_end ≈ T+20s` (±2s tolerance).

**Pass:** Both timestamps within 2s of expected values.
**Fail:** Timestamps outside tolerance.
**Skip:** Log line absent or timestamps unparseable.

---

### test_json_response_format

**Purpose:** Verify that `response_format: "json"` with default field names parses a flat JSON VLM response into correct `info.verdict` and `info.vlm_response` in the Elasticsearch document.

**Config:** `vlm.response_format: "json"`. No `json_parser` override — uses defaults (`prediction_answer`, `reasoning`). Note: `reasoning_fields` is the *input* JSON key list read from the VLM output; the resulting text lands in `info.vlm_response`.

**Setup:** Kills the default NIM simulator and restarts it with `NIM_RESPONSE_FILE` pointing to `json_response.txt`, which contains `{"prediction_answer": "YES", "reasoning": "Worker detected near forklift zone..."}`.

**Trigger:** One incident produced to Kafka.

**Check:** Poll ES simulator. Assert `info.verdict == "confirmed"` (YES maps to confirmed) and `info.vlm_response` contains "forklift".

**Pass:** Verdict is `confirmed` and `vlm_response` matches.
**Fail:** Verdict or `vlm_response` does not match expected values.
**Skip:** NIM sim PID file not found (not managed by this harness).

NIM simulator is automatically restarted in default CR2 mode after this test.

---

### test_json_cookbook_format

**Purpose:** Verify that `response_format: "json"` with CR2 cookbook-style nested JSON, dot-notation `verdict_field`, and boolean `verdict_mapping` produces the correct Elasticsearch document.

**Config:** `vlm.response_format: "json"` with `json_parser`:
- `verdict_field: "hazard_detection.is_hazardous"` (dot-notation for nested field)
- `verdict_mapping: {"true": "YES", "false": "NO"}` (boolean to verdict mapping)
- `reasoning_fields: ["video_description"]`

**Setup:** Kills the default NIM simulator and restarts it with `NIM_RESPONSE_FILE` pointing to `cookbook_response.txt`, which contains cookbook-style JSON with `hazard_detection.is_hazardous: true`.

**Trigger:** One incident produced to Kafka.

**Check:** Poll ES simulator. Assert `info.verdict == "confirmed"` (`is_hazardous=true` maps to `YES` maps to `confirmed`) and `info.vlm_response` contains "safety path".

**Pass:** Verdict is `confirmed` and `vlm_response` matches.
**Fail:** Verdict or `vlm_response` does not match expected values.
**Skip:** NIM sim PID file not found (not managed by this harness).

NIM simulator is automatically restarted in default CR2 mode after this test.

---

### test_direct_media_download

**Purpose:** Verify Alert Bridge can download media directly from URLs provided in `info.media_urls` (Mode 3), bypassing VST lookup entirely.

**Config:** `alert_agent.vst_pass_through_mode: true`, `alert_agent.media_download.enabled: true`, `alert_agent.media_download.allow_private_urls: true`.

**Trigger:** One incident produced with `info.media_urls` containing a JSON array of image URLs pointing to the VST simulator's mock media endpoint.

**Check:** Poll ES simulator until a document appears. Assert `sensorId` matches. Check AB log for "Mode 3: Direct media URLs detected" to confirm the direct download path was used.

**Pass:** Document found in ES with non-empty verdict; Mode 3 log message present.
**Fail:** No document in ES within 60s, or sensorId mismatch.

---

### test_http_ondemand_verification

**Purpose:** Verify the on-demand verification endpoint (`POST /api/v1/verification/ondemand`) — happy path, error handling for unknown alert types, and graceful degradation when VLM is unavailable.

**Config:** Uses dedicated test config `test_http_ondemand_verification/config.yaml` (does not use `shared/config_base.yaml`).

**Sub-tests:**

| # | Scenario | Trigger | Expected |
|---|----------|---------|----------|
| 1 | Happy path | `alert_type: "collision"` + valid `media_path` | HTTP `200`, `status=success`, non-empty `verification` |
| 2 | Unknown alert_type | `alert_type: "nonexistent_type_xyz"` | HTTP `400`, `error=unknown_alert_type` |
| 3 | VLM unavailable | NIM simulator stopped, valid request | HTTP `503`, `error=vlm_unavailable` |

**Pass:** All three sub-tests return expected HTTP codes and error structures.
**Fail:** Any sub-test returns unexpected HTTP code or response body.
**Skip (sub-test 3 only):** NIM sim PID file not found (not managed by this harness). NIM is automatically restarted after sub-test 3.

---

### test_kafka_sink_vlm

**Purpose:** Verify Alert Bridge can process a local video file (Mode 2), encode it to base64, send to VLM for verification, and publish the VLM-enhanced result to a Kafka sink instead of Elasticsearch.

**Config:** `alert_agent.vst_pass_through_mode: true`, `vlm_enhanced_sink.incident.type: "kafka"`, `elastic.enabled: false`.

**Trigger:** One incident produced with `info.video_path` pointing to a local mock video file. The test creates/downloads a minimal video file before producing the incident.

**Check:** Consume from the Kafka output topic (`mdx-vlm-incidents`). Deserialize messages (JSON or protobuf). Assert a message with matching `sensorId` exists and contains VLM response fields.

**Pass:** Message found in Kafka output topic with matching sensorId and VLM verdict.
**Fail:** No messages in Kafka topic, or no message matched the test sensor.

---

### test_async_smoke

**Purpose:** Verify async external I/O guardrails (`alert_agent.async_io.*`) can be enabled and still process incidents end-to-end.

**Trigger:** Build an async-enabled config from `shared/config_base.yaml`, restart AB with that config, send one incident.

**Check:** Poll ES for the test `sensorId`; assert `info.verdict`, `info.verificationResponseCode`, and `info.verificationResponseStatus` are present. Confirm AB log contains `"Async external I/O guardrail is enabled"`.

**Pass:** Document appears with required fields and async guardrail log line exists.
**Fail:** Missing document, missing required fields, or async guardrail log line absent.

---

### test_async_verdict_parity

**Purpose:** Verify async mode returns the same verdict/status signature as sync mode for the same incident shape.

**Trigger:** Run one incident in sync mode and one incident in async mode (AB restarted between runs), using identical payload structure and fixed timestamp.

**Check:** Compare signatures:
- `info.verdict`
- `info.verificationResponseCode`
- `info.verificationResponseStatus`

**Pass:** All signature fields are identical between sync and async runs.
**Fail:** Any signature field differs.

---

### test_async_dedup_parity

**Purpose:** Verify Redis dedup behavior is unchanged when dedup path runs through async wrapper.

**Trigger:** Run duplicate incident (same ID suffix twice) in sync mode, then repeat in async mode (AB restarted between runs; Redis/ES state reset between modes).

**Check:** For both modes, compute `docs_added = after - before`.

**Pass:** `docs_added == 1` for sync and async, and counts match.
**Fail:** Either mode indexes not-equal-to-1 documents, or sync/async counts differ.

---

### test_async_kafka_non_blocking

**Purpose:** Verify that in async mode, Kafka consumption/dispatch continues even while a VLM request is blocked on slow I/O.

**Setup:** Restart AB with async guardrails enabled and DEBUG logging. Restart the NIM simulator with `NIM_STUB_DELAY_SECONDS` to force slow VLM responses.

**Trigger:** Produce a burst of incidents with unique `sensorId` values.

**Check:** In `alert_bridge.log`, find the first `VLM request sent` and first corresponding `VLM response received` for the test sensor prefix. Assert:
- request->response duration reflects injected delay
- `Message queued for async dispatch` / `Queueing message to async dispatch pipeline` appears multiple times while the first request is still waiting
- all burst incidents eventually reach ES

**Pass:** Queueing activity continues before the first delayed VLM response and all burst incidents are indexed.
**Fail:** No delayed response observed, no queue progression during wait window, or burst documents missing.

---

## Writing a New Test

1. Create a directory `test_<name>/` with a `run.sh`
2. Add a `config.yaml` only if your test needs non-default AB settings. Tests without a `config.yaml` use `shared/config_base.yaml` automatically.

### run.sh template

```bash
#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P1_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$P1_ROOT/../../.." && pwd)"
source "$P1_ROOT/shared/helpers.sh"

PID_DIR="${PID_DIR:-/tmp/alert_agent_p1_functional}"
ES_HOST="${ES_HOST:-http://127.0.0.1:9200}"
BOOTSTRAP="${BOOTSTRAP:-127.0.0.1:9092}"
TOPIC="${TOPIC:-mdx-incidents}"
PAYLOAD="${REPO_ROOT}/test/protobuf/test_data/sample_incident.json"

echo "=== P1: My Test Name ==="

# Send incident (timestamps are patched automatically)
produce_incident "$REPO_ROOT" "$BOOTSTRAP" "$TOPIC" "$PAYLOAD" "my_test"
sleep 10

# Check result
DOC=$(poll_es_sim "$ES_HOST" 60 5) || { print_status "fail" "No doc found"; exit 1; }

# Assert (your test-specific logic here)
print_status "ok" "PASS: ..."
```

### What the framework handles

- **Timestamps** — `produce_incident` patches to today automatically
- **State isolation** — Redis and ES sim are flushed between tests
- **AB lifecycle** — orchestrator restarts AB with your config before each test
- **Cleanup** — orchestrator handles teardown after all tests

## For Coding Agents

Run all P1 functional tests from the repo root:

```bash
# Step 1: Ensure Docker is available (for Kafka and Redis containers)
docker info

# Step 2: Activate venv
source /localhome/local-trongp/venv/bin/activate

# Step 3: Navigate to the P1 test directory
cd test/functional/p1

# Step 4: Run all tests
./run_p1.sh

# Step 5: Interpret results
# Exit code 0 = all tests passed
# Exit code 1 = one or more tests failed
```

Run a specific test and keep simulators running for inspection:

```bash
./run_p1.sh --test test_document_schema --skip-cleanup
```

Check Alert Bridge logs if a test fails:

```bash
cat /tmp/alert_agent_p1_functional/alert_bridge.log
```

## Services and Ports

| Service | Port | Health Check |
|---------|------|--------------|
| Kafka | 9092 | TCP connect |
| Redis | 6379 | TCP connect |
| Elasticsearch (sim) | 9200 | `GET /health` |
| NIM (sim) | 18081 | TCP connect |
| VST (sim) | 30888 | `GET /status` |
| VSS (sim) | 8080 | `GET /models` |
| Alert Bridge (HTTP) | 9080 | `GET /health` |

Kafka and Redis run as Docker containers. All other services run as Python processes managed by `run_p1.sh`.

---

### test_realtime_replay

**Purpose:** End-to-end coverage for the replay endpoint (`POST /api/v1/realtime/replay`), concurrency guards, persistence fallback, and state durability across AB restarts.

**Config:** `persistence.enabled: true`, RTVI sim at `:8018`, ES sim at `:9200`. A second config (`config_no_persistence.yaml`) with `persistence.enabled: false` is used for the feature-flag-off sub-test.

**Sub-tests:**

| # | Name | What it validates |
|---|------|-------------------|
| 1 | Happy-path replay | 2 rules re-onboarded, RTVI receives `streams/add` + `generate_captions` for each |
| 2 | Partial RTVI failure | RTVI fault injected → `replayed=0, failed=1`, ES record unchanged |
| 3 | Concurrent replay → 409 | Second replay while first is in-flight returns 409 |
| 4 | POST during replay → 503 | `POST /api/v1/realtime` blocked with `replay_in_progress` |
| 5 | DELETE during replay → 503 | `DELETE /api/v1/realtime/{id}` blocked with `replay_in_progress` |
| 6 | GET during replay → 200 | `GET /api/v1/realtime` and `GET /api/v1/realtime/{id}` remain available |
| 7 | Feature-flag off | AB restarted with `persistence.enabled: false` → replay returns 501 `persistence_disabled`, POST/GET/DELETE still work via in-memory path |
| 8 | AB restart preserves state | Rule created → AB stopped/restarted → rule still visible in GET list (loaded from ES) |

**RTVI sim delay:** Sub-tests 3-6 use the RTVI sim's `PUT /v1/delay` endpoint to slow down `streams_add` responses, keeping the replay in-flight long enough for concurrent requests to be tested.

**AB lifecycle:** Sub-tests 7 and 8 restart AB with different configs using `stop_alert_bridge_local` / `start_alert_bridge_local` from `shared/helpers.sh`.
