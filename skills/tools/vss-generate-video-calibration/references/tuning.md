# AMC Custom Dataset Tuning

Use this workflow when the user explicitly asks to tune or optimize AMC settings, does not know which configuration fits a custom dataset, or accepts the tuning offer after a normal calibration. This workflow owns tuning project names, repeated attempts, artifact preservation, and result selection. Do not run `references/calibration-tail.md` for an explicit tuning request.

Use the applicable input-mode reference only for source-specific discovery, ordering, capture, and upload semantics:

- Local videos: `references/videos.md`
- RTSP streams: `references/rtsp.md`; capture and ingest once, then tune the same frozen clips
- Bundled sample: `references/sample-dataset.md`

Do not use numeric tuning to repair missing or invalid inputs, camera ordering, unsynchronized clips, bad alignment, unresolved layout scale, incomplete rectification, or service, GPU, disk, and permission failures.

## Enter Tuning

For an explicit request such as "tune configs", "tune calibration", "optimize settings", or "find the best configuration":

- Treat the request as approval for up to five sequential AMC configuration attempts.
- Do not ask the user to choose `resnet`, `transformer`, or whether to include VGGT.
- Run both supported AMC detector baselines by default.
- For eligible multi-camera projects, run VGGT in every attempt project. VGGT does not consume an AMC attempt.
- State the bounded plan and expected runtime as a progress update without pausing for confirmation.

Without GT, the two detector baselines are not normally enough to finish tuning. Run at least one safe, evidence-driven follow-up after them when the logs and artifacts support a documented change. Continue through attempt 5 while safe, meaningful changes remain.

Stop early only when:

- a GT-backed target is met;
- an input or infrastructure problem prevents valid comparison; or
- no safe evidence-driven change remains.

Never submit a sixth AMC attempt without asking the user.

## Prepare and Freeze Inputs

1. Run the shared platform preflight and probe the configured `/v1/ready` endpoint and UI root. Reuse a healthy stack. If the stack is unavailable, follow `references/deploy-auto-calibration-service.md` before discussing calibration inputs.
2. For multi-camera tuning, require VGGT support in the running service. Explicit tuning counts as a request for VGGT when following the deployment reference. Resolve `MODEL_MISSING` through its safe VGGT setup flow rather than silently switching to AMC-only tuning. VGGT is not available for single-camera input; record that and continue with AMC.
3. Discover the running schema at `/openapi.yaml`, then `/openapi.json`; use `/docs` or service logs only if neither schema endpoint works.
4. Fetch `GET /v1/config/defaults` and retain the complete runtime `config_params` object as the attempt-1 baseline.
5. Resolve and freeze:
   - sorted, synchronized videos and camera order;
   - `layout.png` and exact `layout_px_per_m`;
   - alignment data and its coordinate space;
   - focal lengths, when provided;
   - optional `GT.zip`;
   - already-linear staging or completed rectification.

A settings file is optional and is not required for tuning. Start from the running service defaults; do not auto-discover or inherit a nearby settings file.

If `layout_px_per_m` is unknown, create attempt 1, upload its videos and layout, and direct the user to UI Step 3. They may enter an exact value or calculate it from two layout points and a known real-world distance, then save it. Read back and freeze the exact saved value. Do not infer it from another dataset or image dimensions.

If alignment is missing, finish media preparation, then direct the user to UI Step 5 to create and save it against the staged rectified frames. Freeze and reuse the saved alignment. External alignment made on original videos uses `coord_space=original`; UI alignment made on rectified frames uses `coord_space=rectified`.

Use the same videos, layout, scale, alignment, camera order, focal lengths, GT, and media preparation for every attempt. Changing any of them silently invalidates the comparison.

## Reuse a Normal Project

When tuning begins after a normal calibration, reuse that project as attempt 1 only when its frozen inputs, exact effective configuration, and detector can be recovered and verified.

- Do not rename or duplicate the server project.
- Record its actual name and ID plus its canonical attempt label in the report.
- Recover the configuration from the project's persisted calibration output, such as `project_<id>/output/config_AutoMagicCalib/`, or a per-project readback endpoint exposed by the running schema. Recover the detector from project metadata or `calibration.log`. Do not use the service-wide `GET /v1/config` alone as evidence of what an earlier project used.
- Normalize and compare every AMC-tunable field with the current runtime defaults plus the same frozen `layout_px_per_m` and explicit dataset-required overrides. Reuse the project only when they are equivalent; apply that exact recovered configuration unchanged for the alternate-detector baseline.
- Preserve its completed AMC artifacts before launching another workload.
- If VGGT already completed, preserve and reuse that result.
- If VGGT is `READY`, preserve AMC first, then run VGGT and post-processing in the same project and preserve VGGT separately.
- Count the completed normal AMC baseline as attempt 1; do not rerun it only to enter tuning.

If the effective configuration or detector cannot be recovered exactly, differs from the intended baseline, or the project otherwise cannot support a valid comparison, retain its result only as an external alternative and create a fresh attempt 1 from runtime defaults.

## Project Names and Output Labels

Derive one stable `dataset_slug` before creating any tuning project: lowercase the dataset name, replace non-alphanumeric runs with underscores, trim separators, and retain readable words.

Use this canonical label for every new server project, output directory, report row, and JSON-summary entry:

```text
<dataset_slug>_attempt_<NN>_<type>
```

Examples:

```text
nv_warehouse_attempt_01_resnet
nv_warehouse_attempt_02_transformer
nv_warehouse_attempt_03_resnet_sf5
```

Use two-digit attempt numbers. Keep the detector and relevant configuration delta readable. Do not switch to opaque abbreviations.

Determine the planned suffixes first, then resolve project-name `maxLength` from the running create-project schema. If it is absent, use a conservative limit of 32 characters. Reserve every attempt/type suffix in full and deterministically shorten only the dataset slug until all names fit. Reuse that exact slug throughout tuning and record the original-name-to-slug mapping.

If project creation returns HTTP 422:

1. Report the validation response body.
2. Revalidate the same logical name against the schema.
3. Retry once with a schema-compliant name while keeping the logical dataset slug stable.
4. If the response proves the service has a smaller limit, rebuild only the not-yet-submitted names with one deterministic compact slug and update the recorded mapping.

A project-creation, upload, configuration, or verification error before an accepted `POST /v1/calibrate/<project_id>` does not consume an AMC attempt.

Write tuning output outside the generated server project tree:

```text
${VSS_DATA_DIR}/auto-calib/tuning/<dataset_slug>/<canonical-label>/
```

For a reused normal project, keep the actual project name unchanged and use the canonical label only for output and reporting.

## Default Attempt Plan

Use this order unless the running schema exposes different supported detectors or the user explicitly narrows the experiment:

1. AMC attempt 1: `resnet` with runtime defaults plus the frozen dataset inputs.
2. AMC attempt 2: `transformer` with identical settings and inputs.
3. AMC attempt 3: one small change or tightly coupled group justified by baseline logs and artifacts.
4. Attempts 4 and 5: continue only while safe evidence supports another documented change.

In every eligible multi-camera attempt project, run and preserve VGGT before submitting AMC. VGGT uses the same project ID but is reported separately and consumes zero AMC attempts.

Maintain an AMC incumbent after every completed attempt. A failed or degraded later experiment cannot replace it. Preserve the best completed baseline as an alternative even when a tuned result wins.

Do not copy a successful configuration from another dataset. Detector and parameter decisions must come from the current dataset's runtime contract, logs, metrics, and artifacts.

## Run One Fresh Attempt

Use the API paths exposed by the running schema. `<MS_URL>` below includes the `/v1` prefix. For each new tuning attempt:

1. Create the canonical project and upload the frozen assets using the applicable input-mode reference.
2. For confirmed-linear media, call `POST <MS_URL>/linear_media/<project_id>`. Otherwise complete Rectification through the UI or running rectification API. Poll project/rectification state and require `rectification_state == COMPLETED`.
3. Build the complete attempt configuration from runtime defaults, the frozen `layout_px_per_m`, explicit dataset-required overrides, and only this attempt's intentional delta. Save that exact JSON as `config.json`.
4. Apply it with `POST <MS_URL>/config/<project_id>`. Require a successful response, immediately call `GET <MS_URL>/config`, and compare its normalized `config_params` with every submitted field. Stop without consuming an attempt if any value differs or the running schema has no reliable readback.
5. Call `POST <MS_URL>/verify_project/<project_id>`, then `GET <MS_URL>/get_project_info/<project_id>` and require the project and result-specific states to be ready.
6. For eligible multi-camera input, call `POST <MS_URL>/vggt/calibrate/<project_id>` and poll `GET <MS_URL>/get_project_info/<project_id>` until `vggt_state` reaches a terminal state. Fetch the VGGT calibration log while running or on failure.
7. When VGGT completes, call `POST <MS_URL>/postprocess/<project_id>` and poll project info until `postprocess_state == COMPLETED`.
8. Immediately preserve the complete VGGT result under `artifacts/vggt/` using the artifact mappings below.
9. Reapply the same complete attempt configuration and repeat the `GET <MS_URL>/config` equality check immediately before AMC.
10. Call `POST <MS_URL>/calibrate/<project_id>` with `{"detector_type":"<detector>"}`. Increment the AMC-attempt count only after this request is accepted.
11. Poll `GET <MS_URL>/get_project_info/<project_id>` until `amc_state` reaches a terminal state. Fetch `GET <MS_URL>/amc/calibrate/<project_id>/log` while running and on failure.
12. When AMC completes, call post-processing again because AMC resets the shared `postprocess_state`; require it to reach `COMPLETED`.
13. Preserve the complete AMC result under `artifacts/amc/` without overwriting VGGT.
14. Compare the result with the incumbent before planning another attempt.

If VGGT fails, preserve its logs, terminal state, and failure reason. Continue to AMC only when the failure does not indicate a shared input or infrastructure problem.

Never run VGGT, AMC, rectification, post-processing, or separate attempts concurrently. Wait for each result-specific calibration and post-processing state to become terminal before starting the next workload.

Create the attempt directory before AMC submission and preserve available results before starting another workload:

```text
<canonical-label>/
  config.json
  project_id.txt
  artifacts/
    vggt/
      calibration.log
      postprocess.log
      project_state.json
      calibration.json
      evaluation_metrics.json
      evaluation_statistics.json
      refined-camera-yaml/
      scaled-world-output/
      overlays/
      mv3dt-camera-map/
    amc/
      calibration.log
      postprocess.log
      project_state.json
      calibration.json
      evaluation_metrics.json
      evaluation_statistics.json
      tracklet-overlays/
      virtual-gt-overlays/
      refined-camera-yaml/
      scaled-world-output/
      mv3dt-camera-map/
```

Preserve only artifacts that exist and record missing artifacts explicitly. For a failed run, keep the calibration log, partial multi-view log, generated camera-pair overlays, exact terminal state, and failure reason.

Use these result-specific mappings when the running schema exposes them, and save their response bodies with clear filenames:

| Result | API or project source |
|---|---|
| State | `GET <MS_URL>/get_project_info/<project_id>` |
| AMC log | `GET <MS_URL>/amc/calibrate/<project_id>/log` |
| VGGT log | `GET <MS_URL>/vggt/calibrate/<project_id>/log` |
| AMC evaluation | `GET <MS_URL>/result/<project_id>/evaluation_metrics` and `/evaluation_statistics` |
| VGGT evaluation | `GET <MS_URL>/vggt_results/<project_id>/evaluation_metrics` and `/evaluation_statistics` |
| AMC overlay | `GET <MS_URL>/result/<project_id>/overlay_image` |
| VGGT overlay | `GET <MS_URL>/vggt_results/<project_id>/overlay_image` when available |
| MV3DT export | `GET <MS_URL>/result/<project_id>/mv3dt_result?result_type=amc` or `result_type=vggt` |
| Complete snapshot | Copy the available result-specific files from `${VSS_APPS_DIR}/services/auto-calibration/projects/project_<id>/output/` before another calibration or post-process can replace shared outputs. |

Also preserve camera-parameter and full-export responses exposed by the running OpenAPI schema. Treat a documented endpoint returning not found as a missing artifact, not permission to invent a replacement path.

AMC trajectory overlays and VGGT calibration visualizations are separate result types. VGGT is not automatically composited onto AMC trajectory overlays, so seeing only AMC trajectories does not mean VGGT failed. Evaluate each result through its own state, post-processing output, camera parameters, metrics, overlays, and exports.

## Diagnose and Adjust

Read `references/calibration-parameters.md` before choosing a numeric change.

1. Identify the failing stage and weakest camera pair from logs and artifacts.
2. Select only a documented parameter whose stage matches that evidence.
3. Prefer an explicit rejected boundary reported by the current run. Otherwise use the parameter reference's midpoint-to-policy-bound rule.
4. Change one parameter or one tightly coupled group and record the hypothesis, old and new values, expected effect, and risk.
5. Compare pair coverage, filtered outliers, reprojection distribution, bundle-adjustment cost, artifacts, and runtime against the incumbent.

Prefer the alternate detector before broadly loosening matching thresholds. Do not use a later-stage threshold to compensate for an earlier filtering or input failure. Stop when no supported change remains inside the documented guardrails.

Never tune `layout_px_per_m`, camera order, synchronization, source files, alignment points, layout orientation, or VGGT controls in response to AMC logs. Treat rectification changes as a separate experiment requiring a fresh project and revalidated alignment.

## Select and Report

For every completed AMC and VGGT result, record what is available:

- pair-by-pair match counts and filtered-outlier counts;
- reprojection mean, standard deviation, and maximum for each camera;
- bundle-adjustment final costs;
- calibration, post-process, and export states;
- runtime, warnings, failures, and missing artifacts.

Select the AMC incumbent using complete evidence, in this order:

1. Completed calibration and post-processing.
2. GT-backed metrics when GT exists.
3. Camera-pair coverage, outliers, L2 metrics, and reprojection distribution.
4. Bundle-adjustment cost, valid artifacts, and warnings.
5. Visual review of overlays, projections, and layout placement.

Do not select on mean reprojection error alone. Disclose when mean error improves but maximum error, variance, or the weakest camera pair worsens. Without GT, label the recommendation heuristic and require visual review in the AMC UI.

Save a concise Markdown report and machine-readable JSON summary containing:

- frozen inputs and runtime defaults;
- original dataset name and stable slug mapping;
- AMC attempt count and per-project VGGT status;
- canonical label, actual project name, and project ID;
- detector, configuration delta, terminal states, metrics, runtime, and artifacts;
- incumbent selection, trade-offs, and preserved baseline alternative;
- recommended configuration JSON and manual next action when no result is acceptable.

Do not fabricate unavailable metrics or overwrite a user's existing configuration. Ask before using the recommendation for a separate production calibration.
