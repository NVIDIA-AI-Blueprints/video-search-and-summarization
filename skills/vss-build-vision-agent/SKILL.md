---
name: vss-build-vision-agent
description: >
  Compose VSS-based agent deployments from a natural-language capability description.
  Use this skill when the user asks for a new VSS profile or extension to an existing
  one (e.g. "create a profile for streaming dense captioning", "add agentic search to
  my base deployment", "integrate my third-party camera system with VSS"). The skill
  reads per-microservice reference files (`integrate-{microservice}.md`,
  `deploy-{microservice}.md`) as ground truth, invents a unique compose-profile flag
  per generation, patches local copies of the relevant upstream service composes
  (never upstream itself), and outputs a validated, self-contained Docker Compose
  deployment under `_builds/{build-name}/` (at the repository root) along with a
  generated per-deployment deploy skill.
license: Apache-2.0
metadata:
  version: "3.2.0"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint orchestration deployment compose code-generation"
---

# Build Vision Agent

> Source: [NVIDIA-AI-Blueprints/video-search-and-summarization](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization)

`build-vision-agent` is the orchestration skill that takes a natural-language capability description (and optionally an existing deployment to extend) and produces a validated Docker Compose file by reading authoritative per-microservice reference files. Use it whenever the user wants a VSS deployment composed for them — net-new profiles, extending a running stack, integrating a third-party system, or merging two profiles.

For Phase 1a (v0.1) the skill supports **IN-1 — streaming and on-demand video dense captioning**, which combines VIOS + RT-VLM + ELK. IN-2 (RT-CV + RT-DETR person detection) and the broader catalog land in subsequent phases. The skill itself does not need updates as new microservices are added — only `references/microservice-catalog.md` and the per-service `integrate-*.md` / `deploy-*.md` files.

## When to Use

- **Net-new profile**: "Create a profile for streaming and on-demand dense captioning"
- **Extension**: "Add agentic video search to my current base deployment at `./compose.yml`"
- **3P integration**: "Integrate my existing camera management system (compose at `./camera-mgmt/compose.yml`) with VSS"
- **Profile combination**: "Combine the Search Profile and Alerts Profile"
- **Helm output (post-v1)**: "Convert my dev-profile-alerts compose to a Helm chart"

If the user asks to **deploy** a generated compose, the skill will create (or update) a per-deployment deploy skill in Step 6 and prompt to invoke it in Step 8 — see those steps below. If the user asks to **call** a service's API (RT-VLM endpoints, VIOS endpoints, etc.), hand off to the relevant upstream skill (`vss-deploy-dense-captioning`, `vss-manage-video-io-storage`, `vss-setup-video-analytics-api`, etc.) — those are bundled into `<BUILD_DIR>/skills/` in Step 6.

> **`<BUILD_DIR>` convention.** All generated assets land under a single per-generation build directory chosen in Step 0. The default location is `_builds/<build-name>/` at the repository root (where `_builds/` is `.gitignore`d); the user can override at Step 0 (new under `_builds/`, overwrite an existing build folder, or supply a custom path). Throughout the rest of this document, paths written as `<BUILD_DIR>/compose.yml`, `<BUILD_DIR>/.env`, `<BUILD_DIR>/patched/`, etc. refer to file paths INSIDE that chosen directory. (Earlier revisions of this skill emitted to `skills/vss-build-vision-agent/build-output/`; any `build-output/` references that remain in prose below should be read as `<BUILD_DIR>/`.)

## How it Works

The skill executes nine steps. Steps 0–4 are read-only / interactive; steps 5–8 produce output.

```
Step 0:   Parse inputs and clarify (enumerate ALL .env files in repo)
Step 1:   Capability → microservice mapping (catalog lookup)
Step 2:   Read integrate-<microservice>.md for each candidate
Step 3:   Conflict detection (ports, shared infra, GPU contention)
Step 4:   Architecture proposal + interactive decisions (GPU, shared infra, models)
Step 5:   Read deploy-<microservice>.md for selected services
Step 6:   Generate compose artifact + bundle related skills + create/update per-deployment deploy skill
Step 6.5: Apply standalone-compose patches (insert new gating flag into patched copies + strip undefined depends_on)
Step 7:   Dry-run validation (no real unexpanded ${...} tokens — exclude $$ escapes)
Step 8:   Review + write output + prompt to deploy
```

Each step is detailed below.

### Step 0 — Parse Inputs and Clarify

Read the user's prompt. Identify:

- **Capability description** — the verb-and-noun phrase describing what the user wants (e.g., "streaming dense captioning", "person counting", "agentic search").
- **Existing deployment** (optional) — path to a Docker Compose file or Helm chart to extend or merge with. If the user provided one, parse it and inventory existing services, images, ports, volumes, and shared infrastructure.
- **Third-party descriptor** (optional) — API base URL, OpenAPI / JSON schema file path, Kafka broker address and topic list, service / DB endpoint list, message bus type. Indicates a 3P integration scenario.
- **Output target** — `compose` (default) or `helm` (post-v1; report as not-yet-supported if requested).
- **Output path (`<BUILD_DIR>`)** — captured via the interactive prompt below; do NOT silently default to a fixed path. See `#### Choose the build directory (`<BUILD_DIR>`)` immediately below for the three-option prompt and resolution rules.

#### Choose the build directory (`<BUILD_DIR>`)

All generated assets land under a single per-generation directory referred to throughout this document as `<BUILD_DIR>`. The canonical home for builds is `_builds/` at the repository root (gitignored). Before doing any other Step 0 work, list `_builds/` and prompt the user with three options:

```
Where should generated assets go?
  (a) New build folder under _builds/  [default]
  (b) Overwrite an existing build folder under _builds/
  (c) Custom path (anywhere on disk)

Existing builds under _builds/:
  - <existing-folder-1>/   (last modified <ts>, profile <flag>)
  - <existing-folder-2>/   (last modified <ts>, profile <flag>)
  (none yet)
```

Resolve based on the user's choice:

- **(a) New build under `_builds/`** (default when the user does not specify). Resolve `<BUILD_DIR> = <repo-root>/_builds/<build-name>/`. The `<build-name>` is **deferred** to Step 6, where it is derived from the invented compose-profile flag — strip the `bp_developer_` prefix and replace remaining underscores with hyphens (e.g. `bp_developer_in_1` → `in-1`, so `<BUILD_DIR> = <repo-root>/_builds/in-1/`). If a folder with that name already exists under `_builds/`, **stop and confirm with the user** — do not silently overwrite. Offer to append a suffix (`in-1-2`, `in-1-3`, ...) or to switch to option (b). In autonomous mode, auto-append the next suffix.
- **(b) Overwrite an existing build folder.** Show the list of existing `_builds/*/` folders with their last-modified timestamp and the compose-profile flag from each folder's `MANIFEST.md` (if present). Ask which to overwrite. Confirm the choice and warn that the existing contents will be replaced — note that Docker volumes and model caches (`mdx_rtvi-hf-cache`, `mdx_rtvi-ngc-model-cache`, `mdx_cosmos_reason2_8b_cache`, etc.) survive because they are managed by Docker, not bind-mounted into `<BUILD_DIR>`. Resolve `<BUILD_DIR>` to the chosen path.
- **(c) Custom path.** Accept any absolute path, or a path relative to the repository root (resolve relative paths against `<repo-root>`). Validate that the parent directory exists and is writable; refuse to write to a path inside `deploy/docker/` (would risk modifying upstream composes — see `[[build-output-self-contained]]`). If the path already exists and contains a previous build (i.e. has a `compose.yml`), confirm overwrite the same way option (b) does.

After resolving, record `<BUILD_DIR>` in the in-session context. Every subsequent step writes only inside this directory. Step 6 also writes `<BUILD_DIR>/MANIFEST.md` recording the resolution choice (which option was picked, the resolved absolute path, and the compose-profile flag) so a future re-invocation against option (b) can identify the prior generation.

> *Autonomous mode:* if the user's request says "deploy autonomously" or the skill is running in a non-interactive eval harness, **default to option (a)** and let Step 6 derive the `<build-name>` from the invented flag. Auto-append a numeric suffix on collision instead of asking.

#### Enumerate ALL `.env` files in the source repo

VSS spreads its environment configuration across **multiple `.env` files** by concern. A skill that reads only the per-profile `dev-profile-*/.env` will miss component-internal variables and fail dry-run with errors like `invalid spec: :/home/vst/vst_release/streamer_videos: empty section between colons` (caused by an unset `${CLIP_STORAGE_PATH}` collapsing the host portion of a volume mount).

Run a recursive `.env` discovery against the source repo:

```bash
find <repo>/deploy -type f -name '.env' -not -path '*/_builds/*' -not -path '*/build-output/*' | sort
```

The canonical set is **10 core `.env` files** (4 developer profiles, 1 industry profile, 5 service-internal) plus a NIM hardware-tier set selected by `HW_PROFILE`. Read the full enumeration, per-file ownership table, NIM hardware-tier layout, and the variable-folding rule for Step 6 in `references/env-file-enumeration.md`.

If any of the following is unclear and the answer materially changes the architecture, **stop and ask** before proceeding:

- The capability description maps to multiple microservice candidates and the user has not narrowed it.
- The user has not said whether this is net-new or an extension of an existing deployment.
- The user wants a feature that requires a microservice not in `references/microservice-catalog.md`.

Do NOT silently fall back to a default profile when the user's intent is ambiguous.

### Step 1 — Capability → Microservice Mapping

Open `references/microservice-catalog.md`. Match the user's capability description against the **Capability tags** column. For each candidate microservice in the catalog:

- Check whether its required peer services (per its `integrate-<microservice>.md`) can be satisfied either by services already present in the user's existing deployment, or by services already in the candidate set.
- Mark the candidate as `reuse` (already in source deployment), `add` (must be brought up), or `unsatisfiable` (required peer missing and not addable from the catalog).

If a requested capability has no matching microservice in the catalog, report the gap to the user (NFR-6) and stop. Do NOT generate a partial compose with hallucinated services.

For IN-1 specifically:
- "Streaming dense captioning" → RT-VLM (carries `dense-captioning`, `streaming-inference`)
- "On-demand dense captioning" → RT-VLM (carries `on-demand-inference`)
- "Kafka publication" → covered by RT-VLM's Kafka outputs in its `integrate-rt-vlm.md`
- "Stored in Elasticsearch" → ELK (carries `caption-storage`, `kafka-ingestion`)
- "Video source" (RTSP and uploaded files) → VIOS (carries `rtsp-ingestion`, `video-upload`)

### Step 2 — Read `integrate-<microservice>.md` for Each Candidate

For each selected service, read its `integrate-<microservice>.md` from `skills/<skill-folder>/references/`. Extract:

- **Required peer services (prose)** — confirm each is satisfied (see Step 1).
- **`component_services:` block** — the structured YAML inside `§ Required Peer Services` listing the upstream compose service-keys this microservice owns. The block has two parts:
  - `always:` — service-keys unconditionally added when this microservice is selected.
  - `variants:` — named decisions (e.g., `sensor_topology`, `vlm_backend`) the skill must resolve in Step 4 before producing the per-generation allow-list. Each variant has a `prompt:`, a `default:`, and a list of `options:` (each with a `when:` hint matching user-intent shapes, plus an `add:` list of service-keys to commit if that option is picked).
- **Inputs and Outputs** — Kafka topics, REST endpoints, file paths, schema references.
- **Environment variables** — note required vs. optional and their compose-side rewrites (e.g., `RTVI_VLM_KAFKA_TOPIC` → `KAFKA_TOPIC`).
- **Network requirements** — `network_mode`, port exposures, DNS expectations.
- **Known integration constraints** — startup ordering, single-instance restrictions, schema-version pinning.

Cite the specific section you relied on for each architectural decision (NFR-5). The architecture proposal in Step 4 must reference these citations.

### Step 3 — Conflict Detection

When extending an existing deployment or merging multiple sources, detect:

- **Port conflicts** — two services bound to the same host port (especially under `network_mode: host`, where conflicts are immediate failures).
- **Duplicate infrastructure** — multiple Elasticsearch / Kafka / Redis instances. The default is to consolidate to one shared instance; deviate only when the user has explicitly asked for isolation.
- **GPU contention** — multiple GPU-reserving services sharing a single GPU when the host has only one. Flag for Step 4 decision.
- **Service-name collisions** — same `container_name` across input composes. Resolve by renaming or by treating the second as a replacement.
- **Schema mismatches** — two services agreeing on a Kafka topic name but disagreeing on payload schema (especially relevant for 3P integrations).

Surface every detected conflict in the Step 4 proposal. Do not silently resolve.

### Step 4 — Architecture Proposal and Interactive Decisions

Present a structured proposal to the user before generating any output. Required sections:

- **Services to add** (with the specific reference-file section that motivated each).
- **Services to reuse** from the existing deployment (when extending).
- **Connections to establish** — Kafka topic wirings, REST URLs, shared volume mounts, network bridges.
- **Shared infrastructure strategy** — single vs. isolated Kafka / Elasticsearch / Redis (default: shared).
- **Conflicts and proposed resolutions** from Step 3.
- **Gaps** — required peer services or interfaces that cannot be satisfied (Step 1 result).
- **Architecture diagram** — an ASCII flowchart rendering the proposal visually. See the sub-section below.

#### Architecture diagram (ASCII)

Render the proposal as an ASCII flowchart (Unicode box-drawing, top-to-bottom layers) so the user can SEE the wiring, not just read it. The diagram is text-based, displays inline in the terminal at Step 4, renders identically in any Markdown viewer over SSH, and persists losslessly in `<BUILD_DIR>/MANIFEST.md`.

Required content (one node per allow-listed service grouped by logical layer; one labeled edge per connection from the integrate refs' `§ Integration Interfaces`; external actors as top-level nodes; deployment shape in a comment), the canonical IN-1 example to use as a template, and the multi-diagram split rule (>~30 nodes) are spelled out in `references/architecture-diagram-template.md`.

Step 6 MUST embed this same diagram verbatim in `<BUILD_DIR>/MANIFEST.md § Architecture` so the operator (and any future regeneration / re-deploy) has a permanent record. Do NOT regenerate the diagram in Step 6 — copy the Step 4 output verbatim.

Then prompt the user for any of the following that are ambiguous (FR-4):

- **`component_services:` variant resolutions.** For each `variants:` block surfaced by Step 2 (schema in `references/component-services-schema.md`), present the variant's selector key (e.g. `sensor_topology`) and the list of `cases:` keys (e.g. `rtsp-and-uploaded`, `warehouse-2d`, `warehouse-3d`, `warehouse-mv3dt`). Use the user's prompt language to pre-suggest a default, then ask explicitly when the choice is non-obvious. The chosen case-name is the `deployment_shape:` written to the sidecar in the next section. Common cases:
  - VIOS `sensor_topology` — `rtsp-and-uploaded` vs. `warehouse-2d` / `warehouse-3d` / `warehouse-mv3dt`.
  - RT-VLM `vlm_backend` (when the integrate doc declares one) — in-process vs. one of the sibling NIMs.
- **GPU assignment** — which physical GPU index each GPU-requiring service should land on. Use `RT_VLM_DEVICE_ID`, `RT_CV_DEVICE_ID`, etc., names from the source compose.
- **Shared vs. isolated infrastructure** — when the user supplied a source compose with its own Kafka / ES, ask explicitly.
- **Endpoint conflicts** — when port collisions cannot be resolved automatically.
- **Model selection** — when multiple VLM / LLM options are compatible.
- **Remote vs. local inference** — for NIM-based services (RT-VLM in `openai-compat` mode, LLM NIMs).
- **External RTSP source location** (when the prompt mentions live stream input) — is the source a public RTSP server, a sibling container, or a host process? Pre-flight reachability **from inside the rtvi-vlm container** (not just the host) before generating the compose. If the source is a non-VSS sidecar, recommend co-locating on the same compose network with `--network-alias` (see `integrate-rt-vlm.md` § Network Requirements > Reaching external RTSP sources). If the source is on the host, verify Docker's iptables FORWARD chain has the necessary rule by probing `docker exec rtvi-vlm bash -c "exec 3<>/dev/tcp/${HOST_IP}/${RTSP_PORT}"`.

Wait for confirmation before continuing. The only exception is **autonomous mode** — when the user's request explicitly says "deploy autonomously" or "run without confirmation", or when running inside a non-interactive eval harness with that permission.

#### Step 4 output — per-generation allow-list sidecar (`build-output/allow-list.yml`)

Once the user confirms the architecture, synthesize a flat allow-list of upstream compose service-keys by **unioning the `component_services:` blocks** of every microservice in the proposal, **resolving each `variants:` block against the user's chosen `deployment_shape`**, and dropping any entry whose `required: false` is excluded by the architecture (e.g. an optional MQTT broker that the user opted out of).

Write the result to `<BUILD_DIR>/allow-list.yml`. This sidecar is the **only** input Step 6.5 reads — the catalog, the per-microservice integrate files, and `SKILL.md` itself are NOT re-parsed at patch time. Persist the sidecar before invoking Step 6, which expects the flag chosen here to be reused.

The sidecar schema, a worked IN-1 example, and the union rules (per-microservice contribution; variant case selection; dedup of identical `(key, file)` pairs; catalog-inconsistency error on conflicting `file:` paths) live in `references/allow-list-sidecar.md`. The full schema for `component_services:` blocks is in `references/component-services-schema.md`.

### Step 5 — Read `deploy-<microservice>.md` for Each Selected Service

For each service the user confirmed in Step 4, read its `deploy-<microservice>.md`. Extract:

- **Container image and tag pattern** — multiarch tag selection (`3.1.0` vs. `3.1.0-sbsa`) based on the host's architecture.
- **GPU requirements** — minimum VRAM, `device_ids` reservation block, `runtime: nvidia` requirement, `NVIDIA_VISIBLE_DEVICES`.
- **Storage** — required bind mounts and named volumes, with size estimates and required permissions (`chmod 777` patterns, no recursive `chown`).
- **Startup behavior** — `depends_on` conditions, healthcheck endpoint and tuning, `start_period` (especially RT-VLM's `1200s` cold-boot window).
- **Prerequisites** — NGC API key, HF token, NVIDIA Container Toolkit, free ports, outbound network requirements.

Validate that the host's GPU configuration (gathered in Step 0 if the user provided it, or queried interactively) satisfies the per-service VRAM and architecture requirements. If not, return to Step 4 to renegotiate.

### Step 6 — Generate the Compose Artifact

Write the compose file following VSS dev-profile conventions:

- **Top-level `compose.yml`** with `include:` directives pointing to per-profile subdirectories (the existing `dev-profile-base/compose.yml`, `dev-profile-search/compose.yml`, etc., pattern).
- **Environment variable substitution** for all secrets, API keys, and host-specific values. Use `${VAR_NAME}` everywhere; emit a corresponding `.env.template` in the same output directory listing every variable with comments describing purpose and required values.
- **GPU device reservations** using `deploy.resources.reservations.devices` with explicit `device_ids` from Step 4.
- **Health checks** for every service that exposes an HTTP endpoint, copied from the per-service `deploy-<microservice>.md` (do not invent — use the exact compose values).
- **`restart` policy** — match the source compose's pattern. VSS conventions: `restart: always` for persistent services, `restart: on-failure` for one-shot init containers, `restart: unless-stopped` where the source uses it.
- **`depends_on:` blocks** with explicit `condition` values from the per-service references (`service_healthy`, `service_started`, `service_completed_successfully`).
- **Compose-profile gating — invent a new flag; patch only build-output copies.** Assign the deployment a unique blueprint profile name following the catalog convention (`bp_developer_in_<N>`, `bp_developer_an_<N>`, or `bp_developer_at_<N>` per the active IN-/AN-/AT- entry in `INTEGRATION-PLAN.md` § Profile Catalog). The flag is **invented for this generation only** — it need not exist anywhere upstream, and upstream service composes are never modified. Step 6.5 copies each involved upstream compose into `build-output/patched/` and adds the new flag to every relevant service's `profiles:` list in those local copies (additive — existing upstream flags like `bp_developer_alerts_2d_vlm`, `bp_developer_search_2d`, `bp_wh_*` stay). The emitted `build-output/compose.yml` `include:`s the patched copies, so `docker compose --env-file build-output/.env -f build-output/compose.yml --profile <new-flag> up -d` deploys against the build-output tree without ever touching the upstream repo. For reference only, upstream's currently-declared flags are: developer (`bp_developer_base_2d`, `bp_developer_search_2d`, `bp_developer_alerts_2d_vlm`, `bp_developer_alerts_2d_cv`, `bp_developer_lvs_2d`, plus `*_IGX-THOR` / `*_AGX-THOR` variants) and warehouse-industry (`bp_wh_{2d,kafka,redis,auto_calib}_*`); inventing a fresh flag avoids colliding with any of them.

For Helm output (post-v1, not implemented in v0.1): generate one Deployment / StatefulSet per service, one Service manifest per service, GPU resource requests parameterized in `values.yaml`, secrets in Secret manifests, all other config in ConfigMaps, with VSS labeling conventions (`app.kubernetes.io/part-of: vss`).

#### Bundle related skills

After writing the compose artifact, copy the skill folders the operator will need to interact with this deployment into `build-output/skills/`. Scope is **only what already exists** in the VSS repo's skills folder — do NOT synthesize a new use-case skill at this step.

What to bundle:

- **Microservice skills**: for each service selected in Step 4, look up the canonical skill folder name from `references/microservice-catalog.md` and copy `<vss-repo>/skills/<skill-name>/` → `build-output/skills/<skill-name>/`. IN-1 bundles `vss-manage-video-io-storage/`, `vss-deploy-dense-captioning/`, and the ELK references (carried inside `vss-build-vision-agent/references/`).
- **Use-case skills**: scan `<vss-repo>/skills/` for top-level skill folders whose `description:` frontmatter matches the capability description from Step 0 (e.g., `streaming-dense-captioning`, `agentic-search`, `person-counting`). Copy each match. **If none match, skip — do not create one.**

Copy the entire skill folder verbatim (including `SKILL.md`, `references/`, `scripts/`, `eval/`). Do not edit any bundled file. Record every bundled skill in `MANIFEST.md` with its source path and a one-line purpose.

#### Create or update the per-deployment deploy skill

Generate a self-contained deploy skill at `build-output/skills/deploy-<profile-name>/SKILL.md` that hardcodes the exact paths and values for this deployment. The `<profile-name>` is derived from the invented flag in Step 6 by stripping the `bp_developer_` prefix and replacing any remaining underscores with hyphens: `bp_developer_in_1` → `deploy-in-1`, `bp_developer_an_1` → `deploy-an-1`, `bp_developer_at_1` → `deploy-at-1`.

The generated SKILL.md must include:

- **Compose path** — absolute or `build-output/`-relative path to the generated `compose.yml`.
- **Env file path** — path to `.env.template` and an instruction to copy it to `.env` and fill in every variable before deploy.
- **GPU assignments** — the device-id map confirmed in Step 4 (`RT_VLM_DEVICE_ID=0`, etc.), so the operator can sanity-check against the host before bring-up.
- **Per-service health endpoints + `start_period`** — copied from each `deploy-<microservice>.md`. RT-VLM's `1200s` cold-boot window must be called out explicitly.
- **Bring-up command** — the exact `docker compose --env-file build-output/.env -f build-output/compose.yml --profile <profile-name> up -d` invocation.
- **Health-check loop** — poll each service's healthcheck endpoint until pass or per-service `start_period` timeout; fail loudly with the specific service name when a check times out.
- **Tear-down command** — `docker compose --env-file build-output/.env -f build-output/compose.yml --profile <profile-name> down -v` (note: `-v` removes named volumes; warn the operator inline).
- **Post-deploy smoke test** — one curl or kafka-console-consumer command per "Outputs" section in the bundled microservice skills' `integrate-<microservice>.md`, so the operator can confirm the wiring actually works.

If a deploy skill already exists at `build-output/skills/deploy-<profile-name>/SKILL.md` (the user is regenerating the same profile), **overwrite it** with the new values. Do not append — stale GPU assignments or stale env paths from a prior run would silently misdirect deploy.

Record the generated deploy skill in `MANIFEST.md` with the bring-up and tear-down commands inline so an operator can read the manifest and execute without opening the skill.

#### Output layout after Step 6

```
build-output/
├── compose.yml
├── .env.template
├── allow-list.yml                      # Step 4 output — per-generation flag + service-key union; Step 6.5 reads this
├── MANIFEST.md
├── patched/                            # Step 6.5 outputs (compose copies with flag inserted + depends_on stripped)
└── skills/
    ├── vss-manage-video-io-storage/    # bundled from <vss-repo>/skills/vss-manage-video-io-storage/
    ├── vss-deploy-dense-captioning/    # bundled from <vss-repo>/skills/vss-deploy-dense-captioning/
    ├── <use-case-skill>/               # bundled IF one matched the capability description; skipped otherwise
    └── deploy-<flag-slug>/
        └── SKILL.md                    # generated; overwritten on re-run
```

### Step 6.5 — Apply Standalone-Compose Patches

The build-output deploys a unique, never-before-seen profile generated by the skill. To make that work against the upstream's existing compose tree **without modifying upstream files**, the skill copies the involved upstream service composes into `<BUILD_DIR>/patched/` and applies four patches:

- **Patch 0** — pre-flight host preparation (run at deploy time): validate `.env` secrets, create bind-mount dirs with permissions, clear conflicting named volumes, kill orphan containers from prior generations, NGC login, profile-flag collision check.
- **Patch 1** — insert the invented gating flag into the `profiles:` list of every `(key, file)` pair in `<BUILD_DIR>/allow-list.yml`. Additive (preserves upstream flags); each sidecar entry is patched at exactly one site. Handles both inline and block-style `profiles:` lists.
- **Patch 2** — strip undefined `depends_on` entries. Compose ≥ v2.36 rejects standalone projects with unresolvable `depends_on` even when `required: false`. For each allow-listed service, walk its `depends_on:` — keep defined peers, strip undefined peers with `required: false`, error on undefined peers without `required: false` (allow-list/upstream inconsistency).
- **Patch 3** — materialize relative-path bind-mount source files. The patched tree under `<BUILD_DIR>/patched/` contains only the patched YAML; Docker would silently create empty directories for `./envoy.yaml`, `./sdr-config`, etc., causing obscure container exits. Walk every patched compose's `volumes:` and `cp -r` upstream sources into the patched copy. Sub-case: SDRC config templates (`config.yml.tmpl` + `docker_cluster_config-streamprocessing.json.tmpl`) are env-var-resolved (not relative), so handle them explicitly when `sdr-controller` is in the allow-list.

The full Patch 0 pre-flight checklist, the Patch 1 / Patch 2 / Patch 3 pseudocode and rationale, the "why an allow-list, not patch-then-exclude" architectural note, the VIOS + SDRC mandatory-stack note, and the per-allow-listed-service IN-1 behavior breakdown all live in `references/standalone-compose-patches.md`. Read it before modifying any patch logic — every entry there is grounded in a live deploy failure mode.

Record the chosen flag at the top of `<BUILD_DIR>/MANIFEST.md`, note every stripped `depends_on` entry in `MANIFEST.md`, and add a row to `PATCHES.md` for every materialized file/directory so the operator can audit.

### Step 7 — Dry-Run Validation

After writing, validate before declaring success. The dry-run command depends on whether the source repo splits env vars across multiple files (Step 0):

```bash
# If single combined env produced in Step 6:
docker compose --env-file .env -f compose.yml config > resolved.yml

# If layering multiple env files (preferred when component .envs are kept separate):
docker compose --env-file .env --env-file <repo>/deploy/docker/services/vios/vst.env \
  -f compose.yml config > resolved.yml
```

Then check there are no **real** unexpanded `${...}` tokens. Compose intentionally preserves `$${...}` (double-dollar) — these are escape sequences that pass `${...}` through to the container's shell at runtime — so a naive `grep '\${'` produces false positives. Match `${` only when **not** preceded by another `$`:

```bash
# Real unexpanded tokens: ${...} not preceded by another $
if grep -nE '(^|[^$])\${[A-Za-z_]' resolved.yml; then
  echo "FAIL: resolved.yml has real unexpanded variables (above)"
  exit 1
fi
echo "PASS — no real unexpanded tokens"
```

Real unexpanded tokens indicate either a missing env entry or a typo. Either fix the `.env` and regenerate, or surface the gap to the user — do not hand-edit `resolved.yml`.

For Helm output (post-v1): run `helm lint` instead.

### Step 8 — Review, Write Output, and Prompt to Deploy

Present a summary of the generated artifact:

- File paths written
- Service list with images and assigned ports
- GPU assignments
- Shared infrastructure decisions
- `.env.template` location and the variables the user must fill in
- Bundled skills (microservice + use-case, from Step 6)
- Generated per-deployment deploy skill (`deploy-<profile-name>`, from Step 6) with its bring-up command

Show the diff if the operation modified an existing deployment. Wait for user confirmation, then write all files to the output directory. Always emit a `MANIFEST.md` listing every generated file and its purpose. The manifest must include an `## Architecture` section embedding the ASCII flowchart produced in Step 4 verbatim — operators reading the manifest should see the wiring at a glance, without re-running the skill.

#### Prompt to deploy

After all files are written, ask the user explicitly:

> "Deploy this profile now? [y/N]"

- **If `y`**: invoke the `deploy-<profile-name>` skill generated in Step 6. The skill should run from `build-output/` as its working directory so it picks up the generated `compose.yml`, `.env`, and `MANIFEST.md`. Before invoking, confirm the user has copied `.env.template` to `.env` and filled in required values (NGC API key, HF token, host IP, GPU IDs) — if `.env` is missing or still contains template placeholders, stop and ask the user to fill them in.
- **If `n` or no response**: print the bring-up command and the skill invocation command so the user can run either later:
  ```
  # Direct compose:
  docker compose --env-file build-output/.env -f build-output/compose.yml --profile <profile-name> up -d

  # Or via the generated skill:
  /deploy-<profile-name>
  ```

The autonomous-mode exception from Step 4 applies here too: when the user's original request explicitly said "deploy autonomously" or "and deploy", treat as `y` without prompting. When running in a non-interactive eval harness without explicit deploy intent, treat as `n` and just print the commands.

## File Structure

```
skills/vss-build-vision-agent/
├── SKILL.md
├── CONTRIBUTING.md                                    # (planned) how to add a new microservice (see Phase 0 deliverables)
├── eval/
│   ├── in-1-streaming-dense-captioning.json      # priority eval — gates Phase 4 rollout
│   ├── in-2-person-detection-rt-detr.json        # priority eval — extensibility test
│   └── ...                                            # follow-on evals as Phase 1c services land
├── references/
│   ├── integrate-microservice-schema.md               # canonical schema for integrate-<microservice>.md
│   ├── deploy-microservice-schema.md                  # canonical schema for deploy-<microservice>.md
│   ├── component-services-schema.md                   # schema for component_services: blocks (always/variants)
│   ├── microservice-catalog.md                        # index: capability tags → service → reference paths
│   ├── env-file-enumeration.md                        # Step 0 detail — 10 core .env files + NIM hw-tier set
│   ├── architecture-diagram-template.md               # Step 4 detail — ASCII flowchart requirements + IN-1 example
│   ├── allow-list-sidecar.md                          # Step 4 detail — sidecar schema, IN-1 example, union rules
│   ├── standalone-compose-patches.md                  # Step 6.5 detail — Patch 0/1/2/3 pseudocode + per-service notes
│   ├── example-walkthroughs.md                        # concrete streaming-dense-captioning walkthrough (IN-1)
│   ├── vss-compose-patterns.md                        # (planned) include-based compose, env_overrides, dry-run
│   ├── vss-helm-patterns.md                           # (planned, post-v1)
│   ├── shared-infrastructure.md                       # (planned) Kafka / ES / Redis sharing decision tree
│   └── gpu-allocation.md                              # (planned) device_ids, count, per-service vs. shared
└── scripts/
    └── validate-references.py                         # discovers and validates every integrate-*.md / deploy-*.md
```

## IN-1 Walkthrough — Concrete Example

A worked end-to-end example of the nine-step flow against the **IN-1 streaming + on-demand dense captioning** prompt — including the caption-topic gotcha (`RTVI_VLM_KAFKA_TOPIC=mdx-vlm-captions`, since the raw compose default `vision-llm-messages` is unsubscribed by both Logstash pipelines), the GPU-placement and `vlm_backend` decisions surfaced in Step 4, and the SDRC-template materialization done by Step 6.5 / Patch 3 — lives in `references/example-walkthroughs.md`.

## Operating Principles

- **Reference files are the source of truth.** Never hallucinate a service's image, port, env var, or peer dependency. If the reference file does not say it, do not generate it.
- **Cite specific sections.** Every architectural decision must point to the reference file and section that motivated it (NFR-5).
- **Surface gaps, do not paper over them.** A missing reference file is a stop condition, not a "best-effort" trigger (NFR-6). The catalog determines what the skill can compose.
- **Prompt for ambiguous decisions.** GPU assignment, shared infra, model selection, remote vs. local inference — all explicit user choices, not silent defaults (FR-4).
- **Idempotency.** Running the skill twice on the same input must produce the same output (NFR-3). The output compose must support `docker compose up` twice without error.
- **No silent modification.** When extending an existing deployment, every change to a pre-existing service must surface in the architecture proposal and diff (NFR-2).
- **Secrets via env substitution only.** No plaintext credentials in generated files (NFR-4). The `.env.template` lists every variable; values are the user's responsibility.

## Tear Down

`build-vision-agent` does not bring services up or down itself — that is the per-deployment `deploy-<profile-name>` skill generated in Step 6. Tear down a running profile with its skill (which knows the right `--profile` gate and volume cleanup):

```
/deploy-<profile-name>            # bring up
/deploy-<profile-name> down       # tear down (or use the explicit command in MANIFEST.md)
```

To remove the generated build artifacts themselves (compose, bundled skills, generated deploy skill):

```bash
rm -rf ./build-output/
```

## References

- `references/microservice-catalog.md` — index of all VSS microservices with reference files
- `references/integrate-microservice-schema.md` — canonical integration-contract schema
- `references/deploy-microservice-schema.md` — canonical deployment-contract schema
- `references/component-services-schema.md` — schema for `component_services:` blocks (always/variants)
- `references/env-file-enumeration.md` — Step 0 `.env` enumeration table + NIM hw-tier layout
- `references/architecture-diagram-template.md` — Step 4 ASCII diagram requirements + canonical IN-1 example
- `references/allow-list-sidecar.md` — Step 4 sidecar schema, IN-1 example, union rules
- `references/standalone-compose-patches.md` — Step 6.5 Patch 0/1/2/3 pseudocode and per-service IN-1 notes
- `references/example-walkthroughs.md` — worked end-to-end walkthroughs (currently: IN-1 streaming-dense-captioning)
- Per-deployment deploy skills are generated by Step 6 at `build-output/skills/deploy-<profile-name>/SKILL.md` — no shared `/deploy` skill exists.
- VSS docs: <https://docs.nvidia.com/vss/latest/>
- agentskills.io spec: governs the `name` / `description` / `version` / `license` frontmatter at the top of this file.
