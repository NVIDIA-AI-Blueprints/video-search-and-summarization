---
name: alerts
description: Manage and monitor VSS alerts — check alert status, submit alerts for VLM verification, customize alert prompts, query confirmed/rejected verdicts. Use when asked to check alerts, submit an alert, customize alert prompts, view recent alerts, or manage alert verification. Requires the alerts profile to be deployed.
metadata:
  { "openclaw": { "emoji": "🚨", "os": ["linux"] } }
---

# VSS Alert Management

Manage alerts after the alerts profile is deployed. To deploy, use the `deploy` skill with `-p alerts`.

## When to Use

- Check, query, or view recent alerts
- Submit alerts for VLM verification
- Customize alert type prompts
- Check verdict status (confirmed/rejected/unverified)
- Add an RTSP stream / camera to the alerts pipeline

---

## Check Alerts via Agent (Natural Language)

Query the VSS agent at port 8000:

```bash
curl -s -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"input_message": "Show me recent alerts for sensor camera-01"}' | jq .

curl -s -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"input_message": "Were there any PPE violations in the last hour?"}' | jq .

curl -s -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"input_message": "List confirmed alerts from today"}' | jq .
```

---

## Submit Alerts via API

Submit alerts directly to the Alert Verification Microservice for VLM review.

### Submit a Behavior Alert (`nv.Behavior`)

```bash
curl -s -X POST http://localhost:8000/api/v1/alerts \
  -H "Content-Type: application/json" \
  -d '{
    "sensorId": "<sensor-id>",
    "timestamp": "2025-09-11T00:08:27.822Z",
    "end": "2025-09-11T00:09:22.122Z",
    "category": "collision",
    "place": { "name": "<location-name>" },
    "objectIds": ["obj-001", "obj-002"],
    "isAnomaly": true
  }' | jq .
```

### Submit an Incident (`nv.Incident`)

```bash
curl -s -X POST http://localhost:8000/api/v1/incidents \
  -H "Content-Type: application/json" \
  -d '{
    "sensorId": "<sensor-id>",
    "timestamp": "2025-09-11T00:08:27.822Z",
    "end": "2025-09-11T00:09:22.122Z",
    "category": "<alert-type>",
    "place": { "name": "<location-name>" }
  }' | jq .
```

Both endpoints return **202 Accepted** — the alert is queued for VLM verification.

### Verdict Interpretation

Verified alerts have an extended `info` block:

| `verdict` | Meaning |
|---|---|
| `confirmed` | VLM determined the alert is real |
| `rejected` | VLM determined it is a false positive |
| `unverified` | Verification could not complete (error) |

Check `verification_response_code` (200 = success) and `reasoning` for VLM explanation.

---

## Customize Alert Prompts

Alert-type prompts are configured in `alert_type_config.json`. Each entry maps an alert `category` to VLM prompts:

```json
{
  "version": "1.0",
  "alerts": [
    {
      "alert_type": "collision",
      "output_category": "Vehicle Collision",
      "prompts": {
        "system": "You are a video analysis expert...",
        "user": "Based on the video, did a collision occur at {place.name}? ...",
        "enrichment": "Describe the collision in detail..."
      }
    }
  ]
}
```

- **`alert_type`** must match the `category` field in submitted alerts
- **`output_category`** is the display name in Elasticsearch/UI
- **`enrichment`** triggers a second VLM call for richer descriptions (optional)
- Prompt changes require a container restart (`alert_agent.enrichment.enabled: true` must be set to use enrichment)

---

## Add a Camera / RTSP Stream

Use the `sensor-ops` skill — it covers all sensor/stream/recording/storage operations for VIOS.

---

## Query Incidents from Elasticsearch

Use the `video-analytics` skill to query incidents, alerts, occupancy, and analytics from Elasticsearch (port 9901).

---

## Interact via Browser (agent-browser)

Use the `agent-browser` skill to interact with the VSS and VIOS UIs. Always snapshot first to get element refs.

Use `--auto-connect` to drive the user's already-running Chrome (no headless browser needed). If that fails, ask the user to launch Chrome with `--remote-debugging-port=9222` and use `--cdp 9222`.

**Snapshot the current tab:**

```bash
npx agent-browser --auto-connect snapshot -i
```

**Open the agent UI (port 3000):**

```bash
npx agent-browser --auto-connect open http://localhost:3000
npx agent-browser --auto-connect wait --load networkidle
npx agent-browser --auto-connect snapshot -i
```

**Open the VIOS dashboard to manage cameras (port 30888):**

```bash
npx agent-browser --auto-connect open http://localhost:30888/vst/
npx agent-browser --auto-connect wait --load networkidle
npx agent-browser --auto-connect snapshot -i
```

**Send a query to the agent:**

```bash
# Find chat input ref (@eN) and submit button (@eM) from snapshot, then:
npx agent-browser --auto-connect fill @eN "Show me recent PPE violation alerts"
npx agent-browser --auto-connect click @eM
npx agent-browser --auto-connect snapshot -i
```

**Take a screenshot and send to user:**

```bash
npx agent-browser --auto-connect screenshot --path /tmp/vss-alerts-ui.png
# Then immediately send /tmp/vss-alerts-ui.png to the user via Slack/current channel
```

> Re-snapshot after every navigation or interaction to get fresh element refs.
