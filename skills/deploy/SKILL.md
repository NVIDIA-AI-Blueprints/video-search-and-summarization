---
name: deploy
description: Deploy, debug, or tear down any VSS profile using the compose-centric workflow — config (dry-run) with env overrides, review resolved compose, then compose up. Also debug/verify a running deployment end-to-end using `scripts/test_base.py` (upload a sample warehouse video, exercise the agent's video-Q&A path, confirm the stack is healthy). Use this skill when the user says "deploy vss", "deploy <profile>", "debug deploy", "verify deployment", "test the deployed agent", or "why is my vss deploy broken". Works via orchestrator-mcp tools (OpenClaw sandbox) or direct docker compose (Claude Code on host).
metadata:
  { "openclaw": { "emoji": "🚀", "os": ["linux"] } }
---

# VSS Deploy

Deploy any VSS profile using a compose-centric workflow: build env overrides, generate resolved compose (dry-run), review, then deploy. Replaces direct `dev-profile.sh` execution with validated, auditable steps.

## Profile Routing

| User says | Profile | Reference |
|---|---|---|
| "deploy vss" / "deploy base" | `base` | `references/base.md` |
| "deploy alerts" / "alert verification" / "real-time alerts" | `alerts` | `references/alerts.md` |
| "deploy for incident report" | `alerts` | `references/alerts.md` |
| "deploy lvs" / "video summarization" | `lvs` | `references/lvs.md` |
| "deploy search" / "video search" | `search` | `references/search.md` |

## When to Use

- Deploy VSS / start VSS / bring up a profile
- Deploy a specific profile (base, alerts, lvs, search)
- Do a dry-run / preview what will be deployed
- Change deployment config (hardware, LLM mode, GPU assignment)
- Tear down a running deployment
- **Debug or verify** an existing deployment (see [Debugging a Deployment](#debugging-a-deployment))

## Execution Modes

The workflow is identical — only the transport differs.

**MCP mode** (OpenClaw in sandbox) — call orchestrator-mcp tools via JSON-RPC on port 8090:

```
deploy/prereqs  → check system readiness
deploy/config   → generate env + resolved compose (dry-run)
deploy/compose-read  → review resolved compose
deploy/compose-edit  → modify before deploying
deploy/up       → start containers
```

**Direct mode** (Claude Code on host) — run docker compose commands directly:

```bash
# 1. Apply env overrides to the profile .env file
# 2. docker compose --env-file .env config > resolved.yml   (dry-run)
# 3. Review resolved.yml
# 4. docker compose -f resolved.yml up -d
```

Use MCP mode inside an OpenClaw/NemoClaw sandbox. Use Direct mode as Claude Code on the host.

## Before Deploying

1. **Repo path** — find `video-search-and-summarization/` on disk. Check `TOOLS.md` if available.
2. **NGC CLI & API key** — see [`references/ngc.md`](references/ngc.md). Check `$NGC_CLI_API_KEY` is set.
3. **System prerequisites** — see [`references/prerequisites.md`](references/prerequisites.md) for GPU, Docker, NVIDIA Container Toolkit.

### Pre-flight Check

Run before every deploy. Do not proceed if any check fails.

```bash
# 1. GPU visible
nvidia-smi --query-gpu=index,name --format=csv,noheader

# 2. NVIDIA runtime in Docker
docker info 2>/dev/null | grep -i "runtimes"

# 3. NVIDIA runtime works end-to-end
docker run --rm --gpus all ubuntu:22.04 nvidia-smi 2>&1 | head -5
```

If check 2 or 3 fails, see [`references/prerequisites.md`](references/prerequisites.md).

## Deployment Flow

Always follow this sequence. Never skip the dry-run.

### Step 1 — Gather context

| Value | How to determine |
|---|---|
| **Profile** | Match user intent to routing table above. Default: `base` |
| **Repo path** | Find `video-search-and-summarization/` on disk |
| **Hardware** | `nvidia-smi --query-gpu=name --format=csv,noheader` → map to profile |
| **LLM/VLM mode** | `local_shared` (default), `local` (dedicated GPUs), or `remote` |
| **API keys** | `NGC_CLI_API_KEY` for local NIMs, `NVIDIA_API_KEY` for remote |
| **Host IP** | `hostname -I \| awk '{print $1}'` |

**Hardware profile mapping:**

| GPU name contains | HARDWARE_PROFILE |
|---|---|
| H100 | `H100` |
| L40S | `L40S` |
| RTX 6000 Ada, RTX PRO 6000 | `RTXPRO6000BW` |
| GB10 (DGX Spark) | `DGX-SPARK` |
| IGX | `IGX-THOR` |
| AGX | `AGX-THOR` |
| Other | `OTHER` |

### Step 2 — Build env_overrides

Build a dictionary of env var overrides based on user intent. Only include vars that differ from the profile's `.env` defaults.

**Always set (they have placeholder defaults in the template):**

| Var | Value |
|---|---|
| `HARDWARE_PROFILE` | Detected or user-specified |
| `MDX_SAMPLE_APPS_DIR` | `<repo>/deployments` |
| `MDX_DATA_DIR` | `<repo>/data` (or user-specified) |
| `HOST_IP` | Detected host IP |
| `NGC_CLI_API_KEY` | From environment or user |

**Common overrides by user intent:**

| User intent | Env overrides |
|---|---|
| Remote LLM | `LLM_MODE=remote`, `LLM_BASE_URL=<host>` (no `/v1`), `NVIDIA_API_KEY=<key>` |
| Remote VLM | `VLM_MODE=remote`, `VLM_BASE_URL=<host>` (no `/v1`), `NVIDIA_API_KEY=<key>` |
| NVIDIA API for remote inference | `LLM_BASE_URL=https://integrate.api.nvidia.com` |
| Dedicated GPUs | `LLM_MODE=local`, `VLM_MODE=local`, `LLM_DEVICE_ID=0`, `VLM_DEVICE_ID=1` |
| Different LLM model | `LLM_NAME=<name>`, `LLM_NAME_SLUG=<slug>` |
| Different VLM model | `VLM_NAME=<name>`, `VLM_NAME_SLUG=<slug>` |

> **Important — `/v1` suffix on base URLs**
>
> `LLM_BASE_URL` and `VLM_BASE_URL` must **not** include a trailing `/v1`.
> The agent's `config.yml` appends `/v1` automatically (`base_url: ${LLM_BASE_URL}/v1`),
> so including it yourself produces `/v1/v1/chat/completions` and requests will fail
> with connection / 404 errors.
>
> If a user or endpoint documentation gives you a URL ending in `/v1`, strip it
> before writing to `.env`. Examples:
> - User says: "LLM is at `http://10.0.0.5:31081/v1`" → write `LLM_BASE_URL=http://10.0.0.5:31081`
> - User says: "Use `https://integrate.api.nvidia.com/v1`" → write `LLM_BASE_URL=https://integrate.api.nvidia.com`

See the profile reference doc for full env override recipes.

**Do NOT set `COMPOSE_PROFILES` directly** — it is computed from `BP_PROFILE`, `MODE`, `HARDWARE_PROFILE`, `LLM_MODE`, `LLM_NAME_SLUG`, `VLM_MODE`, `VLM_NAME_SLUG`.

### Step 3 — Config / dry-run

**Env file location:** `<repo>/deployments/developer-workflow/dev-profile-<profile>/.env`

**MCP mode:**
```
deploy/config(profile=<profile>, env_overrides={...})
```

**Direct mode:**
```bash
REPO=/path/to/video-search-and-summarization
PROFILE=base
ENV_FILE=$REPO/deployments/developer-workflow/dev-profile-$PROFILE/.env

# Read current .env, apply overrides, write back
# (read lines, update matching keys, append new keys, write)

# Resolve compose
cd $REPO/deployments
docker compose --env-file $ENV_FILE config > resolved.yml
```

The resolved YAML is saved to `<repo>/deployments/resolved.yml`.

### Step 4 — Review

Show the user a summary of what will be deployed:

- Profile name and hardware
- LLM/VLM models and mode (local/remote/local_shared)
- Services that will start
- GPU device assignment
- Key endpoints (UI port, agent port)

Ask: **"Looks good — deploy now?"**

Do NOT proceed without user confirmation.

### Step 5 — Deploy

**MCP mode:**
```
deploy/up()
```

**Direct mode:**
```bash
cd $REPO/deployments
docker compose -f resolved.yml up -d --force-recreate
```

Deploy takes ~10-20 min on first run (image pulls + model downloads). Monitor:

```bash
# Container status
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'

# Logs for a specific service
docker compose -f $REPO/deployments/resolved.yml logs --tail 50 <service>
```

Deploy is complete when all `mdx-*` containers show `Up` status.

### Step 6 — Report endpoints

| Profile | Agent UI | REST API | Other |
|---|---|---|---|
| base | `:3000` | `:8000` (Swagger at `/docs`) | — |
| alerts | `:3000` | `:8000` | VIOS dashboard `:30888/vst/` |
| lvs | `:3000` | `:8000` | — |
| search | `:3000` | `:8000` | — |

Use workflow skills after deployment:
- **alerts** / **incident-report** → alert management and incident queries
- **video-search** → semantic video search
- **video-summarization** → long video summarization
- **sensor-ops** → camera/stream management via VIOS
- **video-analytics** → Elasticsearch queries

## Tear Down

**MCP mode:**
```
deploy/down()
```

**Direct mode:**
```bash
cd $REPO/deployments
docker compose -f resolved.yml down
```

## Debugging a Deployment

Use this workflow when the user asks to "debug the deploy", "verify it's working",
"why is the agent not responding", or similar. The goal is to confirm the full
video-ingestion-to-agent-answer path, not just that containers are "Up".

Each profile reference doc (e.g. [`references/base.md`](references/base.md)) has a
**Debugging** section listing the exact commands to run for that profile.

### Quick checks (all profiles)

```bash
# 1. All expected containers Up
docker ps --format 'table {{.Names}}\t{{.Status}}'

# 2. Agent API + UI responding
curl -sf http://localhost:8000/docs >/dev/null && echo "agent OK"
curl -sf http://localhost:3000/ >/dev/null && echo "ui OK"

# 3. VLM NIM responding (base/lvs profiles)
curl -sf http://localhost:30082/v1/models | python3 -m json.tool

# 4. LLM NIM responding
curl -sf http://localhost:30081/v1/models | python3 -m json.tool
```

### End-to-end video sanity check

`scripts/test_base.py` is the canonical end-to-end probe. It:

1. Waits for the agent `/health` endpoint
2. Asks the agent for a VST upload URL (`POST /api/v1/videos`)
3. Uploads a public warehouse video (Pexels CC0, ~1 MB) directly to VST
4. Verifies the video is visible via `GET /vst/api/v1/sensor/streams`
5. Sends the blueprint queries over the agent WebSocket
   (`"What videos are available?"` / `"Generate a report for video <name>"`)
6. Handles HITL prompts (VLM-prompt for `base`, scenario/events/objects for `lvs`)
7. Prints pass/fail and a response snippet

Usage:

```bash
# Install once
pip install websocket-client

# base profile
python skills/deploy/scripts/test_base.py http://localhost:8000 \
    --profile base

# lvs profile
python skills/deploy/scripts/test_base.py http://localhost:8000 \
    --profile lvs

# Use a local video instead of the default Pexels download
python skills/deploy/scripts/test_base.py http://localhost:8000 \
    --video-path /path/to/my_video.mp4 --profile base
```

The script exits non-zero on any failure, so it can also be wired into CI or
an eval verifier. If any step fails, cross-reference the vss-agent log
(`docker logs vss-agent`) for the error line — the script prints which
step (upload / VST check / query) tripped.

## Troubleshooting

- `unknown or invalid runtime name: nvidia` → NVIDIA Container Toolkit not installed or Docker not restarted. See [`references/prerequisites.md`](references/prerequisites.md).
- NGC auth error → re-export `NGC_CLI_API_KEY` or follow [`references/ngc.md`](references/ngc.md).
- GPU not detected → run `sudo modprobe nvidia && sudo modprobe nvidia_uvm`, then retry.
- `deploy/up` fails with "no resolved compose" → must run `deploy/config` (Step 3) first.
- cosmos-reason2-8b crash → must redeploy the full stack (known issue: NIM cannot restart alone).
