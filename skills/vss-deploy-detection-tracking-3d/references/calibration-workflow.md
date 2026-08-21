# Calibration Handoff

Load this reference when a user wants MV3DT / RT-CV-3D deployment but does not already have a usable `calibration.json` for the same cameras.

## Contents

- [Rule](#rule)
- [Inputs To Preserve](#inputs-to-preserve)
- [Local MP4 Calibration](#local-mp4-calibration)
- [RTSP Calibration](#rtsp-calibration)
- [Fetch AMC Outputs For Standalone RT-CV-3D](#fetch-amc-outputs-for-standalone-rt-cv-3d)
- [Validate The Result Before Returning](#validate-the-result-before-returning)
- [BEV Map Assets](#bev-map-assets)

## Rule

Do not duplicate the AutoMagicCalib workflow here. Hand off to `vss-generate-video-calibration`, then return to this standalone deployment skill with a validated `calibration.json`.

- Use `vss-generate-video-calibration` for local MP4 calibration and RTSP calibration. Its platform preflight must pass before deploying AMC, probing VIOS, uploading videos, capturing RTSP clips, or starting calibration. The calibration host needs `x86_64` and NVENC. If the preflight fails, stop the calibration handoff and ask the user to provide an existing `calibration.json`, run calibration on a supported `x86_64` dGPU host, or transfer generated AMC/MV3DT artifacts. Do not continue to deployment with fabricated or stale calibration. DGX Spark is `aarch64`, so use existing/generated artifacts for this flow.
- Do not load `vss-manage-video-io-storage` for normal AMC calibration execution. Use it only when the user is calibrating RTSP streams and the AMC RTSP prerequisite check shows VIOS is not already deployed/reachable. In that case, use the VIOS skill to bring up or verify VIOS, then return to the AMC RTSP flow.

## Inputs To Preserve

Before handing off, capture these values so the standalone RT-CV-3D workflow can resume cleanly:

- Desired input mode: `file` or `stream`.
- For file mode: path to synchronized MP4 directory or explicit `<sensor_id>=/path/file.mp4` mapping.
- For RTSP mode: ordered list of `<sensor_id or camera label>=rtsp://...` URLs.
- Desired project/dataset label.
- Whether the user asked for live OSD, saved perception video, saved BEV video, or both.
- Broker mode. For an explicit external broker request, preserve `MQTT_HOST`, `MQTT_PORT`, and `KAFKA_BOOTSTRAP`.

## Local MP4 Calibration

For local MP4s without calibration:

1. Route to the `vss-generate-video-calibration` skill by name; do not hardcode a filesystem path.
2. Run the AMC platform preflight before upload or calibration work. If it fails, report the unmet requirement and stop until the user provides calibration artifacts or chooses a supported calibration host.
3. Use its local-video mode and references.
4. Let the AMC skill create the project, upload videos, verify alignment/layout, run calibration, optionally run VGGT when requested or already staged, and report the result files.
5. Return here after `project_state == COMPLETED`; then run `Fetch AMC Outputs For Standalone RT-CV-3D`.

## RTSP Calibration

For RTSP cameras without calibration:

1. Route to the `vss-generate-video-calibration` skill by name and use its RTSP mode; do not hardcode a filesystem path.
2. Run the AMC platform preflight before VIOS checks, capture, or calibration work. If it fails, report the unmet requirement and stop until the user provides calibration artifacts or chooses a supported calibration host.
3. Let the AMC RTSP flow perform its VIOS prerequisite check and confirm that the AMC microservice has a correct `VIOS_BASE_URL`.
4. If VIOS is reachable but the AMC microservice env is missing or has the wrong `VIOS_BASE_URL`, follow the AMC deploy reference to set `VIOS_BASE_URL` in the AMC generated env and recreate/restart AMC before capture. Do not add VIOS settings to standalone RT-CV-3D `docker/.env`; RT-CV-3D does not consume them.
5. If VIOS is missing, route to `vss-manage-video-io-storage` by name only to bring up or verify VIOS, then return to the AMC RTSP flow and repeat the `VIOS_BASE_URL` env confirmation. Do not treat VIOS as a standalone RT-CV-3D deployment prerequisite.
6. Preserve the final ordered RTSP URL list. The final standalone deployment will register streams with the direct REST registration block in `references/configure-cameras.md`, which waits on REST `/api/v1/ready` before registration.
7. Return here after `project_state == COMPLETED`; then run `Fetch AMC Outputs For Standalone RT-CV-3D`.

## Fetch AMC Outputs For Standalone RT-CV-3D

After the AMC skill returns, capture `project_id` and fetch the downstream artifacts this standalone deployment needs:

- `calibration.json` from the AMC `export_calibration` endpoint. This becomes `CALIBRATION_JSON` for `scripts/generate-configs.sh`.
- `mv3dt_result` ZIP from AMC. Use it for `transforms.yml` and any BEV helper assets. BEV is ready only when the final `BEV_DATASET_PATH` has both `map.png` and `transforms.yml`.

Prefer VGGT output when VGGT completed; otherwise use the base AMC output.

```bash
cd "${RTCV3D_APP:?set RTCV3D_APP}"
project_id="${project_id:?set AMC project_id}"
AMC_BASE="${AMC_BASE:-http://localhost:${VSS_AUTO_CALIBRATION_PORT:-8010}/v1}"
AMC_OUT="${RTCV3D_APP}/generated/amc/project_${project_id}"
BEV_DATASET_PATH="${AMC_OUT}/bev-dataset"
mkdir -p "${AMC_OUT}" "${BEV_DATASET_PATH}"

info="$(curl -sf "${AMC_BASE}/get_project_info/${project_id}")"
RESULT_TYPE=amc
if printf '%s' "${info}" | jq -e '.project_info.vggt_state == "COMPLETED"' >/dev/null 2>&1; then
  RESULT_TYPE=vggt
fi
echo "Using AMC result_type=${RESULT_TYPE}"

ZIP="${AMC_OUT}/mv3dt_output_${RESULT_TYPE}.zip"
curl -sfL "${AMC_BASE}/result/${project_id}/mv3dt_result?result_type=${RESULT_TYPE}" -o "${ZIP}"
unzip -l "${ZIP}"

EXPORT_URL="${AMC_BASE}/result/${project_id}/export_calibration?result_type=${RESULT_TYPE}&calibration_type=cartesian"
EXPORT_RESPONSE="$(mktemp "${AMC_OUT}/export_response.XXXXXX.json")"
EXPORT_HTTP="$(curl -sS -o "${EXPORT_RESPONSE}" -w '%{http_code}' -X POST "${EXPORT_URL}")"
case "${EXPORT_HTTP}" in 2*) ;; *) echo "ERROR: AMC export_calibration POST failed HTTP ${EXPORT_HTTP}" >&2; cat "${EXPORT_RESPONSE}" >&2; exit 1 ;; esac
EXPORT_RESPONSE="${EXPORT_RESPONSE}" python3 - <<'PY'
import json, os
path = os.environ['EXPORT_RESPONSE']
with open(path, encoding='utf-8') as f:
    text = f.read().strip()
if not text:
    raise SystemExit('ERROR: empty AMC export_calibration response')
try:
    d = json.loads(text)
except json.JSONDecodeError as exc:
    raise SystemExit(f'ERROR: export_calibration response is not JSON: {exc}: {text[:500]}')
if d.get('code', 0) != 0:
    raise SystemExit(f"ERROR: export_calibration returned code={d.get('code')}: {text[:1000]}")
export_file = d.get('export_file') or d.get('file') or d.get('path')
if 'export_file' in d and not export_file:
    raise SystemExit(f"ERROR: export_calibration response has empty export_file: {text[:1000]}")
print('export_calibration response OK')
PY

CAL_TMP="$(mktemp "${AMC_OUT}/calibration.XXXXXX.json")"
curl -sfL "${EXPORT_URL}" -o "${CAL_TMP}"
CAL_TMP="${CAL_TMP}" python3 - <<'PY'
import json, os, re
path = os.environ['CAL_TMP']
with open(path, encoding='utf-8') as f:
    d = json.load(f)
sensors = d.get('sensors')
if not isinstance(sensors, list):
    raise SystemExit('ERROR: exported calibration.json lacks sensors list')
ids = []
for s in sensors:
    if not isinstance(s, dict) or s.get('type') != 'camera':
        continue
    sid = s.get('id')
    if not isinstance(sid, str) or not sid:
        raise SystemExit('ERROR: exported camera sensor has missing/invalid id')
    if sid in {'.', '..'} or '/' in sid or '\\' in sid:
        raise SystemExit(f'ERROR: unsafe camera id in exported calibration: {sid!r}')
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in sid):
        raise SystemExit(f'ERROR: control character in camera id: {sid!r}')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', sid):
        raise SystemExit(f'ERROR: unsafe camera id in exported calibration: {sid!r}')
    if 'cameraMatrix' not in s:
        raise SystemExit(f'ERROR: camera {sid!r} missing cameraMatrix')
    ids.append(sid)
if len(ids) < 2:
    raise SystemExit(f'ERROR: MV3DT requires at least 2 camera sensors; found {len(ids)}')
if len(ids) != len(set(ids)):
    raise SystemExit('ERROR: duplicate camera sensor ids in exported calibration')
print('validated exported camera sensors:', ', '.join(ids))
PY
mv -f "${CAL_TMP}" "${AMC_OUT}/calibration.json"
export CALIBRATION_JSON="${AMC_OUT}/calibration.json"

unzip -j -o "${ZIP}" '*transforms.yml' -d "${BEV_DATASET_PATH}"
if unzip -l "${ZIP}" | awk '{print $4}' | grep -Eq '(^|/)map[.]png$'; then
  unzip -j -o "${ZIP}" '*map.png' -d "${BEV_DATASET_PATH}"
fi

if [ ! -f "${BEV_DATASET_PATH}/map.png" ]; then
  for candidate in     "${LAYOUT_PNG:-}"     "${VSS_APPS_DIR:-}/services/auto-calibration/projects/project_${project_id}/manual_adjustment/layout.png"; do
    if [ -n "${candidate}" ] && [ -f "${candidate}" ]; then
      cp "${candidate}" "${BEV_DATASET_PATH}/map.png"
      break
    fi
  done
fi

test -f "${CALIBRATION_JSON}" || { echo "ERROR: exported calibration.json missing"; exit 1; }
test -f "${BEV_DATASET_PATH}/transforms.yml" || { echo "ERROR: transforms.yml missing from MV3DT ZIP"; exit 1; }
if [ ! -f "${BEV_DATASET_PATH}/map.png" ]; then
  echo "WARN: map.png is missing; BEV visualizer cannot run until the map image used during calibration is provided."
fi
if [ -f "${BEV_DATASET_PATH}/map.png" ] && [ -f "${BEV_DATASET_PATH}/transforms.yml" ]; then
  echo "BEV_READY=1"
else
  echo "BEV_READY=0"
fi

echo "CALIBRATION_JSON=${CALIBRATION_JSON}"
echo "BEV_DATASET_PATH=${BEV_DATASET_PATH}"
```

Then generate standalone runtime configs from the exported `calibration.json`:

```bash
cd "${RTCV3D_APP}"
./scripts/generate-configs.sh "${CALIBRATION_JSON}"
```

Use the generated `generated/camInfo/*.yml` from this script as runtime camInfo. If the AMC ZIP also contains camInfo files, keep them as provenance/debug artifacts; do not copy them over `generated/camInfo` unless the standalone generator fails and the user approves that fallback.

## Validate The Result Before Returning

```bash
CALIBRATION_JSON="${CALIBRATION_JSON:?set path to generated calibration.json}"
test -f "${CALIBRATION_JSON}" || { echo "ERROR: calibration result missing: ${CALIBRATION_JSON}"; exit 1; }
CALIBRATION_JSON="${CALIBRATION_JSON}" python3 - <<'PY'
import json, os, re
with open(os.environ['CALIBRATION_JSON'], encoding='utf-8') as f:
    d = json.load(f)
ids = []
for s in d.get('sensors', []):
    if isinstance(s, dict) and s.get('type') == 'camera':
        sid = s.get('id')
        if not isinstance(sid, str) or not sid:
            raise SystemExit('ERROR: camera sensors need non-empty ids')
        if sid in {'.', '..'} or '/' in sid or '\\' in sid or not re.fullmatch(r'[A-Za-z0-9_.-]+', sid):
            raise SystemExit(f'ERROR: unsafe camera id: {sid!r}')
        ids.append(sid)
if len(ids) < 2:
    raise SystemExit(f'ERROR: MV3DT needs at least 2 calibrated camera sensors; found {len(ids)}')
if len(ids) != len(set(ids)):
    raise SystemExit('ERROR: duplicate camera sensor ids')
print('calibrated camera sensors:', ', '.join(ids))
PY
```

Then return to `configure-cameras.md` to validate `generated/camInfo/`, set `docker/.env`, and stage configs.

## BEV Map Assets

BEV visualization needs a dataset directory containing:

- `map.png`
- `transforms.yml`

If a user-supplied `map.png` exists but `transforms.yml` was not available from the AMC MV3DT ZIP, generate transforms with the standalone script after returning to `RTCV3D_APP` and stage both files in one dataset directory:

```bash
cd "${RTCV3D_APP}"
BEV_DATASET_PATH="${RTCV3D_APP}/generated/bev-dataset"
mkdir -p "${BEV_DATASET_PATH}"
ln -sfn /path/to/map.png "${BEV_DATASET_PATH}/map.png"
./scripts/generate-transforms.sh "${CALIBRATION_JSON}" "${BEV_DATASET_PATH}/map.png" -o "${BEV_DATASET_PATH}/transforms.yml" --force
```

If no map image exists, do not run the BEV visualizer yet; it requires both `map.png` and `transforms.yml`. Request the map image used during calibration, or report that only perception-grid output can be produced until the BEV assets are available.
