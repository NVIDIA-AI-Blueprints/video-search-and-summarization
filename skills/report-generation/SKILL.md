---
name: report-generation
description: Produce video analysis reports via direct VLM (chat/completions) using the report vlm_prompt from video_report_gen config, then the standard Video Analysis Report template. For OpenClaw—first confirm the request is about a video and the clip is accessible; then call the VLM; then return the filled template to the user. Does not use the VSS agent /generate endpoint.
metadata:
  { "openclaw": { "emoji": "📄", "os": ["linux"] } }
---

# Report Generation

Build **timestamped video analysis reports** by calling the **VLM** (`POST …/v1/chat/completions`) with the **`video_report_gen.vlm_prompt`** text from agent configuration (same string **`video_report_gen`** passes as **`user_prompt`** to **`video_understanding`**), then wrap the model output in the **Video Analysis Report** markdown layout.

---

## When to Use

- "Generate a report for this video" / "for `<sensor-id>`"
- "Create an analysis report" / "safety report" for a named clip
- "Report on what happens in the uploaded video"
- "Give me a report"

If the sensor or video is not mentioned in the user request, assume that the user is referring to a video they mentioned previously. 

---

## OpenClaw workflow

Run these steps **in order**:

1. Ensure that the sensor or video is accessible. If it is unclear which sensor/video the user is referring to, confirm with the user before proceeding. Sensor/video is required to proceed.
2. **Accessibility** — Obtain a **playback/storage URL** the VLM can use (see **sensor-ops** for VST). For **remote** VLM, the URL must be reachable from the VLM host (often **public**). For **local** VLM, use internal URLs consistent with your deployment. Optionally verify the URL (e.g. `HEAD`/`GET`) before analysis.
3. **VLM call** — **`POST ${VLM_BASE_URL}/v1/chat/completions`** with **`model`**, **`messages`** containing the full report **`vlm_prompt`** as user **`text`** plus **`video_url`** (see **Direct VLM call**). Use the same **`VLM_BASE_URL`** / **`VLM_NAME`** as the stack (compose / `.env`). Add **`system`** content  to match **`video_understanding.system_prompt`** from config and match the vlm settings for `video_understanding` settings in config.yml.
4. **Report template** — Take the model’s plain-text reply and fill **Basic Information** + **Analysis Results** using the template below; **return that markdown to the user** as the final answer.

---

## Video URL notes

- **Remote VLM** (`vlm_mode: remote`): URL must be reachable from the VLM service (often **public**).
- **Local VLM**: use URLs your deployment expects (internal VST URLs; mirror **`video_understanding`** URL translation rules).

---

## Direct VLM call

Agent VLM profiles use **`base_url: ${VLM_BASE_URL}/v1`**, so:

`POST ${VLM_BASE_URL}/v1/chat/completions`

**Example** — report prompt in **`report_vlm_prompt.txt`** (full **`video_report_gen.vlm_prompt`** from config):

```bash
# NIM/VLM service — often a different port than vss-agent
export VLM_BASE_URL="http://localhost:8001"
export VLM_NAME="nvidia/cosmos-reason2-8b"
export VIDEO_URL="https://example.com/path/accessible-to-vlm.mp4"

curl -s -X POST "${VLM_BASE_URL}/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d "$(jq -n \
    --rawfile text report_vlm_prompt.txt \
    --arg model "$VLM_NAME" \
    --arg vurl "$VIDEO_URL" \
    '{
      model: $model,
      temperature: 0,
      max_tokens: 4096,
      messages: [{
        role: "user",
        content: [
          {type: "text", text: $text},
          {type: "video_url", video_url: {url: $vurl}}
        ]
      }]
    }')" | jq -r '.choices[0].message.content'
```

**Extras:** **`model`** must match the endpoint. NIM/Cosmos may need **extra JSON** (chunking, resolution) per **`rtvi_vlm`** / **`model_kwargs`** in your deployment docs.

---

## Video Analysis Report template

Paste **VLM output** under **Analysis Results**. Fill the table fields (timestamps, source, request). Matches **`video_report_gen._create_report_header`** + body.

```markdown
# Video Analysis Report

## Basic Information

| Field | Value |
|-------|-------|
| **Report Identifier** | vss_report_<YYYYMMDD_HHMMSS> |
| **Date of Analysis** | <YYYY-MM-DD> |
| **Time of Analysis** | <HH:MM:SS> |
| **Reporting AI Agent** | <e.g. openclaw or your label> |
| **Video Source** | <sensor_id or filename> |
| **Analysis Request** | <short description> |

## Analysis Results

<model output: timestamped lines per your vlm_prompt OUTPUT REQUIREMENTS>
```

---

## Cross-Reference

- **sensor-ops** — VST list/storage/replay URLs so the clip is **accessible** before the VLM call
- **video-summarization** / **incident-report** — flows that use the **agent** (`/generate`)
