# P1 Extended Sanity Checks

Extended sanity checks that run against a live Alert Bridge deployment. Complements the P0 checks with deeper field-level and behavioral validation.

## Quick Start

```bash
# HTTP-only mode (most common)
ES_HOST=localhost ./run_p1.sh

# With verbose output
ES_HOST=localhost ./run_p1.sh --verbose

# JSON output for CI/CD
ES_HOST=localhost ./run_p1.sh --json
```

## Checks

| # | Check | Mode | Description |
|---|-------|------|-------------|
| 01 | Document Parity | HTTP | Latest 10 incident IDs all found in VLM index |
| 02 | Verdict Validation | HTTP | Latest 10 docs all have valid non-null `info.verdict` |
| 03 | Redis Dedup Fingerprints | HTTP | >=50% of sampled docs have `info.fingerprint` field (needs discussion) |
| 04 | VST Video URL | HTTP | Latest 10 docs all have `info.videoUrl` or `info.videoSource` |
| 05 | Document Schema | HTTP | Latest 10 docs have all required fields |
| 06 | VLM Response Codes | HTTP | Latest 10 docs all have valid `verificationResponseCode` (200 or non-200) |
| 07 | Verdict Protection | HTTP | >=1 doc with `info.protectionFingerprint` (requires 100+ docs, needs discussion) |
| 08 | HTTP POST Endpoint | HTTP | POST JSON incident to `/api/v1/incidents`; verify HTTP 202 |

## Environment Variables

All P0 variables are inherited. P1 adds:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ES_HOST` | **Yes** | - | Elasticsearch host |
| `ES_PORT` | No | 9200 | Elasticsearch port |
| `SSH_HOST` | No | - | SSH host (inherited from P0, unused by P1 checks) |
| `METRICS_HOST` | No | `$ES_HOST` | Host for metrics queries |
| `METRICS_PORT` | No | 9081 | Metrics service port |
| `AB_HOST` | No | `$ES_HOST` | Alert Bridge HTTP host (check 08) |
| `AB_PORT` | No | 9080 | Alert Bridge HTTP port (check 08) |
| `VERDICT_SAMPLE_SIZE` | No | 20 | Number of docs sampled for verdict distribution (check 02) |

Configuration is loaded from `checks_p1/config_p1.env` at runtime.

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | All checks passed (skips are OK) |
| 1 | One or more checks failed |
| 2 | Configuration error (missing `ES_HOST`) |

## Per-Check Details

### 01 — Document Parity

**Purpose:** Confirm that a document written to the incidents index also appears in the alerts index with the same ID. Detects a broken index routing or missing write step.

**ES Query:** Sample 1 document ID from `INCIDENTS_INDEX_PATTERN`, then query `ALERTS_INDEX_PATTERN` by that ID.

**Pass:** Alert document found with matching ID.
**Fail:** ID present in incidents but absent in alerts.
**Skip:** Incidents index is empty.

---

### 02 — Verdict Distribution

**Purpose:** Confirm the VLM model is actively classifying. If all sampled documents share the same verdict value the model may be stuck or returning a constant output.

**ES Query:** Fetch `VERDICT_SAMPLE_SIZE` documents (default 20) from alerts index, extract `info.verdict`.

**Pass:** 2 or more distinct non-null verdict values found.
**Fail:** All sampled documents share a single verdict value (5+ docs sampled).
**Skip:** Fewer than 5 documents sampled, or all verdicts are null.

---

### 03 — Redis Dedup Fingerprints

**Purpose:** Confirm the Redis deduplication pipeline is writing fingerprints to alert documents. A fingerprint absence in the majority of docs indicates dedup is disabled or broken.

**ES Query:** Fetch `VERIFICATION_SAMPLE_SIZE` documents from alerts index, check `info.fingerprint`.

**Pass:** 50% or more of sampled documents have a non-empty `info.fingerprint`.
**Fail:** Fewer than 50% have a fingerprint.
**Skip:** Fewer than 5 documents available.

---

### 04 — VST Video URL

**Purpose:** Confirm Alert Bridge is retrieving video clips from VST and writing the URL into alert documents.

**ES Query:** Count documents in alerts index where `info.videoUrl` or `info.videoSource` exists.

**Pass:** At least 1 document has a video URL field.
**Fail:** No documents have either video URL field despite non-empty index.
**Skip:** Alerts index is empty.

---

### 05 — Document Schema

**Purpose:** Verify latest documents contain all required fields. Catches schema regressions or missing pipeline steps.

**Required fields:** `sensorId`, `category`, `timestamp`, `info.verdict`, `info.vlm_response`, `info.verificationResponseCode`, `info.verificationResponseStatus`

**ES Query:** Fetch 1 document from alerts index.

**Pass:** All required fields are present and non-empty.
**Fail:** One or more fields are missing. Output lists the missing field names.
**Skip:** Alerts index is empty.

---

### 06 — VLM Error Codes

**Purpose:** Confirm error handling is active. A production index with any meaningful volume should contain at least some non-200 verification responses from transient VLM failures. Zero non-200 codes may indicate error codes are being suppressed or overwritten.

**ES Query:** Count documents where `info.verificationResponseCode` is not 200.

**Pass:** At least 1 document has a non-200 response code.
**Fail:** Zero non-200 codes found across all documents.
**Skip:** Fewer than 10 total documents.

---

### 07 — Verdict Protection

**Purpose:** Confirm the verdict protection mechanism is active. Documents processed by the protection layer receive a `info.protectionFingerprint` field.

**ES Query:** Count documents where `info.protectionFingerprint` exists (requires 100+ total docs to run).

**Pass:** At least 1 document has `info.protectionFingerprint`.
**Fail:** No documents have the field despite 100+ docs in the index.
**Skip:** Fewer than 100 total documents.

---

### 08 — HTTP POST Endpoint

**Purpose:** Confirm the Alert Bridge REST API is reachable and accepts incident submissions.

**Request:** `POST http://{AB_HOST}:{AB_PORT}/api/v1/incidents` with `Content-Type: application/json` and the sample incident payload.

**Pass:** Endpoint returns HTTP 202.
**Fail:** Endpoint returns a non-202 response.
**Skip:** `AB_HOST` is not set, sample payload not found, or connection is refused.

---

## Writing a New Check

Create a script `NN_check_name.sh` in `checks_p1/` (where `NN` is the next available two-digit number). The script runs against a live deployment — all queries must be read-only.

### Script conventions

- Source `../lib/common.sh` at the top. This provides `pass`, `fail`, `skip_check`, and `es_query`, and sets `set -e`.
- For additional config values, source `config_p1.env`. For extra helpers (e.g., shared query patterns), source `lib/helpers_p1.sh`.
- Do not modify any file outside `checks_p1/` — shared framework files in `lib/` and the runner `run_p1.sh` are stable and shared across all checks.

### Error handling

`common.sh` enables `set -e`, so unhandled errors exit immediately. When calling `fail`, use `fail "message" || true` before any `exit 1` to ensure the failure message is printed before the script exits.

Use `skip_check "reason"` when:
- There is insufficient data to evaluate (e.g., fewer than 5 documents in the index)
- A feature is not enabled in this deployment

### ES query pattern

Query the latest records with:

```json
{"size": 10, "sort": [{"timestamp": "desc"}]}
```

Pass the query and index pattern to `es_query`. Avoid full-index scans — always use `size` to bound results.

## For Coding Agents

Run P1 checks against a remote deployment:

```bash
# Step 1: Set target
export ES_HOST="localhost"
export ES_PORT="9200"

# Step 2: (Optional) Set AB_HOST if check 08 is needed
export AB_HOST="localhost"
export AB_PORT="9080"

# Step 3: Navigate to the sanity directory
cd test/sanity

# Step 4: Run all P1 checks
./run_p1.sh

# Step 5: Interpret results
# Exit code 0 = all passed (skips OK)
# Exit code 1 = one or more failed
```

To run a single check directly:

```bash
ES_HOST=localhost bash test/sanity/checks_p1/05_document_schema.sh
```

**Note:** These checks run against a live deployment. They are read-only (no writes to ES). SSH is not required for any P1 check.
