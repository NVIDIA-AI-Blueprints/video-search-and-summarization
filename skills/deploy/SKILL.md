---
name: deploy
description: Load when the user says "configure vss", "deploy vss", "deploy <profile>", "debug deploy", "verify deployment", or "why is my vss deploy broken".
version: "3.2.0"
license: "Apache License 2.0"
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

**Edge hardware routing** (DGX Spark, AGX/IGX Thor): see [`references/edge.md`](references/edge.md) for the 4B-LLM recipe (`config_edge.yml` + standalone vLLM on port 30081). Edge platforms share a single unified-memory GPU between LLM and VLM, so the Nemotron Edge 4B is the default and the Nemotron Nano 9B v2 FP8 is an option when memory allows.

**Each profile's reference owns its sizing table.** Don't pick a deployment shape from this file — open the profile reference and check minimum GPU count for the host's hardware against the (mode × platform) matrix there.


## How it works

```bash
# 1. Apply env overrides to the profile .env file
# 2. docker compose --env-file .env config > resolved.yml   (dry-run)
# 3. Review resolved.yml
# 4. docker compose -f resolved.yml up -d
```

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

### Step 0 — Tear down any existing deployment

If a deployment already exists, tear it down first. Full procedure (resolved.yml-driven path, container-name catch-all patterns covering dev-profile compose files, why leftovers cause `/sensor/list` 502s) lives in [`references/teardown.md`](references/teardown.md).

```bash
# If a resolved.yml from a prior deploy exists, prefer it — it
# knows about all compose-profile services that were brought up.
if [ -f "$REPO/deployments/resolved.yml" ]; then
  docker compose -f "$REPO/deployments/resolved.yml" down --remove-orphans
fi

# Catch-all: remove every VSS-stack container the dev-profile compose
# files bring up (covers leftovers from prior deploys that linger and
# bind ports the new deploy needs, or pass health checks while serving
# stale data).
docker ps -a --format '{{.Names}}' \
  | grep -E '^(vss-|mdx-|perception-|rtvi-|alert-|nvstreamer-|sensor-ms-|vst-ingress-|vst-mcp-|vst-file-proxy|centralizedb-|storage-ms-|streamprocessing-ms-|sdr-(http|streamprocessing)-|envoy-(http|streamprocessing)-|rtspserver-ms-|recorder-ms-|replaystream-ms-|livestream-ms-|metropolis-vss-ui|phoenix)' \
  | xargs -r docker rm -f
```

If this is the host's first deploy, the `docker compose down` line is a no-op (exit 0 with no containers to stop) — safe to run unconditionally.

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
| **`EXTERNAL_IP`** | The address browsers will use to reach the deploy. **Must be a real reachable hostname/IP for the user.** On a bare-metal host this can be `${HOST_IP}` or the host's DNS name. **On Brev, this is the secure-link domain** (e.g. `77770-<BREV_ENV_ID>.brevlab.com`) — see [Step 1c](#step-1c--if-deploying-on-brev-set-up-secure-link-env-vars). |
| **`HAPROXY_PORT`** | The browser-facing ingress port. Default `7777`. On Brev this stays `7777` internally; the secure link adds the `0` suffix externally. |

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

> **FORBIDDEN: `chown -R ubuntu:ubuntu $MDX_DATA_DIR` (or any recursive chown).**
>
> This is "good housekeeping" to a shell-admin instinct but is **the** deploy-breaking command in this stack. You will observe a "healthy" deploy (containers Up, endpoints 200) while the video pipeline is silently broken. Use `chmod -R 777` on the specific subdirs documented in `data-directory.md` — nothing else.

### Step 1c — If deploying on Brev, set up secure-link env vars

On a Brev-managed instance, VSS is accessed from the browser via a Cloudflare-fronted secure link that tunnels to an nginx proxy on port 7777. The proxy consolidates UI + Agent API + VST behind one origin (CORS-safe).

Source the helper **before** `docker compose up`:

```bash
source skills/deploy/scripts/brev_setup.sh
```

It detects `/etc/environment`'s `BREV_ENV_ID` and exports `PROXY_PORT=7777` and `BREV_LINK_PREFIX=77770` (launchable default; override with `BREV_LINK_PREFIX=7777` if the secure link was created manually without the `0` suffix). On non-Brev instances the script is a no-op.

> **Set `EXTERNAL_IP` to the Brev secure-link domain** in `dev-profile-<profile>/.env`. The profile `.env` derives `VSS_PUBLIC_HOST=${EXTERNAL_IP}` and feeds that to haproxy + the agent's external URLs (see [Step 1 callout](#step-1--gather-context)). For a launchable-created link on port 7777, that's `EXTERNAL_IP=${BREV_LINK_PREFIX}-${BREV_ENV_ID}.brevlab.com` (e.g. `77770-<id>.brevlab.com`). Leaving `EXTERNAL_IP=${HOST_IP}` makes report URLs and VST playback links unreachable from the browser even though haproxy is up — the most common Brev-deploy footgun.

See [`references/brev.md`](references/brev.md) for per-profile secure-link requirements, the launchable `0`-suffix quirk, and common CORS / 502 troubleshooting.

### Step 2 — Build env_overrides

Produce an `env_overrides` dict from the user request and the gathered context: choose remote/local LLM/VLM, set credentials, point at endpoints, set platform-specific flags. The full mapping (every override key, when it applies, defaults, profile-specific differences) lives in [`references/env-overrides.md`](references/env-overrides.md). Each profile reference has worked examples for that profile's common scenarios.

### Step 3 — Config / dry-run

**Env file location:** `<repo>/deployments/developer-workflow/dev-profile-<profile>/.env`

> **This is the authoritative `.env`.** Every verifier, healthcheck, and post-deploy tool reads from this path. When you apply env overrides (from Step 2 or from the user's prompt), write them **directly to this file** — not to `generated.env`.
>
> `generated.env` is a scratchpad that `dev-profile.sh` produces during its own internal flow; it is NOT read by the verifier and is wiped on the next invocation. The base `.env` is the source of truth.

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

### Step 3b — Verify resolved.yml has no unexpanded ${...} tokens

Unexpanded `${VAR}` tokens in `resolved.yml` mean compose did not see those env values. Diagnostic procedure and common culprits live in [`references/troubleshooting.md`](references/troubleshooting.md).

### Step 3c — Strip dangling optional `depends_on` from resolved.yml

`docker compose --env-file .env config` filters out services that don't match the active `COMPOSE_PROFILES`, but it leaves `depends_on:` entries that point at those filtered-out services. Compose's schema validator rejects any `depends_on` target that isn't a defined service in the file — even when the entry is `required: false`. The result: `docker compose -f resolved.yml up -d` aborts with

```
service "vst-ingress" depends on undefined service "sensor-ms-2d": invalid compose project
```

before any container starts.

> **Edit the generated `resolved.yml` only — never the source compose files.** The dependencies are correctly marked optional in the source; profile filtering is what creates the dangling references in the resolved artifact.

**Detect:**

```bash
python3 - <<'PY'
import yaml
with open("resolved.yml") as f:
    d = yaml.safe_load(f)
defined = set((d.get("services") or {}).keys())
dangling = []
for name, svc in (d.get("services") or {}).items():
    deps = (svc or {}).get("depends_on") or {}
    targets = deps.keys() if isinstance(deps, dict) else deps
    for t in targets:
        if t not in defined:
            dangling.append((name, t))
for n, t in dangling:
    print(f"{n} -> {t} (not defined)")
print(f"\n{len(dangling)} dangling depends_on entries")
PY
```

**Fix in place** (drops only the dangling entries; required active deps like `kafka`, `redis`, `rtvi-vlm`, `sensor-ms`, `streamprocessing-ms` are preserved):

```bash
python3 - <<'PY'
import yaml
with open("resolved.yml") as f:
    d = yaml.safe_load(f)
defined = set((d.get("services") or {}).keys())
for name, svc in (d.get("services") or {}).items():
    deps = (svc or {}).get("depends_on")
    if not deps:
        continue
    if isinstance(deps, dict):
        kept = {k: v for k, v in deps.items() if k in defined}
        if kept:
            svc["depends_on"] = kept
        else:
            svc.pop("depends_on", None)
    elif isinstance(deps, list):
        kept = [k for k in deps if k in defined]
        if kept:
            svc["depends_on"] = kept
        else:
            svc.pop("depends_on", None)
with open("resolved.yml", "w") as f:
    yaml.safe_dump(d, f, sort_keys=False)
print("resolved.yml normalized")
PY
```

**Re-validate** before `up -d`:

```bash
docker compose -f resolved.yml config --quiet && echo "resolved.yml OK"
```

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
cd $REPO/deployments
docker compose -f resolved.yml up -d
```

> **Do NOT use `--force-recreate` on retries.** It destroys already-warm NIM containers, forcing another 3–5 min torch.compile + CUDA-graph capture per NIM. If the previous `up -d` partially failed, fix the root cause (usually perms or an env typo) and just re-run `up -d` — Docker will re-create only the containers whose config changed or that are down.

Deploy takes ~10–20 min on first run (image pulls + model downloads). Monitor:

```bash
# Container status
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'

# Logs for a specific service
docker compose -f $REPO/deployments/resolved.yml logs --tail 50 <service>
```

Deploy is complete when all `mdx-*` containers show `Up` status.

### Step 6 — 
Fron


## Tear Down

```bash
cd $REPO/deployments
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

- `unknown or invalid runtime name: nvidia` → NVIDIA Container Toolkit not installed or Docker not restarted. See [`references/prerequisites.md`](references/prerequisites.md).
- NGC auth error → re-export `NGC_CLI_API_KEY` or follow [`references/ngc.md`](references/ngc.md).
- GPU not detected → run `sudo modprobe nvidia && sudo modprobe nvidia_uvm`, then retry.
- `docker compose up` fails with "no resolved.yml" → run the dry-run (`docker compose config > resolved.yml`, Step 3) first.
- cosmos-reason2-8b crash → must redeploy the full stack (known issue: NIM cannot restart alone).
