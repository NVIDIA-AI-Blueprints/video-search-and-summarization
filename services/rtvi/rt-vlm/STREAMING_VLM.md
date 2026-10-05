# StreamingVLM: Native and Remote NIM

This branch provides two opt-in backends. Native mode loads the model and patched
vLLM inside RT-VLM. Remote mode uses the RTVI OpenAI-compatible adapter to call a
separate Cosmos streaming NIM's `/v1/streaming/sessions` API. Neither mode is
enabled merely by asking for SSE token output.

## Obtain and Build the PR Source

On an x86 Linux GPU host with Docker, NVIDIA Container Toolkit, Git LFS, and
access to the NGC base images:

```bash
git clone https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization.git
cd video-search-and-summarization
git fetch origin pull/2508/head
git checkout -b test-streaming-vlm FETCH_HEAD
git lfs pull
git rev-parse HEAD
cd services/rtvi/rt-vlm
docker build -f docker/Dockerfile \
  --build-arg RTVI_ENABLE_NATIVE_STREAMING_VLM=true \
  -t rtvi-vlm:streaming-pr2508 .
```

Record the commit and resulting `docker image inspect rtvi-vlm:streaming-pr2508`
identity for reproduction. The native build applies the bundled source delta to
the Dockerfile's pinned vLLM foundation and fails on incompatible source. Do not
replace its base with an arbitrary newer vLLM image. Copying RTVI Python files
onto an unpatched image does not install native StreamingVLM support.

This locally built image includes both adapters; it is not a published registry
tag. A moving GHCR `develop-latest` image must not be assumed to contain this
unmerged PR. Remote-only builds can omit the native build argument.

## Native Mode

From `services/rtvi/rt-vlm/docker`, copy `.env.example` to `.env` and configure
the NGC key, an available GPU, and a port as described in the service README:

```dotenv
RTVI_IMAGE=rtvi-vlm:streaming-pr2508
BACKEND_PORT=8000
NVIDIA_VISIBLE_DEVICES=0
VLM_MODEL_TO_USE=cosmos-reason3
MODEL_PATH=ngc:nim/nvidia/cosmos3-nano-reasoner:bf16-final
VIA_VLM_STREAMING_NIM_ENABLED=false
```

Create `compose.native-streaming.yaml` beside `compose.yaml`:

```yaml
services:
  rtvi-server:
    environment:
      RTVI_STREAMING_VLM_ENABLED: "true"
```

```bash
docker compose -f compose.yaml -f compose.native-streaming.yaml up -d
docker compose -f compose.yaml -f compose.native-streaming.yaml logs -f rtvi-server
```

The explicit override is necessary: adding arbitrary variables to `.env` does
not forward them into a Compose container. Use a live video stream and select
`inference_mode=streaming_vlm` and `streaming_frame_policy=ordered` in its caption
request. See the running service's `http://localhost:8000/docs` for the complete
request schema and the README's live-stream creation examples. Use
`streaming_question_on_decode=true` only when repeated question conditioning is
part of the intended prompt contract. Ordinary requests remain chunked.

## RTVI to Cosmos Streaming NIM

The streaming NIM release candidate used for integration testing is:

```text
nvcr.io/nvstaging/nim/cosmos3:2.1.0-rc.20260916144532-7a95fc7310e35c5f
```

This is an access-restricted pre-release, not a generally available NIM image.
Use it only with registry entitlement; a different release needs its own
protocol validation. Its observed local image identity was
`sha256:9b872954460553fbf151016b4a366b5473d9a66623de1fa41b7b18cc373cefc6`.

Run NIM on a separate host or available GPU. Follow the image's profile/model
setup requirements; do not silently use bundled weights when comparing a
specific checkpoint. A minimal launch with an entitled profile is:

```bash
export NIM_IMAGE=nvcr.io/nvstaging/nim/cosmos3:2.1.0-rc.20260916144532-7a95fc7310e35c5f
export NIM_MODEL_PROFILE=<profile-supported-by-your-model-and-GPU>
export NIM_CACHE_DIR=/absolute/path/to/writable/nim-cache
mkdir -p "$NIM_CACHE_DIR"
docker run -d --name cosmos-streaming-nim --gpus '"device=0"' --ipc=host \
  -p 18091:8000 -v "$NIM_CACHE_DIR:/opt/nim/.cache" \
  -e NGC_API_KEY -e NIM_MODEL_PROFILE \
  -e NIM_ENABLE_STREAMING=true \
  "$NIM_IMAGE"
docker logs -f cosmos-streaming-nim
curl --fail http://localhost:18091/v1/models
```

Replace the profile placeholder before executing. Cache permissions must permit
the image's runtime user to write. The NIM and native RT-VLM model must not both
be loaded onto a memory-constrained GPU. Stop the native deployment first when
switching modes on one GPU.

Configure RT-VLM's `docker/.env` for the remote adapter:

```dotenv
RTVI_IMAGE=rtvi-vlm:streaming-pr2508
BACKEND_PORT=8000
VLM_MODEL_TO_USE=openai-compat
VIA_VLM_STREAMING_NIM_ENABLED=true
VIA_VLM_ENDPOINT=http://<nim-host-reachable-from-rtvi>:18091/v1
VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME=<id-returned-by-nim-v1-models>
VIA_VLM_API_KEY=<key-required-by-your-nim-deployment>
```

Use the standalone Compose stack **without** the native override:

```bash
docker compose -f compose.yaml up -d
docker compose -f compose.yaml logs -f rtvi-server
```

`localhost` inside RT-VLM is not the NIM host. Use a reachable host address or
configured container-network service name. Select `inference_mode=streaming_vlm`
with exactly one sampled frame per update. The adapter creates a persistent
NIM session, posts JPEG/PNG frames, and deletes the session at close. Ordinary
OpenAI-compatible chunking continues to use `/v1/chat/completions`.

## Limits and Validation

- The PR adapter conditions the question on the first frame only; it rejects
  `streaming_question_on_decode` and non-text `response_format`.
- Seed/thinking controls and all native retention options are not forwarded by
  this adapter. Do not claim matched scientific settings for unsupported fields.
- The adapter retries HTTP 415 frame rejection once with a JSON `image_b64`
  envelope containing the same encoded bytes; it does not JPEG-encode again.
  Other HTTP failures are not retried by this fallback.
- Subsequent diagnostic guided-choice/budget forwarding and shutdown overlays
  are not implied by these instructions or the PR build.
  Verify the exact source revision before relying on them. In particular, an
  HTTP 415 from both frame transports remains a failure; do not treat such a
  run as successful.
- Confirm service readiness, effective model identity, legal outputs, session
  close, and a small concurrent/window-rotation canary before throughput or
  accuracy runs. Preserve logs and input/config hashes. API smoke success is
  not accuracy equivalence.
- Keep NGC credentials out of source control and reproduction bundles.

Clean up only the deployment you launched, using its exact Compose file set.
For the separate NIM, use `docker stop cosmos-streaming-nim` followed by
`docker rm cosmos-streaming-nim`; verify no owned GPU process remains.
