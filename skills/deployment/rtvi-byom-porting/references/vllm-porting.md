# vLLM Porting Reference

## VSS Integration Order

1. Configure the existing backend with `VLM_MODEL_TO_USE=vllm-compatible` and
   `MODEL_PATH`.
2. Add a narrow adapter under `services/rtvi/rt-vlm/` for config, processor,
   request or response normalization.
3. Add a discoverable vLLM plugin or shim for model registration and weight
   mapping.
4. Patch vLLM only for behavior unavailable through those boundaries.

The VSS profile maps the corresponding values through
`RTVI_VLM_MODEL_TO_USE`, `RTVI_VLM_MODEL_PATH`, and
`RTVI_VLM_MODEL_IMPLEMENTATION_PATH` in
`deploy/docker/services/rtvi/rtvi-vlm/rtvi-vlm-docker-compose.yml`.

## Plugin And Shim Checklist

- Register the architecture name declared by `config.json`.
- Keep weight mapping deterministic and report kept, renamed, skipped and
  unexpected tensors.
- Do not synthesize critical weights such as `lm_head` unless the model contract
  explicitly ties them.
- Ensure worker subprocesses can import the plugin without modifying global
  site-packages.
- Keep tensor parallelism, maximum model length, quantization and memory
  utilization configurable.
- Preserve non-target models with focused regression coverage.

For Cosmos3 Diffusers checkpoints, verify flat `lm_head.*` weights map to
`language_model.lm_head.*`, visual weights map below `visual.*`, and
generation-only branches are deliberately skipped. Investigate unexpected or
missing tensors before accepting the load.

## Eager And Patch Policy

Default to graph-capable execution. If eager mode is required, retain the exact
non-eager failure and measure the performance difference when practical.

A vLLM patch must be version- and architecture-gated, idempotent, optional when
practical, and covered by a smoke test that fails without it.
