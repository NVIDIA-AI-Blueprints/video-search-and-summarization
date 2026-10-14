# Native StreamingVLM

Native StreamingVLM is an opt-in RT-VLM backend that loads the model and the
bundled patched vLLM in process. It preserves visual context across ordered live
frames. Enabling SSE token output alone does not select this inference mode.

## Build

On an x86 Linux GPU host with Docker, NVIDIA Container Toolkit, Git LFS, and
access to the NGC base images:

```bash
git clone https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization.git
cd video-search-and-summarization/services/rtvi/rt-vlm
git lfs pull
docker build -f docker/Dockerfile \
  --build-arg RTVI_ENABLE_NATIVE_STREAMING_VLM=true \
  -t rtvi-vlm:native-streaming .
```

The build applies the bundled source delta to the Dockerfile's pinned vLLM
foundation and fails when its source or installed distribution version does not
match the manifest. Do not replace the base with an arbitrary vLLM image.
Copying RTVI Python files onto an unpatched image does not install native
StreamingVLM support.

## Run

From `services/rtvi/rt-vlm/docker`, copy `streaming.env.example` to `.env`, set
the NGC key and GPU, then start the base stack with the native override:

```bash
docker compose -p rtvi-native \
  -f compose.yaml -f compose.native-streaming.yaml up -d rtvi-server
curl --fail http://localhost:18094/v1/health/ready
```

The override enables native streaming and defaults to the validated eager and
FP8-KV contract. Changing either setting requires a fresh parity and performance
run. Native streaming requires exactly one VLM process so every update for a
stream reaches the worker that owns its retained history. Live caption requests
select `inference_mode=streaming_vlm` and
`streaming_frame_policy=ordered`. Use `streaming_question_on_decode=true` only
when repeated question conditioning is part of the prompt contract. Ordinary
requests remain chunked.

`streaming_text_round` and exact video compaction require prompt-role metadata
that the NIM-compatible image renderer does not preserve.
Native StreamingVLM rejects those options instead of silently applying an
incorrect retention policy.

## Validation and Cleanup

Before performance or accuracy runs, verify the image identity, effective engine
arguments, legal outputs, ordered frame indices, session close, and a small
concurrent/window-rotation canary. Preserve logs and input/config hashes; an API
smoke pass alone is not accuracy equivalence.

Stop only the deployment you launched:

```bash
docker compose -p rtvi-native \
  -f compose.yaml -f compose.native-streaming.yaml down
```

Keep registry credentials out of source control and reproduction bundles.
