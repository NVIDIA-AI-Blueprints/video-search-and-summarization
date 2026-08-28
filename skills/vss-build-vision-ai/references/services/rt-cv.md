# RT-CV Capability Owner

## Capabilities and service keys

| Capability | Canonical service profile keys | Foundation | Model family |
|---|---|---|---|
| Alerts perception | `perception-alerts` | `alerts` | GDINO |
| Search detection and tracking | `perception-2d-fusion` | `search` | RT-DETR |

Select the Foundation that ships the requested model family. RT-DETR and
GDINO are not interchangeable — each requires its own configs, mounts, and
class-label taxonomy. The detector model family and its emitted class-label
taxonomy are authoritatively defined in
`skills/vss-deploy-detection-tracking-2d/references/integrate-vss-detection-tracking-2d.md`;
the mapping above is the composition surface, not a second source of truth.

## Required peers

- Use the service key defined by the selected developer profile; the shared
  `perception` service is an `extends` source, not a profile key.
- Kafka-backed pipelines require `kafka`, `kafka-topic-init-container`, and
  `broker-health-check`.
- Search RT-CV requires checked-in model/config mounts; model download is
  handled by ds-start phase 0 when `DS_MODEL_DOWNLOAD=auto`.
- Alerts CV mode normally feeds Behavior Analytics; search mode feeds Search
  analytics. Do not add both consumers unless explicitly requested.
- This is a singleton owner: one detector instance per build. When multiple
  pipelines or consumers need detection in one build, they share that single
  detector — resolve to one service key and one model family, not two.
- Selecting or changing the detector/model family is done through the env knobs
  below (`MODEL_TYPE`, `MODEL_NAME_2D`, `DS_MODEL_FAMILY`, `VISION_ENCODER_*`).
  The detector ONNX (and the Search SigLIP vision encoder) is downloaded by the
  RT-CV container at first boot (ds-start phase 0 when `DS_MODEL_DOWNLOAD=auto`)
  from its mounted `models-download.json` into `${VSS_DATA_DIR}/models/`; no
  host-side staging is required. This changes **no service definition**, so it
  needs no `patches/` entry: do not patch `perception-2d-fusion` for a model or
  detector swap.

## Configuration knobs

| Environment variable | Use |
|---|---|
| `VSS_RT_CV_IMAGE`, `VSS_RT_CV_TAG` | Select the RT-CV image. |
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

## Placement and sizing

RT-CV has a fixed footprint determined primarily by its model family and stream
count. Prefer a dedicated device; share only when the measured combined budget
fits. See `../sizing.md` for placement resolution and starting stream counts.

## Sources

- `deploy/docker/services/rtvi/rtvi-cv/compose.yaml`
- `deploy/docker/developer-profiles/dev-profile-alerts/compose.yml`
- `deploy/docker/developer-profiles/dev-profile-search/video-analytics-2d-app/compose.yml`
- `deploy/docker/services/rtvi/rtvi-cv/ds-start.sh`
- `skills/vss-deploy-detection-tracking-2d/references/environment.md`
- `skills/vss-deploy-detection-tracking-2d/references/integrate-vss-detection-tracking-2d.md`
