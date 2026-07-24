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

## Configuration knobs

| Environment variable | Use |
|---|---|
| `PERCEPTION_IMAGE`, `PERCEPTION_TAG` | Select the RT-CV image. |
| `RT_CV_DEVICE_ID`, `RTVI_CV_PORT`, `RTVI_CV_HOST_PORT` | Select GPU and ports. |
| `MODEL_TYPE`, `MODEL_NAME_2D`, `DS_MODEL_FAMILY` | Select the detector/model family supported by mounted configs. |
| `VISION_ENCODER_MODEL`, `VISION_ENCODER_VERSION` | Select the Search vision encoder NGC package. |
| `NUM_SENSORS`, `STREAM_TYPE`, `DS_MESSAGE_RATE` | Configure input count and event transport. |
| `DS_TRACKER_REID`, `DS_SHOW_SENSOR_ID` | Toggle supported tracking metadata. |
| `HARDWARE_PROFILE`, `PERCEPTION_DOCKERFILE_PREFIX` | Select hardware-specific behavior exposed by the Foundation. |

## Sources

- `deploy/docker/services/rtvi/rtvi-cv/compose.yaml`
- `deploy/docker/developer-profiles/dev-profile-alerts/compose.yml`
- `deploy/docker/developer-profiles/dev-profile-search/video-analytics-2d-app/compose.yml`
- `deploy/docker/developer-profiles/dev-profile-search/video-analytics-2d-app/deepstream/scripts/download-vision-encoder.sh`
- `skills/vss-deploy-detection-tracking-2d/references/environment.md`
- `skills/vss-deploy-detection-tracking-2d/references/integrate-vss-detection-tracking-2d.md`
