# vss.video_processing

Independent local media primitives for VSS. This README documents the processing
implementation, contracts, examples, known issues and validation. See the
[VSS package README](../../../README.md) for overall installation and the current
ingestion/summarization workflow. Run checkout commands below from the repository
root.

## What this MR adds and why

This MR implements the first phase of `vss.video_processing`: local media probing,
bounded frame decoding, sparse extraction, sampling, and verified temporary
clips. VLM inference and its response contracts are owned by `vss.ingestion`.

The implementation adds:

- Versioned Pydantic configuration and metadata schemas, with explicit units,
  validation and unknown values. Runtime frames own their NumPy pixels;
  serializable frame metadata and encoded-image envelopes are separate types.
- Interchangeable decoder sessions: PyNvVideoCodec for supported NVIDIA systems,
  OpenCV for CPU use, and a registration seam for other decoders. Native libraries
  are installed by the caller and loaded only when needed.
- GPU initialization followed by automatic CPU fallback when GPU decoding is
  unavailable. An explicit backend selection never silently changes backend.
- Async iterators with bounded batches, serialized native worker calls, session
  concurrency limits and cleanup on cancellation or explicit iterator closure.
- FFmpeg stream-copy clips with independently verified source identity, actual
  bounds and source-clock offsets. This includes a B-frame boundary correction
  that prevents duration-limited copying from losing intervening video packets.
- Tests written before the initial implementation, subsequent documented bug
  regressions, native acceptance tests and a runnable processing demo.

The package no longer requires OpenCV or PyNvVideoCodec binaries as base Python
dependencies. This allows importing schemas and using ingestion/summarization
without installing a local decoder.

## How processing fits ingestion and summarization

The libraries expose low-level processing and public ingestion workflows:

```text
local source → ingest(backends=["cpu_vlm"]) → OpenCV → frame-level VLM
local source → ingest(backends=["gpu_vlm"]) → PyNv → frame-level VLM
local source → ingest(decoder=..., single_decode=True) → multiple VLM backends
local source → ingest(backends=["rtvi"]) → RTVI caption service
caption chunks → summarize_video() → event aggregation → LLM
```

`vss.ingestion.ingest()` selects local VLM providers or remote services through
its `backends` list. The generic `vlm` backend accepts decoder selection through
`decoder="auto"`, `"opencv"`, or `"pynv"`; `cpu_vlm` and `gpu_vlm` are
decoder-fixed conveniences. Multiple compatible VLM providers consume one
decoded stream only when an explicit decoder and `single_decode=True` are
passed. Otherwise backends execute independently. Results are keyed by each
requested backend name.

## Installation and native prerequisites

For a checkout:

```bash
uv sync --all-packages --all-extras
```

Add the RT-CV Kafka client when using the CUDA IPC `rtvi_cv` backend:

```bash
uv sync --package vss-ingestion --extra rtvi-cv
```

For an installed distribution:

```bash
pip install vss vss-ingestion "vss-summarization[openai]"
```

For RT-CV:

```bash
pip install "vss-ingestion[rtvi-cv]"
```

Install only the native tools needed for your use case:

| Operation | Caller-installed prerequisite |
| --- | --- |
| Import schemas/package | No decoder or FFmpeg required |
| `probe_video`, CPU decoding, JPEG/PNG encoding | OpenCV >=4.13; e.g. `pip install "opencv-python-headless>=4.13"` |
| GPU decoding | PyNvVideoCodec >=2.2 plus compatible NVIDIA GPU/driver, Linux or Windows |
| CUDA IPC fan-out | Linux, NVIDIA GPU, DeepStream `nvunixfdsink`/`nvunixfdsrc`, and the native `vss_cuda_ipc_broker` library |
| Clip materialization | External `ffmpeg` **and** `ffprobe` on `PATH` |

Mac uses the OpenCV CPU path. `ffprobe` is normally included with an FFmpeg
installation; verify both executables are available. The `ffmpeg-python` Python
wrapper is not required or used. The `ingestion` extra installs the HTTP client
used for frame-level VLM calls. Neither extra installs decoder binaries.

## Processing API and contracts

Inputs are local `str`/`Path` paths, local `file://` URIs, or structural objects
with `url` and `source_id`. HTTP/S3/RTSP fetching and remote asset IDs are outside
this phase. Resolve or download those sources before calling processing.

| API | Result and behavior |
| --- | --- |
| `await probe_video(source)` | `MediaFacts`, using OpenCV |
| `decode_frames(source, DecodePlan(...))` | Async iterator of frames, one bounded batch at a time |
| `await extract_frames(source, [seconds, ...])` | List in caller order, preserving duplicate requests |
| `subsample_frames(source, SamplingPolicy(...))` | Async iterator; `count`, `fps`, `timestamps` or `all` policy |
| `await materialize_clip(source, start, end)` | Owned temporary `ClipArtifact`; bounds supplied in seconds |
| `probe_video_sync`, `extract_frames_sync` | Convenience wrappers for code without a running event loop |
| `frame.to_base64(format="jpeg")` | Plain base64 string; JPEG or PNG |
| `frame.to_encoded(format="png")` | JSON-safe `EncodedFrame` with metadata, MIME type and base64 payload |

Metadata/config schemas are frozen, reject extra fields and carry
`schema_version="1.0"`. `MediaFacts` includes codec, duration, FPS, width, height,
audio, container, warnings and timing provenance. Unknown values are `None`:
`audio=None` means unverified, whereas `audio=False` means verified absent.

Current probing reports a backend codec identifier and dimensions/rate estimates.
**Audio and container remain unknown, with an entry in `MediaFacts.warnings`,
even when ffprobe is installed.** A filename suffix is not proof of container
format. Optional ffprobe enrichment would be a separate addition. Clip creation
already uses ffprobe for its own verification.

Frame pixels are owned, contiguous, read-only CPU NumPy arrays with shape
`(height, width, 3)`, dtype `uint8`, RGB channel order. This is also the public
representation after GPU decoding: GPU use accelerates decode, but pixels are
transferred to host memory. Frames remain valid after their decoder closes.
`FrameMetadata` preserves observed/requested timestamps, identity, frame index,
backend, device, timing quality and any fallback reason.

Plans and frame timestamps use integer **source-relative nanoseconds**.
`TimeWindow` uses `[start_ns, end_ns)`; an omitted end means EOF. Sparse extraction
accepts **seconds** and returns the first decodable frame at or after each target,
with its actual timestamp. It cannot create a frame at a time the video does not
contain. Generated sampling grids may yield fewer physical frames when targets
coalesce or extend past the last frame. Explicit unmet targets fail.

Native time-to-index seeks can skip eligible variable-frame-rate frames. Both
CPU and GPU backends therefore select from observed timestamps in a forward pass from frame
zero, preserving the true source frame index. Sorted sparse targets share that
pass, but a late target or window still requires decoding its preceding frames.
This is a correctness tradeoff; an efficient seek needs a verified PTS index.
OpenCV reports an unknown raw PTS origin (`timestamp_origin_ns=None`) with a
warning because its frame clock is already source-relative. GPU decoding uses the lower-level PyNv packet decoder with display reordering.
Demuxed packet PTS survive reordering and are rescaled with integer arithmetic,
then normalized against the first video presentation timestamp. SimpleDecoder's
observed DTS-based timestamps and scanned metadata are not used. The demuxer's
terminal empty packet flushes delayed frames; owned host RGB is copied before another
native call can reuse its surfaces. Repeated overlapping PyNv 2.2.3 host RGB
operations corrupted pixels on the tested Linux runner: native RGB kernels share
CUDA color coefficients across sessions. A process lock serializes native creation,
decode/copy, reset and
release. Sessions can coexist, but their native work does not execute in parallel;
concurrency therefore does not promise GPU decode throughput scaling.

GPU exclusive duration and frame count remain unknown: native duration may be
the final timestamp, rather than an exclusive end. Unbounded streaming reaches
native EOF. Finite count sampling needs an explicit caller `TimeWindow`; FPS is
descriptive metadata and is not used to invent presentation timestamps.

Pass the same `source_id` across operations to preserve identity. Structural
source objects keep their ID; a bare path without an explicit ID gets a new UUID
per operation, not a persistent catalog identity or content hash.

`DecodeOptions()` defaults to `decoder="auto"`, `device_id=0`, `batch_size=8`,
`max_concurrency=4`, and `timestamp_mode="best_effort"`. Strict timing rejects
estimated timestamps. Automatic selection tries to initialize an actual GPU
session after a nonfatal CUDA driver/device/context check, then OpenCV. The
check avoids entering known fatal native initialization paths when CUDA is
unavailable; it does not certify codec support or isolate all native failures.
Fallback occurs only on decoder availability failures
before output, and records the reason. Read errors do not restart a stream on a
different backend. If neither backend is usable, `DecoderUnavailableError`
explains the attempts and installation remedies.

Other failures include `FileNotFoundError`, `UnsupportedSourceError`,
`DecodeError`, `TimestampUnavailableError` and `ClipError`. OpenCV can report
some damaged streams as early EOF; this phase does not guarantee detection of
all corruption. Native codec/container availability depends on the installed
build. Color-conversion limitations and the verified pixel scope are documented
under [Known issues](#known-issues). All backend/platform combinations are not
certified. OpenCV automatic display
rotation is disabled to preserve the encoded geometry promised by `MediaFacts`;
portrait pixel dimensions are preserved. Applying rotation metadata is a separate
preprocessing concern and is not exposed as a transform in this API.

## Processing examples

```python
import asyncio
from contextlib import aclosing
from pathlib import Path

from vss.video_processing import (
    DecodeOptions,
    DecodePlan,
    SamplingPolicy,
    TimeWindow,
    decode_frames,
    extract_frames,
    materialize_clip,
    probe_video,
    subsample_frames,
)


async def main(path: Path) -> None:
    identity = "camera-17"
    facts = await probe_video(path, source_id=identity)
    print(facts.model_dump_json(indent=2))

    stream = decode_frames(
        path,
        DecodePlan(window=TimeWindow(end_ns=10_000_000_000)),
        source_id=identity,
        options=DecodeOptions(decoder="auto", batch_size=4),
    )
    async with aclosing(stream):
        async for frame in stream:
            print(frame.timestamp_ns, frame.tensor.shape)
            break  # aclosing releases the native session promptly.

    frames = await extract_frames(path, [1.25, 3.0], source_id=identity)
    print(frames[0].to_encoded(format="png").model_dump_json())

    samples = subsample_frames(
        path,
        SamplingPolicy(mode="count", count=8),
        window=TimeWindow(end_ns=10_000_000_000),
        source_id=identity,
    )
    async with aclosing(samples):
        async for frame in samples:
            print(frame.timestamp_ns)

    artifact = await materialize_clip(path, 2.0, 8.0, source_id=identity)
    with artifact:
        print(artifact.path, artifact.actual_start_ns, artifact.actual_end_ns)
        # Finish any upload/consumption here, before cleanup removes the file.


asyncio.run(main(Path("/path/to/video.mp4")))
```

Clip requests expand to surrounding decodable GOP boundaries, so actual bounds
may exceed requested bounds. [Stream-copy](https://ffmpeg.org/ffmpeg.html#Streamcopy)
avoids re-encoding and preserves retained
video packets and eligible audio. Packet identity/timing verification rejects ambiguous
or unverifiable cuts. Use the measured bounds and `pts_offset_ns` to map
normalized clip-local time to source time. The artifact path belongs to the host
that created it, not a remote consumer. Call `cleanup()` or use its context manager;
publicly constructed artifacts are borrowed and never delete their files.

Audio is required when source audio packets overlap the actual retained video
interval; a clip preceding a delayed audio track can correctly contain no audio.
Unknown audio timing keeps the conservative audio-presence requirement. Current
verification checks video packet identity/timing and expected audio presence; it
does not independently certify every audio track's packet identity or A/V timing.
Packet-hash subsequence matching is linear; JSON parsing and mapping verification
run off the event loop. Clip verification still loads compressed-packet metadata
for the whole source, so memory and probe time scale with its packet count.

Async keeps native work off the event loop and permits overlapping I/O and
independent bounded sessions; it does not itself make decoding faster. Each
session has one serialized worker thread. Native failures are caught there;
traceback, cause and context references are detached before an immutable
category/message record crosses the executor boundary. A fresh typed error is
raised on the async side, preserving cancellation and primary-error precedence. Streaming does not accumulate a whole
video unless the caller collects its frames. Cancellation waits for an active
native call before closing; clip cancellation terminates/reaps the subprocess
and removes partial output. The library bounds its batches; native decoders have
their own buffers/indexes, so total process memory also depends on the backend.

Run the CPU VLM ingestion example:

```bash
export VLM_BASE_URL=https://inference-api.nvidia.com/v1
export VLM_MODEL=your-model
export VLM_API_KEY=your-api-key
PYTHONPATH=src python examples/ingest_video.py demo_videos/sample-sim-traffic.mp4
```

The example passes `decoder="opencv"`, forwards sampled frames to an
OpenAI-compatible VLM, and prints the caption responses. Pass
`decoder="pynv"` for PyNvVideoCodec decoding. Low-level decode, sampling, and
clip APIs remain independently available for offline processing. The
`examples/ingest_multiple.py` runs `vlm`, RTVI-VLM, and RTVI-Embed from one
selected decoder using owned CPU frames. `examples/ingest_multiple_cv.py`
separately demonstrates RTVI-VLM, RTVI-Embed, and RTVI-CV from one PyNv
decoder over CUDA IPC.

Local CPU/GPU ingestion limits complete decode-and-inference operations to four
concurrent calls per event loop by default. Set
`VSS_PROCESSING_MAX_CONCURRENCY` to a positive integer to change that bound.
The value is read once on the event loop's first call; restart the process to
change it. Calls waiting for capacity do not open a decoder or retain sampled frames.
`demo_videos/` and local working plan documents are ignored by Git.

### Shared decode buffers

With an explicit decoder and `single_decode=True`, multi-backend ingestion fans
one ordered decode stream into one bounded buffer per compatible backend. Each
consumer receives an idempotent frame
lease, keeps an independent cursor and sampling policy, and applies
backpressure when its buffer is full. A frame slot is reusable only after every
consumer releases it. End-of-stream and decode failures reach every attached
consumer; cancellation detaches buffers, releases queued and outstanding
leases, and closes the decoder.

The default `owned_cpu` transport shares one immutable host RGB `Frame` object
in process. It supports `vlm`, `rtvi`, and `rtvi_embed`; selected RTVI frames
are JPEG encoded for transport, so this path is not GPU zero-copy.

Pass `cuda_ipc=True` to `ingest()` to use PyNv device NV12 with `rtvi`,
`rtvi_embed`, and `rtvi_cv`; pass `False` to force owned CPU frames. When the
parameter is omitted, `VSS_SHARED_FRAME_TRANSPORT` remains the compatibility
default. The native broker makes one
device-to-device copy into a bounded pool of exportable NV12
`NvBufSurface` slots, then `nvunixfdsink` fans each slot out and the RTVI
pipelines import it with `nvunixfdsrc`. There is no host/JPEG transfer and no
per-backend surface copy after staging. This is zero-copy fan-out, not direct
export of a decoder-owned surface.

All CUDA IPC containers must use the same host and GPU and mount
`VSS_CUDA_IPC_SOCKET_DIR` at the same absolute path. Select the GPU with
`VSS_SHARED_DECODE_DEVICE_ID`. RT-CV additionally consumes analytics from
`RTVI_CV_KAFKA_BOOTSTRAP_SERVERS` and `RTVI_CV_KAFKA_TOPIC` through the
optional `rtvi-cv` dependency. Ingestion validates compatible protocol,
decoder, transport, pixel format, and device before decode starts.

Run the CUDA IPC example after mounting the broker directory at the same path
in every RTVI container:

```bash
export RTVI_BASE_URL=http://127.0.0.1:8018
export RTVI_MODEL=nim_nvidia_cosmos3-nano-reasoner_bf16-final
export RTVI_EMBED_BASE_URL=http://127.0.0.1:8017
export RTVI_CV_BASE_URL=http://127.0.0.1:9000
export RTVI_CV_KAFKA_BOOTSTRAP_SERVERS=127.0.0.1:9092
export RTVI_CV_KAFKA_TOPIC=ds-perception
export VSS_SHARED_DECODE_DEVICE_ID=0
export VSS_CUDA_IPC_SOCKET_DIR=/run/vss/frame-broker

mkdir -p "$VSS_CUDA_IPC_SOCKET_DIR"
chmod 700 "$VSS_CUDA_IPC_SOCKET_DIR"

uv run --package vss-ingestion --extra rtvi-cv \
  python examples/ingest_multiple_cv.py video.mp4
```

The command prints decode/attachment progress and per-chunk captions,
embedding dimensions, detection counts, and track counts. It does not contact
the frame-level `vlm` endpoint in CUDA IPC mode.

## Known issues

### Native OpenCV color conversion

The documented **Mac OpenCV 4.13.0** build produces incorrect RGB values for **full-range SDR
VP9** and **BT.709 SDR** inputs. For example, a gray fixture expected at RGB
`(80, 80, 80)` returns `(73, 73, 73)`. This affects brightness/contrast or colors,
not just metadata. Successful decoding does not establish accurate color.

Source inspection points to OpenCV's native YUV-to-BGR conversion path, before
our Python adapter receives the pixels. The tested wheel links its own FFmpeg
libraries (including swscale 8.3.100). **Installing Homebrew FFmpeg or
`ffmpeg-python` does not replace those libraries or fix this conversion.**

Reliable correction requires validating a corrected OpenCV native build or
adding a color-aware CPU backend through the existing decoder interface. These
remain open implementation fixes. A newer build is a candidate, not a guarantee,
until the unchanged pixel-oracle regressions pass. An approximate correction
after conversion cannot reliably recover values already clipped by the wrong
conversion. The two strict xfails now match only the documented Mac version and all four
linked-library versions (avcodec 61.19.101, avformat 61.7.100, avutil 59.39.100,
swscale 8.3.100). Other builds run ordinary pixel assertions. The Linux wheel
4.13.0.92 links avcodec 62.11.100 and swscale 9.1.100; both oracles pass there.

The passing cross-codec pixel matrix covers explicitly verified limited-range
BT.601 SDR, 8-bit YUV420. Full-range SDR is distinct from HDR/10-bit; HDR/10-bit
color accuracy also remains unverified. See the
[color diagnosis and correction paths](../../../docs/video_processing_validation.rst#color-conversion-diagnosis-and-pre-merge-work)
for the native-build evidence and acceptance requirements.

### Native GPU color and codec scope

On the tested Linux x86_64 PyNvVideoCodec 2.2.3 build, limited-range BT.709 VP9
has an RGB MAE of 6.57 against FFmpeg (acceptance tolerance 5.0). The adapter
rejects that exact native version/format tuple before output with
`DecoderUnavailableError`; automatic mode can use OpenCV with a recorded reason.
The unchanged all-frame RGB/PTS/index oracle is required for the fallback.
This combination is not claimed as GPU color support. H.264 BT.601/BT.709,
HEVC BT.709 and full-range BT.709 VP9 have separate GPU pixel-oracle tests.
Other native versions and HDR/10-bit remain unverified.

H100 reports no AV1 decode support; the tested OpenCV wheel also cannot decode
the AV1 fixture. Explicit GPU rejection does not establish CPU fallback support.
Hardware codec-specific minimum dimensions apply (H100 HEVC: 144x144;
VP9: 128x128). Native errors for invalid dimensions are not generally enough
to distinguish corruption from availability, so generic errors do not trigger
fallback.

### Partial metadata probing

`probe_video()` uses OpenCV and leaves audio/container facts as `None`, with
warnings, even when ffprobe is installed. Optional ffprobe enrichment is not
implemented. Clip verification uses ffprobe independently.

### Incomplete corruption detection

Some damaged streams can appear to OpenCV as early EOF. Tests cover unreadable
corrupt bytes and a missing MP4 index; they do not establish detection of every
mid-stream corruption pattern.
