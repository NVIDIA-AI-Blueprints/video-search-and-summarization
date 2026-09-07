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
  2D tracking routes to the 2D tracking or DeepStream skills. Not for full
  warehouse blueprint deployment, single-camera 2D tracking, camera calibration
  itself, or VSS summarization, Q&A, and RAG workflows.
license: Apache-2.0
metadata:
  author: NVIDIA
  version: "3.3.0"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia vss rtvi-cv-3d mv3dt multi-camera tracking bev-fusion standalone"
---

# VSS Deploy Detection And Tracking 3D

## When to Use This Skill

Deploy the standalone RT-CV-3D MV3DT stack from `services/rtvi/rt-cv-3d/rt-cv-mv3dt`.
This is the default path for MV3DT / RTVI-CV-3D / multi-camera tracking requests.

Do not derive MV3DT services from the warehouse blueprint for this skill. Use
`vss-deploy-profile` only when the user explicitly asks for warehouse MV3DT,
the warehouse blueprint, a `bp_wh*` profile, warehouse compose files, or the
combined warehouse application stack. When routing an explicit warehouse MV3DT
request, also state the boundary: generic MV3DT uses standalone RT-CV-3D, while
warehouse MV3DT uses warehouse/profile deployment. For single-camera 2D
detection or tracking, use the 2D tracking or DeepStream skills instead.

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

- Default to bundled brokers and use `COMPOSE_PROFILES=mosquitto,kafka` for bundled-broker Compose operations. Before `generate-configs.sh`, `stage-configs.sh`, or bundled launch, run the bundled resource preflight: reuse existing standalone containers from this app without rewriting ports, reject foreign fixed-name container collisions, and for a fresh start select free Kafka, MQTT, and DeepStream REST ports in standalone `docker/.env`. Do not use full-stack `docker compose up -d` as the generic file-mode launch path; file mode must start support services, capture Kafka baselines, optionally prestart BEV, and only then start `perception` with `--no-deps`.
- For explicit external broker requests, collect, export, and validate `MQTT_HOST`, `MQTT_PORT`, and `KAFKA_BOOTSTRAP`; set `USE_EXTERNAL_BROKERS=1`; generate pub/sub config with `MQTT_BROKERS="${MQTT_HOST}:${MQTT_PORT}" ./scripts/generate-configs.sh`; verify `mdx-raw` and `mdx-bev` already exist on external Kafka with bounded `kafka-topics --describe`; use external-broker Compose mode without bundled profiles; and verify Kafka offsets against the external `KAFKA_BOOTSTRAP`. File-mode external-broker runs still follow the same two-phase ordering. Delegate only TLS/auth variants to the standalone README custom-broker section.
- Require calibrated, time-synchronized multi-camera input. MV3DT needs at least two cameras; 30 FPS sources should be synchronized within about one frame duration.
- For recorded files, use `INPUT_MODE=file`; each `.mp4` name must match a sensor id in `calibration.json` and the generated `camInfo`. File input is a finite batch run: tell the user up front that `vss-rtvi-cv-mv3dt` exits after end-of-stream and remaining support containers are stopped after successful verification unless the user asks to keep them.
- For the sample dataset / 4-cam example dataset, load `references/sample-dataset.md`. Use the standalone sample flow: NGC warehouse app-data for models/videos, repo sample `calibration.json` and `Top.png` for calibration/BEV map, generated transforms, `INPUT_MODE=file`, `NUM_CAMS=4`, bundled brokers, then the normal display-first visualization decision: live OSD plus live fused BEV when a working display is found and the user did not ask to save; saved grid plus saved fused BEV when headless or explicitly requested.
- When the user provides MP4 paths, preserve them as deployment inputs. Use their directory as `VIDEO_DIR` when basenames already match sensor ids; otherwise create generated symlinks named `<sensor_id>.mp4` only when the mapping is explicit or unambiguous by count/order. Do not mutate source videos.
- For live RTSP, use `INPUT_MODE=stream`. Dynamic REST registration is the first path; stream keys must match the calibration sensor ids. Use the direct REST registration block in `references/configure-cameras.md` so readiness JSON is parsed independent of whitespace. Do not treat `STREAM_ADD_SUCCESS` or `stream-count` alone as success. A live RTSP deployment succeeds only when the expected sources become active, every camera has recent non-zero FPS, and `mdx-raw`/`mdx-bev` offsets grow.
- When the user provides RTSP URLs, preserve them as deployment inputs and register them after the stream-mode compose service is running with the direct REST block in `references/configure-cameras.md`; the block waits on `/api/v1/ready` for `ds-ready` to become `YES`, so the `ds-ready: YES` log line is optional diagnostic evidence. Do not stop at telling the user to run registration manually. Ask for mapping only if bare URLs cannot be matched to calibration sensor ids by count/order. If dynamic registration accepts streams but active sources, FPS, or Kafka growth remain zero after bounded verification, treat dynamic add as failed and use the generic static RTSP `[source-list]` fallback in `references/configure-cameras.md` with the same user-provided `sensor_id=rtsp://...` mappings. Do not substitute sample calibration or sample camera mappings unless the user explicitly requested the sample dataset.
- If calibration is missing, hand off to `vss-generate-video-calibration` and run its AMC platform preflight before VIOS, capture, upload, or calibration work. If the preflight fails, stop and ask the user to provide existing/generated calibration artifacts or choose a supported `x86_64` dGPU/NVENC calibration host. For RTSP calibration, use `vss-manage-video-io-storage` only to bring up or verify the VIOS prerequisite when VIOS is not already deployed/reachable; AMC owns calibration and `VIOS_BASE_URL` env wiring once VIOS is available.
- Do not use VST for visualization. Use the standalone OSD/save-video path and BEV visualizer scripts.
- Always run the real display probe in `references/configure-cameras.md` before choosing `OSD=0` as the headless fallback; do not infer headless mode only from GPU presence, `xdpyinfo` installation, or a stale/missing `DISPLAY`. Treat display mode as two live windows by default: the DeepStream camera-grid OSD and the separate fused BEV visualizer. Treat `save video`, `save output`, and confirmed headless fallback as saved perception grid plus saved fused BEV by default. Before launch, preflight host tools needed for selected output: `ffprobe` for saved artifact verification, and the BEV visualizer Python/OpenCV/Kafka dependencies when BEV visualization/recording is enabled. Before promising BEV, resolve `BEV_DATASET_PATH` to a directory containing both `map.png` and `transforms.yml`; if either is missing, request the missing BEV asset or report perception-grid-only output explicitly.
- BEV video is not emitted by the perception container. It is produced by the separate host-side `scripts/bev-visualizer.sh` Kafka consumer. For finite file input, keep the BEV process under the same long-lived shell/session that starts perception, waits for EOS, verifies offsets/artifacts, finalizes BEV, and performs cleanup; do not start BEV in a separate short tool call and assume `nohup ... &` will survive runner process-group cleanup. Wait for Kafka assignment and verify the PID is still alive immediately before file-mode perception or RTSP stream registration. For finite file-input live display runs, start live fused BEV before perception and, after EOS, tell the user to press `q` in the BEV window or stop only the tracked current-run BEV PID through the safe teardown flow.

## Workflow

Use the workflow selection table and run stages below. Load only the references
needed for the user's selected input, broker, visualization, calibration, and
verification path.

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
3. If the user asked for the sample dataset or 4-cam example dataset, load `references/sample-dataset.md` first. Resolve/download app-data, set `MODELS_DIR`, `VIDEO_DIR=<APP_DATA_DIR>/videos/warehouse-4cams-20mx20m-synthetic`, `CALIBRATION_JSON`, `BEV_DATASET_PATH`, `NUM_CAMS=4`, and `INPUT_MODE=file`, then continue with camera validation and the normal display/save decision before setting `OSD`, `SAVE_VIDEO`, or `BEV_SAVE_VIDEO`.
4. Validate or obtain `calibration.json`. If missing, hand off to `vss-generate-video-calibration` by name and do not duplicate the AMC workflow inline. Explicitly include the AMC platform preflight failure path: stop and request existing/generated calibration artifacts or a supported `x86_64` dGPU/NVENC calibration host. After AMC completes, fetch the AMC MV3DT export ZIP, export `calibration.json`, validate JSON by filtering sensors where `type == "camera"` and requiring at least two non-empty safe unique camera IDs, then stage BEV assets before continuing. For saved output or BEV viewing, resolve `BEV_DATASET_PATH` to a directory containing both `map.png` and `transforms.yml` before launch.
5. Set required values in `docker/.env`: `MODELS_DIR`, `NUM_CAMS`, `INPUT_MODE`, `VIDEO_DIR` for file input, and optional image/GPU values. For supplied MP4 paths, point `VIDEO_DIR` at the matching source directory or at a generated symlink directory with one `<sensor_id>.mp4` per camera.
6. Initialize broker mode before config generation or staging. For bundled mode, run the bundled resource preflight in `references/deploy-rtvi-cv-3d-stack.md` now so selected `MQTT_PORT`, `KAFKA_PORT`, and `KAFKA_BOOTSTRAP` are already in `docker/.env`. For external mode, validate broker endpoints and required topics before launch.
7. Generate `generated/camInfo/` and `generated/pub_sub_info_config.yml` from `calibration.json` with the standalone `scripts/generate-configs.sh`, using the selected MQTT endpoint; do not mount warehouse MV3DT calibration directories.
8. Run the concrete display probe from `references/configure-cameras.md` before staging configs; it must test the current `DISPLAY` and discovered X socket candidates such as `:0`/`:1`, export a working `DISPLAY` when found, and print `RTCV3D_DISPLAY_AVAILABLE`. Then choose visualization:
   - If a working display is detected and the user did not ask to save, stage with `OSD=1 SAVE_VIDEO=0`, set `BEV_SAVE_VIDEO=0 BEV_SOURCE=fused`, and use live fused BEV visualization when BEV assets are present.
   - If no display is detected, use saved output as the default fallback: set `SAVE_VIDEO=1` and save fused BEV after `BEV_DATASET_PATH` resolves with both required files.
   - If the user asked to save output, set `SAVE_VIDEO=1` even when a display exists and also save fused BEV by default after `BEV_DATASET_PATH` resolves with both required files.
   - If the user asked for both live view and saved output, use `OSD=1 SAVE_VIDEO=1` and start saved fused BEV in parallel.
9. Stage DeepStream configs with `scripts/stage-configs.sh`, then assert `generated/configs/ds-main-config-mv3dt.txt` contains a Kafka `msg-broker-conn-str` matching the selected `KAFKA_BOOTSTRAP` and `RAW_TOPIC`. For `INPUT_MODE=file`, also assert the staged config disables live latency dropping: `[source-list] low-latency-mode=0`, `[source-attr-all] drop-on-latency=0`, and `[source-attr-all] latency=100000`.
10. Preflight output/tooling and model-cache writeability. Cold TensorRT engine builds can take 5-10 minutes and must be able to persist engines under the mounted model directories. State this warning and the scoped ACL remediation pattern in the user-visible status/report. If a model cache is not writable by the container UID/GID, stop and request approval before applying an ACL to only the affected model directory, for example `sudo setfacl -m u:<uid>:rwx -m d:u:<uid>:rwx <model-dir>`; do not use broad `chmod 777` or broad recursive `chown`.
11. For every `INPUT_MODE=file` run, start the selected brokers and `bev-fusion`, wait for broker/topic-init/BEV Fusion readiness, then capture Kafka baselines before starting `perception`. Do this even when saved output or BEV visualization is not requested.
12. If saved BEV is selected/defaulted, or if file input needs any live/saved BEV visualization, use the two-phase launch in `references/deploy-rtvi-cv-3d-stack.md`: after support readiness and file baselines, start the BEV visualizer/recorder in the same long-lived shell/session that will start `perception`, wait for EOS, verify outputs, and finalize BEV. Wait for its Kafka consumer group assignment, verify the recorder PID is still alive, then start `perception` with `--no-deps`. Saved output uses `BEV_SAVE_VIDEO=1 BEV_SOURCE=fused` by default; display-only output uses `BEV_SAVE_VIDEO=0 BEV_SOURCE=fused` so the BEV window is live. For stream mode with no BEV prestart requirement, full-stack Compose launch is acceptable: bundled uses `COMPOSE_PROFILES=mosquitto,kafka docker compose up -d`; external uses `docker compose up -d`. Never use full-stack `docker compose up -d` for file input.
13. For RTSP input, after stream-mode compose is running, register the provided streams with the direct REST registration block in `references/configure-cameras.md`, which waits on `/api/v1/ready` for `ds-ready=YES` using JSON parsing. Use explicit `<sensor_id>=<rtsp_url>` pairs when provided; otherwise map bare URLs to calibration sensor ids only when the counts and ordering are clear. Preserve the final mapping so it can also be used for the static RTSP source-list fallback if dynamic REST add does not produce active sources.
14. For RTSP, verify REST readiness, exact stream registration, non-zero FPS, `mdx-raw`/`mdx-bev` offset growth, and requested visualization artifacts. If registration succeeds but active sources/FPS/Kafka remain zero, restage the same RTSP mapping as a static `[source-list]`, restart only perception as appropriate for stream mode, and rerun the same verification. For file input, do not require `ds-ready: YES`; treat `vss-rtvi-cv-mv3dt` `Exited (0)` with `App run successful` as EOS success, then require `mdx-raw` and `mdx-bev` offsets to be greater than pre-run baselines.
15. For completed file-input runs, after outputs are verified, stop only the remaining standalone support services unless the user asked to keep them running for inspection or reuse. Preserve generated configs, calibration, videos, and outputs.

## Success Criteria

- `generated/camInfo/` contains one `.yml` per filtered camera sensor and `generated/configs/` exists.
- Runtime images are reported from `docker compose config --images`; the skill does not infer image tags from its own version.
- `docker compose` uses `services/rtvi/rt-cv-3d/rt-cv-mv3dt/docker/compose.yml`.
- `vss-rtvi-cv-bev-fusion` becomes `healthy`.
- For RTSP: REST `/api/v1/ready` reports `ds-ready=YES`, registered stream count equals `NUM_CAMS`, registered IDs exactly match generated camInfo IDs with no duplicates/extras, every expected source has recent non-zero FPS, and both `mdx-raw` and `mdx-bev` offsets grow while streams are active.
- For file input: `vss-rtvi-cv-mv3dt` may end as `Exited (0)` after EOS and is successful only when logs include `App run successful` and both `mdx-raw` and `mdx-bev` offsets exceed pre-run baselines.
- If live OSD was selected, display access was checked before staging with `OSD=1` without broad `xhost +`, and display-mode visualization includes both the DeepStream camera-grid OSD window and the separate live fused BEV window when BEV assets are present.
- If saved output was selected/defaulted, report current-run `video-output/grid-view.mkv` and saved BEV artifact paths with non-empty size, run-start timestamp checks, `ffprobe` success, and current BEV log evidence including `Video saved` with positive frame count. If BEV was skipped because assets were missing, report that explicitly.
- If live BEV visualization was selected, report the tracked visualizer PID/log, Kafka consumer group assignment evidence, and for finite file input either that the user closed the BEV window with `q` or that the tracked current-run PID was safely stopped after EOS.

## Related Skills

- `vss-generate-video-calibration` owns AMC deployment and calibration from local MP4s or RTSP streams.
- `vss-manage-video-io-storage` is used only to bring up or verify VIOS when RTSP calibration needs VIOS and it is not already deployed.
- `vss-deploy-profile` owns full warehouse blueprint deployments, including explicit warehouse MV3DT requests.
