# Patch Reference: VIOS (build-vision-agent)

This file is owned by `vss-build-vision-agent`. It holds the machinery the orchestrator needs to fold VIOS into a generated deployment: the `component_services:` block, the Step 6.5 patch specifics (Patch 1 flag sites, Patch 3 SDRC config-template materialization), and the invented-flag + patched-copy wiring. It is NOT a microservice contract.

For the underlying VIOS API, env vars, ports, SDRC routing facts, and known constraints, read the skill-neutral pair files in the VIOS skill:

- `skills/vss-manage-video-io-storage/references/integrate-vios-service.md` — VIOS integration contract: API schema, inputs/outputs, env vars, network, known constraints, SDRC routing facts, the two ingestion topologies.
- `skills/vss-manage-video-io-storage/references/deploy-vios-service.md` — VIOS deployment contract: images, GPU/CPU/memory, storage, startup, dry-run, verify, tear-down.

Schema for the `component_services:` block is in `references/component-services-schema.md`; the per-generation sidecar is `references/allow-list-sidecar.md`; the patch pseudocode is `references/standalone-compose-patches.md`. The NvStreamer synthetic-RTSP **validation harness** that exercises Topology A's live path is documented in `references/validation-harness.md` (NvStreamer is emitted directly by Step 6 — it is deliberately NOT a VIOS-owned compose service and NOT in the `component_services:` block below).

## How the skill uses this file

- **Step 2 / Step 4** read the `component_services:` block below (NOT the integrate doc) to learn which upstream compose service-keys VIOS owns and which `sensor_topology` variant cases exist. Step 4 unions this block with the other selected microservices' patch files, resolves the variant against the user-chosen `deployment_shape`, and writes the flat allow-list to `allow-list.yml` under the build directory.
- **Step 6.5** reads ONLY the resulting sidecar (never this file, never the catalog, never the integrate prose) and applies the patches in the "Patch specifics" section below to the VIOS + SDRC compose copies under the build directory's patched tree (`patched/services/vios/` and `patched/services/infra/sdrc/`).

## component_services block

Top-level entries are added to the allow-list whenever VIOS is selected; the two `variants:` blocks (sensor-ms and streamprocessing-ms, both keyed on `sensor_topology`) resolve to exactly one case per generation. The SDRC stack (6 services) is required and single-variant. `sensor-bp-wait-bp-configurator` is `required: false` (warehouse profiles only) and is omitted from a default IN-1 allow-list.

```yaml
component_services:
  # HAProxy ingress — required, single variant. HTTP-only reverse proxy on port 7777;
  # routes /vst/... → vst-ingress:30888. Must be allow-listed so Patch 1 adds the
  # invented profile flag — making port 7777 the stable external HTTP entry point for
  # VIOS API calls from smoke tests. Without this, HAProxy stays on its upstream
  # profiles only (bp_developer_search_2d etc.) and is absent from generated profiles.
  - key: vss-haproxy-ingress
    file: services/infra/haproxy/compose.yml
    role: HTTP reverse proxy (port 7777) — stable external entry point for all VIOS HTTP API calls. Use http://${HOST_IP}:7777/vst/api/v1 in smoke tests, not the direct vst-ingress port (30888).
  # PostgreSQL — required, single variant
  - key: centralizedb
    file: services/vios/foundational/docker-compose.yaml
    role: PostgreSQL state store for sensor configurations and stream metadata.
  # VST ingress — required, single variant
  - key: vst-ingress
    file: services/vios/foundational/docker-compose.yaml
    role: Public REST gateway for /sensor, /storage, /record, /replay, /live, /proxy.
  # SDRC stack — required, single variant. Combined WDM controller + Envoy router
  # (sdr-controller) plus 5 one-shot init containers from the same docker-compose.yaml.
  # Replaces the legacy sdr-streamprocessing + envoy-streamprocessing pair (deprecated
  # in 3.2, source tree slated for removal). All 6 services are enumerated below so
  # Patch 1 adds the invented profile flag to each. The render-config init
  # container additionally requires the build to materialize config.yml.tmpl
  # + the docker_cluster_config-*.json.tmpl set that matches the deployment's
  # stream-consuming workloads under SDR_CONTROLLER_CONFIG_PATH/configs. The template
  # SET is workload-dependent (see Patch 3): a VIOS/streamprocessing-only build uses the
  # 2d_vlm model (streamprocessing workload only); a build that also runs RT-CV
  # (cv-verification) MUST use the 2d_cv model, which ADDS docker_cluster_config-rtvi-cv.json.tmpl.
  # Templates are not compose services (not in component_services) but are a hard
  # requirement for the SDRC chain to route streams to each workload.
  - key: init-dirs
    file: services/infra/sdrc/docker-compose.yaml
    role: One-shot — chmod 0777 ./log + ./.wdm-env so the host user can clean up later. Strict prereq for sdr-controller.
  - key: render-config
    file: services/infra/sdrc/docker-compose.yaml
    role: One-shot — renders every *.tmpl under SDR_CONTROLLER_CONFIG_PATH/configs in place, substituting ${HOST_IP} / ${NUM_STREAMS} / ${NUM_SENSORS}. Strict prereq for sdr-controller.
  - key: wdm-env-from-config
    file: services/infra/sdrc/docker-compose.yaml
    role: One-shot — writes ./.wdm-env from the rendered config.yml. Gates downstream peer consumers (wait-for-redis / wait-for-docker-workloads); NOT consumed by sdr-controller.
  - key: wait-for-redis
    file: services/infra/sdrc/docker-compose.yaml
    role: One-shot — blocks until Redis is up on WDM_WL_REDIS_SERVER:WDM_WL_REDIS_PORT. Gates downstream peer consumers; NOT consumed by sdr-controller.
  - key: wait-for-docker-workloads
    file: services/infra/sdrc/docker-compose.yaml
    role: One-shot — blocks until the docker workloads listed in config.yml exist. Gates downstream peer consumers; NOT consumed by sdr-controller.
  - key: sdr-controller
    file: services/infra/sdrc/docker-compose.yaml
    role: WDM controller + Envoy router; advertises streamprocessing-ms on the rendered Envoy listener (WDM_MS_LISTENER_PORT, default 10000) so vss-vios-sensor's STREAM_PROCESSOR_MODULE_ENDPOINT=http://localhost:10000 contract is honored.
  # Sensor microservice — sibling-variant branching by sensor topology
  - variants:
      key: sensor_topology
      cases:
        rtsp-and-uploaded:
          - key: sensor-ms
            file: services/vios/initiator/docker-compose.yaml
            role: VST adaptor with vst_rtsp profile — accepts both RTSP input and uploaded files.
        warehouse-2d:
          - key: sensor-ms-2d
            file: services/vios/initiator/docker-compose.yaml
            role: VST adaptor preconfigured with the warehouse-2d vst_config overlay.
        warehouse-3d:
          - key: sensor-ms-3d
            file: services/vios/initiator/docker-compose.yaml
            role: VST adaptor preconfigured with the warehouse-3d vst_config overlay.
        warehouse-mv3dt:
          - key: sensor-ms-mv3dt
            file: services/vios/initiator/docker-compose.yaml
            role: VST adaptor preconfigured with the multi-view warehouse vst_config overlay.
  # Streamprocessing — sibling-variant branching by the SAME topology selector as sensor-ms
  - variants:
      key: sensor_topology
      cases:
        rtsp-and-uploaded:
          - key: streamprocessing-ms
            file: services/vios/streamprocessing/docker-compose.yaml
            role: DeepStream pipeline for plain RTSP-and-uploaded video streams.
        warehouse-2d:
          - key: streamprocessing-ms-2d
            file: services/vios/streamprocessing/docker-compose.yaml
            role: DeepStream pipeline with warehouse-2d label overlay.
        warehouse-3d:
          - key: streamprocessing-ms-3d
            file: services/vios/streamprocessing/docker-compose.yaml
            role: DeepStream pipeline with warehouse-3d label overlay.
        warehouse-mv3dt:
          - key: streamprocessing-ms-mv3dt
            file: services/vios/streamprocessing/docker-compose.yaml
            role: DeepStream pipeline with multi-view warehouse label overlay.
  # bp-configurator wait shim — NOT in any default allow-list; warehouse profiles only.
  - key: sensor-bp-wait-bp-configurator
    file: services/vios/initiator/docker-compose.yaml
    role: One-shot poller that waits for the warehouse blueprint configurator readyz endpoint.
    required: false
```

## Patch specifics (Step 6.5)

Applied to patched copies under `<BUILD_DIR>/patched/services/{vios,infra/sdrc}/`; the upstream tree is never modified.

### Patch 1 — invented flag (the VIOS + SDRC set must be patched together)

For Topology A (SDRC-routed — the canonical IN-1 path), Patch 1 must append the invented flag (e.g. `bp_developer_in_1`) to the `profiles:` list of **all** of: `vss-haproxy-ingress` (in `services/infra/haproxy/compose.yml`), `sensor-ms*`, `streamprocessing-ms*`, AND every service in `services/infra/sdrc/docker-compose.yaml` (`init-dirs`, `render-config`, `wdm-env-from-config`, `wait-for-redis`, `wait-for-docker-workloads`, `sdr-controller`), plus `centralizedb` + `vst-ingress`. Patching only `streamprocessing-ms` leaves `sensor-ms` unable to reach the SDRC-rendered Envoy listener on `localhost:10000`, so `POST /sensor/add` fails with `Invalid Parameters` and no useful diagnostic. (The legacy `sdr-streamprocessing` + `envoy-streamprocessing` pair is gated to a dead profile in 3.2 — do not contribute it.) For lighter direct-routing instead, set `STREAM_PROCESSOR_MODULE_ENDPOINT=http://localhost:30001` + `VST_NGINX_MODE=vst-direct` in the build `.env` and skip the SDRC stack entirely.

### Patch 3 — SDRC config-template materialization

The SDRC `render-config` init container reads `*.tmpl` files from `${SDR_CONTROLLER_CONFIG_PATH}/configs/` and renders each in place (substituting `${HOST_IP}` / `${NUM_STREAMS}` / `${NUM_SENSORS}`). Step 6.5 must materialize a `config.yml.tmpl` **plus the `docker_cluster_config-*.json.tmpl` set that matches the deployment's stream-consuming workloads** into the build under whatever path becomes `SDR_CONTROLLER_CONFIG_PATH`. The WDM in `sdr-controller` only routes streams to a workload that has a `docker_cluster_config-<workload>.json.tmpl` — a missing per-workload cluster config means that workload's container gets **`Active sources: 0`** even though everything is "healthy."

**The template set is workload-dependent — pick by what is in the allow-list (Finding F-J, 2026-06-16):**

| Deployment | Model dir | Cluster-config templates to materialize |
|---|---|---|
| VIOS / streamprocessing only (e.g. IN-1 dense captioning, `alert_source=vlm-realtime`) | `developer-profiles/dev-profile-alerts/sdrc/2d_vlm/configs/` | `config.yml.tmpl` + `docker_cluster_config-streamprocessing.json.tmpl` |
| **Includes RT-CV** (`alert_source=cv-verification`; `perception-alerts` / `vss-rtvi-cv` in the allow-list) | `developer-profiles/dev-profile-alerts/sdrc/2d_cv/configs/` | `config.yml.tmpl` + `docker_cluster_config-streamprocessing.json.tmpl` + **`docker_cluster_config-rtvi-cv.json.tmpl`** |

Rule: materialize **one `docker_cluster_config-<workload>.json.tmpl` per stream-consuming workload in the allow-list.** RT-CV (`vss-rtvi-cv`) is such a workload, so a `cv-verification` build MUST use the `2d_cv` set; omitting `docker_cluster_config-rtvi-cv.json.tmpl` is what leaves RT-CV with `Active sources: 0` (no streams routed) while logs otherwise look clean. The matching `SDR_CONTROLLER_CONFIG_PATH=...sdrc/${MODE}` already encodes this via `MODE` (`2d_cv` vs `2d_vlm`) — keep the path and the materialized template set in sync with the chosen `MODE`. If the `*.tmpl` files are absent entirely, `sdrc-render-config` exits with `render-config: no *.tmpl files found in /tmpl`, the rest of the SDRC chain never runs, and `sdr-controller` never boots — leaving sensor-ms's `localhost:10000` call unanswered. (The legacy `./envoy.yaml` + `./sdr-config/` bind sources from the removed `services/vios/sdr/streamprocessing/` tree no longer apply.)

### Validation harness (NvStreamer)

When a Topology A profile needs a live RTSP source for validation and no real camera/RTSP URL was supplied, Step 6 emits the NvStreamer synthetic-RTSP harness. See `references/validation-harness.md` for the full contract; the NvStreamer → VIOS `POST /sensor/add` handoff sequence is documented there and in `vss-manage-video-io-storage/references/nvstreamer-api-reference.md § Canonical workflow`. NvStreamer is NOT in the `component_services:` block above.

## Emitted shape (patched example)

The patched VIOS + SDRC blocks are `include:`d from `<BUILD_DIR>/compose.yml`; deploy with `docker compose --env-file <BUILD_DIR>/.env -f <BUILD_DIR>/compose.yml --profile <invented-flag> up -d`. Canonical container names (verified live): `vss-vios-postgres`, `vss-vios-ingress`, `vss-vios-sensor`, `vss-vios-streamprocessing`, plus the SDRC `sdr-controller` + 5 init containers. Each gets the invented flag appended to its `profiles:` list in the patched copy. The `sdr-controller` block additionally mounts `${SDR_CONTROLLER_CONFIG_PATH}/configs:/configs/:ro`, `./log:/logs`, and the host docker socket; Patch 3 materializes the `*.tmpl` pair at the configs path. See the `## Example Compose Snippet` in `integrate-vios-service.md` for the full upstream block shapes this is patched from.
