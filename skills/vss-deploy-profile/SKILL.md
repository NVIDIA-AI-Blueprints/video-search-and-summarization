---
name: vss-deploy-profile
description: Load when the user says "configure vss", "deploy vss", "deploy `profile`", "debug deploy", "verify deployment", or "why is my vss deploy broken".
license: Apache-2.0
metadata:
  version: "3.2.0"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint deployment"
---

# VSS Deploy

Deploy any VSS profile using a compose-centric workflow: build env overrides, generate resolved compose (dry-run), review, then deploy.

This SKILL.md covers the cross-profile concerns (**profile routing**, **prerequisites**, **NGC**, **GPU setup**, and the deploy/teardown flow). Profile-specific service lists, sizing, env recipes, endpoints, and debugging live in per-profile reference docs — load the one that matches the user's intent.

## Profile Routing

Match the user's request to a profile, then load that profile's reference for sizing, services, env recipes, and debugging.

| User says | Profile | Reference |
|---|---|---|
| "deploy vss" / "deploy base" | `base` | [`references/base.md`](references/base.md) |
| "deploy alerts" / "alert verification" / "real-time alerts" / "deploy for incident report" | `alerts` | [`references/alerts.md`](references/alerts.md) |
| "deploy lvs" / "video summarization" | `lvs` | [`references/lvs.md`](references/lvs.md) |
| "deploy search" / "video search" | `search` | [`references/search.md`](references/search.md) |
| "deploy warehouse" / "warehouse blueprint" / "vss warehouse" | `warehouse` | [`references/warehouse.md`](references/warehouse.md) |
| "debug warehouse" / "warehouse not working" / "warehouse FPS low" / "warehouse BEV out of sync" | `warehouse` (debug) | [`references/warehouse-debug.md`](references/warehouse-debug.md) |

**Edge hardware routing** (DGX Spark, AGX/IGX Thor): see [`references/edge.md`](references/edge.md) for the 4B-LLM recipe (`config_edge.yml` + standalone vLLM on port 30081). Edge platforms share a single unified-memory GPU between LLM and VLM, so the Nemotron Edge 4B is the default and the Nemotron Nano 9B v2 FP8 is an option when memory allows.

**Each profile's reference owns its sizing table.** Don't pick a deployment shape from this file — open the profile reference and check minimum GPU count for the host's hardware against the (mode × platform) matrix there.


## How it works

```bash
# 1. cp dev-profile-<profile>/.env dev-profile-<profile>/generated.env  (clean copy)
# 2. Apply env overrides to generated.env  (source .env stays untouched)
# 3. docker compose --env-file generated.env config > resolved.yml      (dry-run)
# 4. Review resolved.yml
# 5. docker compose -f resolved.yml up -d
```

The source `.env` is treated as **read-only defaults** committed to the repo. The skill's per-deploy working copy is `generated.env` — same pattern `dev-profile.sh` uses internally. This keeps the checked-in `.env` clean across iterations.

## Prerequisites

1. **Repo path** — find `video-search-and-summarization/` on disk. Check `TOOLS.md` if available.
2. **NGC CLI & API key** — see [`references/ngc.md`](references/ngc.md). Confirm `$NGC_CLI_API_KEY` is set.
3. **System prerequisites (GPU driver, Docker, NVIDIA Container Toolkit, kernel sysctls)** — full checks in [`references/prerequisites.md`](references/prerequisites.md). Canonical hardware/driver matrix is the [VSS prerequisites page](https://docs.nvidia.com/vss/3.2.0/prerequisites.html).

### Pre-flight check

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

## Model Selection

- `$LLM_REMOTE_URL` / `$VLM_REMOTE_URL` if the user asks for remote
- `$NGC_CLI_API_KEY` (local NIMs) or `$NVIDIA_API_KEY` (remote)

If no combination on this host satisfies the profile's sizing requirements, **stop and report the blocker** — don't silently pick another shape.

> **Edge shared mode requires Edge 4B + `HF_TOKEN`.** On DGX Spark and AGX/IGX Thor, both LLM and VLM must fit in unified memory, AND the standard `nvcr.io/nim/nvidia/nvidia-nemotron-nano-9b-v2:1` image has a broken arm64 manifest. Run `NVIDIA-Nemotron-Edge-4B-v2.1-EA-020126_FP8` as a standalone vLLM container on port 30081 with the agent pointed at it via `--use-remote-llm`. Full recipe and the mandatory `HF_TOKEN` verification step are in [`references/edge.md`](references/edge.md).

## Deployment Flow

Always follow this sequence. Never skip the dry-run.

### Step 0 — Tear down any existing deployment + clear data volumes

If a deployment already exists, tear it down AND clear stale data volumes before redeploying. 

Full procedure lives in [`references/teardown.md`](references/teardown.md).

### Step 1 — Gather context

Before building env overrides, confirm:

| Value | How to determine |
|---|---|
| **Profile** | Match user intent to the routing table above. Default: `base` |
| **Repo path** | Find `video-search-and-summarization/` on disk |
| **Hardware** | `nvidia-smi --query-gpu=name,memory.total --format=csv,noheader` |
| **LLM/VLM placement** | Cross-reference available GPUs against the chosen profile's **Minimum GPU count** table |
| **API keys** | `NGC_CLI_API_KEY` for local NIMs, `NVIDIA_API_KEY` for remote |
| **`HOST_IP`** | `hostname -I \| awk '{print $1}'` — the host's primary internal IP |
| **`EXTERNAL_IP`** | The address browsers will use to reach the deploy. **Must be a real reachable hostname/IP for the user.** On a bare-metal host this can be `${HOST_IP}` or the host's DNS name. **On Brev, this is the secure-link domain** (e.g. `7777-<BREV_ENV_ID>.brevlab.com`) — see [Step 1c](#step-1c--if-deploying-on-brev-set-up-secure-link-env-vars). |
| **`HAPROXY_PORT`** | The browser-facing ingress port. Default `7777`. On Brev this stays `7777` internally; the secure link prefixes it directly (e.g. `7777-<id>.brevlab.com`). Older launchables used to add a trailing `0` giving `77770-...`; that form is now legacy. |

> The haproxy ingress container (`services/infra/haproxy/compose.yml:46-47`) **also** reads `VSS_PUBLIC_HOST` and `VSS_PUBLIC_PORT` directly from the env to render its config templates and rewrite URLs.
>
> **Validation step the agent must run before `docker compose up`:**
>
> 1. Verify `EXTERNAL_IP` is set and reachable from the user's browser (not `localhost`, not `0.0.0.0`, not the host's internal-only IP if the deploy will be browsed remotely). confirm with the user if needed. assuming using brev secured link if deployed on brev.
> 2. Verify `HAPROXY_PORT` is set (default `7777`) and the chosen value isn't already bound on the host.
> 3. Confirm the resolved compose has `VSS_PUBLIC_HOST` and `VSS_PUBLIC_PORT` populated (no unexpanded `${...}` — see [Step 3b](#step-3b--verify-resolvedyml-has-no-unexpanded--tokens)).
> Forgetting this is a silent footgun: containers come up healthy, but VST playback / report links / the UI's API calls all 404 or hit Cloudflare-Access loops because the URLs embed an internal-only address.

### Step 1b — Prepare the data directory

Layout (asset paths, ownership, mount points, profile-specific subdirs) is documented in [`references/data-directory.md`](references/data-directory.md). Read that file before deploying for the first time on a host or when changing profiles.

> **FORBIDDEN: `chown -R ubuntu:ubuntu $VSS_DATA_DIR` (or any recursive chown).**
>
> This is "good housekeeping" to a shell-admin instinct but is **the** deploy-breaking command in this stack. You will observe a "healthy" deploy (containers Up, endpoints 200) while the video pipeline is silently broken. Use `chmod -R 777` on the specific subdirs documented in `data-directory.md` — nothing else.

### Step 1c — Initialize `generated.env`

The skill's per-deploy working copy. Always start from a fresh copy of the source `.env` — never mutate the source.

```bash
PROFILE=base
ENV_SRC=$REPO/deploy/docker/developer-profiles/dev-profile-$PROFILE/.env
ENV_GEN=$REPO/deploy/docker/developer-profiles/dev-profile-$PROFILE/generated.env

cp "$ENV_SRC" "$ENV_GEN"
```

All subsequent writes (Brev `EXTERNAL_IP`, the env_overrides dict from Step 2) go to `$ENV_GEN`. `$ENV_SRC` is read-only from here on.

### Step 1d — If deploying on Brev, set `EXTERNAL_IP` to the secure-link domain

On a Brev-managed instance, VSS is accessed from the browser via a Cloudflare-fronted secure link that tunnels to an nginx proxy on port 7777. The proxy consolidates UI + Agent API + VST behind one origin (CORS-safe).

Read `BREV_ENV_ID` from `/etc/environment` and write `EXTERNAL_IP` into `generated.env` (NOT `.env`):

```bash
brev_env_id=$(awk -F= '/^BREV_ENV_ID=/ {gsub(/"/, "", $2); print $2; exit}' /etc/environment)
sed -i "s|^EXTERNAL_IP=.*|EXTERNAL_IP=7777-${brev_env_id}.brevlab.com|" "$ENV_GEN"
```

The profile `.env` derives `VSS_PUBLIC_HOST=${EXTERNAL_IP}` and feeds that to haproxy + the agent's external URLs (see [Step 1 callout](#step-1--gather-context)). Leaving `EXTERNAL_IP=${HOST_IP}` makes report URLs and VST playback links unreachable from the browser even though haproxy is up — the most common Brev-deploy footgun.

See [`references/brev.md`](references/brev.md) for per-profile secure-link requirements and troubleshooting (manually-created links, CORS, 502s).

### Step 2 — Build env_overrides

Produce an `env_overrides` dict from the user request and the gathered context: choose remote/local LLM/VLM, set credentials, point at endpoints, set platform-specific flags. The full mapping (every override key, when it applies, defaults, profile-specific differences) lives in [`references/env-overrides.md`](references/env-overrides.md). Each profile reference has worked examples for that profile's common scenarios.

### Step 3 — Apply overrides + dry-run

**Working env file:** `<repo>/deploy/docker/developer-profiles/dev-profile-<profile>/generated.env` (created in Step 1c).

> **Two env files, distinct roles.**
> - `.env` — **read-only defaults**, checked in. Don't mutate it from the skill.
> - `generated.env` — **the skill's per-deploy working copy**. All overrides (the dict from Step 2, plus the Brev `EXTERNAL_IP` from Step 1d) land here. `--env-file` always points at this file. Post-deploy verifiers should also read from `generated.env` for the actually-deployed values — see [Debugging a Deployment](#debugging-a-deployment).
>
> `generated.env` matches the convention `dev-profile.sh` uses internally — it's a per-invocation scratchpad regenerated by `cp .env generated.env` each run.

```bash
# (Step 1c already ran: cp $ENV_SRC $ENV_GEN)

# Apply the env_overrides dict from Step 2 to generated.env
# (read lines, update matching keys, append new keys, write)
# Example:
#   sed -i "s|^LLM_MODE=.*|LLM_MODE=remote|" "$ENV_GEN"
#   sed -i "s|^LLM_BASE_URL=.*|LLM_BASE_URL=http://localhost:30081|" "$ENV_GEN"

# Resolve compose
cd $REPO/deploy/docker
docker compose --env-file $ENV_GEN config > resolved.yml
```

The resolved YAML is saved to `<repo>/deploy/docker/resolved.yml`.

### Step 3b — Verify resolved.yml has no unexpanded ${...} tokens

Unexpanded `${VAR}` tokens in `resolved.yml` mean compose did not see those env values. Diagnostic procedure and common culprits live in [`references/troubleshooting.md`](references/troubleshooting.md).

### Step 3c — Strip dangling optional `depends_on` from resolved.yml

**MUST run after Step 3, before Step 5.** Skipping this aborts the deploy:

Normalize - drop optional dependencies for services filtered out from resolved.yml

```bash
# From the repo root
uv run skills/vss-deploy-profile/scripts/normalize_resolved_yml.py "$REPO/deploy/docker/resolved.yml"
```
If `uv` isn't on the host, install it once with `curl -LsSf https://astral.sh/uv/install.sh | sh` (no root needed).
**Re-validate** before `up -d`:

```bash
docker compose -f "$REPO/deploy/docker/resolved.yml" config --quiet && echo "resolved.yml OK"
```

If validation still fails after the normalizer runs, capture the error and inspect — that's a different bug (a dependency that's not optional, or another schema violation), not the dangling-depends_on case.

### Step 4 — Review

Show the user a summary of what will be deployed:

- Profile name and hardware
- LLM/VLM models and mode (local/remote/local_shared)
- Services that will start
- GPU device assignment
- Key endpoints (UI port, agent port)

Ask: **"Looks good — deploy now?"** and wait for confirmation before Step 5.

**Exception — autonomous mode.** If the user's request already asks you to run autonomously (e.g. "deploy X autonomously", "run without confirmation", "non-interactive"), skip the confirmation prompt and proceed straight to Step 5. This path exists so automated eval / CI invocations don't hang waiting for a human reply they'll never get. In all other cases, a human must approve.

### Step 5 — Deploy

```bash
cd $REPO/deploy/docker
docker compose -f resolved.yml up -d
```

> **Do NOT use `--force-recreate` on retries.** It destroys already-warm NIM containers, forcing another 3–5 min torch.compile + CUDA-graph capture per NIM. If the previous `up -d` partially failed, fix the root cause (usually perms or an env typo) and just re-run `up -d` — Docker will re-create only the containers whose config changed or that are down.

`docker compose up -d` returns as soon as the daemon has **created** the containers — it does **not** wait for the processes inside to finish initializing. Polling `docker ps | grep -qx <name>` immediately after returns 0 (container exists) while `curl :8000/docs` returns exit 7 (Python process inside is still importing modules, loading models, binding the port). Eval verifiers and humans both regularly trip on this — declaring "deploy done" right after `up -d` returns probes a half-warm stack, and `vss-agent` / `:8000/docs` / `vss-agent-ui` checks all spuriously fail before the agent has actually bound its ports.

### Step 5b — Wait until the stack is actually healthy

Do **not** declare the deploy done after `up -d` returns. Cold deploys (first-time NIM image pulls + model warmup) can legitimately take 10–20 min, so the timeouts in the probes below are generous on purpose.

First, wait for the compose project to settle. Every container must be either `running` or cleanly `exited 0` — one-shot init jobs (e.g. `vss-kibana-init`) legitimately exit 0 and stay exited, which is fine. Anything `restarting`, `unhealthy`, or `exited <N≠0>` is a deploy failure even though `up -d` returned 0.

```bash
# docker compose 2.21+ emits NDJSON (one bare object per line) from
# `ps --format json`, not a JSON array — so no `.[]` here; jq's default
# input loop already iterates each line. The filter accepts only
# `running` and `exited 0`; everything else (restarting, unhealthy,
# exited with non-zero code) is a failure.
docker compose -f resolved.yml ps --format json \
  | jq -r 'select((.State == "running" or (.State == "exited" and .ExitCode == 0)) | not)
           | "\(.Name)\t\(.State)\texit=\(.ExitCode // "?")\t\(.Status)"' \
  | { mapfile -t bad; if [ "${#bad[@]}" -gt 0 ]; then
        printf 'FAIL: %s\n' "${bad[@]}" >&2; exit 1;
      fi; }
```

Container state alone isn't enough — the processes inside may still be importing modules, loading models, and binding ports. Probe the profile's documented readiness endpoints next.

**Each `references/<profile>.md` lists the endpoints that must be reachable** for that profile (agent REST API, UI, inference NIMs, etc., on the ports the profile actually opens). Run those `curl` checks with a generous deadline (15 min is reasonable for cold NIM warmup) and only declare the deploy done once every documented endpoint returns the expected success exit code.

If any probe times out, dump `docker compose ps` + `docker compose logs --tail 100 <slow-service>` and report the slow container — don't claim success on a half-warm stack.

### Step 6 — 
Fron


## Tear Down

```bash
cd $REPO/deploy/docker
docker compose -f resolved.yml down
```

For switching profiles or recovering from a partial deploy, follow the full procedure in [`references/teardown.md`](references/teardown.md).

## Debugging a Deployment

Use this workflow when the user asks to "debug the deploy", "verify it's working", "why is the agent not responding", or similar. The goal is to confirm the full video-ingestion-to-agent-answer path, not just that containers are "Up".

Each profile reference has a **Debugging** section listing the exact commands and failure-mode table for that profile.

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

After the quick checks above pass, drive a real query through the agent — e.g. ask it over the REST API or UI to describe a video you've uploaded to VST. If the agent returns a non-empty answer, the upload → ingest → inference → reply path is healthy. If it fails, `docker logs vss-agent` shows which stage tripped.

## Troubleshooting

Start with [`references/agent-failure-modes.md`](references/agent-failure-modes.md) for cross-profile failures such as NIM cold-start timeouts, OOM, remote endpoint 5xx responses, missing `NGC_CLI_API_KEY` / `HF_TOKEN`, unexpanded values in `resolved.yml` etc.