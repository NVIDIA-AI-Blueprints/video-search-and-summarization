# Turbo NIM Through RTVI

This recipe connects RT-VLM's `openai-compat` streaming adapter to a separate
Cosmos3 Turbo NIM. It depends on the streaming adapter in PR #2508. Native mode
is not required; see [STREAMING_VLM.md](STREAMING_VLM.md) for native instructions
and building this branch's RT-VLM image (remote-only builds may omit the native
build argument).

## Start Turbo NIM

The October 1 experimental RC is pinned below. Registry entitlement is required;
authenticate with `docker login nvcr.io` using your own locally stored NGC key.
Do not commit credentials or publish this restricted image to another registry.

```bash
export NIM_IMAGE=nvcr.io/nvstaging/nim/cosmos3-turbo-reasoner:2.1.0-rc.experimental.20261001171255-d8829b41f92dbcb3@sha256:a0c0177af4887522894f040297156050fd1c7d4356202f91cc5b083a0e4bfe6f
export MODEL_DIR=/absolute/path/to/cosmos3-nano-reasoner-bf16-final
export NIM_CACHE_DIR=/absolute/path/to/fresh-writable-nim-cache
mkdir -p "$NIM_CACHE_DIR"
docker pull "$NIM_IMAGE"
docker image inspect "$NIM_IMAGE" --format '{{json .RepoDigests}}'
docker run -d --name rtvi-turbo-nim --gpus '"device=0"' --ipc=host \
  -p 127.0.0.1:18093:8000 \
  -v "$MODEL_DIR:/model:ro" -v "$NIM_CACHE_DIR:/opt/nim/.cache" \
  -e NIM_MODEL_PATH=/model -e NIM_DISABLE_MODEL_DOWNLOAD=1 \
  -e NIM_MODEL_TYPE=reasoner -e NIM_MODEL_VARIANT=nano -e NIM_PRECISION=bf16 \
  -e NIM_MODEL_PROFILE=ce9cb91d779aea8017b2f5b9118b4608313f5b6e80bf6ef602fcd74b53fc9e3a \
  -e NIM_USE_DFLASH=0 -e NIM_ENABLE_STREAMING=1 \
  -e NIM_STREAMING_MAX_SESSIONS=2 -e NIM_STREAMING_MAX_VIDEO_SEGMENTS=8 \
  -e NIM_GPU_MEMORY_UTILIZATION=0.75 -e NIM_MAX_MODEL_LEN=16384 \
  "$NIM_IMAGE"
docker logs -f rtvi-turbo-nim
```

Replace both directory placeholders. The cache must be writable by the image's
runtime user. Wait for readiness before running RTVI:

```bash
curl --fail http://127.0.0.1:18093/v1/health/ready
curl --fail http://127.0.0.1:18093/v1/models
```

The Turbo **container** in this recipe loads the Nano BF16 **model profile**.
This small integration configuration disables DFlash/speculation and needs no
draft model. It is not the tuned 12-token speculation benchmark configuration.
Do not infer accuracy or camera capacity from this smoke test, or substitute
another checkpoint without recording its identity. Stop native model serving
before using the same GPU for NIM.

## Configure RT-VLM

Configure the standalone RT-VLM `docker/.env`:

```dotenv
RTVI_IMAGE=rtvi-vlm:streaming-pr2508
VLM_MODEL_TO_USE=openai-compat
VIA_VLM_STREAMING_NIM_ENABLED=true
VIA_VLM_ENDPOINT=http://<nim-host-reachable-from-rtvi>:18093/v1
VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME=<id-returned-by-v1-models>
OPENAI_API_KEY=local
VIA_VLM_API_KEY=local
```

Use the service's real keys if authentication is enabled. Do not add the native
streaming Compose override. Start with `docker compose up -d` from `docker/`.
The loopback NIM binding above is for same-host testing; production deployment
needs an authenticated network route reachable from RT-VLM. `localhost` inside
an ordinary container is not the NIM host.

Live caption requests must select `inference_mode=streaming_vlm` and exactly one
sampled frame per update. The adapter uses persistent REST streaming sessions
(`/v1/streaming/sessions`), not `/v1/chat/completions` or token-output SSE. The
image also exposes a WebSocket API; the RTVI test deliberately uses the adapter's
REST path rather than a direct-NIM benchmark client. Existing adapter limits
in [STREAMING_VLM.md](STREAMING_VLM.md#limits-and-validation) still apply.

## Run the Adapter Smoke Test

Run from the RT-VLM source directory on the Linux GPU host. `RTVI_IMAGE` must be
built from this branch; the source and test below are mounted read-only to make
the exact tested revision explicit. Record `git rev-parse HEAD` and both image
identities. This command uses host networking only for a same-host test.

```bash
export RTVI_IMAGE=rtvi-vlm:streaming-pr2508
export NIM_MODEL_ID=<id-returned-by-v1-models>
export SMOKE_OUT=/absolute/path/to/new-smoke-evidence
mkdir "$SMOKE_OUT"
docker run --rm --name rtvi-turbo-smoke --network=host --gpus '"device=0"' \
  -e NVIDIA_DRIVER_CAPABILITIES=compute,utility,video \
  -e USER=smoke -e LOGNAME=smoke -e HOME=/tmp \
  -e PYTHONPATH=/opt/nvidia/rtvi/rtvi \
  -e OPENAI_API_KEY=local -e VIA_VLM_API_KEY=local \
  -e VIA_VLM_STREAMING_NIM_ENABLED=true \
  -e VIA_VLM_ENDPOINT=http://127.0.0.1:18093/v1 \
  -e VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME="$NIM_MODEL_ID" \
  -v "$PWD/src:/opt/nvidia/rtvi/rtvi:ro" \
  -v "$PWD/scripts/smoke_streaming_nim.py:/smoke.py:ro" \
  -v "$SMOKE_OUT:/evidence" --entrypoint python3 \
  "$RTVI_IMAGE" /smoke.py --out /evidence/smoke
docker logs rtvi-turbo-nim > "$SMOKE_OUT/nim.log" 2>&1
```

Expected: `PASS: 44 outputs, two concurrent sessions, reopen and teardown`, exit
code zero, and `smoke/gate.json` with `status=passed`. The test decodes the bundled
warmup clip through RTVI, encodes JPEG95 once, submits 20 updates per concurrent
session, closes both, and creates a fresh four-update session. It checks nonempty
outputs, unique session IDs and contiguous frame indices when returned. Inputs,
JPEG hashes, output text and per-request timings are preserved. Use a new output
directory each time; failures must not overwrite previous evidence.

This is a decoder/model-adapter smoke test, not a full RT-VLM REST caption
workflow, accuracy evaluation, internal eviction proof, or throughput benchmark.
Review both client and NIM logs for errors even when the gate passes.

### Recorded Validation

On October 5, 2026, the pinned Turbo RC above passed this test on an H100 PCIe
80GB: 44 nonempty outputs, three distinct sessions, contiguous frame indices and
successful remote close. The JSON frame fallback was exercised; client and
service logs had no ERROR or traceback lines. Four focused NIM adapter unit
tests also passed. Owned containers were removed and GPU memory returned to
14 MiB. No throughput or accuracy claim is made.

The live check mounted adapter source from prerequisite commit
`5b886252fa91e98c7ce5e1df1d2d45e07471abb7` onto the existing RTVI runtime foundation
`sha256:c8551f83f7abed6c89c597c4edffec587efc5cd9e52991bdb29d6c9d6e615fba`.
It used the smoke script in this change, read-only cached BF16 weights, the
launch settings above, and no runtime monkeypatches. A fresh full Docker build
and the full REST caption pipeline were not validated by this check.

## Clean Up

```bash
docker stop rtvi-turbo-nim
docker rm rtvi-turbo-nim
nvidia-smi
```

Stop only your own deployments. Preserve logs and test artifacts before cleanup.
