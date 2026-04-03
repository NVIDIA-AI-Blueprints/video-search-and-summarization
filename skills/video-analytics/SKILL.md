---
name: video-analytics
description: Query video analytics incidents, alerts, sensor data, and metrics from Elasticsearch via the VA-MCP server (port 9901). Use for any question about what happened in video — PPE violations, alerts, incidents, object counts, speeds, occupancy, or anything that requires looking up recorded events. This is the primary way to answer "what happened", "show me alerts", "any violations", "how many people", etc.
metadata:
  { "openclaw": { "emoji": "🔎", "os": ["linux"] } }
---

# Video Analytics (VA-MCP)

Queries incidents, alerts, and metrics stored in Elasticsearch via MCP JSON-RPC at **port 9901**.

> **ALWAYS run the commands below yourself and relay results to the user. Do NOT guess or describe — actually execute and report back.**

---

## REQUIRED: Two-Step Pattern (copy this exactly)

**Every query requires two shell commands run in sequence:**

```bash
# Step 1: initialize — get session ID from response HEADER
SESSION_ID=$(curl -si -X POST http://localhost:9901/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"cli","version":"1.0"}},"id":0}' \
  | grep -i "mcp-session-id" | awk '{print $2}' | tr -d '\r')

# Step 2: call the tool using the session ID in the header
curl -s -X POST http://localhost:9901/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "mcp-session-id: $SESSION_ID" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"video_analytics__get_incidents","arguments":{"max_count":10}},"id":1}' \
  | grep '^data:' | sed 's/^data: //' | jq -r '.result.content[0].text'
```

> The session ID comes from the **response header** `mcp-session-id`, not the body.
> Skipping Step 1 always results in `Bad Request: Missing session ID`.

---

## Tool Reference

Replace the `-d` payload in Step 2 with any of the following.

### video_analytics__get_incidents

| Parameter | Type | Description |
|---|---|---|
| `source` | string | Sensor ID or place name (optional) |
| `source_type` | string | `sensor` or `place` |
| `start_time` | string | ISO 8601: `YYYY-MM-DDTHH:MM:SS.sssZ` |
| `end_time` | string | ISO 8601 |
| `max_count` | int | Max results (default: 10) |
| `includes` | list | Extra fields: `objectIds`, `info` |
| `vlm_verdict` | string | `confirmed`, `rejected`, or `unverified` |

```bash
# Recent incidents (all sensors)
-d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"video_analytics__get_incidents","arguments":{"max_count":10}},"id":1}'

# For a specific sensor
-d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"video_analytics__get_incidents","arguments":{"source":"<sensor-id>","source_type":"sensor","max_count":20}},"id":1}'

# Confirmed (VLM-verified) only
-d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"video_analytics__get_incidents","arguments":{"vlm_verdict":"confirmed","max_count":10}},"id":1}'
```

### video_analytics__get_incident

```bash
-d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"video_analytics__get_incident","arguments":{"id":"<incident-id>","includes":["objectIds","info"]}},"id":1}'
```

### video_analytics__get_sensor_ids

```bash
-d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"video_analytics__get_sensor_ids","arguments":{}},"id":1}'
```

### video_analytics__get_places

```bash
-d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"video_analytics__get_places","arguments":{}},"id":1}'
```

### video_analytics__get_fov_histogram

```bash
-d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"video_analytics__get_fov_histogram","arguments":{"source":"<sensor-id>","source_type":"sensor","start_time":"<ISO>","end_time":"<ISO>","object_type":"Person","bucket_count":10}},"id":1}'
```

### video_analytics__analyze

`analysis_type`: `max_min_incidents`, `average_speed`, `avg_num_people`, `avg_num_vehicles`

```bash
-d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"video_analytics__analyze","arguments":{"source":"<sensor-id>","source_type":"sensor","start_time":"<ISO>","end_time":"<ISO>","analysis_type":"avg_num_people"}},"id":1}'
```

### vst_sensor_list

```bash
-d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"vst_sensor_list","arguments":{}},"id":1}'
```
