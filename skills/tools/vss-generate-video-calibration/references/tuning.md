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
2. For multi-camera tuning, require VGGT support in the running service. Resolve `MODEL_MISSING` through the deployment reference rather than silently switching to AMC-only tuning. VGGT is not available for single-camera input; record that and continue with AMC.
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

When tuning begins after a normal calibration, reuse that project as attempt 1 when its frozen inputs and baseline configuration are valid.

- Do not rename or duplicate the server project.
- Record its actual name and ID plus its canonical attempt label in the report.
- Preserve its completed AMC artifacts before launching another workload.
- If VGGT already completed, preserve and reuse that result.
- If VGGT is `READY`, preserve AMC first, then run VGGT and post-processing in the same project and preserve VGGT separately.
- Count the completed normal AMC baseline as attempt 1; do not rerun it only to enter tuning.

If the normal project cannot support a valid comparison, report the reason and create a fresh attempt 1.

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

For each new tuning attempt:

1. Create the canonical project and reproduce the frozen inputs and media preparation.
2. Require `rectification_state == COMPLETED` and verify the project reaches `READY`.
3. For eligible multi-camera input, require `vggt_state == READY`, start VGGT, and poll `vggt_state` independently to a terminal state.
4. When VGGT completes, run post-processing and require `postprocess_state == COMPLETED`.
5. Immediately preserve the complete VGGT result under `artifacts/vggt/`.
6. Submit AMC with the attempt detector and configuration. Increment the AMC-attempt count only after the request is accepted.
7. Poll `amc_state` independently to a terminal state.
8. When AMC completes, run post-processing again because AMC resets the shared `postprocess_state`.
9. Preserve the complete AMC result under `artifacts/amc/` without overwriting VGGT.
10. Compare the result with the incumbent before planning another attempt.

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
