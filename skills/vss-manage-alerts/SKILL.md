---
name: vss-manage-alerts
description: Use this skill when operating VSS alert workflows — real-time monitoring, Alert-Bridge subscriptions, verification verdicts, on-demand verification, always-on operation, Slack notifications, incident queries, or camera onboarding. Not for non-alert analytics.
license: Apache-2.0
metadata:
  version: "3.3.3"
  author: "NVIDIA Video Search and Summarization Team"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint operational"
---
## Purpose

Operate the VSS alert pipeline (mode detection, Alert-Bridge subscriptions, verification verdicts, on-demand verification, always-on operation, Slack notifications, queries, camera onboarding, verifier-prompt customization).

## Prerequisites

- Active VSS **alerts** profile reachable either on Docker (`$HOST_IP:9080` Alert
  Bridge) or through the public Ingress (`VSS_PUBLIC_URL` with `/alert-bridge`).
- Follow
  [`../vss-build-vision-agent/references/deployment_resolution.md`](../vss-build-vision-agent/references/deployment_resolution.md)
  for the shared `VSS_PUBLIC_URL` contract.
- `curl` and `jq` on the agent host. Docker Compose mode detection may use
  `docker` / `generated.env`; Kubernetes must not.

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
- List or query detected incidents / alerts (Workflow C)
- Inspect CV verification results and verdicts (confirmed/rejected/not-confirmed/verification-failed), explain how verification works, customize VLM-verifier prompts (CV mode — Workflow B)
- Run a one-shot on-demand verification of a specific video/image URL (CV mode — Workflow F)
- Check whether always-on alerting is active, query its incidents, troubleshoot missing always-on alerts (VLM real-time — Workflow G; operate only, no config authoring)
- Add a new camera to the alerts pipeline (Workflow A)

---

## Deployment prerequisite

Requires the VSS **alerts** profile in either `verification` (CV) or `real-time`
(VLM) mode. Resolve endpoints once before probing. See
[`../vss-build-vision-agent/references/deployment_resolution.md`](../vss-build-vision-agent/references/deployment_resolution.md).

```bash
# Prefer VSS_PUBLIC_URL; accept legacy VSS_ENDPOINT as the same public origin.
if [ -z "${VSS_PUBLIC_URL:-}" ] && [ -n "${VSS_ENDPOINT:-}" ]; then
  VSS_PUBLIC_URL="${VSS_ENDPOINT}"
fi

if [ -n "${VSS_PUBLIC_URL:-}" ]; then
  DEPLOYMENT_KIND="kubernetes"
  VSS_PUBLIC_URL="${VSS_PUBLIC_URL%/}"
  # Force public prefixes — ignore leftover Docker AB / VST / VA_MCP host ports.
  AB="${VSS_PUBLIC_URL}/alert-bridge"
  VST="${VSS_PUBLIC_URL}"                    # paths append /vst/api/v1/...
  VST_API_BASE="${VST}/vst/api/v1"
  VA_MCP_URL="${VSS_PUBLIC_URL}/va-mcp"
else
  DEPLOYMENT_KIND="docker"
  : "${HOST_IP:?Set HOST_IP for Docker Compose or VSS_PUBLIC_URL for Kubernetes}"
  AB="http://${HOST_IP}:9080"
  VST="http://${HOST_IP}:30888"
  VST_API_BASE="${VST}/vst/api/v1"
  VA_MCP_URL="http://${HOST_IP}:9901"
fi
```

On Kubernetes, do not use `kubectl port-forward`, Service DNS, NodePorts,
`docker exec`, `docker inspect`, or `docker ps`. Probe Alert Bridge with
`curl -sf --max-time 5 "$AB/health"` (`/health`, not `/api/v1/health`).

```bash
# Reachability — either mode. Alert Bridge must answer before operate work.
curl -sf --max-time 5 "$AB/health" >/dev/null
```

Optional Docker-only peer check (skipped on Kubernetes — RT-VLM is not on
alerts Ingress):

```bash
if [ "${DEPLOYMENT_KIND:-docker}" != "kubernetes" ]; then
  curl -sf --max-time 5 "http://${HOST_IP}:8000/docs" >/dev/null \
    && docker ps --format '{{.Names}}' \
       | grep -qE '^(vss-rtvi-cv|vss-rtvi-vlm)$'
fi
```

If the Alert Bridge probe fails, ask which mode to deploy and hand off to
`/vss-deploy-profile -p alerts -m <mode>` (decline → stop; pre-authorized
autonomous deploy → run directly with `verification` by default). If it
passes, detect the mode per Step 1.

---

## The Two Modes (Deploy-Time Choice)

| Mode | Deploy flag | Env (`.env`) | What runs | What is available |
|---|---|---|---|---|
| **CV (verification)** | `-m verification` | `MODE=2d_cv` | RT-CV (Grounding DINO) + Behavior Analytics + `alert-bridge` VLM verifier + **`rtvi-vlm`** | Static CV pipeline (**Workflow A**) + verification results & verdicts (**Workflow B**) + on-demand verification (**Workflow F**). VIOS webhooks register streams with RT-CV (`notification_config_2d_cv.json`); Agent `rtvi_cv_base_url` is omitted. Realtime rule CRUD (**D**) and Slack (**E**) are gated to real-time mode (skill refuses on CV). |
| **VLM (real-time)** | `-m real-time` | `MODE=2d_vlm` | `alert-bridge` + `rtvi-vlm` | Dynamic VLM real-time alerts (**Workflow D**), Slack (**E**), incident queries (**C**), and always-on operation (**Workflow G** — `ALERT_AGENT_ALWAYS_ON=true` / `alert_agent.always_on: true` when deployed with `-m real-time`). No static CV pipeline; Agent `rtvi_cv_base_url` is omitted so ingest skips RT-CV. |

**Switching modes** uses the `vss-deploy-profile` teardown + deploy flow with the other `-m` flag. The deploy script selects `config.yml` for CV or `config-real-time.yml` for real-time; VIOS webhooks follow `notification_config_${MODE}.json`. `rtvi-vlm` runs in both modes.

**`RTVI_VLM_KAFKA_ENABLED` is mode-specific.** `overrides.env` ships `RTVI_VLM_KAFKA_ENABLED=false` for verification (`2d_cv`), where nothing consumes RT-VLM's Kafka output and leaving it on makes RT-VLM publish duplicate incidents that Logstash indexes under `mdx-vlm-incidents-1970-01-01`. Real-time (`2d_vlm`) alerts depend on RT-VLM publishing to Kafka, so the line must be commented out in that mode — `dev-profile.sh` does this automatically for `-m real-time`. If a real-time deployment produces no alerts, check that this override is not still active in `generated.env`.

---

## Step 1 — Detect the Currently Deployed Mode

Before running any alert workflow, check which mode is live.

**Kubernetes** — do not use `docker ps`. Prefer an explicit mode hint, then ask:

```bash
# Operator / deploy hint (preferred on Kubernetes).
# ALERTS_MODE=real-time|verification  or  MODE=2d_vlm|2d_cv
if [ -n "${ALERTS_MODE:-}" ]; then
  case "${ALERTS_MODE}" in
    real-time|realtime|vlm|2d_vlm) echo "mode=VLM" ;;
    verification|cv|2d_cv) echo "mode=CV" ;;
  esac
elif [ "${MODE:-}" = "2d_vlm" ]; then echo "mode=VLM"
elif [ "${MODE:-}" = "2d_cv" ]; then echo "mode=CV"
elif [ "${DEPLOYMENT_KIND:-docker}" = "kubernetes" ]; then
  # Docs walkthrough is real-time; ask rather than guessing from private pods.
  echo "Ask the user: is this alerts deployment real-time (VLM) or verification (CV)?"
fi
```

**Docker only** — use CV-only containers as the signal (`vss-rtvi-vlm` runs in
both modes, so it is **not** a reliable mode signal alone):

```bash
if [ "${DEPLOYMENT_KIND:-docker}" != "kubernetes" ]; then
  # CV verification mode (vss-behavior-analytics + vss-rtvi-cv are CV-only)
  if docker ps --format '{{.Names}}' | grep -qx vss-behavior-analytics; then
    echo "mode=CV"
  # VLM real-time mode has no CV pipeline; vss-rtvi-vlm runs in both modes.
  elif docker ps --format '{{.Names}}' | grep -qx vss-rtvi-vlm; then
    echo "mode=VLM"
  fi
fi
```

If `vss-behavior-analytics` is present → **CV mode** (which also has `vss-rtvi-vlm`).
If only `vss-rtvi-vlm` is present (and no CV pipeline) → **VLM mode**.
If neither matches on Docker, the alerts profile is not deployed — direct the user to the `vss-deploy-profile` skill.

Alternative Docker signal (preferred when `docker ps` isn't accessible): check the deployed `generated.env`, falling back to `overrides.env` before a deployment has generated one:

```bash
if [ "${DEPLOYMENT_KIND:-docker}" != "kubernetes" ]; then
  ENV_FILE=deploy/docker/developer-profiles/dev-profile-alerts/generated.env
  [ -f "$ENV_FILE" ] || ENV_FILE=deploy/docker/developer-profiles/dev-profile-alerts/overrides.env
  grep -E '^MODE=' "$ENV_FILE"
  # MODE=2d_cv   → CV mode (full superset)
  # MODE=2d_vlm  → VLM real-time mode (vss-rtvi-vlm only; no vss-rtvi-cv)
fi
```

---

## Step 2 — Route by Deployed Mode

| Deployed mode | User asks about… | Action |
|---|---|---|
| **VLM real-time** | Slack webhook setup/status/test/stop | **Workflow E** — `references/alert-notify.md` |
| **VLM real-time** | always-on status ("is always-on active?"), always-on incidents, "why aren't always-on alerts appearing?" | **Workflow G** — `references/always-on.md` (operate only; config authoring is out of scope) |
| **CV verification** | always-on operation | Refuse — always-on rides the realtime rule engine; canonical refusal text below |
| **VLM real-time** | rule CRUD, or start/stop a realtime alert on a sensor (with **or without** a detection condition — no condition → default prompt), or stop/delete a named alert (by `alert_type`/condition or rule ID) | **Workflow D** — `references/alert-subscriptions.md` (incl. two-step stop/confirm) |
| **CV verification** | subscription/rule CRUD or Slack/notification setup | Refuse — see canonical refusal text below |
| **CV or VLM** | incident lookup / *what happened* (recent alerts, time-range, casual "any alerts today?") | **Workflow C (Query)** — works on both; **always run the query, never answer from memory** |
| **CV** | verification results / verdicts ("was it confirmed?", "show verification results"), *how does verification work*, verifier-prompt customization | **Workflow B (Verification)** — `references/verification.md`. **But** a verdict/result **follow-up to an on-demand verification just run** ("was it confirmed?", "what was the result?") → stay in **Workflow F**: poll `/realtime/incidents` by the `correlationId` (that result is incident-kind, not in `mdx-vlm-alerts-*`) |
| **CV** | one-shot "verify **this** clip/image" with a media URL, or the literal "on-demand" | **Workflow F (On-demand)** — `references/on-demand-verification.md` |
| **CV** | static CV alert onboarding | **Workflow A (CV)** — onboard RTSP via `vss-manage-video-io-storage`; VIOS webhook registers it with RT-CV |
| **VLM** | verification results / verdict inspection, verifier-prompt config, or on-demand verification (CV-only capabilities) | *Explain-only* asks → answer from **Workflow B/F** background, no calls needed. *Execution* asks → VLM-mode refusal text below (redeploy hint `-m verification`) |
| **VLM** | a CV / behavior-analytics / PPE-rule alert needing the static CV pipeline | **Redeployment required** — confirm first, then `vss-deploy-profile -m verification` |
| **any** | video summarization, highlight reels, reports, non-alert analytics | **Out of scope** — hand off to `vss-generate-video-report` / `vss-query-analytics` (Cross-Skill Links); do **not** answer it via incidents or rules, even when incidents are empty |

**Always confirm before triggering a redeploy.** A mode switch stops all currently-running monitoring and restarts services.

### Intent precedence (first match wins)

1. **Workflow E (Slack)** — Slack-specific keywords (`slack`, `webhook` + `slack`, `bot token`, `slack channel`). `notify` alone is **not** sufficient.
2. **Workflow F (On-demand)** — a one-shot "verify / check / analyze **this**" pointing at a **specific media artifact** (video/image URL, clip, file), or the literal `on-demand`. Guard: *continuous monitoring of a sensor/stream* is **never** F — that's D ("watch camera X for PPE" → D; "verify this clip URL for PPE" → F).
3. **Workflow G (Always-on)** — the literal `always-on` (status, incidents, troubleshooting phrasings). Operate-not-author: status checks and queries only; never author or edit always-on rule config. A request to *create* an ordinary realtime rule is **not** G — that's D.
4. **Workflow B (Verification results)** — verification/verdict keywords (`verdict`, `confirmed?`/`rejected?`, `verification results`, "how does verification work", verifier prompt/config) **without** a media artifact to verify and without a start/stop/rule intent. Reads the `mdx-vlm-alerts-*` store (interim ES probe) and the verifier config — never the rules list. Bare "any alerts today?" is **not** B — it stays Workflow C.
5. **Workflow D (Alert rules)** — any realtime-alert request on a sensor: rule CRUD keywords (`rule`, `subscription`, rule ID), a sensor with a detection condition, a **bare start/stop with no condition** (→ default prompt), **or stopping/deleting a named alert by type/condition** ("stop the PPE alert", "delete the collision rule"). A named `alert_type`/condition = an existing **rule** → D's two-step stop protocol (`GET /api/v1/realtime` → yes/no confirm → delete).
6. **Workflow C (Query)** — incident lookup / *what happened* (`show/list incidents`, `recent alerts`, time-range queries, **and casual "any alerts…?" / "any alerts so far today?" / "what's been triggered?" phrasings**). Bare `alerts` (without `rule`/`subscription`/`active rules`) means **incidents** → Workflow C, never Workflow D.
7. **Workflow A (CV)** — CV deployment handling for anything not matched above.

> **`alerts` vs `alert rules` (C vs D) — pick exactly one, never both:**
> *what happened / has been triggered* (incidents) → **Workflow C**
> (`GET /api/v1/realtime/incidents`). *What
> rules/subscriptions are configured or active* → **Workflow D** (the
> **bare** `GET /api/v1/realtime`, no `/incidents`). Bare `alerts` =
> incidents (C); `alert rules` / `subscriptions` / `active rules` =
> inventory (D). Never answer from memory; run the one correct call —
> full endpoint detail in Workflow C below.

> **`verdicts` vs `alerts` (B vs C) — pick exactly one:** *was it
> confirmed / show verdicts / verification results* → **Workflow B**
> (interim ES probe on `mdx-vlm-alerts-*`). *What happened / any alerts
> today* → **Workflow C** (`/incidents`), even on a CV deployment.

**All start/stop requests → Workflow D.** A start with a condition uses it verbatim as the `prompt`; a bare start with no condition uses D's **default prompt** (don't ask the user for one). Any stop — bare or type-named ("stop the **PPE** alert") — resolves the rule via `GET /api/v1/realtime`, then D's two-step confirm; never `POST /generate`.

If a prompt mixes workflows ("start monitoring and send to Slack"), ask one clarifying question to split execution order.

### CV-mode refusal text for D, E, and G intents

When the deployed mode is CV verification and the user asks for an alert-subscription, always-on, or Slack/notification intent, refuse with this message verbatim:

> "Alert subscriptions, always-on operation, and Slack notifications are only supported in VLM real-time mode. Your current deployment is `<CV verification | not deployed>`. To use these features, redeploy with `/vss-deploy-profile -p alerts -m real-time` (note: switching tears down current CV monitoring)."

No auto-redeploy. The user decides whether to switch modes.

### VLM-mode text for verification-execution intents (Workflows B and F)

Verification verdicts and on-demand verification exist only on a CV (verification) deployment. On VLM real-time, *explain-only* asks ("how does verification work?") are always answerable from Workflow B/F background — no calls, no refusal. For *execution* asks (inspect verdicts, customize verifier prompts, verify a clip on demand), reply with this message verbatim:

> "Verification workflows (verdict inspection, verifier-prompt configuration, on-demand verification) require the verification (CV) deployment. Your current deployment is VLM real-time. To use them, redeploy with `/vss-deploy-profile -p alerts -m verification` (note: switching stops currently-running realtime monitoring)."

No auto-redeploy here either.

---

## Prereq for Either Mode: Sensor Must Be in VIOS

Both modes require the camera registered in VIOS first (via the `vss-manage-video-io-storage` skill):

- RTSP URL / IP camera → add it with `POST /sensor/add` (that skill's Section 6); record the `sensorId` / name.
- Named existing sensor → confirm it appears in `GET /sensor/list` before proceeding.
- **The `/sensor/add` payload MUST carry BOTH keys** — omitting `name` is the classic mistake (VST then silently names the sensor `SENSOR`):
  ```json
  { "sensorUrl": "<url exactly as NVStreamer's streams API returned it>", "name": "<exact requested name>" }
  ```
  After the POST, confirm that exact name appears in `GET /sensor/list`; a default-named entry (`SENSOR`) means the name was not applied — delete and re-register with the `name` key.
- **Never hand-construct the RTSP URL.** For an NVStreamer-served stream, query NVStreamer for the served URL (`GET :31000/vst/api/v1/sensor/<name>/streams` → `url`) and register it **verbatim** — including its container-internal host/port (VST shares that docker network; a guessed `<host-ip>:<port>` or `localhost` URL is typically unreachable from the VST container and the stream never activates). After registering, confirm the sensor exposes a non-empty `rtsp://` stream URL (aggregate `GET /vst/api/v1/sensor/streams`) before proceeding — an empty `url` means the source is unreachable and the registration must be redone.

On **CV**, adding the RTSP is the *entire* onboarding step (VIOS `camera_streaming` webhook registers the stream with RT-CV). On **VLM**, it is the prerequisite for creating a realtime alert rule (Workflow D).

---

## The Alert Bridge API (direct — no `/generate`)

Alert rule CRUD (Workflow D) and incident queries (Workflow C) call the **Alert Bridge REST API directly** — do **not** use the VSS Agent `POST /generate`, and do **not** call the `rtvi-vlm` microservice directly.

Resolve `$AB` / `$VST` once in *Deployment prerequisite* (Kubernetes forces
`${VSS_PUBLIC_URL}/alert-bridge` and `${VSS_PUBLIC_URL}`; Docker keeps
`:9080` / `:30888`). Do not reintroduce host-port overrides when
`VSS_PUBLIC_URL` is set.

**Availability check:** `curl -sf --connect-timeout 5 "$AB/health"` (note: `/health`, not `/api/v1/health`).

**Sensor resolution:** rule create/list and incident filtering resolve a sensor **name → `sensorId` (UUID) + RTSP `url`** via `GET $VST/vst/api/v1/sensor/list` — see `references/alert-subscriptions.md`. Never fabricate a `sensor_id` or `live_stream_url`.

---

## Workflow A — CV Mode (`-m verification` / `MODE=2d_cv`)

CV alerts are **deployment-driven, not request-driven** — there is no agent
call to "create" one.

1. Check if the sensor is in VIOS via `vss-manage-video-io-storage`'s `GET /sensor/list` (idempotent — don't blindly `POST /sensor/add`).
2. If missing, onboard via that skill's `POST /sensor/add`. Once the sensor is online, VIOS posts `camera_streaming` to RT-CV via the verification webhook config.
3. Confirm online: `curl -s "$VST_API_BASE/sensor/<sensorId>/status" | jq .`
4. Verified alerts land in Elasticsearch (`mdx-vlm-alerts-*`, Behavior Analytics → `alert-bridge` verification per `alert_type_config.json`). This store has **no REST query endpoint** — Workflow C's `/incidents` covers real-time incident-kind results only; inspect these CV behavior-alert verdicts via **Workflow B**'s interim ES probe.

A static-CV-pipeline alert on a VLM-only deployment is a mode mismatch — see the routing table above.

---

## Workflow B — Verification Results & Verdicts (CV mode)

How a CV alert becomes a verdict: RT-CV (Grounding DINO) detections → Behavior Analytics emits a candidate alert → Alert Bridge invokes the VLM verifier (prompts per `alert_type`) → the verified document lands in Elasticsearch **`mdx-vlm-alerts-*`** with `verdict` + `verificationResponseCode` in its `info` block.

1. **Explain / interpret** — verdict values are `confirmed` / `rejected` / `not-confirmed` / `verification-failed` / `""` (empty — the default `use_verdict: false` freestyle deploy or a pluggable parser; a valid state, **not** a failure); full table in `references/verification.md`.
2. **Inspect results — interim ES probe (Docker only).** This store has **no REST query endpoint yet** (a dedicated Alert Bridge endpoint is planned). On **Docker**, query Elasticsearch directly:
   ```bash
   if [ "${DEPLOYMENT_KIND:-docker}" != "kubernetes" ]; then
     curl -sf "http://${HOST_IP}:9200/mdx-vlm-alerts-*/_search?size=10" | jq '.hits.hits[]._source'
   fi
   ```
   On **Kubernetes**, Elasticsearch `:9200` is not on stock alerts Ingress — do
   not port-forward. Prefer Workflow C (`GET $AB/api/v1/realtime/incidents`)
   for real-time incident-kind results, or redeploy/ask for a Docker path when
   CV verdict inspection via `mdx-vlm-alerts-*` is required.
   An **empty hit list is a valid answer** — report "no verification results yet" and stop. Never substitute another source for a verdict question: not the rules list, not `/incidents`, and **not the `mdx-vlm-incidents-*` ES index** (that is Workflow C's incident store — its documents carry no verification verdicts; presenting them as "verdicts recorded" is a wrong answer even when `mdx-vlm-alerts-*` is empty). **Exception — on-demand follow-up:** if the ask follows an on-demand verification you just ran (Workflow F) and `mdx-vlm-alerts-*` is empty, do **not** dead-end here — that result is incident-kind. Continue in **Workflow F**: poll `GET $AB/api/v1/realtime/incidents` for the request's `correlationId` and report its `reasoning` / `vlm_response` (a default freestyle deploy carries no `verdict` field).
3. **Verifier-prompt config** — REST CRUD on `$AB/api/v1/verification/config[/{alert_type}]` (`GET` list / `GET` one / `POST` / `PUT` / `DELETE`), or the config-file + restart path — rules and payload shapes in `references/verification.md`.

Load `references/verification.md` for the full verdict table, probe recipes, and prompt-customization rules. CV mode only for execution; explain-only asks are answerable in any mode.

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
> Do NOT touch `vss-alert-bridge` for Slack ops — Slack is never configured through Alert Bridge realtime rule APIs.

One relay, **two backends**: the `alert-notify` webhook server fans incidents out to **Slack** and/or the **OpenClaw Dashboard**, selected by `NOTIFY_BACKENDS` (default **`dashboard`** — a Slack setup MUST set `NOTIFY_BACKENDS=slack`, or `slack,dashboard` for both). The four skill-level ops all hit `:9090`: **status** (`GET /webhook/alert-notify/status`), **start** (creds gate below), **test** (POST a sample incident to `/webhook/alert-notify`), **stop**.

**Credentials gate before any start:** Slack needs `SLACK_BOT_TOKEN` + `SLACK_CHANNEL_ID` (plus `VST_ENDPOINT`); the server **exits at startup** on a failed Slack auth or missing `VST_ENDPOINT` — never start it with placeholder values. Ask for real credentials and stop until provided.

Routes here: "Set up Slack notifications", "Check if alert-notify is running", "Send a test alert to Slack". Does **not** route here: "Notify me when someone enters the zone" (→ Workflow D), "Alert and notify on my phone" (ambiguous — ask).

Load and follow `references/alert-notify.md`. Code lives in `scripts/alert-notify/`. VLM real-time mode only.

---

## Workflow F — On-Demand Verification (CV mode)

One-shot verification of a **specific media artifact** the user points at — never a continuous rule. Endpoint is **`POST $AB/api/v1/verification/ondemand`** (there is **no** `/verification/verify` route):

1. **Resolve the category first** — `GET $AB/api/v1/verification/config` and pick the existing `alert_type` matching the user's ask (e.g. a ladder/PPE config for a ladder-safety question). Never invent a category: an unknown one is a deterministic `400 {"error":"unknown_category"}`.
2. **Submit**:
   ```bash
   curl -sf -X POST "$AB/api/v1/verification/ondemand" -H 'Content-Type: application/json' -d '{
     "category": "<alert_type from config>",
     "info": { "media_urls": ["<video/image URL>"], "media_type": "video" }
   }'
   ```
   Response is **HTTP 202** `{"status":"accepted","correlationId":"…","message":…,"timestamp":…}` — report the actual `correlationId` (server default `ondemand-<uuid>`; your own `id` field is used if you send one). 400 = `unknown_category` / `invalid_request` (`info.media_urls` list + `media_type` ∈ `video`|`image` are required).
3. **Result lands async — in the realtime incident store by default.** A minimal `{category, info}` submission is **incident-kind**: its result surfaces via Workflow C's **`GET $AB/api/v1/realtime/incidents`** — match the `correlationId` (= the incident `id`; the submission's `sensorId` defaults to `ondemand`). Only a submission that explicitly carries `notification_type: "alert"` lands in `mdx-vlm-alerts-*` (then use Workflow B's interim ES probe). Allow ≥2 min; the VLM must fetch the URL itself — an unreachable URL still lands a document via the **error path** (`verdict: "verification-failed"`, non-200 `verificationResponseCode`). **When describing the result fields**, report: `verificationResponseCode` (200 = VLM success, 4xx/5xx = error path) and the raw VLM output (`reasoning` / `vlm_response`) — and **always note that `verdict` may be absent or empty** on the default deploy (`use_verdict: false` freestyle mode); never present `verdict` as a guaranteed field.

Load `references/on-demand-verification.md` for the full contract, media constraints, and result-validation checklist. CV mode for execution; explain-only asks answerable anywhere. A 202 means **accepted**, not verified — never report a verdict at submit time.

---

## Workflow G — Always-On Operation (VLM real-time mode only)

Always-on alerting starts pre-configured rules automatically when SDR announces a camera (`camera_streaming` → one realtime rule per `always_on_rules` YAML entry; `camera_remove` tears them down) via `POST $AB/api/v1/realtime/always-on`. **Operate, don't author** — this workflow never creates or edits always-on rule config; it checks status, queries results, and troubleshoots.

1. **Status** — the feature is gated by `ALERT_AGENT_ALWAYS_ON` (substituted into `alert_agent.always_on` in the Alert Bridge config). `dev-profile.sh` sets it **true** for `-m real-time` (`MODE=2d_vlm`) and **false** for `-m verification` (`MODE=2d_cv`). VIOS webhooks follow `VST_NOTIFICATION_CONFIG_PATH=…/notification_config_${MODE}.json` in overrides (`2d_vlm` → Alert Bridge always-on; `2d_cv` → RT-CV stream add/remove). There is **no** `/always-on/health` endpoint — do not invent one. Signals: the config gate itself, or a `503 {"reason":"ALWAYS_ON_DISABLED"}` from the endpoint. Zero-side-effect probe options in `references/always-on.md`.
2. **Query incidents** — always-on rules are ordinary realtime rules once started; their incidents surface through **Workflow C** (`GET $AB/api/v1/realtime/incidents`). The rules live in an **in-memory sidecar** (not the ES-backed rules index), so they may not appear in Workflow D's rules list — that is expected, not a bug.
3. **Troubleshoot "no always-on alerts"** — walk the ladder in `references/always-on.md`: feature gate → rules YAML resolves/validates at boot → SDR events actually reaching Alert Bridge → stream registered on `rtvi-vlm` → incidents query.

Load `references/always-on.md` for the event contract, reason-code table, YAML resolution chain, and the troubleshooting ladder. VLM real-time mode only; refuse on CV with the canonical text. Config authoring (editing `always_on_rules`) is **out of scope** in this pass — say so when asked.

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

**Casual phrasings route here too** — "Any alerts so far today?", "What's been triggered?", "Anything detected lately?" are all incident queries. A bare "alerts" question is *always* an incident lookup (C), never a rule listing (D). Incidents produced by **always-on** rules (Workflow G) appear here like any other realtime incident, and so do **on-demand verification results** (incident-kind, `sensorId: "ondemand"` — see Workflow F).

> **Do NOT list subscription rules for an incident query.** The **bare** `GET /api/v1/realtime` (no `/incidents`) lists *rules* (Workflow D) and is wrong for "what happened".

**Scope — real-time incident-kind results only.** CV / Behavior-Analytics verified alerts (PPE, ladder, proximity, restricted-area) are stored in a separate `mdx-vlm-alerts-*` index with **no REST query endpoint**, so this call does **not** surface them — in a CV deployment it typically returns empty for those. For time-range / occupancy / PPE metrics use the **`vss-query-analytics` skill** (VA-MCP :9901).

### Verdict interpretation (CV mode)

CV-verified alerts carry `verdict` + `verificationResponseCode` + `reasoning` in their `info` block; VLM real-time incidents have no separate verdict (the trigger is itself a Yes/No answer). Verdict table, result inspection, and verifier-prompt rules → **Workflow B** / `references/verification.md`.

---

## Cross-Skill Links

| Task | Skill |
|---|---|
| Deploy, redeploy, or switch alert mode | **`vss-deploy-profile`** — `-p alerts -m {verification,real-time}` |
| Add an RTSP/IP camera, list sensors, snapshots, clips | **`vss-manage-video-io-storage`** (Section 6 for Add Sensor) |
| Time-range incident / occupancy / PPE metrics from Elasticsearch | **`vss-query-analytics`** (VA-MCP :9901) |
| Detailed incident report from an alert | **`vss-generate-video-report`** |
| Subscriptions / Slack sub-workflows | `references/alert-subscriptions.md`, `references/alert-notify.md` (code in `scripts/alert-notify/`) |
| Alert Bridge deployment / integration contracts | `references/deploy-alerts.md`, `references/integrate-alerts.md` |

---

## Gotchas

- **`alert-notify` (port 9090) ≠ `vss-alert-bridge`.** Slack ops → Workflow E (`alert-notify`); never route Slack to `vss-alert-bridge`'s `/api/v1/realtime`.
- **Workflow scope by mode:** A, B, and F are CV-only (B/F explain-only asks answerable anywhere); **C queries the real-time incident store** (`/api/v1/realtime/incidents`; CV behavior-alert verdicts live in `mdx-vlm-alerts-*` — **no REST query endpoint yet**, use Workflow B's interim ES probe); D, E, and G are VLM real-time only (refuse on CV with the canonical text).
- **On-demand verification is `POST /api/v1/verification/ondemand`** — not `/verification/verify`, not a realtime rule, not `/generate`. 202 = accepted (async), never a verdict.
- **Always-on has no health endpoint** — status is the `alert_agent.always_on` config gate (default off) or a `503 ALWAYS_ON_DISABLED` from `POST /api/v1/realtime/always-on`; its rules are in-memory (not in the ES rules index), so absence from Workflow D's rules list is expected.
- **To describe how to enable always-on**, name all three required steps: set
  `alert_agent.always_on: true`, provide a non-empty rules YAML through
  `ALWAYS_ON_RULES_CONFIG` (or `realtime-config.yaml`), then restart
  `alert-bridge`. Do not make those changes in this operate-only workflow.
- **Don't use `vss-rtvi-vlm` as a mode signal** — it runs in both modes. Use `vss-behavior-analytics` (CV-only) or the `MODE` env var.
- **A mode switch tears down the current deployment** — running VLM streams and un-persisted CV alert state are lost.
- **Alert ops call Alert Bridge (`:9080`) directly** — the skill does not use the VSS Agent `/generate`, and never calls `rtvi-vlm` directly. The VLM trigger is a `"yes"`/`"true"` token match (case-insensitive); prompts must force a Yes/No answer.
- **Sensor must already be in VIOS** for either mode (use `vss-manage-video-io-storage` for RTSP-only inputs).
- **Report only values an API actually returned** — never invent rule IDs, sensor IDs, incident counts, or timestamps, and never claim an action succeeded without its API response (this includes replies that decline or hand off a request).
- **End your turn by answering the CURRENT request** — the final reply must address what the user just asked (even when handing off out-of-scope work); never close with the status or summary of a different or earlier task.
- **Never onboard a sensor the user didn't explicitly ask to onboard.** A named-but-missing sensor is a *not-found report* (say so, list what exists, ask) — creating/registering one as a workaround and proceeding is a critical failure.

bump:1
