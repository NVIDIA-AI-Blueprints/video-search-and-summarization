# VIOS Capability Owner

## Capabilities and service keys

| Capability | Canonical service profile keys |
|---|---|
| Video database and ingest | `centralizedb`, `vst-ingress` |
| Sensor and stream management | `sensor-ms`, `streamprocessing-ms`, `sensor-ms-<mode>`, `streamprocessing-ms-<mode>` |
| Profile stream sources | `nvstreamer-alerts`, `nvstreamer-lvs`, `nvstreamer-2d-fusion`, `nvstreamer-2d`, `nvstreamer-3d` |
| SDR controller and config rendering | `init-dirs`, `render-config`, `wdm-env-from-config`, `wait-for-redis`, `sdr-controller` |
| WebRTC relay for VST playback | `turnserver`, `turnserver-init` |

## Required peers

- `centralizedb`, `vst-ingress`, `sensor-ms`, and `streamprocessing-ms` form the
  normal developer VIOS core.
- SDR-controlled profiles require the full helper sequence shown above and
  `redis`. No capability names these helpers, so only their status as VIOS peers
  keeps them out of the forward-closure prune in
  [`../composition.md`](../composition.md).
- `turnserver` and `turnserver-init` are required wherever VST playback is
  served, alongside `sensor-bp-wait-bp-configurator`, which gates sensor
  registration on the configurator ([`configurator.md`](configurator.md)).
- NvStreamer variants require the matching developer profile's mounted configs
  and, where declared, `broker-health-check`.
- `vios-apt-cache-init` has no `profiles:` gate and is a `depends_on` of
  `streamprocessing-ms-*`; it resolves into every build and cannot be pruned.
- Add only the profile-specific NvStreamer key; do not activate multiple
  variants for one source.

## Configuration knobs

| Environment variable | Use |
|---|---|
| `VSS_APPS_DIR`, `VSS_DATA_DIR`, `VST_CONFIG_PATH` | Resolve checked-in configs and persistent data. |
| `VST_INGRESS_HOST_PORT`, `SENSOR_HTTP_HOST_PORT`, `STREAM_PROCESSOR_HTTP_HOST_PORT` | Publish VIOS APIs. |
| `RTSP_SERVER_HOST_PORT`, `RTSP_SERVER_HOST_PORT_END` | Publish RTSP playback ports. |
| `VST_BASE_URL`, `VST_INTERNAL_URL`, `VST_EXTERNAL_URL`, `VST_MCP_URL` | Configure internal and public routing. |
| `VST_NGINX_MODE` | Select direct or SDRC routing supported by the Foundation. |
| `VST_ENABLE_NOTIFICATION` | Publish VIOS lifecycle events (`camera_add` / `camera_streaming` / `camera_remove`) to Redis `vst.event`. The VIOS composes default it to `${VST_USE_SDRC:-false}`, so it flips with the routing mode unless the build pins it — pin it whenever a build changes `VST_USE_SDRC`. |
| `VST_NOTIFICATION_CONFIG_PATH` | Select the notification config bind-mounted over the fixed `configs/notification_config.json` in both `sensor-ms` and `streamprocessing-ms`. Defaults to the shared, webhooks-disabled `${VSS_APPS_DIR}/services/vios/configs/notification_config.json`. |
| `SDR_CONTROLLER_CONFIG_PATH`, `SDRC_*_HOST_PORT` | Select rendered SDR config and host ports. |
| `NVSTREAMER_HTTP_PORT`, `NVSTREAMER_HTTP_HOST_PORT`, `NVSTREAMER_INSTALL_ADDITIONAL_PACKAGES` | Configure a profile's NvStreamer source. |
| `NUM_SENSORS`, `STREAM_TYPE` | Configure source count and broker type where supported. |

When a build changes `VSS_APPS_DIR` or a public host primitive, put every
selected dependent path and URL in the build `override.env`; Compose does not
re-expand values already read from the Foundation env files.

## Notification config — a converged singleton

The mounted `notification_config.json` **is** the fan-out policy: VIOS posts
sensor lifecycle events (`camera_streaming`, `camera_remove`) to the webhook
receivers it defines. It is a singleton config on a singleton service, so every
build resolves it explicitly, exactly like the Behavior Analytics joint config.

- **The invariant: the mounted config's receiver set equals the build's deployed
  consumer set.** Inheriting a Foundation's file breaks it both ways — a build
  that *adds* a capability leaves the new consumer unprovisioned; one that drops
  a capability leaves an orphaned receiver failing every delivery, and with no
  webhook introspection that failure is log-identical to a real one, so the
  noise costs the only signal there is. Resolve against the deployed set, never
  against the profile the build started from — inheriting is the right answer
  only when the build leaves that Foundation's consumer set unchanged.
- **Selection is env-indirected; no Compose patch.** Point
  `VST_NOTIFICATION_CONFIG_PATH` in the build `override.env` at the chosen file.
  The file is bind-mounted into both `sensor-ms` and `streamprocessing-ms` at
  the fixed container path `configs/notification_config.json` and read once at
  process start — a change needs a container restart, and the two mount points
  must resolve the same file.
- **Match on the receiver set, not the file name.** The shipped configs all live
  in `${VSS_APPS_DIR}/services/vios/configs/`; their names record the profile
  pair they were introduced for, not who may mount them:
  - **RT-CV + RT-Embed** → `notification_config_search_alerts_2d_cv.json`
  - **RT-CV + RT-Embed + Alert Bridge** → `notification_config_search_alerts_2d_vlm.json`
  - **no webhook fan-out** → `notification_config.json`, the shared default
    (webhooks disabled)

  Both joint configs target the converged RT-CV at `vss-rtvi-cv:9000` and keep
  the `camera_remove` Elasticsearch cleanup receivers (detection, behavior, and
  filtered-embedding indices) verbatim. Any other receiver set — one carrying
  RT-VLM tagging, for instance — has no shipped match; author it below. Never
  mount a config out of a Foundation's directory: it would tie this build's
  fan-out to that profile's future edits.
- **No shipped config matches the deployed set → author a pruned one.** Copy the
  closest shipped file to `_builds/<name>/configs/notification_config.json`,
  delete the `request[]` entries for consumers this build does not deploy (from
  **both** the `camera_streaming` and `camera_remove` items), and point
  `VST_NOTIFICATION_CONFIG_PATH` at it with an absolute `${BUILD_DIR}/configs/`
  path. Prune only; do not retune `timeout_ms`, `retry`, headers, or
  `user_defined_metadata`, which carry the tested values.
- **The joint configs omit RT-VLM tagging on purpose.** When the single
  `rtvi-vlm` is bridge-driven for alerting, continuous tagging on it is a
  capacity decision, not an inherited default. Add it back only when the
  always-on rules share the same decode settings (`chunk_duration` etc.) and the
  instance has headroom — a second caption request with a different decode
  signature is rejected `400 BadParameters`. That receiver is what feeds BM25
  tag search, so a build owing both tag search and bridge-driven alerting must
  reconcile the two decode signatures; it cannot have the receiver on any other
  terms.
- **Webhooks are independent of `message_broker.enable_notification`.** The
  factory gates only the Redis/Kafka publisher on that flag; the webhook
  notifier turns on the presence of enabled items alone.
  `VST_ENABLE_NOTIFICATION=false` with `webhooks.enabled: true` is valid and
  intended.
- **`VST_ENABLE_NOTIFICATION` overrides the JSON producer flag** — applied after
  the JSON load, so the file's `enable_notification` never wins; pin the env var
  in the build rather than trusting the file.
  `enable_notification_consumer` has **no** env override and is live: it gates
  the `LiveMetadataStore` Kafka consumer feeding the live bbox overlay off
  `mdx-raw`, so the joint configs keep it `true`.
- **RT-CV port is a three-place coupling**: the webhook URL, `RTVI_CV_PORT`
  (container side of the `ports:` mapping), and `http-port` in the mounted
  DeepStream run config must agree. A build that remaps the port changes all
  three and forks the joint file.

## Sources

- `deploy/docker/services/vios/compose.yml`
- `deploy/docker/services/vios/foundational/docker-compose.yaml`
- `deploy/docker/services/vios/initiator/docker-compose.yaml`
- `deploy/docker/services/vios/streamprocessing/docker-compose.yaml`
- `deploy/docker/services/vios/configs/notification_config_search_alerts_2d_cv.json`
- `deploy/docker/services/vios/configs/notification_config_search_alerts_2d_vlm.json`
- `deploy/docker/services/infra/sdrc/docker-compose.yaml`
- `skills/operations/vss-manage-video-io-storage/references/deploy-vios-service.md`
- `skills/operations/vss-manage-video-io-storage/references/integrate-vios-service.md`
