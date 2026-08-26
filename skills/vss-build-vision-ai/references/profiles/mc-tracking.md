# Multi-Camera Tracking Developer Profile

## Capabilities and routing cues

- Calibrated multi-camera 3D tracking with RT-DETR, the MV3DT tracker, BEV
  fusion, VIOS/VST ingestion, Behavior Analytics, and Elasticsearch/Kibana.
- Choose this Foundation for generic MCT/multi-camera-tracking setup, an exact
  capability match, or a VIOS-backed customization. It deploys no VSS Agent,
  LLM, VLM, or agent UI.
- An explicit request to operate the checked-in `mc-tracking` developer profile
  as shipped belongs to `vss-deploy-profile`. An explicit request for the
  standalone RTVI-CV-3D/MV3DT stack belongs to
  `vss-deploy-detection-tracking-3d`.
- Do not call a non-VIOS stack the developer profile's `MINIMAL` variant: the
  checked-in MCT `MINIMAL` sets retain VIOS, Configurator, SDRC, and Behavior
  Analytics and remove only the dashboard/indexing/monitoring tier.

Detailed deployment and debugging procedures remain owned by
`skills/vss-deploy-profile/references/mc-tracking.md`; do not duplicate them
here.

## Profile Service Set

Authoritative source:
`deploy/docker/developer-profiles/dev-profile-mc-tracking/overrides.env`.
The stock default is the Kafka full-stack set:

```text
turnserver-init,turnserver,redis,init-dirs,render-config,wdm-env-from-config,wait-for-redis,wait-for-docker-workloads,sdr-controller,centralizedb,vst-ingress,sensor-bp-wait-bp-configurator,kafka,kafka-topic-init-container,broker-health-check,vss-behavior-analytics-mc-tracking,nvstreamer-mc-tracking,bp-configurator-mc-tracking-init,bp-configurator-mc-tracking,vss-rtvi-cv-bev-fusion-mc-tracking,vss-rtvi-cv-mc-tracking,mosquitto,sensor-ms-mc-tracking,streamprocessing-ms-mc-tracking,elasticsearch,elasticsearch-init-container,kibana,logstash,vss-haproxy-ingress,kibana-init-container-mc-tracking,vss-video-analytics-api-mc-tracking,import-calibration-output-container-mc-tracking,dcgm-exporter,prometheus,grafana,node-exporter,cadvisor
```

Use only a checked-in `COMPOSE_PROFILES_MC_TRACKING_*` set for stock mode:

- `KAFKA` or `REDIS` for the complete application.
- `KAFKA_MINIMAL` or `REDIS_MINIMAL` for the VIOS-backed application without
  ELK, Video Analytics API, calibration/dashboard import, ingress, or
  monitoring.
- `PLAYBACK_KAFKA` or `PLAYBACK_REDIS` for analytics playback without live
  perception and VIOS.

Keep `STREAM_TYPE` aligned with the selected set. Redis mode still includes
the shared `redis` service and `broker-health-check`; it omits Kafka and its
topic initializer.

## Capability owners present

| Owner | Service profile keys |
|---|---|
| RT-CV-3D | `vss-rtvi-cv-mc-tracking`, `vss-rtvi-cv-bev-fusion-mc-tracking`, `mosquitto` |
| Configurator | `bp-configurator-mc-tracking-init`, `bp-configurator-mc-tracking` |
| Behavior Analytics | `vss-behavior-analytics-mc-tracking` |
| VIOS | `nvstreamer-mc-tracking`, `sensor-ms-mc-tracking`, `streamprocessing-ms-mc-tracking`, `centralizedb`, `vst-ingress`, and the SDRC helper keys |
| ELK/broker | Kafka or Redis, `broker-health-check`, `elasticsearch`, `elasticsearch-init-container`, `logstash`, `kibana`, `kibana-init-container-mc-tracking` |
| Analytics API/calibration | `vss-video-analytics-api-mc-tracking`, `import-calibration-output-container-mc-tracking` |
| Ingress/monitoring | `vss-haproxy-ingress`, TURN, DCGM, Prometheus, Grafana, node-exporter, cAdvisor |

## Profile-specific environment knobs

| Knob | Purpose |
|---|---|
| `STREAM_TYPE`, `COMPOSE_PROFILES` | Select the matching Kafka/Redis and full/minimal/playback set. |
| `NUM_STREAMS`, `SAMPLE_VIDEO_DATASET` | Match the camera count and calibrated dataset; the stock sample uses four cameras. |
| `RT_CV_DEVICE_ID`, `RTVI_CV_MV3DT_HOST_PORT`, `RTVI_CV_MV3DT_PORT` | Place and expose RT-CV-3D. |
| `VSS_RT_CV_MV3DT_BEV_FUSION_IMAGE`, `VSS_RT_CV_MV3DT_BEV_FUSION_TAG` | Select BEV fusion. |
| `MQTT_HOST`, `MQTT_PORT` | Connect perception and BEV fusion through Mosquitto. |
| `SENSOR_FILE_PATH`, `NVSTREAMER_CONFIG_DIR`, `SDR_CONTROLLER_CONFIG_PATH` | Resolve profile-owned camera, NvStreamer, and SDRC configuration. |
| `BP_CONFIGURATOR_ENV_FILE` | Point the MCT Configurator at the absolute build `override.env`; include its required deployment-value closure because the checked-in `overrides.env` contains placeholders. |
| `NGC_CLI_API_KEY`, `DS_MODEL_DOWNLOAD` | Download RT-DETR and BodyPose3DNet on first start. |

## Composition guardrails

- Calibration and `camInfo` must match every selected camera. The stock sample
  is already calibrated; for custom media with missing calibration, hand off to
  `vss-generate-video-calibration` before deployment.
- Set `BP_CONFIGURATOR_ENV_FILE=${BUILD_DIR}/override.env` and materialize the
  Configurator's required host/path/broker values there. Otherwise the
  container reads the checked-in placeholder override layer even though
  Compose resolution used the build override.
- Treat RT-CV-3D plus BEV fusion as one singleton tracking pipeline. Do not add
  a second RT-CV detector or combine it with Search/Alerts unless a validated
  owner contract explicitly supports the combination.
- Do not add VSS Agent, LLM, VLM, or agent UI to stock MCT. MCT is externally
  consumable through its service APIs, not an agent deployment.
- Do not apply the generic VLM/LLM Docker-bridge firewall rule to stock MCT.
  Inspect firewall state read-only and validate local endpoints first. Any
  remote browser or proven service reachability rule requires one explicit,
  least-privilege approval; denial ends that mutation path.
- The profile is domain-neutral for calibrated person/forklift tracking. Add
  domain assets as a delta; do not recreate a Warehouse MV3DT profile.

## Stock readiness checks

```bash
curl -sf "http://${HOST_IP}:${RTVI_CV_MV3DT_HOST_PORT:-9000}/ready"
curl -sf "http://${HOST_IP}:${NVSTREAMER_HTTP_HOST_PORT:-31000}/api/v1/sensor/version"
curl -sf "http://${HOST_IP}:${SENSOR_HTTP_HOST_PORT:-30000}/docs"
curl -sf "http://${HOST_IP}:${STREAM_PROCESSOR_HTTP_HOST_PORT:-30001}/docs"
curl -sf "http://${HOST_IP}:${VST_INGRESS_HOST_PORT:-30888}/api/v1/sensor/version"
```

For a full set, also probe Elasticsearch, Kibana, and Video Analytics API.
Readiness is not complete until RT-CV reports all expected active sources and
the selected broker's tracking topics advance.

## Sources

- `deploy/docker/developer-profiles/dev-profile-mc-tracking/.env`
- `deploy/docker/developer-profiles/dev-profile-mc-tracking/overrides.env`
- `deploy/docker/developer-profiles/dev-profile-mc-tracking/compose.yml`
- `deploy/docker/services/rtvi/rtvi-cv/rtvi-cv-mv3dt/compose.yaml`
- `skills/vss-deploy-profile/references/mc-tracking.md`
- `skills/vss-deploy-detection-tracking-3d/SKILL.md`
