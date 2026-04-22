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

Use the `vios` skill — it covers all sensor/stream/recording/storage operations for VIOS.

---

## Query Incidents from Elasticsearch

Use the `video-analytics` skill to query incidents, alerts, occupancy, and analytics from Elasticsearch (port 9901).

