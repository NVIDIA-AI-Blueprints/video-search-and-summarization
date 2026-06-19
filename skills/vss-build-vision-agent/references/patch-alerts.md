# Patch Reference: Alert Verification / Alert Bridge (build-vision-agent)

This file is owned by `vss-build-vision-agent`. It holds the machinery the orchestrator
needs to fold the Alert Verification service (Alert Bridge, `vss-alert-bridge`) into a
generated deployment: the `component_services:` block, the Step 6.5 patch specifics
(Patch 1 flag insertion, Patch 2 `depends_on` strip, Patch 3 realtime-config
materialization), and the `.env` overrides that wire AB at the existing `rtvi-vlm`. It is
NOT a microservice contract.

For the underlying Alert Bridge API, env vars, ports, Kafka topics, ES sinks, realtime
rule schema, and known constraints, read the skill-neutral pair files in the alerts skill:

- `skills/vss-manage-alerts/references/integrate-alerts.md` — Alert Verification
  integration contract: API schema, inputs/outputs, env vars, network, known constraints.
- `skills/vss-manage-alerts/references/deploy-alerts-service.md` — Alert Verification
  deployment contract: image, GPU (none), CPU/memory, storage, startup, verify, tear-down.

Schema for the `component_services:` block is in `references/component-services-schema.md`;
the per-generation sidecar is `references/allow-list-sidecar.md`; the patch pseudocode is
`references/standalone-compose-patches.md`.

## How the skill uses this file

- **Step 2 / Step 4** read the `component_services:` block below (NOT the integrate doc)
  to learn which upstream compose service-key Alert Verification owns. Step 4 unions this
  block with the other selected microservices' patch files and writes the flat allow-list
  to `allow-list.yml` under the build directory.
- **Step 6.5** reads ONLY the resulting sidecar (never this file, never the catalog, never
  the integrate prose) and applies the patches in the "Patch specifics" section below to
  the `alert-bridge` compose copy under the build directory's patched tree
  (`patched/services/alert/`).

## component_services block

Alert Verification owns a single compose service (`alert-bridge`); there are no variants.
It calls the existing `rtvi-vlm` over HTTP — it does NOT bring its own VLM, and the sibling
NIM service-keys MUST NOT appear in the allow-list (see Patch 2). Kafka / ES / Redis /
kafka-topic-init are ELK-owned (already in the allow-list when ELK is selected) and are
NOT re-declared here.

```yaml
component_services:
  # Alert Bridge itself — required, single variant. CPU-only VLM verifier; delegates
  # inference to the existing rtvi-vlm over HTTP at ${VLM_BASE_URL} / ${RTVI_VLM_BASE_URL}.
  - key: alert-bridge
    file: services/alert/compose.yml
    role: VLM-verified alerting — consumes mdx-incidents/mdx-alerts + exposes /api/v1/realtime; writes verified docs to ES mdx-vlm-incidents / mdx-vlm-alerts.
    required_peers: []   # rtvi-vlm is supplied by patch-rt-vlm.md's component_services when RT-VLM is selected (it always is for an AN-1/IN-1-layered build); kafka/redis/es/kafka-topic-init come from ELK.
```

## Alert verifier GPU model — calls rtvi-vlm over HTTP (NO second GPU)

**Resolved design decision.** The Alert Bridge image (`vss-alert-verification`) carries no
model and reserves no GPU. It calls the existing `rtvi-vlm` (the same container IN-1
deploys for captioning) over HTTP:

- stream-driven verification → `VLM_BASE_URL` (default `http://${HOST_IP}:${VLM_PORT}`,
  `VLM_PORT=8018`)
- real-time always-on → `RTVI_VLM_BASE_URL` (default `http://${HOST_IP}:8018`),
  `config.yml` reads `rtvi_vlm.base_url: ${RTVI_VLM_BASE_URL}/v1`

So an AN-1 (= IN-1 + VLM alerting) build allocates exactly **one** GPU
(`RT_VLM_DEVICE_ID`), shared between captioning and alert verification. The skill must NOT
add a second device reservation and must NOT pull any sibling NIM. The single `rtvi-vlm`
serves both workloads.

## Patch specifics (Step 6.5)

Applied to the patched copy of `services/alert/compose.yml` under `<BUILD_DIR>/patched/`;
the upstream tree is never modified.

### Patch 1 — invented flag

The upstream `alert-bridge` service gates behind `profiles: ["bp_wh_2d",
"bp_developer_alerts_2d_cv", "bp_developer_alerts_2d_vlm"]`. Step 6.5 appends the
per-generation invented flag (e.g. `bp_developer_an_1`) to that `profiles:` list in the
patched copy (additive — existing upstream flags stay).

### Patch 2 — strip undefined `depends_on` peers

The upstream `alert-bridge` `depends_on` declares (besides the always-defined ELK peers
`kafka`, `redis`, `elasticsearch`, `kafka-topic-init-container`):

- `nvstreamer-alerts` — `required: false`
- the 8 sibling-NIM keys — all `required: false`: `cosmos-reason1-7b`,
  `cosmos-reason1-7b-shared-gpu`, `cosmos-reason2-8b`, `cosmos-reason2-8b-shared-gpu`,
  `cosmos3-reasoner`, `cosmos3-reasoner-shared-gpu`, `qwen3-vl-8b-instruct`,
  `qwen3-vl-8b-instruct-shared-gpu`
- `rtvi-vlm` — `required: false`

The **generalized** Patch 2 rule (same as patch-rt-vlm.md) strips whichever `depends_on`
peers are **undefined** in the patched include graph and keeps whichever are **defined**:

- For an AN-1 layered on IN-1, `rtvi-vlm` IS in the include graph → **keep** it (drop its
  `required: false` to a hard gate is optional; keeping it as-is is fine — the point is it
  resolves). `kafka`/`redis`/`elasticsearch`/`kafka-topic-init-container` are ELK-defined →
  **keep**.
- The 8 NIM keys + `nvstreamer-alerts` are **undefined** (IN-1 uses the in-process RT-VLM
  backend with no sibling NIM, and the validation harness uses `nvstreamer-validation`,
  not `nvstreamer-alerts`) → **strip**.

Because the rule is "strip whatever is undefined," it is robust to the NIM peer set
changing.

### Patch 3 — realtime-config materialization (NEW for alerts)

The upstream `dev-profile-alerts` ships **no** `realtime-config.yml` and does **not** set
`VLM_AS_VERIFIER_CONFIG_FILE_REALTIME`. The compose's realtime-config volume default is the
non-existent placeholder `/path/to/realtime-config.yml`, which Docker would silently
create as an empty directory (mount failure for the always-on REST path).

Step 6.5 must materialize a real realtime ruleset into the patched tree and point the env
var at it:

1. `cp` the canonical sample
   `deploy/docker/industry-profiles/warehouse-operations/vlm-as-verifier/realtime-config.yml`
   into `<BUILD_DIR>/patched/services/alert/realtime-config.yml` (and, if the always-on
   ruleset is to be customized for the build's capability, edit the `always_on_rules`
   prompts in the copy — never the upstream).
2. Also `cp` `config.yml` + `alert_type_config.json` from
   `dev-profile-alerts/vlm-as-verifier/configs/` into the patched tree so the AB config
   bind-mounts resolve to build-local files (avoids depending on the upstream
   developer-profile dir layout).
3. Set the three `VLM_AS_VERIFIER_*` env vars in the generated `.env` to the patched-tree
   absolute paths (see ".env overrides" below).

This is the alerts analogue of patch-vios.md Patch 3 (SDRC config-template
materialization) and patch-rt-vlm.md's FILE_URL_ALLOWED_DIRS handling.

## .env overrides the skill applies

In the generated `<BUILD_DIR>/.env`, fold in the alert-specific variables. Resolve image
tags from `dev-profile-alerts/.env` / `dev-profile-base/.env` (do not hardcode). Required
additions on top of the IN-1 `.env`:

```
# Alert Bridge — REST API + VLM wiring (reuses the IN-1 rtvi-vlm; no new GPU)
ALERT_BRIDGE_PORT=9080
ALERT_BRIDGE_URL=http://${HOST_IP}:9080
VLM_PORT=8018
VLM_BASE_URL=                       # empty → compose default http://${HOST_IP}:${VLM_PORT}
RTVI_VLM_BASE_URL=http://${HOST_IP}:8018
VLM_NAME=nim_nvidia_cosmos-reason2-8b_hf-1208   # runtime model id (matches /v1/models); NOT the friendly name
VLM_MODE=local_shared
LLM_MODE=local_shared
VLM_MODEL_TYPE=rtvi                 # rtvi (NOT nim) — nim leaks verify_ssl → 422
RTVI_VLM_MODEL_TO_USE=cosmos-reason2

# vlm-as-verifier config files (point at the build-local patched copies)
VLM_AS_VERIFIER_CONFIG_FILE=<BUILD_DIR>/patched/services/alert/config.yml
VLM_AS_VERIFIER_CONFIG_FILE_REALTIME=<BUILD_DIR>/patched/services/alert/realtime-config.yml
VLM_AS_VERIFIER_ALERT_TYPE_CONFIG_FILE=<BUILD_DIR>/patched/services/alert/alert_type_config.json
```

`HOST_IP`, `NGC_CLI_API_KEY`, `VSS_DATA_DIR`, `VSS_APPS_DIR`, `RT_VLM_DEVICE_ID`, and all
the IN-1 RT-VLM / VIOS / ELK vars are inherited unchanged from the IN-1 `.env`.

Note `config.yml` references `${VLM_BASE_URL}` and `${VLM_NAME}` etc. — but AB's own
entrypoint (`env-substitute.py`) does that substitution from the container env at start, so
those tokens must be present in AB's environment (they are, via the compose `environment:`
block, which Compose populates from the build `.env`). The `EXTERNAL_IP` / `INTERNAL_IP`
URL-rewrite vars resolve from `${EXTERNAL_IP}` (= `${HOST_IP}`) and `${HOST_IP}`.

## Emitted shape

The patched `alert-bridge` block is `include:`d from `<BUILD_DIR>/compose.yml`; deploy with
`docker compose --env-file <BUILD_DIR>/.env -f <BUILD_DIR>/compose.yml --profile
<invented-flag> up -d`. See the `## Example Compose Snippet` in `integrate-alerts.md` for
the upstream block this is patched from.
