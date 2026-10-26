Video processing validation gates
=================================

This document records evidence still required before claiming Phase 1 support
across the advertised operating systems, decoder backends, and sustained workloads.
It combines build-specific observations with outstanding release gates. There
is no automated 30-minute wall-clock soak runner in this repository. Record
commands, versions, raw measurements, failures, and skipped cases for each run.

Current platform evidence
-------------------------

The original development environment is macOS with CPU decoding. Local evidence includes
OpenCV 4.13, a clean installed-wheel import without native decoder packages,
five-minute H.264/AAC decode and clip checks, independent timestamp checks, and an
accelerated 30-minute input run. The accelerated run took about 72 seconds and is
not a wall-clock soak. Its early-close memory retention has since been reproduced
and fixed; sustained Mac wall-clock validation is still pending. Consult the README
and executable test ledger for exact local results and limitations.

The first run peaked at 1.16 GiB while repeated early closures retained frame
batches until cyclic garbage collection. A regression reproduced this with GC
disabled. Replacing the exception reference with a boolean failure flag removed
the traceback cycle. The clean installed-wheel repeat decoded 53,952 frames,
performed 20 early closures and cancellation/reuse, sampled 322.75-322.83 MiB RSS
and peaked at 332.36 MiB. This fixes the observed retention; it does not establish
a universal memory ceiling or replace sustained CPU/GPU testing.

.. list-table:: Platform acceptance
   :header-rows: 1
   :widths: 25 20 55

   * - Platform / backend
     - Status
     - Required evidence
   * - macOS / OpenCV CPU
     - Observed locally
     - Supported release environment, clean wheel and sustained resource checks.
   * - Linux / OpenCV CPU
     - Observed on Ubuntu 22.04/H100 runner
     - Installed-wheel CPU, clip, timing and input gates; see dated record below.
   * - Linux / PyNvVideoCodec GPU
     - Observed on H100/PyNv 2.2.3
     - Build-specific PTS/RGB/lifetime checks; remaining codec/platform gates below.
   * - Windows / OpenCV CPU
     - Pending
     - CPU, clip, timing and input acceptance gates.
   * - Windows / PyNvVideoCodec GPU
     - Pending
     - CPU gates plus GPU PTS/RGB, fallback and VRAM checks.

Do not mark a row complete from a different operating system or backend. A skipped
test is unverified. A backend being available does not establish its codec support
or color accuracy.

Linux H100 GPU repair record — 2026-10-06
-----------------------------------------

The canonical ``feat/video_processing`` checkout remained at
``ebf2a8c905a3632e40ebf0c02a77d1ea5dc51d56``. Implementation and tests ran in a
disposable ``fix/video-processing-gpu-acceptance`` clone under ``/tmp``; no
canonical repository or media writes, push or merge were performed. Local source
revision/patch and complete runner evidence are recorded in the external report.

Runner: Slurm 7323103, gpu-h100-0304, one NVIDIA H100 80GB HBM3 (compute
capability 9.0), Ubuntu 22.04.4 x86_64, driver 580.82.07, Python 3.13.15,
NumPy 2.5.3, PyNvVideoCodec 2.2.3 and opencv-python-headless 4.13.0.92.
OpenCV reports avcodec 62.11.100, avformat 62.3.100, avutil 60.8.100 and
swscale 9.1.100. External FFmpeg/ffprobe are the BtbN static GPL build
``N-127222-g151814650f-20261006``. The native package does not expose its
runtime/Video Codec SDK version through the previously documented attributes;
``nvidia-smi`` CUDA 13.0 is driver compatibility, not proof of linked runtime.

Original wheel SHA-256:
``67ad30e433bd1e47c19138489ea3396d4b1a0a2bc1ce2b3298aee4181b3250cc``.
Original installed-wheel native result: 292 passed, 16 failed, three intentional
OpenCV-only metadata skips. Fourteen GPU cases stopped at missing FPS; two CPU
strict XPASS cases reflected successful Linux RGB conversion.

Candidate wheel SHA-256:
``cd28821db0e061fb76eb46b092f75ff182309dab3b3ba5748d458b3674e65770``.
It was imported from
``/tmp/vss-gpu-repair-7323103/candidate-env/lib/python3.13/site-packages/vss/video_processing``
with ``PYTHONPATH`` unset. Decoder-free schema import preceded installation of
the caller's native packages. Candidate video-processing acceptance: **352 passed,
three intentional OpenCV metadata skips**, including added regressions. Ordinary
full nonfunctional suite: **353 passed, 62 opt-in native skips, two live-service
cases deselected**. Ruff, formatting, Pyright, import direction, wheel build and
installed dependency checks passed. The full nonfunctional suite with all native
flags enabled also passed: **412 passed, three intentional skips, two live cases
deselected**. No live VLM or inference endpoint was used.

The adapter uses verified lower-level ``CreateDemuxer``/``CreateDecoder`` packet
PTS, native display reordering and one terminal zero-length flush packet. Native-only
CFR/VFR/late-gap/nonzero-origin experiments matched every decoded-frame PTS and
pixel identity against independent ffprobe/FFmpeg oracles. Packet storage is reused;
host RGB copies precede the next decode. Sparse selection reads forward from source
start with true ordinals. Native duration/frame count are unknown rather than used
as an unverified exclusive end; finite count sampling requires a caller window.
No scanned metadata, FPS-derived timestamps, external timing tool dependency or
native time-to-index seek was substituted.

Both private H.264 videos were streamed completely with explicit ``pynv`` and
``auto`` actually on GPU: 8,992 and 3,913 frames, true source ordinals and **zero
nanoseconds maximum error** against integer ffprobe presentation timestamps.
Nine adjacent early/middle/late RGB identities per video were matched independently;
maximum per-frame RGB MAE was 0.1756 / 1.4107, below the unchanged 5.0 tolerance.
The five-minute input has B-frame reordering; the traffic input has no B frames.
Sparse duplicate/unordered requests, finite-window count/FPS sampling, PNG/JPEG
encoding and a verified H.264/AAC clip's timeline/audio/cleanup passed. Fixture
nonzero-origin and delayed-audio clip checks remain separate native coverage.

With cyclic GC disabled, twenty native allocated failures per stage passed across
factory/open/probe/seek/read/close. Native decoder weakrefs were supported and
cleared after each operation; alias destruction occurred on its owner thread.
Raw native controls ran twenty times. VRAM stayed at 520 MiB through all 120
failures; RSS increased from 174.73 to 176.03 MiB. Fresh immutable category/message
records cross the worker boundary, with native traceback/cause/context references
detached there. Exact built-in diagnostic strings prevent custom string subclasses
from transporting native attributes. The payload-bearing subclass regression failed
before the additional one-line fix; twenty-repeat own-thread and throwing-formatter
controls passed after it. Two earlier partial soaks used superseded wheels; the
required full soak below passed against the final hash above. Successful-stream
checks included twenty early closes, twenty
cancellations, twenty repeated extracts and reusable permits. A separate barrier
held two actual GPU sessions live together for twenty GC-disabled repetitions,
checking four independent RGB/PTS/index frames from each source per pair, owning
threads and native weakrefs (last live/closed VRAM: 636/522 MiB). The strengthened
pixel check exposed intermittent RGB MAE near 15 before serialization. Exact
2.2.3 source uses shared CUDA constant matrix/offset state and releases the GIL
around Decode. Stream synchronization did not fix it and is a no-op in this host
configuration; host copies are already synchronous. A process RLock now covers
native open, decode/copy, reset and close. Both sessions coexist, but native work
is serialized; GPU concurrency does not imply decode throughput scaling. The process lock covers
this backend, including different devices; it cannot coordinate unrelated direct
PyNv calls outside the adapter. A new
concurrent-work regression failed before the lock. Bounded source excerpts and
archive SHA-256 are retained in ``pynv223_concurrency_evidence.txt``.
Twenty additional repeated cancellations gated an in-flight worker read on a real
native session. Cancellation waited for that operation before owner-thread close;
native weakrefs cleared, VRAM stayed at 520 MiB and a single permit was reusable.
The gate holds the worker operation; it does not claim interruption inside a C++
CUDA call. An exact-wheel media run first failed the external lifecycle RSS-span limit:
the single-session warm-up did not cover its later first mixed-session workload.
RSS rose 64.97 MiB, then leveled off. The failure is retained. A rerun warmed both
workload types with eight mixed pairs, with GC disabled and the same 64 MiB/32 MiB
limits. All media checks passed; measured post-warm RSS span was 0.0078 MiB and
VRAM span 0 MiB. Warm-up samples are retained too. Independent twenty-pair
concurrency data and native weakref release support a bounded workload high-water
effect; the precise allocator mechanism is not established. These observations
are not a general memory ceiling.

The final installed-wheel soak passed **1,810.28 monotonic seconds** (30 minutes
10 seconds), from **20:01:59 to 20:32:10 UTC**, after functional acceptance.
Cyclic GC stayed disabled. **131 cycles** performed two full/partial streams,
early close, repeated cancellation and sparse duplicate extraction per cycle:
655 measured operations, with 761,627 main stream frames (761,807 including
180 warm-up stream frames; auxiliary frame outputs are not counted). Traffic
was decoded fully 131 times; the five-minute input fully 27 times and through
its first two seconds 104 times. Pixel arrays were streamed and discarded.
Post-operation RSS ranged **292.49–302.23 MiB**; VRAM stayed **520 MiB** and
thread count stayed four. Second-half RSS growth was **7.87 MiB**, VRAM growth
0 MiB, within the unchanged 64/32 MiB limits. Internal sampled peak RSS was
325.71 MiB. The independent two-second monitor collected 871 samples across
the timed interval: peak RSS 325.42 MiB, live VRAM 520–580 MiB. Both elapsed
records and complete memory series are retained in ``results/soak*.json*``.
These measurements establish this exercised workload on this wheel and runner;
they do not establish a universal memory ceiling or other platform support.

Independent 320x192 8-bit YUV420 fixtures passed every RGB/PTS/index oracle on
GPU for H.264 BT.601 limited range, H.264 BT.709 limited range, HEVC BT.709
limited range and VP9 BT.709 full range. Limited-range BT.709 VP9 on Linux x86_64
PyNv 2.2.3 failed the raw native RGB oracle (MAE 6.5736). The adapter now rejects
that exact version/format tuple before output with a color-conversion diagnostic;
automatic mode passed all twelve unchanged frame oracles via OpenCV. This is
**validated CPU fallback, not GPU color support**. Native color enums were
verified on limited/full-range fixtures (BT_709=1, MPEG=0, JPEG=1).

H100 reports no AV1 capability; explicit GPU rejection passed. This OpenCV build
also failed to decode the AV1 fixture, so AV1 CPU fallback is not claimed. Initial
160x96 HEVC/VP9 fixtures were below the reported hardware minima (144x144 and
128x128); their failures are retained as invalid-fixture evidence and the valid
matrix uses 320x192. Generic native dimension errors still do not trigger fallback.

Hidden-device and invalid-ordinal checks ran in isolated processes: explicit GPU
selection produced typed unavailability, while auto returned OpenCV frames and
recorded the reason. Physical driver absence, Windows, other native builds,
HDR/10-bit and additional codec/resolution/color combinations remain unverified.
The documented Mac CPU color defects remain open on that exact binary. Their
strict xfails now match the version, OS and all four linked-library versions;
other builds execute ordinary unchanged RGB assertions before classifying outcomes.

Evidence outside Git: ``/tmp/vss-gpu-repair-20261006``. Relevant files include
``REPORT.txt``, ``repair.patch``, ``final-lock-only-checks.log``,
``raw_experiment.log``, ``lifetime_final.log``, ``concurrency_final.log``,
``media_final_warm.log``, ``cancellation_final.log``, ``wheel_identity.log``, ``soak.log`` and retained JSON/JUnit/wheel artifacts.
Private media payloads are excluded from Git and report artifacts. Scratch/native
allocation cleanup is recorded in the final external report.

Local review after the supplied GPU report
-----------------------------------------

The supplied patch hash was verified, and its initial local rebuild reproduced
the accepted ``cd28821...`` wheel hash above. Astra's follow-up found one
compatibility regression: absent/noncallable ``CreateDemuxer`` prevented automatic
CPU fallback. Four red regressions preceded restoring the callable availability
check. Valid native API execution is unchanged. Native double platform controls
and construction/reset/destruction serialization assertions were also strengthened;
independently removing each native operation lock made those assertions fail.

The final local candidate wheel SHA-256 is
``4040cd05f28e0fd74bdf04131099ce8eb0fc15177b4638ae5eb47ef1f7d9b133``.
Local CPU/FFmpeg-enabled full nonfunctional verification: **396 passed, 24 native
or nonapplicable skips, two live-service cases deselected, two strict known-Mac
color xfails**, with 90% aggregate statement coverage. Ruff, formatting, Pyright,
import direction, lockfile, build and isolated installed-wheel native-free smoke
checks passed. Astra independently reran all 43 adapter tests with no follow-up
finding. Intentional test amendments are recorded in the test suite README.

The earlier supplied Linux GPU results and soak certify the earlier accepted
wheel. Confirmation of the final candidate was pending at this local review;
the subsequent installed-wheel rerun below closes that gate. Raw runner artifacts
were not attached; local review verified the supplied patch and reports, without
independently rerunning GPU acceptance on this CPU-only host.

Final committed-wheel GPU acceptance — 2026-10-06
------------------------------------------------

The supplied updated-wheel report records acceptance of committed revision
``9add3a39d48d9b168f75b6c8a0248576f4269317`` on ``feat/video_processing``.
Its wheel SHA-256 is
``4040cd05f28e0fd74bdf04131099ce8eb0fc15177b4638ae5eb47ef1f7d9b133``,
matching the final local candidate. A disposable copy of the commit was built and
installed into a fresh environment with ``PYTHONPATH`` unset. All 32 installed
VSS files matched the wheel before acceptance, before soak and after soak;
decoder-free import/schema smoke passed before native packages were installed.
No runtime changes were required.

Runner: Slurm allocation 7326734, node gpu-h100-0204, NVIDIA H100 80GB,
driver 580.82.07, Python 3.13.15, PyNvVideoCodec 2.2.3,
opencv-python-headless 4.13.0.92, NumPy 2.5.3 and Pydantic 2.13.5.
External FFmpeg was BtbN ``N-127222-g151814650f-20261006``.

* Native processing acceptance: **359 passed, three intentional OpenCV-only
  metadata skips**.
* Ordinary repository regression: **360 passed, 62 native opt-in skips, two
  live-service cases deselected**.
* Full repository regression with native opt-ins enabled: **419 passed, three
  intentional skips, two live cases deselected**, with no failures or errors.
  These suites overlap; their counts must not be added.
* Ruff, formatting, Pyright, import direction and installed dependency checks
  passed. The known native VP9 rejection/CPU-fallback oracle passed; this remains
  fallback acceptance rather than GPU color support for that combination.

Twenty-one independent real-media checks passed. Explicit GPU and automatic
selection decoded all 8,992 and 3,913 frames of the supplied videos, with zero
nanosecond error against independent presentation PTS. Automatic selection
actually used GPU. Nine early/middle/late RGB frames per video had maximum MAE
0.175551 and 1.410629 respectively, below the unchanged 5.0 threshold. Sparse
targets, ownership, sampling, transfer, clip/audio offsets and isolated hidden
device/invalid ordinal behavior passed. Small CFR/VFR/late-gap/nonzero-origin
fixtures independently matched presentation timestamps and pixels.

With cyclic GC disabled, 120 allocated-native failure checks, twenty in-flight
read cancellations and twenty concurrent session pairs confirmed native release
on the owning worker. Warmed mixed lifecycle RSS span was 0.183594 MiB, with
zero completed-operation VRAM span. Direct-native controls were retained.

The wall-clock soak lasted **1812.041758 seconds**, from 22:25:17Z to 22:55:29Z,
with 131 completed cycles and 761,807 counted frames including warmup. First/last
cycle RSS was 233.867188/237.656250 MiB; completed-cycle VRAM stayed at 520 MiB.
Workload peak sampled RSS was 320.457031 MiB. Independent two-second monitoring
recorded 874 samples and transient VRAM up to 580 MiB, returning to 520 MiB at
every completed cycle. Unchanged latter-half endpoint growth checks passed;
temporary host allocations mean their endpoint difference is not a fitted memory
trend or a universal memory-budget guarantee.

The report records unchanged canonical source/media, exported results before
scratch deletion, and deliberate release of the allocation after successful
validation. Raw wheels, logs and scripts were not attached locally; the supplied
report identifies them under the exported ``runner/results`` artifact tree.

**The final committed wheel passes the reported GPU gate for MR preparation**
on the pinned Linux/H100 configuration. Physical driver absence, Windows/other
architectures, other native builds, HDR and untested codec/color combinations
remain unverified; the documented Mac color defects remain open. Subsequent
runtime changes require validation of the resulting wheel.

Post-acceptance factory availability guard — 2026-10-07
------------------------------------------------------

Greptile's noncallable ``CreateDecoder`` finding was reproduced with two failing
public regressions before changing the None-only check to a callable check.
Four absent/None controls already passed. Automatic mode now falls back for this
API availability failure, while explicit GPU mode raises DecoderUnavailableError;
valid callable execution and corruption error classification are unchanged.

Local verification: **402 passed, 24 native/nonapplicable skips, two live cases
deselected, two known-Mac-color xfails**. Ruff, formatting, Pyright, build and
isolated installed-wheel decoder-free smoke passed. New wheel SHA-256:
``c9a70dd36d904bc1f5d1f8528698b58224f2b42a02757168c7768577a48ba479``.
The GPU acceptance above remains tied to the earlier ``4040cd05...`` wheel.
Native confirmation of this new artifact is pending; no new GPU or soak result
is claimed from local tests. Test intentions are recorded in the suite README.

Runner record
-------------

For every runner, capture these details with the test output:

* OS release/build, architecture, Python version, package revision, built wheel
  filename and SHA-256, and whether the test imported the installed wheel or the
  source checkout.
* CPU model and core count; for GPU runs, GPU model, compute capability, driver
  version, available VRAM, and CUDA/runtime versions reported by the installed
  PyNvVideoCodec build.
* OpenCV exact version and build information; PyNvVideoCodec exact version; and
  ``ffmpeg -version`` / ``ffprobe -version`` output including enabled decoders,
  encoders, filters, and libraries.
* Exact command, environment flags, test counts, skips, expected failures, test
  input identity/metadata, and all failures. Keep private media out of reports;
  use a stable redacted label and record its codec/container/stream properties.

Clean-wheel and native acceptance
---------------------------------

Build and install the actual wheel in an isolated environment for each platform.
First verify import and schema use without OpenCV or PyNvVideoCodec installed;
then add only the caller-selected native prerequisites and exercise the installed
wheel. Do not let ``PYTHONPATH=src`` shadow the installed package during the
installed-wheel checks. Record both phases and the wheel hash.

Run ordinary tests and native CPU, clip, timing, and input acceptance on each CPU
runner. Missing dependencies are intended to fail when a corresponding opt-in
flag is set:

.. code-block:: bash

   PYTHONPATH=src pytest tests -m "not functional" -q
   RUN_VSS_CPU_TESTS=1 RUN_VSS_CLIP_TESTS=1 RUN_VSS_TIMING_TESTS=1 \
   RUN_VSS_INPUT_TESTS=1 PYTHONPATH=src pytest tests -m "not functional" -q

Run native GPU decode and timing acceptance on each GPU runner as well:

.. code-block:: bash

   RUN_VSS_GPU_TESTS=1 RUN_VSS_TIMING_TESTS=1 PYTHONPATH=src \
   pytest tests/video_processing/test_native.py \
          tests/video_processing/test_native_timing.py -q

Commands above use POSIX shell environment syntax. In Windows PowerShell set
the corresponding flags explicitly, for example:

.. code-block:: powershell

   $env:RUN_VSS_GPU_TESTS = "1"
   $env:RUN_VSS_TIMING_TESTS = "1"
   $env:PYTHONPATH = "src"
   python -m pytest tests/video_processing/test_native.py tests/video_processing/test_native_timing.py -q

Use the same ``$env:NAME = "1"`` form for CPU/clip/input flags. Preserve the
opt-in behavior; do not set GPU flags on a CPU-only runner and call that a pass.

The runner record must show PyNvVideoCodec, driver, GPU and FFmpeg build versions
for this command. Confirm the test actually selected the GPU backend; a green run
that only exercised CPU fallback does not count as GPU acceptance. Exercise
automatic selection with a usable GPU and with GPU initialization unavailable,
confirming the reported backend/fallback reason, and exercise explicit backend
selection to ensure it does not silently switch.

For every claimed backend/platform pair, review results for these contracts:

* **Timestamps:** compare decoded presentation timestamps with independent
  ffprobe results for constant-rate and variable-rate inputs, nonzero timestamp
  origins, B-frame reordering, and audio/video stream start differences. Confirm
  nanosecond conversion uses source time bases rather than an average-FPS estimate
  where real PTS is available.
* **Pixels and geometry:** confirm owned, contiguous, read-only CPU RGB arrays
  remain valid after decoder closure, dimensions preserve coded geometry, and
  color results are checked against an independent oracle. The current verified
  pixel fixture scope is limited-range BT.601 SDR, 8-bit YUV420. Full-range SDR
  VP9 and BT.709 SDR have documented unresolved OpenCV mismatches; their strict
  expected-failure checks must remain visible and must not be counted as accurate
  color support. HDR/10-bit and untested formats remain unverified.
* **Codec/container coverage:** record each format and codec actually exercised
  separately for OpenCV and PyNvVideoCodec, including unsupported or failed
  combinations. Include the generated input matrix and representative caller
  media where licensing and privacy permit. A successful probe alone does not
  establish successful full decode, accurate color, or clip support.
* **Fallback and cleanup:** verify availability fallback happens before output,
  carries a reason, and recovers the concurrency permit. Verify read failures do
  not restart on another decoder. Verify early iterator closure/cancellation
  closes the native session after any active native call, then allow another
  session to open. For clip operations, verify cancellation reaps FFmpeg and
  removes partial output, and normal context exit removes the owned artifact.

Use the exact environment flags documented in
``src/vss/video_processing/README.md`` and
``tests/video_processing/README.rst``; the color-limit expected failures require
``RUN_VSS_INPUT_TESTS=1 RUN_VSS_COLOR_LIMIT_TESTS=1``. Report expected failures
separately from passes. Do not weaken them to obtain a green summary.

Sustained resource validation
-----------------------------

Run a wall-clock soak separately from accelerated long-input decode. The latter
checks handling of a long timeline quickly; it cannot establish stable resources
over 30 minutes. No fixed RSS/VRAM budget is defined here. Capture a time series
and investigate unbounded growth or unreleased resources before making a release
claim.

For each supported CPU and GPU backend, use a representative local video of at
least 30 minutes. Record codec, resolution, frame rate mode, audio layout, file
size and duration. Run both:

* **Single session:** stream through the entire video with bounded batches; do
  not collect all frames in memory. Record start, periodic, peak, end-of-decode,
  post-close and post-cancellation RSS. For GPU, also record VRAM at those points.
* **Concurrent sessions:** run one session per intended concurrency level (at
  minimum one and more than one), using independent bounded streams. Record each
  session's lifetime and combined RSS/VRAM series. Keep the configured
  ``max_concurrency`` and actual active-session count in the report.

Sample process RSS and GPU memory periodically throughout each run (for example,
every 30 seconds) and at these checkpoints: immediately before decode, shortly
after opening, at regular intervals, at peak, after normal close, after an
intentional early iterator close, after cancellation during active decode, and
after a subsequent successful run on the same event loop. Include sampling
interval, sampling tool, units, process identity, and any other workloads on the
machine. On GPU, capture ``nvidia-smi`` process and device memory, not only total
system GPU use.

Repeat the early-close and cancellation cases enough to reveal a trend (at least
20 iterations is a useful starting point, not a passing threshold). Between runs,
verify another stream can acquire the concurrency permit and that native memory
returns or stabilizes after cleanup. Separately run clip creation, consumption,
and cancellation; confirm the artifact/subprocess is cleaned up. Preserve raw
time-series data and note any allocator caching or delayed driver reclamation
rather than interpreting one peak number as a leak or proof of safety.

Color-conversion diagnosis and pre-merge work
---------------------------------------------

Video generally stores luminance/chroma (YUV), requiring a range and conversion
matrix to obtain RGB. Full-range versus limited-range describes how brightness
values are represented; BT.709 specifies a different matrix from BT.601. Wrong
range handling changes brightness/contrast, and a wrong matrix changes colors.
These are 8-bit SDR cases, independent of HDR/10-bit support.

The installed OpenCV 4.13.0 backend reports FFMPEG, with native avcodec 61.19.101,
avformat 61.7.100, avutil 59.39.100 and swscale 8.3.100. The separate reference
FFmpeg executable is 9.0.2. A constant full-range VP9 gray frame returns RGB
(73, 73, 73) through OpenCV versus (80, 80, 80) through the independent reference.
The BT.709 colorful H.264 fixture's mean absolute RGB error is 5.959, above the
unchanged threshold of 3. Reports are in ignored
``demo_videos/input-acceptance-results/color-diagnosis.json``.

Inspection of `OpenCV 4.13's conversion code
<https://github.com/opencv/opencv/blob/4.13.0/modules/videoio/src/cap_ffmpeg_impl.hpp#L1773>`_
points to the older explicit swscale context initialization, which configures
pixel format/size but does not propagate the frame's color range/matrix. This is
the likely native cause of the observed differences. The conversion occurs before
the Python adapter receives BGR pixels; swapping BGR to RGB cannot correct it.
Disabling conversion does not expose a supported complete planar YUV frame through
this OpenCV path, so it is not a safe shortcut to Python-side color conversion.

There is no requirement to postpone a fix until after the MR. Reliable options
are to validate a corrected OpenCV native build, or implement a color-aware CPU
decoder behind the existing modular session contract. Both need the same native
pixel/PTS/selection/ownership/cancellation tests, including the two discovered
color cases. Installing a newer external FFmpeg executable alone does not change
the libraries linked into the caller's OpenCV wheel. A newer native build is a
candidate fix, not an established guarantee until the regressions pass.

Do not apply an approximate post-conversion RGB correction as the fix: incorrect
conversion can clip values, which cannot be recovered reliably afterward. The
two strict expected failures remain unresolved on that documented Mac build
and must not count as color-correct acceptance there. Linux outcomes are recorded
above with the unchanged oracle assertions. Before claiming those inputs are supported accurately, implement and
validate the chosen conversion/decode path; intentionally amend the known-failure
markers only after the corresponding unchanged oracle assertions pass.

Release boundaries
------------------

Final local review: 351 tests passed with aggregate vss statement coverage of 89%,
18 native/nonapplicable cases skipped, two live cases deselected and two known-color
expected failures. Astra High independently reproduced and reviewed corrections
for late VFR selection, GPU initialization/fallback/lifetime, delayed audio clips,
off-loop packet verification, unknown CPU raw origin and colon-containing Paths.
The executable test ledger records the red-first guards and follow-up review.

MR CI now configures a Linux native CPU/FFmpeg acceptance job plus decoder-free
installed-wheel smoke checks. The pipeline has not been run from this local
environment; the dated local Linux/H100 record above does not certify Windows or other builds. In particular,
test real CUDA-driver absence, a visible driver with no usable GPU, unsupported
codec/resolution and allocated-then-failed initialization. The ctypes preflight
guards source-observed fatal native initialization logging; doubles cannot certify
native process survival, thread-affine release or hardware compatibility.

Processing is currently an independent library. The existing ingestion and
summarization integration tests exercise their HTTP-mocked whole-video workflow;
they do not exercise local frames/clips through a production VLM adapter. A future
adapter and live processing-to-inference-to-summary E2E are separate gates and
must not be claimed by these processing runner results.

Phase 1 platform claims require completed rows above, clean-wheel evidence, native
acceptance for each claimed backend/platform pair, and reviewed sustained resource
traces. Keep unresolved format/color cases, skipped GPU tests, and peak-memory
investigations explicit in release notes until their evidence changes.
