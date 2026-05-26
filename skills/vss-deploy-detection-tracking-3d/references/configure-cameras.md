# Configure cameras (sync NUM_STREAMS to calibration)

Parent: [`../SKILL.md`](../SKILL.md). Run **after** calibration is on disk (either ship-with-repo for `sample`, or landed by [`calibration-workflow.md`](calibration-workflow.md), or user-supplied) and **before** [`deploy-rtvi-cv-3d-stack.md`](deploy-rtvi-cv-3d-stack.md).

The shipped warehouse `.env` defaults to `NUM_STREAMS=4` and a 4-camera sample. If you're using the sample as-is, this reference is a no-op — skim and continue. It's load-bearing only when the user's actual camera count differs from 4, or when redeploying after AMC trimmed cameras down.

## Why this matters

`NUM_STREAMS` propagates to several places that must agree, or perception either silently drops cameras or crashes:

| Consumer | Where | What it does |
|---|---|---|
| `vss-configurator-mv3dt` | `blueprint-configurator/blueprint_config.yml` line 579–586 | Computes `final_stream_count = min(NUM_STREAMS, max_streams_supported[HARDWARE_PROFILE].mv3dt)` and runs a `keep_count` op against `${VSS_DATA_DIR}/videos/${SAMPLE_VIDEO_DATASET}/` — **silently deletes excess `.mp4` files**, lex-sorted, last N kept |
| `vss-rtvi-cv-bev-fusion` | `services/rtvi/rtvi-cv/rtvi-cv-mv3dt/compose.yaml:53` (`MAX_EXPECTED_SENSORS: ${NUM_STREAMS:-4}`) | BEV Fusion buffers per-camera detections; if `MAX_EXPECTED_SENSORS` < actual streams, late cameras get dropped from fused frames |
| `vss-rtvi-cv-mv3dt` (perception) | `warehouse-mv3dt-app.yml:290-291` (`BATCH_SIZE` and `MAX_BATCH_SIZE` set to `${NUM_STREAMS:-4}`) | DeepStream batch size — wrong value triggers reallocation or OOM at engine build |
| `vss-vios-nvstreamer-mv3dt` / VST sensor-ms | streamcount registration with VST | If configurator registers more sensors than calibration covers, perception will receive frames for un-calibrated cameras and reject them |

The authoritative source for **how many cameras you have** is `calibration.json` — it has an explicit `sensors[]` array. Use that as ground truth; the `camInfo/` directory listing is a fallback.

## Step 1 — Count cameras from `calibration.json`

```bash
DATASET="${SAMPLE_VIDEO_DATASET:?}"
CAL_DIR="${VSS_APPS_DIR}/industry-profiles/warehouse-operations/warehouse-mv3dt-app/calibration/sample-data/${DATASET}"

# Authoritative: parse calibration.json's sensors[] array (id field per sensor)
if test -f "${CAL_DIR}/calibration.json"; then
  CAM_COUNT=$(jq '.sensors | length' "${CAL_DIR}/calibration.json")
  SENSOR_IDS=$(jq -r '.sensors[].id' "${CAL_DIR}/calibration.json")
  echo "From calibration.json: ${CAM_COUNT} sensors — ${SENSOR_IDS}"
else
  # Fallback: count camInfo files. The shipped sample uses Camera*.yml; AMC
  # output may be cam_*.yaml. Accept both extensions AND both naming patterns.
  CAM_COUNT=$(find "${CAL_DIR}/camInfo/" -maxdepth 1 \
    \( -name 'cam_*.yml' -o -name 'cam_*.yaml' -o -name 'Camera*.yml' -o -name 'Camera*.yaml' \) \
    2>/dev/null | wc -l)
  echo "From camInfo/ (fallback): ${CAM_COUNT} files"
fi

test "${CAM_COUNT}" -ge 2 || { echo "ERROR: MV3DT requires ≥2 cameras"; exit 1; }
```

If `CAM_COUNT == 0`: calibration not actually landed yet — go back to [`calibration-workflow.md`](calibration-workflow.md) Step 4. If you're on the sample path and this happens, check the actual directory contents — the shipped sample uses `Camera.yml`, `Camera_01.yml`, `Camera_02.yml`, `Camera_03.yml`.

If `CAM_COUNT == 1`: MV3DT is a multi-view stack — single-camera deployment isn't supported. Use the 2D / 3D-per-camera paths in `vss-deploy-profile/references/warehouse.md` instead.

## Step 2 — Check against the GPU's `max_streams_supported`

Before propagating `NUM_STREAMS`, confirm the GPU can actually run that many MV3DT streams. The configurator will trim videos otherwise (silently).

```bash
HARDWARE_PROFILE_VAL=$(grep '^HARDWARE_PROFILE=' "${ENV_FILE:-${VSS_APPS_DIR}/industry-profiles/warehouse-operations/.env}" | cut -d= -f2)
echo "HARDWARE_PROFILE=${HARDWARE_PROFILE_VAL}"

# Lookup mv3dt cap (from blueprint_config.yml lines 592-642)
case "${HARDWARE_PROFILE_VAL}" in
  RTXPRO6000BW)  CAP=18 ;;
  H100)          CAP=13 ;;
  RTXA6000ADA)   CAP=6  ;;
  L40S)          CAP=7  ;;
  L4|RTXA6000)   CAP=2  ;;
  IGX-THOR)      CAP=7  ;;
  DGX-SPARK)     CAP=4  ;;
  *)             CAP="?"; echo "WARN: HARDWARE_PROFILE=${HARDWARE_PROFILE_VAL} unknown to this skill" ;;
esac

echo "GPU cap for mv3dt: ${CAP}"
echo "Calibrated cameras: ${CAM_COUNT}"
echo "Effective stream count = min(${CAM_COUNT}, ${CAP})"
```

If `CAP < CAM_COUNT`, the user has more cameras than the GPU can process at MV3DT batch size. The configurator's `keep_count` file_management op will trim `.mp4` files at `${VSS_DATA_DIR}/videos/${SAMPLE_VIDEO_DATASET}/` down to `CAP`. Decide:

- **Accept the cap.** Continue — perception will run with `CAP` streams, fusion will see `CAP` cameras. Tell the user explicitly so they're not surprised.
- **Move to a larger GPU.** Re-check `HARDWARE_PROFILE` against the actual hardware (see SKILL.md Prerequisites §3 — `A6000` is **not** valid; use `RTXA6000` for Ampere, `RTXA6000ADA` for Ada).
- **Override the cap.** Add a hardware-profile override in `blueprint-configurator/blueprint_config.yml` (advanced, requires understanding the trade-off — FPS will drop).

## Step 3 — Sync NUM_STREAMS in .env

```bash
ENV_FILE="${VSS_APPS_DIR}/industry-profiles/warehouse-operations/.env"

# Use the lesser of CAM_COUNT and CAP — match what the configurator will compute
[ "${CAP}" = "?" ] && EFFECTIVE="${CAM_COUNT}" \
  || EFFECTIVE=$(( CAM_COUNT < CAP ? CAM_COUNT : CAP ))

# Idempotent in-place replace
if grep -q '^NUM_STREAMS=' "${ENV_FILE}"; then
  sed -i "s/^NUM_STREAMS=.*/NUM_STREAMS=${EFFECTIVE}/" "${ENV_FILE}"
else
  echo "NUM_STREAMS=${EFFECTIVE}" >> "${ENV_FILE}"
fi

grep '^NUM_STREAMS=' "${ENV_FILE}"
```

This single key drives all three consumers above — compose substitutes `${NUM_STREAMS}` at `up` time.

## Step 4 — Confirm DeepStream batch size

The shipped DeepStream config under `warehouse-mv3dt-app/deepstream/configs/` references `BATCH_SIZE` via env at runtime (set by `vss-configurator-mv3dt`). If the user hand-edited that config (rare), confirm `batch-size = NUM_STREAMS`:

```bash
DS_CFG_DIR="${VSS_APPS_DIR}/industry-profiles/warehouse-operations/warehouse-mv3dt-app/deepstream/configs"
grep -RnE '^batch-size|^max-batch-size' "${DS_CFG_DIR}" 2>/dev/null | head
```

Expected: lines show `${BATCH_SIZE}` / `${NUM_STREAMS}` (good — env-driven) or a number equal to `EFFECTIVE`. `vss-configurator-mv3dt` materializes the final DeepStream config on first start, so manual edits here are usually unnecessary — only intervene if a previous deploy left stale numbers.

## Step 5 — (Re-deploy only) Trim stale VST sensors

Relevant only when this is a **re-deploy** after the camera count changed (e.g. user re-calibrated with fewer cameras after a sensor failed). On a fresh deploy, VST is empty — skip.

`vss-configurator-mv3dt` registers cameras with VST on start; it does **not** delete stale ones. List and trim:

```bash
VST_HOST="${HOST_IP:-localhost}"
VST_PORT="${VST_PORT:-30888}"

# List current sensors
curl -sf "http://${VST_HOST}:${VST_PORT}/vst/api/v1/sensor/list" | jq -r '.[].sensorId'

# Compare against the sensor IDs in calibration.json — delete extras
KEEP_IDS=$(jq -r '.sensors[].id' "${CAL_DIR}/calibration.json")
for sid in $(curl -sf "http://${VST_HOST}:${VST_PORT}/vst/api/v1/sensor/list" | jq -r '.[].sensorId'); do
  if ! echo "${KEEP_IDS}" | grep -Fxq "${sid}"; then
    curl -X DELETE "http://${VST_HOST}:${VST_PORT}/vst/api/v1/sensor/${sid}"
  fi
done
```

If you prefer a clean slate, [`teardown.md`](teardown.md) drops VST data entirely and the next deploy will re-register sensors from scratch.

## Step 6 — Sanity check before deploy

```bash
ENV_FILE="${VSS_APPS_DIR}/industry-profiles/warehouse-operations/.env"

# Triplet must agree:
echo "calibration.json sensors: $(jq '.sensors | length' "${CAL_DIR}/calibration.json" 2>/dev/null || echo MISSING)"
echo "camInfo files:            $(find "${CAL_DIR}/camInfo/" -maxdepth 1 \( -name '*.yml' -o -name '*.yaml' \) 2>/dev/null | wc -l)"
echo "NUM_STREAMS (.env):       $(grep '^NUM_STREAMS=' "${ENV_FILE}" | cut -d= -f2)"
echo "GPU cap for mv3dt:        ${CAP}"
```

All three counts should line up; `NUM_STREAMS` ≤ `CAP`. Now proceed to [`deploy-rtvi-cv-3d-stack.md`](deploy-rtvi-cv-3d-stack.md).
