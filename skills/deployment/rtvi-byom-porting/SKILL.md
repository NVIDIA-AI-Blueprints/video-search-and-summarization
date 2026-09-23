---
name: rtvi-byom-porting
description: Use when adding, debugging, or validating a bring-your-own VLM in VSS RT-VLM, including custom Hugging Face or NGC checkpoints, vLLM adapters or plugins, model shims, and model-specific runtime dependencies. Not for selecting an already-supported model or ordinary RT-VLM deployment.
license: Apache-2.0
metadata:
  version: "3.3.0"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint rtvi vlm byom vllm model-porting"
---

# RTVI BYOM Porting

## Purpose

Port a VLM into the VSS RT-VLM service without hard-coding one checkpoint or
GPU platform. Prefer configuration, then an RTVI adapter or vLLM plugin, and
patch vLLM only when the supported extension points cannot express the model.

Use `vss-deploy-dense-captioning` for ordinary deployment or an already-supported
model. Use this skill when model architecture, processor, weight mapping, runtime
dependencies, or request adaptation requires repository work.

## Model Contract

Before changing code, record:

- immutable model source and revision, license and access requirements;
- architecture, processor, tokenizer, quantization and context length;
- vision-token and frame-sampling assumptions;
- CUDA architecture, memory, vLLM/runtime and custom-kernel requirements.

For a private Hugging Face source, use `HF_TOKEN` only during authenticated
download/cache population. Never print it, embed it in `MODEL_PATH`, or bake it
into an image. Prove the cached model starts after the temporary credential is
removed. If remote model code is required, review it first and pair
`VLM_TRUST_REMOTE_CODE=true` with an exact allowlist entry. For the VSS profile,
set `RTVI_VLM_MODEL_PATH_ALLOWLIST`; inside the service container this becomes
`RTVI_MODEL_PATH_ALLOWLIST`. Likewise, the profile input
`RTVI_VLM_ALLOW_UNSAFE_MODEL_CONFIG` becomes
`RTVI_ALLOW_UNSAFE_MODEL_CONFIG`; do not enable it without reviewing the
blocked config hooks.

## Integration Decision

Stop at the first path that works:

1. **Configuration only:** use `VLM_MODEL_TO_USE=vllm-compatible` and
   `MODEL_PATH=<ngc: or mounted path>`. A `git:` source is acceptable only for
   exploration because the develop downloader does not pin Hugging Face
   revisions; use a revision-pinned mounted snapshot for reproducible evidence.
2. **RTVI adapter:** normalize config, processor, request or response behavior
   inside `services/rtvi/rt-vlm/` while preserving existing model behavior.
3. **Plugin or shim:** register architecture and deterministic weight mappings
   without editing vendored vLLM internals.
4. **Version-gated patch:** patch vLLM only after recording why the first three
   paths cannot work. Keep it optional and add a focused regression test.

Use `VLM_MODEL_TO_USE=custom` with `MODEL_IMPLEMENTATION_PATH` only for a custom
RTVI model implementation. In the VSS Compose profile these are exposed as
`RTVI_VLM_MODEL_TO_USE`, `RTVI_VLM_MODEL_PATH`, and
`RTVI_VLM_MODEL_IMPLEMENTATION_PATH`.

For source, adapter, plugin, or custom-backend changes, build the RT-VLM image
from `services/rtvi/rt-vlm/`, test it with standalone Compose via `RTVI_IMAGE`,
then select the same repository and tag in the VSS profile via
`VSS_RT_VLM_IMAGE` and `VSS_RT_VLM_TAG`. Record the tested image digest. A host
`MODEL_IMPLEMENTATION_PATH` alone is insufficient: the implementation must
exist at that path inside the selected image or an explicit Compose bind mount.

Read [references/vllm-porting.md](references/vllm-porting.md) before changing
model loading, registration, weight mapping, kernels, cache behavior or vLLM.

## Hard Gates

- Do not enable eager mode by default. If it is unavoidable, capture the exact
  blocker, performance impact and removal condition.
- Do not bind generic model logic to H100, B200, RTX, Orin, x86 or Jetson.
  Capability-detect or use existing configuration boundaries.
- Keep secrets out of commands, logs, images, reports and committed files.
- Do not accept a successful load as proof of a successful port. Output must be
  legible and grounded for every claimed modality.

## Validation

Run the smallest relevant checks in this order:

1. Static checks and focused tests for changed Python, shell and configuration.
2. Start RT-VLM through the canonical VSS Compose/profile path and verify
   `/v1/health/ready` and `/v1/models`. First use standalone Compose when a
   revision-pinned host model snapshot must be mounted; the VSS profile does not
   expose `MODEL_ROOT_DIR` on develop.
3. Smoke-test text, image and video inputs for every claimed modality. Set the
   explicit media type when testing images rather than relying on video routing.
4. Read [references/quality-gates.md](references/quality-gates.md), retain raw
   redacted responses, and reject gibberish, repetition or ungrounded claims.
5. Run relevant caption, event-detection or summarization accuracy checks.
6. Record latency, throughput, GPU memory/utilization, eager state and custom
   kernel state.

Use `scripts/byom_port_report.py` to turn observed JSON facts into a compact
Markdown report. The helper records evidence; it does not manufacture PASS
results.

## Completion Contract

Report the exact model revision and backend, integration path, eager-mode state,
platform gating, smoke/accuracy/performance evidence, remaining blockers and the
next bounded validation step.
