Video processing: executable specification
==========================================

This suite originated before processing implementation. The full working plan is
docs/video_processing_plan.rst (an ignored local document); it records reviewed
refinements to the original contracts below. Missing processing exports/decoder
seams produced a deliberate red baseline, not xfail or unconditional skip, before
production writes began.
Ingestion/summarization interfaces remain unchanged by processing implementation.

Run with Python 3.13+, NumPy, Pydantic, pytest and pytest-asyncio. The existing
regression suite also uses the ingestion/summarization development dependencies.
For a source checkout::

    PYTHONPATH=src pytest tests/video_processing --collect-only -q
    PYTHONPATH=src pytest tests/video_processing -q
    PYTHONPATH=src pytest tests --ignore=tests/video_processing -m "not functional" -q
    PYTHONPATH=src pytest tests/integration/test_ingestion_summarization.py -q

Ordinary processing tests use synthetic decoder sessions and mocked subprocesses.
They do not require a GPU, OpenCV, PyNvVideoCodec, FFmpeg, or an inference service.
Native acceptance tests skip only until explicitly enabled::

    RUN_VSS_CPU_TESTS=1 PYTHONPATH=src pytest tests/video_processing/test_native.py -q
    RUN_VSS_GPU_TESTS=1 PYTHONPATH=src pytest tests/video_processing/test_native.py -q
    RUN_VSS_CLIP_TESTS=1 PYTHONPATH=src pytest tests/video_processing/test_native.py -q
    RUN_VSS_TIMING_TESTS=1 RUN_VSS_CPU_TESTS=1 PYTHONPATH=src pytest tests/video_processing/test_native_timing.py -q
    RUN_VSS_TIMING_TESTS=1 RUN_VSS_GPU_TESTS=1 PYTHONPATH=src pytest tests/video_processing/test_native_timing.py -q

Install OpenCV >=4.13 yourself for CPU acceptance and image-encoding tests.
GPU acceptance needs PyNvVideoCodec >=2.2 on a supported NVIDIA system, OpenCV,
and external FFmpeg for its known H.264 fixture. Clip acceptance needs FFmpeg
and ffprobe. Fixture generation defaults to libx264; VSS_TEST_H264_ENCODER can
select a compatible installed H.264 encoder. Opting in with missing prerequisites
fails instead of silently skipping the requested acceptance check. The separate
native timing suite needs FFmpeg/ffprobe and compares VFR, nonzero-origin and
B-frame fixtures against independent ffprobe presentation timestamps.

Interface decisions locked by the tests
--------------------------------------

* SourceRef structurally accepts url/source_id; existing RequestSource works
  unchanged. Paths and local file:// URIs work; remote locators and asset IDs
  remain with RTVI in this first release. Generated identities are per-operation,
  not persistent catalog entries or hashes. source_id= preserves caller identity.
* MediaFacts uses the design document's audio field. Unknown audio is None,
  not False. Additional provenance uses duration_quality, frame_rate_mode,
  frame_count and timestamp_origin_ns. Wire/config models are frozen and v1.
* Frame exposes tensor, timestamp_ns, source_id, index and decoder/timestamp
  diagnostics. Its metadata property is a JSON-safe FrameMetadata. The runtime
  frame owns its pixels. EncodedFrame uses metadata, mime_type and data_base64.
* TimeWindow uses integer source-relative nanoseconds and [start, end). DecodePlan
  defaults to all frames from start to EOF; sample_timestamps_ns=None means
  sequential, while () means no targets. extract_frames accepts seconds, keeps
  caller order and duplicates, and fails rather than silently omitting a request
  with no decodable frame at or after it. subsample_frames de-duplicates physical
  frames reached by multiple targets. Fixed-count/FPS target generation follows
  RT-VLM's spacing rules, with one intentional difference: exclusive end bounds.
* SamplingPolicy is a Pydantic-facing facade accepting mode=count/fps/timestamps/
  all plus the matching count, fps or timestamps_ns fields; incompatible fields
  fail. Caller-defined temporal selection is represented by explicit timestamps.
* DecodeOptions defaults to decoder=auto, device_id=0, timestamp_mode=best_effort,
  batch_size=8, max_concurrency=4. Explicit native backend names are pynv/opencv.
* Best effort labels estimates; strict rejects them. Raw backend timestamps are
  normalized against MediaFacts.timestamp_origin_ns. Average FPS is never used
  to replace reliable VFR timestamps. Requested and observed times are distinct.
* register_decoder accepts a name and zero-argument backend factory; duplicates
  fail unless replace=True. The private registry test seam is
  vss.video_processing.decoders.registry._DECODER_REGISTRY. Sessions expose
  blocking probe(), seek(ns), read_frames(batch_size), and close(); read results
  structurally expose tensor/timestamp_ns/index/timestamp_quality. Each session
  uses a serialized worker. The doubles intentionally seek before the target,
  so selection, normalization and ownership must be implemented in the library.
* Automatic selection falls back only for DecoderUnavailableError during open,
  before returning frames. Explicit requests, corrupt input and mid-stream
  decode errors propagate. CPU fallback carries a reason in frame metadata.
* No background full-video prefetch. max_concurrency limits live sessions across
  calls using that configuration. Cancellation waits for a native call to finish
  before closing its session. Use aclosing() for explicit early iterator closure.
* materialize_clip is async; artifact.cleanup() and artifact context management
  are synchronous. External FFmpeg copies full GOPs covering the request,
  preserves audio, verifies output and reports actual source bounds. pts_offset_ns
  maps clip-local zero to the original source. Cancellation terminates/reaps the
  subprocess and removes partial files. Caller-installed decoders are not base
  wheel dependencies; the packaging test intentionally fails until that changes.

Acceptance evidence still needed on real runners
-----------------------------------------------

The synthetic tests establish orchestration contracts, but cannot prove a native
decoder reports correct PTS. Run the native VFR/nonzero-origin/B-frame suite on
real backends. Additional curated HDR/10-bit, rotation and corrupt-media fixtures
are needed before claiming support for those inputs. Mac CPU, Linux CPU/GPU and
Windows CPU/GPU require their own runner results. Native memory profiling is
needed in addition to the bounded-read test; this suite does not claim GPU
correctness or absence of VRAM leaks without it.

Whole-pipeline integration gates
--------------------------------

tests/integration/test_ingestion_summarization.py exercises the real public
ingest(), queue, RTVI backend/transport/SSE mapper, summarize_video(), aggregation,
OpenAISummaryGenerator and provider SDK. Only external HTTP responses and backend
deployment configuration are injected. It covers both Responses and Chat
Completions requests, out-of-order chunks, duplicate updates, valid overlapping
events and evidence, usage/schema round trips, deferred summary from actual
ingestion outputs, empty output, inference/model/summary failures, asset ownership,
delete-conflict retries, request and mid-SSE cancellation, pending cancellation,
and subsequent queue reuse. The temporary input bytes are uploaded; no native
decoding is claimed by this network-mocked integration test.

This is the existing whole-video baseline, not proof that local processing feeds
inference. It explicitly characterizes today's remote asset identity. A future
production processing adapter must pass the following test-first acceptance
matrix; a test-only adapter must not supply its missing dispatch/normalization:

* CPU frame mode, GPU frame mode and automatic CPU fallback: sampled library
  frames reach the actual production ingestion adapter and backend request,
  with bounded dispatch, requested/actual PTS and original source identity.
* Clip mode: the library-owned clip is uploaded, retained for backend access,
  released after processing, and never confused with the original source file.
* Clock mapping: a clip-local event at 2-3 seconds with a verified 28-second
  source offset reaches the generator and final response at 30-31 seconds.
  Check explicit caption event times as well as chunk bounds; never double-apply
  the offset. Cover nonzero origins, B-frames, audio/video start differences.
* Global lineage: multiple physical clips preserve original source IDs and
  globally ordered chunk indices despite remote asset IDs and response ordering.
* Cancellation/failure: queued, preparing, uploading, decoding, streaming and
  summarizing phases release their owned resources; upstream failure does not
  generate a success summary. Pending work must not start after cancellation.
* Deferred summarization: stored actual ingestion outputs remain usable after
  all transient videos/assets are released, without repeat ingestion calls.

Live E2E additionally requires real inference and summary endpoints and native
decoding on each supported mode/platform. Existing functional RTVI tests use a
deterministic summary generator; they do not establish live-summary E2E.

Intentional review amendments
-----------------------------

The Astra review of the added integration suite prompted specific corrections:
overlapping observations were moved inside their respective chunk windows;
VLM_MODEL is isolated to make model-discovery failure deterministic; fixture
teardown snapshots/joins queue workers through upload-deletion retries before
closing HTTP clients; mid-SSE cancellation uses a
blocking HTTP byte stream; pending cancellation is verified after a completed
subsequent job establishes queue progress. These strengthen the intended
coverage rather than relaxing assertions to fit implementation behavior.

Before processing implementation, resolve the earlier review findings on clip
clock verification/ownership, worker affinity and cancellation, limiter scope,
batch-buffer lifetime, fallback initialization and sampling semantics. Document
the requirement and rationale for every test amendment. Public API documentation
must state inputs/defaults/units/output shapes/errors/dependencies/resource
lifetimes and include usable examples; tests should inspect signature/schema
structure rather than exact docstring prose.

The preimplementation Astra amendments resolved these specific gaps before
production code started, increasing processing coverage from 145 to 216 cases:

* Factory/open/probe/seek/read/close now require the same worker thread; controlled
  blocking native calls prove close waits safely through repeated cancellation.
* Equivalent and default DecodeOptions share the event-loop/numeric-limit group;
  cancelled waiters never open sessions, and permits recover after failure.
* Recycled-buffer doubles preserve distinct pixels inside each raw batch until
  the next operation, making batch-size>1 ownership checks meaningful. Returned
  Frame pixels are explicitly read-only and separately owned.
* GPU-plus-CPU unavailability requires actionable diagnostics. Availability
  fallback occurs only during open; probe/seek/read errors never restart output.
* Count sampling accepts finite windows with unknown file duration; online FPS
  has an explicit grid distinct from finite RT-VLM spacing. Explicit targets fail
  if unmet; generated count/FPS may coalesce or omit unavailable tail targets.
* Public ClipArtifact construction is borrowed; only materialized owned clips
  clean their file. Consistent packet-hash/timing fixtures establish actual clip
  clock mapping and reject wrong identity/timing/coverage. Kill escalation reaps
  a child that ignores termination. Requested seeks alone never prove offsets.
* Signature/schema tests inspect typed parameters, async behavior, keyword
  defaults, closed/described/versioned wire schemas and actionable optional
  encoder failures in fresh interpreters, without exact docstring-string tests.

Frozen red baseline: 105 failures, 87 setup errors, one pass and 23 opt-in native
skips. These reflected absent runtime/decoder exports and pending packaging
changes. All 216 cases collected cleanly and Ruff passed before implementation.

Follow-up regressions were added intentionally after source inspection/runtime
review, bringing the suite to 229 cases without relaxing existing assertions:

* Five built-in PyNv adapter tests exercise documented NVIDIA APIs, rational
  timebase conversion with positive/negative origins and VFR, owned host RGB
  buffers and compatibility errors. NVIDIA's source proved raw PTS values are
  stream ticks rather than ns; this protects a concrete conversion correction.
* Three regressions require correct FFmpeg input-clock translation, sparse
  completion without unrelated later reads, and an advisory seek to the earliest
  target. Previous value-only/output fixtures did not establish those behaviors.
* Four subsampling modes require immediate awaited decoder closure on early
  aclosing(), rather than eventual async-generator finalization. Repeated clip
  cancellation requires child kill/reap before partial-output removal.

These are explicitly postimplementation bug regressions; only the initial 216
case freeze is claimed as the preimplementation baseline. Mock native APIs
continue to supplement, not replace, opt-in native runner acceptance.

A final root-review regression brings the total to 230: repeated identical video
GOP payloads/timing can match several source positions. Selecting the first hash
match falsely accepted a clip offset in a reproduced failure. Clip verification
must reject ambiguous mapping and remove its output rather than use the requested
seek as evidence. This is an intentional additional failure-path requirement,
consistent with the existing promise to fail on unverifiable source bounds.

Local validation evidence (2026-10-05)
-------------------------------------

With caller-installed OpenCV 4.13.0 in an isolated Python 3.13 environment::

    RUN_VSS_CPU_TESTS=1 PYTHONPATH=src pytest tests -m "not functional" -q

Result: 272 passed, 18 native acceptance cases skipped, two live functional cases
deselected. This includes five real OpenCV CPU checks and the existing
ingestion/summarization HTTP-mocked integration tests. Ruff, new-file formatting,
Pyright and the capability import-direction contract passed. GPU decoding,
external FFmpeg clip/timing acceptance and live inference were not exercised;
the native runner and future adapter integration gates above remain applicable.

Traffic-video acceptance refinement
----------------------------------

The caller-provided sample-sim-traffic.mp4 decoded all 3,913 1920x1080 frames
through OpenCV 4.13.0 on the development Mac, preserved increasing timestamps
and frame ownership after close, and produced eight samples and two PNGs.
CAP_PROP_FOURCC reported h264, but the adapter discarded it. Thirteen additional
probe regressions were written first (four failed, nine passed) before preserving
printable FourCC tags in MediaFacts.codec. Missing, zero, non-finite, out-of-range,
fractional and nonprintable values remain unknown; audio/container remain None.
Codec now explicitly accepts a backend tag rather than promising a normalized
codec name. This fixes an observed metadata omission, without adding dependencies
or changing existing assertions. Processing coverage now collects 243 cases.

The updated suite passed 285 cases with 18 native acceptance skips and two live
functional exclusions; Ruff, formatting, Pyright and import contracts passed.
A separate streaming resource check observed process peak RSS 376.6 MiB after
300 frames and 456.2 MiB after 3,913 frames, cancellation in 2.8 ms and successful
subsequent extraction under the same single-session limiter. These are observations
for this input and environment, not memory-leak/soak guarantees. Full report,
sample PNGs and resource measurements are in ignored demo_videos/processing-results.
External FFmpeg/ffprobe and native GPU acceptance remain unexecuted locally.

External-tool native acceptance (2026-10-05)
------------------------------------------

After caller-installed Homebrew FFmpeg/ffprobe 9.0.2 became available, the existing
CPU, clip and timing acceptance tests passed: 12 native cases, 11 GPU cases skipped.
Actual traffic-video clip checks covered requests 3-5s, 59.5-61s and 128-130.2s,
verified surrounding source bounds, original identity and temporary-file cleanup.
Independent ffprobe inspection found one H.264/yuv420p video stream and no audio.
The whole-video processing demo with --clip also passed; reports are under ignored
demo_videos/processing-results and demo_videos/processing-with-clip.

Three additive native clip tests extend the existing VFR/nonzero-origin/B-frame
fixtures. They decode the materialized clips with ffprobe and compare mapped
presentation timestamps to independently decoded source frames. This checks that
packet verification corresponds to playable, correctly mapped frames, a property
the original no-B-frame clip fixture alone could not establish. Existing assertions
are retained and no production change was needed. Processing coverage is now 246
cases; these additions remain opt-in via RUN_VSS_CLIP_TESTS and RUN_VSS_TIMING_TESTS.
The native timing module passed nine cases, with six GPU-only skips. The full
suite with CPU/clip/timing flags passed 295 cases, skipped 11 GPU cases and
excluded two live functional cases. Ruff, formatting and diff checks passed.
Native GPU,
additional platform/format/color/orientation coverage and live adapter E2E remain
unverified; installing the tools does not automatically enrich public MediaFacts.

Five-minute audio/B-frame acceptance (2026-10-05)
------------------------------------------------

Caller-provided 5min.mp4 has 8,992 1920x1080 H.264 frames at 30000/1001 FPS,
two-frame reorder depth, AAC stereo 48 kHz and a data track. Full Mac CPU decode,
sampling, encoding and full-source materialization passed. Small-window copies
initially failed strict packet identity verification: duration-limited stream
copy admitted a future reference packet while excluding some intervening B-frame
packets. A small generated long-GOP/B-frame/AAC fixture reproduced this failure
before the runtime change.

The fix retains -t for audio and adds a video-only, no-payload-modification packet
filter at the next GOP's presentation boundary. Integer PTS/rational timebase
establish exact relative duration; rescaling through the filter timebase prevents
rounded decimal boundaries from retaining the next keyframe. EOF cuts and all
identity/timing verification remain unchanged. Four native regressions cover
first/second GOPs and zero/nonzero source origins; each checks exact surrounding
bounds and decodes both video and audio. No existing assertions were weakened.
Processing coverage now collects 250 cases.

Real-file clips requested at 3-5s, 59.5-61s, 145-155s and 298-300s verified source
identity/timing, AAC stereo retention, full video/audio decoding and cleanup.
Reports are under ignored demo_videos/5min-processing-results. Optional data
tracks are deliberately not carried by the video/audio clip contract.

Final full regression passed 299 cases, skipped 11 GPU cases and excluded two
live functional cases. Ruff, formatting, Pyright, import contracts and diff checks
passed. Focused stress review found no blockers; its stronger-decode suggestion
was applied via FFmpeg -xerror. All four generated regressions and all four
real-file clips passed strict video/audio decoding after that refinement.

Input acceptance and clean-wheel refinement (2026-10-05)
-------------------------------------------------------

One new PyNv probe regression failed before removing filename-derived container
metadata. The adapter now preserves unknown audio/container, matching the original
contract, without changing existing tests. Nine new opt-in CPU input cases cover
H.264/MP4, HEVC/MP4, VP9/WebM, native portrait pixels, explicit rotation metadata,
corrupt bytes, missing MP4 index and separate delayed-audio/delayed-video starts.
The rotation case reproduced swapped dimensions before disabling OpenCV automatic
orientation; the original schema already promises coded geometry. Independent
FFmpeg -noautorotate RGB oracles retain the same error threshold. Corrupt-input
checks bound permit-recovery time; offset clips compare independently decoded
source/output presentation clocks, retain audio and clean their owned files.

The matrix can derive two-second fixtures from either private sample using
VSS_INPUT_VIDEO; CI defaults to generated testsrc2 and needs neither private file.
It intentionally does not claim every original-file property or damage pattern.
The five-minute source initially exposed full-range VP9 RGB mismatch (mean error
7.285 vs the threshold 3); explicit BT.709 fixtures exposed a broader matrix
conversion mismatch. The new fixture scope was intentionally narrowed to known
limited-range BT.601 SDR/8-bit YUV420, with range/matrix/pixel format independently
checked by ffprobe. No original assertion was weakened and the RGB threshold was
retained. Full-range SDR is not HDR: both it and BT.709 SDR have unresolved native
color-conversion limitations on this OpenCV build.

Two additional opt-in strict expected-failure tests preserve deterministic
mid-gray full-range VP9 and colorful BT.709 H.264 reproductions. Only a dedicated
ColorConversionMismatch satisfies the expected failure; fixture/setup failures
still fail. An unexpected correct conversion fails strictly and prompts review.
Enable with RUN_VSS_INPUT_TESTS=1 RUN_VSS_COLOR_LIMIT_TESTS=1. These are explicitly
postimplementation limitation guards, not part of the frozen preimplementation
baseline. The current processing suite collects 262 cases.

Final full local command enabled CPU, clip, timing, input and color-limit flags:
309 passed, 11 GPU cases skipped, two live functional cases deselected and two
known-color cases expected-failed. A green exit does not resolve those two defects.
Nine input acceptance cases passed using each caller sample as the fixture source.

The actual wheel was built and installed in a fresh temporary Python environment.
Isolated (-I) import/schema round trips succeeded with neither cv2 nor
PyNvVideoCodec installed; probe and auto decode produced actionable typed errors.
After caller-installed OpenCV 4.13.0.92, the installed wheel decoded all 8,992
five-minute frames, sampled/encoded images and produced a verified audio-bearing
clip with cleanup. Imports came from site-packages, not PYTHONPATH/the checkout.
Reports and the full-range failure measurement are in ignored
demo_videos/input-acceptance-results. Native GPU/other-platform and sustained
RSS/VRAM evidence remain separate acceptance gates.

The installed-wheel CPU resource check also decoded a 30-minute 1080p H.264/AAC
input made by repeating the five-minute sample six times: 53,952 monotonically
timed frames, 20 early closures, cancellation and subsequent single-permit reuse.
Sampled current RSS after each five-minute segment stayed at 315.14-315.34 MiB;
process peak RSS was 1,164.56 MiB. Peak native allocation needs investigation
before a firm memory-budget claim. This accelerated ~72-second run checks longer
input handling; it does not replace a sustained 30-minute wall-clock soak or GPU
VRAM checks. The temporary long video was removed; measured results remain in
ignored demo_videos/input-acceptance-results/long-cpu-resource-check.json.

Early-close batch retention correction (2026-10-05)
--------------------------------------------------

Phase profiling identified the initial 1.16 GiB peak during repeated early close,
not during the continuous long-video decode. Each early aclose retained about
48 MiB of raw/snapshot batches until cyclic GC. _decode_stream stored its primary
GeneratorExit exception; its traceback pointed back to the generator frame and
created a cycle. Native capture closure and permit recovery had passed, but did
not prove immediate Python batch release.

A new regression failed before the fix: disable cyclic GC, observe raw-array
weakrefs, retain the caller's independently owned Frame, close six streams and
require hidden batch buffers to disappear immediately. Cleanup now receives only
a boolean indicating a primary failure, preserving error precedence without
retaining the exception/traceback. Two additional guards establish cleanup errors
surface after normal exhaustion, preserve a primary decode error when both fail,
and release capacity for a subsequent operation. Existing assertions remain
unchanged. Focused stress review found no blocker.

The installed wheel was rebuilt/reinstalled and the complete 30-minute input run
repeated with the same 53,952-frame/20-closure/cancellation-reuse workload. Decode
RSS samples were 322.75-322.83 MiB and process peak 332.36 MiB, replacing the prior
1,164.56 MiB peak. The earlier report is preserved as
long-cpu-resource-check.before-memory-fix.json; the current report contains the
rerun. The focused phase profiler also stabilized near 310 MiB across 20 closures.
These observations remove the reproduced batch-retention defect, not the pending
sustained/hardware-specific resource gates.

The processing suite now collects 265 cases. Full enabled local regression:
312 passed, 11 GPU cases skipped, two live cases deselected and two known-color
cases expected-failed. Explicit color diagnostics reproduce (73,73,73) vs expected
(80,80,80) for full-range gray VP9, and mean absolute RGB error 5.959 for colorful
BT.709 H.264. Installed OpenCV's native swscale is 8.3.100, independent of the
user-installed reference FFmpeg 9.0.2. Color limitations remain unresolved;
tracked docs/video_processing_validation.rst records concrete runner gates and
reliable pre-merge correction options.

Final Astra review corrections (2026-10-05)
------------------------------------------

Astra High reviewed the implementation, contracts and tests read-only, and
independently reproduced seven defects missed by the previous green baseline.
Each correction gained a deliberate red regression before its implementation:

* Late-first-target VFR extraction skipped the first eligible frame and fabricated
  source ordinals. A 40-frame fixture with a three-second PTS gap now checks late
  targets and a late window against independently decoded ffprobe PTS/indices.
  OpenCV rewinds and selects from observed PTS in one forward pass. The extra
  prefix decode cost is explicit, rather than claiming reliable sparse time seek.
* CPU metadata invented a zero raw PTS origin. Native VFR/nonzero-origin/B-frame
  probes now require None plus a warning; output timestamps remain source-relative.
* GPU initialization classified source corruption as availability and retained
  partially allocated resources through native error tracebacks. Actual-adapter
  tests inject external native-module failures at demux/construction/metadata;
  corruption must not retry CPU. GC-disabled weakrefs require immediate native
  destruction on the opening worker, including when stop() raises AttributeError.
* Source inspection of NVIDIA 2.2.3 found fatal native CUDA initialization logging
  that can exit the process. Nonfatal stdlib ctypes driver/device/context checks
  now precede decoder allocation. Status-code/ordinal/context tests cover Linux
  and Windows loader paths; public auto/explicit decoder tests assert native
  construction never runs after a failed preflight. These doubles do not replace
  real absent-driver/no-device/GPU runner evidence.
* Clips preceding a delayed audio track were rejected. A native 12-second fixture
  with audio starting at eight seconds checks both an early silent clip and an
  overlapping audio-bearing clip. Lost overlapping audio remains an error, and
  missing timing for any audio track keeps conservative verification. Per-track
  audio packet identity and A/V timing are not independently certified.
* Packet-hash matching copied large slices at every candidate offset on the event
  loop. An adversarial comparison-count guard checks linear KMP matching; mocked
  public materialization checks JSON parsing and mapping run off the loop.
  Existing ambiguous identity/timing rejection remains intact.
* A relative Path containing a colon was mistaken for a URI. A public probe
  regression now checks filesystem resolution and identity without URI parsing.

Existing contract assertions were preserved. The suite now collects 311 processing
cases. Full enabled local verification: 351 passed, 18 gated/nonapplicable native
cases skipped, two live functional cases deselected, and two strict known-color
cases expected-failed; aggregate vss statement coverage is 89%. Ruff, formatting,
Pyright, import direction, lockfile validation and package build checks passed.
Astra's targeted follow-up found no remaining blocker in these corrections.

MR CI now includes a Linux CPU/FFmpeg acceptance job and an isolated installed-wheel
smoke check with declared base dependencies but no native decoders. CI results,
real GPU/platform support and sustained resource gates remain pending; local
verification is not evidence that the newly configured Linux CI job has run.


GPU repair regressions (2026-10-06)
----------------------------------

* Corrected the SimpleDecoder double's metadata to ``average_fps`` and
  ``codec_name``; its wrong DTS timestamps remain modeled. The production adapter
  now uses verified lower-level packet PTS with native display reordering instead.
* Added a mutable-packet/raw-decoder double, delayed EOF flush and recycled host
  surfaces. Public tests cover true indices, nonzero/negative rational origins,
  missing FPS, final EOF/window/target and rejection of missing frame/packet PTS.
  Native timing fixtures, target times and oracle tolerances are unchanged.
* First missing presentation PTS is a timing error, not decoder availability.
  Amended the old first-timestamp auto-fallback expectation accordingly. Removed
  the fake stop-present/stop-missing parameter: PyNv 2.2.3 has no working stop API.
  Destruction-thread and weak-reference assertions remain.
* Added GC-disabled twenty-repeat worker regressions for factory/open/probe/seek/
  read/close, chained errors and repeated cancellation of allocated opening calls.
  A pure category/message failure record crosses the executor, never a native
  exception. Diagnostic strings are forced to exact built-in ``str`` so custom
  subclasses cannot transport native attributes; throwing formatters also have
  a GC-disabled worker control. Existing cleanup/primary-error/permit tests remain
  intact.
* Two CPU color xfails now match the exact documented Mac version and FFmpeg
  library fingerprint, chosen before assertions. Imports remain lazy. Ordinary
  assertions execute on Linux; no pixel tolerance, fixture or oracle was weakened.
* Added independent GPU codec/color identity fixtures. Hardware minimum dimensions
  are respected; unsupported hardware is checked as typed rejection, not skipped
  or counted as a GPU decode. Linux x86_64 PyNv 2.2.3 limited-range BT.709 VP9 is
  rejected before output because its native RGB oracle fails; auto must pass the
  unchanged all-frame RGB/PTS/index oracle via CPU. Full-range VP9 stays on GPU.

Red and green logs, raw native PTS/pixel experiments, native GC-disabled controls,
media acceptance scripts and sustained measurements are retained outside Git in
``/tmp/vss-gpu-repair-20261006``. Private media never enter the patch.

* Concurrent native-work regression: two real facade sessions must serialize
  native calls. The test failed before the process lock; GPU validation additionally
  keeps both sessions live while comparing independent pixels/PTS/indices.

Local patch review and contract amendments (2026-10-06)
-----------------------------------------------------

The supplied repair patch SHA-256 matched
``bda4704857326c68e79786addeae5dce622f05d5ce80ef35117636dca912aa27``.
Before the amendments below, a local rebuild reproduced the GPU-accepted wheel
SHA-256 ``cd28821db0e061fb76eb46b092f75ff182309dab3b3ba5748d458b3674e65770``.
The GPU results above concern that wheel, not the final local candidate.

* The external native-module double now explicitly models Linux x86_64, rather
  than inheriting the review host's ARM architecture. Two negative controls keep
  the limited-range VP9 restriction scoped to the evidenced native platform.
  These controls do not claim native Windows or ARM support.
* Astra identified a missing demuxer API being classified as a decode failure,
  preventing availability fallback. Four public regressions first failed for
  absent/noncallable ``CreateDemuxer`` in auto and explicit-GPU modes. Restored
  the callable availability check: auto falls back, explicit GPU reports typed
  unavailability, and neither allocates a GPU decoder. The native callable path
  is unchanged. The optional native object's annotation preserves type checking.
* The concurrency double now measures constructor, decode and destructor overlap.
  Added a two-worker reset/close regression. A temporary pytest plugin independently
  removed the open/read/seek/close locks; every mutation failed an overlap assertion.
  With the locks intact, all 43 adapter tests pass. No oracle or tolerance changed.

Final local suite: **396 passed, 24 gated/nonapplicable skips, two live cases
deselected, two strict known-Mac-color xfails**; aggregate statement coverage is
90%. The processing suite collects 362 cases. Ruff, formatting, Pyright, import
direction, lockfile checks, build and installed-wheel decoder-free smoke passed.
Astra independently reran the adapter tests and found no remaining follow-up issue.

Final local wheel SHA-256:
``4040cd05f28e0fd74bdf04131099ce8eb0fc15177b4638ae5eb47ef1f7d9b133``.
The compatibility check was a post-report production amendment. GPU confirmation
was pending at this local review; the subsequent committed-wheel rerun accepted
this exact wheel, as recorded in the tracked validation document. The supplied
report's raw runner artifacts were not attached or independently rerun on this
CPU-only host.

Native decoder factory compatibility regression (2026-10-07)
-----------------------------------------------------------

Greptile identified the analogous noncallable ``CreateDecoder`` case: the old
None-only guard converted a TypeError into DecodeError, preventing automatic
CPU fallback. Six new public cases cover absent, None and noncallable factories
in auto/explicit-GPU modes. The two noncallable cases failed before the fix;
the four missing/None controls already passed. The guard now requires callable
API availability, preserving the actual callable path and the existing tests
that forbid fallback for corrupt input. Tests check typed explicit rejection,
the CPU fallback diagnostic and absence of native decoder allocation.

Full local CPU/FFmpeg-enabled verification: **402 passed, 24 skipped, two live
cases deselected, two strict known-Mac-color xfails**. Ruff, formatting, Pyright,
build and isolated installed-wheel decoder-free smoke passed. This runtime guard
amendment produces a new wheel; the earlier GPU report remains evidence for its
recorded wheel, rather than acceptance of this new artifact.
