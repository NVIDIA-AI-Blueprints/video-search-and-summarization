---
name: vss-manage-alerts
description: Use this skill when operating VSS alert workflows — real-time monitoring, Alert-Bridge subscriptions, verification verdicts, on-demand verification, always-on operation, Slack notifications, incident queries, or camera onboarding. Not for non-alert analytics.
license: Apache-2.0
metadata:
  version: "3.3.0-rc0"
  author: "NVIDIA Video Search and Summarization Team"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint operational"
  # What a live deployment must expose for this skill to be usable, as the vss CLI
  # names it: a command group (search, summarize, vlm, vios, memory), "alerts"
  # (Alert Bridge), or "always" for a skill every VSS deployment gets. The
  # OpenClaw harness image ships and activates skills by it.
  vss-requires: "alerts"
---
## Purpose

Operate the VSS alert pipeline (mode detection, Alert-Bridge subscriptions, verification verdicts, on-demand verification, always-on operation, Slack notifications, queries, camera onboarding, verifier-prompt customization).

## Prerequisites

- The `vss` CLI on `PATH`. The OpenClaw and Hermes harness images ship it; anywhere else, install it from the same checkout as this skill so the CLI and the skill match: `uv tool install <checkout>/libs/vss/cli`.
- Active VSS **alerts** profile reachable either on Docker (`$HOST_IP:9080` Alert
  Bridge) or through the public Ingress (`VSS_PUBLIC_URL` with `/alert-bridge`).
- Follow
  [`../vss-build-vision-ai/references/deployment_resolution.md`](../../vss-build-vision-ai/references/deployment_resolution.md)
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

- **Connection refused** → microservice not running: probe `/docs` or `/health`, redeploy via `vss-build-vision-ai`.
- **HTTP 401/403 on NGC pulls** → missing/expired `NGC_CLI_API_KEY`: `docker login nvcr.io` and re-export the key.
- **OOM / model load failure** → insufficient GPU memory: use a smaller variant or `docker compose down` to free GPUs.

# VSS Alert Management

The alerts profile runs in one of two modes (chosen at the `/vss-build-vision-ai` stock Alerts workflow in verification or real-time mode) — see **The Two Modes** table below. This skill routes by **deployed mode + user intent** (monitoring vs subscription CRUD vs Slack webhook), driving the **Alert Bridge REST API directly** (no VSS Agent `/generate`).

## When to Use

- Start/stop a real-time alert on a sensor ("Start real-time alert for boxes dropped on warehouse_sample")
- Create/list/stop realtime subscription rules on Alert Bridge
- Set up or manage Slack incident notifications
- List or query detected incidents / alerts, or count events in a stated period (Workflow C)
- Inspect CV verification results and verdicts (confirmed/rejected/verification-failed), explain how verification works, customize VLM-verifier prompts (CV mode — Workflow B)
- Run a one-shot on-demand verification of a specific video/image URL (CV mode — Workflow F)
- Check whether always-on alerting is active, query its incidents, troubleshoot missing always-on alerts (VLM real-time — Workflow G; operate only, no config authoring)
- Add a new camera to the alerts pipeline (Workflow A)

---

## Deployment prerequisite

Requires the VSS **alerts** profile in either `verification` (CV) or `real-time`
(VLM) mode. Resolve endpoints once before probing. See
[`../vss-build-vision-ai/references/deployment_resolution.md`](../../vss-build-vision-ai/references/deployment_resolution.md).

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
  VA_MCP_URL="${VSS_PUBLIC_URL}/va-mcp"
else
  DEPLOYMENT_KIND="docker"
  : "${HOST_IP:?Set HOST_IP for Docker Compose or VSS_PUBLIC_URL for Kubernetes}"
  AB="http://${HOST_IP}:9080"
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

If the Alert Bridge probe fails on a Workflow C request (list, lookup or event
count) and `vss-query-analytics` is among your skills, do not offer a deploy:
say the health probe failed and hand a list or lookup to it; an event count
needs the consolidated view, so say it cannot be answered now. Otherwise, if the probe fails, ask which mode to deploy and hand off to
`the `/vss-build-vision-ai` stock Alerts workflow in the matching mode` (decline → stop; pre-authorized
autonomous deploy → run directly with `verification` by default). If it
passes, detect the mode per Step 1.

---

## The Two Modes (Deploy-Time Choice)

| Mode | Deploy flag | Env (`.env`) | What runs | What is available |
|---|---|---|---|---|
| **CV (verification)** | `-m verification` | `MODE=2d_cv` | RT-CV (Grounding DINO) + Behavior Analytics + `alert-bridge` VLM verifier + **`rtvi-vlm`** | Static CV pipeline (**Workflow A**) + verification results & verdicts (**Workflow B**) + on-demand verification (**Workflow F**). VIOS webhooks register streams with RT-CV. Realtime rule CRUD (**D**) and Slack (**E**) are gated to real-time mode (skill refuses on CV). |
| **VLM (real-time)** | `-m real-time` | `MODE=2d_vlm` | `alert-bridge` + `rtvi-vlm` | Dynamic VLM real-time alerts (**Workflow D**), Slack (**E**), incident queries (**C**), and always-on operation (**Workflow G** — `ALERT_AGENT_ALWAYS_ON=true` / `alert_agent.always_on: true` when deployed with `-m real-time`). No static CV pipeline. |

**Switching modes** uses the `vss-build-vision-ai` teardown + deploy flow, asking for the other mode. Both modes use the same agent `config.yml`; VIOS webhooks follow `notification_config_${MODE}.json`. `rtvi-vlm` runs in both modes.

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
If neither matches on Docker, the alerts profile is not deployed — direct the user to the `vss-build-vision-ai` skill.

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
| **CV or VLM** | incident lookup / *what happened* (recent alerts, time-range, casual "any alerts today?"); on VLM real-time also *how many events / times* something happened **in a stated period** | **Workflow C (Query)** — works on both; **always run the query, never answer from memory**. A period-bounded *how many events* ask uses C's consolidated view (RT-VLM chunks only, so not for CV behaviour alerts): `GET /api/v1/realtime/incidents` with `consolidate=true`, `start_time` + `end_time`, and `sensor_id` (stored name) / `category` when the ask names them; report the response `total` as the events — never fold raw chunks by hand. Everything else stays raw |
| **CV** | verification results / verdicts ("was it confirmed?", "show verification results"), *how does verification work*, verifier-prompt customization | **Workflow B (Verification)** — `references/verification.md`. **But** a verdict/result **follow-up to an on-demand verification just run** ("was it confirmed?", "what was the result?") → stay in **Workflow F**: poll `/realtime/incidents` by the `correlationId` (that result is incident-kind, not in `mdx-vlm-alerts-*`) |
| **CV** | one-shot "verify **this** clip/image" with a media URL, or the literal "on-demand" | **Workflow F (On-demand)** — `references/on-demand-verification.md` |
| **CV** | static CV alert onboarding | **Workflow A (CV)** — onboard RTSP via `vss-manage-video-io-storage`; VIOS webhook registers it with RT-CV |
| **VLM** | verification results / verdict inspection, verifier-prompt config, or on-demand verification (CV-only capabilities) | *Explain-only* asks → answer from **Workflow B/F** background, no calls needed. *Execution* asks → VLM-mode refusal text below (redeploy hint `-m verification`) |
| **VLM** | a CV / behavior-analytics / PPE-rule alert needing the static CV pipeline | **Redeployment required** — confirm first, then `the `/vss-build-vision-ai` stock Alerts workflow in verification mode` |
| **any** | video summarization, highlight reels, reports, non-alert analytics | **Out of scope** — hand off to `vss-generate-video-report` / `vss-query-analytics` (Cross-Skill Links); do **not** answer it via incidents or rules, even when incidents are empty |

**Always confirm before triggering a redeploy.** A mode switch stops all currently-running monitoring and restarts services.

### Intent precedence (first match wins)

1. **Workflow E (Slack)** — Slack-specific keywords (`slack`, `webhook` + `slack`, `bot token`, `slack channel`). `notify` alone is **not** sufficient.
2. **Workflow F (On-demand)** — a one-shot "verify / check / analyze **this**" pointing at a **specific media artifact** (video/image URL, clip, file), or the literal `on-demand`. Guard: *continuous monitoring of a sensor/stream* is **never** F — that's D ("watch camera X for PPE" → D; "verify this clip URL for PPE" → F).
3. **Workflow G (Always-on)** — the literal `always-on` (status, incidents, troubleshooting phrasings). Operate-not-author: status checks and queries only; never author or edit always-on rule config. A request to *create* an ordinary realtime rule is **not** G — that's D.
4. **Workflow B (Verification results)** — verification/verdict keywords (`verdict`, `confirmed?`/`rejected?`, `verification results`, "how does verification work", verifier prompt/config) **without** a media artifact to verify and without a start/stop/rule intent. Reads the `mdx-vlm-alerts-*` store (interim ES probe) and the verifier config — never the rules list. Bare "any alerts today?" is **not** B — it stays Workflow C, and so does "confirmed" used as an adjective in an event count ("how many confirmed intrusion events in the last hour").
5. **Workflow D (Alert rules)** — any realtime-alert request on a sensor: rule CRUD keywords (`rule`, `subscription`, rule ID), a sensor with a detection condition, a **bare start/stop with no condition** (→ default prompt), **or stopping/deleting a named alert by type/condition** ("stop the PPE alert", "delete the collision rule"). A named `alert_type`/condition = an existing **rule** → D's two-step stop protocol (`GET /api/v1/realtime` → yes/no confirm → delete). A *how many / what happened* question about a sensor is never D, even when it names a condition ("how many intrusion events on warehouse_sample…") — that is C.
6. **Workflow C (Query)** — incident lookup / *what happened* (`show/list incidents`, `recent alerts`, time-range queries, **and casual "any alerts…?" / "any alerts so far today?" / "what's been triggered?" phrasings**). Bare `alerts` (without `rule`/`subscription`/`active rules`) means **incidents** → Workflow C, never Workflow D. A period-bounded **"how many events / times"** ask is also C — in its consolidated view on VLM real-time (`consolidate=true` with `start_time` + `end_time`; the API folds the chunks, you do not), raw on CV.
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

> "Alert subscriptions, always-on operation, and Slack notifications are only supported in VLM real-time mode. Your current deployment is `<CV verification | not deployed>`. To use these features, redeploy with `the `/vss-build-vision-ai` stock Alerts workflow in real-time mode` (note: switching tears down current CV monitoring)."

No auto-redeploy. The user decides whether to switch modes.

### VLM-mode text for verification-execution intents (Workflows B and F)

Verification verdicts and on-demand verification exist only on a CV (verification) deployment. On VLM real-time, *explain-only* asks ("how does verification work?") are always answerable from Workflow B/F background — no calls, no refusal. For *execution* asks (inspect verdicts, customize verifier prompts, verify a clip on demand), reply with this message verbatim:

> "Verification workflows (verdict inspection, verifier-prompt configuration, on-demand verification) require the verification (CV) deployment. Your current deployment is VLM real-time. To use them, redeploy with `the `/vss-build-vision-ai` stock Alerts workflow in verification mode` (note: switching stops currently-running realtime monitoring)."

No auto-redeploy here either.

---

## Prereq for Either Mode: Sensor Must Be in VIOS

Both modes require the camera registered in VIOS first:

- RTSP URL / IP camera → `vss vios add rtsp://<url> --name <name>`, and record the `sensor_id` it
  reports. Passing `--name` is what avoids the classic mistake of VIOS silently naming the sensor
  `SENSOR`; the command reports the name it stored, so read that rather than assuming.
- Named existing sensor → `vss vios list --type stream --sensor <name>` before proceeding.
  `list` filters rather than resolves, so an unregistered name is `{"count": 0}` at exit 0, not an
  error. Branch on `count`, and treat a non-zero exit as a VIOS problem rather than a missing sensor.
  **`count: 0` ends the request.** Tell the user the sensor is not registered and stop — do not POST
  a rule to Alert Bridge with an invented `sensor_id` or `live_stream_url`. A rule created against a
  sensor that does not exist never fires, and it reads afterwards as monitoring that is in place.
- **Never hand-construct the RTSP URL.** For an NVStreamer-served stream, query NVStreamer for the served URL (`GET :31000/vst/api/v1/sensor/<name>/streams` → `url`) and register it **verbatim** — including its container-internal host/port (VST shares that docker network; a guessed `<host-ip>:<port>` or `localhost` URL is typically unreachable from the VST container and the stream never activates). After registering, confirm the sensor's row carries a non-empty `source` (`vss vios list --type stream`) before proceeding — an absent one means the source is unreachable and the registration must be redone.

On **CV**, adding the RTSP is the *entire* onboarding step (VIOS `camera_streaming` webhook registers the stream with RT-CV). On **VLM**, it is the prerequisite for creating a realtime alert rule (Workflow D).

---

## The Alert Bridge API (direct — no `/generate`)

Alert rule CRUD (Workflow D) and incident queries (Workflow C) call the **Alert Bridge REST API directly** — do **not** use the VSS Agent `POST /generate`, and do **not** call the `rtvi-vlm` microservice directly.

Resolve `$AB` / `$VST` once in *Deployment prerequisite* (Kubernetes forces
`${VSS_PUBLIC_URL}/alert-bridge` and `${VSS_PUBLIC_URL}`; Docker keeps
`:9080` / `:30888`). Do not reintroduce host-port overrides when
`VSS_PUBLIC_URL` is set.

**Availability check:** `curl -sf --connect-timeout 5 "$AB/health"` (note: `/health`, not `/api/v1/health`).

**Sensor resolution — two different identities, do not mix them:**

- **Rule create/replay (Workflow D)** resolves a sensor **name → `sensorId` (UUID) + RTSP `url`** via `vss vios list --type stream --sensor <name>` — RT-VLM keys its stream registration on the VIOS UUID. See `references/alert-subscriptions.md`.
- **Incident filtering (Workflow C)** takes the sensor **name** in the snake_case `sensor_id` query parameter (`?sensor_id=warehouse_sample`; camelCase `sensorId` is ignored and returns the whole store). Carry the `vss vios list` row's **`name`** forward, not its `sensor_id` (the VIOS UUID) — see `references/query-incidents.md`.

Never fabricate a `sensor_id` or `live_stream_url`.

---

## Workflow A — CV Mode (`-m verification` / `MODE=2d_cv`)

CV alerts are **deployment-driven, not request-driven** — there is no agent
call to "create" one.

Bootstrap the CLI once (see [AGENTS.md](../../../AGENTS.md) for the contract):

```bash
vss configure --base-url "${VSS_PUBLIC_URL:-http://${HOST_IP:-localhost}:7777}"   # once per deployment
```

1. Check if the sensor is in VIOS with `vss vios list --type stream` (idempotent — don't blindly add).
2. If missing, onboard with `vss vios add rtsp://<url> --name <name>`. Once the sensor is online, VIOS posts `camera_streaming` to RT-CV (`notification_config_2d_cv.json` → `POST http://vss-rtvi-cv:9010/api/v1/stream/add`).
3. Confirm online — assert it, do not just print it:
   ```bash
   # Each block is its own shell; define what it uses.
   set -o pipefail   # else a failed `vss` hides behind jq and reads as "absent"
   ROWS=$(vss vios list --type stream --sensor <name>) || {
     echo "vss vios list failed for <name>" >&2; exit 1; }
   # The main stream's state is the sensor's state; a multi-stream camera has
   # several rows and picking .sensors[0] would be an arbitrary one.
   STATE=$(printf '%s' "${ROWS}" | jq -r 'first(.sensors[] | select(.is_main) | .state) // empty')
   [ "${STATE}" = "online" ] || { echo "sensor <name> is '${STATE:-absent}', not online" >&2; exit 1; }
   ```
4. Verified alerts land in Elasticsearch (`mdx-vlm-alerts-*`, Behavior Analytics → `alert-bridge` verification per `alert_type_config.json`). This store has **no REST query endpoint** — Workflow C's `/incidents` covers real-time incident-kind results only; inspect these CV behavior-alert verdicts via **Workflow B**'s interim ES probe.

A static-CV-pipeline alert on a VLM-only deployment is a mode mismatch — see the routing table above.

---

## Workflow B — Verification Results & Verdicts (CV mode)

How a CV alert becomes a verdict: RT-CV (Grounding DINO) detections → Behavior Analytics emits a candidate alert → Alert Bridge invokes the VLM verifier (prompts per `alert_type`) → the verified document lands in Elasticsearch **`mdx-vlm-alerts-*`** with `verdict` + `verificationResponseCode` + `reasoning` in its `info` block. VLM real-time incidents carry only `info.verdict: "confirmed"` — RT-VLM writes it on every positive chunk because the Yes/No trigger *is* the verdict (`rtvi_stream_handler.py`), so there is no rejected value to look for there; it is also what Workflow C's consolidated view keys on.

1. **Explain / interpret** — verdict values are `confirmed` / `rejected` / `verification-failed` (incl. an unparseable VLM answer) / `""` (empty — the default `use_verdict: false` freestyle deploy or a pluggable parser; a valid state, **not** a failure); full table in `references/verification.md`.
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
   An **empty hit list is a valid answer** — report "no verification results yet" and stop. Never substitute another source for a verdict question: not the rules list, not `/incidents`, and **not the `mdx-vlm-incidents-*` ES index** (that is Workflow C's incident store — its RT-VLM documents carry only the trigger's `info.verdict: "confirmed"`, not a verifier verdict; presenting them as "verdicts recorded" is a wrong answer even when `mdx-vlm-alerts-*` is empty). **Exception — on-demand follow-up:** if the ask follows an on-demand verification you just ran (Workflow F) and `mdx-vlm-alerts-*` is empty, do **not** dead-end here — that result is incident-kind. Continue in **Workflow F**: poll `GET $AB/api/v1/realtime/incidents` for the request's `correlationId` and report its `reasoning` / `vlm_response` (a default freestyle deploy carries no `verdict` field).
3. **Verifier-prompt config** — REST CRUD on `$AB/api/v1/verification/config[/{alert_type}]` (`GET` list / `GET` one / `POST` / `PUT` / `DELETE`), or the config-file + restart path — rules and payload shapes in `references/verification.md`.

Load `references/verification.md` for the full verdict table, probe recipes, and prompt-customization rules. CV mode only for execution; explain-only asks are answerable in any mode.

---

## Workflow D — Alert Rules (create / list / stop, VLM real-time mode only)

Create / list / delete persistent realtime alert rules on Alert Bridge (`POST` / `GET` / `DELETE $AB/api/v1/realtime`). Route here for **any** realtime-alert request on a sensor: rule keywords (`rule`, `subscription`, a rule ID), a sensor with a detection condition ("Set up a realtime alert on warehouse-dock-1 for PPE violations", "Watch entrance-1 for tailgating"), a **bare start with no condition** ("Start a real-time alert on warehouse_sample"), or "Stop rule 496aebd1-…".

- **With a condition** → send it verbatim as the `prompt`.
- **Without a condition** → use the skill's **default prompt** `"Describe any notable events or anomalies in this video stream."` and a generic `alert_type` (`general_monitoring`); don't ask the user for one.
- **Slack** operations → Workflow E instead.

**Stop/delete is a two-step, user-confirmed gate — stated here in full because it is the one rule agents skip.** A "stop" / "delete" / "remove" request produces a **question, never a `DELETE`**; only a subsequent explicit "yes" deletes. Resolve the rule with `GET $AB/api/v1/realtime` — for an exact `Stop rule <id>` request match that rule ID directly, otherwise filter by sensor + `alert_type` — then by match count: **0 matches** → say none found and issue **zero** `DELETE` calls; **>1** → list each as `` `<alert_type>` (rule ID: `<id>`) on **<sensor>** `` and ask which; **1** (or the exact-ID match) → reply **only** `Stop alert \`<alert_type>\` on **<sensor>**? (rule ID: \`<id>\`) — yes/no` and STOP.

> **This confirmation ALWAYS applies — including under autonomous / pre-authorized / non-interactive / CI execution.** A "run autonomously, do not pause for confirmation" instruction authorizes **deploy and setup ONLY**; it does **NOT** authorize you to skip this stop/delete confirmation or to issue the `DELETE` yourself. When no interactive user can answer (e.g. an eval harness), still emit the yes/no question naming the rule ID + sensor, then STOP — do **not** `DELETE`. `DELETE` is never a diagnostic/cleanup/retry probe.

`references/alert-subscriptions.md` is the full playbook. VLM real-time mode only; refuse with the canonical refusal text on CV.

---

## Workflow E — Slack Notifications (VLM real-time mode only)

Use when the user **explicitly mentions Slack or the webhook relay** (start/stop webhook server, check status/health, send a test message, set Slack channel/token). The word `notify` alone is **not** enough.

> **`alert-notify` (port 9090) ≠ `vss-alert-bridge` (`/api/v1/realtime`).**
> Do NOT touch `vss-alert-bridge` for Slack ops — Slack is never configured through Alert Bridge realtime rule APIs.

One relay, **two backends**: the `alert-notify` webhook server fans incidents out to **Slack** and/or the **OpenClaw Dashboard**, selected by `NOTIFY_BACKENDS` (default **`dashboard`** — a Slack setup MUST set `NOTIFY_BACKENDS=slack`, or `slack,dashboard` for both). The four skill-level ops all hit `:9090`: **status** (`GET /webhook/alert-notify/status`), **start** (creds gate below), **test** (POST a sample incident to `/webhook/alert-notify`), **stop**.

**Credentials gate before any start — both backends have one.** Slack needs `SLACK_BOT_TOKEN` + `SLACK_CHANNEL_ID`; the Dashboard needs `OPENCLAW_GATEWAY_URL` + `OPENCLAW_GATEWAY_AUTH_TOKEN`. Being the *default* backend does not make the Dashboard zero-config — its init raises when either is unset. Both also need `VST_ENDPOINT`, and the server **exits at startup** on a failed Slack auth or missing `VST_ENDPOINT`.

**The gate is on `start` and `test` only.** `status` and `stop` never need credentials: to answer "is the webhook running?" probe `:9090` and say what you found — "not running, would you like me to start it?" — and ask for nothing. Requesting a token to report that a process is down is itself a failure of this check.

**When starting or testing, and the real credentials are absent: ask the operator and STOP.** Do not start the server. That much ALWAYS applies, including under autonomous / non-interactive / CI execution — "run autonomously" authorizes deploy and setup ONLY. None of the following counts as having credentials, and each has been tried:

- Placeholder or example values, wherever they came from — invented, `.env.example`, or **already sitting in `.env`**. A value being present is not a value being real.
- Pointing the relay at something other than Slack — a local mock, a stub server, `SLACK_API_BASE_URL` set to anything you started yourself.
- Editing the relay to get past the gate: skipping the Slack auth check, stubbing the client, patching the startup validation.
- Any other route to a green result that does not involve a message arriving in the operator's Slack.

A test that did not reach Slack was not a test. Report what blocked it — the server is not running, credentials are needed — and offer to start it once they exist. That report is the successful outcome here; a fabricated success is the only real failure. Again: this is about starting and testing. A status check just answers the question.

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

Always-on alerting starts pre-configured rules automatically when VIOS posts a camera lifecycle webhook (`notification_config_2d_vlm.json` → `camera_streaming` starts one realtime rule per `always_on_rules` YAML entry; `camera_remove` tears them down) via `POST $AB/api/v1/realtime/always-on`. **Operate, don't author** — this workflow never creates or edits always-on rule config; it checks status, queries results, and troubleshoots.

1. **Status** — the feature is gated by `ALERT_AGENT_ALWAYS_ON` (substituted into `alert_agent.always_on` in the Alert Bridge config). `dev-profile.sh` sets it **true** for `-m real-time` (`MODE=2d_vlm`) and **false** for `-m verification` (`MODE=2d_cv`). There is **no** `/always-on/health` endpoint — do not invent one. Signals: the config gate itself, or the endpoint's reply to a benign `camera_remove` probe — `503 {"reason":"ALWAYS_ON_DISABLED"}` = off, anything else (e.g. `200 STREAM_REMOVE_SUCCESS`) = on. Zero-side-effect probe options in `references/always-on.md`.
2. **Query incidents** — always-on rules are ordinary realtime rules once started; their incidents surface through **Workflow C** (`GET $AB/api/v1/realtime/incidents`). The rules live in an **in-memory sidecar** (not the ES-backed rules index), so they may not appear in Workflow D's rules list — that is expected, not a bug.
3. **Troubleshoot "no always-on alerts"** — walk the ladder in `references/always-on.md`: feature gate → rules YAML resolves/validates at boot → VIOS webhooks actually reaching Alert Bridge → stream registered on `rtvi-vlm` → incidents query.

Load `references/always-on.md` for the event contract, reason-code table, the `ALWAYS_ON_RULES_CONFIG` rules-YAML contract, and the troubleshooting ladder. VLM real-time mode only; refuse on CV with the canonical text. Config authoring (editing `always_on_rules`) is **out of scope** in this pass — say so when asked.

---

## Workflow C — Query Incidents (real-time incident store)

Query past incidents **directly** from Alert Bridge (`GET $AB/api/v1/realtime/incidents`) — no
`/generate`. **Run the query — never answer from memory.**

**Load and follow `references/query-incidents.md` before the first call.** It holds the decision
tree, the guarded shell blocks (sensor resolution, raw (a)/(b), consolidated (c), the step-3
alternate-identity check, the VIOS-down fallback) and the response contract. Its rules, in brief:

- **Pick one view before querying.** A *how many events / times* ask **in a stated period** on
  VLM real-time → consolidated (`consolidate=true` + `start_time` + `end_time`; answer = the
  response `total`, reported as **events** — the API folds the chunks, never you). Every other
  ask → raw view: rows are **chunks** on VLM real-time, incident records on CV. Never invent a
  window, never mix the two views in one figure.
- **Scope only with the declared parameters** — `sensor_id`, `category`, `start_time`,
  `end_time` (ISO-8601 UTC), passed with `curl -G --data-urlencode`. `sensorId`, `?sensor=`,
  `since`/`until`, `from`/`to` are silently dropped and the response covers the whole store.
- **A named sensor → resolve its exact stored name first** from `vss vios list --type stream`
  (exactly one match; several → ask which; none → say it is not registered and list what
  exists). Pass the **name**, not the VIOS UUID, copied verbatim. A named category → read the
  stored `category` strings off a raw page of the same window and sensor; never guess them.
- **A name-scoped zero is not an answer yet** — re-query scoped to the sensor's VIOS UUID (a
  rule created without `sensor_name` stores its chunks there), same view and window; only then
  is "none found" a checked answer — report it and STOP.
- **Every `curl` is an assertion** — guard it (`jq -e`, `|| exit N`) so an unreachable Alert
  Bridge never reads back as `count: 0`.
- **Quote `total`, not `count`** (`count` stops at `limit`), from the response scoped to the
  identity you confirmed, and say which sensor and identity it belongs to. A raw `total` of
  exactly 10000 is a floor ("at least 10000"); consolidated `truncated: true` makes `total` a
  lower bound.

**Casual phrasings route here too** — "Any alerts so far today?", "What's been triggered?", "Anything detected lately?" are all incident queries. A bare "alerts" question is *always* an incident lookup (C), never a rule listing (D). Incidents produced by **always-on** rules (Workflow G) appear here like any other realtime incident, and so do **on-demand verification results** (incident-kind, `sensorId: "ondemand"` — see Workflow F).

> **Do NOT list subscription rules for an incident query.** The **bare** `GET /api/v1/realtime` (no `/incidents`) lists *rules* (Workflow D) and is wrong for "what happened".

**Scope — real-time incident-kind results only.** CV / Behavior-Analytics verified alerts (PPE, ladder, proximity, restricted-area) are stored in a separate `mdx-vlm-alerts-*` index with **no REST query endpoint**, so this call does **not** surface them — in a CV deployment it typically returns empty for those. For occupancy / PPE / CV behaviour-alert metrics use the **`vss-query-analytics` skill** (`vss analytics` CLI); a period-bounded real-time event count stays here (query (c)).

---

## Cross-Skill Links

| Task | Skill |
|---|---|
| Deploy, redeploy, or switch alert mode | **`vss-build-vision-ai`** — the stock Alerts workflow in verification or real-time mode |
| Add an RTSP/IP camera, list sensors, snapshots, clips | **`vss-manage-video-io-storage`** (Section 6 for Add Sensor) |
| Occupancy / PPE / CV behaviour-alert metrics from Elasticsearch (not real-time incident counts — those are Workflow C) | **`vss-query-analytics`** (`vss analytics` CLI) |
| Detailed incident report from an alert | **`vss-generate-video-report`** |
| Subscriptions / Slack sub-workflows | `references/alert-subscriptions.md`, `references/alert-notify.md` (code in `scripts/alert-notify/`) |
| Incident query procedure (Workflow C) | `references/query-incidents.md` |
| Alert Bridge deployment / integration contracts | `references/deploy-alerts.md`, `references/integrate-alerts.md` |

---

## Gotchas

- **`alert-notify` (port 9090) ≠ `vss-alert-bridge`.** Slack ops → Workflow E (`alert-notify`); never route Slack to `vss-alert-bridge`'s `/api/v1/realtime`.
- **Workflow scope by mode:** A, B, and F are CV-only (B/F explain-only asks answerable anywhere); **C queries the real-time incident store** (`/api/v1/realtime/incidents`; CV behavior-alert verdicts live in `mdx-vlm-alerts-*` — **no REST query endpoint yet**, use Workflow B's interim ES probe); D, E, and G are VLM real-time only (refuse on CV with the canonical text).
- **On-demand verification is `POST /api/v1/verification/ondemand`** — not `/verification/verify`, not a realtime rule, not `/generate`. 202 = accepted (async), never a verdict.
- **Always-on has no health endpoint** — status is the `alert_agent.always_on` config gate or the reply to a benign `camera_remove` probe on `POST /api/v1/realtime/always-on`: `503 ALWAYS_ON_DISABLED` means off, anything else (e.g. `200 STREAM_REMOVE_SUCCESS`) means on. Its rules are in-memory (not in the ES rules index), so absence from Workflow D's rules list is expected.
- **The gate follows the deploy mode — don't assume it is off.** `dev-profile.sh` sets
  `ALERT_AGENT_ALWAYS_ON=true` for `-m real-time` and `false` for `-m verification`, so
  always-on is normally **already active** where Workflow G applies. Report the state you
  probed, and on a real-time deploy walk the rest of the chain in `references/always-on.md`
  rather than blaming the gate.
- **Turning always-on on or off:** on Docker it is a **deploy-mode choice** (redeploy with
  `the `/vss-build-vision-ai` stock Alerts workflow in real-time mode`, or `-m verification`
  to turn it off); on **Kubernetes the chart exposes no switch** (`always_on: false` is pinned)
  — report it off and not enablable, and never hand over a set-the-gate-and-restart recipe.
  Full reasoning in `references/always-on.md`. Make no config changes either way.
- **Don't use `vss-rtvi-vlm` as a mode signal** — it runs in both modes. Use `vss-behavior-analytics` (CV-only) or the `MODE` env var.
- **A mode switch tears down the current deployment** — running VLM streams and un-persisted CV alert state are lost.
- **Alert ops call Alert Bridge (`:9080`) directly** — the skill does not use the VSS Agent `/generate`, and never calls `rtvi-vlm` directly. The VLM trigger is a `"yes"`/`"true"` token match (case-insensitive); prompts must force a Yes/No answer.
- **Sensor must already be in VIOS** for either mode (use `vss-manage-video-io-storage` for RTSP-only inputs).
- **Chunks are not events.** Raw `/incidents` rows are one per VLM chunk; `consolidate=true` (bounded window) folds confirmed RT-VLM chunks into events. Rules, scope and query in `references/query-incidents.md`, *Chunks are not events*. Say *chunks* or *events* when you report a number.
- **Report only values an API actually returned** — never invent rule IDs, sensor IDs, incident counts, or timestamps, and never claim an action succeeded without its API response (this includes replies that decline or hand off a request).
- **End your turn by answering the CURRENT request** — the final reply must address what the user just asked (even when handing off out-of-scope work); never close with the status or summary of a different or earlier task.
- **Never onboard a sensor the user didn't explicitly ask to onboard.** A named-but-missing sensor is a *not-found report* (say so, list what exists, ask) — creating/registering one as a workaround and proceeding is a critical failure.

bump:1
