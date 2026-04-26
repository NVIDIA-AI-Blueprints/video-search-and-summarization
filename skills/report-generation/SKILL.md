---
name: report-generation
description: Produce video analysis reports by discovering the deployed VSS agent, querying POST /generate for a timestamped captioned summary of the clip, then formatting the agent reply as the standard Video Analysis Report markdown. 
version: "3.2.0"
license: "Apache License 2.0"
---

# Report Generation

Build **timestamped video analysis reports** by **querying the VSS agent** for a description of the video using `POST …/generate`. The agent runs **`video_understanding`** (and related tools) internally. Take the agent’s **caption-style text with timestamps** and paste it into the **Video Analysis Report** template below.

---

## When to Use

- "Generate a report for this video" / "for `<sensor-id>`"
- "Create an analysis report"
- "Report on what happens in the uploaded video"
- "Give me a report"

---

## OpenClaw workflow

Run these steps **in order**:

1. **Sensor / clip** — Confirm which **sensor id** or **video** the user means. If unclear, ask before proceeding. If the sensor or video is not mentioned directly in the user request, the user may be referring to a video they mentioned previously.

2. **VSS agent deployment** — Resolve the agent **HTTP base URL**. Read **`VSS_AGENT_PORT`**, **`EXTERNAL_IP` / `HOST_IP`**, or compose / deployment docs for the machine where the stack runs. Typical pattern: **`http://<host>:<port>`** with port from env (often **`8000`** for the agent API).

3. **Query the agent** — **`POST ${VSS_AGENT_BASE_URL}/generate`** with JSON **`{"input_message": "<prompt>"}`**. Ask for a **captioned summary with timestamps** (chronological segments, seconds from clip start), e.g. describe scenes and events with time ranges. Name the **sensor / file** in the message so the agent has the necessary information.
   - DO NOT mention a report to vss agent

4. **Report template** — Copy the agent’s final text (timestamped caption/summary) into **Analysis Results** and fill **Basic Information**; **return that markdown** to the user.
0l
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

- **sensor-ops** — VST sensors, storage, and clip URLs if you need to verify the video exists before calling the agent.
- **video-summarization** / **incident-report** — other **`/generate`** patterns; this skill focuses on **timestamped captions → report template**.