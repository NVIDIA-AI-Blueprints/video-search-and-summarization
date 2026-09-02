---
name: vss-build-vision-ai
description: >-
  Add agent-ready vision capabilities — dense captioning, detection, search, alerting, summarization — to an agent or application through a customizable, self-contained vision stack built on the NVIDIA VSS Blueprint. Use this skill when a developer or agent wants to give their app vision: pick capabilities via guided intake ("build a vision agent", "add vision capabilities") or describe them in natural language ("create a profile for streaming dense captioning", "add agentic search to my base deployment", "deploy warehouse 3d"). Route, compose, configure, and deploy stock base, alerts, LVS, search developer profiles, or the warehouse industry profile and lean custom combinations expressed as delta overlays using one current developer profile as the Foundation.
license: Apache-2.0
metadata:
  version: "3.2.0"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint orchestration deployment compose code-generation"
---

# Build Vision Agent

`build-vision-ai` gives agents and developers **agent-ready vision capabilities through a customizable, self-contained application stack** built on the **NVIDIA VSS Blueprint**. A developer or agent adds vision to their application by selecting the capabilities they want (guided intake) or describing them in natural language, and the skill routes to a validated developer profile — or composes the smallest delta overlay on top of one — and deploys it. Use it whenever the user wants vision capabilities composed for them: deploying a stock profile, extending a running deployment, or building a lean custom combination.

**Two ways in:** **guided intake** (state an open intent like "build a vision agent" / "add vision capabilities" and the skill walks you through capability selection) or **prompt-driven** (name the capability or profile directly). Both land on the same routing and composition flow.

## References

- [`references/composition.md`](references/composition.md) — delta-profile rules, Foundation selection, build artifact contract, resolution, and validation.
- [`references/deployment.md`](references/deployment.md) — resolved Compose deployment lifecycle.
- [`references/deployment_resolution.md`](references/deployment_resolution.md) — deployment publication of `VSS_PUBLIC_URL`, public-route mappings, and the endpoint contract consumed by operate skills.
- [`references/teardown.md`](references/teardown.md) — default project-volume cleanup, explicit cache-preserving teardown, stale-volume removal, and bind-mounted data cleanup.
- [`references/prerequisites.md`](references/prerequisites.md), [`references/credentials.md`](references/credentials.md), and [`references/ngc.md`](references/ngc.md) — host, GPU runtime, firewall, credential, entitlement, and NGC checks.
- [`references/sizing.md`](references/sizing.md) — consolidated developer-profile sizing, model placement, shared-GPU budgets, stream capacity, utilization tuning, and validation.
- [`references/edge.md`](references/edge.md) — DGX Spark and Thor routing, unified-memory budgeting, cache management, and edge model recipes.
- [`references/env-overrides.md`](references/env-overrides.md), [`references/data-directory.md`](references/data-directory.md), [`references/readiness.md`](references/readiness.md), [`references/troubleshooting.md`](references/troubleshooting.md), and [`references/brev.md`](references/brev.md) — deployment checks, mandatory data-directory preparation, and environment-specific runtime guidance.
- [`references/profiles/`](references/profiles/) — current developer profile capabilities, exact service sets, owner mappings, knobs, readiness checks, and sources.
- [`references/services/`](references/services/) — capability-owner contracts for service keys, required peers, configurable environment knobs, and sources.

## Routing

| Request | Route |
|---|---|
| Deploy, start, run, verify, or stop a named `base`, `alerts`, `lvs`, or `search` profile | Stock mode for that profile. |
| Any warehouse request — deploy, run, verify, stop, customize | `references/profiles/warehouse.md` owns every warehouse fact. It carries no intake questions and no step sequence — variant selection is **Q2w below**, and the lifecycle is the shared Steps. Warehouse **registers its own sources** via `bp-configurator-<mode>`; never hand-provision one. Select a variant per Q2w and expand its `COMPOSE_PROFILES_WH_*` list verbatim. Warehouse is variant selection, not composition: to change the shape of a deployment, select a different variant. |
| Deploy capabilities that exactly match one current developer profile | Stock mode for the exact match. |
| Build, create, extend, customize, combine, add, or remove capabilities | Delta mode using the closest current developer profile as the Foundation. |
| A named profile qualified as headless | Delta mode off that profile, not a stock deploy. |
| Deploy capabilities with no exact match | Build the smallest delta, then deploy it. |
| Provision, register, or ingest a source (file or live stream) into a deployed build, or fan it out to consumers | `vss-manage-video-io-storage` `references/provision-vios-source.md` — headless, direct REST (resolve consumer ports from `resolved.yml`, confirm no `vss-agent`); not `vss-search-archive`. |
| Resolution leaves a blocker the rules cannot settle (unmapped or ambiguous capability, Foundation tie, singleton conflict, or requested/excluded contradiction) | Clarification gate (`references/composition.md`): after one deterministic pass, ask one structured question, then resolve on the answer. Never re-run the same resolution or guess past the blocker. |
| `smartcities` or another industry profile | Stop: `warehouse` is the only supported industry Foundation. |
| Open / generic / "quickstart" intent with no named capability or profile | Guided front door (Q1): Pre-built workflow (Stock mode) or Custom build (Delta mode). |

## Entry Mode (Step 0)

Before routing, detect the **entry mode** — one of three: **Prompt-driven**, **Pre-built workflow**, or **Custom build**. All three share the same downstream machinery (profile catalog, Foundation selection, delta composition, resolution, and deployment); the mode only determines where the flow enters. **Pre-built workflow** is a fast path — it deploys a validated developer profile's authoritative service set unchanged in Stock mode (**no capability delta**), still producing a minimal stock `_builds/<name>/` for the shared validate -> deploy -> readiness -> teardown lifecycle — while **Custom build** is a guided front door onto Delta mode.

### Step 0.0 — Entry-mode detection

Classify the request before any other work:

1. **A concrete capability, microservice, profile, or existing deployment is named** (e.g. "create a profile for streaming dense captioning", "add agentic search to my base deployment", "deploy the alerts profile") → **Prompt-driven**. Parse inputs and continue at Step 1.
2. **An open / generic / first-time / "quickstart" intent with no extractable capability** (e.g. "build a vision agent", "add vision capabilities", "help me get started", "just deploy something"), or no capability description at all → open the **guided front door** (Q1 below), which leads with **Pre-built workflow (the recommended default)** and offers **Custom build**.
3. **Ambiguous** → ask one disambiguating question, or default to the guided front door (it is safe, reversible, and explicit: the user makes selections before anything is generated or deployed). Never silently assume a capability or fall back to a default profile.

### Guided front door — Q1

Ask via `AskUserQuestion` (single-select). Generate or deploy **nothing** until the user selects AND confirms downstream (the deploy prompt for Pre-built workflow; the Step 6 architecture diagram for Custom build).

**Q1 — Starting point.** *"How would you like to start?"*

- **Deploy a pre-built developer workflow** *(recommended for a first run / quickstart)* — Choose from a ready-made, validated VSS developer profile. Fastest path to a running system; no composition needed. Deploys as-is; you can customize it afterward. → **Q2a**
- **Deploy a pre-built industry blueprint** — Warehouse multi-camera perception (2D RT-DETR or 3D Sparse4D) with behavior analytics. Deployed as-is. → **Q2w**
- **Build a custom configuration** — pick the specific vision capabilities you need and let the skill compose the smallest delta overlay for them. → **Q2b**

### Mode: Pre-built workflow (quickstart)

The recommended first-run path. Deploys a validated developer profile via **Stock mode** — it keeps the profile's authoritative `COMPOSE_PROFILES` unchanged (**no delta**: no added or removed profile keys, no new service composes), then writes and deploys the standard stock `_builds/<name>/` artifacts like any other build (Steps 5-9). Ask **Q2a (single-select): "Which pre-built workflow do you want to deploy?"** and map the choice to the developer profile:

| Option | Capability | Profile |
|---|---|---|
| **Base** | VLM dense captioning and Q&A | `base` |
| **Alerts** | VLM real-time alerting or alert verification | `alerts` (mode picked in Q2a-mode) |
| **Video Summarization** | Time-windowed video summaries | `lvs` |
| **Search** | Object and video embeddings + agentic search | `search` |

> **Four-option limit.** `AskUserQuestion` shows at most **four** options per question (single- or multi-select), so Q2a must stay at the four developer profiles above. The `alerts` profile's two modes are **not** separate top-level rows (that would be a fifth option and get silently dropped); they are chosen in a follow-up, **Q2a-mode**, below. More generally, **any** question that needs more than four choices must **not** use the `AskUserQuestion` widget — present the options inline in the conversation and collect a typed reply instead (see **Q2b**, which does this for the capability multi-select).

**Q2a-mode — only when the user picks Alerts (single-select): "Which alerts mode?"** The `alerts` developer profile ships two modes, selected by its `MODE` knob; each has its own checked-in `COMPOSE_PROFILES` set in `dev-profile-alerts/overrides.env`, so both are still stock deployments (no delta):

| Option | Capability | Mode |
|---|---|---|
| **Real-time alerting** | Continuous RT-VLM inspection + real-time alert APIs | `2d_vlm` |
| **Alert verification** | Object detection with analytics and VLM event contextualization (RT-CV detection + behavior analytics + VLM verification + incidents) | `2d_cv` |

These are **predefined developer profiles** — the skill keeps the profile's authoritative `COMPOSE_PROFILES` unchanged (Stock mode, Step 5 exact match) and follows the shared build lifecycle (Steps 5–9). For Alerts, set the profile `MODE` per Q2a-mode.

**Customize a pre-built workflow → Custom build.** After a pre-built deploy (or instead of deploying), offer: *"Want to customize this workflow? I'll use **<selected profile>** as the starting point."* On **yes**, transition into **Custom build**, seeding the selected profile as the **Foundation** and computing a **capability delta** on top of it (the profile itself is never modified — it is only the baseline). The stock build becomes a **Delta build**: the same `_builds/<name>/` machinery now carries the added/removed profile keys and any changed knobs.

### Mode: Pre-built industry blueprint (warehouse)

Reached from Q1 → industry blueprint, or when the request names warehouse
directly. Expand the selected variant's service list verbatim — warehouse is
variant selection, not composition, so there is no delta path. Read
[`references/profiles/warehouse.md`](references/profiles/warehouse.md) before
asking, and apply its Hard constraints while asking, not after.

Up to three single-select questions, each inside the four-option cap. Describe
each option from warehouse.md's **Profile Service Set** table; do not restate
its service lists here, or this table drifts from the one that is authoritative:

| Question | Options |
|---|---|
| **Q2w-mode** — *"Which warehouse perception mode?"* | `2d` (RT-DETR) · `3d` (Sparse4D, depth-aware) |
| **Q2w-profile** — *"Which deployment variant?"* | `bp_wh` · `bp_wh_kafka` · `bp_wh_redis` |
| **Q2w-size** — *"Minimal or extended?"* | Extended · Minimal |

Filter the remaining options rather than validating the answers afterwards.
Both filters below are warehouse.md's to state; it is the source of truth for
why, and this list only says when to apply them:

- **Omit `bp_wh` from Q2w-profile when Q2w-mode is `3d`** — Hard constraints:
  `bp_wh` is 2D-only. Leaving it selectable turns an impossible deployment into
  a late runtime failure.
- **Skip Q2w-size entirely for `bp_wh`** — the Profile Service Set table lists
  no minimal variant for it.
- Set `SAMPLE_VIDEO_DATASET` and `NUM_STREAMS` from the chosen variant, not from
  the Foundation default; the dataset ↔ variant pairing is a Hard constraint.

The answers select exactly one `COMPOSE_PROFILES_WH_*` list. Record its name in
`FOUNDATION_VARIANT`, expand it verbatim into `COMPOSE_PROFILES`, and continue
at **Step 2** with `FOUNDATION=warehouse`. The shared lifecycle applies from
there, with four warehouse divergences: skip **Step 4**
(`references/composition.md` is the delta flow), **Step 5**'s effective service
set is already fixed above, **Step 7** additionally writes `configurator.env`,
and **Step 8** resolves through
[`references/profiles/warehouse.md`](references/profiles/warehouse.md) rather
than the delta flow in `references/composition.md`.

### Mode: Custom build (guided)

For a user who wants a specific composition. Reached from Q1 → Custom build, or by customizing a pre-built workflow (seeded with that profile as the Foundation). Ask **Q2b (multi-select): "Which vision capabilities do you want? (select all that apply)"** Each option maps to canonical service-profile keys owned by a capability owner under `references/services/`. **Video I/O + storage (VIOS) is always included** — every profile needs it — along with the shared `redis` cache peer that ships with the Foundation; present these as informational, not as choices. The **ELK + Kafka message bus / indexing stack is _not_ unconditional**: it is added only when a selected capability is Kafka-backed or Elasticsearch-indexed (see the note under the table), so a dense-captioning-only build keeps the smallest delta. (When seeded from a pre-built workflow, that profile's capabilities are pre-checked.)

Offer the user **exactly** the capabilities in the table below. Each row's owner contract, canonical service-profile key(s), and closest Foundation profile are fixed — do not invent options or keys outside it. Because this list can exceed four rows and `AskUserQuestion` caps a question at four options, **do not pose Q2b through the `AskUserQuestion` widget** — present this table in the conversation and have the user reply with the capabilities they want (by name or number; multiple allowed). Fall back to an `AskUserQuestion` multi-select only when four or fewer capabilities remain offerable.

| Option (shown to user) | Owner contract (`references/services/`) | Canonical service-profile key(s) | Closest Foundation | Peer notes |
|---|---|---|---|---|
| **Dense captioning** — natural-language descriptions of video | `rt-vlm.md` | `rtvi-vlm` | `base` | — |
| **Object detection & tracking (2D)** — bounding boxes, class labels, track IDs | `rt-cv.md` | `perception-2d-fusion` *(search)* / `perception-alerts` *(alerts)* | `search` | Kafka-backed; use the selected profile's key, not the shared `perception` extends source |
| **Semantic search over video** — embeddings + agentic search | `search.md` (+ `rt-embed.md`) | `vss-search-analytics-2d-fusion`, `rtvi-embed` | `search` | Requires RT-CV + RT-Embed + ELK; critique needs RT-VLM unless disabled |
| **Real-time alerting / verification** — VLM-verified incidents | `alerts.md` | `alert-bridge`, `vss-va-mcp`, `vss-video-analytics-api` | `alerts` | Real-time needs RT-VLM; CV-verification needs RT-CV + Behavior Analytics |
| **Video summarization** — time-windowed summaries on demand | `lvs.md` | `lvs-server` | `lvs` | Requires Agent + one reachable LLM + one VLM/RT-VLM |

**Always included — do not offer as choices:** VIOS video I/O + storage (`vios.md`) plus the shared `redis` cache peer that ships with the Foundation. **Added conditionally, never offered directly:** retain the HAProxy ingress (`ingress.md`) only with the Agent/UI tier or when the request explicitly asks for a unified browse/operate origin; otherwise prune `vss-haproxy-ingress` and create no ingress patch. The **ELK + Kafka broker / indexing stack** (`elk.md`) is pulled in **only** for capabilities that are Kafka-backed or Elasticsearch-indexed — Semantic search (`vss-search-analytics-2d-fusion` + `rtvi-embed`), Real-time alerting / verification (`alert-bridge` requires Kafka + Elasticsearch), or Video summarization when its Kafka/ES event or DB backend is enabled; RT-VLM adds Kafka when its resolved `RTVI_VLM_MESSAGE_BUS` is `kafka`. Kibana is not implied by selecting Elasticsearch: retain `kibana` and exactly the selected Foundation's initializer only when that Foundation already ships them, and never add or borrow Kibana keys for a Foundation that does not. A dense-captioning-only build that does not request message publication adds **no** ELK/Kafka and sets both `RTVI_VLM_MESSAGE_BUS=` and `RTVI_VLM_KAFKA_ENABLED=false` during the VSS Compose compatibility transition, preserving the smallest-delta contract. The LLM NIM (`llm-nim.md`) and VLM NIM (`vlm-nim.md`) model backends are likewise activated only when a selected capability needs a local model (integrated RT-VLM is the `rt-vlm.md` owner, not the VLM NIM backend).

Rules for the multi-select:
- **Offer exactly the table rows** whose owner contract exists under `references/services/` (all rows are present on this branch); show any pending capability disabled with a short "not yet available" note. **Never offer a foundational or model-backend owner as a choice** — do **not** silently offer a capability the skill cannot resolve.
- **Require at least one capability** — the foundational services alone are not a vision agent.
- Multiple selections compose in one deployment (e.g. captioning + alerting, or captioning + detection).

After Q2b, the selected capabilities **are** the required-capability set. Select the closest current developer profile as the **Foundation**, compute the **smallest delta** (add or remove only canonical service-profile keys, change only requested knobs), and continue at Step 2. This is **Delta mode** (per the Routing table); `_builds/<name>/` is created here.

## Steps

1. Detect the **entry mode** (see [Entry Mode (Step 0)](#entry-mode-step-0) above). Then parse the request and any eval specification into required capabilities, excluded capabilities, configuration knobs, and observable success checks. Custom build supplies the capability set directly via multi-select; Pre-built workflow keeps a named profile's authoritative service set unchanged (Stock mode).
2. Read the matching file under `references/profiles/` and `references/sizing.md`. In delta mode, compare all four developer profiles and select exactly one Foundation; ask only when two are equally plausible. `warehouse` never competes in that comparison — it is selected only by an explicit warehouse request. Read `references/edge.md` for DGX Spark or Thor.
3. Before resolution or deployment, run the applicable checks from `references/prerequisites.md`, `references/credentials.md`, and `references/ngc.md`. Read the environment and Brev references when applicable.
4. Read `references/composition.md` and only the capability-owner files under `references/services/` needed by the request.
5. Determine the effective service set. For an exact stock match, keep its authoritative set unchanged. Otherwise compute the smallest delta from the Foundation’s exact `COMPOSE_PROFILES`: add or remove only canonical service profile keys and change only requested environment knobs. If this single pass leaves a blocker the rules cannot settle (an unmapped or ambiguous capability, a Foundation tie, a singleton conflict, or a requested/excluded contradiction), apply the clarification gate in `references/composition.md`: ask one structured question, then resolve on the answer; never re-run the same resolution or guess past the blocker.
6. Before writing delta artifacts or starting a stock or delta deployment, present a compact architecture diagram in the conversation. Show the Foundation, added and removed capability owners and service keys, principal data flows and topics, external endpoints, and GPU/model placement. Do not save the diagram as a build artifact.
7. For every stock or delta build, write `_builds/<name>/override.env`, `_builds/<name>/compose.yml`, and `_builds/<name>/resolved.yml`. Implement the architecture preview's Foundation, effective service set, and changed knobs exactly; do not recompute or silently narrow the approved delta during artifact generation unless the user changes the request or resolution exposes a new blocker. Put the Foundation, the full effective `COMPOSE_PROFILES`, required build-local path/host values, and only environment values that are customized or transitively derived from a customization in `override.env`; do not copy unchanged Foundation defaults such as stock ports or model knobs. Make `compose.yml` include the root `deploy/docker/compose.yml` plus only minimal changed or new service Compose files, if any. Treat `<name>` only as a filesystem label; never add it to `COMPOSE_PROFILES`.
8. Generate `resolved.yml` with `docker compose config` using the ordered env layers in `references/composition.md` — or, for `warehouse`, the env layers and resolve pipeline in `references/profiles/warehouse.md` — normalize dangling optional dependencies with `scripts/normalize_resolved_yml.py`, then run the mandatory check/create gate in `references/data-directory.md` on every build, deploy or not — it **blocks** a `warehouse` build whose `${VSS_DATA_DIR}` is not the supplied app-data bundle — it prepares the external `${VSS_DATA_DIR}` any later bring-up needs (this agent's or a hand-run `docker compose up`) and never touches the repo tree. When the effective `COMPOSE_PROFILES` includes an RT-CV perception key (`perception-alerts`, `perception-2d-fusion`), no host-side or agent detector staging is required: the RT-CV container downloads the detector ONNX at first boot (ds-start phase 0) from its mounted `models-download.json` into the world-writable `${VSS_DATA_DIR}/models` the gate just created. Reject stale placeholders and invalid checked-in bind sources with `scripts/validate_resolved_yml.py`; if validation finds real unresolved `${...}` Compose interpolation, add only the missing concrete values to `override.env` and regenerate before proceeding. Do not count escaped container-shell variables such as `$${HOST_IP}` as unresolved Compose interpolation. Validate the selected keys, services, images, required peers, GPU placement, utilization, and requested success checks against that exact file.
9. If deployment was requested, deploy the exact `_builds/<name>/resolved.yml` validated in the previous step, refresh its registry images even when their tags already exist locally, use `references/readiness.md` with the matching profile checks, and follow `references/deployment.md` for the resolved-Compose lifecycle. When a source must be provisioned into the deployed build (a headless build registers none at bring-up), resolve the consumer ports and confirm the build is headless (no `vss-agent`) from `resolved.yml`, then follow `vss-manage-video-io-storage` `references/provision-vios-source.md` — **except for `warehouse`, which registers its own sources automatically.** When a search query round-trip is then requested against the deployed build, run `vss configure --base-url <build-origin>` (the fronting `http://$HOST_IP:$HAPROXY_HOST_PORT`) through the project-local entry point (`uv run --project <repo>/services/agent --no-dev vss …`, per `references/deployment_resolution.md`) — not a bare `vss` — then defer entirely to `vss-search-archive` for decomposition, mode, and the query itself. For stop or cleanup, follow `references/teardown.md`: remove project volumes by default and preserve model caches only when the user explicitly requests it.
