---
name: vss-build-vision-agent
description: Add agent-ready vision capabilities — dense captioning, detection, search, alerting, summarization — to an agent or application through a customizable, self-contained vision stack built on the NVIDIA VSS Blueprint. Use this skill when a developer or agent wants to give their app vision: pick capabilities via guided intake ("build a vision agent", "add vision capabilities") or describe them in natural language ("create a profile for streaming dense captioning", "add agentic search to my base deployment"). Route, compose, configure, and deploy stock base, alerts, LVS, or search developer profiles and lean custom combinations expressed as delta overlays using one current developer profile as the Foundation.
license: Apache-2.0
metadata:
  version: "3.2.0"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint orchestration deployment compose code-generation"
---

# Build Vision Agent

`build-vision-agent` gives agents and developers **agent-ready vision capabilities through a customizable, self-contained application stack** built on the **NVIDIA VSS Blueprint**. A developer or agent adds vision to their application by selecting the capabilities they want (guided intake) or describing them in natural language, and the skill routes to a validated developer profile — or composes the smallest delta overlay on top of one — and deploys it. Use it whenever the user wants vision capabilities composed for them: deploying a stock profile, extending a running deployment, or building a lean custom combination.

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
| Deploy capabilities that exactly match one current developer profile | Stock mode for the exact match. |
| Build, create, extend, customize, combine, add, or remove capabilities | Delta mode using the closest current developer profile as the Foundation. |
| Deploy capabilities with no exact match | Build the smallest delta, then deploy it. |
| Two Foundations have an equally small capability delta | Ask the user to choose between those Foundations. |
| Warehouse or another industry profile | Stop: this skill currently covers developer examples only. |
| Open / generic / "quickstart" intent with no named capability or profile | Guided front door (Q1): Pre-built workflow (Stock mode) or Custom build (Delta mode). |

## Entry Mode (Step 0)

Before routing, detect the **entry mode** — one of three: **Prompt-driven**, **Pre-built workflow**, or **Custom build**. All three share the same downstream machinery (profile catalog, Foundation selection, delta composition, resolution, and deployment); the mode only determines where the flow enters. **Pre-built workflow** is a true fast path — it deploys a validated developer profile as-is in Stock mode with **no delta and no `_build`** — while **Custom build** is a guided front door onto Delta mode.

### Step 0.0 — Entry-mode detection

Classify the request before any other work:

1. **A concrete capability, microservice, profile, or existing deployment is named** (e.g. "create a profile for streaming dense captioning", "add agentic search to my base deployment", "deploy the alerts profile") → **Prompt-driven**. Parse inputs and continue at Step 1.
2. **An open / generic / first-time / "quickstart" intent with no extractable capability** (e.g. "build a vision agent", "add vision capabilities", "help me get started", "just deploy something"), or no capability description at all → open the **guided front door** (Q1 below), which leads with **Pre-built workflow (the recommended default)** and offers **Custom build**.
3. **Ambiguous** → ask one disambiguating question, or default to the guided front door (it is safe, reversible, and explicit: the user makes selections before anything is generated or deployed). Never silently assume a capability or fall back to a default profile.

### Guided front door — Q1

Ask via `AskUserQuestion` (single-select). Generate or deploy **nothing** until the user selects AND confirms downstream (the deploy prompt for Pre-built workflow; the Step 6 architecture diagram for Custom build).

**Q1 — Starting point.** *"How would you like to start?"*

- **Deploy a pre-built workflow** *(recommended for a first run / quickstart)* — Choose from a ready-made, validated VSS developer profile. Fastest path to a running system; no composition needed. Deploys as-is; you can customize it afterward.
- **Build a custom configuration** — pick the specific vision capabilities you need and let the skill compose the smallest delta overlay for them.

### Mode: Pre-built workflow (quickstart)

The recommended first-run path. Deploys a validated upstream developer profile **as-is** via **Stock mode** — no delta, **no `_build`**, no invented flag, and no compose patching. Ask **Q2a (single-select): "Which pre-built workflow do you want to deploy?"** and map the choice to the developer profile:

| Option | Capability | Profile |
|---|---|---|
| **Base** | VLM dense captioning and Q&A | `base` |
| **Alerts** | VLM real-time alerting | `alerts` (`2d_vlm`) |
| **Alert Verification** | Object detection with analytics and VLM event contextualization | `alerts` (`2d_cv`) |
| **Video Summarization** | Time-windowed video summaries | `lvs` |
| **Search** | Object and video embeddings + agentic search | `search` |

> **Alerts vs. Alert Verification.** Both are modes of the single `alerts` developer profile, selected by the profile's `MODE` knob: `2d_vlm` (continuous RT-VLM inspection + real-time alert APIs) vs `2d_cv` (RT-CV detection + behavior analytics + VLM verification + incidents). Each has its own checked-in `COMPOSE_PROFILES` set in `dev-profile-alerts/overrides.env`, so selecting the mode is still a stock deployment, not a delta.

These are **predefined developer profiles**, so the skill does **not** compute a delta and does **not** create a `_build`. It: (1) maps the selection to the profile above; (2) runs the applicable prerequisite / credential / NGC checks (`references/prerequisites.md`, `references/credentials.md`, `references/ngc.md`); (3) deploys the profile **as-is** in **Stock mode** (per the Routing table) against the unmodified `deploy/docker/` tree; (4) runs the profile's readiness checks (`references/readiness.md`) and reports; (5) **offers to customize**.

**Customize a pre-built workflow → seed a Custom build.** After a pre-built deploy (or instead of deploying), offer: *"Want to customize this workflow? I'll use **<selected profile>** as the starting point."* On **yes**, transition into **Custom build**, seeding the selected profile as the **Foundation** for a delta (the profile itself is never modified — it is only the baseline). This is the **only** way the pre-built mode produces a `_build`: the user must explicitly choose to customize.

### Mode: Custom build (guided)

For a user who wants a specific composition. Reached from Q1 → Custom build, or by customizing a pre-built workflow (seeded with that profile as the Foundation). Ask **Q2b (multi-select): "Which vision capabilities do you want? (select all that apply)"** Each option maps to canonical service-profile keys owned by a capability owner under `references/services/`. **Foundational services — video I/O + storage and message bus + indexing — are always included**; present them as informational, not as choices. (When seeded from a pre-built workflow, that profile's capabilities are pre-checked.)

Offer the user **exactly** the capabilities in the table below. Each row's owner contract, canonical service-profile key(s), and closest Foundation profile are fixed — do not invent options or keys outside it.

| Option (shown to user) | Owner contract (`references/services/`) | Canonical service-profile key(s) | Closest Foundation | Peer notes |
|---|---|---|---|---|
| **Dense captioning** — natural-language descriptions of video | `rt-vlm.md` | `rtvi-vlm` | `base` | — |
| **Object detection & tracking (2D)** — bounding boxes, class labels, track IDs | `rt-cv.md` | `perception-2d-fusion` *(search)* / `perception-alerts` *(alerts)* | `search` | Kafka-backed; use the selected profile's key, not the shared `perception` extends source |
| **Semantic search over video** — embeddings + agentic search | `search.md` (+ `rt-embed.md`) | `vss-search-analytics-2d-fusion`, `rtvi-embed` | `search` | Requires RT-CV + RT-Embed + ELK; critique needs RT-VLM unless disabled |
| **Real-time alerting / verification** — VLM-verified incidents | `alerts.md` | `alert-bridge`, `vss-va-mcp`, `vss-video-analytics-api-alerts` | `alerts` | Real-time needs RT-VLM; CV-verification needs RT-CV + Behavior Analytics |
| **Video summarization** — time-windowed summaries on demand | `lvs.md` | `lvs-server` | `lvs` | Requires Agent + one reachable LLM + one VLM/RT-VLM |

**Always included — do not offer as choices:** VIOS video I/O + storage (`vios.md`) and ELK + Kafka + Redis message bus + indexing (`elk.md`). **Added automatically, never offered directly:** the LLM NIM (`llm-nim.md`) and VLM NIM (`vlm-nim.md`) model backends — activated only when a selected capability needs a local model (integrated RT-VLM is the `rt-vlm.md` owner, not the VLM NIM backend).

Rules for the multi-select:
- **Offer exactly the table rows** whose owner contract exists under `references/services/` (all rows are present on this branch); show any pending capability disabled with a short "not yet available" note. **Never offer a foundational or model-backend owner as a choice** — do **not** silently offer a capability the skill cannot resolve.
- **Require at least one capability** — the foundational services alone are not a vision agent.
- Multiple selections compose in one deployment (e.g. captioning + alerting, or captioning + detection).

After Q2b, the selected capabilities **are** the required-capability set. Select the closest current developer profile as the **Foundation**, compute the **smallest delta** (add or remove only canonical service-profile keys, change only requested knobs), and continue at Step 2. This is **Delta mode** (per the Routing table); `_builds/<name>/` is created here.

## Steps

1. Detect the **entry mode** (see [Entry Mode (Step 0)](#entry-mode-step-0) above). Then parse the request and any eval specification into required capabilities, excluded capabilities, configuration knobs, and observable success checks. Custom build supplies the capability set directly via multi-select; Pre-built workflow deploys a named profile as-is in Stock mode.
2. Read the matching file under `references/profiles/` and `references/sizing.md`. In delta mode, compare all four current profiles and select exactly one Foundation; ask only when two are equally plausible. Read `references/edge.md` for DGX Spark or Thor.
3. Before resolution or deployment, run the applicable checks from `references/prerequisites.md`, `references/credentials.md`, and `references/ngc.md`. Read the environment and Brev references when applicable.
4. Read `references/composition.md` and only the capability-owner files under `references/services/` needed by the request.
5. Determine the effective service set. For an exact stock match, keep its authoritative set unchanged. Otherwise compute the smallest delta from the Foundation’s exact `COMPOSE_PROFILES`: add or remove only canonical service profile keys and change only requested environment knobs.
6. Before writing delta artifacts or starting a stock or delta deployment, present a compact architecture diagram in the conversation. Show the Foundation, added and removed capability owners and service keys, principal data flows and topics, external endpoints, and GPU/model placement. Do not save the diagram as a build artifact.
7. For every stock or delta build, write `_builds/<name>/override.env`, `_builds/<name>/compose.yml`, and `_builds/<name>/resolved.yml`. Put the Foundation, the full effective `COMPOSE_PROFILES`, required build-local path/host values, and only environment values that are customized or transitively derived from a customization in `override.env`; do not copy unchanged Foundation defaults such as stock ports or model knobs. Make `compose.yml` include the root `deploy/docker/compose.yml` plus only minimal changed or new service Compose files, if any. Treat `<name>` only as a filesystem label; never add it to `COMPOSE_PROFILES`.
8. Generate `resolved.yml` with `docker compose config` using the ordered env layers in `references/composition.md`, normalize dangling optional dependencies with `scripts/normalize_resolved_yml.py`, then run the mandatory check/create gate in `references/data-directory.md`. Reject stale placeholders and invalid checked-in bind sources with `scripts/validate_resolved_yml.py`; if validation finds real unresolved `${...}` Compose interpolation, add only the missing concrete values to `override.env` and regenerate before proceeding. Do not count escaped container-shell variables such as `$${HOST_IP}` as unresolved Compose interpolation. Validate the selected keys, services, images, required peers, GPU placement, utilization, and requested success checks against that exact file.
9. If deployment was requested, deploy the exact `_builds/<name>/resolved.yml` validated in the previous step, refresh its registry images even when their tags already exist locally, use `references/readiness.md` with the matching profile checks, and follow `references/deployment.md` for the resolved-Compose lifecycle. For stop or cleanup, follow `references/teardown.md`: remove project volumes by default and preserve model caches only when the user explicitly requests it.
