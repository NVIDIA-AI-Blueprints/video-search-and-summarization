---
name: vss-manage-alerts
description: Use for VSS alert workflows — real-time monitoring, Alert-Bridge subscriptions, Slack notifications, incident queries, camera onboarding. Not for non-alert analytics.
license: Apache-2.0
metadata:
  version: "3.2.0"
  author: "NVIDIA Video Search and Summarization team"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint operational"
---
## Purpose

Operate the VSS alert pipeline (mode detection, Alert-Bridge subscriptions, Slack notifications, queries, camera onboarding, verifier-prompt customization).

## Prerequisites

- Active VSS deployment reachable on `$HOST_IP` (see `vss-deploy-profile` and `references/`).
- NGC credentials in `$NGC_CLI_API_KEY` and `$NVIDIA_API_KEY` for any image pulls.
- `curl`, `jq`, and Docker available on the caller.

## Instructions

Follow the routing tables and step-by-step workflows below. Each section that ends in *workflow*, *quick start*, or *flow* is intended to be executed top-to-bottom. Detailed reference material lives in `references/` and helper scripts live in `scripts/` — call them via `run_script` when the skill points to a script by name.

## Examples

Worked end-to-end examples are kept under `evals/` (each `*.json` manifest contains a runnable scenario) and inline in the per-workflow `curl` blocks below. Run a Tier-3 evaluation with `nv-base validate <this-skill-dir> --agent-eval` to replay them.

## Limitations

- Requires the matching VSS profile / microservice to be deployed and reachable from the caller.
- NGC-hosted models and NIMs may be subject to rate-limits, GPU memory requirements, and license restrictions.
- Concurrency, GPU memory, and storage limits depend on the host hardware and the profile's compose file.

## Troubleshooting

- **Error**: REST call returns connection refused. **Cause**: target microservice not running. **Solution**: probe `/docs` or `/health`; redeploy via `vss-deploy-profile` or the matching `vss-deploy-*` skill.
- **Error**: HTTP 401/403 from NGC pulls. **Cause**: missing/expired `NGC_CLI_API_KEY`. **Solution**: `docker login nvcr.io` and re-export the key before retrying.
- **Error**: container OOM or model fails to load. **Cause**: insufficient GPU memory for the selected profile. **Solution**: switch to a smaller variant or free GPUs via `docker compose down`.

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

Requires the VSS **alerts** profile on `$HOST_IP` in either `verification` (CV) or `real-time` (VLM) mode.

```bash
# Either perception-alerts (CV mode) OR rtvi-vlm (VLM mode) must be present.
curl -sf --max-time 5 "http://${HOST_IP}:8000/docs" >/dev/null \
  && docker ps --format '{{.Names}}' \
     | grep -qE '^(perception-alerts|rtvi-vlm)$'
```

If the probe fails, ask the user which mode to deploy and hand off to
`/vss-deploy-profile -p alerts -m <mode>`. If the user declines, stop. If the
caller pre-authorized autonomous deploy, run it directly with mode
`verification` by default. If it passes, detect the mode per Step 1.

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
| **VLM real-time** | Slack webhook setup/status/test/stop | Run **Workflow E (Slack Notifications)** — follow `references/alert-notify.md` |
| **VLM real-time** | subscription / rule CRUD, or **set up / create / watch / flag** a realtime alert on a specific sensor with a detection condition, **or stop / delete a named alert** (by `alert_type`/condition or rule ID, e.g. "stop the PPE alert on warehouse_sample", "delete the collision rule") | Run **Workflow D (Alert Subscriptions)** — follow `references/alert-subscriptions.md` for Alert Bridge rule management (including the two-step stop/confirm protocol). |
| **CV verification** | subscription/rule CRUD or Slack/notification setup | Refuse — see Canonical refusal text below |
| **CV or VLM** | generic start/stop monitoring via VSS Agent **without** a specific detection condition (e.g. "start real-time alert for sensor warehouse_sample") | Run **Workflow B (VLM)** — call the VSS Agent with a detection prompt. `rtvi-vlm` runs in both modes. |
| **CV or VLM** | incident lookup / list / query — *what happened* (recent alerts, time-range queries, **and casual phrasings** like "any alerts so far today?", "any alerts today?", "what's been triggered?", "anything detected?") | Run **Workflow C (Query)** — `video_analytics_mcp.get_incidents` works on both deployments. **Always execute the query — never answer an incident question from memory.** |
| **CV** | static CV alert onboarding (just add the camera and let CV pipeline emit alerts) / verdict prompts customization | Run **Workflow A (CV)** — onboard RTSP via `vss-manage-video-io-storage` skill; CV pipeline picks it up automatically. No per-request create call. |
| **VLM** | specifically a CV / behavior-analytics / PPE-rule alert that requires the static CV pipeline | **Redeployment required.** Confirm with the user first, then point to the `vss-deploy-profile` skill for `-m verification`. |

**Always confirm before triggering a redeploy.** A mode switch stops all currently-running monitoring and restarts services.

### Intent precedence (first match wins)

1. **Workflow E (Slack)** — Slack-specific keywords (`slack`, `webhook` + `slack`, `bot token`, `slack channel`). `notify` alone is **not** sufficient.
2. **Workflow D (Subscriptions)** — sensor name **plus** a detection condition, or rule CRUD keywords (`rule`, `subscription`, rule ID), **or stopping/deleting a named alert by its type/condition** (e.g. "stop the PPE alert on warehouse_sample", "delete the collision alert", "turn off the fire alert"). Naming an `alert_type`/condition refers to an existing **rule** → use D's two-step stop protocol (find the rule via `GET /api/v1/realtime`, ask the yes/no confirmation, then delete). Do **not** route a named-alert stop to Workflow B.
3. **Workflow B (VLM monitoring)** — generic start/stop on a sensor with **no** detection condition and **no** alert-type qualifier (e.g. "start real-time alert for sensor warehouse_sample", "stop real-time alert for sensor warehouse_sample"). If a stop names an alert type/condition ("stop the **PPE** alert"), it is a rule stop → Workflow D, not B.
4. **Workflow C (Query)** — incident lookup / *what happened* (`show/list incidents`, `recent alerts`, time-range queries, **and casual "any alerts…?" / "any alerts so far today?" / "what's been triggered?" phrasings**). Bare `alerts` (without `rule`/`subscription`/`active rules`) means **incidents** → Workflow C, never Workflow D.
5. **Workflow A (CV)** — CV deployment handling for anything not matched above.

> **`alerts` vs `alert rules` (C vs D):** a question about *what happened / has been triggered* (incidents) → **Workflow C** — answered via `POST /generate` **or** the Alert Bridge **incidents** endpoint `GET /api/v1/realtime/incidents` (note the `/incidents` suffix). A question about *what rules/subscriptions are configured or currently active* → **Workflow D** — the **bare** rules list `GET /api/v1/realtime` (no `/incidents`). The word `alerts` on its own = incidents (C); `alert rules` / `subscriptions` / `currently active rules` = inventory (D). Pick **exactly one** workflow — never run both for one query. For an incident query, **never list subscription rules via the bare `GET /api/v1/realtime`** (that is Workflow D); but `GET /api/v1/realtime/incidents` **is** a valid Workflow C incident query. Never answer from memory — execute the correct call.

**Disambiguation (B vs D):** if a sensor is named with start/monitor language but the detection condition is unclear, ask:
> *"Do you want me to (a) create a persistent alert rule on Alert Bridge that keeps running until you delete it, or (b) start a one-time monitoring session via the VSS Agent?"*

**Stop routing (B vs D):** "Stop the **&lt;type&gt;** alert on &lt;sensor&gt;" (names an `alert_type`/condition such as PPE, collision, fire, loitering) = stopping an existing **subscription rule** → **Workflow D**: do not call `POST /generate`; instead find the rule via Alert Bridge `GET /api/v1/realtime`, then follow the two-step stop/confirm protocol in `references/alert-subscriptions.md`. Only a bare "stop real-time alert / stop monitoring on &lt;sensor&gt;" with **no** alert-type qualifier is a Workflow B stop.

If a prompt mixes workflows ("start monitoring and send to Slack"), ask one clarifying question to split execution order.

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

## Workflow A — CV Mode (`-m verification` / `MODE=2d_cv`)

CV alerts are **deployment-driven, not request-driven** — there is no agent
call to "create" one.

1. Check if the sensor is already in VIOS via `vss-manage-video-io-storage`'s `GET /sensor/list` (idempotent — never blindly `POST /sensor/add`).
2. If missing, onboard via `vss-manage-video-io-storage` `POST /sensor/add` (see that skill's Section 6). The CV pipeline picks up the stream automatically once it is registered and online.
3. Confirm online: `curl -s "http://<VST_ENDPOINT>/vst/api/v1/sensor/<sensorId>/status" | jq .`
4. Wait for alerts to land in Elasticsearch (Behavior Analytics → `alert-bridge` VLM verification per `alert_type_config.json`). Query results with **Workflow C**.

If the user asks for a static-CV-pipeline alert on a VLM-only deployment, that is a mode mismatch — see the routing table above.

---

## Workflow B — VLM Real-time Monitoring (CV or VLM mode)

Generic start / stop intents through the VSS Agent for a named sensor
without a detection condition (if a condition is present, route to
Workflow D). `rtvi-vlm` runs in both modes.

```bash
# start: input_message = "Start real-time alert for sensor <id>"
# stop:  input_message = "Stop real-time alert for sensor <id>"
curl -s -X POST "$AGENT/generate" -H "Content-Type: application/json" \
  -d '{"input_message": "<start|stop> real-time alert for sensor <id>"}' | jq .
```

Under the hood the agent calls `rtvi_prompt_gen` then
`rtvi_vlm_alert action="start"`. Alert semantics: every chunk is
captioned; a chunk whose VLM response contains `yes` / `true`
(case-insensitive) publishes an incident to `mdx-vlm-incidents`.
Prompts must force a Yes/No answer. A static-CV-pipeline request on a
VLM-only deployment is a mode mismatch — see the routing table.

---

## Workflow D — Alert Subscriptions (VLM real-time mode only)

Create / list / delete persistent realtime alert rules on Alert Bridge.
Route here when the prompt has rule keywords (`rule`, `subscription`, a rule
ID) **or** when it pairs a specific sensor with a specific detection
condition (e.g. "Set up a realtime alert on warehouse-dock-1 for PPE
violations", "Watch sensor entrance-1 for tailgating", "Stop rule
496aebd1-…").

**Not here:** generic start/stop without a condition (→ Workflow B) or Slack
operations (→ Workflow E).

Load and follow `references/alert-subscriptions.md` as the authoritative
playbook for subscription CRUD. VLM real-time mode only; refuse with the
canonical refusal text on CV.

---

## Workflow E — Slack Notifications (VLM real-time mode only)

Use when the user **explicitly mentions Slack or the webhook relay** (start/stop webhook server, check status/health, send a test message, set Slack channel/token). The word `notify` alone is **not** enough.

> **`alert-notify` (port 9090) ≠ `vss-alert-bridge` (`/api/v1/realtime`).**
> Do NOT touch `vss-alert-bridge` for Slack ops.

Examples that route here: "Set up Slack notifications for alerts", "Check if
alert-notify is running", "Send a test alert notification to Slack", "Start
the alert webhook for Slack".

Examples that do NOT route here: "Notify me when someone enters the zone" (→
Workflow D/B), "Alert and notify on my phone" (ambiguous — ask).

Load and follow `references/alert-notify.md`. Code lives in
`scripts/alert-notify/`. VLM real-time mode only.

---

## Workflow C — Query / List Alerts (works on either mode)

Both CV- and VLM-generated alerts land in Elasticsearch and are
queryable via the agent's `video_analytics_mcp.get_incidents` tool. POST
natural-language requests to `$AGENT/generate` — "Show me recent alerts
for sensor X", "List confirmed alerts from the last hour", "Show
collision incidents from Camera_02 between `<ISO>` and `<ISO>`".

**Casual phrasings route here too.** Questions like "Any alerts so far
today?", "Any alerts today?", "What's been triggered?", or "Anything
detected lately?" are incident queries — issue a `POST /generate` (e.g.
`{"input_message": "List alerts from today"}`) and summarize the result.
**Never answer these from memory and never reply "no alerts" without
running the query.** A bare "alerts" question is *always* an incident
lookup (Workflow C), not a subscription-rule listing (Workflow D).

> **Workflow C has two incident endpoints — both valid:**
> - `POST /generate` — natural-language incident questions via the VSS Agent.
> - `GET http://<HOST>:9080/api/v1/realtime/incidents` — the Alert Bridge
>   **incidents** endpoint (note the `/incidents` suffix). Query params:
>   `sensor_id`, `start_time`, `end_time` (ISO-8601), `limit`, `offset`.
>   Response includes `total` (total matching incident count), `count`
>   (number returned), and `incidents[]`. **Prefer this for a count or a
>   structured/filtered list** — read `total` for "how many" questions.
>
> ```bash
> # count incidents for a sensor since a timestamp
> curl -sf "http://<HOST>:9080/api/v1/realtime/incidents?sensor_id=<UUID>&start_time=<ISO>" | jq '.total'
> ```
>
> **Do NOT list subscription rules for an incident query.** The **bare**
> `GET /api/v1/realtime` (no `/incidents`) lists *subscription rules*
> (Workflow D) and is wrong for "what happened" questions — never call,
> probe, or "orient" against it, and do not load the Workflow D playbook
> (`references/alert-subscriptions.md`) for an incident query. The
> `/api/v1/realtime/incidents` endpoint above is a **different** endpoint
> and **is** allowed for Workflow C.
>
> **Empty result is a valid answer.** If no incidents match (e.g. a
> freshly deployed system with no activity yet), report that **none were
> found / the count is 0** for the requested period and STOP — do not fall
> back to listing rules or hunting other endpoints.

For
richer / non-natural-language filtering (sensor-level, time-series,
counts) use the **`vss-query-analytics` skill** (VA-MCP on port 9901).

### Verdict interpretation (CV mode only)

Verified alerts carry an extended `info` block:

| `verdict` | Meaning |
|---|---|
| `confirmed` | VLM determined the alert is real |
| `rejected` | VLM determined it is a false positive |
| `unverified` | Verification could not complete (error) |

Check `verification_response_code` (200 = success) and `reasoning` for
the VLM's explanation. VLM-mode incidents are always "confirmed" at
source (the trigger itself is a Yes/No VLM answer), so there is no
separate verdict field.

---

## Customize CV Verifier Prompts (CV mode only)

CV-path verifier prompts live in
`deployments/developer-workflow/dev-profile-alerts/vlm-as-verifier/configs/alert_type_config.json`.
Each entry maps a CV `alert_type` (the `category` field emitted by
Behavior Analytics) to the VLM `system` / `user` / optional
`enrichment` prompts.

Key rules:

- `alert_type` must match the `category` emitted by Behavior Analytics.
- `output_category` is the display name in Elasticsearch / UI.
- `enrichment` triggers a second VLM call for a richer description;
  requires `alert_agent.enrichment.enabled: true`.
- Edits require an `alert-bridge` container restart to take effect.

VLM real-time prompts are not configured in a file — they are
per-request, shaped by `rtvi_prompt_gen` from the user's
natural-language detection description.

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
| Forward incidents to Slack webhook | Sub-workflow: `references/alert-notify.md`, code in `scripts/alert-notify/` |

---

## Gotchas

- **`alert-notify` (port 9090) ≠ `vss-alert-bridge`.** "Slack webhook" → Workflow E (`alert-notify`). Never route Slack intents to `vss-alert-bridge`'s `/api/v1/realtime`.
- **Workflow scope by mode:** Workflow A is CV-only. Workflows B and C work on either mode. Workflows D and E (subscriptions and Slack) are VLM real-time only — refuse with the canonical refusal text if attempted on CV.
- **Don't use `rtvi-vlm` container presence as a mode signal.** It runs in both modes. Use `vss-behavior-analytics-alerts` (CV-only) or the `MODE` env var instead.
- **A mode switch tears down the current deployment.** Any running VLM monitoring streams and any CV alert state not already in Elasticsearch will be lost.
- **Don't call the `rtvi-vlm` microservice directly** from this skill. Always go through `$AGENT/generate`. The agent handles sensor→RTSP lookup, stream registration, and teardown.
- **Sensor must already be in VIOS** for either mode. If the user hands you only an RTSP URL, use the `vss-manage-video-io-storage` skill first.
- **VLM alert trigger is a `"yes"` / `"true"` token match** on the VLM response (case-insensitive). `rtvi_prompt_gen` enforces the Yes/No pattern — don't hand-craft prompts that break it.
- **Stopping a VLM alert is one agent call** ("Stop real-time alert…"); the agent handles both the caption-stream and the stream-registration teardown.
- **Prompt changes to `alert_type_config.json` need an `alert-bridge` restart.** `alert_agent.enrichment.enabled: true` is required for the `enrichment` prompt to fire.

bump:1
