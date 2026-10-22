# VSS

Python libraries for video processing, ingestion and summarization. Python 3.13
or newer is required.

This directory is the combined VSS library workspace. The existing
`nvidia-vss-core` and `nvidia-vss-cli` projects remain in `core/` and `cli/`.
The public `vss` namespace is provided by this root project and the
`packages/ingestion` and `packages/summarization` feature wheels.

## Installation

For a checkout:

```bash
uv sync --all-packages --all-extras
```

For an installed distribution:

```bash
pip install vss vss-ingestion "vss-summarization[openai]"
```

## Vision AI tool adapter

`../vision-ai/packages/vss` provides the `nvidia-vss-vision-ai` adapter. It
registers the `vss` entry point through the shared `vision_ai.tools` contract
and calls only public `vss.ingestion` and `vss.summarization` APIs.

```bash
uv run --project ../vision-ai vision-ai vss \
  --source /path/to/video.mp4 \
  --backends vlm \
  --decoder opencv \
  --summarize
```

Backend service URLs, credentials, GPU placement, and transport endpoints stay
in deployment configuration; they are not tool inputs or result fields.

Local processing uses caller-installed OpenCV >=4.13 for CPU decoding, probing
and image encoding, or PyNvVideoCodec >=2.2 on supported NVIDIA Linux/Windows
systems for GPU decoding. Clip creation requires external `ffmpeg` and `ffprobe`.
Native decoder binaries are not base package dependencies.

## Video processing

This MR adds independent local video probing, bounded async decoding, sparse
extraction, sampling and verified temporary clips. Versioned schemas preserve
source identity and actual timestamps; interchangeable decoders support automatic
GPU-to-CPU availability fallback and explicit cleanup.

Frame-level VLM processing probes and samples local video through these APIs
using automatic GPU-to-CPU decoder selection, then sends each bounded frame
chunk to an OpenAI-compatible multimodal endpoint. Ingestion is separate and
contains the whole-video RTVI caption backend.

See the [video processing README](src/vss/video_processing/README.md) for API
contracts, dependencies, examples, implementation details and validation evidence.

## Known issues

The documented Mac OpenCV 4.13.0 build has full-range VP9 and BT.709 SDR
color mismatches. Those unchanged oracles pass on the tested Linux 4.13 build.
PyNvVideoCodec 2.2.3 on Linux x86_64 has a separate limited-range BT.709 VP9
RGB defect: explicit GPU decoding rejects that path and automatic selection
uses the CPU decoder. See [processing known issues](src/vss/video_processing/README.md#known-issues).

Installed-wheel Linux acceptance has been exercised on H100 with PyNvVideoCodec
2.2.3, including presentation timing, worker failure cleanup and independent RGB
checks. The [validation record](docs/video_processing_validation.rst) records
measurements, sustained-run evidence and remaining platform/codec gates.

## Processing, ingestion and summarization

```text
local video → ingestion.ingest(backends=["cpu_vlm"]) → OpenCV → generic VLM
local video → ingestion.ingest(backends=["gpu_vlm"]) → PyNv → generic VLM
local video → ingest(decoder=..., single_decode=True) → multiple VLM backends
local video → ingestion.ingest(backends=["rtvi"]) → RTVI
VLM chunks → summarize_video() → event aggregation → LLM → CompletionResponse
```

Start an RTVI `rt-vlm` service following the
[service instructions](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization/tree/develop/services/rtvi),
then configure the endpoints:

```bash
export VSS_INGESTION_BACKEND=rtvi
export RTVI_BASE_URL=http://127.0.0.1:8018
export RTVI_MODEL=nim_nvidia_cosmos3-nano-reasoner_bf16-final
export RTVI_API_KEY=your-optional-rtvi-api-key
export VSS_SUMMARIZATION_BASE_URL=https://your-llm-endpoint/v1
export VSS_SUMMARIZATION_MODEL=your-model-name
export VSS_SUMMARIZATION_API_KEY=your-api-key
```

`VSS_INGESTION_BACKEND` defaults to `rtvi` and accepts a comma-separated backend
list when `backends` is omitted. Explicit `backends=[...]` takes precedence.
`RTVI_API_KEY` is optional for unauthenticated endpoints. Ingestion uses a
bounded in-process queue: defaults are four running jobs and 100 waiting jobs,
configurable with
`VSS_INGESTION_MAX_CONCURRENCY` and `VSS_INGESTION_QUEUE_SIZE`. `ingest()`
submits immediately and returns an awaitable job. Summarization uses the
Responses API by default.

RTVI-Embed is configured independently, so caption and embedding backends can
be enabled and used concurrently:

```bash
export RTVI_EMBED_BASE_URL=http://127.0.0.1:8017
export RTVI_EMBED_MODEL=cosmos-embed1-448p
export RTVI_EMBED_API_KEY=your-optional-api-key
```

```python
results = await ingest(source, backends=["vlm", "rtvi_embed"])
captions = results["vlm"]
embeddings = results["rtvi_embed"]
```

Multi-backend ingestion runs each backend's normal path by default. To request
one decoder, pass an explicit `decoder=` and `single_decode=True`.

With `cuda_ipc=False`, `vlm`, RTVI, and RTVI-Embed consume bounded owned-CPU
RGB frames. RTVI samples are JPEG encoded for upload, so this path is not GPU
zero-copy. The requested decoder (including `opencv` or `pynv`) is honored.

With `cuda_ipc=True`, RTVI, RTVI-Embed, and RTVI-CV consume PyNv-decoded NV12
frames over same-host CUDA IPC. This path performs one device-to-device staging
copy and then zero-copy fan-out without host/JPEG transfer. When `cuda_ipc` is
omitted, `VSS_SHARED_FRAME_TRANSPORT` selects the compatibility default.
Ingestion requires every selected backend to support the selected transport
and decoder and fails before decoding if they are incompatible.
`VSS_SHARED_DECODE_BUFFER_DEPTH` sets the positive per-backend queue depth and
defaults to 8.

The embedding path returns validated `list[EmbeddingChunkResponse]` values.
Each response preserves the caller's source identity and carries either an
embedding vector or a chunk-local error.

For library-side decoding with a generic multimodal VLM:

```bash
export VLM_BASE_URL=https://inference-api.nvidia.com/v1
export VLM_MODEL=gcp/google/gemini-3.5-flash-lite
export VLM_API_KEY=your-api-key

python examples/ingest_video.py test_video_720p.mp4
```

The generic `vlm` ingestion backend is independent of decoder selection. Pass
`decoder="opencv"`, `decoder="pynv"`, or `decoder="auto"` (the default) to
choose the decode path. It samples eight RGB frames per chunk and sends them to
the configured multimodal VLM. `cpu_vlm` and `gpu_vlm` remain convenient
decoder-fixed aliases for independent ingestion.

Multiple registered `VLMBackend` implementations can be passed together. They
share one selected decoder when `single_decode=True`, while retaining separate
buffers, sampling, model configuration, and result keys. Without that flag,
each backend runs independently. See
[`examples/ingest_multiple.py`](examples/ingest_multiple.py) for a standalone
multi-provider example. For same-host CUDA IPC fan-out to RTVI-VLM,
RTVI-Embed, and RTVI-CV, see
[`examples/ingest_multiple_cv.py`](examples/ingest_multiple_cv.py).

Custom `VLMBackend` implementations receive public `VLMFrameChunk` values with
source identity, half-open chunk bounds in seconds, and owned frames whose
presentation timestamps are in nanoseconds.

```python
import asyncio
import json
from vss.ingestion import RequestSource, ingest
from vss.summarization import summarize_video


async def main() -> None:
    results = await ingest(
        RequestSource.for_file("/path/to/video.mp4"),
        backends=["vlm"],
        decoder="opencv",
        prompt="Return visible events with start_time, end_time, type, description.",
        chunk_duration=60,
    )
    completion = await summarize_video(results["vlm"])
    print(json.dumps(json.loads(completion.choices[0].message.content), indent=2))


asyncio.run(main())
```

## Persistent captions (M6)

Ingestion does not write files unless storage is explicitly configured or
injected. Base storage needs only the standard library and Pydantic; it
does not import ingestion, video processing, or native decoders. The file
adapter defaults to `~/.vss` when selected; memory storage is transient.
`StorageConfig(backend="memory")` explicitly selects transient storage even when
file storage is configured in the environment. M6 includes file, memory and
optional Elasticsearch adapters (`pip install "vss[storage]"`). ES persists
snapshots remotely, with same-machine job ownership through local OS locks;
cross-host takeover and incremental checkpoint writes remain separate work.
Checkpoints support local VLM
ingestion with an explicit model, concrete decoder, and caller-maintained
pipeline revision.
Trusted immutable source identities can skip repeated full-video hashing.
File storage reuses lock slots; unsupported pre-release lock layouts are rejected.
Both features are detailed in the storage README.
Managed observations retain the caller's original source ID separately from
RTVI upload IDs. Arbitrary caller JSON and model-generated text are preserved
without automatic sensitive-content redaction; see the storage README for limits.

The runnable [persistent video example](examples/persistent_video.py) prints
the job/source IDs before inference, can resume confirmed chunks, and can
summarize stored captions in a later process without the source video. See the
[storage README](src/vss/storage/README.md) for contracts and integration, and the
[test and usage guide](tests/storage/README.rst) for configuration and validation.

The InferenceHub Qwen endpoint can be selected with `VLM_BASE_URL`, `VLM_MODEL`,
and `VLM_API_KEY`; summary generation uses `VSS_SUMMARIZATION_*` settings and
`VSS_SUMMARIZATION_API=chat_completions`. Qwen completed the traffic video: five
caption chunks were persisted, then a separate process aggregated seven events
and generated a summary. Capacity-related HTTP 429s were recovered through
checkpoint resume. Deterministic tests need no credentials. Windows file-lock/
durability and GPU pipeline integration remain platform gates.
