# AGENTS.md

## Scope

Applies to `vss-generate-video-calibration`, the AutoMagicCalib and calibration
workflow skill.

## Rules

- Use this skill for camera calibration data generation, not for deploying the
  final MV3DT runtime unless chained by the 3D tracking skill.
- Preserve camera file names, ordering, alignment data, layout images, and user
  dataset slugs.
- Verify disk, NGC credentials, and HF token requirements before running VGGT or
  AMC-heavy paths.
- Do not delete calibration project state unless the user or eval spec
  explicitly authorizes teardown.

## Eval Behavior

- Return artifact paths, calibration status, and the next deployment step.
- Keep custom-data setup distinct from the shipped sample dataset.
