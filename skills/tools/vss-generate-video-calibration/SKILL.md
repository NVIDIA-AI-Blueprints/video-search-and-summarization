---
name: vss-generate-video-calibration
description: Use this skill when running AutoMagicCalib on local MP4s, RTSP, or the bundled sample dataset, or when deploying vss-auto-calibration. Do not use for non-AMC calibration or runtime analytics.
license: Apache-2.0
metadata:
  version: "3.3.0"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint operational"
---
## Purpose

Run AutoMagicCalib end-to-end on local files, RTSP streams, or the bundled sample dataset and (when needed) deploy the AMC microservice.

## Instructions

Follow the routing tables and step-by-step workflows below. Each section that ends in *workflow*, *quick start*, or *flow* is intended to be executed top-to-bottom. Detailed reference material lives in `references/`; load only the reference needed for the selected input mode.

## Examples

Worked end-to-end examples are kept under `evals/` (each `*.json` manifest contains a runnable scenario) and inline in the per-workflow `curl` blocks below. Run a Tier-3 evaluation with `nv-base validate <this-skill-dir> --agent-eval` to replay them.

## Limitations

- Requires the matching VSS profile / microservice to be deployed and reachable from the caller.
- NGC-hosted models and NIMs may be subject to rate-limits, GPU memory requirements, and license restrictions.
- Concurrency, GPU memory, and storage limits depend on the host hardware and the profile's compose file.

## Troubleshooting

- **Error**: REST call returns connection refused. **Cause**: target microservice not running. **Solution**: probe `/docs` or `/health`; redeploy via `vss-deploy-profile` or the matching `vss-deploy-*` skill.
- **Error**: HTTP 401/403 from NGC pulls. **Cause**: missing/expired `NGC_CLI_API_KEY`. **Solution**: `docker login nvcr.io` and re-export the key before retrying.
- **Error**: container OOM or model fails to load. **Cause**: insufficient GPU memory for the selected profile. **Solution**: switch to a smaller variant or free GPUs via `docker compose down`.

# VSS Generate Video Calibration

Run AutoMagicCalib over one of three input sources and drive the calibration through the microservice REST API. Input collection differs per source; linear-media staging through results is shared and lives in this file. Pick the right input-mode reference and pair it with the [Shared Calibration Tail](#shared-calibration-tail) below.

Shared helper references are loaded only when needed:
- Read [`references/common-steps.md`](references/common-steps.md) when a mode reference needs the shared `create_project`, video-upload, or handoff snippets.
- Read [`references/calibration-tail.md`](references/calibration-tail.md) when you need the reusable Python implementation of the linear-media → verify → VGGT/post-process (when available) → AMC/post-process → results tail.

## Input Routing

Match the user's request to a mode, then load that mode's reference for input collection, mode-specific API calls, and the full Python script.

| User says / has | Mode | Reference |
|---|---|---|
| "launch AMC" / "deploy auto-calibration" / "set up auto-magic-calib" / "start AMC microservice" | `deploy` | [`references/deploy-auto-calibration-service.md`](references/deploy-auto-calibration-service.md) |
| "calibrate my videos" / "calibrate from video files" / local `cam_*.mp4` files | `videos` | [`references/videos.md`](references/videos.md) |
| "calibrate RTSP streams" / "calibrate from live cameras" / live RTSP URLs | `rtsp` | [`references/rtsp.md`](references/rtsp.md) |
| "test sample dataset" / "verify AMC install" / "launch and test" | `sample-dataset` | [`references/sample-dataset.md`](references/sample-dataset.md) |

**Disambiguation rule:** if the user is asking to launch / deploy / set up AMC (no calibration verb) → `deploy`. If they provide RTSP URLs → `rtsp`. If they mention local files / a videos directory → `videos`. If they ask to verify install or test the bundled sample → `sample-dataset`. Combined intents (e.g. "launch AMC and calibrate my videos") → walk `deploy` first, then the calibration mode. When ambiguous, ask via `AskUserQuestion`.

## Prerequisites (shared across calibration modes)

- Platform preflight from [`references/deploy-auto-calibration-service.md` Step 0](references/deploy-auto-calibration-service.md#step-0--platform-preflight) passes before any AMC deploy or calibration API work. The calibration host needs `x86_64`, NVIDIA GPU access, and NVENC hardware encoder support. If the preflight fails, stop immediately, tell the user which requirement was not met, and ask them to provide an existing `calibration.json`, run calibration on a supported `x86_64` dGPU host, or transfer generated calibration artifacts. Do not continue AMC setup, VIOS probing, capture, upload, or calibration automatically. DGX Spark is `aarch64`, so use existing/generated artifacts for this flow.
- AMC microservice + UI running. If not, walk [`references/deploy-auto-calibration-service.md`](references/deploy-auto-calibration-service.md) first.
- Microservice reachable at `http://<HOST_IP>:${VSS_AUTO_CALIBRATION_HOST_PORT:-8010}/v1/ready` → `{"code":0,...}`.
- Projects directory writable by the container user. If you didn't just deploy (so Step 5 of the deploy reference hasn't run), confirm the write test in [`references/deploy-auto-calibration-service.md` § Step 5](references/deploy-auto-calibration-service.md#step-5--confirm-the-projects-directory-is-writable) — otherwise the first `create_project` returns `[Errno 13] Permission denied`.
- Python 3 with `requests` installed (each input-mode reference includes a self-healing venv fallback for direct runs).

Mode-specific prerequisites (VIOS for `rtsp`, sample zip for `sample-dataset`) live in the respective references. The platform preflight applies even when an AMC service is already running.

## Shared Calibration Tail

The linear-media → verify → VGGT/post-process (when available) → AMC/post-process → results sequence is identical regardless of input mode. After the mode-specific reference has uploaded videos / ingested RTSP clips / uploaded the bundled sample, run this tail. Use [`references/calibration-tail.md`](references/calibration-tail.md) for the shared Python snippet.

AMC UI numbering is fixed: **1 Project Setup → 2 Video Configuration → 3 Parameters → 4 Rectification → 5 Manual Alignment → 6 Execute Calibration → 7 Results**. Skill Steps A–F below are shared API workflow labels, not replacement UI step numbers: A–B complete prerequisites for UI Step 6, C–F execute there, and review happens in UI Step 7.

### Step A — Stage Linear Media

AMC v3.3.0 cannot calibrate raw media. After the mode-specific workflow has uploaded videos or completed RTSP ingest, explicitly choose one path before verification:

- **Already-linear/pinhole media** — call `POST /v1/linear_media/<project_id>` or choose UI Step 4 **Videos Are Rectified**, then require `rectification_state == "COMPLETED"`.
- **Distorted media** — open AMC UI **Step 4: Rectification**. Choose Auto Rectification or Manual Rectification; for either path review every camera preview, tune the supported distortion model/parameters when needed, and click **Generate rectified videos**. Auto/manual estimation first reaches `READY_FOR_REVIEW`; only the generated-video commit reaches `rectification_state == "COMPLETED"`.

The Step 4 UI supports `simple_radial`, `radial`, and `simple_divisional` distortion models, live preview/grid review, per-project committed parameters, rectification logs, and a download of the generated rectified videos. Do not treat `READY_FOR_REVIEW` as calibrated or continue to alignment from it.

Rectification produces `rectified.mp4` and `rectified.jpg`. UI interactive alignment uses rectified-image coordinates. External alignment upload defaults to `coord_space=original`; pass `coord_space=rectified` only when its camera points already match rectified frames. Never call `/v1/calibrate/<project_id>` before linear-media staging and alignment are complete. Re-rectification invalidates verification, AMC/VGGT outputs, and post-processing: verify again, relaunch desired calibration(s), then re-run post-processing after each completed result.

Before alignment, set the floor-plan world scale in UI **Step 3: Parameters**. Upload `layout.png` in Step 2 first, then either enter a positive layout pixels-per-metre value or use the UI’s two-point measurement with a known physical distance. This scale controls world/layout conversion and post-processing; do not guess it when an on-site measurement is available. Changing scale or alignment after calibration likewise requires a new post-process pass. Step 3 also exposes optional focal lengths, virtual-GT floor-grid spacing, and red-pole/person height.

### Step B — Verify Project

```
POST /v1/verify_project/<project_id>
```

Fresh-project response: `{"project_state": "READY"}` — must be `READY` before first calibration. A persisted project with completed raw results can remain aggregate `COMPLETED`; its pipeline-specific state determines rerun/export handling. If fresh verification is not READY, re-check videos, linear-media state, alignment, and layout.

### Step C — Run VGGT First When Available

After project verification, check `vggt_state` before starting AMC. VGGT is an independent, multi-camera-only method. When available, make it first/default because it normally completes faster; then run AMC independently for comparison. These are separate choices in UI **Step 6: Execute Calibration**; VGGT does not depend on AMC output.

- `READY` — run VGGT first unless user explicitly requests AMC-only.
- `RUNNING` — resume polling persisted run; never start AMC concurrently.
- `COMPLETED` — reuse raw VGGT result and run post-processing if `vggt_export_ready` is still false.
- `MODEL_MISSING` — do not call VGGT or claim it is ready. Report the absent model and continue AMC. Stage the licensed model only when VGGT is requested; see [`references/deploy-auto-calibration-service.md`](references/deploy-auto-calibration-service.md) Step 2.
- `ERROR` or another non-ready terminal state — skip VGGT and preserve exact state in report.

```
POST /v1/vggt/calibrate/<project_id>
GET  /v1/get_project_info/<project_id>                    # poll vggt_state
POST /v1/postprocess/<project_id>                          # mandatory after successful multi-camera VGGT
GET  /v1/get_project_info/<project_id>                    # poll postprocess_state
GET  /v1/vggt_results/<project_id>/evaluation_statistics  # only when GT exists
```

Run the shared post-process immediately after successful VGGT. Require `postprocess_state == "COMPLETED"` before using or exporting VGGT-derived artifacts. A failed VGGT or VGGT post-process does not invalidate AMC; report that failure and continue AMC only after no VGGT/post-process job remains running.

### Step D — Start AMC Calibration

**Confirm the plan before calibrating.** Whether the settings file and detector were auto-detected or asked, present a short summary and confirm via `AskUserQuestion` before the `POST /calibrate`. The resolved values are the defaults, so confirming is one click — but the user can switch the detector or skip an auto-detected settings file. Summarize:

- **Detector** — `resnet` or `transformer` (the value to be sent).
- **Calibration settings** — the file being applied (path), or default parameters (with the option to tune them in the UI first — see below).
- **Optional overrides** — ground-truth zip and focal lengths, if any.

The sample-dataset install-check run uses a fixed `resnet` and can proceed without this confirmation.

```
POST /v1/calibrate/<project_id>
Content-Type: application/json

{"detector_type": "resnet"}   # or "transformer"
```

`detector_type` is a separate `/calibrate` parameter — **not** consumed by `/v1/config/<id>`. If the user provided a calibration settings file, parse it for `"detector"` / `"detector_type"` and use that value. If the file doesn't specify one, the default (`resnet`) is the value shown in the confirmation above — the user can switch it there before calibrating. If there's no settings file at all, ask the user via `AskUserQuestion`:

- `resnet` — default, fast.
- `transformer` — slower, better under heavy occlusion.

UI Step 3 (Parameters) does NOT cover detector choice; never assume the user picked one in the UI.

**Also when there's no settings file, ask whether to tune the calibration parameters first** (`AskUserQuestion`):

- **Proceed with the default parameters** — well-suited to typical warehouse scenes; recommended unless the user has specific tuning in mind.
- **Adjust parameters in the UI first** — open the project, go to Step 3: Parameters, change values, and click Save; then continue.

Wait for the user's choice — and, if they choose to tune, for them to confirm they've Saved — before calling `/calibrate`. Finish all parameter and rectification edits before either calibration starts; UI settings are disabled while AMC or VGGT is running.

### Step E — Poll for AMC Completion

```
GET /v1/get_project_info/<project_id>
```

Poll every 10 s. Use pipeline-specific `project_info.amc_state`, not aggregate `project_state` (a completed VGGT result can keep the aggregate project successful even if AMC later fails):

| State | Meaning |
|---|---|
| `RUNNING` | AMC calibration in progress |
| `COMPLETED` | Finished |
| `ERROR` | Failed — pull log via `GET /v1/amc/calibrate/<id>/log` |

When calibration starts, surface the project ID, the UI URL (`http://<HOST_IP>:${VSS_AUTO_CALIBRATION_UI_HOST_PORT:-5000}`), and the log endpoint so the user can watch progress while the run proceeds. During `RUNNING`, emit a progress line at least once a minute with elapsed time so a long run doesn't look stalled. On `ERROR`, fetch and show the last lines of `GET /v1/amc/calibrate/<id>/log` before stopping. Live logs can also be streamed via `GET /v1/calibrate/<project_id>/log/<type>/stream`.

Typical time: **10–60 min** (your-own videos), **10–30 min** (bundled sample).

### Step F — Post-process AMC and Compare Results

For multi-camera projects, layout post-processing is mandatory after **each** successful calibration: run it immediately after VGGT, then call it again after AMC. The shared endpoint regenerates derived artifacts for every raw result tree currently available; the second call is still required because AMC resets `postprocess_state` and adds/changes its raw result tree.

```
POST /v1/postprocess/<project_id>
GET  /v1/get_project_info/<project_id>  # poll postprocess_state until COMPLETED
```

Do not report or export a derived multi-camera result until the post-process call following that calibration reaches `COMPLETED`; raw AMC/VGGT results may remain valid when post-processing fails.

```
GET /v1/get_project_info/<project_id>                    # project state
GET /v1/result/<project_id>/evaluation_statistics        # only if GT uploaded
GET /v1/result/<project_id>/overlay_image                # visual overlay (PNG)
GET /v1/amc/calibrate/<project_id>/log                   # calibration log
```

Evaluation response includes `Average L2 distance(m)` and `Average reprojection error 0(px)`. Evaluation metrics are produced **only when a ground-truth `GT.zip` was uploaded** — a missing `evaluation_statistics` result is normal otherwise and is not the end of result reporting.

After `COMPLETED`, always give the user a way to review result for that exact project, regardless of whether metrics exist:

- **UI** — `http://<HOST_IP>:${VSS_AUTO_CALIBRATION_UI_HOST_PORT:-5000}`; open project, then **Step 7: Results** to compare AMC and VGGT metrics, camera parameters, and exports and select more accurate method. Trajectory overlay depends on AMC tracklets; a VGGT-only run can validly have no trajectory overlay.
- **Overlay image on disk** — `${VSS_APPS_DIR}/services/auto-calibration/projects/project_<id>/output/multi_view_results/BA_output/results_ba_scaled_world/overlay_img_*.png` (single-camera projects use `output/single_view_results/cam_00/verification_map_overlay.png`).
- **Project files** — `${VSS_APPS_DIR}/services/auto-calibration/projects/project_<id>/`.

## Settings File + Detector Pattern

Optional across all three modes. When the user provides a JSON settings file (typically exported from UI Step 3 Download), POST it verbatim:

```
POST /v1/config/<project_id>
Content-Type: application/json

<file contents, posted as-is>
```

The file applies its supported API configuration. UI **Step 3** owns floor-plan scale/focal-length settings; UI **Step 4** owns rectification. After a successful POST, **also** parse the file for `"detector"` / `"detector_type"` — if it's `"resnet"` or `"transformer"`, use that value for the `/calibrate` call (detector is a separate API parameter, not consumed by `/config`).

Non-2xx is surfaced — do not silently fall back. Skip this call entirely if the user chose the UI-fallback path.

## UI Fallback Pattern

When alignment / layout files aren't on disk, direct the user to the appropriate AMC UI step:

- **Settings missing** → "Open UI project `<project_id>`, go to **Step 3: Parameters**, tune via the settings dialog (or accept defaults), click Save." **Also**: before the `/calibrate` call, ask the user via `AskUserQuestion` whether to use the `resnet` or `transformer` detector — Step 3 doesn't cover detector choice.
- **Layout missing** → "Open UI project `<project_id>`, go to **Step 2: Video Configuration**, upload `layout.png` only (do NOT re-upload videos — they're already attached via API/RTSP), click Save."
- **Alignment missing** → "Open UI project `<project_id>`, go to **Step 5: Manual Alignment**, either upload `alignment_data.json` or mark correspondence points on the rectified frame/layout, click Save."

Wait for user confirmation. For alignment/layout, verify on disk before continuing:

```bash
# Project state lives under $VSS_APPS_DIR/services/auto-calibration/projects
# (the path bind-mounted into the MS container in
#  deploy/docker/services/auto-calibration/ms/compose.yml).
HOST_PROJECTS="${VSS_APPS_DIR}/services/auto-calibration/projects"

ls "$HOST_PROJECTS/project_<project_id>/manual_adjustment/"
# Expected: alignment_data.json, layout.png
```

## Success Criteria

- Every requested pipeline reaches its own terminal success state (`amc_state == "COMPLETED"`; and `vggt_state == "COMPLETED"` when VGGT was requested/run).
- For multi-camera runs, `postprocess_state == "COMPLETED"` after the post-process call following each successful calibration.
- If manual alignment was used: `${VSS_APPS_DIR}/services/auto-calibration/projects/project_<id>/manual_adjustment/` contains `alignment_data.json` + `layout.png`.
- If GT was uploaded: evaluation returns typical thresholds (`Average L2 distance(m)` < 1.5, `Average reprojection error 0(px)` < 5 for your data; < 10 for the bundled sample).
- Requested pipeline(s) have no unreported `ERROR`; failure in one independent method does not erase a successful result from the other.

## Key Output Files

Under `${VSS_APPS_DIR}/services/auto-calibration/projects/project_<project_id>/`:

```
project_<project_id>/
├── manual_adjustment/
│   ├── alignment_data.json
│   └── layout.png
├── output/
│   ├── single_view_results/cam_XX/
│   │   ├── camInfo_hyper_XX.yaml
│   │   └── trajDump_Stream_0_3d.txt
│   ├── multi_view_results/BA_output/results_ba/
│   │   ├── initial/camInfo_XX.yaml
│   │   └── refined/camInfo_XX.yaml          # ← final calibration
│   └── multi_view_results/BA_output/results_ba_scaled_world/
│       └── overlay_img_XX.png               # ← visual overlay for review
└── calibration.log
```

## Cross-cutting Troubleshooting

Mode-specific issues live in each reference's own troubleshooting table.

| Issue | Fix |
|---|---|
| `verify_project` state not `READY` | Confirm videos uploaded/ingested and alignment + layout are present (either via API or via UI manual alignment). Mode-specific upload steps in the reference. |
| Manual alignment files missing after UI step | User didn't click Save; also verify `${VSS_APPS_DIR}/services/auto-calibration/projects/project_<id>/manual_adjustment/` exists. |
| Calibration stuck `RUNNING` > 90 min | `GET /v1/amc/calibrate/<id>/log` — usually insufficient tracklets (scene too static). See "Custom Dataset" guidelines in root `README.md`. |
| Immediate `ERROR` state | Check video naming: must be `cam_00.mp4`, `cam_01.mp4`, … contiguous (videos mode) / camera_name labels (RTSP mode). |
| Low L2 but high reprojection | Provide explicit `focal_length` override during input upload (see videos / rtsp references). |
| VGGT `MODEL_MISSING` | Model is absent. Do not invoke VGGT; report this state and continue AMC, or stage the licensed model only if the user wants VGGT. |
| Upload timeout | Large videos — bump `timeout=300` to e.g. `600` in the per-mode Python script. |
| Port scan finds no backend | Backend not running — walk [`references/deploy-auto-calibration-service.md`](references/deploy-auto-calibration-service.md) first. |

## For Downstream Skills — MV3DT Export

Downstream consumers (e.g. a Multi-View 3D Tracking skill owned by another team) fetch the MV3DT-format calibration output directly from the microservice. This skill returns the `project_id`; the downstream skill calls:

```
GET /v1/result/{project_id}/mv3dt_result?result_type=amc
# Response: application/zip — mv3dt_output.zip containing transforms.yml
```

For independent VGGT output (only available if VGGT ran to `COMPLETED`, see Step F):

```
GET /v1/result/{project_id}/mv3dt_result?result_type=vggt
# Response: application/zip — vggt_mv3dt_output.zip
```

Downstream skill flow:
1. Call this skill with the user's inputs; capture the printed `project_id`.
2. Wait for skill to return; it polls each requested pipeline-specific state and required post-process pass to a terminal state.
3. `GET /v1/result/{project_id}/mv3dt_result?result_type=amc` — save the ZIP locally.
4. If independent VGGT calibration also ran, optionally fetch `?result_type=vggt` for the VGGT MV3DT result.

## Related Skills

- [`vss-manage-video-io-storage`](../../operations/vss-manage-video-io-storage/SKILL.md) — VIOS API skill; only the `rtsp` calibration mode depends on VIOS being reachable.

Root `README.md` "Custom Dataset" and "Calibration Workflow (UI)" sections document input-video guidelines and the UI-driven alternative to this API flow.
