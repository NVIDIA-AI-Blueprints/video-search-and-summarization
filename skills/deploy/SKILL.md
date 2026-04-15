---
name: deploy
description: Deploy or tear down any VSS profile using the compose-centric workflow — config (dry-run) with env overrides, review resolved compose, then compose up. Works via orchestrator-mcp tools (OpenClaw sandbox) or direct docker compose (Claude Code on host).
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
| Remote LLM | `LLM_MODE=remote`, `LLM_BASE_URL=<url>`, `NVIDIA_API_KEY=<key>` |
| Remote VLM | `VLM_MODE=remote`, `VLM_BASE_URL=<url>`, `NVIDIA_API_KEY=<key>` |
| NVIDIA API for remote inference | `LLM_BASE_URL=https://integrate.api.nvidia.com/v1` |
| Dedicated GPUs | `LLM_MODE=local`, `VLM_MODE=local`, `LLM_DEVICE_ID=0`, `VLM_DEVICE_ID=1` |
| Different LLM model | `LLM_NAME=<name>`, `LLM_NAME_SLUG=<slug>` |
| Different VLM model | `VLM_NAME=<name>`, `VLM_NAME_SLUG=<slug>` |

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

## Troubleshooting

- `unknown or invalid runtime name: nvidia` → NVIDIA Container Toolkit not installed or Docker not restarted. See [`references/prerequisites.md`](references/prerequisites.md).
- NGC auth error → re-export `NGC_CLI_API_KEY` or follow [`references/ngc.md`](references/ngc.md).
- GPU not detected → run `sudo modprobe nvidia && sudo modprobe nvidia_uvm`, then retry.
- `deploy/up` fails with "no resolved compose" → must run `deploy/config` (Step 3) first.
- cosmos-reason2-8b crash → must redeploy the full stack (known issue: NIM cannot restart alone).
