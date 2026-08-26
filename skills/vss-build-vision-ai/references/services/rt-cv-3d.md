# RT-CV-3D Multi-Camera Tracking Capability Owner

## Capabilities and service keys

| Capability | Canonical service profile keys | Foundation |
|---|---|---|
| VIOS-backed multi-camera 3D tracking | `vss-rtvi-cv-mc-tracking`, `vss-rtvi-cv-bev-fusion-mc-tracking`, `mosquitto` | `mc-tracking` |

The tracker is the MV3DT configuration of RTVI-CV-3D. Treat the perception,
BEV-fusion, and Mosquitto services as one capability owner, not three optional
features.

## Required peers

- Select exactly one transport mode. Kafka requires `kafka`,
  `kafka-topic-init-container`, and `broker-health-check`; Redis transport
  requires `redis` and `broker-health-check`. The Kafka profile still retains
  the shared `redis` service used by VIOS and SDRC.
- The developer-profile path requires `bp-configurator-mc-tracking`, the
  MCT-specific NvStreamer, sensor, stream-processing, SDRC, and VIOS core keys
  listed in `../profiles/mc-tracking.md`.
- Behavior Analytics is part of the VIOS-backed MCT application. ELK,
  dashboards, Video Analytics API, calibration import, ingress, and monitoring
  are removable only through the checked-in MCT minimal sets or an equivalent
  validated delta.
- Calibration and `camInfo` are hard prerequisites. Use the checked-in sample
  calibration for the sample dataset; otherwise hand off to
  `vss-generate-video-calibration` before composing the deployment.
- Model download is handled by `ds-start-mc-tracking.sh` from the profile's
  `models-download.json`; do not stage RT-DETR or BodyPose3DNet in a build
  patch.
- This owner is a singleton. Do not combine it with `perception-alerts` or
  `perception-2d-fusion` unless a separately validated integration defines the
  detector taxonomy, topics, calibration, and downstream consumers.

## Standalone boundary

Only an explicit request for the standalone RTVI-CV-3D/MV3DT stack routes to
`vss-deploy-detection-tracking-3d`. That skill owns the smaller service Compose,
input-mode selection, calibration handoff, OSD/saved output, and runtime
verification. Keep unqualified MCT requests in Build Vision Agent. If requested
customization conflicts with this owner's required peers, clarify the intended
deployment rather than silently removing peers or switching skills.

## Configuration knobs

| Environment variable | Use |
|---|---|
| `PERCEPTION_IMAGE`, `PERCEPTION_TAG` | Select the RTVI-CV image. |
| `RT_CV_DEVICE_ID`, `RTVI_CV_MV3DT_HOST_PORT`, `RTVI_CV_MV3DT_PORT` | Select GPU and ports. |
| `NUM_STREAMS`, `SAMPLE_VIDEO_DATASET` | Match the calibrated camera set. |
| `STREAM_TYPE` | Select Kafka or Redis transport. |
| `MQTT_HOST`, `MQTT_PORT` | Connect the per-camera trackers and BEV fusion. |
| `VSS_RT_CV_MV3DT_BEV_FUSION_IMAGE`, `VSS_RT_CV_MV3DT_BEV_FUSION_TAG` | Select BEV fusion. |
| `NGC_CLI_API_KEY`, `DS_MODEL_DOWNLOAD` | Control first-start model acquisition. |

## Placement and sizing

RTVI-CV-3D and BEV fusion use the MCT Foundation's reviewed placement. Preserve
`RT_CV_DEVICE_ID` in stock mode and validate actual utilization at the selected
camera count. See `../sizing.md`; do not infer an LLM/VLM budget because the
stock MCT profile contains neither.

## Sources

- `deploy/docker/developer-profiles/dev-profile-mc-tracking/compose.yml`
- `deploy/docker/developer-profiles/dev-profile-mc-tracking/.env`
- `deploy/docker/developer-profiles/dev-profile-mc-tracking/overrides.env`
- `deploy/docker/services/rtvi/rtvi-cv/rtvi-cv-mv3dt/compose.yaml`
- `skills/vss-deploy-detection-tracking-3d/SKILL.md`
- `skills/vss-deploy-profile/references/mc-tracking.md`
