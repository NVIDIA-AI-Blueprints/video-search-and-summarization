# VideoPrism BYOM For RT-Embed

This reference guides an agent through integrating a VideoPrism-style embedding
backend with the VSS RT-Embed 3.3.0 / 26.07.3 code path.

## 1. Start From The Existing Custom Model Pattern

Use the Cosmos-Embed1 sample as the contract reference:

```bash
services/rtvi/rt-embed/src/models/custom/samples/cosmos-embed1/inference.py
services/rtvi/rt-embed/src/models/custom/samples/cosmos-embed1/create_triton_model_repo.py
```

The RT-Embed runtime loads custom models through:

```bash
MODEL_PATH
MODEL_IMPLEMENTATION_PATH
MODEL_REPOSITORY_SCRIPT_PATH
```

The implementation path must contain `inference.py`. The dynamic loader imports
that module and instantiates the `BaseVlmModel` subclass.

## 2. Add The VideoPrism Model Directory

Create:

```bash
services/rtvi/rt-embed/src/models/custom/samples/videoprism/
```

Minimum first version:

```bash
inference.py
```

Optimized version:

```bash
inference.py
create_triton_model_repo.py
triton_model_repo/video_embeddings/config.pbtxt
triton_model_repo/text_embeddings/config.pbtxt   # only if VideoPrism has text encoder support
```

## 3. Implement `inference.py`

Mirror the shape of the Cosmos sample:

- Import `BaseVlmModel`, `VlmGenerationConfig`, `VlmModelOutput`, and `ChunkInfo`.
- Load the VideoPrism processor/model in `_initialize_model`.
- Implement `model_name`.
- Implement `can_batch`.
- Implement `generate(...)`.
- Return embeddings as Python lists, not CUDA tensors.

Skeleton:

```python
import os
from typing import List, Optional

import torch

from common.chunk_info import ChunkInfo
from common.logger import logger
from models.base_vlm_model import BaseVlmModel, VlmGenerationConfig, VlmModelOutput


class VideoPrismEmbedModel(BaseVlmModel):
    def _initialize_model(self, **kwargs):
        self._model_name = os.getenv("VIDEOPRISM_MODEL_NAME", "videoprism")
        self._checkpoint = self.model_path
        logger.info("Initializing VideoPrism model from %s", self._checkpoint)
        # Load processor/model here.

    def _shutdown_model(self):
        # Release model references, Triton server handles, and CUDA memory here.
        pass

    @property
    def model_name(self) -> str:
        return self._model_name

    def can_batch(self, item1, item2):
        return True

    def generate(
        self,
        query: str,
        chunks: List[ChunkInfo],
        video_frames: Optional[List[torch.Tensor]] = None,
        video_frames_times: Optional[List[List[float]]] = None,
        generation_config: Optional[VlmGenerationConfig] = None,
        **kwargs,
    ) -> List[VlmModelOutput]:
        outputs = []
        for idx, chunk in enumerate(chunks):
            if chunk.chunk_type == "text":
                # Implement only if VideoPrism has a compatible text encoder.
                raise ValueError("VideoPrism BYOM text embeddings are not configured")

            frames = video_frames[idx]
            embedding = self._embed_video(frames)
            outputs.append(
                VlmModelOutput(
                    output="",
                    input_tokens=frames.numel(),
                    output_tokens=len(embedding),
                    embeddings=embedding,
                )
            )
        return outputs
```

Always confirm the `VlmModelOutput` dataclass in `models/base_vlm_model.py`
before committing, because branch-specific fields can change.

## 4. Decide Text Endpoint Behavior

RT-Embed exposes both:

- `/v1/generate_video_embeddings`
- `/v1/generate_text_embeddings`

Video search requires video and text embeddings in the same vector space. If the
VideoPrism model being integrated is video-only, choose one of these explicit
behaviors:

1. Return a clear 4xx error for text chunks with a message like
   `VideoPrism BYOM text embeddings are not configured`.
2. Pair VideoPrism with a compatible text encoder and guarantee both endpoints
   return the same embedding dimension and semantic space.

Do not silently return zero vectors, random vectors, or embeddings from an
unrelated text model.

## 5. Add Optional Triton/TensorRT Path

Start with PyTorch inference first. Once correctness is proven, add:

```bash
create_triton_model_repo.py
triton_model_repo/video_embeddings/config.pbtxt
```

The script should:

- Resolve `MODEL_PATH` to a local checkpoint.
- Export or copy ONNX artifacts into `/tmp/triton_model_repo/<model-name>`.
- Build TensorRT engines only when the target host and TensorRT version support
  the model.
- Keep engine filenames deterministic and include precision or extra-arg suffixes
  when those options change engine contents.

If no optimized path exists yet, set:

```bash
export DISABLE_OPTIMIZATION=true
unset MODEL_REPOSITORY_SCRIPT_PATH
```

or point `MODEL_REPOSITORY_SCRIPT_PATH` to a no-op script that validates the
checkpoint and exits successfully.

## 6. Wire Docker Compose

For local Compose validation:

```bash
export RTVI_EMBED_IMAGE=nvcr.io/nvidia/vss-core/vss-rt-embed
export RTVI_EMBED_TAG=3.3.0-26.07.3
export RTVI_EMBED_PORT=8017
export VSS_DATA_DIR="${PWD}/.standalone-data"
export NGC_API_KEY="<ngc-api-key>"
export HF_TOKEN="${HF_TOKEN:-}"

export MODEL_PATH="git:https://huggingface.co/<org>/<videoprism-checkpoint>"
export MODEL_IMPLEMENTATION_PATH="/opt/nvidia/rtvi/rtvi/models/custom/samples/videoprism"
export MODEL_REPOSITORY_SCRIPT_PATH="/opt/nvidia/rtvi/rtvi/models/custom/samples/videoprism/create_triton_model_repo.py"
```

If testing source before it is baked into the image, mount the source tree so the
in-container implementation path exists:

```yaml
volumes:
  - ./services/rtvi/rt-embed/src:/opt/nvidia/rtvi/rtvi:ro
```

Then run:

```bash
cd deploy/docker/services/rtvi/rtvi-embed
docker compose -f rtvi-embed-docker-compose.yml \
  --profile bp_developer_search_2d up -d rtvi-embed
```

## 7. Wire Helm

Set these `rtvi-embed` chart values:

```yaml
modelPath: "git:https://huggingface.co/<org>/<videoprism-checkpoint>"
modelImplementationPath: "/opt/nvidia/rtvi/rtvi/models/custom/samples/videoprism"
modelRepositoryScriptPath: "/opt/nvidia/rtvi/rtvi/models/custom/samples/videoprism/create_triton_model_repo.py"
```

Render before deploying:

```bash
helm template rtvi-embed deploy/helm/services/rtvi/charts/rtvi-embed \
  --set modelPath="git:https://huggingface.co/<org>/<videoprism-checkpoint>" \
  --set modelImplementationPath="/opt/nvidia/rtvi/rtvi/models/custom/samples/videoprism" \
  --set modelRepositoryScriptPath="/opt/nvidia/rtvi/rtvi/models/custom/samples/videoprism/create_triton_model_repo.py"
```

Confirm the rendered container env includes the three expected values.

## 8. Validate Runtime Behavior

Readiness:

```bash
BASE_URL="http://localhost:${RTVI_EMBED_PORT:-8017}"
curl -fsS "$BASE_URL/v1/ready?detailed=true"
curl -fsS "$BASE_URL/v1/models" | jq .
```

Video embedding smoke:

```bash
FILE_ID=$(curl -fsS -X POST "$BASE_URL/v1/files" \
  -F purpose=vision \
  -F media_type=video \
  -F file=@/path/to/smoke.mp4 | jq -r .id)

MODEL_ID=$(curl -fsS "$BASE_URL/v1/models" | jq -r '.data[0].id')
curl -fsS -X POST "$BASE_URL/v1/generate_video_embeddings" \
  -H "Content-Type: application/json" \
  -d "{\"id\":\"$FILE_ID\",\"model\":\"$MODEL_ID\",\"chunk_duration\":5}" \
  | jq .
```

Text endpoint decision:

```bash
curl -i -sS -X POST "$BASE_URL/v1/generate_text_embeddings" \
  -H "Content-Type: application/json" \
  -d "{\"text_input\":\"person walking\",\"model\":\"$MODEL_ID\"}"
```

Expected result:

- Compatible text encoder present: HTTP 200 and same dimension as video vectors.
- Video-only backend: clear 4xx response. A 500 is a bug.

## 9. Test Before PR

Run focused checks:

```bash
python3 -m pytest tests/rtvi_embed/test_rtvi_embed_server.py -q
python3 -m pytest tests/rtvi_embed/test_rtvi_embed_stream_handler.py -q
python3 -m pytest tests/rtvi_embed/test_create_triton_model_repo.py -q
```

If the BYOM wrapper adds new behavior, add tests for:

- Model discovery and `model_name`.
- Video embedding dimension and JSON serialization.
- Text endpoint behavior, including the expected error path for video-only mode.
- `MODEL_REPOSITORY_SCRIPT_PATH` handling when optimization is disabled.

## 10. Completion Criteria

The VideoPrism BYOM integration is ready when:

- `inference.py` loads from `MODEL_IMPLEMENTATION_PATH`.
- `/v1/models` advertises the VideoPrism model id.
- Video embeddings return non-empty numeric vectors with a stable dimension.
- Text endpoint behavior is explicitly supported or explicitly rejected.
- Compose and Helm can set the three model path variables.
- Cold-start logs clearly show the VideoPrism path, not the Cosmos sample path.
