---
name: vss-deploy-detection-tracking-3d
description: >
  Use when deploying or operating standalone RTVI-CV-3D / MV3DT multi-camera
  3D tracking for calibrated MP4/file inputs and live RTSP streams:
  missing-calibration handoff to AMC skills, the 4-camera sample dataset,
  camera config, BEV Fusion, live OSD or saved grid/BEV outputs, bundled
  brokers, basic external MQTT/Kafka brokers, verification, and teardown.
  Trigger for generic MV3DT, RTVI-CV-3D, multi-view 3D tracking, multi-cam
  tracking, or sample MV3DT dataset requests. Explicit warehouse
  blueprint/profile MV3DT requests route to vss-deploy-profile; single-camera
  2D tracking routes to the 2D tracking or DeepStream skills.
license: Apache-2.0
metadata:
  author: NVIDIA
  version: "3.3.0"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia vss rtvi-cv-3d mv3dt multi-camera tracking bev-fusion standalone"
---

# VSS Deploy Detection And Tracking 3D

Deploy the standalone RT-CV-3D MV3DT stack from `services/rtvi/rt-cv-3d/rt-cv-mv3dt`.
This is the default path for MV3DT / RTVI-CV-3D / multi-camera tracking requests.

Do not derive MV3DT services from the warehouse blueprint for this skill. Use
`vss-deploy-profile` only when the user explicitly asks for warehouse MV3DT,
the warehouse blueprint, a `bp_wh*` profile, warehouse compose files, or the
combined warehouse application stack.

Public docs: https://docs.nvidia.com/vss/latest/object-detection-tracking.html.

## Examples

Example operation prompts:

- "Deploy MV3DT on my calibrated four-camera MP4 dataset and save output."
- "Deploy MV3DT on the sample dataset."
- "Enable multi-camera tracking on the 4-cam example dataset."
- "Run RTVI-CV-3D on these RTSP streams after calibration."
- "Deploy multi-cam tracking; if there is no display, save the videos."
- "Use an external MQTT broker and external Kafka for this RT-CV-3D deployment."
- "Verify the standalone RT-CV-3D deployment and show output paths."
- "Tear down everything for standalone MV3DT."


## Output Permissions

Keep output permissions scoped to the standalone runtime paths. If output writes fail, report the directory owner/mode, container user, and relevant logs instead of loosening permissions broadly.

## What This Deploys

The standalone compose file is `services/rtvi/rt-cv-3d/rt-cv-mv3dt/docker/compose.yml`.
It deploys:

| Service | Container | Role |
|---|---|---|
| `perception` | `vss-rtvi-cv-mv3dt` | RT-DETR plus MV3DT DeepStream perception; publishes per-camera 3D measurements to Kafka topic `mdx-raw` and uses MQTT `/trck/*` tracklet exchange. |
| `bev-fusion` | `vss-rtvi-cv-bev-fusion` | Consumes `mdx-raw`, fuses same-object measurements across cameras, and publishes `mdx-bev`. |
| `mosquitto` | `vss-mosquitto-mv3dt` | Optional bundled MQTT broker, enabled by the `mosquitto` compose profile. |
| `kafka` | `kafka` | Optional bundled Kafka broker, enabled by the `kafka` compose profile. |
| `kafka-topic-init` | `kafka-topic-init` | Optional one-shot topic initializer for `mdx-raw` and `mdx-bev`, enabled by the `kafka` compose profile. |

The standalone stack does not deploy VST, VIOS, NvStreamer, Elasticsearch,
Kibana, Logstash, video-analytics-api, behavior analytics, SDR controller,
warehouse configurator, agents, LLM, or VLM services.

## Core Rules

- Default to bundled brokers and use `COMPOSE_PROFILES=mosquitto,kafka` for bundled-broker Compose operations. Before bundled launch, preflight `KAFKA_PORT` and `KAFKA_CONTROLLER_PORT`; if defaults such as `9092/9093` are occupied, select free alternatives such as `19092/19093` and persist `KAFKA_PORT`, `KAFKA_CONTROLLER_PORT`, and `KAFKA_BOOTSTRAP` in standalone `docker/.env`. Do not use full-stack `docker compose up -d` as the generic file-mode launch path; file mode must start support services, capture Kafka baselines, optionally prestart BEV, and only then start `perception` with `--no-deps`.
- For explicit external broker requests, collect, export, and validate `MQTT_HOST`, `MQTT_PORT`, and `KAFKA_BOOTSTRAP`; set `USE_EXTERNAL_BROKERS=1`; generate pub/sub config with `MQTT_BROKERS="${MQTT_HOST}:${MQTT_PORT}" ./scripts/generate-configs.sh`; use external-broker Compose mode without bundled profiles; and verify Kafka against the external `KAFKA_BOOTSTRAP`. File-mode external-broker runs still follow the same two-phase ordering. Delegate only TLS/auth variants to the standalone README custom-broker section.
- Require calibrated, time-synchronized multi-camera input. MV3DT needs at least two cameras; 30 FPS sources should be synchronized within about one frame duration.
- For recorded files, use `INPUT_MODE=file`; each `.mp4` name must match a sensor id in `calibration.json` and the generated `camInfo`. File input is a finite batch run: `vss-rtvi-cv-mv3dt` exits after end-of-stream.
- For the sample dataset / 4-cam example dataset, load `references/sample-dataset.md`. Use the standalone sample flow: NGC warehouse app-data for models/videos, repo sample `calibration.json` and `Top.png` for calibration/BEV map, generated transforms, `INPUT_MODE=file`, `NUM_CAMS=4`, bundled brokers, saved perception grid, and saved fused BEV by default.
- When the user provides MP4 paths, preserve them as deployment inputs. Use their directory as `VIDEO_DIR` when basenames already match sensor ids; otherwise create generated symlinks named `<sensor_id>.mp4` only when the mapping is explicit or unambiguous by count/order. Do not mutate source videos.
- For live RTSP, use `INPUT_MODE=stream`, launch first, then register streams with `scripts/add-streams.sh`; stream keys must match the calibration sensor ids.
- When the user provides RTSP URLs, preserve them as deployment inputs and run `scripts/add-streams.sh` after `ds-ready: YES`; do not stop at telling the user to run it manually. Ask for mapping only if bare URLs cannot be matched to calibration sensor ids by count/order.
- If calibration is missing, hand off to `vss-generate-video-calibration`. For RTSP calibration, use `vss-manage-video-io-storage` only to bring up or verify the VIOS prerequisite when VIOS is not already deployed/reachable; AMC owns calibration and `VIOS_BASE_URL` env wiring once VIOS is available.
- Do not use VST for visualization. Use the standalone OSD/save-video path and BEV visualizer scripts.
- Treat `save video`, `save output`, and headless fallback as saved perception grid plus saved fused BEV by default. Before launch, preflight host tools needed for selected output: `ffprobe` for saved artifact verification, and the BEV visualizer Python dependencies including Tkinter when BEV visualization/recording is enabled. Before promising BEV, resolve `BEV_DATASET_PATH` to a directory containing both `map.png` and `transforms.yml`; if either is missing, request the missing BEV asset or report perception-grid-only output explicitly.
- BEV video is not emitted by the perception container. It is produced by the separate host-side `scripts/bev-visualizer.sh` Kafka consumer; start it before data starts flowing: before `scripts/add-streams.sh` for RTSP and before starting `perception` for file input.

## Workflow Selection

Load the minimum references needed for the current request:

| User intent | References |
|---|---|
| First-time setup, prerequisites, model/assets, `.env` | `references/deploy-rtvi-cv-3d-stack.md` |
| Sample dataset, 4-cam example dataset, warehouse 4-camera synthetic dataset | `references/sample-dataset.md`, then `references/configure-cameras.md`, `references/deploy-rtvi-cv-3d-stack.md`, and `references/verify-and-view.md` |
| Existing or newly generated calibration; local MP4 or RTSP input config | `references/configure-cameras.md` |
| Missing calibration | `references/calibration-workflow.md`, then `references/configure-cameras.md` |
| Launch or redeploy the stack | `references/deploy-rtvi-cv-3d-stack.md` |
| Add/list/remove live RTSP streams | `references/configure-cameras.md` |
| Verify containers, logs, Kafka topics, or output artifacts | `references/verify-and-view.md` |
| Live OSD, saved perception video, live BEV, or saved BEV video | `references/verify-and-view.md` |
| Completed file-input post-run support-service cleanup; stop, tear down everything, or clean generated state | `references/teardown.md` |
| Diagnose failures | `references/troubleshooting.md` |

## Run Stages

Follow these stages for deployment work:

1. Resolve `RTCV3D_APP` to `services/rtvi/rt-cv-3d/rt-cv-mv3dt`.
2. Identify the input mode: `file` for local MP4s or `stream` for RTSP.
3. If the user asked for the sample dataset or 4-cam example dataset, load `references/sample-dataset.md` first. Resolve/download app-data, set `MODELS_DIR`, `VIDEO_DIR`, `CALIBRATION_JSON`, `BEV_DATASET_PATH`, `NUM_CAMS=4`, `INPUT_MODE=file`, `SAVE_VIDEO=1`, and saved fused BEV defaults, then continue with camera validation and normal file-mode deployment.
4. Validate or obtain `calibration.json`. If missing, hand off to the AMC skill, fetch the AMC MV3DT export ZIP, export `calibration.json`, and stage BEV assets before continuing. For saved output or BEV viewing, resolve `BEV_DATASET_PATH` to a directory containing both `map.png` and `transforms.yml` before launch.
5. Generate `generated/camInfo/` and `generated/pub_sub_info_config.yml` from `calibration.json` with the standalone `scripts/generate-configs.sh`; do not mount warehouse MV3DT calibration directories.
6. Set required values in `docker/.env`: `MODELS_DIR`, `NUM_CAMS`, `INPUT_MODE`, `VIDEO_DIR` for file input, and optional image/GPU/port values. For supplied MP4 paths, point `VIDEO_DIR` at the matching source directory or at a generated symlink directory with one `<sensor_id>.mp4` per camera.
7. Detect display availability before staging configs:
   - If a working display is detected and the user did not ask to save, stage with `OSD=1`.
   - If no display is detected, use saved output as the default fallback: set `SAVE_VIDEO=1` and save fused BEV after `BEV_DATASET_PATH` resolves with both required files.
   - If the user asked to save output, set `SAVE_VIDEO=1` even when a display exists and also save fused BEV by default after `BEV_DATASET_PATH` resolves with both required files.
   - If the user asked for both live view and saved output, use `OSD=1 SAVE_VIDEO=1` and start saved fused BEV in parallel.
8. Stage DeepStream configs with `scripts/stage-configs.sh`.
9. For every `INPUT_MODE=file` run, start the selected brokers and `bev-fusion`, wait for broker/topic-init/BEV Fusion readiness, then capture Kafka baselines before starting `perception`. Do this even when saved output or BEV visualization is not requested.
10. If saved BEV is selected/defaulted, or if file input needs any live/saved BEV visualization, use the two-phase launch in `deploy-rtvi-cv-3d-stack.md`: after support readiness and file baselines, start the BEV visualizer/recorder, wait for its Kafka consumer group assignment, then start `perception` with `--no-deps`. Saved output uses `BEV_SAVE_VIDEO=1 BEV_SOURCE=fused` by default. For stream mode with no BEV prestart requirement, full-stack Compose launch is acceptable: bundled uses `COMPOSE_PROFILES=mosquitto,kafka docker compose up -d`; external uses `docker compose up -d`. Never use full-stack `docker compose up -d` for file input.
11. For RTSP input, wait for `ds-ready: YES`, then register the provided streams with `scripts/add-streams.sh`. Use explicit `<sensor_id>=<rtsp_url>` pairs when provided; otherwise map bare URLs to calibration sensor ids only when the counts and ordering are clear.
12. For RTSP, verify `ds-ready: YES`, exact stream registration, non-zero FPS, `mdx-raw`/`mdx-bev` offset growth, and requested visualization artifacts. For file input, do not require `ds-ready: YES`; treat `vss-rtvi-cv-mv3dt` `Exited (0)` with `App run successful` as EOS success, then require `mdx-raw` and `mdx-bev` offsets to be greater than pre-run baselines.
13. For completed file-input runs, after outputs are verified, stop only the remaining standalone support services unless the user asked to keep them running for inspection or reuse. Preserve generated configs, calibration, videos, and outputs.

## Success Criteria

- `generated/camInfo/` contains one `.yml` per filtered camera sensor and `generated/configs/` exists.
- Runtime images are reported from `docker compose config --images`; the skill does not infer image tags from its own version.
- `docker compose` uses `services/rtvi/rt-cv-3d/rt-cv-mv3dt/docker/compose.yml`.
- `vss-rtvi-cv-bev-fusion` becomes `healthy`.
- For RTSP: `ds-ready: YES`, registered stream count equals `NUM_CAMS`, registered IDs exactly match generated camInfo IDs with no duplicates/extras, every expected source has recent non-zero FPS, and both `mdx-raw` and `mdx-bev` offsets grow while streams are active.
- For file input: `vss-rtvi-cv-mv3dt` may end as `Exited (0)` after EOS and is successful only when logs include `App run successful` and both `mdx-raw` and `mdx-bev` offsets exceed pre-run baselines.
- If live OSD was selected, display access was checked before staging with `OSD=1` without broad `xhost +`.
- If saved output was selected/defaulted, report current-run `video-output/grid-view.mkv` and saved BEV artifact paths with non-empty size, run-start timestamp checks, `ffprobe` success, and current BEV log evidence including `Video saved` with positive frame count. If BEV was skipped because assets were missing, report that explicitly.
- If live BEV visualization was selected, report the tracked visualizer PID/log and Kafka consumer group assignment evidence.

## Related Skills

- `vss-generate-video-calibration` owns AMC deployment and calibration from local MP4s or RTSP streams.
- `vss-manage-video-io-storage` is used only to bring up or verify VIOS when RTSP calibration needs VIOS and it is not already deployed.
- `vss-deploy-profile` owns full warehouse blueprint deployments, including explicit warehouse MV3DT requests.
