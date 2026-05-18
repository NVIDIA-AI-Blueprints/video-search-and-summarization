---
name: vss-deploy-detection-tracking-2D
description: >
  Use when the user wants to deploy, operate, debug, or tear down the RTVI-CV
  (Real Time Video Intelligence CV) microservice locally, OR call its REST API
  on a running instance. Deploy triggers: deploy/run/launch/start/bring up/set
  up/restart rtvi-cv, rtvicv, rtvi cv, warehouse 2d/3d, sparse4d, smartcity
  rtdetr, smartcity gdino, perception app, metropolis perception app — with or
  without modifiers like "with N streams", "with display", "save to file",
  "from rtsp". Teardown triggers: stop/tear down/shutdown/kill/cleanup of
  rtvi-cv, rtvicv-perception-docker, the perception container. Debug triggers:
  check rtvi-cv logs, diagnose rtvi-cv failures, troubleshoot rtvi-cv crashing
  or healthcheck failing. API triggers: add/remove/list streams, check
  ready/live/startup, get metrics, FPS, GPU usage, generate text embeddings,
  call rtvi-cv api on localhost:9000/api/v1. Do NOT use for remote-host
  provisioning — runs against localhost only.
license: Apache-2.0
metadata:
  version: "3.1.0"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia rtvi-cv deployment rest-api docker deepstream ngc warehouse smartcity sparse4d gdino rt-detr metropolis stream-management health-check metrics"
---

# RTVI-CV — Detection & Tracking (Unified Skill)

Unified skill for the **Real Time Video Intelligence CV (RTVI-CV)** microservice. Two action surfaces in one skill:

- **Deploy / operate / debug / tear down** the RTVI-CV container locally → see [`references/deploy-vss-detection-tracking-2D.md`](references/deploy-vss-detection-tracking-2D.md)
- **Call the RTVI-CV REST API** (streams, health, metrics, embeddings) on a running instance → see [`references/usage-vss-detection-tracking-2D.md`](references/usage-vss-detection-tracking-2D.md)

> **Service**: `rtvi-cv` (`metropolis_perception_app`)
> **Image**: `nvcr.io/<org>/<repo>:<tag>` — user-supplied at deploy time
> **REST port**: `9000` (`/api/v1` — `/live`, `/ready`, `/startup`, `/metrics`, `/stream/add`, `/stream/remove`, embeddings)
> **Hardware**: x86/aarch64 dGPU (T4, A100, L40, H100, B200, RTX), SBSA (Spark, Grace-Hopper), Jetson (Thor, Orin, Xavier)

---

## Action routing — pick once per invocation

| User intent (sample phrasing) | Flow | Load this reference |
|-------------------------------|------|---------------------|
| `deploy rtvi-cv warehouse 2d`, `run rtvicv warehouse-3d with 4 streams`, `start smartcity gdino`, `launch perception app`, `bring up sparse4d` | **DEPLOY** | [`references/deploy-vss-detection-tracking-2D.md`](references/deploy-vss-detection-tracking-2D.md) |
| `stop rtvi-cv`, `tear down`, `kill the perception container`, `cleanup rtvicv-perception-docker` | **TEARDOWN** (handled by deploy doc → "Mode Selection") | [`references/deploy-vss-detection-tracking-2D.md`](references/deploy-vss-detection-tracking-2D.md) + [`references/teardown-flow.md`](references/teardown-flow.md) |
| `check rtvi-cv logs`, `diagnose rtvi-cv crashing`, `troubleshoot healthcheck failing`, `rtvi-cv won't start` | **DEBUG** | [`references/deploy-vss-detection-tracking-2D.md`](references/deploy-vss-detection-tracking-2D.md) + [`references/troubleshooting.md`](references/troubleshooting.md) |
| `add a stream`, `remove camera`, `list streams`, `health check`, `is rtvi-cv ready`, `get metrics`, `what's the FPS`, `check GPU usage`, `generate text embeddings`, `call rtvi-cv api` | **API USAGE** | [`references/usage-vss-detection-tracking-2D.md`](references/usage-vss-detection-tracking-2D.md) + [`references/api-reference.md`](references/api-reference.md) |

**Selection rule:** match the user's phrasing against the table above and immediately load the corresponding reference file. Do not mix the flows — DEPLOY assumes no running container yet; API USAGE assumes the container is already running on `http://<host>:9000`.

If intent is genuinely ambiguous (e.g., the user says just "I want to use rtvi-cv"), ask one `AskQuestion`: deploy a new instance, or call an already-running one?

---

## What lives where

```
vss-deploy-detection-tracking-2D/
├── SKILL.md                                    # this file (TOC + routing)
├── eval/
│   ├── deploy-evals.json                       # deploy-flow eval cases
│   └── usage-evals.json                        # API-flow eval cases
├── scripts/                                    # 24 bash + python helpers (deploy flow)
│   ├── load_defaults.sh                        # platform + YAML defaults
│   ├── fetch_resources.sh                      # NGC download + extract + scan
│   ├── apply_in_container.sh                   # host-side wrapper for Step 4
│   ├── start_app_in_container.sh               # host-side wrapper for Step 5
│   ├── apply_config.sh / discover_streams.sh / add_streams.sh / …
│   └── (see scripts/ directory for full inventory)
└── references/
    ├── deploy-vss-detection-tracking-2D.md     # DEPLOY / TEARDOWN / DEBUG runbook (full workflow, every step preserved)
    ├── usage-vss-detection-tracking-2D.md      # API USAGE workflow
    ├── api-reference.md                        # endpoint schemas + curl templates
    ├── task-list.md                            # Step 0 — TodoWrite templates
    ├── usecases.md                             # per-use-case NGC refs, configs, run commands
    ├── platforms.md                            # docker run per platform + display/file variants
    ├── ngc-setup.md                            # NGC credentials + downloads
    ├── resource-plan.md                        # resource decision logic, source precedence
    ├── pipeline-config.md                      # batch / source / sink decision tree
    ├── container-reuse.md                      # reuse/restart/parallel decision JSON
    ├── apply-config.md                         # Step 4 — path sub, batch, sink, sources, engine cache
    ├── start-app.md                            # Step 5 — start + readiness + metrics + log
    ├── next-steps.md                           # Step 6 — stream lifecycle, REST examples
    ├── teardown-flow.md                        # 5-step teardown (discover → execute)
    ├── environment.md                          # secrets, mounts, env vars, GPU, ports, dry run
    ├── ux-conventions.md                       # visibility / AskQuestion contract
    ├── workflow-reference.md                   # alternative walkthrough
    ├── troubleshooting.md                      # common failure modes
    ├── upgrade-rollback.md                     # image upgrade / rollback procedure
    └── deploy-defaults.yml                     # SINGLE source of truth for default tags/refs/paths/GPU index
```

All scripts are invoked from the skill root via `$SKILL_DIR/scripts/<name>` — paths inside the deploy reference doc are preserved verbatim and resolve correctly when the agent runs from skill root.

---

## How to use this skill

1. **Read this file first.** It only routes — it does not contain workflows.
2. **Match the user's intent** against the routing table above.
3. **Load exactly one reference doc** (DEPLOY or API USAGE). Don't preload both — each reference is large and contains its own full contract.
4. **Follow the loaded reference exactly.** The reference docs are the byte-for-byte preserved contracts from the predecessor skills `vss-deploy-detection-tracking-2D` (deploy/teardown/debug) and `rtvicv-api` (REST API) — every step ordering invariant, bash-batching rule, box-rendering rule, and `AskQuestion` contract is retained.
5. **For DEPLOY**, the reference doc enforces its own startup contract: one-line acknowledgement → `TodoWrite` widget → Step 1 question. Do not narrate, do not pre-flight beyond what the reference allows.

---

## Quick triggers (mnemonic)

| Phrase | Flow |
|--------|------|
| `deploy rtvicv warehouse 2d with 4 streams and display` | DEPLOY |
| `run smartcity gdino on gpu 1` | DEPLOY |
| `stop the perception container` | TEARDOWN (deploy doc) |
| `rtvi-cv healthcheck failing` | DEBUG (deploy doc + troubleshooting) |
| `add a stream to rtvi-cv` | API USAGE |
| `is rtvi-cv ready on localhost:9000` | API USAGE |
| `get rtvi-cv metrics` | API USAGE |
| `generate text embeddings via rtvi-cv` | API USAGE |
