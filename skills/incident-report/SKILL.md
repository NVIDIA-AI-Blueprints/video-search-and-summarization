---
name: incident-report
description: Generate and query incident reports from VSS — look up incidents in Elasticsearch, analyze incident patterns, generate narrative reports. Use when asked about incidents, incident reports, PPE violations, safety events, or "what happened". Requires the alerts profile to be deployed.
metadata:
  { "openclaw": { "emoji": "📋", "os": ["linux"] } }
---

# Incident Report Workflows

Query incidents from Elasticsearch, analyze patterns, and generate reports. Requires the alerts profile — deploy with the `deploy` skill (`-p alerts`).

## When to Use

- "Show me incidents from today"
- "Generate an incident report"
- "How many PPE violations this week?"
- "Summarize incidents for sensor X"
- "What happened at the loading dock?"

---

## Query Incidents via VA-MCP

Use the two-step MCP pattern to query Elasticsearch (port 9901):

```bash
# Step 1: initialize — get session ID
SESSION_ID=$(curl -si -X POST http://localhost:9901/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"cli","version":"1.0"}},"id":0}' \
  | grep -i "mcp-session-id" | awk '{print $2}' | tr -d '\r')

# Step 2: query incidents
curl -s -X POST http://localhost:9901/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "mcp-session-id: $SESSION_ID" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"video_analytics__get_incidents","arguments":{"max_count":10}},"id":1}' \
  | grep '^data:' | sed 's/^data: //' | jq -r '.result.content[0].text'
```

### Filter by Sensor

```bash
-d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"video_analytics__get_incidents","arguments":{"source":"<sensor-id>","source_type":"sensor","max_count":20}},"id":1}'
```

### Filter by Time Range

```bash
-d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"video_analytics__get_incidents","arguments":{"start_time":"2025-09-11T00:00:00.000Z","end_time":"2025-09-11T23:59:59.999Z","max_count":50}},"id":1}'
```

### Filter by VLM Verdict

```bash
# Only confirmed incidents
-d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"video_analytics__get_incidents","arguments":{"vlm_verdict":"confirmed","max_count":10}},"id":1}'
```

### Get Incident Detail

```bash
-d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"video_analytics__get_incident","arguments":{"id":"<incident-id>","includes":["objectIds","info"]}},"id":1}'
```

---

## Analyze Incident Patterns

```bash
# Peak incident times
-d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"video_analytics__analyze","arguments":{"source":"<sensor-id>","source_type":"sensor","start_time":"<ISO>","end_time":"<ISO>","analysis_type":"max_min_incidents"}},"id":1}'

# Average people count
-d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"video_analytics__analyze","arguments":{"source":"<sensor-id>","source_type":"sensor","start_time":"<ISO>","end_time":"<ISO>","analysis_type":"avg_num_people"}},"id":1}'
```

---

## Generate Reports via Agent

Ask the VSS agent to generate a report:

```bash
curl -s -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"input_message": "Generate an incident report for sensor <sensor-id>"}' | jq .
```

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

- **sensor-ops** — get sensor details, snapshots, and stream info for context
- **video-analytics** — full VA-MCP tool reference for deeper ES queries
- **alerts** — submit new alerts, customize alert prompts, check verdicts
