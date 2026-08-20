# VSS MC-Tracking Profile — Reference

Profile: `mc-tracking` | Blueprint: `bp_developer_mc-tracking` | Mode: `mc-tracking`

Multi-camera 3D person/forklift tracking with BEV (bird's-eye-view) fusion, calibration import, and behavior analytics, packaged as a standalone developer profile — its own compose file, env files, camera config, calibration assets, and DeepStream/SDR-controller configs all live under `developer-profiles/dev-profile-mc-tracking/`.

## What's different from `base` / `search` / `lvs` / `alerts`

- **No VSS Agent, agent UI, LLM NIM, or VLM NIM.** This profile is perception + tracking + analytics only — there's no conversational/report-generation layer.
- **No Cosmos Embed / RT-VLM.** Detection is RT-DETR (Person = class 0, Forklift = class 1) feeding a multi-camera tracker and BEV fusion, not an embedding or captioning pipeline.
- **Uses the full VIOS stack** (`sensor-ms`, `streamprocessing-ms`, `nvstreamer`, `vst-ingress`) plus `bp-configurator` (dynamic per-camera config generation) and `sdr-controller` (SDR/WDM: provisions camera streams onto the perception pod at runtime).
- **Two message-broker variants** (`STREAM_TYPE=kafka` or `redis`) and **minimal / playback variants** — see `COMPOSE_PROFILES_MC_TRACKING_*` in `overrides.env`.

## What gets deployed

Container names are the actual `container_name:` keys in `deploy/docker/developer-profiles/dev-profile-mc-tracking/compose.yml` and the shared service files it pulls in.

| Service | Container | Port | Purpose |
|---|---|---|---|
| Perception (RT-DETR + tracker) | `vss-rtvi-cv-mc-tracking` | 9000 | DeepStream pipeline; extends the shared `rtvi-cv-mv3dt` base (`services/rtvi/rtvi-cv/rtvi-cv-mv3dt/compose.yaml`) |
| BEV Fusion | `vss-rtvi-cv-bev-fusion-mc-tracking` | — | Cross-camera measurement fusion into a shared BEV coordinate frame; extends the same shared base's `measurement-fusion` |
| bp-configurator (+ init) | `vss-configurator-mc-tracking`, `vss-configurator-mc-tracking-init` | — | Dynamic per-deploy config generation (camera list, DS main config, vst-config.json) — extends the centralized `bp-configurator-base` |
| nvstreamer | `vss-vios-nvstreamer-mc-tracking` | 31000 | Sample-video RTSP source / upload UI |
| sensor-ms | `vss-vios-sensor` | 30000 | Camera/sensor registration (VMS); shared block `sensor-ms-mc-tracking` in `services/vios/initiator/` |
| streamprocessing-ms | `vss-vios-streamprocessing` | 30001, 30554–30564 | Re-transcodes/relays camera streams; shared block `streamprocessing-ms-mc-tracking` in `services/vios/streamprocessing/` |
| VST ingress | `vss-vios-ingress` | 30888 | Video storage/ingest |
| sdr-controller | `sdr-controller` | 5003 (control), 10000 (proxy), 8011 | Provisions/deprovisions camera streams on `vss-rtvi-cv-mc-tracking` and `vss-vios-streamprocessing` at runtime |
| Behavior Analytics (+ playback) | `vss-behavior-analytics-mc-tracking`, `vss-behavior-analytics-playback-mc-tracking` | — | Tracks → zone/behavior events |
| Video Analytics API | `vss-video-analytics-api-mc-tracking` | 8081 | REST API over analytics/incidents |
| Calibration import | `vss-import-calibration-output-mc-tracking` | — | One-shot: imports `calibration/sample-data/.../calibration.json` into Video Analytics API |
| Elasticsearch + Logstash + Kibana (+ init) | `elasticsearch`, `logstash`, `kibana`, `vss-kibana-init-mc-tracking` | 9200, 5601 | Analytics index + dashboards |
| Kafka **or** Redis | `kafka` / `redis` | 9092 / 6379 | Message bus (pick one via `STREAM_TYPE`) |
| Mosquitto | `mosquitto` | 1883 | Cross-camera MQTT (tracker pub/sub) |
| HAProxy ingress, TURN server | `vss-haproxy-ingress`, `vss-turnserver` | 7777, 3478 | Browser-facing ingress / WebRTC relay |
| DCGM/Prometheus/Grafana/node-exporter/cAdvisor | — | 9400, 9090, 35000, 19100, 18080 | Optional monitoring stack |

## Detection classes

Person (class 0) and Forklift (class 1) — no other classes are tracked by this profile.

## Sample dataset

`SAMPLE_VIDEO_DATASET="warehouse-4cams-20mx20m-synthetic"` (4 synthetic cameras, 20m × 20m floor) is the default. `NUM_STREAMS` in `.env` must match the camera count in the selected dataset (default `4`). Calibration, camInfo, and imagery for this dataset live under `developer-profiles/dev-profile-mc-tracking/calibration/sample-data/warehouse-4cams-20mx20m-synthetic/`.

## Hardware profiles

Valid `HARDWARE_PROFILE` values: `H100`, `L4`, `L40S`, `RTXA6000`, `RTXA6000ADA`, `RTXPRO6000BW`, `RTXPRO4500BW`, `IGX-THOR`, `DGX-SPARK`.

- `PERCEPTION_TAG="3.3.0-26.07.2"` by default; switch to the `-sbsa-` variant for DGX Spark / IGX Thor / SBSA platforms (commented alternative in `.env`).
- `RT_CV_DEVICE_ID` (perception/BEV-fusion GPU) defaults unscoped (`'0'`) — this profile isn't expected to run concurrently with another profile on the same host, so no reserved/shared-device-id bookkeeping is needed. NVStreamer itself needs no GPU (per `services/nvstreamer/base.yml`'s design) and carries no device reservation.

## Hard rules

- **No VSS Agent / agent UI / LLM / VLM in the resolved service set** — if you see any of those containers in `docker compose config --services`, something is wrong with `COMPOSE_PROFILES`.
- **This profile's compose, env files, camera config, calibration, and SDR-controller config are all self-contained under `dev-profile-mc-tracking/`.**
- **Not expected to run concurrently with another profile on the same host** — GPU device IDs and container names are not namespaced for coexistence beyond what Compose's single shared namespace already requires (all `mc-tracking` service/container names carry an explicit `-mc-tracking` suffix so they don't collide with other profiles at the Compose-file level, but device IDs and ports are not defensively partitioned).
- **`sdrc/` path is hardcoded** to this profile's own `sdrc/` directory (`SDR_CONTROLLER_CONFIG_PATH`) — no `MODE`-based path selection, since this profile has exactly one mode.

## Env file location

```
deploy/docker/containers.env
deploy/docker/developer-profiles/dev-profile-mc-tracking/.env
deploy/docker/developer-profiles/dev-profile-mc-tracking/overrides.env
deploy/docker/developer-profiles/dev-profile-mc-tracking/generated.env
```

`containers.env` is shared across every profile (image/tag defaults) and is always the first `--env-file` in the deploy/teardown chain below.

Set `VSS_APPS_DIR` (repo's `deploy/docker` path) and `VSS_DATA_DIR` (data directory for videos, playback, calibration, runtime logs, RT-CV model cache) in `overrides.env`/`generated.env` before first deploy — both ship as `/path/to/...` placeholders.

## Deploy

Follow the umbrella skill's standard flow (Steps 1c–5b) with `PROFILE=mc-tracking`, or run directly:

```bash
cd deploy/docker

cp developer-profiles/dev-profile-mc-tracking/overrides.env \
   developer-profiles/dev-profile-mc-tracking/generated.env
# edit generated.env: VSS_APPS_DIR, VSS_DATA_DIR, HOST_IP, NGC_CLI_API_KEY, ...

docker compose -f compose.yml \
  --env-file containers.env \
  --env-file developer-profiles/dev-profile-mc-tracking/.env \
  --env-file developer-profiles/dev-profile-mc-tracking/generated.env \
  up -d
```

This uses the same three-file `--env-file` chain (`containers.env`, profile `.env`, profile `generated.env`) and `dev-profile-<profile>/{.env,overrides.env,generated.env}` layout as the rest of the profiles — `mc-tracking` is deployed with direct `docker compose` commands rather than through `dev-profile.sh`.

## Perception model download (automatic)

Same manifest-driven pattern as other profiles: `ds-start-mc-tracking.sh` downloads models automatically via `models-download.json` when `DS_MODEL_DOWNLOAD=auto` (the default). Ensure `NGC_CLI_API_KEY` is set and `$VSS_DATA_DIR/models` exists and is writable before first deploy. RT-CV builds a TensorRT engine from the downloaded models on first start (a few minutes) — the engine cache persists under `$VSS_DATA_DIR/models/` across ordinary restarts.

## Endpoints (after deploy)

| Service | URL |
|---|---|
| nvstreamer UI | `http://<HOST_IP>:31000/` |
| Kibana | `http://<HOST_IP>:5601/` (or through HAProxy ingress at `${PUBLIC}/kibana`) |
| Video Analytics API | `http://<HOST_IP>:8081/` |
| sensor-ms (VMS) | `http://<HOST_IP>:30000/` |
| streamprocessing-ms | `http://<HOST_IP>:30001/` |
| VST ingress | `http://<HOST_IP>:30888/` |
| Elasticsearch | `http://<HOST_IP>:9200/` |

## Teardown

Manual teardown commands (ordinary stop, full reset with `-v --rmi all`, dangling-volume cleanup, and `cleanup_all_datalog.sh` usage) are documented in [`deploy/docker/README.md` § MC-Tracking developer profile](../../../deploy/docker/README.md#mc-tracking-developer-profile) — follow that section rather than `references/teardown.md` (which assumes the `dev-profile.sh`-managed profiles).

## Debugging

Perception/provisioning failures in this profile are almost always one of the five issues below, roughly in the order you'll hit them on a fresh deploy:

- **`bp-configurator` exits with `HOST_IP must be set ... placeholder '<HOST_IP>'`** — its `env_file` defaults to `overrides.env`, which still has the placeholder, not `generated.env`. Set `BP_CONFIGURATOR_ENV_FILE=<absolute path to generated.env>` in `generated.env` before deploying.
- **`bp-configurator`'s `file_management` step fails with "Directory not found"** for the sample video dataset — symlinks into `$VSS_DATA_DIR/videos/...` don't resolve correctly inside the container's mount namespace. Copy the sample video/playback files into the data dir directly (`cp -a`, not `ln -s`).
- **`kibana` unhealthy, logs show an Elasticsearch version mismatch** (e.g. Kibana `9.4.4` vs a stale locally-cached `elasticsearch:9.3.3`) — rebuild the Elasticsearch image: `docker compose build elasticsearch` (it's pinned to the matching version in `services/infra/Dockerfiles/elasticsearch.Dockerfile`), then recreate the container.
- **`vss-behavior-analytics-mc-tracking` / `vss-video-analytics-api-mc-tracking` restart-looping with `EACCES`** — `bp-configurator` rewrites config files but preserves their original restrictive permissions, and `data_log/vss_video_analytics_api/` gets auto-created `root:root`. Fix with `chmod -R o+rX` on the rewritten config dirs and `chmod -R 777` on `data_log/vss_video_analytics_api`.
- **`vss-rtvi-cv-mc-tracking` stuck at 0 FPS, logs flooded with `uri:/api/v1/stream/remove` and no `stream/add`** — this is stale provisioning state, not a code bug. Root cause: `sdr-controller` (WDM) caches "what's currently provisioned on this pod" in a Redis hash (`vss-rtvi-cv-mc-tracking`) and sensor identity in Postgres (`vss_vios_pg_data`), neither of which is cleared by a plain `docker compose down` (no `-v`). After a non-destructive teardown + redeploy, those caches can point at camera UUIDs that no longer exist, so `sdr-controller` retries `stream/remove` (`500 STREAM_REMOVE_FAIL, No record found`) forever and never reaches `stream/add`. Fix: `docker exec redis redis-cli DEL vss-rtvi-cv-mc-tracking rtvi-cv-mc-tracking-data vss-rtvi-cv-mc-tracking-pod && docker restart sdr-controller` — or, more reliably, tear down with `-v` (wipes Postgres + Redis) before redeploying. Confirm the fix with `docker logs vss-rtvi-cv-mc-tracking | grep 'Active sources'` (should read 4, not 0) and `docker logs sdr-controller | grep -o '\(add\|delete\) operation Response Code: [0-9]*' | sort | uniq -c` (should show `200`s, not a `remove`-only loop).
- **Shell-exported vars silently override `generated.env`** — if `PERCEPTION_TAG`, `VSS_RT_CV_MV3DT_BEV_FUSION_IMAGE/TAG`, or `NGC_CLI_API_KEY` were ever `source`d into the current shell (e.g. from an earlier `source .env`), Compose gives OS env vars precedence over `--env-file`, and a stray literal-quote-baked value (`PERCEPTION_TAG="3.3.0-26.07.2"` with the quotes taken literally) produces `invalid reference format` on `up -d`. `env | grep -E "PERCEPTION_TAG|VSS_RT_CV_MV3DT_BEV_FUSION|NGC_CLI_API_KEY"` and `unset` anything present before redeploying.

For a clean, known-good reset covering the last three issues at once: `docker compose ... down -v --rmi all` (wipes Postgres/Redis/images) + `docker volume ls -q -f dangling=true -f label=com.docker.compose.project=${COMPOSE_PROJECT_NAME:-vss} | xargs -r docker volume rm` (scoped to this project's volumes — an unscoped `dangling=true` filter would also delete dangling volumes from unrelated stopped containers/apps on the host), then redeploy. This forces a rebuild of any locally-built images (Elasticsearch, init containers), so expect the first `up -d` after a `-v --rmi all` teardown to take several minutes longer than an ordinary redeploy.
