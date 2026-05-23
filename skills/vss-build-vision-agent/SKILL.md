---
name: vss-build-vision-agent
description: >
  Compose VSS-based agent deployments from a natural-language capability description.
  Use this skill when the user asks for a new VSS profile or extension to an existing
  one (e.g. "create a profile for streaming dense captioning", "add agentic search to
  my base deployment", "integrate my third-party camera system with VSS"). The skill
  reads per-microservice reference files (`integrate-<microservice>.md`,
  `deploy-<microservice>.md`) as ground truth, invents a unique compose-profile flag
  per generation, patches build-output copies of the relevant upstream service
  composes (never upstream itself), and outputs a validated, self-contained Docker
  Compose deployment under `build-output/` along with a generated per-deployment
  deploy skill.
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

If the user asks to **deploy** a generated compose, the skill will create (or update) a per-deployment deploy skill in Step 6 and prompt to invoke it in Step 8 — see those steps below. If the user asks to **call** a service's API (RT-VLM endpoints, VIOS endpoints, etc.), hand off to the relevant upstream skill (`vss-deploy-dense-captioning`, `vss-manage-video-io-storage`, `vss-setup-video-analytics-api`, etc.) — those are bundled into `build-output/skills/` in Step 6.

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
- **Output path** — where to write the generated artifact. Default: `./build-output/`.

#### Enumerate ALL `.env` files in the source repo

VSS spreads its environment configuration across **multiple `.env` files** by concern. A skill that only reads the per-profile `dev-profile-*/.env` will miss component-internal variables and produce a `.env` that fails dry-run with errors like `invalid spec: :/home/vst/vst_release/streamer_videos: empty section between colons` (caused by an unset `${CLIP_STORAGE_PATH}` collapsing the host portion of a volume mount).

Run a recursive `.env` discovery against the source repo and record every file found:

```bash
find <repo>/deployments -type f -name '.env' -not -path '*/build-output/*' | sort
```

For the current upstream, the canonical set is **10 core `.env` files** (4 developer profiles, 1 industry profile, 5 service-internal) plus a NIM hardware-tier set selected per host architecture (see below the table):

| File | Owns variables for |
|---|---|
| `deploy/docker/developer-profiles/dev-profile-base/.env` | base profile (deployment shape, hardware, NIM placement, paths) |
| `deploy/docker/developer-profiles/dev-profile-lvs/.env` | LVS profile additions (`VLM_PORT`, `LVS_IMAGE`, `LVS_BACKEND_URL`, etc.) |
| `deploy/docker/developer-profiles/dev-profile-search/.env` | search profile additions (Kafka + ELK on, embedding service, video-analytics, etc.) |
| `deploy/docker/developer-profiles/dev-profile-alerts/.env` | alerts profile additions (alert verification, vlm-as-verifier, behavior analytics) |
| `deploy/docker/services/vios/vst.env` | **VIOS-internal vars** — `CLIP_STORAGE_PATH`, `VST_TEMP_FILES_PATH`, `SDR_IMAGE`, `ENVOY_PROXY_IMAGE`, `VST_STREAM_PROCESSOR_IMAGE`, `KAFKA_BOOTSTRAP_URL`, `REDIS_HOSTADDR`, `REDIS_PORT`, `REDIS_MSG_KEY`, `STREAM_PROCESSOR_HTTP_PORT`, `RTSP_SERVER_PORT`, `SENSOR_MODULE_ENDPOINT`, `VST_INGRESS_ENDPOINT`, `VST_INSTALL_ADDITIONAL_PACKAGES`, `MCP_GATEWAY_*` (the canonical image names live here too — `vss-vios-sensor`, not the historical `vss-vst-*`) |
| `deploy/docker/services/vios/compose-defaults.env` | Compose defaults — empty/placeholder values for variables referenced across all composes included by `deploy/docker/compose.yml`, suppressing `docker compose` warnings when a profile does not use a particular service. Override per-variable in the active profile `.env`. |
| `deploy/docker/services/video-summarization/.env` | LVS-component-internal vars (LVS image tag, backend port wiring) |
| `deploy/docker/services/rtvi/rtvi-vlm/.env` | RT-VLM-component-internal defaults |
| `deploy/docker/services/rtvi/rtvi-embed/.env` | RT-Embedding-component-internal defaults |
| `deploy/docker/industry-profiles/warehouse-operations/.env` | Industry-profile variant — `warehouse-operations` stack additions and overrides (separate from the developer-profile category) |


#### NIM hardware-tier `.env` files (new structural category)

Beyond the 10 core files above, the NIM service tree carries a **per-hardware-tier `.env` set**: one file per (model × hardware) combination, plus `-shared` variants for shared-GPU mode. Selection is by `HW_PROFILE` (set in `dev-profile-base/.env` or equivalent), not by enumeration.

Layout: `deploy/docker/services/nim/<model>/hw-<HW_PROFILE>.env` (standalone) and `hw-<HW_PROFILE>-shared.env` (shared-GPU). Plus `deploy/docker/services/nim/fallback-override.env` for cross-model overrides.

Models present upstream (as of this writing): `cosmos-reason1-7b`, `cosmos-reason2-8b`, `qwen3-vl-8b-instruct`, `gpt-oss-20b`, `llama-3.3-nemotron-super-49b-v1.5`, `nemotron-3-nano`, `nvidia-nemotron-nano-9b-v2`, `nvidia-nemotron-nano-9b-v2-fp8`.

Hardware tiers: `H100`, `RTXPRO6000BW`, `L40S`, `DGX-SPARK`, `AGX-THOR`, `IGX-THOR`, `OTHER` (not every model has every tier — `cosmos-reason1-7b` skips DGX-SPARK / Thor tiers, etc.).

**Step 0 must determine `HW_PROFILE` first**, then pick the matching NIM env file(s) for every NIM model the profile uses. Do not blindly fold all hardware-tier files into one `.env` — they contain mutually-exclusive `MODEL_PROFILE` / `LIMITS_*` values per tier and would clobber each other.

When generating the output `.env` in Step 6, **fold in every variable referenced by any selected service's compose** — even if it lives outside the per-profile `.env`. Cross-reference each candidate's `integrate-<microservice>.md` § Environment Variables for the authoritative per-service list, and walk the actual compose YAML for `${VAR}` substitutions to catch any the reference file missed.

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

- **Required peer services** — confirm each is satisfied (see Step 1).
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

Then prompt the user for any of the following that are ambiguous (FR-4):

- **GPU assignment** — which physical GPU index each GPU-requiring service should land on. Use `RT_VLM_DEVICE_ID`, `RT_CV_DEVICE_ID`, etc., names from the source compose.
- **Shared vs. isolated infrastructure** — when the user supplied a source compose with its own Kafka / ES, ask explicitly.
- **Endpoint conflicts** — when port collisions cannot be resolved automatically.
- **Model selection** — when multiple VLM / LLM options are compatible.
- **Remote vs. local inference** — for NIM-based services (RT-VLM in `openai-compat` mode, LLM NIMs).
- **External RTSP source location** (when the prompt mentions live stream input) — is the source a public RTSP server, a sibling container, or a host process? Pre-flight reachability **from inside the rtvi-vlm container** (not just the host) before generating the compose. If the source is a non-VSS sidecar, recommend co-locating on the same compose network with `--network-alias` (see `integrate-rt-vlm.md` § Network Requirements > Reaching external RTSP sources). If the source is on the host, verify Docker's iptables FORWARD chain has the necessary rule by probing `docker exec rtvi-vlm bash -c "exec 3<>/dev/tcp/${HOST_IP}/${RTSP_PORT}"`.

Wait for confirmation before continuing. The only exception is **autonomous mode** — when the user's request explicitly says "deploy autonomously" or "run without confirmation", or when running inside a non-interactive eval harness with that permission.

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
├── MANIFEST.md
├── patched/                            # Step 6.5 outputs (compose copies with stripped depends_on)
└── skills/
    ├── vss-manage-video-io-storage/    # bundled from <vss-repo>/skills/vss-manage-video-io-storage/
    ├── vss-deploy-dense-captioning/    # bundled from <vss-repo>/skills/vss-deploy-dense-captioning/
    ├── <use-case-skill>/               # bundled IF one matched the capability description; skipped otherwise
    └── deploy-<profile-name>/
        └── SKILL.md                    # generated; overwritten on re-run
```

### Step 6.5 — Apply Standalone-Compose Patches

The build-output deploys a unique, never-before-seen profile generated by the skill. To make that work against the upstream's existing compose tree **without modifying upstream files**, the skill copies the involved upstream service composes into `build-output/patched/` and applies two patches to each copy.

#### Patch 1 — Insert the new gating flag

Add the invented flag (chosen in Step 6's compose-profile gating bullet — e.g., `bp_developer_in_1`) to every relevant service's `profiles:` list in each patched copy. The flag is **additive** — upstream flags already in each list (`bp_developer_alerts_2d_vlm`, `bp_developer_search_2d`, `bp_wh_*`, etc.) stay. For an IN-1-shaped profile (RT-VLM + VIOS + ELK + Kafka), expect to touch 15–20 `profiles:` blocks across 4–5 patched composes — typically `rtvi-vlm` (1 site), VIOS containers in `services/vios/compose.yml` (3–4), the SDR streamprocessing trio (3), and the foundational `kafka` / `redis` / `elasticsearch` / `kibana` / `logstash` / `broker-health-check` services in `services/infra/compose.yml` (6+). Generate a `PATCHES.md` artifact listing every site touched so the operator can audit.

Record the chosen flag at the top of `build-output/MANIFEST.md` and use it in every bring-up / tear-down command surfaced to the user.

#### Patch 2 — Strip undefined `depends_on` entries

Recent Docker Compose (≥ v2.36) validates the entire compose project at load time and **rejects `depends_on` references to services that are not defined within the same project**, even when those references carry `required: false`. `--no-deps` does NOT bypass this validation. The full VSS `deploy/docker/compose.yml` works because it `include:`s every sibling compose (services/{infra, vios, rtvi, nim, agent, alert, monitoring, ui, video-summarization, auto-calibration, dt-based-calibration}, developer-profiles, industry-profiles) and therefore every `depends_on` target resolves. A *standalone* compose generated by this skill — one that includes only the subset needed for a focused profile — does not have that property and will fail with errors like:

```
service "rtvi-vlm" depends on undefined service "cosmos-reason1-7b": invalid compose project
```

**Detection.** Before running Step 7's dry-run, scan every patched compose copy in `build-output/patched/` (and any compose the skill emitted directly) for `depends_on:` references whose targets are not defined elsewhere in the include graph:

```bash
# pseudocode — actual implementation walks the include graph
for compose in build-output/patched/**/*.{yml,yaml} build-output/compose.yml; do
  scan compose for depends_on entries → set of referenced service names
  collect set of service names defined across all included composes
  diff = referenced − defined
  for each name in diff: candidate for stripping
done
```

**Fix.** For each `depends_on` entry whose target is in the diff and whose `required: false`, **strip the entry** from the patched copy. If the target is required (no `required: false` flag), it is a real gap — surface it to the user as a missing peer service rather than silently stripping.

The canonical case is RT-VLM: `rtvi-vlm-docker-compose.yml` declares `depends_on` on `cosmos-reason1-7b`, `cosmos-reason1-7b-shared-gpu`, `cosmos-reason2-8b`, `cosmos-reason2-8b-shared-gpu`, `qwen3-vl-8b-instruct`, `qwen3-vl-8b-instruct-shared-gpu`, and `broker-health-check` — all with `required: false`. When `nim/compose.yml` is NOT in the include set (e.g., IN-1 in VLM-only mode runs RT-VLM with `VLM_MODEL_TO_USE=cosmos-reason2` so the model is loaded in-process and no sibling NIM is needed), all six NIM references must be stripped. `broker-health-check` stays — it is defined in `mdx-foundational.yml` which IS in the include set.

Implementation reference (works without `yq`):

```python
from pathlib import Path

p = Path("build-output/patched/rtvi/rtvi-vlm/rtvi-vlm-docker-compose.yml")
out, skip, base = [], False, 4   # 4-space indent of the "depends_on:" key
for line in p.read_text().splitlines():
    s = line.lstrip()
    indent = len(line) - len(s)
    if not skip and line.startswith("    depends_on:"):
        skip = True
        continue
    if skip:
        if s and indent <= base:
            skip = False
            out.append(line)
        continue
    out.append(line)
p.write_text("\n".join(out) + "\n")
```

The full per-service walkthrough lives in each affected service's `deploy-<microservice>.md` § Standalone Fix (currently documented for RT-VLM in `deploy-rt-vlm.md`).

After applying the fix, **note in `MANIFEST.md`** which `depends_on` entries were stripped from which file, so the operator understands the difference between the patched copy and the upstream original.

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

Show the diff if the operation modified an existing deployment. Wait for user confirmation, then write all files to the output directory. Always emit a `MANIFEST.md` listing every generated file and its purpose.

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
├── CONTRIBUTING.md                                    # how to add a new microservice (see Phase 0 deliverables)
├── eval/
│   ├── in-1-streaming-dense-captioning.json      # priority eval — gates Phase 4 rollout
│   ├── in-2-person-detection-rt-detr.json        # priority eval — extensibility test
│   └── ...                                            # follow-on evals as Phase 1c services land
├── references/
│   ├── integrate-microservice-schema.md               # canonical schema for integrate-<microservice>.md
│   ├── deploy-microservice-schema.md                  # canonical schema for deploy-<microservice>.md
│   ├── microservice-catalog.md                        # index: capability tags → service → reference paths
│   ├── vss-compose-patterns.md                        # (planned) include-based compose, env_overrides, dry-run
│   ├── vss-helm-patterns.md                           # (planned, post-v1)
│   ├── shared-infrastructure.md                       # (planned) Kafka / ES / Redis sharing decision tree
│   └── gpu-allocation.md                              # (planned) device_ids, count, per-service vs. shared
└── scripts/
    └── validate-references.py                         # discovers and validates every integrate-*.md / deploy-*.md
```

## IN-1 Walkthrough — Concrete Example

User prompt:
> "Create a profile for streaming and on-demand video dense captioning. Streamed dense captions should be published to the kafka message bus and stored in elasticsearch."

Skill execution:

1. **Step 0 — Parse**: capability = "streaming + on-demand dense captioning, Kafka + ES storage". No existing deployment, no 3P descriptor. Output target = compose (default). Net-new.

2. **Step 1 — Catalog**: tag-match against `microservice-catalog.md`. Candidates: RT-VLM (`dense-captioning`, `streaming-inference`, `on-demand-inference`), VIOS (`rtsp-ingestion`, `video-upload`), ELK (`caption-storage`, `kafka-ingestion`). All required peers satisfiable from within the candidate set plus `kafka` from the foundational stack.

3. **Step 2 — Read integrate refs**: `integrate-rt-vlm.md`, `integrate-vios.md`, `integrate-elk.md`. Note: RT-VLM's `clip_storage` mount must be the same host path as VIOS's `${VST_VIDEO_STORAGE_PATH}`. RT-VLM publishes to `vision-llm-messages`; Logstash pipeline subscribes via the foundational `mdx-logstash.conf` (under `deploy/docker/services/infra/elk/logstash/pipelines/kafka/`).

4. **Step 3 — Conflicts**: none for net-new. Note that VIOS uses `network_mode: host` while RT-VLM uses default bridge — wiring crosses through `${HOST_IP}:9092` for Kafka and a shared volume for video.

5. **Step 4 — Proposal**:
   - Services to add: VIOS (4 containers), RT-VLM (1 container + sibling NIM), ELK (4 containers), Kafka (foundational), Redis (foundational), `broker-health-check`.
   - Sibling VLM NIM choice: `cosmos-reason2-8b` by default. Confirm or ask.
   - GPU assignment: RT-VLM on GPU 0; sibling NIM on GPU 0 (`local_shared`) or GPU 1 (`local`). Ask.
   - Shared ES: single instance (default).
   - Caption topic: `vision-llm-messages` default; confirm whether to remap to foundational `mdx-vlm`.

6. **Step 5 — Read deploy refs**: `deploy-rt-vlm.md`, `deploy-vios.md`, `deploy-elk.md`. Validate host has GPU with ≥ 16 GB VRAM for the cosmos-reason2-8b backend.

7. **Step 6 — Generate**: write `build-output/compose.yml` plus per-service includes, `.env.template`, `MANIFEST.md`. Invent the flag `bp_developer_in_1` (matching the IN-1 catalog entry) and pass it as `--profile bp_developer_in_1` at deploy time. Step 6.5 will copy the upstream `rtvi-vlm`, VIOS, SDR, and foundational ELK/Kafka composes into `build-output/patched/` and add `bp_developer_in_1` to every relevant service's `profiles:` list inside those local copies; upstream files stay untouched. Bundle the `vss-manage-video-io-storage/` and `vss-deploy-dense-captioning/` skills from `<vss-repo>/skills/` into `build-output/skills/` (ELK references already live inside `vss-build-vision-agent/references/`). Scan `<vss-repo>/skills/` for a use-case skill matching "streaming dense captioning" and bundle if present, otherwise skip. Generate `build-output/skills/deploy-in-1/SKILL.md` with the exact compose path, env path, RT-VLM `1200s` cold-boot window, GPU 0 assignment for RT-VLM + sibling NIM, healthcheck loop, and the bring-up / tear-down commands hardcoded.

8. **Step 7 — Dry-run**: `docker compose --env-file .env.template config > resolved.yml` and confirm no `${...}` remain.

9. **Step 8 — Review and prompt to deploy**: present summary including bundled skills and the generated `deploy-in-1` skill; on confirmation, write to `./build-output/`. Then ask "Deploy this profile now? [y/N]" — on `y`, verify `.env` is filled in and invoke `deploy-in-1`; on `n`, print the bring-up command.

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
- Per-deployment deploy skills are generated by Step 6 at `build-output/skills/deploy-<profile-name>/SKILL.md` — no shared `/deploy` skill exists.
- VSS docs: <https://docs.nvidia.com/vss/latest/>
- agentskills.io spec: governs the `name` / `description` / `version` / `license` frontmatter at the top of this file.
