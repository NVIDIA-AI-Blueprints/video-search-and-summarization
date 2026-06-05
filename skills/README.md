# VSS Skills

Skills for working with the **NVIDIA Blueprint for Video Search & Summarization (VSS)** — a suite of GPU-accelerated microservices for building vision agents and video-analytics applications. Each subdirectory under `skills/` is a self-contained skill following the [agentskills.io](https://agentskills.io/specification) specification, with `name`, `description`, `version`, and `license` declared in its `SKILL.md` frontmatter.

> **New here? Read [Orientation](#orientation-how-vss-fits-together) first, then [Which skill do I need?](#which-skill-do-i-need).** Those two sections are the fastest path from a user request to the right skill.

These skills are a **developer-side tool**: a coding agent (Claude Code, Codex, NemoClaw, or any agentskills.io host) loads them to *deploy and operate* a VSS deployment from natural language. They are distinct from the in-product **VSS Agent**, which runs *inside* a deployed VSS workflow and orchestrates microservices to answer end-user questions. The skills here drive VSS; the VSS Agent is one of the things they can drive.

---

## Orientation: how VSS fits together

VSS-based deployments are multi-layer systems. Most skills map to exactly one layer, so knowing the layers tells you which skill to reach for. Video flows top-to-bottom: raw frames are turned into features in real time, those features are published to a message bus, analyzed downstream, placed in storage or accessed by an agent to generate results (i.e. summaries, search, etc).

```
                         ┌──────────────────────────────────────────────┐
  VIDEO IN (files,       │  1. REAL-TIME VIDEO INTELLIGENCE             │
  RTSP, live streams) ──▶│     Extract features from video in real time │
                         │     • RT-CV     detection & tracking (2D/3D) │
                         │     • RT-Embed  semantic video embeddings    │
                         │     • RT-VLM    captions / incident detection│
                         └────────────────────────┬─────────────────────┘
                                                  │ features → message broker / DB
                         ┌────────────────────────▼─────────────────────┐
                         │  2. DOWNSTREAM ANALYTICS                     │
                         │     Turn features into insights & alerts     │
                         │     • Behavior Analytics  (events, incidents)│
                         │     • Alert Contextualization                |
                         └────────────────────────┬─────────────────────┘
                                                  │ incidents, metrics → Elasticsearch
                         ┌────────────────────────▼─────────────────────┐
                         │  3. AGENT & OFFLINE PROCESSING               │
                         │     Reason over results for users            │
                         │     • Search, Summarize, Ask, Report         │
                         │     • Query analytics  (via VA-MCP)          │
                         └──────────────────────────────────────────────┘

  MIDDLEWARE (cross-cutting): Video IO & Storage (VIOS) · API Gateway / MCP ·
  Message Broker (Kafka/Redis) · Database (Elasticsearch) · Calibration
```

**Two ways skills are used** — most workflows touch both:

- **Deploy time** — *"Deploy VSS for video search."* A skill selects the right profile or microservice, runs pre-flight checks, and brings up the Docker Compose stack. (`vss-deploy-*`, `vss-setup-*`, `vss-generate-video-calibration`)
- **Runtime** — *"Add this camera," "summarize this clip," "show me today's incidents."* Once VSS is running, a skill calls the live REST / MCP / VIOS APIs. (everything else)

**Profiles vs. standalone microservices.** A *profile* is a pre-assembled stack of microservices wired together for one workflow. Use **`vss-deploy-profile`** to bring up a whole workflow (`base`, `search`, `lvs`, `alerts`, `warehouse`, `edge`). Use the individual **`vss-deploy-*` / `vss-setup-*`** skills only when you need one microservice on its own.

| Profile | Workflow it deploys |
|---|---|
| `base` | Video retrieval, VLM Q&A, and report generation on short clips (the quickstart) |
| `search` | Natural-language search across video archives using embeddings |
| `lvs` | Summarization of long recordings via chunking + dense-caption aggregation |
| `alerts` | Real-time perception → behavior analytics → VLM alert verification |
| `warehouse` / `edge` | Industry example stacks and edge-device deployments |

---

## Which skill do I need?

Match the user's intent to a skill. Start here before opening any individual `SKILL.md`.

| I want to… | Use this skill |
|---|---|
| Stand up a whole VSS workflow (base / search / lvs / alerts / warehouse) | [`vss-deploy-profile`](vss-deploy-profile/SKILL.md) |
| Search archived video with natural language ("find the red truck") | [`vss-search-archive`](vss-search-archive/SKILL.md) |
| Summarize a long recording | [`vss-summarize-video`](vss-summarize-video/SKILL.md) |
| Ask a one-off visual question about a clip | [`vss-ask-video`](vss-ask-video/SKILL.md) |
| Produce a formatted analysis report | [`vss-generate-video-report`](vss-generate-video-report/SKILL.md) |
| Produce a report using the frag / Enterprise-RAG pipeline | [`vss-generate-video-report-rag`](vss-generate-video-report-rag/SKILL.md) |
| Add / manage / monitor alerts on a stream | [`vss-manage-alerts`](vss-manage-alerts/SKILL.md) |
| Read incidents, metrics, or sensor data (incl. Slack/Kafka feeds) | [`vss-query-analytics`](vss-query-analytics/SKILL.md) |
| Add a camera, extract a clip, grab a snapshot, manage recordings | [`vss-manage-video-io-storage`](vss-manage-video-io-storage/SKILL.md) |
| Run object detection & tracking on streams (2D) | [`vss-deploy-detection-tracking-2d`](vss-deploy-detection-tracking-2d/SKILL.md) |
| Run multi-camera 3D / BEV-fusion tracking | [`vss-deploy-detection-tracking-3d`](vss-deploy-detection-tracking-3d/SKILL.md) |
| Generate dense captions / detect anomalies via VLM on streams | [`vss-deploy-dense-captioning`](vss-deploy-dense-captioning/SKILL.md) |
| Generate semantic video embeddings as a standalone service | [`vss-deploy-video-embedding`](vss-deploy-video-embedding/SKILL.md) |
| Calibrate a multi-camera dataset (often a prerequisite for 3D) | [`vss-generate-video-calibration`](vss-generate-video-calibration/SKILL.md) |
| Deploy behavior analytics on its own | [`vss-setup-behavior-analytics`](vss-setup-behavior-analytics/SKILL.md) |
| Deploy the video-analytics REST API on its own | [`vss-setup-video-analytics-api`](vss-setup-video-analytics-api/SKILL.md) |

**Skills chain.** Skills auto-invoke each other when a prerequisite is missing — e.g. `vss-deploy-detection-tracking-3d` calls `vss-generate-video-calibration` when calibration data is absent. When a request spans layers (deploy a profile *and* add a camera *and* run a search), the agent composes several skills in sequence; the catalog below is grouped by layer so you can see what's adjacent.

**Easy to confuse:**

- `vss-ask-video` (one-off VLM question on a clip) vs. `vss-search-archive` (retrieval across an archive) vs. `vss-query-analytics` (read already-computed metrics/incidents — no live inference).
- `vss-generate-video-report` (formatted report from per-clip VLM or an incident range) vs. `vss-generate-video-report-rag` (the frag/RAG pipeline with HITL parameter collection).
- `vss-deploy-profile` (a whole workflow stack) vs. the `vss-deploy-*` / `vss-setup-*` skills (a single microservice).

---

## Catalog (by layer)

### Deployment & infrastructure
| Skill | Description |
|---|---|
| [vss-deploy-profile](vss-deploy-profile/SKILL.md) | Select, configure, deploy, verify, debug, or tear down any VSS **profile** (`base`, `search`, `lvs`, `alerts`, `warehouse`, `edge`) with a Docker Compose-centric workflow. Start here for a full workflow. |
| [vss-generate-video-calibration](vss-generate-video-calibration/SKILL.md) | Run AutoMagicCalib (AMC) camera calibration on local MP4s, RTSP streams, or the bundled sample dataset; deploy the `vss-auto-calibration` microservice when needed. |

### Layer 1 — Real-time video intelligence
| Skill | Description |
|---|---|
| [vss-deploy-detection-tracking-2d](vss-deploy-detection-tracking-2d/SKILL.md) | Deploy/operate the RTVI-CV perception microservice for 2D detection & tracking (`warehouse-2d/3d`, `smartcity-rtdetr/gdino`) and call its REST API. |
| [vss-deploy-detection-tracking-3d](vss-deploy-detection-tracking-3d/SKILL.md) | Deploy/operate the RTVI-CV-3D stack (MV3DT / Multi-View 3D Tracking) — per-camera DeepStream + BEV fusion over calibrated cameras. Auto-chains to calibration when missing. |
| [vss-deploy-dense-captioning](vss-deploy-dense-captioning/SKILL.md) | Deploy and call the RT-VLM dense-captioning microservice (captions, alerts, stream management, OpenAI-compatible completions) on files and live RTSP. |
| [vss-deploy-video-embedding](vss-deploy-video-embedding/SKILL.md) | Deploy and operate the RT-Embed video-embedding microservice — `/v1` REST API for file/text/video embeddings and live RTSP, plus Redis/Kafka/OTel integration. |

### Layer 2 — Downstream analytics
| Skill | Description |
|---|---|
| [vss-manage-alerts](vss-manage-alerts/SKILL.md) | Add, manage, and monitor alerts on streamed video — CV verification mode or VLM real-time mode, Alert-Bridge subscriptions, Slack notifications, camera onboarding. |
| [vss-setup-behavior-analytics](vss-setup-behavior-analytics/SKILL.md) | Deploy the `vss-behavior-analytics` service standalone — pick the entrypoint (Analytics 2D / 3D / mv3dt, dev_example, fusion_search), point it at a profile-shipped or custom config and optional calibration, and (with a Kafka / Redis Streams / MQTT broker reachable) push dynamic-config and dynamic-calibration updates over the `mdx-notification` topic — all without bringing up the full warehouse stack. |
| [vss-setup-video-analytics-api](vss-setup-video-analytics-api/SKILL.md) | Deploy the `vss-video-analytics-api` REST service standalone against custom Elasticsearch and Kafka infrastructure. |

### Layer 3 — Agent & offline processing
| Skill | Description |
|---|---|
| [vss-search-archive](vss-search-archive/SKILL.md) | Search video archives with natural language using multi-embedding fusion (Cosmos-Embed1) plus CV attribute matching; also ingests files/RTSP for search. |
| [vss-summarize-video](vss-summarize-video/SKILL.md) | Summarize a recorded video via chunking, dense captioning, and aggregation using the Long Video Summarization (LVS) microservice (HITL-gated, VLM fallback). |
| [vss-ask-video](vss-ask-video/SKILL.md) | Answer a fresh text question about a recorded clip via the VSS agent's `video_understanding` (VLM) tool. |
| [vss-generate-video-report](vss-generate-video-report/SKILL.md) | Produce a formatted markdown report by querying the VSS agent's `/generate` endpoint — per-clip VLM (Mode A) or incident-range (Mode B). |
| [vss-generate-video-report-rag](vss-generate-video-report-rag/SKILL.md) | Generate video summary reports with Enterprise RAG context using the VSS frag/RAG pipeline and HITL parameter collection. |
| [vss-query-analytics](vss-query-analytics/SKILL.md) | Query analytics metrics, incidents, alerts, and sensor data from Elasticsearch via the VA-MCP server (port 9901). |

### Middleware
| Skill | Description |
|---|---|
| [vss-manage-video-io-storage](vss-manage-video-io-storage/SKILL.md) | Video/stream management, recording timelines, clip extraction, snapshots, and add/delete sensors via the Video IO & Storage (VIOS) microservices. |

Skills with `evals/*.json` specs are exercised automatically by the Skills Eval CI workflow on every PR that touches `skills/**`; legacy `eval/*.json` specs are still accepted for skills that have not moved yet. See [`.github/skill-eval/AGENTS.md`](../.github/skill-eval/AGENTS.md) for harness behavior.

---

## Renamed in GA

The VSS 3.2 GA skill names replaced the pre-GA slash-command names:

| Pre-GA command | VSS 3.2 GA command |
|---|---|
| `/alerts` | `/vss-manage-alerts` |
| `/deploy` | `/vss-deploy-profile` |
| `/report` | `/vss-generate-video-report` |
| `/rt-vlm` | `/vss-deploy-dense-captioning` |
| `/video-analytics` | `/vss-query-analytics` |
| `/video-search` | `/vss-search-archive` |
| `/video-summarization` | `/vss-summarize-video` |
| `/video-understanding` | `/vss-ask-video` |
| `/vios` | `/vss-manage-video-io-storage` |
| `/vss-frag` | `/vss-generate-video-report-rag` |

## Install (recommended: ask your coding agent)

Open this repository in your coding agent (Claude Code, Codex, Cursor, or any other agentskills.io-compatible host) and paste the following prompt:

> Read `skills/README.md` and every `SKILL.md` file under `skills/`. For each skill in the catalog, install it for this host so I can invoke it from a shell or chat session. Use the host's standard skills directory:
>
> - Claude Code: `~/.claude/skills/<name>/`
> - Codex: `~/.codex/skills/<name>/`
> - Hosts that follow the agentskills.io universal path: `~/.agents/skills/<name>/`
>
> Symlink each skill folder rather than copying it so a `git pull` here keeps every install up to date. Skip skills that are already installed and pointing at this checkout. When you're done, list the skills you registered and which directory you used.

The agent will read the frontmatter of each `SKILL.md`, create the symlinks, and confirm what's installed. The skills become invokable in the next agent session.

### Single-skill install

To install skills individually, paste the following prompt:

> Install only `skills/<name>/` for this host the same way.

### Update

After `git pull`, the symlinks already point at the updated content — nothing to do unless skills were added or renamed. To pick up new skills use the following prompt:

> Re-read `skills/README.md` and add any new skills missing from this host's skills directory.

### Uninstall

To uninstall skills, paste the following prompt:

> Remove every VSS skill symlink you previously created under this host's skills directory.

## Source of truth

This `skills/` directory is the canonical source. Skills published to the public catalog at `github.com/nvidia/skills` are mirrored from here at sync time.
