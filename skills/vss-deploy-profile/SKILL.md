---
name: vss-deploy-profile
description: Use when the user asks to select, configure, deploy, verify, debug, or tear down a VSS profile (base, search, lvs, warehouse, edge). Not for standalone microservices — use the vss-deploy-* skill.
license: Apache-2.0
metadata:
  version: "3.2.1"
  author: "NVIDIA Video Search and Summarization team"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint deployment"
---
# VSS Deploy

Do not use this skill for:

- Standalone microservice deployment outside a compose profile — use the matching skill: `vss-deploy-dense-captioning`, `vss-deploy-detection-tracking-2d`, `vss-deploy-detection-tracking-3d`, or `vss-deploy-video-embedding`.
- Summarizing or querying a video once `lvs` is deployed — use `vss-summarize-video`.
- NGC CLI install/configure in isolation — see [`references/ngc.md`](references/ngc.md), or `vss-deploy-profile` will run it as part of the credential gate.

## Available Scripts

| Script | Purpose | Arguments |
|---|---|---|
| `scripts/normalize_resolved_yml.py` | Strip optional `depends_on` entries for services filtered out of `resolved.yml` before deploy. | Path to `resolved.yml` |
| `scripts/probe_remote_models.sh` | Probe an OpenAI-compatible remote LLM/VLM endpoint and verify the selected model id. | Base URL, optional expected model id |
| `scripts/check_credentials.sh` | Probe `NGC_CLI_API_KEY`/`NGC_API_KEY`, `NVIDIA_API_KEY`, and `HF_TOKEN` against their services; see [`references/credentials.md`](references/credentials.md). | None (reads env vars) |

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

**Edge hardware routing** (DGX Spark, AGX/IGX Thor): see [`references/edge.md`](references/edge.md). All three edge platforms run the in-tree `nvidia-nemotron-nano-9b-v2-fp8` compose service (raw vLLM) on port `30081`; `dev-profile.sh` selects it automatically.

**Each profile's reference owns its sizing table.** Don't pick a deployment shape from this file — open the profile reference and check minimum GPU count for the host's hardware against the (mode × platform) matrix there.

## Instructions

The deployment flow is always: copy `overrides.env` to `generated.env`, apply overrides, dry-run compose into `resolved.yml` with both env layers, review, normalize, deploy with the same env layers, then wait for readiness.

```bash
# 1. cp dev-profile-<profile>/overrides.env dev-profile-<profile>/generated.env  (clean copy)
# 2. Apply env overrides to generated.env  (source .env and overrides.env stay untouched)
# 3. docker compose --env-file .env --env-file generated.env config > resolved.yml  (dry-run)
# 4. Review resolved.yml
# 5. docker compose --env-file .env --env-file generated.env -f resolved.yml up -d
```

`.env` is the read-only checked-in stable-default layer. `overrides.env` is the read-only checked-in profile override layer for values the deploy scripts may modify per host/profile. `generated.env` is the per-deploy working copy created from `overrides.env`. Step 1c covers this in full.

## Prerequisites

1. **Repo path** — auto-detect `video-search-and-summarization/` before
   asking the user. Use the detected path as `$REPO` for all subsequent
   commands.
2. **Credential gates** — see [`references/credentials.md`](references/credentials.md): `NGC_CLI_API_KEY` for local/local_shared NIM pulls, `NVIDIA_API_KEY` for remote NIM endpoints, and `HF_TOKEN` for edge recipes that use gated HF models.
3. **System prerequisites (GPU driver, Docker, NVIDIA Container Toolkit, kernel sysctls, and — if `ufw` is active — the [Docker-bridge→host firewall allow](references/prerequisites.md#firewall) so bridge NIMs can fetch clips from host-mode VST)** — full checks in [`references/prerequisites.md`](references/prerequisites.md). Canonical hardware/driver matrix is the [VSS prerequisites page](https://docs.nvidia.com/vss/3.2.0/prerequisites.html).

The auto-detect snippet (git-root, then a common-path probe gated on
`deploy/docker/compose.yml` + `dev-profile.sh` + `skills/vss-deploy-profile`)
lives in [`references/prerequisites.md`](references/prerequisites.md#repo-detect).
Export the resolved `$REPO`; if detection fails, ask the user for the checkout path.

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

**Endpoint intent gate.** Don't infer remote placement from stray env vars
(`LLM_ENDPOINT_URL`, `VLM_ENDPOINT_URL`, `LLM_BASE_URL`, `VLM_BASE_URL` may be
leftovers). Use remote LLM/VLM only when (1) the user asked for / supplied a
remote endpoint, (2) local sizing can't fit the selected models and the user
agrees, or (3) an edge recipe needs a standalone local service VSS treats as
`remote` (e.g. the standalone small-model vLLM alternative in `edge.md`
on `localhost:30081`). If an endpoint var is set but the user didn't ask
for remote, surface it in Step 1 and ask — never silently deploy remote
because a var happened to exist.

If no combination on this host satisfies the profile's sizing requirements, **stop and report the blocker** — don't silently pick another shape.

> **Edge shared mode is platform-specific.** Full recipes are in [`references/edge.md`](references/edge.md).

## Deployment Flow

Always follow this sequence. Never skip the dry-run.

### Step 0 — Tear down any existing deployment + clear data volumes

If a deployment already exists, tear it down AND clear stale data volumes before redeploying. 

Full procedure lives in [`references/teardown.md`](references/teardown.md).

### Step 0a — Credentials gate (run before any env mutation)

Validate every credential and selected remote endpoint the chosen profile
needs **before** Step 1c copies `overrides.env` to `generated.env`. A 401 here is a
30-second failure; the same 401 inside a NIM cold-start is a 10–20 min
failure. Run the discovery and probe flow in
[`references/credentials.md`](references/credentials.md), including
`scripts/probe_remote_models.sh` for any LLM/VLM endpoint you plan to write
into `generated.env`. Map the result against the chosen mode: missing
or invalid required credentials/endpoints are blockers, optional credentials
are not.

### Step 1 — Gather context

Before building env overrides, confirm:

| Value | How to determine |
|---|---|
| **Profile** | Match user intent to the routing table above. Default: `base` |
| **Repo path** | Use the `$REPO` value auto-detected in prerequisites. If auto-detect failed, ask the user for the checkout path before continuing. |
| **Hardware** | `nvidia-smi --query-gpu=name,memory.total --format=csv,noheader` |
| **LLM/VLM placement** | Explicitly decide local / local_shared / remote. Cross-reference available GPUs against the chosen profile's **Minimum GPU count** table. If endpoint env vars are present but the user did not request remote, ask whether to use or ignore them. |
| **API keys** | `NGC_CLI_API_KEY` for local NIMs, `NVIDIA_API_KEY` for remote |
| **`HOST_IP`** | In-cluster dial address: `ip route get 1.1.1.1` src (like `dev-profile.sh`; correct on LAN + cloud). If that interface is a VPN/tunnel, fall back to the LAN IP and **prompt the user** — [Network addressing](references/prerequisites.md#addressing). |
| **`EXTERNAL_IP`** | Browser-facing address; defaults to `${HOST_IP}`. Override when the browser path differs — cloud public IP, Brev secure-link (Step 1d), or tunnel; **ask the user where they browse from if unsure**. [Network addressing](references/prerequisites.md#addressing). |
| **`HAPROXY_HOST_PORT`** | Browser-facing ingress host port. Default `7777`; change it in `generated.env` if the host port conflicts. |
| **`HAPROXY_PORT`** | HAProxy container listen port. Default `7777`; leave it unchanged unless a platform-specific path, such as Brev, requires it. |

Before `docker compose up`, verify `EXTERNAL_IP`, `HAPROXY_HOST_PORT`, `VSS_PUBLIC_HOST`, and `VSS_PUBLIC_PORT` are populated with browser-reachable values. Otherwise the stack may appear healthy while UI/API/VST links 404 or loop through Cloudflare Access.

### Step 1b — Prepare the data directory

Layout (asset paths, ownership, mount points, profile-specific subdirs) is documented in [`references/data-directory.md`](references/data-directory.md). Read that file before deploying for the first time on a host or when changing profiles.


### Step 1c — Initialize `generated.env`

The skill's per-deploy working copy. Always start from a fresh copy of `overrides.env`, never mutate `.env` or `overrides.env`.

```bash
PROFILE=base
ENV_SRC=$REPO/deploy/docker/developer-profiles/dev-profile-$PROFILE/.env
ENV_POST=$REPO/deploy/docker/developer-profiles/dev-profile-$PROFILE/overrides.env
ENV_GEN=$REPO/deploy/docker/developer-profiles/dev-profile-$PROFILE/generated.env

cp "$ENV_POST" "$ENV_GEN"
```

All subsequent writes (Brev `EXTERNAL_IP`, the env_overrides dict from Step 2) go to `$ENV_GEN`. `$ENV_SRC` and `$ENV_POST` are read-only from here on, and Compose must receive `$ENV_SRC` before `$ENV_GEN`.

### Step 1d — Brev only: detect first, then set `EXTERNAL_IP` to the secure-link domain

**Detect Brev before anything else** — a Brev-provisioned instance sets `BREV_ENV_ID` in `/etc/environment`; nothing else does:

```bash
grep -qE '^BREV_ENV_ID=' /etc/environment && echo "on Brev" || echo "not Brev"
```

- **not Brev** → skip the rest of this step and **do not read [`references/brev.md`](references/brev.md)**; keep the normal `${HOST_IP}`-based `EXTERNAL_IP`.
- **on Brev** → apply the Brev secure-link overrides from [`references/brev.md` § Setup flow](references/brev.md#setup-flow) to `generated.env` (NOT `.env`). Those set `EXTERNAL_IP` / `VSS_PUBLIC_HOST` to the secure-link domain **and** `VSS_PUBLIC_HTTP_PROTOCOL=https` / `VSS_PUBLIC_WS_PROTOCOL=wss` / `VSS_PUBLIC_PORT=443` — setting `EXTERNAL_IP` alone leaves `http://…:7777` UI/API/WS links that the browser blocks as mixed content.

### Step 2 — Build env_overrides

Produce an `env_overrides` dict from the user request and the gathered
context: explicitly choose remote/local LLM/VLM, set credentials, point at
endpoints, set platform-specific flags. Do not let existing shell env vars
silently pick placement; write the selected `LLM_MODE` / `VLM_MODE` and
matching endpoint/model fields into `generated.env`. The full mapping (every
override key, when it applies, defaults, profile-specific differences) lives
in [`references/env-overrides.md`](references/env-overrides.md). Each profile
reference has worked examples for that profile's common scenarios.


### Step 3 — Apply overrides + dry-run

**Working env files:** `<repo>/deploy/docker/developer-profiles/dev-profile-<profile>/.env` plus `<repo>/deploy/docker/developer-profiles/dev-profile-<profile>/generated.env` (created in Step 1c from `overrides.env`).

> **Reminder (see Step 1c):** apply all overrides (Step 2 dict + Brev `EXTERNAL_IP`) to `generated.env`; Compose gets `.env` first and `generated.env` second, and post-deploy verifiers read `generated.env` for the actually-deployed override values.

```bash
# (Step 1c already ran: cp $ENV_POST $ENV_GEN)

# Apply the env_overrides dict from Step 2 to generated.env
# (read lines, update matching keys, append new keys, write)
# Example:
#   sed -i "s|^LLM_MODE=.*|LLM_MODE=remote|" "$ENV_GEN"
#   sed -i "s|^LLM_BASE_URL=.*|LLM_BASE_URL=http://localhost:30081|" "$ENV_GEN"

# Resolve compose
cd $REPO/deploy/docker
docker compose --env-file $ENV_SRC --env-file $ENV_GEN config > resolved.yml
```

The resolved YAML is saved to `<repo>/deploy/docker/resolved.yml`.

### Step 3b — Verify resolved.yml has no unexpanded ${...} tokens

Unexpanded `${VAR}` tokens in `resolved.yml` mean compose did not see those env values. Diagnostic procedure and common culprits live in [`references/troubleshooting.md`](references/troubleshooting.md).


### Step 3c — Verify access to selected NGC artifacts

After `resolved.yml` exists and before Compose starts, follow
[`references/credentials.md` § Artifact Entitlement Probes](references/credentials.md#artifact-entitlement-probes).
Verify every selected container image, NGC model/resource path, and
profile-staged artifact. Authentication alone does not prove repository
entitlement. Any access failure is a blocker; do not start Compose.

### Step 3d — Strip dangling optional `depends_on` from resolved.yml


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
docker compose --env-file $ENV_SRC --env-file $ENV_GEN -f resolved.yml up -d
```

> **Both `--env-file` arguments are mandatory and ordered.** Without the same `.env` + `generated.env` pair used in Step 3, `COMPOSE_PROFILES` may be unset or incomplete and `up -d` can exit 0 with zero selected services.

> **Avoid broad `--force-recreate` on ordinary retries** — it destroys warm
> NIM containers (another 3–5 min torch.compile + CUDA-graph capture each).
> Fix the root cause (usually perms or an env typo) and just re-run `up -d`;
> use targeted `--force-recreate --no-deps <service...>` only when a profile
> reference documents it as the recovery path.

`docker compose up -d` only creates containers; it does not wait for internal services to finish warming. Never declare deploy success until the readiness gates pass.

### Step 5b — Wait until the stack is actually healthy

**Gate 0 — container count must be > 0.** Refuse to proceed past `up -d` until the started count (`docker compose -f resolved.yml ps -q | wc -l`) is non-zero and ≥ the expected count (`config --services | wc -l`); a zero/short count almost always means a missing `--env-file` in Step 5. The exact gate plus the full readiness procedure live in [`references/readiness.md`](references/readiness.md).

Cold deploys can take 10–20 min, and each profile reference lists the required endpoints. **Never declare deploy done after `up -d`; only after every documented endpoint succeeds.**

## Tear Down

To tear down a deployment — full host reclaim or cache-preserving redeploy / profile
switch — follow [`references/teardown.md`](references/teardown.md). Always tear down
by the `mdx` project with `-v --remove-orphans`; a plain `docker compose down` leaves
volumes and networks behind.

## Debugging a Deployment

Use this workflow when the user asks to "debug the deploy", "verify it's working", "why is the agent not responding", or similar. The goal is to confirm the full video-ingestion-to-agent-answer path, not just that containers are "Up".

Each profile reference has a **Debugging** section listing the exact commands and failure-mode table for that profile.

### Quick checks (all profiles)

```bash
# 1. All expected containers Up
docker ps --format 'table {{.Names}}\t{{.Status}}'

# 2. Agent API + UI responding
curl -sf http://localhost:8000/health >/dev/null && echo "agent OK"
curl -sf http://localhost:3000/ >/dev/null && echo "ui OK"
```

The LLM/VLM NIM probes — including the `*_MODE=remote` handling that skips
`localhost:3008x` (where a connection refused is expected) and probes the
selected `*_BASE_URL/v1/models` via `scripts/probe_remote_models.sh` — are in
[`references/troubleshooting.md`](references/troubleshooting.md#nim-probes).

## Limitations

- This skill deploys compose-based VSS profiles only; standalone microservice deployment belongs to the matching `vss-deploy-*` skill.
- Hardware sizing, model placement, and profile-specific readiness are owned by profile references; do not infer them from memory.
- Privileged host remediation requires user approval when passwordless sudo is unavailable.

## Troubleshooting

The common-error quick reference, the full symptom → cause → fix table, the
unexpanded-`${...}` diagnostic, and the NIM endpoint probes are consolidated in
[`references/troubleshooting.md`](references/troubleshooting.md) — start there
for any deploy, runtime, or probe failure, then continue in the matching
per-profile reference's Debugging section.
