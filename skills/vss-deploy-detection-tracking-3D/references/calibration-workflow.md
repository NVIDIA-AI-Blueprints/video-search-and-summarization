# Calibration workflow (chain into AMC)

Parent: [`../SKILL.md`](../SKILL.md). Load this reference **only when** the user picked `videos` or `rtsp` in Q1 AND the calibration check in Q2 found `calibration.json` + `camInfo/` missing or incomplete.

**Skip when:** Q1 = `sample` (calibration ships with the repo) or the user has supplied a calibration path themselves — go straight to [`configure-cameras.md`](configure-cameras.md) → [`deploy-rtvi-cv-3d-stack.md`](deploy-rtvi-cv-3d-stack.md).

This reference drives AMC end-to-end via its REST API — the user does **not** open the AMC UI. Hand-back to SKILL.md happens once calibration files are landed at the MV3DT mount path.

## Where calibration must end up

For perception and BEV fusion to read them, calibration files must live at:

```
${VSS_APPS_DIR}/industry-profiles/warehouse-operations/warehouse-mv3dt-app/calibration/sample-data/${SAMPLE_VIDEO_DATASET}/
├── calibration.json                        # consumed by vss-behavior-analytics-mv3dt (warehouse-mv3dt-app.yml:25)
├── camInfo/cam_*.yaml                      # consumed by vss-rtvi-cv-mv3dt (warehouse-mv3dt-app.yml:283)
└── images/                                 # optional reference frames, matches sample layout
```

The user's Q3 slug becomes the `${SAMPLE_VIDEO_DATASET}` directory name.

## Step 1 — Deploy AMC standalone

Hand off to the AMC skill's deploy reference: walk **Path B (standalone)** in [`../../vss-generate-video-calibration/references/deploy-auto-calibration-service.md`](../../vss-generate-video-calibration/references/deploy-auto-calibration-service.md), which is:

```bash
cd "${VSS_APPS_DIR}"
COMPOSE_PROFILES=auto-calib docker compose \
  --env-file industry-profiles/warehouse-operations/.env \
  up -d
```

Wait for readiness:

```bash
curl -sf "http://localhost:${VSS_AUTO_CALIBRATION_PORT:-8010}/v1/ready"
# Expected: {"code":0,"message":"VSS Auto Calibration Microservice is ready"}
```

This brings up `vss-auto-calibration` (port 8010) + `vss-auto-calibration-ui` (port 5000) **without** perception, BEV Fusion, mosquitto, nvstreamer-mv3dt, or VST. The `auto-calib` profile shares only `redis` with MV3DT — meaning teardown later will not collide with anything we're about to deploy.

**Note:** `auto_calib` (standalone) is the right choice for the `videos` mode. For the `rtsp` mode, AMC needs VIOS reachable to ingest live streams; check `VIOS_BASE_URL` resolves to a running VST as documented in `deploy-auto-calibration-service.md` Step 2b. If the user only has RTSP URLs but no pre-existing VIOS, the AMC skill's `rtsp.md` reference will deploy VIOS first.

## Step 2 — Drive AMC API end-to-end

Use the AMC skill's mode-specific reference for the upload portion, then the shared tail in its `SKILL.md` for verify → calibrate → poll → results.

### Videos mode

Per [`../../vss-generate-video-calibration/references/videos.md`](../../vss-generate-video-calibration/references/videos.md):

```python
# Inputs collected from the user:
#   VIDEO_DIR  — directory with cam_00.mp4 ... cam_NN.mp4 (4 cams expected for MV3DT)
#   PROJECT    — short project name
#   DETECTOR   — "resnet" or "transformer" (from Q3)
#   BASE_URL   — http://<HOST_IP>:8010 (AMC microservice)
import requests, time
from pathlib import Path

# 1. Create project
r = requests.post(f"{BASE_URL}/v1/create_project",
                  data={"project_name": PROJECT})
project_id = r.json()["project_id"]

# 2. Upload videos (sorted by filename — server assigns camera indices in upload order)
videos = sorted(Path(VIDEO_DIR).glob("cam_*.mp4"))
files = [("files", (v.name, v.read_bytes(), "video/mp4")) for v in videos]
requests.post(f"{BASE_URL}/v1/upload_video_files/{project_id}",
              files=files, timeout=300)

# 3. Resolve + upload settings / alignment / layout (auto-scan VIDEO_DIR + parent)
#    Per videos.md "Step 3 — Resolve Local Files". If missing → UI fallback.

# 4. Verify → calibrate → poll (shared tail, vss-generate-video-calibration/SKILL.md:40-99)
assert requests.post(f"{BASE_URL}/v1/verify_project/{project_id}").json()["project_state"] == "READY"
requests.post(f"{BASE_URL}/v1/calibrate/{project_id}",
              json={"detector_type": DETECTOR})

while True:
    info = requests.get(f"{BASE_URL}/v1/get_project_info/{project_id}").json()
    state = info["project_info"]["project_state"]
    if state == "COMPLETED":
        break
    if state == "ERROR":
        log = requests.get(f"{BASE_URL}/v1/amc/calibrate/{project_id}/log").text
        raise RuntimeError(f"AMC failed:\n{log}")
    time.sleep(10)
```

### RTSP mode

Same shape, but the upload step is replaced by VIOS-mediated RTSP ingest per [`../../vss-generate-video-calibration/references/rtsp.md`](../../vss-generate-video-calibration/references/rtsp.md): create project, point AMC at the RTSP URLs (the rtsp.md script handles the ingest), then proceed to the shared tail.

Typical wall-clock for the calibrate step:

| Source | Time |
|---|---|
| 4 user videos (~5 min each) | 10–60 min |
| RTSP (4 streams, 5 min capture) | 10–60 min |
| Bundled sample | 10–30 min |

## Step 3 — Fetch the MV3DT export

The AMC microservice exposes a dedicated MV3DT export endpoint (documented in [`../../vss-generate-video-calibration/SKILL.md:176-196`](../../vss-generate-video-calibration/SKILL.md)):

```bash
# Fetch the MV3DT-format calibration output as a ZIP
curl -sfL "http://localhost:8010/v1/result/${project_id}/mv3dt_result?result_type=amc" \
  -o /tmp/mv3dt_output.zip

# Inspect — ZIP contains transforms.yml (and possibly per-cam files)
unzip -l /tmp/mv3dt_output.zip
```

For **VGGT-refined** output (only if VGGT was staged + ran to `COMPLETED` — see `deploy-auto-calibration-service.md` Step 2 and the optional VGGT section below):

```bash
curl -sfL "http://localhost:8010/v1/result/${project_id}/mv3dt_result?result_type=vggt" \
  -o /tmp/mv3dt_output_vggt.zip
```

Use the VGGT ZIP in place of the AMC ZIP if available — VGGT refinement typically improves accuracy.

## Step 4 — Land calibration at the MV3DT mount path

```bash
DATASET="${SAMPLE_VIDEO_DATASET:?slug from Q3}"
CAL_DIR="${VSS_APPS_DIR}/industry-profiles/warehouse-operations/warehouse-mv3dt-app/calibration/sample-data/${DATASET}"

mkdir -p "${CAL_DIR}/camInfo" "${CAL_DIR}/images"

# camInfo/*.yaml — perception mounts this directory at /tmp/camInfo/
unzip -j -o /tmp/mv3dt_output.zip 'camInfo/*' -d "${CAL_DIR}/camInfo/" 2>/dev/null \
  || unzip -j -o /tmp/mv3dt_output.zip '*.yaml' -d "${CAL_DIR}/camInfo/"

# calibration.json — consolidated calibration consumed by behavior-analytics-mv3dt
#   Sourced from AMC project output: services/auto-calibration/projects/project_<id>/output/
PROJECT_OUTPUT="${VSS_APPS_DIR}/services/auto-calibration/projects/project_${project_id}/output"
cp "${PROJECT_OUTPUT}/calibration.json" "${CAL_DIR}/calibration.json" 2>/dev/null \
  || echo "WARN: calibration.json not at expected path — check AMC project layout"

# Optional: reference images for the dataset directory layout
ls "${PROJECT_OUTPUT}"/*.png 2>/dev/null | head -4 | xargs -I{} cp {} "${CAL_DIR}/images/" || true

# Permissions — perception mount must be readable inside the container
sudo chmod -R a+rX "${CAL_DIR}"
```

> **Permission rule:** always `chmod`, never `chown`. Containers run as varied UIDs; world-readable is the safe baseline. This matches the convention in `vss-deploy-profile/references/data-directory.md`.

**Sanity check** before moving on:

```bash
ls "${CAL_DIR}/camInfo/"*.yaml | wc -l   # must equal user's camera count (typically 4)
test -f "${CAL_DIR}/calibration.json" && echo OK
```

Both must pass. If `camInfo/` is empty, the ZIP layout was unexpected — open `/tmp/mv3dt_output.zip` and confirm where the YAML files live. If `calibration.json` is missing, AMC may not have produced it for this project; pull the calibration log: `curl http://localhost:8010/v1/amc/calibrate/${project_id}/log`.

## Step 5 — Tear down AMC

Leave the host clean before MV3DT comes up — they share `redis` and the host:port for `vss-auto-calibration` (still on `bp_wh_*_mv3dt` profile gating, so it will redeploy correctly under MV3DT later).

```bash
cd "${VSS_APPS_DIR}"
COMPOSE_PROFILES=auto-calib docker compose \
  --env-file industry-profiles/warehouse-operations/.env \
  down
```

Project state under `${VSS_APPS_DIR}/services/auto-calibration/projects/project_<id>/` is bind-mounted, so it survives the down. You can re-run AMC later without losing work.

## Step 6 — Return to SKILL.md

Calibration is now on disk at `${CAL_DIR}`. Hand back to the parent flow:

1. Walk [`configure-cameras.md`](configure-cameras.md) — set `NUM_STREAMS` to the `camInfo/*.yaml` count, sync DeepStream batch sizes.
2. Walk [`deploy-rtvi-cv-3d-stack.md`](deploy-rtvi-cv-3d-stack.md) — `docker compose up` with `MODE=mv3dt` + `BP_PROFILE=bp_wh_kafka` + `MINIMAL_PROFILE="true"`.
3. Walk [`verify-and-view.md`](verify-and-view.md) — confirm perception FPS, BEV ready, VST video wall.

## VGGT refinement (optional)

If the user wants higher accuracy and is willing to stage the VGGT model:

1. The VGGT model is a ~4.7 GB file at `${VSS_DATA_DIR}/auto-calib/vggt/vggt_1B_commercial.pt`. Staging steps (HuggingFace license accept + token + download) live in [`../../vss-generate-video-calibration/references/deploy-auto-calibration-service.md`](../../vss-generate-video-calibration/references/deploy-auto-calibration-service.md) Step 2 — don't duplicate here.
2. After Step 2 above completes (`project_state == COMPLETED`), check `vggt_state` in `/v1/get_project_info/<id>`. If `READY`, fire VGGT:

   ```bash
   curl -X POST "http://localhost:8010/v1/vggt/calibrate/${project_id}"
   # Poll vggt_state via /v1/get_project_info/<id> until COMPLETED
   ```
3. Step 3 above already handles VGGT export — use `result_type=vggt` when fetching the ZIP.

Skip this entire section unless the user explicitly opted in — VGGT staging is not on the happy path.

## Failure modes specific to this chain

| Symptom | Fix |
|---|---|
| `verify_project` not `READY` | Alignment / layout missing — fall back to UI per `vss-generate-video-calibration/SKILL.md#ui-fallback-pattern` |
| `project_state == ERROR` early | Video naming wrong (must be `cam_00.mp4`, `cam_01.mp4`, … contiguous) or videos not time-synced |
| Calibration stuck `RUNNING` > 90 min | Scene too static (low tracklet count). Pull `GET /v1/amc/calibrate/<id>/log` — typically suggests adding more motion or longer captures |
| MV3DT export ZIP missing `camInfo/*.yaml` | AMC project did not produce MV3DT export — check `/v1/get_project_info/<id>` for `vggt_state` or `project_state` not `COMPLETED` |
| User has only 1–3 cameras | MV3DT requires multi-view (≥2). With < 4, set `NUM_STREAMS` to the actual count and confirm the camera-clustering script (`create_camera_clusters.py`) is configured for that count |

For everything else, see [`troubleshooting.md`](troubleshooting.md).
