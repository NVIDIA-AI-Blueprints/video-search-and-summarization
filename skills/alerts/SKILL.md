---
name: alerts
description: Manage and monitor VSS alerts after the alerts profile is deployed. The deployment's mode (CV vs VLM real-time) is fixed at deploy time and determines the workflow — start/stop real-time alerts via the VSS Agent on a VLM deployment, onboard CV alerts by adding RTSP streams to VIOS on a CV deployment, query incidents, customize verifier prompts. Use when asked to start/stop a real-time alert, check or list alerts, add a camera, customize alert prompts, or view verdicts.
version: "3.1.0"
license: "Apache License 2.0"
---

# VSS Alert Management

The alerts profile is deployed in **one** of two modes at a time. The mode is chosen at `deploy -p alerts -m {verification,real-time}` and is static until you tear down and redeploy. Which mode is running determines which flow to use — this skill does not route per-request.

## When to Use

- Start or stop a real-time alert on a sensor ("Start real-time alert for boxes dropped on sensor warehouse_sample")
- List or query detected incidents / alerts
- Add a new camera to the alerts pipeline
- Customize the VLM-verifier prompts (CV mode)
- Check verdicts (confirmed / rejected / unverified)

---

## The Two Modes (Deploy-Time Choice)

| Mode | Deploy flag | Env (`.env`) | What runs | How alerts are created |
|---|---|---|---|---|
| **CV (verification)** | `-m verification` | `MODE=2d_cv` | RT-CV (Grounding DINO) + Behavior Analytics + `alert-bridge` VLM verifier | **Static:** any RTSP flowing through VIOS is auto-processed; Behavior Analytics emits candidates; VLM verifies each clip per `alert_type_config.json` |
| **VLM (real-time)** | `-m real-time` | `MODE=2d_vlm` | `rtvi-vlm` continuous inference | **Dynamic:** user asks the VSS Agent to start monitoring a sensor with a natural-language detection prompt |

**Mode is static.** Switching requires `deploy down` + `deploy up` with the other `-m` flag — see the `deploy` skill. Never assume both flows are available at once.

---

## Step 1 — Detect the Currently Deployed Mode

Before running any alert workflow, check which mode is live. Use container names as the signal:

```bash
# VLM real-time mode
docker ps --format '{{.Names}}' | grep -qx rtvi-vlm && echo "mode=VLM"

# CV verification mode (behavior analytics is CV-only; alert-bridge is the VLM verifier)
docker ps --format '{{.Names}}' | grep -qx vss-behavior-analytics-alerts && echo "mode=CV"
```

Exactly one of these should match on a healthy alerts deployment. If neither matches, the alerts profile is not deployed — direct the user to the `deploy` skill.

Alternative signal (if `docker ps` isn't available in the current context): check the profile's `.env`:

```bash
grep -E '^MODE=' deployments/developer-workflow/dev-profile-alerts/.env
# MODE=2d_cv   → CV mode
# MODE=2d_vlm  → VLM real-time mode
```

---

## Step 2 — Route by Deployed Mode

| Deployed mode | User asks about… | Action |
|---|---|---|
| **CV** | any alert request | Run **Workflow A (CV)** — onboard RTSP via `vios` skill, then query alerts. No per-request create call. |
| **CV** | specifically a VLM real-time alert ("start alert for boxes dropped…") | **Redeployment required.** Confirm with the user first, then point to the `deploy` skill to tear down and redeploy with `-m real-time`. Do not attempt to run the VLM flow on a CV deployment — the agent's `rtvi_vlm_alert` tool will fail because `rtvi-vlm` is not running. |
| **VLM** | any alert request | Run **Workflow B (VLM)** — call the VSS Agent with a detection prompt. |
| **VLM** | specifically a CV / behavior-analytics / PPE-rule alert | **Redeployment required.** Confirm, then point to `deploy` skill for `-m verification`. The CV pipeline (RT-CV, Behavior Analytics, `alert-bridge`) is not running on a VLM deployment. |

**Always confirm before triggering a redeploy.** A mode switch stops all currently-running monitoring and restarts services.

---

## Prereq for Either Mode: Sensor Must Be in VIOS

Both modes require the camera to be registered in VIOS first.

- If the user hands you only an RTSP URL (or an IP camera) — **defer to the `vios` skill** to add it via `POST /sensor/add` (see `vios` skill Section 6). Record the returned `sensorId` / name.
- If the user names an existing sensor — confirm it is listed by `GET /sensor/list` via the `vios` skill before proceeding.

On a **CV deployment**, adding the RTSP is the *entire* onboarding step — the pipeline picks up the stream automatically once it is in VIOS. On a **VLM deployment**, adding the RTSP is a prerequisite to Workflow B.

---

## The Agent `/generate` Endpoint

All VLM-flow actions and all query actions go through the VSS Agent's natural-language endpoint:

```bash
AGENT="http://<AGENT_ENDPOINT>"   # default http://localhost:8000 on the alerts profile

curl -s -X POST "$AGENT/generate" \
  -H "Content-Type: application/json" \
  -d '{"input_message": "<natural-language request>"}' | jq .
```

**Endpoint resolution:** use the agent endpoint from the active VSS deployment context. If unavailable, ask the user. Do not discover via filesystem.

**Availability check:** `curl -sf --connect-timeout 5 "$AGENT/docs"`.

Do not call the `rtvi-vlm` microservice endpoints directly — always go through the agent. The agent internally dispatches to `rtvi_vlm_alert`, `rtvi_prompt_gen`, and `video_analytics_mcp.get_incidents`.

---

## Workflow A — CV Mode (deployment is `-m verification` / `MODE=2d_cv`)

On a CV deployment, alerts are **deployment-driven, not request-driven**. There is no agent call to "create" an alert.

1. **Onboard the camera** — add the RTSP to VIOS via the `vios` skill (`POST /sensor/add`). Once registered and online, the CV pipeline picks it up automatically.
2. **Confirm the sensor is online:**

   ```bash
   curl -s "http://<VST_ENDPOINT>/vst/api/v1/sensor/<sensorId>/status" | jq .
   ```

3. **Wait for alerts to land in Elasticsearch.** Behavior Analytics emits candidates that match configured rules; `alert-bridge` calls the VLM to confirm/reject each candidate per `alert_type_config.json`. Use **Workflow C** to query results.

If the user asks you to "start a real-time alert" on a CV deployment, that is a mode mismatch — see the routing table above.

---

## Workflow B — VLM Mode (deployment is `-m real-time` / `MODE=2d_vlm`)

On a VLM deployment, the user drives alert creation via natural-language requests to the VSS Agent. The agent calls `rtvi_prompt_gen` to turn the description into a Yes/No detection question, then `rtvi_vlm_alert` with `action="start"` to register the stream with `rtvi-vlm` and begin continuous monitoring.

**Canonical sample request:**

```bash
curl -s -X POST "$AGENT/generate" \
  -H "Content-Type: application/json" \
  -d '{"input_message": "Start real-time alert for boxes dropped on sensor warehouse_sample"}' | jq .
```

More examples:

```bash
# Vehicle collisions on a street cam
curl -s -X POST "$AGENT/generate" -H "Content-Type: application/json" \
  -d '{"input_message": "Start real-time alert for vehicle collisions on sensor Camera_02"}' | jq .

# Forklift-pedestrian proximity
curl -s -X POST "$AGENT/generate" -H "Content-Type: application/json" \
  -d '{"input_message": "Monitor Warehouse_Dock_3 for a forklift passing within 1 meter of a pedestrian"}' | jq .

# Generic start (no specific target — uses default prompt)
curl -s -X POST "$AGENT/generate" -H "Content-Type: application/json" \
  -d '{"input_message": "Start real-time alert for sensor warehouse_sample"}' | jq .
```

**What the agent does under the hood:**
1. `rtvi_prompt_gen` — converts "boxes dropped" → `prompt: "Detect for a box being dropped. Answer in Yes or No"`, `system_prompt: "You are a helpful assistant."`.
2. `rtvi_vlm_alert action="start"` — looks up the sensor in VIOS live streams, calls `POST /v1/streams/add` and `POST /v1/generate_captions_alerts` (`stream=true`) on `rtvi-vlm`. Returns `stream_id`.

**Alert semantics:** every chunk is captioned; a chunk whose VLM response contains **`"yes"` or `"true"`** (case-insensitive) triggers an incident published to the Kafka incident topic (`mdx-vlm-incidents` on the alerts profile). That is why prompts must force a Yes/No answer.

**Stop monitoring:**

```bash
curl -s -X POST "$AGENT/generate" -H "Content-Type: application/json" \
  -d '{"input_message": "Stop real-time alert for sensor warehouse_sample"}' | jq .
```

If the user asks you to flag a scenario that matches a CV `alert_type` (e.g. "ladder PPE violation") on a VLM deployment, that is a mode mismatch — see the routing table above.

---

## Workflow C — Query / List Alerts (works on either mode)

Both CV- and VLM-generated alerts land in Elasticsearch and are queryable via the agent's `video_analytics_mcp.get_incidents` tool.

```bash
curl -s -X POST "$AGENT/generate" -H "Content-Type: application/json" \
  -d '{"input_message": "Show me recent alerts for sensor warehouse_sample"}' | jq .

curl -s -X POST "$AGENT/generate" -H "Content-Type: application/json" \
  -d '{"input_message": "List confirmed alerts from the last hour"}' | jq .

curl -s -X POST "$AGENT/generate" -H "Content-Type: application/json" \
  -d '{"input_message": "Were there any PPE violations today on Camera_02?"}' | jq .

curl -s -X POST "$AGENT/generate" -H "Content-Type: application/json" \
  -d '{"input_message": "Show collision incidents from Camera_02 between 2026-04-23T00:00:00.000Z and 2026-04-23T23:59:59.000Z"}' | jq .
```

For richer / non-natural-language filtering (sensor-level, time-series, counts): use the **`video-analytics` skill** (VA-MCP on port 9901).

### Verdict interpretation (CV mode only)

Verified alerts carry an extended `info` block:

| `verdict` | Meaning |
|---|---|
| `confirmed` | VLM determined the alert is real |
| `rejected` | VLM determined it is a false positive |
| `unverified` | Verification could not complete (error) |

Check `verification_response_code` (200 = success) and `reasoning` for the VLM's explanation. VLM-mode incidents are always "confirmed" at source (the trigger itself is a Yes/No VLM answer), so there is no separate verdict field.

---

## Customize CV Verifier Prompts (CV mode only)

CV-path verifier prompts live in:

```
deployments/developer-workflow/dev-profile-alerts/vlm-as-verifier/configs/alert_type_config.json
```

Each entry maps a CV `alert_type` (the `category` field emitted by Behavior Analytics) to the VLM prompts used for verification:

```json
{
  "version": "1.0",
  "alerts": [
    {
      "alert_type": "FOV Count Violation",
      "output_category": "Ladder PPE Violation",
      "prompts": {
        "system": "You are a helpful assistant.",
        "user": "Is anyone on the ladder without a hardhat and safety vest? Answer yes or no.",
        "enrichment": "Describe the PPE violation in detail..."
      }
    }
  ]
}
```

- **`alert_type`** must match the `category` emitted by Behavior Analytics.
- **`output_category`** is the display name in Elasticsearch / UI.
- **`enrichment`** (optional) triggers a second VLM call for a richer description; requires `alert_agent.enrichment.enabled: true`.
- **Changes require a restart** of the `alert-bridge` (vlm-as-verifier) container.

**VLM real-time prompts are not configured in a file** — they are per-request, shaped by `rtvi_prompt_gen` from the user's natural-language detection description.

---

## Cross-Skill Links

| Task | Skill |
|---|---|
| Deploy, redeploy, or switch alert mode | **`deploy`** skill — `deploy -p alerts -m {verification,real-time}` |
| Add an RTSP / IP camera to VIOS | **`vios`** skill — Section 6 (Add Sensor / Stream) |
| List sensors, take a snapshot, download a clip | **`vios`** skill |
| Time-range incident / occupancy / PPE metrics from Elasticsearch | **`video-analytics`** skill (VA-MCP :9901) |
| Generate a detailed incident report from an alert | **`incident-report`** skill |

---

## Gotchas

- **Mode is static.** Do not attempt to run the VLM flow on a CV deployment or vice versa — required services won't be running. Confirm with the user, then route to the `deploy` skill for redeployment.
- **A mode switch tears down the current deployment.** Any running VLM monitoring streams and any CV alert state not already in Elasticsearch will be lost.
- **Don't call the `rtvi-vlm` microservice directly** from this skill. Always go through `$AGENT/generate`. The agent handles sensor→RTSP lookup, stream registration, and teardown.
- **Sensor must already be in VIOS** for either mode. If the user hands you only an RTSP URL, use the `vios` skill first.
- **VLM alert trigger is a `"yes"` / `"true"` token match** on the VLM response (case-insensitive). `rtvi_prompt_gen` enforces the Yes/No pattern — don't hand-craft prompts that break it.
- **Stopping a VLM alert is one agent call** ("Stop real-time alert…"); the agent handles both the caption-stream and the stream-registration teardown.
- **Prompt changes to `alert_type_config.json` need an `alert-bridge` restart.** `alert_agent.enrichment.enabled: true` is required for the `enrichment` prompt to fire.
