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
| `SDR_CONTROLLER_CONFIG_PATH`, `SDRC_*_HOST_PORT` | Select rendered SDR config and host ports. |
| `NVSTREAMER_HTTP_PORT`, `NVSTREAMER_HTTP_HOST_PORT`, `NVSTREAMER_INSTALL_ADDITIONAL_PACKAGES` | Configure a profile's NvStreamer source. |
| `NUM_SENSORS`, `STREAM_TYPE` | Configure source count and broker type where supported. |

When a build changes `VSS_APPS_DIR` or a public host primitive, put every
selected dependent path and URL in the build `override.env`; Compose does not
re-expand values already read from the Foundation env files.

## Sources

- `deploy/docker/services/vios/compose.yml`
- `deploy/docker/services/vios/foundational/docker-compose.yaml`
- `deploy/docker/services/vios/initiator/docker-compose.yaml`
- `deploy/docker/services/vios/streamprocessing/docker-compose.yaml`
- `deploy/docker/services/infra/sdrc/docker-compose.yaml`
- `skills/vss-manage-video-io-storage/references/deploy-vios-service.md`
- `skills/vss-manage-video-io-storage/references/integrate-vios-service.md`
