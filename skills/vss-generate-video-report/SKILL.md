---
name: vss-generate-video-report
description: Produce a timestamped video analysis report from a clip. Use when the user says "generate a report", "give me a report", or "create a report on this video".
license: Apache-2.0
metadata:
  version: "3.2.0"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint operational"
---

# Report

Build **timestamped video analysis reports** by **querying the VSS agent** for a description of the video using `POST …/generate`. The agent runs **`video_understanding`** (and related tools) internally. Take the agent’s **caption-style text with timestamps** and paste it into the **Video Analysis Report** template below.

---

## When to Use

- "Generate a report for this video" / "for `<sensor-id>`"
- "Create an analysis report"
- "Report on what happens in the uploaded video"
- "Give me a report"

---

## Deployment prerequisite

This skill requires the VSS **base** profile running on the host at `$HOST_IP`. Before any request:

1. Probe the VSS agent:
   ```bash
   curl -sf --max-time 5 "http://${HOST_IP}:8000/docs" >/dev/null
   ```

2. **If the probe fails**, ask the user:
   > *"The VSS `base` profile isn't running on `$HOST_IP`. Shall I deploy it now using the `/vss-deploy-profile` skill with `-p base`?"*

   - If yes → hand off to the `/vss-deploy-profile` skill. Return here once it succeeds.
   - If no → stop. Do not run this skill against a missing stack.

   (If your caller has granted explicit pre-authorization to deploy
   autonomously — e.g. the request says "pre-authorized to deploy
   prerequisites", or you are running in a non-interactive evaluation
   harness with that permission — skip the confirmation and invoke
   `/vss-deploy-profile` directly.)

3. If the probe passes, proceed.

---

## Sensor prerequisite

**You MUST list VST sensors before any `/generate` call.** This is required even when the user names the sensor explicitly, even when the user asserts the video is already uploaded, and even when a previous turn appeared to use the same video. Do not skip this step.

1. List sensors:
   ```bash
   curl -sf --max-time 5 "http://${HOST_IP}:30888/vst/api/v1/sensor/list" | jq '.[].name'
   ```

2. Compare the returned `name` values against the user-supplied `<sensor-id>` (or **filename stem**, e.g. `warehouse_safety_0001`).

3. **If a matching sensor is present** → proceed to the Agent workflow below.

4. **If no matching sensor is present** — upload the video first, then re-list to confirm the new sensor appears:
   ```bash
   # filename: must not contain whitespace
   # timestamp: ISO 8601 UTC — default 2025-01-01T00:00:00.000Z if user did not specify
   curl -s -X PUT "http://${HOST_IP}:30888/vst/api/v1/storage/file/<filename>?timestamp=<timestamp>" \
     -H "Content-Type: application/octet-stream" \
     -H "Content-Length: <file_size_in_bytes>" \
     --upload-file /path/to/<filename> | jq .
   ```
   See `/vss-manage-video-io-storage` for full upload semantics (v1 vs v2, conflict handling, delete flow). In interactive runs, confirm with the user before uploading. **Never** issue an unconditional PUT without first running the sensor-list check above — that is exactly the failure mode this prerequisite exists to prevent.

---

## Agent workflow

The Sensor prerequisite above must have already confirmed (or made) the sensor exist on VST. Then run these steps **in order**:

1. **Sensor / clip** — Confirm which **sensor id** or **video** the user means. If unclear, ask before proceeding. If the sensor or video is not mentioned directly in the user request, the user may be referring to a video they mentioned previously.

2. **VSS agent deployment** — Resolve the agent **HTTP base URL**. Read **`VSS_AGENT_PORT`**, **`EXTERNAL_IP` / `HOST_IP`**, or compose / deployment docs for the machine where the stack runs. Typical pattern: **`http://<host>:<port>`** with port from env (often **`8000`** for the agent API).

3. **Query the agent** — **`POST ${VSS_AGENT_BASE_URL}/generate`** with JSON **`{"input_message": "<prompt>"}`**. Ask for a **captioned summary with timestamps** (chronological segments, seconds from clip start), e.g. describe scenes and events with time ranges. Name the **sensor / file** in the message so the agent has the necessary information.
   - DO NOT mention a report to vss agent

4. **Report template** — Copy the agent’s final text (timestamped caption/summary) into **Analysis Results** and fill **Basic Information**; **return that markdown** to the user.

---

## Query VSS agent (`/generate`)

```bash
# Set from deployment (compose / .env / host where vss-agent listens)
export VSS_AGENT_BASE_URL="http://localhost:8000"

curl -s -X POST "${VSS_AGENT_BASE_URL}/generate" \
  -H "Content-Type: application/json" \
  -d '{"input_message": "Describe in detail what happens in the video for sensor <sensor-id>, with timestamps (start–end in seconds from clip start) for each segment or event."}' | jq .
```

---

## Video Analysis Report template

Paste the **agent’s timestamped summary** under **Analysis Results**. Fill the table fields (timestamps, source, request).

```markdown
# Video Analysis Report

## Basic Information

| Field | Value |
|-------|-------|
| **Report Identifier** | vss_report_<YYYYMMDD_HHMMSS> |
| **Date of Analysis** | <YYYY-MM-DD> |
| **Time of Analysis** | <HH:MM:SS> |
| **Reporting AI Agent** | <e.g. your label> |
| **Video Source** | <sensor_id or filename> |
| **Analysis Request** | <description of user's request to you> |

## Analysis Results

<agent output: timestamped caption / summary>
```

---

## Cross-Reference

- **vss-manage-video-io-storage** — VST sensors, storage, and clip URLs if you need to verify the video exists before calling the agent.
- **vss-summarize-video** / **vss-manage-alerts** — other **`/generate`** patterns; this skill focuses on **timestamped captions → report template**.
