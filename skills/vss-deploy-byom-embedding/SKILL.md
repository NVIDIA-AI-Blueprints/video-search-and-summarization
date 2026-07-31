---
name: vss-deploy-byom-embedding
description: >
  Use this skill when adding, wiring, or validating a bring-your-own-model
  implementation for the VSS RT-Embed video embedding microservice, especially
  VideoPrism on the VSS 3.3.0 code line. Covers custom model code,
  Docker Compose and Helm overrides, model repository scripts, API validation,
  and operational gotchas.
license: Apache-2.0
metadata:
  version: "3.3.0"
  author: "NVIDIA Video Search and Summarization team"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint operational byom rtvi-embed videoprism"
---

# VSS RT-Embed BYOM

Use this skill when you need to:

- Add a custom RT-Embed model implementation under `services/rtvi/rt-embed`.
- Integrate VideoPrism as a bring-your-own-model backend for video embeddings.
- Override RT-Embed model paths in Docker Compose or Helm for VSS 3.3.0.
- Validate `/v1/models`, `/v1/generate_video_embeddings`, and text endpoint behavior.

**Trigger phrases:** `vss-deploy-byom-embedding`, `RT-Embed BYOM`, `VideoPrism embed`,
`VideoPrism RT-Embed`, `custom embed model`, `MODEL_IMPLEMENTATION_PATH`,
`MODEL_REPOSITORY_SCRIPT_PATH`, `bring your own embedding model`.

## When Not To Use

Do not use this skill for a standard RT-Embed deployment with the default
Cosmos-Embed1 backend. Use
[`vss-deploy-video-embedding`](../vss-deploy-video-embedding/SKILL.md) for
normal image pulls, Compose/Helm startup, health checks, and API smoke tests
when no custom model wrapper or BYOM path is being added.

## Scope

RT-Embed BYOM uses the existing `custom` model loader. The deployment chooses the
implementation and model artifact with environment variables; code changes are
only needed when the custom model wrapper does not already exist in the image or
mounted source tree.

For the full implementation checklist, read
[`references/videoprism-byom.md`](references/videoprism-byom.md).

## Model Contract

Any RT-Embed BYOM wrapper must:

- Provide an `inference.py` module containing one `BaseVlmModel` subclass.
- Accept `model_path` and initialize all model state in `_initialize_model`.
- Return a stable `model_name` from `model_name`.
- Implement `generate(...)` and return `VlmModelOutput` entries with embedding vectors.
- Keep video and text embedding dimensions consistent if both endpoints are advertised.
- Clearly reject or document text embedding requests if the BYOM backend is video-only.

Use the Cosmos-Embed1 sample as the local pattern:

```bash
services/rtvi/rt-embed/src/models/custom/samples/cosmos-embed1/
```

## Standard File Layout

Create the VideoPrism BYOM wrapper in the same sample tree:

```bash
services/rtvi/rt-embed/src/models/custom/samples/videoprism/
├── inference.py
├── create_triton_model_repo.py        # only if using Triton/ONNX/TensorRT
└── triton_model_repo/                 # only if using Triton
```

If the first implementation is PyTorch-only, omit the Triton repo script and set
`DISABLE_OPTIMIZATION=true` during validation. Add the optimized path only after
the PyTorch path is correct.

## Deployment Overrides

For Docker Compose on RT-Embed 3.3.0:

```bash
export RTVI_EMBED_IMAGE=nvcr.io/nvidia/vss-core/vss-rt-embed
# Pin a published RT-Embed image tag for the VSS 3.3.0 code line.
export RTVI_EMBED_TAG="${RTVI_EMBED_TAG:-3.3.0}"
export MODEL_PATH="git:https://huggingface.co/<org>/<videoprism-checkpoint>"
export MODEL_IMPLEMENTATION_PATH="/opt/nvidia/rtvi/rtvi/models/custom/samples/videoprism"
export MODEL_REPOSITORY_SCRIPT_PATH="/opt/nvidia/rtvi/rtvi/models/custom/samples/videoprism/create_triton_model_repo.py"
```

For Helm, set the equivalent values in the `rtvi-embed` chart:

```yaml
modelPath: "git:https://huggingface.co/<org>/<videoprism-checkpoint>"
modelImplementationPath: "/opt/nvidia/rtvi/rtvi/models/custom/samples/videoprism"
modelRepositoryScriptPath: "/opt/nvidia/rtvi/rtvi/models/custom/samples/videoprism/create_triton_model_repo.py"
```

If the implementation is source-mounted instead of baked into the image, mount
the repo or package directory into the container so those in-container paths
exist before `start_rtvi_embed.sh` runs.

## Validation

After deployment:

```bash
BASE_URL="http://localhost:${RTVI_EMBED_PORT:-8017}"
curl -fsS "$BASE_URL/v1/ready?detailed=true"
curl -fsS "$BASE_URL/v1/models"
```

Then validate one small video or image input:

```bash
FILE_ID=$(curl -fsS -X POST "$BASE_URL/v1/files" \
  -F purpose=vision \
  -F media_type=video \
  -F file=@/path/to/smoke.mp4 | jq -r .id)

MODEL_ID=$(curl -fsS "$BASE_URL/v1/models" | jq -r '.data[0].id')
curl -fsS -X POST "$BASE_URL/v1/generate_video_embeddings" \
  -H "Content-Type: application/json" \
  -d "{\"id\":\"$FILE_ID\",\"model\":\"$MODEL_ID\",\"chunk_duration\":5}"
```

Confirm the returned embedding dimension and model id match the VideoPrism
wrapper. For text search workflows, also test `/v1/generate_text_embeddings`;
otherwise confirm it fails with a clear 4xx message instead of a server error.

## Common Pitfalls

- `MODEL_IMPLEMENTATION_PATH` must point to the directory containing `inference.py`,
  not the parent `samples/` directory.
- `MODEL_REPOSITORY_SCRIPT_PATH` must be unset or valid. A stale Cosmos path will
  build the wrong Triton repository.
- VideoPrism may be video-only. Do not claim text-to-video search support unless
  the text encoder maps into the same embedding space.
- Keep output vectors JSON-serializable; convert tensors to CPU lists.
- Validate with warm caches and cold caches. First boot failures are often model
  download or path issues, not inference issues.

## References

| File | When to read |
|---|---|
| [`references/videoprism-byom.md`](references/videoprism-byom.md) | Step-by-step VideoPrism BYOM implementation and validation checklist. |
| [`../vss-deploy-video-embedding/SKILL.md`](../vss-deploy-video-embedding/SKILL.md) | Standard RT-Embed deployment, API usage, and troubleshooting. |
