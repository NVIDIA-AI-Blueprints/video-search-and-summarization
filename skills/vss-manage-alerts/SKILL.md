---
name: vss-manage-alerts
description: Use for VSS alert workflows — real-time monitoring, Alert-Bridge subscriptions, Slack notifications, incident queries, camera onboarding. Not for non-alert analytics.
license: Apache-2.0
metadata:
  version: "3.2.0"
  author: "NVIDIA Video Search and Summarization Team <vss-team@nvidia.com>"
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

Runnable end-to-end scenarios live under `evals/` (each `*.json` manifest); inline `curl` blocks appear in each workflow below. Replay with `nv-base validate <this-skill-dir> --agent-eval`.

## Limitations

Requires the matching VSS profile/microservice deployed and reachable. NGC-hosted models/NIMs are subject to rate-limits, GPU-memory needs, and license terms; concurrency and storage limits depend on host hardware and the profile's compose file.

## Troubleshooting

- **Connection refused** → microservice not running: probe `/docs` or `/health`, redeploy via `vss-deploy-profile`.
- **HTTP 401/403 on NGC pulls** → missing/expired `NGC_CLI_API_KEY`: `docker login nvcr.io` and re-export the key.
- **OOM / model load failure** → insufficient GPU memory: use a smaller variant or `docker compose down` to free GPUs.

# VSS Alert Management

The alerts profile runs in one of two modes (chosen at `/vss-deploy-profile -p alerts -m {verification,real-time}`) — see **The Two Modes** table below. This skill routes by **deployed mode + user intent** (monitoring vs subscription CRUD vs Slack webhook), driving the **Alert Bridge REST API directly** (no VSS Agent `/generate`).

## When to Use

- Start/stop a real-time alert on a sensor ("Start real-time alert for boxes dropped on warehouse_sample")
- Create/list/stop realtime subscription rules on Alert Bridge
- Set up or manage Slack incident notifications
- List or query detected incidents / alerts; check verdicts (confirmed/rejected/not-confirmed/verification-failed)
- Add a new camera to the alerts pipeline; customize VLM-verifier prompts (CV mode)

---

## Deployment prerequisite

Requires the VSS **alerts** profile on `$HOST_IP` in either `verification` (CV) or `real-time` (VLM) mode.

```bash
# Either vss-rtvi-cv (CV mode) OR vss-rtvi-vlm (VLM mode) must be present.
curl -sf --max-time 5 "http://${HOST_IP}:8000/docs" >/dev/null \
  && docker ps --format '{{.Names}}' \
     | grep -qE '^(vss-rtvi-cv|vss-rtvi-vlm)$'
```

If the probe fails, ask which mode to deploy and hand off to `/vss-deploy-profile -p alerts -m <mode>` (decline → stop; pre-authorized autonomous deploy → run directly with `verification` by default). If it passes, detect the mode per Step 1.

---

## The Two Modes (Deploy-Time Choice)

| Mode | Deploy flag | Env (`.env`) | What runs | What is available |
|---|---|---|---|---|
| **CV (verification)** | `-m verification` | `MODE=2d_cv` | RT-CV (Grounding DINO) + Behavior Analytics + `alert-bridge` VLM verifier + **`rtvi-vlm`** | Static CV pipeline (**Workflow A**) + verification verdicts. Realtime rule CRUD (**D**) and Slack (**E**) are gated to real-time mode (skill refuses on CV). |
| **VLM (real-time)** | `-m real-time` | `MODE=2d_vlm` | `alert-bridge` + `rtvi-vlm` | Dynamic VLM real-time alerts (**Workflow D**), Slack (**E**), and incident queries (**C**). No static CV pipeline. |

**Switching modes** uses the `vss-deploy-profile` teardown + deploy flow with the other `-m` flag (VLM → CV adds the CV pipeline; CV → VLM tears it down). `rtvi-vlm` runs in both modes.

---

## Step 1 — Detect the Currently Deployed Mode

Before running any alert workflow, check which mode is live. Use **CV-only** containers as the signal — `vss-rtvi-vlm` is **not** a reliable mode signal because it runs in both modes.

```bash
# CV verification mode (vss-behavior-analytics + vss-rtvi-cv are CV-only)
docker ps --format '{{.Names}}' | grep -qx vss-behavior-analytics && echo "mode=CV"

# VLM real-time mode (no CV pipeline; vss-rtvi-vlm still runs)
docker ps --format '{{.Names}}' | grep -qx vss-behavior-analytics || \
  docker ps --format '{{.Names}}' | grep -qx vss-rtvi-vlm && echo "mode=VLM"
```

If `vss-behavior-analytics` is present → **CV mode** (which also has `vss-rtvi-vlm`).
If only `vss-rtvi-vlm` is present (and no CV pipeline) → **VLM mode**.
If neither matches, the alerts profile is not deployed — direct the user to the `vss-deploy-profile` skill.

Alternative signal (preferred when `docker ps` isn't accessible): check the profile's `generated.env`:

```bash
grep -E '^MODE=' deploy/docker/developer-profiles/dev-profile-alerts/generated.env
# MODE=2d_cv   → CV mode (full superset)
# MODE=2d_vlm  → VLM real-time mode (vss-rtvi-vlm only; no vss-rtvi-cv)
```

---

## Step 2 — Route by Deployed Mode

| Deployed mode | User asks about… | Action |
|---|---|---|
| **VLM real-time** | Slack webhook setup/status/test/stop | **Workflow E** — `references/alert-notify.md` |
| **VLM real-time** | rule CRUD, or start/stop a realtime alert on a sensor (with **or without** a detection condition — no condition → default prompt), or stop/delete a named alert (by `alert_type`/condition or rule ID) | **Workflow D** — `references/alert-subscriptions.md` (incl. two-step stop/confirm) |
| **CV verification** | subscription/rule CRUD or Slack/notification setup | Refuse — see canonical refusal text below |
| **CV or VLM** | incident lookup / *what happened* (recent alerts, time-range, casual "any alerts today?") | **Workflow C (Query)** — works on both; **always run the query, never answer from memory** |
| **CV** | static CV alert onboarding / verdict-prompt customization | **Workflow A (CV)** — onboard RTSP via `vss-manage-video-io-storage`; pipeline auto-picks it up |
| **VLM** | a CV / behavior-analytics / PPE-rule alert needing the static CV pipeline | **Redeployment required** — confirm first, then `vss-deploy-profile -m verification` |

**Always confirm before triggering a redeploy.** A mode switch stops all currently-running monitoring and restarts services.

### Intent precedence (first match wins)

1. **Workflow E (Slack)** — Slack-specific keywords (`slack`, `webhook` + `slack`, `bot token`, `slack channel`). `notify` alone is **not** sufficient.
2. **Workflow D (Alert rules)** — any realtime-alert request on a sensor: rule CRUD keywords (`rule`, `subscription`, rule ID), a sensor with a detection condition, a **bare start/stop with no condition** (→ default prompt), **or stopping/deleting a named alert by type/condition** ("stop the PPE alert", "delete the collision rule"). A named `alert_type`/condition = an existing **rule** → D's two-step stop protocol (`GET /api/v1/realtime` → yes/no confirm → delete).
3. **Workflow C (Query)** — incident lookup / *what happened* (`show/list incidents`, `recent alerts`, time-range queries, **and casual "any alerts…?" / "any alerts so far today?" / "what's been triggered?" phrasings**). Bare `alerts` (without `rule`/`subscription`/`active rules`) means **incidents** → Workflow C, never Workflow D.
4. **Workflow A (CV)** — CV deployment handling for anything not matched above.

> **`alerts` vs `alert rules` (C vs D) — pick exactly one, never both:**
> *what happened / has been triggered* (incidents) → **Workflow C**
> (`GET /api/v1/realtime/incidents`). *What
> rules/subscriptions are configured or active* → **Workflow D** (the
> **bare** `GET /api/v1/realtime`, no `/incidents`). Bare `alerts` =
> incidents (C); `alert rules` / `subscriptions` / `active rules` =
> inventory (D). Never answer from memory; run the one correct call —
> full endpoint detail in Workflow C below.

**All start/stop requests → Workflow D.** A start with a condition uses it verbatim as the `prompt`; a bare start with no condition uses D's **default prompt** (don't ask the user for one). Any stop — bare or type-named ("stop the **PPE** alert") — resolves the rule via `GET /api/v1/realtime`, then D's two-step confirm; never `POST /generate`.

If a prompt mixes workflows ("start monitoring and send to Slack"), ask one clarifying question to split execution order.

### CV-mode refusal text for D and E intents

When the deployed mode is CV verification and the user asks for an alert-subscription or Slack/notification intent, refuse with this message verbatim:

> "Alert subscriptions and Slack notifications are only supported in VLM real-time mode. Your current deployment is `<CV verification | not deployed>`. To use these features, redeploy with `/vss-deploy-profile -p alerts -m real-time` (note: switching tears down current CV monitoring)."

No auto-redeploy. The user decides whether to switch modes.

---

## Prereq for Either Mode: Sensor Must Be in VIOS

Both modes require the camera registered in VIOS first (via the `vss-manage-video-io-storage` skill):

- RTSP URL / IP camera → add it with `POST /sensor/add` (that skill's Section 6); record the `sensorId` / name.
- Named existing sensor → confirm it appears in `GET /sensor/list` before proceeding.
- **Register under exactly the requested name and verify it.** After `POST /sensor/add`, confirm that exact name appears in `GET /sensor/list` — VST silently assigns a generic default (e.g. `SENSOR`) when the payload's `name` key is missing or mis-keyed. A default-named entry means the name was not applied: delete/re-register with a correct `name` field before proceeding.

On **CV**, adding the RTSP is the *entire* onboarding step (pipeline auto-picks it up). On **VLM**, it is the prerequisite for creating a realtime alert rule (Workflow D).

---

## The Alert Bridge API (direct — no `/generate`)

Alert rule CRUD (Workflow D) and incident queries (Workflow C) call the **Alert Bridge REST API directly** — do **not** use the VSS Agent `POST /generate`, and do **not** call the `rtvi-vlm` microservice directly.

```bash
AB="http://${HOST_IP}:9080"     # Alert Bridge (fixed port 9080 on the alerts profile)
VST="http://${HOST_IP}:30888"   # VIOS/VST (sensor + RTSP resolution)
```

**Availability check:** `curl -sf --connect-timeout 5 "$AB/health"` (note: `/health`, not `/api/v1/health`).

**Sensor resolution:** rule create/list and incident filtering resolve a sensor **name → `sensorId` (UUID) + RTSP `url`** via `GET $VST/vst/api/v1/sensor/list` — see `references/alert-subscriptions.md`. Never fabricate a `sensor_id` or `live_stream_url`.

---

## Workflow A — CV Mode (`-m verification` / `MODE=2d_cv`)

CV alerts are **deployment-driven, not request-driven** — there is no agent
call to "create" one.

1. Check if the sensor is in VIOS via `vss-manage-video-io-storage`'s `GET /sensor/list` (idempotent — don't blindly `POST /sensor/add`).
2. If missing, onboard via that skill's `POST /sensor/add`. The CV pipeline auto-picks up the stream once registered and online.
3. Confirm online: `curl -s "http://<VST_ENDPOINT>/vst/api/v1/sensor/<sensorId>/status" | jq .`
4. Verified alerts land in Elasticsearch (`mdx-vlm-alerts-*`, Behavior Analytics → `alert-bridge` verification per `alert_type_config.json`). This store has **no REST query endpoint** — Workflow C's `/incidents` covers real-time incident-kind results only, not these CV behavior-alert verdicts.

A static-CV-pipeline alert on a VLM-only deployment is a mode mismatch — see the routing table above.

---

## Workflow D — Alert Rules (create / list / stop, VLM real-time mode only)

Create / list / delete persistent realtime alert rules on Alert Bridge (`POST` / `GET` / `DELETE $AB/api/v1/realtime`). Route here for **any** realtime-alert request on a sensor: rule keywords (`rule`, `subscription`, a rule ID), a sensor with a detection condition ("Set up a realtime alert on warehouse-dock-1 for PPE violations", "Watch entrance-1 for tailgating"), a **bare start with no condition** ("Start a real-time alert on warehouse_sample"), or "Stop rule 496aebd1-…".

- **With a condition** → send it verbatim as the `prompt`.
- **Without a condition** → use the skill's **default prompt** `"Describe any notable events or anomalies in this video stream."` and a generic `alert_type` (`general_monitoring`); don't ask the user for one.
- **Slack** operations → Workflow E instead.

Load and follow `references/alert-subscriptions.md` as the authoritative playbook for rule CRUD (incl. the two-step stop/confirm). VLM real-time mode only; refuse with the canonical refusal text on CV.

---

## Workflow E — Slack Notifications (VLM real-time mode only)

Use when the user **explicitly mentions Slack or the webhook relay** (start/stop webhook server, check status/health, send a test message, set Slack channel/token). The word `notify` alone is **not** enough.

> **`alert-notify` (port 9090) ≠ `vss-alert-bridge` (`/api/v1/realtime`).**
> Do NOT touch `vss-alert-bridge` for Slack ops.

Routes here: "Set up Slack notifications", "Check if alert-notify is running", "Send a test alert to Slack". Does **not** route here: "Notify me when someone enters the zone" (→ Workflow D), "Alert and notify on my phone" (ambiguous — ask).

Load and follow `references/alert-notify.md`. Code lives in `scripts/alert-notify/`. VLM real-time mode only.

---

## Workflow C — Query Incidents (real-time incident store)

Query past incidents **directly** from Alert Bridge — no `/generate`:

```bash
# recent incidents (optionally filter by sensor / category / time / limit)
curl -sf "$AB/api/v1/realtime/incidents?limit=20" | jq .
# scope to one sensor: resolve name → sensorId (UUID) via VIOS, then:
curl -sf "$AB/api/v1/realtime/incidents?sensor_id=<UUID>&start_time=<ISO>&end_time=<ISO>" | jq .
```

Response is an `IncidentListResponse`: `{ "status", "incidents": [...], "count", "total", "timestamp" }`. Summarize each incident's timestamp, sensor (reverse-resolve `sensor_id` → name), and category. **Run the query — never answer from memory.** An **empty `incidents` list is a valid answer**: report "none found / count 0" and STOP; do not fall back to listing rules.

**Casual phrasings route here too** — "Any alerts so far today?", "What's been triggered?", "Anything detected lately?" are all incident queries. A bare "alerts" question is *always* an incident lookup (C), never a rule listing (D).

> **Do NOT list subscription rules for an incident query.** The **bare** `GET /api/v1/realtime` (no `/incidents`) lists *rules* (Workflow D) and is wrong for "what happened".

**Scope — real-time incident-kind results only.** CV / Behavior-Analytics verified alerts (PPE, ladder, proximity, restricted-area) are stored in a separate `mdx-vlm-alerts-*` index with **no REST query endpoint**, so this call does **not** surface them — in a CV deployment it typically returns empty for those. For time-range / occupancy / PPE metrics use the **`vss-query-analytics` skill** (VA-MCP :9901).

### Verdict interpretation (CV mode)

CV-verified incidents carry a `verdict` (`confirmed` / `rejected` / `not-confirmed` / `verification-failed`, or empty when a pluggable parser is used) plus `verificationResponseCode` and `reasoning` in their `info` block; VLM real-time incidents have no separate verdict (the trigger is itself a Yes/No answer). See `references/cv-verifier-prompts.md` for the verdict table and prompt-customization rules.

---

## Cross-Skill Links

| Task | Skill |
|---|---|
| Deploy, redeploy, or switch alert mode | **`vss-deploy-profile`** — `-p alerts -m {verification,real-time}` |
| Add an RTSP/IP camera, list sensors, snapshots, clips | **`vss-manage-video-io-storage`** (Section 6 for Add Sensor) |
| Time-range incident / occupancy / PPE metrics from Elasticsearch | **`vss-query-analytics`** (VA-MCP :9901) |
| Detailed incident report from an alert | **`vss-generate-video-report`** |
| Subscriptions / Slack sub-workflows | `references/alert-subscriptions.md`, `references/alert-notify.md` (code in `scripts/alert-notify/`) |

---

## Gotchas

- **`alert-notify` (port 9090) ≠ `vss-alert-bridge`.** Slack ops → Workflow E (`alert-notify`); never route Slack to `vss-alert-bridge`'s `/api/v1/realtime`.
- **Workflow scope by mode:** A is CV-only; **C queries the real-time incident store** (`/api/v1/realtime/incidents`; CV behavior-alert verdicts live in `mdx-vlm-alerts-*` with no query endpoint); D and E are VLM real-time only (refuse on CV with the canonical text).
- **Don't use `vss-rtvi-vlm` as a mode signal** — it runs in both modes. Use `vss-behavior-analytics` (CV-only) or the `MODE` env var.
- **A mode switch tears down the current deployment** — running VLM streams and un-persisted CV alert state are lost.
- **Alert ops call Alert Bridge (`:9080`) directly** — the skill does not use the VSS Agent `/generate`, and never calls `rtvi-vlm` directly. The VLM trigger is a `"yes"`/`"true"` token match (case-insensitive); prompts must force a Yes/No answer.
- **Sensor must already be in VIOS** for either mode (use `vss-manage-video-io-storage` for RTSP-only inputs).
- **Report only values an API actually returned** — never invent rule IDs, sensor IDs, incident counts, or timestamps, and never claim an action succeeded without its API response (this includes replies that decline or hand off a request).
- **End your turn by answering the CURRENT request** — the final reply must address what the user just asked (even when handing off out-of-scope work); never close with the status or summary of a different or earlier task.

bump:1
