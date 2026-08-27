# VSS MC-Tracking Profile — Reference

Profile: `mc-tracking` | Blueprint: `bp_developer_mc-tracking` | Mode: `mc-tracking`

Multi-camera 3D person/forklift tracking with BEV (bird's-eye-view) fusion, appearance-based re-identification, calibration import, and behavior analytics, packaged as a standalone developer profile — its own compose file, env files, camera config, calibration assets, and DeepStream/SDR-controller configs all live under `developer-profiles/dev-profile-mc-tracking/`.

## What's different from `base` / `search` / `lvs` / `alerts`

- **No VSS Agent, agent UI, LLM NIM, or VLM NIM.** This profile is perception + tracking + analytics only — there's no conversational/report-generation layer.
- **No Cosmos Embed / RT-VLM.** Detection is RT-DETR (Person = class 0, Forklift = class 1) feeding a multi-camera tracker and BEV fusion, not a video-captioning pipeline.
- **Appearance ReID is on by default.** The tracker extracts CLIP-ReID embeddings in-pipeline and delegates re-association to an external `reid-embed` service backed by Milvus, which also emits SigLIP2 secondary embeddings on `mdx-compressed-embeddings`. See [ReID embedding and re-association](#reid-embedding-and-re-association).
- **Uses the full VIOS stack** (`sensor-ms`, `streamprocessing-ms`, `nvstreamer`, `vst-ingress`) plus `bp-configurator` (dynamic per-camera config generation) and `sdr-controller` (SDR/WDM: provisions camera streams onto the perception pod at runtime).
- **Two message-broker variants** (`STREAM_TYPE=kafka` or `redis`) and **minimal / playback variants** — see `COMPOSE_PROFILES_MC_TRACKING_*` in `overrides.env`. The ReID stack is included in the Kafka, Redis, and both `_MINIMAL` variants; the `_PLAYBACK` variants do not run perception, so they omit it.

## What gets deployed

Container names are the actual `container_name:` keys in `deploy/docker/developer-profiles/dev-profile-mc-tracking/compose.yml` and the shared service files it pulls in.

| Service | Container | Port | Purpose |
|---|---|---|---|
| Perception (RT-DETR + tracker) | `vss-rtvi-cv-mc-tracking` | 9000 | DeepStream pipeline; extends the shared `rtvi-cv-mv3dt` base (`services/rtvi/rtvi-cv/rtvi-cv-mv3dt/compose.yaml`) |
| BEV Fusion | `vss-rtvi-cv-bev-fusion-mc-tracking` | — | Cross-camera measurement fusion into a shared BEV coordinate frame; extends the same shared base's `measurement-fusion` |
| ReID model init | `vss-reid-embed-init-mc-tracking` | — | One-shot: downloads SigLIP2 from NGC and exports the CLIP-ReID tracker ONNX into `$VSS_DATA_DIR/models/reid` |
| ReID service | `reid-embed-mc-tracking` | 8088 | Appearance-based re-association for the tracker; consumes `mdx-raw`, republishes compressed embeddings to `mdx-compressed-embeddings` |
| ReID vector store | `reid-milvus-mc-tracking` | — | Milvus standalone holding the ReID embedding gallery |
| ReID store metadata / object store | `reid-etcd-mc-tracking`, `reid-minio-mc-tracking` | — | Milvus's required etcd and MinIO backends (not published to the host) |
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

Person (class 0) and Forklift (class 1) — no other classes are tracked by this profile. ReID re-association runs on **Person only** (`operateOnClassIds: [0]` in the tracker's `ReIDService` block, matching `PoseEstimator`).

## ReID embedding and re-association

Two distinct models are involved, and they are easy to confuse:

| Model | Loaded by | Path | Purpose |
|---|---|---|---|
| CLIP-ReID (Market-1501 ViT-B-16) | DeepStream tracker | `$VSS_DATA_DIR/models/reid/reid_model.onnx` | Per-object appearance embedding extracted in the perception pipeline (1280-D, `inferDims: [3, 256, 128]`) |
| SigLIP2 (`nvidia/tao/siglip_v2:deployable_v1.0`) | `reid-embed` service | `$VSS_DATA_DIR/models/reid/siglip_v2_vdeployable_v1.0/siglip2_v1.0.onnx` | Secondary embedding computed inside the service (`ENABLE_SECONDARY_EMBEDDING=True`, `SECONDARY_EMBEDDING_MODEL=siglip2`) |

**Data flow.** The tracker runs with `--tracker-reid` (appended to `metropolis_perception_app` in `ds-start-mc-tracking.sh`) and `reidType: 2` (ReID-based re-association), extracting an embedding every `reidExtractionInterval: 8` frames and publishing frames to `mdx-raw` as before. `reid-embed` consumes `mdx-raw`, keeps the embedding gallery in Milvus, and answers the tracker's re-association queries over HTTP at `reid-embed:${REID_SERVICE_PORT}` (the tracker's `ReIDService` block). It also compresses those embeddings and retains only the samples that best represent each object (`ENABLE_COMPRESSION=True`, `COMPRESSION_INTERVAL_SEC=1`). Secondary embeddings (SigLIP2) are generated **only for those kept samples**, then published on `mdx-compressed-embeddings`.

**Networking.** All ReID containers run on the Compose `default` bridge with short aliases (`reid-embed`, `reid-milvus`, `reid-etcd`, `reid-minio`) — not host networking. Only `reid-embed` is published to the host, at `${REID_SERVICE_HOST_PORT}:${REID_SERVICE_PORT}` (both `8088`), for host-side probes. 

**Port consistency.** The tracker config ships `servicePort: 8088`, and `bp-configurator` rewrites that line from `${REID_SERVICE_PORT}` on every deploy (a `text_replace` operation in `blueprint-configurator/blueprint_config.yml`). Changing `REID_SERVICE_PORT` in `generated.env` is therefore enough — do not hand-edit `ds-mc-tracking-tracker-config.yml`.

**Startup order.** `reid-embed` waits for Milvus healthy, `vss-reid-embed-init-mc-tracking` completed successfully, and `broker-health-check`; perception (`vss-rtvi-cv-mc-tracking`) then waits for `reid-embed` **healthy**. Its healthcheck polls `/health/ready` with a **300s `start_period`**, which is what the model load needs on a cold start — perception legitimately sits idle for several minutes on the first deploy.

**Storage and indexing.** The Kafka topic / Redis stream `mdx-compressed-embeddings` is created by the topic-init and broker-health-check services, consumed by both Logstash pipelines (`nv.Frame` protobuf), and indexed under `mdx-compressed-embeddings-*` via `mdx_compressed_embeddings_template` (priority 516) with the `mdx-compressed-embeddings-ilm-policy` retention policy. The template deliberately **omits `dims`** on the `dense_vector` field: the compression ratio, and therefore the vector width, is decided inside `reid-embed`, so Elasticsearch infers it from the first indexed document. A matching Kibana index pattern ships in `kibana-dashboard/mc-tracking-kibana-objects.ndjson`.

**Images.** `reid-embed-init-mc-tracking` and `reid-embed-mc-tracking` share one image, `${VSS_REID_EMBED_IMAGE:-nvcr.io/nvstaging/vss-core/reid-embed}:${VSS_REID_EMBED_TAG:-3.3.0-26.08.1}`. Both variables are defined in `containers.env` (the Compose `image:` lines carry the same literal defaults as a safety net). Override either in `generated.env` to pin a different registry or tag.

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

1. **Sample video data**

   Sample videos come from the `vss-warehouse-app-data` NGC resource:

   ```bash
   ngc \
      registry \
      resource \
      download-version \
      nvidia/vss-warehouse/vss-warehouse-app-data:3.2.0

   # OR manually download the tar file from NGC:
   # https://catalog.ngc.nvidia.com/orgs/nvidia/teams/vss-warehouse/resources/vss-warehouse-app-data?version=3.2.0

   cd vss-warehouse-app-data_v3.2.0
   tar -xvf vss-warehouse-app-data.tar.gz
   ```

   Point `VSS_DATA_DIR` at the extracted directory (containing `videos/warehouse-4cams-20mx20m-synthetic/`). Calibration/camInfo/imagery for the default dataset are self-contained in-repo under `developer-profiles/dev-profile-mc-tracking/calibration/sample-data/warehouse-4cams-20mx20m-synthetic/` — no separate calibration download needed.

   Models download automatically (see below); this download is for sample videos only.

   Model acquisition is automatic and manifest-driven: `ds-start-mc-tracking.sh` downloads RT-DETR + BodyPose3DNet via `models-download.json` when `DS_MODEL_DOWNLOAD=auto` (the default) on first perception start. Ensure `NGC_CLI_API_KEY` is set and `$VSS_DATA_DIR/models` exists and is writable before first deploy. RT-CV builds a TensorRT engine from the downloaded models on first start (a few minutes) — the engine cache persists under `$VSS_DATA_DIR/models/` across ordinary restarts.

   **ReID models are the one exception to "no separate download init service."** `vss-reid-embed-init-mc-tracking` runs once before `reid-embed` and populates `$VSS_DATA_DIR/models/reid` by calling `services/rtvi/reid-embed/download-embedding-models.sh --secondary --clipreid`:

   - `--secondary` pulls `nvidia/tao/siglip_v2:deployable_v1.0` from NGC (needs `NGC_CLI_API_KEY`).
   - `--clipreid` fetches the CLIP-ReID source from GitHub plus the Market-1501 checkpoint **from Google Drive**, then exports `reid_model.onnx` with `convert_clipreid_to_onnx.py`. The export calls `.cuda()`, so the init container reserves `${RT_CV_DEVICE_ID:-0}`.

   Both stages skip work that is already present, so redeploys are cheap; pass `--force` to re-fetch. Artifacts are chowned to `STORAGE_UID`/`STORAGE_GID` (`1001`) so the perception container can read them after it drops privileges. The Google Drive dependency means this step needs outbound internet beyond NGC — on an air-gapped host, stage `reid_model.onnx` and `siglip_v2_vdeployable_v1.0/` into `$VSS_DATA_DIR/models/reid` yourself and the init container will no-op.

2. **Edit deployment overrides**

   Keep stable profile defaults in **`developer-profiles/dev-profile-mc-tracking/.env`**. Copy **`overrides.env`** to **`generated.env`** and edit `generated.env` for the target machine:

   ```bash
   cd deploy/docker

   cp developer-profiles/dev-profile-mc-tracking/overrides.env \
      developer-profiles/dev-profile-mc-tracking/generated.env
   ```

   - **`VSS_APPS_DIR`**: absolute path to this repository's `deploy/docker` directory
   - **`VSS_DATA_DIR`**: extracted `vss-warehouse-app-data` directory (step 1)
   - **`HOST_IP`** / **`EXTERNAL_IP`**: host address and externally reachable address
   - **`NGC_CLI_API_KEY`**: an NGC key with access to the RT-DETR warehouse, BodyPose3DNet, and SigLIP2 (`nvidia/tao/siglip_v2`) model packages
   - **`HARDWARE_PROFILE`**: see [Hardware profiles](#hardware-profiles)
   - **`STREAM_TYPE`**: `kafka` or `redis` — keep aligned with `COMPOSE_PROFILES` (next bullet)
   - **`COMPOSE_PROFILES`**: one of the `COMPOSE_PROFILES_MC_TRACKING_*` variants defined in `overrides.env` — must match `STREAM_TYPE`
   - **`REID_SERVICE_PORT`** / **`REID_SERVICE_HOST_PORT`**: both `8088` by default. `REID_SERVICE_PORT` is the in-container port and is propagated into the tracker config by `bp-configurator`; change `REID_SERVICE_HOST_PORT` alone if `8088` is already taken on the host
   - **`BP_CONFIGURATOR_ENV_FILE`**: set to the absolute path of `generated.env` itself — `bp-configurator`'s own `env_file:` defaults to `overrides.env`, not `generated.env` (see [Debugging](#debugging))

3. **Start the stack**

   ```bash
   docker compose -f compose.yml \
     --env-file containers.env \
     --env-file developer-profiles/dev-profile-mc-tracking/.env \
     --env-file developer-profiles/dev-profile-mc-tracking/generated.env \
     up --detach --pull always --force-recreate --build
   ```

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
| ReID service health | `http://<HOST_IP>:8088/health/ready` |

## Teardown

4. **Stop the stack**

   ```bash
   docker compose -f compose.yml \
     --env-file containers.env \
     --env-file developer-profiles/dev-profile-mc-tracking/.env \
     --env-file developer-profiles/dev-profile-mc-tracking/generated.env \
     down -v --remove-orphans
   ```

   `-v` wipes Postgres (`vss_vios_pg_data`, a named Docker volume). It does **not** wipe Redis — Redis's data (`$VSS_DATA_DIR/data_log/redis/data`) is a host bind mount, not a Docker volume, so it survives `down -v` intact (including `sdr-controller`'s stale provisioning state, see [Debugging](#debugging)). Clear it with step 5's `cleanup_all_datalog.sh`, or manually: `rm -rf $VSS_DATA_DIR/data_log/redis/data/*`.

   For a full reset that also drops locally-built images (Elasticsearch, init containers), use `down -v --rmi all` instead; expect the next `up` to take several minutes longer while those images rebuild.

   **Dangling-volume cleanup** (scoped to `COMPOSE_PROJECT_NAME` — `vss` by default — so dangling volumes from unrelated stopped containers/apps on the host are not touched):

   ```bash
   docker volume ls -q -f "dangling=true" -f "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME:-vss}" | xargs -r docker volume rm
   ```

5. **Data / backup cleanup**

   To reset `data_log` volumes, calibration/VST data, and blueprint-configurator backups in a way that matches how you deployed:

   ```bash
   bash scripts/cleanup_all_datalog.sh -e developer-profiles/dev-profile-mc-tracking/generated.env
   ```

   This deletes calibration output and VST/nvstreamer runtime data by default — pass `--skip-delete-calibration-data` and/or `--skip-delete-vst-data` to keep them. It does not touch `$VSS_DATA_DIR/models/` (downloaded models / built TensorRT engines, including `models/reid`) or `$VSS_DATA_DIR/videos/` / `$VSS_DATA_DIR/playback/` (sample media). Leaving `models/reid` in place is what makes redeploys skip the CLIP-ReID export; delete it only when you want a clean re-download.

   Milvus's own state lives in Compose volumes and is removed by `down -v`, so the embedding gallery starts empty on the next deploy. That is intended — the gallery is rebuilt from live traffic.

   Use `generated.env` here, not `overrides.env` — `overrides.env`'s `VSS_DATA_DIR` is still the checked-in `/path/to/...` placeholder, so pointing `-e` at it fails with `Error: VSS data dir '/path/to/vss-mc-tracking-data' not found` and silently skips the actual `data_log` cleanup (verified).

## Debugging

Perception/provisioning failures in this profile are almost always one of the issues below, roughly in the order you'll hit them on a fresh deploy:

- **Perception never starts and `reid-embed-mc-tracking` sits in `health: starting` for minutes** — expected on a cold start, not a fault. The healthcheck's `start_period` is 300s to cover the ReID/SigLIP2 model load, and `vss-rtvi-cv-mc-tracking` has a `service_healthy` dependency on it. Watch `docker logs -f reid-embed-mc-tracking` and only investigate if it goes `unhealthy` or the container restarts.
- **`vss-reid-embed-init-mc-tracking` fails on the CLIP-ReID checkpoint** — the Market-1501 checkpoint comes from Google Drive via `gdown`, which fails on hosts without general outbound internet (or when Drive rate-limits). The script prints the exact target path; stage `Market1501_clipreid_12x12sie_ViT-B-16_60.pth` (or a prebuilt `reid_model.onnx`) into `$VSS_DATA_DIR/models/reid` manually and rerun — existing files are skipped. The SigLIP2 half only needs NGC and fails separately with an NGC auth error if `NGC_CLI_API_KEY` lacks access.
- **Tracker logs a ReID engine/ONNX error, or `reid-embed` reports the SigLIP2 path missing** — `$VSS_DATA_DIR/models/reid` did not get populated, or got populated with the wrong ownership. The init container writes as root and chowns to `1001:1001`; if you pre-staged files by hand, match that (`chown -R 1001:1001 $VSS_DATA_DIR/models/reid`). Note the ReID mount is a **passthrough** (same absolute path inside and outside the container), so a `MOUNT_DIR` override must be valid on both sides.
- **`reid-embed` is up but the tracker never re-associates** — check the port contract. The tracker's `ReIDService.servicePort` is rewritten from `REID_SERVICE_PORT` by `bp-configurator` on each deploy, so a hand-edit of `ds-mc-tracking-tracker-config.yml` is silently reverted. Confirm the effective value in the config dump that `ds-start-mc-tracking.sh` prints at startup, and that `serviceAddress` is the network alias `reid-embed`.
- **`mdx-compressed-embeddings-*` index never appears in Kibana** — either the topic/stream is missing (it is created by the topic-init and broker-health-check services; a stale broker-health-check image from a prior deploy will not know about it, so redeploy with `--build` after pulling this change), or Logstash is not consuming it. The index mapping intentionally has no fixed `dims`, so a mapping-conflict error there means something other than `reid-embed` wrote to the index first.
- **`bp-configurator` exits with `HOST_IP must be set ... placeholder '<HOST_IP>'`** — its `env_file` defaults to `overrides.env`, which still has the placeholder, not `generated.env`. Set `BP_CONFIGURATOR_ENV_FILE=<absolute path to generated.env>` in `generated.env` before deploying.
- **Switching to Redis mode (`STREAM_TYPE=redis` in `generated.env`), `broker-health-check` still waits for Kafka** — its image is selected by `STREAM_TYPE` at build time (`Dockerfiles/${STREAM_TYPE}-health-check.Dockerfile`), so a stale cached image from a prior Kafka deploy keeps checking for Kafka even after `STREAM_TYPE` is fixed. Always redeploy with `--build` (see the `up` command above) when switching `STREAM_TYPE`. Also set `COMPOSE_PROFILES=${COMPOSE_PROFILES_MC_TRACKING_REDIS}` (or the `_MINIMAL`/`_PLAYBACK` variant) to match.
- **`bp-configurator`'s `file_management` step fails with "Directory not found"** for the sample video dataset — symlinks into `$VSS_DATA_DIR/videos/...` don't resolve correctly inside the container's mount namespace. Copy the sample video/playback files into the data dir directly (`cp -a`, not `ln -s`).
- **`kibana` unhealthy, logs show an Elasticsearch version mismatch** (e.g. Kibana `9.4.4` vs a stale locally-cached `elasticsearch:9.3.3`) — rebuild the Elasticsearch image: `docker compose build elasticsearch` (it's pinned to the matching version in `services/infra/Dockerfiles/elasticsearch.Dockerfile`), then recreate the container.
- **`vss-behavior-analytics-mc-tracking` / `vss-video-analytics-api-mc-tracking` restart-looping with `EACCES`** — `bp-configurator` rewrites config files but preserves their original restrictive permissions, and `data_log/vss_video_analytics_api/` gets auto-created `root:root`. Fix with `chmod -R o+rX` on the rewritten config dirs and `chmod -R 777` on `data_log/vss_video_analytics_api`.
- **`vss-rtvi-cv-mc-tracking` stuck at 0 FPS** even though `bp-configurator` logs "Successfully added sensor" for all 4 cameras and `sdr-controller` shows `200`s — this is stale provisioning state, not a code bug. Root cause: `sdr-controller` (WDM) caches "what's currently provisioned on this pod" in a Redis hash (`vss-rtvi-cv-mc-tracking`) and sensor identity in Postgres (`vss_vios_pg_data`), neither of which is cleared by a plain `docker compose down` (no `-v`) or even `down -v` (Redis is a bind mount, not a Docker volume — see step 4). After a non-destructive teardown + redeploy, those caches can point at camera UUIDs that no longer exist, so provisioning silently never converges (symptoms vary — can show as a `stream/remove`-only loop with no `stream/add`, or as the sensors appearing "added" successfully while the perception pod never actually receives them).

  **Fix: tear down cleanly, don't patch a running system.** `docker exec redis redis-cli DEL vss-rtvi-cv-mc-tracking rtvi-cv-mc-tracking-data vss-rtvi-cv-mc-tracking-pod && docker restart sdr-controller` looks like the targeted fix but is **unreliable in practice** (verified) — restarting only `sdr-controller` leaves it waiting fresh on the `vst.event` Redis stream, but `bp-configurator` already sent its one-shot sensor config *before* the restart and won't resend it just because `sdr-controller` came back, so the new `sdr-controller` process never receives it and the stack stays stuck. Instead, tear down fully (`down -v --remove-orphans`) and clear `$VSS_DATA_DIR/data_log/redis/data/*` (via `cleanup_all_datalog.sh`, step 5) *before* redeploying — this was verified to reliably fix it, the partial restart was not. Confirm with `docker logs vss-rtvi-cv-mc-tracking | grep 'Active sources'` (should read 4, not 0).
- **Shell-exported vars silently override `generated.env`** — if `PERCEPTION_TAG`, `VSS_RT_CV_MV3DT_BEV_FUSION_IMAGE/TAG`, or `NGC_CLI_API_KEY` were ever `source`d into the current shell (e.g. from an earlier `source .env`), Compose gives OS env vars precedence over `--env-file`, and a stray literal-quote-baked value (`PERCEPTION_TAG="3.3.0-26.07.2"` with the quotes taken literally) produces `invalid reference format` on `up -d`. `env | grep -E "PERCEPTION_TAG|VSS_RT_CV_MV3DT_BEV_FUSION|NGC_CLI_API_KEY"` and `unset` anything present before redeploying.

For a clean, known-good reset covering the last three issues at once: `docker compose ... down -v --rmi all` (wipes Postgres + images) + `rm -rf $VSS_DATA_DIR/data_log/redis/data/*` (wipes Redis — a bind mount, not touched by `-v`) + `docker volume ls -q -f dangling=true -f label=com.docker.compose.project=${COMPOSE_PROJECT_NAME:-vss} | xargs -r docker volume rm` (scoped to this project's volumes — an unscoped `dangling=true` filter would also delete dangling volumes from unrelated stopped containers/apps on the host), then redeploy. This forces a rebuild of any locally-built images (Elasticsearch, init containers), so expect the first `up` after a `-v --rmi all` teardown to take several minutes longer than an ordinary redeploy.
