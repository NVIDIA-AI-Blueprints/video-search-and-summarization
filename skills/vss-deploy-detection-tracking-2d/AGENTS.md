# AGENTS.md

## Scope

Applies to `vss-deploy-detection-tracking-2d`, the RT-CV 2D detection and
tracking skill.

## Rules

- Use the use-case references before choosing a model, output sink, batch size,
  or config file.
- Do not write explicit `model-engine-file` paths for shipped warehouse/smart
  city configs; use the documented engine cache workflow.
- Choose output sink from user intent: display, save-to-file, or benchmark.
  Ask when ambiguous outside non-interactive evals.
- Keep NGC resource download, config edits, launch, verification, and teardown
  steps in the documented order.

## Eval Behavior

- In CI, use the spec-provided platform and pre-authorization.
- Return evidence from deployment logs, API metrics, or generated output rather
  than only container status.
