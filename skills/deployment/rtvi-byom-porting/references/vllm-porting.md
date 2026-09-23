# vLLM Porting Reference

## VSS Integration Order

1. Configure the existing backend with `VLM_MODEL_TO_USE=vllm-compatible` and
   `MODEL_PATH`.
2. Add a narrow adapter under `services/rtvi/rt-vlm/` for config, processor,
   request or response normalization.
3. Add a discoverable vLLM plugin or shim for model registration and weight
   mapping.
4. Patch vLLM only for behavior unavailable through those boundaries.

## Reproducible Private Hugging Face Baseline

The develop downloader does not pass a Hugging Face revision and its cache key
uses only the repository basename. Do not use `MODEL_PATH=git:...` as immutable
evidence. Stage an exact snapshot outside the service, then mount it with the
standalone Compose path:

```bash
read -rsp "Hugging Face token: " HF_TOKEN && export HF_TOKEN
hf download ORG/MODEL --revision COMMIT_SHA --local-dir /absolute/model/snapshot
unset HF_TOKEN

cd services/rtvi/rt-vlm/docker
BACKEND_PORT=8000 \
MODEL_ROOT_DIR=/absolute/model/snapshot \
MODEL_PATH=/absolute/model/snapshot \
VLM_MODEL_TO_USE=vllm-compatible \
VLLM_ENFORCE_EAGER=false \
docker compose up -d
```

If reviewed remote code is required, set `VLM_TRUST_REMOTE_CODE=true` and set
`RTVI_MODEL_PATH_ALLOWLIST` to the exact mounted path in standalone Compose. In
the full VSS profile, use `RTVI_VLM_MODEL_PATH_ALLOWLIST`; use
`RTVI_VLM_ALLOW_UNSAFE_MODEL_CONFIG` only after reviewing blocked config hooks.
The full profile does not mount `MODEL_ROOT_DIR` on develop, so promote the
tested snapshot and any plugin/custom implementation into a pinned derived
image, or add an explicit reviewed bind-mount override.

For any source, adapter, plugin, or custom-backend change, build and test the
actual runtime image:

```bash
cd services/rtvi/rt-vlm
docker build -f docker/Dockerfile -t vss-rt-vlm:byom .
cd docker
RTVI_IMAGE=vss-rt-vlm:byom docker compose up -d
docker image inspect vss-rt-vlm:byom --format '{{.Id}}'
```

After standalone validation, select that same image in the VSS profile with
`VSS_RT_VLM_IMAGE=vss-rt-vlm` and `VSS_RT_VLM_TAG=byom`. Verify the
implementation/plugin imports inside the running container before accepting
readiness.

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
