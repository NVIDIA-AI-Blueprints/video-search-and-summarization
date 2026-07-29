# AGENTS.md

## Scope

Applies to `vss-deploy-detection-tracking-3d`, the MV3DT and multi-camera 3D
tracking skill.

## Rules

- Check calibration requirements first. Chain to `vss-generate-video-calibration`
  when calibration data is missing and the task authorizes setup.
- Do not use the shipped warehouse synthetic sample slug for custom-data evals.
- Preserve camera ordering, timestamps, calibration files, and layout paths.
- Verify both AMC/calibration readiness and MV3DT runtime health before
  reporting success.
- Avoid hardcoded hardware or model variants; use the references for platform
  and resource decisions.

## Eval Behavior

- Multi-step specs preserve state between cases. Do not reset Docker or project
  state between chained steps unless the spec asks for teardown.
- Report calibration artifacts, deployment state, and tracking evidence.
