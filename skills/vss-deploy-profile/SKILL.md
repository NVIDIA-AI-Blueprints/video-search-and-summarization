---
name: vss-deploy-profile
description: Use to select, configure, deploy, verify, debug, or tear down a VSS profile (base, search, lvs, warehouse, edge). Not for standalone microservices — use the vss-deploy-* skill.
license: Apache-2.0
metadata:
  version: "3.2.0"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint deployment"
---
# VSS Deploy

Deploy any VSS profile (`base`, `search`, `lvs`, `warehouse`, `alerts`, `edge`) using a compose-centric workflow: build env overrides, generate resolved compose (dry-run), review, then deploy. This SKILL.md covers the cross-profile concerns (**profile routing**, **prerequisites**, **NGC**, **GPU setup**, and the deploy/teardown flow). Profile-specific service lists, sizing, env recipes, endpoints, and debugging live in per-profile reference docs — load the one that matches the user's intent.

Helper script: `run_script("scripts/normalize_resolved_yml.py", "<resolved.yml>")` normalizes a `docker compose config` dry-run dump for diff-friendly review during Step 3c. All other deployment work goes through `compose` / `dev-profile.sh`.

## Profile Routing

Match the user's request to a profile, then load that profile's reference for sizing, services, env recipes, and debugging.

| User says | Profile | Reference |
|---|---|---|
| "deploy vss" / "deploy base" | `base` | [`references/base.md`](references/base.md) |
| "deploy alerts" / "alert verification" / "real-time alerts" / "deploy for incident report" | `alerts` | [`references/alerts.md`](references/alerts.md) |
| "deploy lvs" / "video summarization" | `lvs` | [`references/lvs-profile.md`](references/lvs-profile.md) |
| "deploy search" / "video search" | `search` | [`references/search.md`](references/search.md) |
| "deploy warehouse" / "warehouse blueprint" / "vss warehouse" | `warehouse` | [`references/warehouse.md`](references/warehouse.md) |
| "debug warehouse" / "warehouse not working" / "warehouse FPS low" / "warehouse BEV out of sync" | `warehouse` (debug) | [`references/warehouse-debug.md`](references/warehouse-debug.md) |

**Edge hardware routing** (DGX Spark, AGX/IGX Thor): see [`references/edge.md`](references/edge.md). DGX Spark uses the DGX Spark-only `NVIDIA-Nemotron-Nano-9B-v2-DGX-Spark` NIM as a standalone local LLM on port `30081` until the image is wired into compose. AGX/IGX Thor still uses the Edge 4B standalone vLLM fallback because this skill does not have a verified Thor Nano 9B NIM path.

**Each profile's reference owns its sizing table.** Don't pick a deployment shape from this file — open the profile reference and check minimum GPU count for the host's hardware against the (mode × platform) matrix there.


## How it works

```bash
# 1. cp dev-profile-<profile>/.env dev-profile-<profile>/generated.env  (clean copy)
# 2. Apply env overrides to generated.env  (source .env stays untouched)
# 3. docker compose --env-file generated.env config > resolved.yml      (dry-run)
# 4. Review resolved.yml
# 5. docker compose --env-file generated.env -f resolved.yml up -d
```

The source `.env` is treated as **read-only defaults** committed to the repo. The skill's per-deploy working copy is `generated.env` — same pattern `dev-profile.sh` uses internally. This keeps the checked-in `.env` clean across iterations.

## Prerequisites

1. **Repo path** — find `video-search-and-summarization/` on disk. Check `TOOLS.md` if available.
2. **NGC CLI & API key** — see [`references/ngc.md`](references/ngc.md). Confirm `$NGC_CLI_API_KEY` is set.
3. **System prerequisites (GPU driver, Docker, NVIDIA Container Toolkit, kernel sysctls)** — full checks in [`references/prerequisites.md`](references/prerequisites.md). Canonical hardware/driver matrix is the [VSS prerequisites page](https://docs.nvidia.com/vss/3.2.0/prerequisites.html).

### Pre-flight check

Run before every deploy. The full system checklist and remediation steps live
in [`references/prerequisites.md`](references/prerequisites.md#preflight).
For DGX Spark / IGX Thor / AGX Thor, also run the cache-cleaner check in
[`references/edge.md`](references/edge.md#cache-cleaner-every-edge-deploy).

**Detect sudo mode first.** Several pre-flight remediations and the
edge cache-cleaner installer call `sudo`. If the host requires a
sudo password, those steps will silently no-op under `sudo -n` and
leave the deploy in a half-prepared state.

```bash
if sudo -n true 2>/dev/null; then
  echo "passwordless sudo — pre-flight will auto-install missing pieces"
else
  echo "sudo requires password — pre-flight will NOT auto-install; hand commands to the user"
fi
```

When sudo needs a password, the skill **must not** run privileged
installers itself. Surface the copy-pasteable command block from
`references/prerequisites.md` to the user with a *"run this once and
confirm"* handoff, then resume after the user replies.

Minimum smoke test (must succeed):

```bash
nvidia-smi --query-gpu=index,name --format=csv,noheader
docker info 2>/dev/null | grep -qi runtimes \
  && docker run --rm --gpus all ubuntu:22.04 nvidia-smi >/dev/null 2>&1 \
  && echo "nvidia runtime OK"
```

If the smoke test fails, do not proceed; open
[`references/prerequisites.md`](references/prerequisites.md#preflight)
for the remediation tree.

## Model Selection

- `$LLM_REMOTE_URL` / `$VLM_REMOTE_URL` if the user asks for remote
- `$NGC_CLI_API_KEY` (local NIMs) or `$NVIDIA_API_KEY` (remote)

If no combination on this host satisfies the profile's sizing requirements, **stop and report the blocker** — don't silently pick another shape.

> **Edge shared mode is platform-specific.** On DGX Spark, run `nvcr.io/nim/nvidia/nvidia-nemotron-nano-9b-v2-dgx-spark:1.0.0-variant` as a standalone local NIM on port `30081` and point the agent at it with `LLM_MODE=remote`. On AGX/IGX Thor, keep using the Edge 4B standalone vLLM fallback with `HF_TOKEN`. Full recipes are in [`references/edge.md`](references/edge.md).

## Deployment Flow

Always follow this sequence. Never skip the dry-run.

### Step 0 — Tear down any existing deployment + clear data volumes

If a deployment already exists, tear it down AND clear stale data volumes before redeploying. 

Full procedure lives in [`references/teardown.md`](references/teardown.md).

### Step 0a — Credentials gate (run before any env mutation)

Validate every credential the chosen profile needs **before** Step 1c
copies `.env` to `generated.env`. A 401 here is a 30-second failure;
the same 401 inside a NIM cold-start is a 10–20 min failure.

Required by mode: `NGC_CLI_API_KEY` for any local NIM (`LLM_MODE`/`VLM_MODE` ∈ `local`,`local_shared`); `NVIDIA_API_KEY` for any remote NIM; add `HF_TOKEN` on edge (DGX Spark / IGX-Thor / AGX-Thor with Edge 4B — gated model).

**Discovery — surface, do not auto-source.** If `$NGC_CLI_API_KEY` is
unset but `~/.ngc/config` exists, extract `apikey`/`org`/`team` and
ask the user *"Use NGC account `${org}/${team}` for the deploy?"*
before exporting. Same pattern for `$HF_TOKEN` via
`~/.cache/huggingface/token`.

**Probes (fail fast on 401/403).** Each probe runs only when its key is
set; an unset key prints `skip` (it may simply not be required for the
chosen mode — see the gate below).

```bash
# NGC — local NIM image pulls
if [ -n "$NGC_CLI_API_KEY" ]; then
  curl -sf -u "\$oauthtoken:$NGC_CLI_API_KEY" \
    "https://authn.nvidia.com/token?service=ngc" >/dev/null \
    && echo "NGC_CLI_API_KEY ok" || echo "NGC_CLI_API_KEY invalid (401/403)"
else
  echo "NGC_CLI_API_KEY not set — skip (required for any local NIM)"
fi

# build.nvidia.com — remote NIM endpoints
if [ -n "$NVIDIA_API_KEY" ]; then
  curl -sf -H "Authorization: Bearer $NVIDIA_API_KEY" \
    "https://integrate.api.nvidia.com/v1/models" >/dev/null \
    && echo "NVIDIA_API_KEY ok" || echo "NVIDIA_API_KEY invalid (401/403)"
else
  echo "NVIDIA_API_KEY not set — skip (required only for remote NIM)"
fi

# HF — edge only (gated Edge 4B)
if [ -n "$HF_TOKEN" ]; then
  status=$(curl -sf -o /dev/null -w '%{http_code}' \
    -H "Authorization: Bearer $HF_TOKEN" \
    "https://huggingface.co/api/models/nvidia/NVIDIA-Nemotron-Edge-4B-v2.1-EA-020126_FP8")
  [ "$status" = "200" ] \
    && echo "HF_TOKEN ok" \
    || echo "HF_TOKEN invalid or no access to gated Edge 4B (HTTP $status)"
else
  echo "HF_TOKEN not set — skip (required only on edge with Edge 4B)"
fi
```

Map the results against what the chosen mode needs (Required-by-mode
list above): a key reported `invalid` that the mode needs, or a `skip`
for a key the mode **requires**, is a blocker — prompt the user, re-probe,
and do **not** proceed to Step 1 until it resolves. A `skip` for a key the
mode does not use is fine.

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
docker compose --env-file $ENV_GEN -f resolved.yml up -d
```

> **`--env-file` is mandatory.** `resolved.yml` resolves variable
> substitutions at config time but the per-service `profiles:` keys
> are still active filters at `up` time. Without `--env-file`,
> `COMPOSE_PROFILES` is unset, every service gets filtered out, and
> `up -d` exits **0** with `no service selected` and an empty
> container set — a silent zero-container "success". Always pass the
> same `generated.env` here that was used in Step 3.

> **Do NOT use `--force-recreate` on retries.** It destroys already-warm NIM containers, forcing another 3–5 min torch.compile + CUDA-graph capture per NIM. If the previous `up -d` partially failed, fix the root cause (usually perms or an env typo) and just re-run `up -d` — Docker will re-create only the containers whose config changed or that are down.

`docker compose up -d` returns as soon as the daemon has **created** the containers — it does **not** wait for the processes inside to finish initializing. Polling `docker ps | grep -qx <name>` immediately after returns 0 (container exists) while `curl :8000/docs` returns exit 7 (Python process inside is still importing modules, loading models, binding the port). Eval verifiers and humans both regularly trip on this — declaring "deploy done" right after `up -d` returns probes a half-warm stack, and `vss-agent` / `:8000/docs` / `vss-agent-ui` checks all spuriously fail before the agent has actually bound its ports.

### Step 5b — Wait until the stack is actually healthy

**Gate 0 — container count must be > 0.** `docker compose up -d` can
return 0 while starting **zero** containers (see the `--env-file`
warning in Step 5). The readiness probes in `readiness.md` walk an
empty container list and incorrectly report success. Refuse to
proceed past `up -d` until at least one container exists:

```bash
expected=$(docker compose --env-file $ENV_GEN -f resolved.yml config --services | wc -l)
actual=$(docker compose -f resolved.yml ps -q | wc -l)
[ "$actual" -gt 0 ] && [ "$actual" -ge "$expected" ] \
  || { echo "FAIL: expected $expected services, got $actual — re-check Step 5 --env-file"; exit 1; }
```

`docker compose up -d` returns once containers are *created*, not when
the processes inside are *ready*. Cold deploys can legitimately take
10–20 min. The full readiness procedure (compose-ps NDJSON gate + the
per-profile `curl` checks + slow-container triage) lives in
[`references/readiness.md`](references/readiness.md); each
`references/<profile>.md` lists the endpoints that must be reachable
for that profile. **Never declare the deploy done after `up -d`
returns** — only after every documented endpoint succeeds.

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
