---
name: video-summarization
description: Summarize long videos, generate shift reports, and analyze extended recordings. Use when asked to summarize a video, generate a shift summary, analyze a long recording, or create a daily activity report. Requires the LVS profile to be deployed.
metadata:
  { "openclaw": { "emoji": "📹", "os": ["linux"] } }
---

# Video Summarization Workflows

Summarize long-form video content that exceeds standard VLM context limits. Requires the LVS profile — deploy with the `deploy` skill (`-p lvs`).

## When to Use

- "Summarize this video"
- "Generate a shift summary"
- "What happened during the night shift?"
- "Create a daily activity report"
- Analyze extended recordings (minutes to hours)

---

## How LVS Works

1. **Segment** — Long video is split into manageable segments
2. **Analyze** — Each segment is analyzed independently by the VLM
3. **Synthesize** — Segment analyses are combined into a coherent summary
4. **Report** — Final summary with timestamped highlights is generated

---

## Summarize via Agent UI

Open `http://<HOST_IP>:3000/`, upload a video, then ask:

```
Summarize this video
Generate a report for this video
What are the key events in this recording?
```

---

## Summarize via REST API

```bash
# Upload a video first (via VIOS)
curl -s -X PUT http://localhost:30888/vst/api/v1/storage/file/my-video.mp4 \
  --upload-file /path/to/video.mp4 | jq .

# Then ask the agent to summarize
curl -s -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"input_message": "Can you generate a report for this video?"}' | jq .
```

### More Examples

```bash
# Summarize for a specific sensor
curl -s -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"input_message": "Can you generate a report for <sensor-id>?"}' | jq .

# Ask about specific timeframes
curl -s -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"input_message": "Summarize the activity between 8am and 12pm"}' | jq .
```

---

## Report Generation (HITL Flow)

Report generation triggers a **HITL (Human-in-the-Loop) prompt-editing flow** — the agent pauses for the user to review the VLM prompt before generating.

| User input | Effect |
|---|---|
| Submit (empty) | Approve prompt and generate |
| New text | Replace prompt manually |
| `/generate <description>` | LLM writes a new prompt from description |
| `/refine <instructions>` | LLM refines current prompt |
| `/cancel` | Cancel |

Generated reports: `http://<HOST_IP>:8000/static/agent_report_<DATE>.md` / `.pdf`

> Reports are in-memory by default — lost on container restart. Mount a volume to persist them.

---

## Cross-Reference with Other Skills

- **sensor-ops** — upload videos, manage streams, get snapshots for context
- **video-analytics** — query incidents/events from Elasticsearch for the same timeframe
- **incident-report** — generate incident-focused reports from alert data

---

## Interact via Browser (agent-browser)

```bash
npx agent-browser --auto-connect open http://localhost:3000
npx agent-browser --auto-connect wait --load networkidle
npx agent-browser --auto-connect snapshot -i
```

Upload a video via the UI, then send a summarization query.
