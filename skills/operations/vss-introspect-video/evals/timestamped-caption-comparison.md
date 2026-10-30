# Timestamped-caption comparison plan

Do not launch this comparison without approval. It does not change scoring,
budgets, models, or dataset contents. It contains no questions, answer options,
or expected labels.

Enable the experiment with `VLM_OUTPUT_MODE=timestamped-caption` in the
environment the guarded sensor helper runs in (the evaluation's run
environment). The helper reads the variable itself and it overrides any
`--output-mode` flag, so every inspection in the run uses that mode. Leave the
variable unset, or set `VLM_OUTPUT_MODE=legacy`, for the control. Both arms use the same budgets, FPS limits, models, and ledger
rules.

## Case IDs

Correct-case regression checks:

- `nv_warehouse_240119_GoPro_2_GH013939-g1-1`
- `nv_warehouse_240119_GoPro_1_GH023938-g1-4`
- `nv_warehouse_240119_GoPro_1_GH043938-g1-4`
- `nv_warehouse_240119_GoPro_1_GH063938_1-g1-2`
- `vss-sample-warehouse-ladder-combined-g1-3`

Often-wrong checks:

- `nv_warehouse_240119_GoPro_2_GH053938-g1-2`
- `nv_warehouse_240119_GoPro_1_GH023938-g1-3`
- `nv_warehouse_240119_GoPro_2_GH013939-g1-3`
- `nv_warehouse_240119_GoPro_1_GH053938-g1-3`
- `nv_warehouse_240119_GoPro_2_GH013938-g1-4`

## Fixed-call replay

Hold the visual input, submitted window, FPS, model, and agent inspection focus
constant. Vary only the output instructions: legacy sends the focus unchanged, and
timestamped-caption appends the caption reporting instructions for that window's
actual duration. This replay does not measure a new agent window or a new focus.

## Live end-to-end comparison

Hold configuration and inspection budgets constant across the two arms. Record
any agent-selected window or inspection-focus difference beside the answer.
Those differences are not part of the fixed-call contract comparison.

## Metrics to record

- Answer accuracy and any correct-case regressions.
- Event extraction errors and identity errors.
- Timestamp validity, plus temporal accuracy from manual review.
- Missing events and unsupported observations.
- Format failures, quality repairs, VLM calls, latency, and budget exhaustion.

Synthetic helper tests do not establish that the model is more accurate.
