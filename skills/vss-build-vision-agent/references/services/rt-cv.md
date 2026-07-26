# RT-CV Capability Owner

## Capabilities and service keys

| Capability | Canonical service profile keys |
|---|---|
| Alerts perception | `perception-alerts` |
| Search model initialization | `perception-2d-init` |
| Search detection and tracking | `perception-2d-fusion` |

## Required peers

- Use the service key defined by the selected developer profile; the shared
  `perception` service is an `extends` source, not a profile key.
- Kafka-backed pipelines require `kafka`, `kafka-topic-init-container`, and
  `broker-health-check`.
- Search RT-CV requires its matching init service and checked-in model/config
  mounts.
- Alerts CV mode normally feeds Behavior Analytics; search mode feeds Search
  analytics. Do not add both consumers unless explicitly requested.
- This is a singleton owner: one detector instance per build. When multiple
  pipelines or consumers need detection in one build, they share that single
  detector — resolve to one service key and one model family, not two.

## Configuration knobs

| Environment variable | Use |
|---|---|
| `PERCEPTION_IMAGE`, `PERCEPTION_TAG` | Select the RT-CV image. |
| `RT_CV_DEVICE_ID`, `RTVI_CV_PORT`, `RTVI_CV_HOST_PORT` | Select GPU and ports. |
| `MODEL_TYPE`, `MODEL_NAME_2D`, `DS_MODEL_FAMILY` | Select the detector/model family supported by mounted configs. This also fixes the **class-label taxonomy** — the exact class names and their casing emitted on `mdx-raw`. Different model families emit different label sets and casing, so Foundations that ship different families are not interchangeable here. |
| `VISION_ENCODER_MODEL`, `VISION_ENCODER_VERSION` | Select the Search vision encoder NGC package. |
| `NUM_SENSORS`, `STREAM_TYPE`, `DS_MESSAGE_RATE` | Configure input count and event transport. |
| `DS_TRACKER_REID`, `DS_SHOW_SENSOR_ID` | Toggle supported tracking metadata. |
| `HARDWARE_PROFILE`, `PERCEPTION_DOCKERFILE_PREFIX` | Select hardware-specific behavior exposed by the Foundation. |

Downstream consumers that filter on class labels (Behavior Analytics, for
instance) key on this detector's emitted taxonomy. In a combined build that
converges on a single detector, align those consumer configs to the resolved
model family's label set and casing, not to whatever a source profile's config
happened to ship.

## Sources

- `deploy/docker/services/rtvi/rtvi-cv/compose.yaml`
- `deploy/docker/developer-profiles/dev-profile-alerts/compose.yml`
- `deploy/docker/developer-profiles/dev-profile-search/video-analytics-2d-app/compose.yml`
- `deploy/docker/developer-profiles/dev-profile-search/video-analytics-2d-app/deepstream/scripts/download-vision-encoder.sh`
- `skills/vss-deploy-detection-tracking-2d/references/environment.md`
- `skills/vss-deploy-detection-tracking-2d/references/integrate-vss-detection-tracking-2d.md`
