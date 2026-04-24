---
name: deploy
description: Deploy, debug, or tear down any VSS profile using a compose-centric workflow — config (dry-run) with env overrides, review resolved compose, then compose up. Use this skill when the user says "deploy vss", "deploy <profile>", "debug deploy", "verify deployment", or "why is my vss deploy broken".
version: "3.1.0"
license: "Apache License 2.0"
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

**Edge hardware routing** (DGX Spark, AGX/IGX Thor): see [`references/edge.md`](references/edge.md)
for the 4B-LLM recipe (`config_edge.yml` + standalone vLLM on port 30081). Edge
platforms share a single unified-memory GPU between LLM and VLM, so the
Nemotron Edge 4B is the default and the Nemotron Nano 9B v2 FP8 is an option
when memory allows.

## When to Use

- Deploy VSS / start VSS / bring up a profile
- Deploy a specific profile (base, alerts, lvs, search)
- Do a dry-run / preview what will be deployed
- Change deployment config (hardware, LLM mode, GPU assignment)
- Tear down a running deployment
- **Debug or verify** an existing deployment (see [Debugging a Deployment](#debugging-a-deployment))

## How it works

Run docker compose commands directly on the host:

```bash
# 1. Apply env overrides to the profile .env file
# 2. docker compose --env-file .env config > resolved.yml   (dry-run)
# 3. Review resolved.yml
# 4. docker compose -f resolved.yml up -d
```

## Before Deploying

1. **Repo path** — find `video-search-and-summarization/` on disk. Check `TOOLS.md` if available.
2. **NGC CLI & API key** — see [`references/ngc.md`](references/ngc.md). Check `$NGC_CLI_API_KEY` is set.
3. **System prerequisites (GPU VRAM, driver, Docker, NVIDIA Container Toolkit)** — canonical reference is the [**VSS prerequisites page**](https://docs.nvidia.com/vss/3.1.0/prerequisites.html). That page lists supported hardware, per-profile GPU requirements, and the minimum driver/CUDA version per NIM. Read it and pick the LLM/VLM placement that fits the host — don't guess thresholds from this skill.

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

### Step 0 — Tear down any existing deployment

Before every deploy, **always** stop any prior VSS stack. This is
mandatory even if you think the host is clean, and especially when
switching profiles (`base` → `search`, `alerts` verification →
`alerts` real-time, etc.). Compose profile flags only *start* the
services listed under the selected profile — they do NOT stop
services from a previously-active profile, so containers from the
prior deploy linger and pass unrelated container-name checks,
contaminate results, and can bind ports the new deploy needs.

```bash
# If a resolved.yml from a prior deploy exists, prefer it — it
# knows about all compose-profile services that were brought up.
if [ -f "$REPO/deployments/resolved.yml" ]; then
  docker compose -f "$REPO/deployments/resolved.yml" down --remove-orphans
fi

# Catch-all: remove any VSS-stack containers that survived
# (leftovers from a crashed prior run or manual docker runs).
docker ps -a --format '{{.Names}}' \
  | grep -E '^(vss-|mdx-|perception-|rtvi-|alert-|nvstreamer-)' \
  | xargs -r docker rm -f
```

If this is the host's first deploy, the `docker compose down`
line is a no-op (exit 0 with no containers to stop) — safe to run
unconditionally.

### Step 1 — Gather context

Discover what's available on the host and cross-reference with the
[VSS prerequisites page](https://docs.nvidia.com/vss/3.1.0/prerequisites.html)
to choose a deployment shape that fits.

| Value | How to determine |
|---|---|
| **Profile** | Match user intent to routing table above. Default: `base` |
| **Repo path** | Find `video-search-and-summarization/` on disk |
| **Hardware** | `nvidia-smi --query-gpu=name,memory.total --format=csv,noheader` → look up per-GPU VRAM against the prerequisites page |
| **LLM/VLM placement** | Pick `local_shared`, `local`, or `remote` per LLM/VLM based on available GPUs + `$LLM_REMOTE_URL` / `$VLM_REMOTE_URL` / `$NGC_CLI_API_KEY`. If no combination on this host satisfies the prerequisites, stop and report the blocker instead of silently picking another shape. |
| **API keys** | `NGC_CLI_API_KEY` for local NIMs, `NVIDIA_API_KEY` for remote |
| **Host IP** | `hostname -I \| awk '{print $1}'` |

**Hardware profile mapping:**

| GPU name contains | HARDWARE_PROFILE | Recommended LLM path |
|---|---|---|
| H100 | `H100` | Nano 9B v2 (NIM) |
| L40S | `L40S` | Nano 9B v2 (NIM) |
| RTX 6000 Ada, RTX PRO 6000 | `RTXPRO6000BW` | Nano 9B v2 (NIM) |
| GB10 (DGX Spark) | `DGX-SPARK` | **Edge 4B** (vLLM) — see [`references/edge.md`](references/edge.md) |
| IGX | `IGX-THOR` | **Edge 4B** (vLLM) — see [`references/edge.md`](references/edge.md) |
| AGX | `AGX-THOR` | **Edge 4B** (vLLM) — see [`references/edge.md`](references/edge.md) |
| Other | `OTHER` | — |

**Minimum GPU count per (profile × mode × platform).** Canonical source
is the [VSS prerequisites page](https://docs.nvidia.com/vss/3.1.0/prerequisites.html);
reproduced here so the skill can fail fast when the host is too small:

| Profile | Mode | H100 / RTX PRO 6000 (Blackwell) | L40S | DGX-Spark / IGX-Thor / AGX-Thor |
|---|---|---|---|---|
| `base` | shared (`local_shared` LLM + VLM) | **1** | — (48 GB/GPU too small) | **1** (Edge 4B + VLM, unified memory) |
| `base` | dedicated (`local` LLM + VLM) | **2** | **2** | — |
| `base` | `remote-llm` | **1** (VLM local) | **1** (VLM local) | **1** (remote LLM only) |
| `base` | `remote-vlm` | **1** (LLM local) | **1** (LLM local) | — |
| `base` | `remote-all` | **0** | **0** | **0** |
| `lvs` | shared | **1** | — | - |
| `lvs` | dedicated | **2** | **2** | — |
| `lvs` | `remote-llm/vlm` | 1 | 1 | - |
| `lvs` | `remote-all` | 0 | 0 | - |
| `alerts` (verification / CV) | shared | **2**  | — | — |
| `alerts` (verification / CV) | dedicated | **3** | **3**  | — |
| `alerts` (verification / CV) | `remote-all` | 1 | 1 | 1 |
| `alerts` (verification / CV) | `remote-llm/vlm` | 2 | 2 | 1 |
| `alerts` (real-time / VLM) | shared | **2** | — | — |
| `alerts` (real-time / VLM) | dedicated | **3** | **3**  | — |
| `alerts` (real-time / VLM) | `remote-llm` | 2 | 2 | 1 |
| `search` | shared | **2** | — | - |
| `search` | dedicated | **3** | **3**  | — |
| `search` | `remote-*` | **2**  | **2** | - |

A few hard rules encoded in the table:

- **L40S can't do `shared`.** 48 GB is not enough VRAM for LLM + VLM
  on a single GPU. Fall back to `dedicated` or a `remote-*` mode.
- **L40S needs +1 GPU for alerts / search vs H100** because the
  shared-on-one-GPU trick doesn't work — RT-CV / Embed1 must take
  their own GPU, and LLM+VLM still need a second.
- **DGX-Spark / Thor are early-access for most profiles.** Only
  `base` + `lvs` are expected to fully land locally; `alerts` /
  `search` currently require a remote LLM. See
  [`references/edge.md`](references/edge.md).

If the host's (GPU count × VRAM) combination doesn't appear above,
**stop and report the blocker** — don't silently pick a different
mode.

> **Edge shared mode requires Edge 4B + `HF_TOKEN`.** On DGX Spark and AGX/IGX
> Thor, both LLM and VLM must fit in unified memory, AND the standard
> `nvcr.io/nim/nvidia/nvidia-nemotron-nano-9b-v2:1` image has a broken arm64
> manifest. You must run `NVIDIA-Nemotron-Edge-4B-v2.1-EA-020126_FP8` as a
> standalone vLLM container on port 30081 with the agent pointed at it via
> `--use-remote-llm`. Full recipe and the mandatory `HF_TOKEN` verification
> step are in [`references/edge.md`](references/edge.md).

### Step 1b — Prepare the data directory

**This is the #1 source of silent-deploy bugs. Follow it exactly.**

The stack mounts several subdirs of `$MDX_DATA_DIR` into containers that each
run as a different uid. Docker auto-creates empty bind-mount paths as
`root:root`, which is read-only for the container processes. The reference
`scripts/dev-profile.sh` uses `chmod -R 777` on the relevant subdirs — it
does **not** `chown`.

Run this verbatim before `docker compose up`:

```bash
DATA=$MDX_DATA_DIR      # e.g. <repo>/data
mkdir -p \
  "$DATA/data_log/analytics_cache" \
  "$DATA/data_log/calibration_toolkit" \
  "$DATA/data_log/elastic/data" \
  "$DATA/data_log/elastic/logs" \
  "$DATA/data_log/kafka" \
  "$DATA/data_log/redis/data" \
  "$DATA/data_log/redis/log" \
  "$DATA/agent_eval/dataset" \
  "$DATA/agent_eval/results"
# Profile-specific subdirs:
#   alerts → mkdir -p "$DATA/data_log/vss_video_analytics_api" "$DATA/videos/dev-profile-alerts" "$DATA/models/rtdetr-its" "$DATA/models/gdino"
#   search → mkdir -p "$DATA/models"
chmod -R 777 "$DATA/data_log" "$DATA/agent_eval"
# If you created $DATA/models above, also: chmod -R 777 "$DATA/models"
```

> **FORBIDDEN: `chown -R ubuntu:ubuntu $MDX_DATA_DIR` (or any recursive chown).**
>
> This is "good housekeeping" to a shell-admin instinct but is **the** deploy-
> breaking command in this stack. You will observe a "healthy" deploy
> (containers Up, endpoints 200) while the video pipeline is silently broken.
> Use `chmod -R 777` on the specific subdirs above — nothing else.

**Known per-container uid gotchas** (each uses a bind mount under `$DATA`):

| Container | Image | Runs as | Mount path | Symptom if permissions wrong |
|---|---|---|---|---|
| `centralizedb-dev` | postgres:17.6-alpine | uid **70** | `$DATA/data_log/vst/postgres/db` | Can't read own PGDATA → VST `sensor_details` query fails → uploaded videos never appear in `/vst/api/v1/sensor/streams` → warehouse E2E check returns empty |
| `mdx-redis` | redis:8.2.2-alpine | uid **999** | `$DATA/data_log/redis/log`, `/redis/data` | "Can't open the log file: Permission denied" → redis dies → `envoy-streamprocessing` dies (needs Redis Lua script) → stream pipeline broken |
| `elasticsearch` | elasticsearch | uid **1000** | `$DATA/data_log/elastic/{data,logs}` | "AccessDeniedException" on startup → ES refuses to start |
| `vst` / `sensor-ms-dev` | vst | uid **1000** | `$DATA/data_log/vst/*` (videos, clips) | 403 on ingest or stream write |

`chmod -R 777 $DATA/data_log` covers all of these. Do NOT chown them to
individual uids — containers that init their own dirs on first start (like
postgres) will then re-chown to their uid and a later chown back to ubuntu
breaks them.

**If postgres is already broken** (common when redeploying without a clean
`data-dir`):
```bash
sudo rm -rf "$DATA/data_log/vst/postgres"  # postgres re-initializes on next start
docker restart centralizedb-dev
```

### Step 1c — If deploying on Brev, set up secure-link env vars

On a Brev-managed instance, VSS is accessed from the browser via a
Cloudflare-fronted secure link that tunnels to an nginx proxy on port 7777.
The proxy consolidates UI + Agent API + VST behind one origin (CORS-safe).

Source the helper **before** `docker compose up`:

```bash
source skills/deploy/scripts/brev_setup.sh
```

It detects `/etc/environment`'s `BREV_ENV_ID` and exports `PROXY_PORT=7777`
and `BREV_LINK_PREFIX=77770` (launchable default; override with
`BREV_LINK_PREFIX=7777` if the secure link was created manually without
the `0` suffix). On non-Brev instances the script is a no-op.

See [`references/brev.md`](references/brev.md) for:
- Per-profile secure-link requirements (base needs 1, lvs/search/alerts need 2–3)
- The launchable `0`-suffix quirk
- Common CORS / 502 troubleshooting

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
| Remote LLM | `LLM_MODE=remote`, `LLM_BASE_URL=<host>` (no `/v1`), `LLM_NAME=<model>`, `NVIDIA_API_KEY=<key>` |
| Remote VLM | `VLM_MODE=remote`, `VLM_BASE_URL=<host>` (no `/v1`), `VLM_NAME=<model>`, `NVIDIA_API_KEY=<key>` |
| **Remote LLM AND remote VLM** (aka `remote-all`) | **BOTH of the above** — you must set `LLM_MODE=remote`, `VLM_MODE=remote`, `LLM_BASE_URL`, `VLM_BASE_URL`, `LLM_NAME`, `VLM_NAME`. The presence of a remote VLM endpoint does not imply `VLM_MODE=remote` — you have to write it explicitly. |
| NVIDIA API for remote inference | `LLM_BASE_URL=https://integrate.api.nvidia.com` |
| Dedicated GPUs | `LLM_MODE=local`, `VLM_MODE=local`, `LLM_DEVICE_ID=0`, `VLM_DEVICE_ID=1` |
| Different LLM model | `LLM_NAME=<name>`, `LLM_NAME_SLUG=<slug>` |
| Different VLM model | `VLM_NAME=<name>`, `VLM_NAME_SLUG=<slug>` |

**Extracting remote endpoints from user intent.**

If the user says "remote LLM" or mentions an LLM endpoint URL, you MUST do
all of the following before `docker compose up`:

1. Identify the endpoint URL and model name. If the user gave them in
   their prompt (e.g. *"deploy with remote LLM at
   `http://launchpad:11571` serving `nvidia/nvidia-nemotron-nano-9b-v2`"*),
   use those values directly. Strip any trailing `/v1` (see callout below).
2. If the user said "remote" without providing a URL or model, **stop and
   ask the user** for:
   - The LLM endpoint URL (without `/v1`)
   - The LLM model name served there
   - (same pair for VLM if they also said "remote VLM")
   - An `NVIDIA_API_KEY` if the endpoint requires one
3. Write `LLM_MODE=remote` + `LLM_BASE_URL=<url>` + `LLM_NAME=<model>` into
   `deployments/developer-workflow/dev-profile-<profile>/.env`. Do the
   same pair for VLM if the user said remote VLM. Use `sed -i
   "s|^KEY=.*|KEY=VALUE|"` — the `.env` template ships with placeholder
   rows for these keys.
4. After writing, `grep -E '^(HARDWARE_PROFILE|LLM_MODE|VLM_MODE|LLM_BASE_URL|VLM_BASE_URL)=' <env-file>`
   and verify every line shows the value you intended. A silent miss on
   `LLM_MODE` / `VLM_MODE` is the #1 cause of deployments coming up with
   wrong compose profiles.

Never leave `LLM_MODE` or `VLM_MODE` unset when the user said "remote".
The `.env` template's placeholder value is `LLM_MODE=local` — failing to
overwrite it means you silently deployed in local mode with remote URLs
dangling, which no `COMPOSE_PROFILES` path correctly supports.

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

> **This is the authoritative `.env`.** Every verifier, healthcheck, and
> post-deploy tool reads from this path. When you apply env overrides
> (from Step 2 or from the user's prompt), write them **directly to this
> file** — not to `generated.env`.
>
> `generated.env` is a scratchpad that `dev-profile.sh` produces during
> its own internal flow; it is NOT read by the verifier and is wiped on
> the next invocation. An agent that uses `dev-profile.sh` as a one-shot
> deploy but leaves the base `.env` untouched will silently fail env
> checks even when the stack comes up cleanly. If you used
> `dev-profile.sh` and see `generated.env` on disk, copy its key/value
> lines back into the base `.env`, or re-apply your `sed` commands
> against the base `.env` after the fact. The base `.env` is the source
> of truth.

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

Ask: **"Looks good — deploy now?"** and wait for confirmation before Step 5.

**Exception — autonomous mode.** If the user's request already asks
you to run autonomously (e.g. "deploy X autonomously", "run without
confirmation", "non-interactive"), skip the confirmation prompt and
proceed straight to Step 5. This path exists so automated eval /
CI invocations don't hang waiting for a human reply they'll never
get. In all other cases, a human must approve.

### Step 5 — Deploy

```bash
cd $REPO/deployments
docker compose -f resolved.yml up -d
```

> **Do NOT use `--force-recreate` on retries.** It destroys already-warm
> NIM containers, forcing another 3–5 min torch.compile + CUDA-graph capture
> per NIM. If the previous `up -d` partially failed, fix the root cause
> (usually perms or an env typo) and just re-run `up -d` — Docker will
> re-create only the containers whose config changed or that are down.

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
- **vios** → camera/stream management via VIOS
- **video-analytics** → Elasticsearch queries

## Tear Down

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

After the quick checks above pass, drive a real query through the agent — e.g.
ask it over the REST API or UI to describe a video you've uploaded to VST.
If the agent returns a non-empty answer, the upload → ingest → inference →
reply path is healthy. If it fails, `docker logs vss-agent` shows which stage
tripped.

## Troubleshooting

- `unknown or invalid runtime name: nvidia` → NVIDIA Container Toolkit not installed or Docker not restarted. See [`references/prerequisites.md`](references/prerequisites.md).
- NGC auth error → re-export `NGC_CLI_API_KEY` or follow [`references/ngc.md`](references/ngc.md).
- GPU not detected → run `sudo modprobe nvidia && sudo modprobe nvidia_uvm`, then retry.
- `docker compose up` fails with "no resolved.yml" → run the dry-run (`docker compose config > resolved.yml`, Step 3) first.
- cosmos-reason2-8b crash → must redeploy the full stack (known issue: NIM cannot restart alone).
