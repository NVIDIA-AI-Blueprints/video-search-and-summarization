---
name: vss-manage-alerts
description: Use when asked about real-time alerts, alert subscription rules (create/list/delete via Alert Bridge), Slack webhook notifications for incidents, incident queries, camera onboarding for alerts, VLM verifier prompt customization, or alert verdicts.
license: Apache-2.0
metadata:
  version: "3.2.0"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint operational"
---

# VSS Alert Management

The alerts profile is deployed in one of two modes at a time. The mode is chosen at `/vss-deploy-profile -p alerts -m {verification,real-time}`.

- **CV (verification)** mode runs the static CV pipeline (RT-CV + Behavior Analytics + `alert-bridge` VLM verifier) **and** the dynamic `rtvi-vlm` real-time service. Workflow A (static CV alerts) and Workflow B (VLM monitoring) are available; Workflows D and E require VLM real-time mode.
- **VLM (real-time)** mode runs **only** `rtvi-vlm` for dynamic real-time alerts. CV pipeline (RT-CV, Behavior Analytics) is not running, so Workflow A is unavailable.

This skill routes by **deployed mode + user intent** (monitoring vs subscription CRUD vs Slack webhook operations).

## When to Use

- Start or stop a real-time alert on a sensor ("Start real-time alert for boxes dropped on sensor warehouse_sample")
- Create, list, or stop realtime subscription rules on Alert Bridge ("List active realtime rules on warehouse-dock-1")
- Set up or manage Slack incident notifications ("Start alert Slack webhook and send test notification")
- List or query detected incidents / alerts
- Add a new camera to the alerts pipeline
- Customize the VLM-verifier prompts (CV mode)
- Check verdicts (confirmed / rejected / unverified)

---

## Deployment prerequisite

This skill requires the VSS **alerts** profile running on the host at `$HOST_IP`, in either `verification` or `real-time` mode. Before any request:

1. Probe the stack:
   ```bash
   # Either perception-alerts (CV mode) OR rtvi-vlm (VLM mode) must be present.
   curl -sf --max-time 5 "http://${HOST_IP}:8000/docs" >/dev/null \
     && docker ps --format '{{.Names}}' \
        | grep -qE '^(perception-alerts|rtvi-vlm)$'
   ```

2. **If the probe fails**, ask the user:
   > *"The VSS `alerts` profile isn't running on `$HOST_IP`. Which mode should I deploy — `verification` (CV) or `real-time` (VLM)?"*

   - Answer → hand off to the `/vss-deploy-profile` skill with `-p alerts -m <mode>`. Return here once it succeeds.
   - If the user declines → stop. Do not run this skill against a missing stack.

   (If your caller has granted explicit pre-authorization to deploy
   autonomously — e.g. the request says "pre-authorized to deploy
   prerequisites", or you are running in a non-interactive evaluation
   harness with that permission — skip the confirmation and invoke
   `/vss-deploy-profile` directly. Default the mode to `verification` unless the
   request specifies otherwise.)

3. If the probe passes, detect the mode per § Step 1 below.

---

## The Two Modes (Deploy-Time Choice)

| Mode | Deploy flag | Env (`.env`) | What runs | What is available |
|---|---|---|---|---|
| **CV (verification)** | `-m verification` | `MODE=2d_cv` | RT-CV (Grounding DINO) + Behavior Analytics + `alert-bridge` VLM verifier + **`rtvi-vlm`** | **Both** static CV pipeline (Workflow A) **and** dynamic VLM real-time alerts (Workflows B/D) |
| **VLM (real-time)** | `-m real-time` | `MODE=2d_vlm` | `alert-bridge` + `rtvi-vlm` | **Only** dynamic VLM real-time alerts (Workflows B/D) and `alert-bridge` backend. No static CV pipeline. |

**Switching modes** requires the `vss-deploy-profile` teardown and deploy flow with the other `-m` flag. Going from VLM → CV adds the static CV pipeline; going from CV → VLM tears down the CV pipeline. `rtvi-vlm` is present in both modes.

---

## Step 1 — Detect the Currently Deployed Mode

Before running any alert workflow, check which mode is live. Use **CV-only** containers as the signal — `rtvi-vlm` is **not** a reliable mode signal anymore because it runs in both modes.

```bash
# CV verification mode (behavior analytics + perception-alerts are CV-only)
docker ps --format '{{.Names}}' | grep -qx vss-behavior-analytics-alerts && echo "mode=CV"

# VLM real-time mode (no CV pipeline; only rtvi-vlm)
docker ps --format '{{.Names}}' | grep -qx vss-behavior-analytics-alerts || \
  docker ps --format '{{.Names}}' | grep -qx rtvi-vlm && echo "mode=VLM"
```

If `vss-behavior-analytics-alerts` is present → **CV mode** (which also has `rtvi-vlm`).
If only `rtvi-vlm` is present (and no CV pipeline) → **VLM mode**.
If neither matches, the alerts profile is not deployed — direct the user to the `vss-deploy-profile` skill.

Alternative signal (preferred when `docker ps` isn't accessible): check the profile's `.env`:

```bash
grep -E '^MODE=' deployments/developer-workflow/dev-profile-alerts/.env
# MODE=2d_cv   → CV mode (full superset)
# MODE=2d_vlm  → VLM real-time mode (rtvi-vlm only)
```

---

## Step 2 — Route by Deployed Mode

| Deployed mode | User asks about… | Action |
|---|---|---|
| **VLM real-time** | Slack webhook setup/status/test/stop | Run **Workflow E (Slack Notifications)** — follow `references/alert-notify-slack.md` |
| **VLM real-time** | subscription / rule CRUD, or **set up / create / watch / flag** a realtime alert on a specific sensor with a detection condition | Run **Workflow D (Alert Subscriptions)** — follow `references/alert-subscriptions.md` for Alert Bridge rule management. |
| **CV verification** | subscription/rule CRUD or Slack/notification setup | Refuse — see Canonical refusal text below |
| **CV or VLM** | generic start/stop monitoring via VSS Agent **without** a specific detection condition (e.g. "start real-time alert for sensor warehouse_sample") | Run **Workflow B (VLM)** — call the VSS Agent with a detection prompt. `rtvi-vlm` runs in both modes. |
| **CV or VLM** | incident lookup / list / query (recent alerts, time-range queries) | Run **Workflow C (Query)** — `video_analytics_mcp.get_incidents` works on both deployments. |
| **CV** | static CV alert onboarding (just add the camera and let CV pipeline emit alerts) / verdict prompts customization | Run **Workflow A (CV)** — onboard RTSP via `vss-manage-video-io-storage` skill; CV pipeline picks it up automatically. No per-request create call. |
| **VLM** | specifically a CV / behavior-analytics / PPE-rule alert that requires the static CV pipeline | **Redeployment required.** Confirm with the user first, then point to the `vss-deploy-profile` skill for `-m verification`. |

**Always confirm before triggering a redeploy.** A mode switch stops all currently-running monitoring and restarts services.

### Intent precedence (to avoid overlap)

Apply these matching rules top-to-bottom; first match wins:

1. **Workflow E (Slack):** contains Slack-specific keywords (`slack`, `webhook` **co-occurring with** `slack`, `test notification` **to Slack**, `bot token`, `slack channel`). The word `notify` alone is **not** sufficient — it must appear alongside `slack` or `webhook` to trigger this workflow. Phrases like "notify me when …" or "alert and notify on …" without Slack/webhook context are **not** Slack intents.
2. **Workflow D (Subscriptions):** the user's message targets a **specific sensor** AND describes a **specific detection condition** (what to watch for). Trigger keywords include `rule`, `subscription`, `create/list/delete realtime rule`, explicit rule ID, **or** natural-language phrasing that pairs a sensor with a condition: `set up … alert on <sensor> for <condition>`, `watch <sensor> for …`, `flag … on <sensor>`, `monitor <sensor> for <condition>`, `create an alert on <sensor> for …`, `alert me if … on <sensor>`. If both a sensor name and a detection condition are present, route here — even without the words "rule" or "subscription".
3. **Workflow B (VLM monitoring):** generic start/stop/monitor intent that names a sensor but does **not** specify a detection condition (e.g. "Start real-time alert for sensor warehouse_sample", "Stop alert on Camera_02"). This workflow also covers cases where the user explicitly asks the VSS Agent to handle a prompt.
4. **Workflow C (Query):** incident lookup/reporting requests (`show/list incidents`, `recent alerts`, time-range queries).
5. **Workflow A (CV):** CV deployment handling when not matched by higher-priority intents.

**Disambiguation rule (B vs D):** If a prompt is ambiguous — it names a sensor and uses start/monitor language but you cannot tell whether a detection condition is present or the user wants ad-hoc agent monitoring vs a persistent subscription rule — ask one clarifying question:
> *"Do you want me to (a) create a persistent alert rule on Alert Bridge that keeps running until you delete it, or (b) start a one-time monitoring session via the VSS Agent?"*

If a prompt mixes two workflows ("start monitoring and send to Slack"), ask one clarifying question to split execution order.

### CV-mode refusal text for D and E intents

When the deployed mode is CV verification and the user asks for an alert-subscription or Slack/notification intent, refuse with this message verbatim:

> "Alert subscriptions and Slack notifications are only supported in VLM real-time mode. Your current deployment is `<CV verification | not deployed>`. To use these features, redeploy with `/vss-deploy-profile -p alerts -m real-time` (note: switching tears down current CV monitoring)."

No auto-redeploy. The user decides whether to switch modes.

---

## Prereq for Either Mode: Sensor Must Be in VIOS

Both modes require the camera to be registered in VIOS first.

- If the user hands you only an RTSP URL (or an IP camera) — **defer to the `vss-manage-video-io-storage` skill** to add it via `POST /sensor/add` (see `vss-manage-video-io-storage` skill Section 6). Record the returned `sensorId` / name.
- If the user names an existing sensor — confirm it is listed by `GET /sensor/list` via the `vss-manage-video-io-storage` skill before proceeding.

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

1. **Check if the sensor is already in VIOS** (idempotency — never blindly POST `/sensor/add`). Use the `vss-manage-video-io-storage` skill's `GET /sensor/list`:
   - If the user gave a **sensor name** that matches an existing entry — skip to Step 3 (already onboarded).
   - If the user gave an **RTSP URL** that matches an existing sensor's stream URL — skip to Step 3 (already onboarded).
   - Otherwise, the sensor is missing — continue to Step 2.

2. **Onboard only if missing** — add the RTSP to VIOS via the `vss-manage-video-io-storage` skill (`POST /sensor/add`, see `vss-manage-video-io-storage` skill Section 6). Record the returned `sensorId` / name. Once registered and online, the CV pipeline picks up the stream automatically.

3. **Confirm the sensor is online:**

   ```bash
   curl -s "http://<VST_ENDPOINT>/vst/api/v1/sensor/<sensorId>/status" | jq .
   ```

4. **Wait for alerts to land in Elasticsearch.** Behavior Analytics emits candidates that match configured rules; `alert-bridge` calls the VLM to confirm/reject each candidate per `alert_type_config.json`. Use **Workflow C** to query results.

**Idempotent by design** — re-running this workflow on an already-onboarded sensor is safe: Step 1 detects existing registration and skips Step 2.

If the user asks for a static-CV-pipeline alert (driven by Behavior Analytics) on a VLM-only deployment, that is a mode mismatch — see the routing table above.

---

## Workflow B — VLM Real-time Monitoring (works on both CV and VLM deployments)

This workflow handles **generic monitoring intents** (start/stop) via natural-language requests to the VSS Agent — specifically when the user names a sensor but does **not** provide a specific detection condition, or when the user explicitly asks the VSS Agent to handle the request. If the user specifies both a sensor **and** a detection condition (what to watch for), route to **Workflow D** instead.

`rtvi-vlm` runs in both CV and VLM modes, so this workflow is available regardless of the deployed mode. The agent calls `rtvi_prompt_gen` to turn the description into a Yes/No detection question, then `rtvi_vlm_alert` with `action="start"` to register the stream with `rtvi-vlm` and begin continuous monitoring.

**Canonical sample request (generic start — no specific detection condition):**

```bash
curl -s -X POST "$AGENT/generate" \
  -H "Content-Type: application/json" \
  -d '{"input_message": "Start real-time alert for sensor warehouse_sample"}' | jq .
```

More examples:

```bash
# Generic start on a different sensor (uses default detection prompt)
curl -s -X POST "$AGENT/generate" -H "Content-Type: application/json" \
  -d '{"input_message": "Start real-time alert on Camera_02"}' | jq .
```

> **Note:** Requests that include a specific detection condition (e.g. "Start real-time alert for **boxes dropped** on sensor warehouse_sample", "Monitor Warehouse_Dock_3 for **a forklift passing within 1 meter of a pedestrian**") are now routed to **Workflow D** (Alert Subscriptions via Alert Bridge) instead. The examples below are kept for reference on how the VSS Agent processes them under the hood, but the routing entry point is Workflow D.
>
> ```bash
> # These route to Workflow D first, which calls Alert Bridge — not this workflow
> # "Start real-time alert for boxes dropped on sensor warehouse_sample"
> # "Monitor Warehouse_Dock_3 for a forklift passing within 1 meter of a pedestrian"
> # "Start real-time alert for vehicle collisions on sensor Camera_02"
> ```

**What the agent does under the hood (when Workflow B is the active path):**
1. `rtvi_prompt_gen` — if no detection condition is given, uses a default prompt; otherwise converts the description → `prompt: "Detect for <condition>. Answer in Yes or No"`, `system_prompt: "You are a helpful assistant."`.
2. `rtvi_vlm_alert action="start"` — looks up the sensor in VIOS live streams, then calls the Alert Bridge realtime API to register the stream with `rtvi-vlm` and start caption generation. Returns an alert rule ID.

**Alert semantics:** every chunk is captioned; a chunk whose VLM response contains **`"yes"` or `"true"`** (case-insensitive) triggers an incident published to the Kafka incident topic (`mdx-vlm-incidents` on the alerts profile). That is why prompts must force a Yes/No answer.

**Stop monitoring:**

```bash
curl -s -X POST "$AGENT/generate" -H "Content-Type: application/json" \
  -d '{"input_message": "Stop real-time alert for sensor warehouse_sample"}' | jq .
```

If the user explicitly asks for a **static CV pipeline** alert (e.g. configured PPE-rule alerts driven by Behavior Analytics, not on-demand VLM detection) on a VLM-only deployment, that is a mode mismatch — see the routing table above. On a CV deployment both styles work.

---

## Workflow D — Alert Subscriptions (VLM real-time mode only, nested workflow)

Use this workflow when the user wants to create, list, or delete persistent realtime alert rules on Alert Bridge. This includes both explicit rule-management requests (using words like "rule", "subscription", rule IDs) **and** natural-language requests that pair a specific sensor with a specific detection condition — even without rule/subscription terminology.

**Route here when the prompt contains a sensor name + a detection condition**, e.g.:
- "Set up a realtime alert on warehouse-dock-1 for PPE violations"
- "Monitor camera-lobby for unauthorized access after hours"
- "Create an alert on parking-cam-3 for vehicle collisions"
- "Watch sensor entrance-1 for tailgating"
- "Alert me if someone enters restricted zone on cam-floor-2"
- "Flag anyone without a safety vest on warehouse-dock-1"
- "Show me all active realtime rules"
- "Stop rule 496aebd1-16d0-4123-81cf-10603e047d02"
- "List active rules on warehouse-dock-1"

**Do NOT route here** for generic start/stop without a detection condition (→ Workflow B) or for Slack-specific operations (→ Workflow E).

Execution rule:
- Load and follow `references/alert-subscriptions.md` as the authoritative playbook for subscription CRUD.
- Keep this `alerts` skill as the entrypoint and router; treat `references/alert-subscriptions.md` as a delegated sub-workflow.
- VLM real-time mode only. Subscription and notification surfaces are scoped to real-time mode by design; refuse and surface the redeploy hint on CV.

---

## Workflow E — Slack Notifications (nested workflow)

Use this workflow when the user **explicitly mentions Slack or the webhook relay** for incidents (start/stop webhook server, check status/health, send test message, or set Slack channel/token). The word "notify" alone does **not** trigger this workflow — it must co-occur with `slack`, `webhook`, or `bot token`.

> **`alert-notify-slack` (port 9090) ≠ `vss-alert-bridge` (`/api/v1/realtime`).** Do NOT interact with `vss-alert-bridge` for Slack operations — that service handles VLM verification (Workflow D), not Slack.

Examples:
- "Set up Slack notifications for alerts"
- "Check if alert-notify-slack is running"
- "Send a test alert notification to Slack"
- "Start the alert webhook for Slack"
- "Slack webhook start"

**Not** this workflow (route elsewhere):
- "Notify me when someone enters the zone" → this is an alert creation intent (Workflow D or B), not a Slack setup request
- "Alert and notify on my phone" → not Slack-specific, ask the user to clarify

Execution rule:
- Load and follow `references/alert-notify-slack.md` as the authoritative playbook. Code lives in `scripts/alert-notify-slack/`.
- Keep this `alerts` skill as the entrypoint and router; treat `references/alert-notify-slack.md` as a delegated sub-workflow.
- VLM real-time mode only. Requires the VLM profile deployed; the parent skill verifies mode before invoking E.

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

For richer / non-natural-language filtering (sensor-level, time-series, counts): use the **`vss-query-analytics` skill** (VA-MCP on port 9901).

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
| Deploy, redeploy, or switch alert mode | **`vss-deploy-profile`** skill — `/vss-deploy-profile -p alerts -m {verification,real-time}` |
| Add an RTSP / IP camera to VIOS | **`vss-manage-video-io-storage`** skill — Section 6 (Add Sensor / Stream) |
| List sensors, take a snapshot, download a clip | **`vss-manage-video-io-storage`** skill |
| Time-range incident / occupancy / PPE metrics from Elasticsearch | **`vss-query-analytics`** skill (VA-MCP :9901) |
| Generate a detailed incident report from an alert | **`vss-generate-video-report`** skill |
| Alert subscriptions (create/list/delete rules) | Sub-workflow: `references/alert-subscriptions.md` |
| Forward incidents to Slack webhook | Sub-workflow: `references/alert-notify-slack.md`, code in `scripts/alert-notify-slack/` |

---

## Gotchas

- **`alert-notify-slack` (port 9090) ≠ `vss-alert-bridge`.** "Slack webhook" → Workflow E (`alert-notify-slack`). Never route Slack intents to `vss-alert-bridge`'s `/api/v1/realtime`.
- **Workflow scope by mode:** Workflow A is CV-only. Workflows B and C work on either mode. Workflows D and E (subscriptions and Slack) are VLM real-time only — refuse with the canonical refusal text if attempted on CV.
- **Don't use `rtvi-vlm` container presence as a mode signal.** It runs in both modes. Use `vss-behavior-analytics-alerts` (CV-only) or the `MODE` env var instead.
- **A mode switch tears down the current deployment.** Any running VLM monitoring streams and any CV alert state not already in Elasticsearch will be lost.
- **Don't call the `rtvi-vlm` microservice directly** from this skill. Always go through `$AGENT/generate`. The agent handles sensor→RTSP lookup, stream registration, and teardown.
- **Sensor must already be in VIOS** for either mode. If the user hands you only an RTSP URL, use the `vss-manage-video-io-storage` skill first.
- **VLM alert trigger is a `"yes"` / `"true"` token match** on the VLM response (case-insensitive). `rtvi_prompt_gen` enforces the Yes/No pattern — don't hand-craft prompts that break it.
- **Stopping a VLM alert is one agent call** ("Stop real-time alert…"); the agent handles both the caption-stream and the stream-registration teardown.
- **Prompt changes to `alert_type_config.json` need an `alert-bridge` restart.** `alert_agent.enrichment.enabled: true` is required for the `enrichment` prompt to fire.
