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

## Output contract — DEPLOY flow

When running the DEPLOY / TEARDOWN / DEBUG flow, the agent MUST honour
all four items below on every successful deploy. These are the user's
only feedback channel between steps; skipping any of them is a
behaviour regression.

1. **Render every step's exit in a fixed-width box** — Step 1 *Deploy
   targets*, Step 2 *Pipeline configuration*, Step 3 *Container*, Step 4
   *Apply configuration*, Step 5 *Plan* + *Results*. Not just the final
   summary. The box is the user's step receipt. Geometry is fixed (see
   § "Universal box format" below). Per-step **content** rules (what
   rows go inside each box) live in [`references/deploy-vss-detection-tracking-2D.md`](references/deploy-vss-detection-tracking-2D.md)
   under "Step N box content rule".
2. **After the Step 5 Results box, issue the Step 6 `AskUserQuestion`**
   from [`references/next-steps.md`](references/next-steps.md) § "11.c"
   — never replace it with a free-form *Next steps* bullet list. The
   menu is the deploy's exit handle: it lets the user run metrics,
   manage streams, tail logs, or tear down with one click instead of
   having to remember curl URLs.
3. **After the user picks a Step 6 bucket, issue the follow-up
   `AskUserQuestion`** from [`references/next-steps.md`](references/next-steps.md)
   § "11.d" — never substitute prose + ready-to-copy curl examples + a
   free-text "want me to run X?" question. Each bucket has its own
   menu of concrete actions; the user picks the action, then the skill
   emits the API box and runs the curl. Per-bucket follow-ups:
   - **Manage streams** → Add / Remove / List. **Remove builds its
     options dynamically from `/stream/get-stream-info`** — one option
     per active stream labelled `<camera_id> · <camera_url>` plus
     "Remove ALL" when `ACTIVE > 1` (full spec: § "`remove_streams`
     sub-flow").
   - **Stop the deployment** → Stop app / Stop container / Full teardown.
   - **Check metrics & FPS** → no follow-up; run `collect_metrics.sh`
     directly after printing the `/api/v1/metrics` API box.
   - **Check liveness / readiness** → no follow-up; probe all three
     health endpoints after printing their API boxes.
4. **Render the FULL per-step content, not an overview row** —
   rendering the box is necessary but not sufficient. Each step has a
   row composition spec in
   [`references/deploy-vss-detection-tracking-2D.md`](references/deploy-vss-detection-tracking-2D.md)
   under "Step N box content rule". **Step 4 (Apply configuration) is
   where the agent collapses most often** — its canonical
   per-use-case key list lives in
   [`references/apply-config.md`](references/apply-config.md)
   § "Per-use-case complete edit list", and the agent MUST emit one
   `✔ [section] key=value  — annotation` row per key in that table for
   the active use case + settings. A section with 5 keys → 5 rows; a
   section with 6 keys → 6 rows. Never one overview row per section.

Forbidden (these are the shortcuts the agent falls back to under
pressure, and they break the user's UX):

- ❌ A one-line `✔ App ready in Ns, N streams, fps total Y` in place of
  the Step 5 Results box.
- ❌ ASCII box-drawing chars (`+`, `-`, `=`, `*`) instead of light
  box-drawing chars (`┌ ─ ┐ │ └ ┘`).
- ❌ Skipping Step 6 on the assumption "the user knows what to do next".
- ❌ After Step 6, dumping a markdown wall of prose + multiple curl
  blocks + a closing "want me to run any of these?" — that's the
  shape the agent falls back to and it bypasses both the 11.d menu
  and the per-API-call box. The user picks from a menu; the skill
  shows the resolved API box; the skill runs it. No free-text Q.
- ❌ Step 4 overview collapses — these are explicitly banned by the
  deploy doc's Step 4 content rule:
    - `✔ Batch size 3 (tile grid: 1×3)` → required: 5 separate rows
      (`[streammux] batch-size=3`, `[primary-gie] batch-size=3`,
      `[source-list] max-batch-size=3`, `[tiled-display] rows=1`,
      `[tiled-display] columns=3`).
    - `✔ Output sink eglsink` → required: one row per sink key
      (4 keys for eglsink, e.g. `[sink0] enable=1`, `type=2`,
      `sync=0`, `qos=0` — read apply-config.md for the exact list).
    - `✔ Sources static (3 streams, http-port=9000)` → required: six
      annotated `[source-list]` rows.
    - `✔ Tile grid 1 row × 3 cols` (single row) → required: two
      rows, `[tiled-display] rows=1` and `[tiled-display] columns=3`.

## Universal box format

The geometry contract for every step-exit box (Step 1 through Step 5
Results). The same shape across every box; only the **title** and the
**body rows** change per step.

- **Width: 128 chars** corner-to-corner — `┌` at column 1, `┐` at
  column 128. Wider terminals leave the box flush-left; do not stretch
  it. Inner content area is **124 chars** (with one space margin on
  each side inside the `│` borders).
- **Light box-drawing chars only**: `┌ ─ ┐ │ └ ┘`. No `+`, `-`, `=`,
  `*` ASCII fallbacks.
- **Top border — title CENTERED**: `┌` + N₁ dashes + `␣` + title + `␣`
  + N₂ dashes + `┐`, where `N₁ + N₂ + len(title) + 2 = 126`. Distribute
  the pad: `N₁ = floor((126 − len(title) − 2) / 2)`,
  `N₂ = 126 − len(title) − 2 − N₁`. N₁ and N₂ differ by at most 1.
- **Body**: one `│ <content padded to inner-content 124> │` per fact.
  Each fact line uses the `  ✔ <key-padded-to-13>  <value>` form (two
  spaces in, glyph, key right-padded to 13, two spaces, value).
- **Blank lines between groups**: render `│ <124 spaces> │` between
  logical groups (e.g. Identity / Model / Videos in Step 1) so the
  user can scan the box at a glance.
- **Bottom border**: `└` + 126 dashes + `┘` — solid border, no title.

Standard step titles (used at the top of each step's box):

```
┌─────────────────────────────────────────────────────── Deploy targets ───────────────────────────────────────────────────────┐
┌─────────────────────────────────────────────────── Pipeline configuration ───────────────────────────────────────────────────┐
┌───────────────────────────────────────────────────────── Container ──────────────────────────────────────────────────────────┐
┌──────────────────────────────────────────────────── Apply configuration ─────────────────────────────────────────────────────┐
┌──────────────────────────────────────────────── Perception Application — Plan ───────────────────────────────────────────────┐
┌────────────────────────────────────────────── Perception Application — Results ──────────────────────────────────────────────┐
```

Per-step content rules (which rows go in which box, mode-aware row
hiding, the apply-config sectioned layout, the Step 5 PLAN-then-RESULT
pattern, the Step 3 `docker run` synthesis requirement) live in
[`references/deploy-vss-detection-tracking-2D.md`](references/deploy-vss-detection-tracking-2D.md)
under "Step N box content rule" — read those when rendering the
corresponding step.

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
