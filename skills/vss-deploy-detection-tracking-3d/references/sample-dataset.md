# Sample 4-Camera Dataset

Load this reference when the user asks to deploy MV3DT / RTVI-CV-3D on the sample dataset, the 4-cam example dataset, or the warehouse 4-camera synthetic dataset.

This is still the standalone RT-CV-3D path. Do not switch to warehouse compose files or `vss-deploy-profile` unless the user explicitly asks for the full warehouse blueprint.

## Contents

- [What The Sample Uses](#what-the-sample-uses)
- [Resolve App Data](#resolve-app-data)
- [Stage Sample Calibration And BEV Assets](#stage-sample-calibration-and-bev-assets)
- [Env Values For The Sample](#env-values-for-the-sample)
- [Display And Visualization](#display-and-visualization)
- [Verify Sample Run](#verify-sample-run)

## What The Sample Uses

| Input | Source |
|---|---|
| Models | Extracted `vss-warehouse-app-data/models` from the NGC warehouse app-data resource |
| Videos | Extracted `vss-warehouse-app-data/videos/warehouse-4cams-20mx20m-synthetic/` |
| Calibration | Repo sample `deploy/docker/industry-profiles/warehouse-operations/warehouse-mv3dt-app/calibration/sample-data/warehouse-4cams-20mx20m-synthetic/calibration.json` |
| BEV map | Repo sample `.../warehouse-4cams-20mx20m-synthetic/images/Top.png` |
| BEV transforms | Generate with `scripts/generate-transforms.sh` into `generated/bev-dataset/transforms.yml` |

For this checked-in sample dataset only, the expected camera IDs are `Camera`, `Camera_01`, `Camera_02`, and `Camera_03`; the sample video directory should contain matching `.mp4` names.

## Resolve App Data

Use an existing extracted app-data directory if the user already has one. Otherwise download the NGC warehouse app-data resource named by the user, environment, release notes, or public VSS docs. The expected resource shape is `nvidia/vss-warehouse/vss-warehouse-app-data:<version>`; use the resource for the tested VSS release and do not infer the version from this skill.

Do not print NGC keys. Prefer existing `~/.ngc/config`; only ask for an NGC API key when no usable config or current-session `NGC_CLI_API_KEY` exists.

```bash
cd "${RTCV3D_APP:?set RTCV3D_APP}"
REPO_ROOT="$(git rev-parse --show-toplevel)"
APP_DATA_ROOT="${APP_DATA_ROOT:-${REPO_ROOT}/data/ngc}"
mkdir -p "${APP_DATA_ROOT}"

find_app_data() {
  root="$1"
  find "${root}" -type d -path '*/vss-warehouse-app-data/models/mtmc' -print 2>/dev/null \
    | sed 's#/models/mtmc$##' \
    | sort -u
}

if [ -n "${WAREHOUSE_APP_DATA_DIR:-}" ]; then
  APP_DATA_DIR="$(readlink -f "${WAREHOUSE_APP_DATA_DIR}")"
else
  matches="$(find_app_data "${APP_DATA_ROOT}" || true)"
  count="$(printf '%s\n' "${matches}" | sed '/^$/d' | wc -l | tr -d ' ')"
  if [ "${count}" = 1 ]; then
    APP_DATA_DIR="$(printf '%s\n' "${matches}" | sed '/^$/d')"
  elif [ "${count}" -gt 1 ]; then
    printf '%s\n' "${matches}" >&2
    echo "ERROR: multiple vss-warehouse-app-data directories found; set WAREHOUSE_APP_DATA_DIR explicitly." >&2
    exit 1
  fi
fi

if [ -z "${APP_DATA_DIR:-}" ]; then
  WAREHOUSE_APP_DATA_NGC="${WAREHOUSE_APP_DATA_NGC:?set the release-compatible NGC resource, for example nvidia/vss-warehouse/vss-warehouse-app-data:<version>}"
  command -v ngc >/dev/null || { echo "ERROR: ngc CLI is required to download sample app-data. Install/configure NGC CLI or set WAREHOUSE_APP_DATA_DIR to an existing extract." >&2; exit 1; }
  if ! ngc config current >/dev/null 2>&1; then
    if [ -z "${NGC_CLI_API_KEY:-}" ]; then
      echo "ERROR: NGC CLI is not configured. Ask the user for an NGC API key via masked input, then export NGC_CLI_API_KEY for this session. Persist ~/.ngc/config only after explicit user approval." >&2
      exit 1
    fi
    export NGC_CLI_API_KEY
    echo "Using current-session NGC_CLI_API_KEY for this download; not writing ~/.ngc/config."
    if [ "${PERSIST_NGC_CONFIG:-0}" = 1 ]; then
      mkdir -p "${HOME}/.ngc"
      chmod 700 "${HOME}/.ngc"
      {
        printf '[CURRENT]\n'
        printf 'apikey = %s\n' "${NGC_CLI_API_KEY}"
        printf 'format_type = ascii\n'
        printf 'org = %s\n' "${NGC_CLI_ORG:?set NGC_CLI_ORG when explicitly persisting ~/.ngc/config}"
        printf 'team = %s\n' "${NGC_CLI_TEAM:-}"
      } > "${HOME}/.ngc/config"
      chmod 600 "${HOME}/.ngc/config"
      ngc config current >/dev/null
    fi
  fi
  (cd "${APP_DATA_ROOT}" && ngc registry resource download-version "${WAREHOUSE_APP_DATA_NGC}")
  downloaded="$(find "${APP_DATA_ROOT}" -maxdepth 1 -type d -name 'vss-warehouse-app-data_v*' -printf '%T@ %p\n' | sort -nr | awk 'NR==1 {print $2}')"
  test -n "${downloaded}" || { echo "ERROR: could not find downloaded vss-warehouse-app-data_v* directory under ${APP_DATA_ROOT}" >&2; exit 1; }
  tarball="$(find "${downloaded}" -maxdepth 1 -type f -name '*.tar.gz' | head -1)"
  if [ -n "${tarball}" ] && [ ! -d "${downloaded}/vss-warehouse-app-data" ]; then
    (cd "${downloaded}" && tar -xvf "${tarball}")
  fi
  APP_DATA_DIR="${downloaded}/vss-warehouse-app-data"
fi

test -d "${APP_DATA_DIR}/models/mtmc" || { echo "ERROR: missing sample models under ${APP_DATA_DIR}/models/mtmc" >&2; exit 1; }
test -d "${APP_DATA_DIR}/models/mv3dt/BodyPose3DNet" || { echo "ERROR: missing BodyPose3DNet under ${APP_DATA_DIR}/models/mv3dt" >&2; exit 1; }
test -f "${APP_DATA_DIR}/models/mtmc/rtdetr_warehouse_v1.0.2.fp16.onnx" || { echo "ERROR: missing release-compatible RT-DETR model: ${APP_DATA_DIR}/models/mtmc/rtdetr_warehouse_v1.0.2.fp16.onnx" >&2; exit 1; }
test -f "${APP_DATA_DIR}/models/mv3dt/BodyPose3DNet/bodypose3dnet_accuracy.onnx" || { echo "ERROR: missing BodyPose3DNet ONNX model: ${APP_DATA_DIR}/models/mv3dt/BodyPose3DNet/bodypose3dnet_accuracy.onnx" >&2; exit 1; }
test -d "${APP_DATA_DIR}/videos/warehouse-4cams-20mx20m-synthetic" || { echo "ERROR: missing sample videos under ${APP_DATA_DIR}/videos/warehouse-4cams-20mx20m-synthetic" >&2; exit 1; }
echo "Validated sample model files and video directory under ${APP_DATA_DIR}"

export MODELS_DIR="${APP_DATA_DIR}/models"
export VIDEO_DIR="${APP_DATA_DIR}/videos/warehouse-4cams-20mx20m-synthetic"
echo "Sample VIDEO_DIR=${VIDEO_DIR}"
```

For registry-image access, also log in to `nvcr.io` if needed before Compose pulls the RT-CV-3D images:

```bash
if [ -n "${NGC_CLI_API_KEY:-}" ]; then
  printf '%s' "${NGC_CLI_API_KEY}" | docker login nvcr.io --username '$oauthtoken' --password-stdin
fi
```

## Stage Sample Calibration And BEV Assets

The repo sample calibration path is used only as an input artifact; the runtime camInfo files must still be generated by standalone `scripts/generate-configs.sh`.

```bash
cd "${RTCV3D_APP:?set RTCV3D_APP}"
REPO_ROOT="$(git rev-parse --show-toplevel)"
SAMPLE_DATA="${REPO_ROOT}/deploy/docker/industry-profiles/warehouse-operations/warehouse-mv3dt-app/calibration/sample-data/warehouse-4cams-20mx20m-synthetic"
CALIBRATION_JSON="${SAMPLE_DATA}/calibration.json"
MAP_PNG="${SAMPLE_DATA}/images/Top.png"

test -f "${CALIBRATION_JSON}" || { echo "ERROR: sample calibration missing: ${CALIBRATION_JSON}" >&2; exit 1; }
test -f "${MAP_PNG}" || { echo "ERROR: sample BEV map missing: ${MAP_PNG}" >&2; exit 1; }

BEV_DATASET_PATH="${RTCV3D_APP}/generated/bev-dataset"
mkdir -p "${BEV_DATASET_PATH}"
ln -sfn "${MAP_PNG}" "${BEV_DATASET_PATH}/map.png"
./scripts/generate-transforms.sh "${CALIBRATION_JSON}" "${BEV_DATASET_PATH}/map.png" -o "${BEV_DATASET_PATH}/transforms.yml" --force

export CALIBRATION_JSON MAP_PNG BEV_DATASET_PATH NUM_CAMS=4 INPUT_MODE=file BEV_SOURCE=fused
```

Then load `configure-cameras.md` and continue from `Validate Calibration`. The file-input checks should pass because the sample videos match the sample camera IDs exactly.

## Env Values For The Sample

Set these in standalone `docker/.env` before staging and launch. Preserve image tag values already present in the checked-out package.

```text
MODELS_DIR=<APP_DATA_DIR>/models
NUM_CAMS=4
INPUT_MODE=file
VIDEO_DIR=<APP_DATA_DIR>/videos/warehouse-4cams-20mx20m-synthetic
# Set SAVE_VIDEO after the display/save decision.
USE_EXTERNAL_BROKERS=0
RAW_TOPIC=mdx-raw
FUSED_TOPIC=mdx-bev
```

Use bundled brokers unless the user explicitly asks for external brokers. For the sample, run the bundled resource preflight from `references/deploy-rtvi-cv-3d-stack.md` before `generate-configs.sh` or `stage-configs.sh`; otherwise a fallback Kafka port such as `19092` can be selected too late and the staged config may still point at `9092`.

After the bundled preflight has exported/persisted the selected broker values, continue through `references/configure-cameras.md`: generate configs with `MQTT_BROKERS="${MQTT_HOST}:${MQTT_PORT}"`, run the display probe, stage with the selected `OSD`/`SAVE_VIDEO` values, and assert the staged Kafka `msg-broker-conn-str` matches `KAFKA_BOOTSTRAP`.

## Display And Visualization

For a generic sample request such as "deploy MV3DT on the sample dataset", do not force saved output before probing display availability. Use the display probe in `references/configure-cameras.md` first:

- If a working display is found and the user did not ask to save, stage `INPUT_MODE=file OSD=1 SAVE_VIDEO=0`, set `BEV_SAVE_VIDEO=0 BEV_SOURCE=fused`, and start live fused BEV before perception so both the `DeepStreamTest5App` camera grid and `Bird-Eye View of Multi-View 3D Tracking` BEV windows can render. After file EOS, finalize live BEV by asking the user to press `q` in the BEV window, or safely stop only the tracked current-run BEV PID through the teardown identity checks for unattended closeout.
- If no working display is found, state the probe result and use the saved fallback: stage `INPUT_MODE=file OSD=0 SAVE_VIDEO=1`, set `BEV_SAVE_VIDEO=1 BEV_SOURCE=fused`, and verify saved grid plus fused BEV artifacts.
- If the user explicitly asked to save, stage with `SAVE_VIDEO=1` even when display is available; use `OSD=1 SAVE_VIDEO=1` only when the user asked for both live and saved output.

Before any sample run with saved output or BEV, run `references/deploy-rtvi-cv-3d-stack.md` `Selected Output Tool Preflight`; that is the explicit `ffprobe` check plus BEV visualizer Python import check for OpenCV, Kafka, NumPy, and YAML. For any sample file-input run that uses live or saved BEV, use the two-phase BEV launch from `references/deploy-rtvi-cv-3d-stack.md`: start bundled brokers and `bev-fusion`, capture Kafka baselines, start the fused BEV visualizer/recorder in the same long-lived shell/session, wait for its Kafka consumer group assignment, verify the PID is still alive, then start `perception` and keep that session alive through EOS/finalization.

## Verify Sample Run

Use the file-input success criteria in `verify-and-view.md`:

- `vss-rtvi-cv-mv3dt` exits with code `0` after EOS and logs `App run successful`.
- `mdx-raw` and `mdx-bev` offsets exceed pre-run baselines.
- For saved output, `video-output/grid-view.mkv` is from the current run, non-empty, and `ffprobe` can parse it.
- For saved BEV, the current-run fused BEV video is from `bev-output/`, non-empty, `ffprobe` can parse it, and the current BEV recorder log contains `Video saved` with a positive frame count.
- For live display mode, report the detected `DISPLAY`, OSD/live BEV mode, BEV consumer group assignment evidence, and that the `DeepStreamTest5App` and BEV windows should be closed with `q`; after file EOS, verify the BEV window was closed or safely stop the tracked current-run BEV PID.
