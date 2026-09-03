---
name: vss-build-vision-ai
description: >-
  Add agent-ready vision capabilities — dense captioning, detection, search, alerting, summarization — to an agent or application through a customizable, self-contained vision stack built on the NVIDIA VSS Blueprint. Use this skill when a developer or agent wants to give their app vision: pick capabilities via guided intake ("build a vision agent", "add vision capabilities") or describe them in natural language ("create a profile for streaming dense captioning", "add agentic search to my base deployment", "deploy warehouse 3d"). Route, compose, configure, and deploy stock base, alerts, LVS, search developer profiles, or the warehouse industry profile and lean custom combinations expressed as delta overlays using one current developer profile as the Foundation. Not for operating a stack that is already deployed — searching, asking about a video, summarizing, managing alerts, or generating a report — and not for deploying a single microservice on its own; use the matching vss-* skill for those.
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
- [`references/agent-harness.md`](references/agent-harness.md) — the in-stack `vss-agent` and host-side NemoClaw harnesses, why at most one is deployed and what removing the agent costs, NemoClaw's default model provider, and its post-readiness bring-up.
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
| Drive the build from NemoClaw / OpenClaw / Hermes, a sandbox, or a chat UI instead of the in-stack agent | The NemoClaw harness (`references/agent-harness.md`): a host-side harness step after readiness, plus one removal from the service set — `vss-agent`, since exactly one harness is deployed. That removal makes it a Delta build. Never add a `nemoclaw` key to `COMPOSE_PROFILES`. |
| Install the harness against an already-deployed build (no composition requested) | `references/agent-harness.md` bring-up alone — resolve the origin from the running build, skip Steps 5–8. |
| Provision, register, or ingest a source (file or live stream) into a deployed build, or fan it out to consumers | `vss-manage-video-io-storage` `references/provision-vios-source.md` — headless, direct REST (resolve consumer ports from `resolved.yml`, confirm no `vss-agent`); not `vss-search-archive`. |
| Resolution leaves a blocker the rules cannot settle (unmapped or ambiguous capability, Foundation tie, singleton conflict, or requested/excluded contradiction) | Clarification gate (`references/composition.md`): after one deterministic pass, ask one structured question, then resolve on the answer. Never re-run the same resolution or guess past the blocker. |
| `smartcities` or another industry profile | Stop: `warehouse` is the only supported industry Foundation. |
| Open / generic / "quickstart" intent with no named capability or profile | Guided front door (Q1): Pre-built workflow (Stock mode) or Custom build (Delta mode). |

**Every "Stock mode" row above is conditional on [Q3](#harness-selection--q3).** Each of `base`, `alerts`, `lvs`, `search`, and `bp_wh` ships `vss-agent`, which Q3 removes on either answer — so a stock route that reaches Q3 becomes a **Delta build**. Stock survives only where the profile carries no agent (the warehouse Kafka, Redis, and minimal variants) or where the request names the in-stack agent and so skips Q3.

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

**All four then reach [Q3](#harness-selection--q3), which removes `vss-agent` on either answer and makes the build a Delta.** The quickstart is still the fast path — one removal, no added keys — but report it as a delta in the Step 6 diagram and the final summary rather than calling it a stock deploy. Keep it out of the Q3 question itself, per **Keep the question about the harness**. On `lvs` and `search`, a **no** is worth a sentence of its own: the Web UI reaches summarization and text search only through the agent, so with no harness those capabilities are `vss summarize` and `vss search` from the host, with the UI left as a dashboard.

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
at **Step 2** with `FOUNDATION=warehouse`.

Only `COMPOSE_PROFILES_WH_2D` (`bp_wh`) carries `vss-agent`; the Kafka, Redis,
and minimal variants ship agentless and so **skip [Q3](#harness-selection--q3)
entirely**. Where Q3 is asked, dropping that one key is the single edit
permitted to a warehouse variant list — everything else stays verbatim, and
Step 8 still resolves through `warehouse.md`. The shared lifecycle applies from
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
| **Video summarization** — time-windowed summaries on demand | `lvs.md` | `lvs-server` | `lvs` | Requires one reachable LLM + one VLM/RT-VLM; something must drive `/v1/summarize`, but no agent need be deployed |

**Always included — do not offer as choices:** VIOS video I/O + storage (`vios.md`), the HAProxy ingress (`ingress.md`) — providing a stable, unified interface to the VSS stack so agents and skills reference a single endpoint rather than per-service ports — plus the shared `redis` cache peer that ships with the Foundation. **Added conditionally, never offered directly:** the **ELK + Kafka broker / indexing stack** (`elk.md`) is pulled in **only** for capabilities that are Kafka-backed or Elasticsearch-indexed — Semantic search (`vss-search-analytics-2d-fusion` + `rtvi-embed`), Real-time alerting / verification (`alert-bridge` requires Kafka + Elasticsearch), or Video summarization when its Kafka/ES event or DB backend is enabled; RT-VLM adds Kafka **only** when `RTVI_VLM_KAFKA_ENABLED=true`. A dense-captioning-only build on `base` therefore adds **no** ELK/Kafka, preserving the smallest-delta contract. The LLM NIM (`llm-nim.md`) and VLM NIM (`vlm-nim.md`) model backends are likewise activated only when a selected capability needs a local model (integrated RT-VLM is the `rt-vlm.md` owner, not the VLM NIM backend).

Rules for the multi-select:
- **Offer exactly the table rows** whose owner contract exists under `references/services/` (all rows are present on this branch); show any pending capability disabled with a short "not yet available" note. **Never offer a foundational or model-backend owner as a choice** — do **not** silently offer a capability the skill cannot resolve.
- **Require at least one capability** — the foundational services alone are not a vision agent.
- Multiple selections compose in one deployment (e.g. captioning + alerting, or captioning + detection).

After Q2b, the selected capabilities **are** the required-capability set. Select the closest current developer profile as the **Foundation**, compute the **smallest delta** (add or remove only canonical service-profile keys, change only requested knobs), and continue at Step 2. This is **Delta mode** (per the Routing table); `_builds/<name>/` is created here.

### Harness selection — Q3

Applies to **every** entry mode — quickstart, warehouse, and custom build alike — and only when the request does not already name a harness. A harness is what a person or another agent talks to in order to drive the build; it is orthogonal to the capability set. Read [`references/agent-harness.md`](references/agent-harness.md) before offering this — it owns the contract.

**Ask Q3 exactly when the Foundation's service set carries `vss-agent`** — every developer profile does, as does warehouse `bp_wh`. Skip it entirely, with no question and no harness, when the set carries no agent: an ingest-, index-, or API-only build is legitimately headless, and Q3 would invent a requirement. When the user has already said "headless", that *is* the answer — do not re-ask.

**Q3 — Harness (yes/no).** *"Deploy an agent harness with this build?"*

| Answer | Harness | Effect on the build |
|---|---|---|
| **yes** *(default)* | `nemoclaw` | A host-side sandbox with the VSS skills installed drives the build over its public origin, and is its only conversational surface. |
| **no** | none | No harness at all. Drive the build with the `vss` CLI from the host. |

**Keep it a binary.** Do not present a menu of harnesses or ask which one to use — the only question is whether to deploy NemoClaw. **Yes is the default answer**: take it when the user defers or picks nothing. Still *ask*, because either answer changes the service set.

**Keep the question about the harness.** Word the prompt and both option labels around what the user ends up with — a sandbox chat surface, or the `vss` CLI on the host. Keep `vss-agent`, service keys, and the Stock/Delta vocabulary out of both: that is the skill's own bookkeeping, not a trade-off the user is being asked to weigh, and attaching it to the question makes a routine choice read as a warning. The removal and what it costs belong in the Step 6 architecture diagram and the final summary, where the answer is already known.

**`vss-agent` is removed on both answers.** The in-stack agent is deployed only when the request names it — "the chat agent", "the Web UI", "the agent REST API" — and such a request skips Q3 entirely, as one that names any harness does. Honour it when it comes; never steer it to NemoClaw.

Apply these on **either** answer:

- **Remove `vss-agent` and change no other key.** `vss-ui`, `phoenix`, and the `llm_*` peer stay; pruning them is a capability decision, not a harness one. `vss-ui` remains useful with no agent — its Alerts, Dashboard, and Video Management tabs address Alert Bridge, Kibana, and VST directly. Its dependency on the agent ships as `required: false` so the filtered project resolves; never re-add a hard `depends_on` in a build override.
- **Report what the removal costs**, in the build's own terms: the Web UI's chat (sidebar and tab) and Search tab stop answering, along with the ingress `/api`, `/chat`, `/websocket` routes; the Alerts tab's *Generate Report* action goes with them, since it drives the sidebar; search **ingestion and deletion** lose their agent lifecycle; `vss-generate-video-report-rag` becomes unavailable (route reports through `vss-generate-video-report`). On `lvs` that is the whole graphical surface for the profile's capability — the UI has no LVS client and reaches summarization only through the agent. Untouched: alerting, analytics, dashboards, ingest, and the CLI in full — no `vss` command group needs the agent, `search` included.
- **Provisioning moves to the headless path.** Use `vss-manage-video-io-storage` `references/provision-vios-source.md`: with no agent route its own gate passes, and it is the only path that fans a source into RT-CV and RT-Embed. Alert rules stay with `vss-manage-alerts`.
- Removing a service key makes it a **Delta build**, never a Stock deploy — on a no as much as a yes, and on a quickstart as much as a custom build.
- **A capability only the Agent owner serves contradicts a no.** Agentic natural-language decomposition (`/api/v1/search`) and `/generate` have no non-agent provider. When the request needs one, a no drops a requested capability: take it to the clarification gate rather than resolving it either way.

Apply these on a **yes** only:

- **Preflight the host before accepting the yes**, per the Prerequisites section of [`references/agent-harness.md`](references/agent-harness.md): `COMPATIBLE_API_KEY`, the NemoClaw CLI, and Python 3.11+. A missing piece is a **blocker at this step, not at Step 10** — name what is missing and ask whether to supply it, proceed with no harness, or name the in-stack agent instead. Deploy nothing until that is answered, and never substitute a harness silently.
- `vss-haproxy-ingress` is **required** — the sandbox reaches the build only through one origin, and there is no ingress-less host-CLI read path. NemoClaw paired with "no ingress" is a capability contradiction for the clarification gate, not something to settle by dropping a side.
- On `alerts`, a NemoClaw build **must** carry the curated `haproxy.cfg` that admits `host.openshell.internal`; do not reach the sandbox by repointing `EXTERNAL_IP`, which Alert Bridge uses to rewrite clip URLs and would leave alert evidence unopenable.

`nemoclaw` is a harness label, never a service: it must not appear in `COMPOSE_PROFILES`, `compose.yml`, or `patches/`.

## Steps

1. Detect the **entry mode** (see [Entry Mode (Step 0)](#entry-mode-step-0) above). Then parse the request and any eval specification into required capabilities, excluded capabilities, configuration knobs, the **harness** (see [Harness selection — Q3](#harness-selection--q3)), and observable success checks. Custom build supplies the capability set directly via multi-select; Pre-built workflow keeps a named profile's authoritative service set unchanged (Stock mode).
2. Read the matching file under `references/profiles/` and `references/sizing.md`. In delta mode, compare all four developer profiles and select exactly one Foundation; ask only when two are equally plausible. `warehouse` never competes in that comparison — it is selected only by an explicit warehouse request. Read `references/edge.md` for DGX Spark or Thor.
3. Before resolution or deployment, run the applicable checks from `references/prerequisites.md`, `references/credentials.md`, and `references/ngc.md`. When the harness is NemoClaw — including by default — add its host preflight from `references/agent-harness.md`; a missing token or CLI blocks here, while the build is still cheap to re-aim. Read the environment and Brev references when applicable.
4. Read `references/composition.md` and only the capability-owner files under `references/services/` needed by the request.
5. Determine the effective service set. For an exact stock match, keep its authoritative set unchanged. Otherwise compute the smallest delta from the Foundation’s exact `COMPOSE_PROFILES`: add or remove only canonical service profile keys and change only requested environment knobs. If this single pass leaves a blocker the rules cannot settle (an unmapped or ambiguous capability, a Foundation tie, a singleton conflict, or a requested/excluded contradiction), apply the clarification gate in `references/composition.md`: ask one structured question, then resolve on the answer; never re-run the same resolution or guess past the blocker.
6. Before writing delta artifacts or starting a stock or delta deployment, present a compact architecture diagram in the conversation. Show the Foundation, added and removed capability owners and service keys, principal data flows and topics, external endpoints, and GPU/model placement. Whenever Q3 was asked, show `vss-agent` as removed; on a yes, add NemoClaw as a host-side box outside the Compose project, reaching the build through the ingress origin. That diagram is the clearest place for the user to catch a harness they did not intend, or the loss of a surface they were relying on. Do not save the diagram as a build artifact.
7. For every stock or delta build, write `_builds/<name>/override.env`, `_builds/<name>/compose.yml`, and `_builds/<name>/resolved.yml`. Put the Foundation, the full effective `COMPOSE_PROFILES`, required build-local path/host values, and only environment values that are customized or transitively derived from a customization in `override.env`; do not copy unchanged Foundation defaults such as stock ports or model knobs. Make `compose.yml` include the root `deploy/docker/compose.yml` plus only minimal changed or new service Compose files, if any. Treat `<name>` only as a filesystem label; never add it to `COMPOSE_PROFILES`.
8. Generate `resolved.yml` with `docker compose config` using the ordered env layers in `references/composition.md` — or, for `warehouse`, the env layers and resolve pipeline in `references/profiles/warehouse.md` — normalize dangling optional dependencies with `scripts/normalize_resolved_yml.py`, then run the mandatory check/create gate in `references/data-directory.md` on every build, deploy or not — it **blocks** a `warehouse` build whose `${VSS_DATA_DIR}` is not the supplied app-data bundle — it prepares the external `${VSS_DATA_DIR}` any later bring-up needs (this agent's or a hand-run `docker compose up`) and never touches the repo tree. When the effective `COMPOSE_PROFILES` includes an RT-CV perception key (`perception-alerts`, `perception-2d-fusion`), no host-side or agent detector staging is required: the RT-CV container downloads the detector ONNX at first boot (ds-start phase 0) from its mounted `models-download.json` into the world-writable `${VSS_DATA_DIR}/models` the gate just created. Reject stale placeholders and invalid checked-in bind sources with `scripts/validate_resolved_yml.py`; if validation finds real unresolved `${...}` Compose interpolation, add only the missing concrete values to `override.env` and regenerate before proceeding. Do not count escaped container-shell variables such as `$${HOST_IP}` as unresolved Compose interpolation. Validate the selected keys, services, images, required peers, GPU placement, utilization, and requested success checks against that exact file.
9. If deployment was requested, deploy the exact `_builds/<name>/resolved.yml` validated in the previous step, refresh its registry images even when their tags already exist locally, use `references/readiness.md` with the matching profile checks, and follow `references/deployment.md` for the resolved-Compose lifecycle. When a source must be provisioned into the deployed build (a build with no agent registers none at bring-up), resolve the consumer ports and confirm `resolved.yml` carries no `vss-agent` — true of every build that reached Q3 — then follow `vss-manage-video-io-storage` `references/provision-vios-source.md` — **except for `warehouse`, which registers its own sources automatically.** When a search query round-trip is then requested against the deployed build, run `vss configure --base-url <build-origin>` (the fronting `http://$HOST_IP:$HAPROXY_HOST_PORT`) through the project-local entry point (`uv run --project <repo>/services/agent --no-dev vss …`, per `references/deployment_resolution.md`) — not a bare `vss` — then defer entirely to `vss-search-archive` for decomposition, mode, and the query itself. For stop or cleanup, follow `references/teardown.md`: remove project volumes by default and preserve model caches only when the user explicitly requests it.
10. When the harness is NemoClaw, bring it up **after** the readiness gate passes, per `references/agent-harness.md`: resolve the deployed origin, then execute the checked-in `deploy/docker/scripts/deploy_nemoclaw.ipynb` through `deploy/docker/scripts/run_setup_notebook.py`. That notebook is the single source of host-side harness logic — never reimplement its onboarding, policy, skill-install, or workspace steps, and never hand-run the NemoClaw CLI in its place. Pass `NEMOCLAW_RECREATE_SANDBOX=0` unless the user asked to rebuild the harness; the notebook's own default discards the sandbox and every agent session in it. For the harness's own LLM, default to notebook option (a) — the remote OpenAI-compatible endpoint (`NEMOCLAW_PROVIDER=custom`) serving Claude Opus — and do not ask; override to a local or other model only on an explicit request. A missing `COMPATIBLE_API_KEY` should have blocked at Step 3; reaching here without one is still a blocker to report, never grounds to silently substitute build.nvidia.com or a local model. In the final summary, report the Agent UI the notebook prints as a **markdown link** rather than a bare URL — descriptive link text, target copied verbatim including the `#token=` fragment, per the exact form in `references/agent-harness.md`. Harness and build are independent lifecycles: `references/teardown.md` removes the Compose project only, and destroying the sandbox is the separate command in `references/agent-harness.md`.
