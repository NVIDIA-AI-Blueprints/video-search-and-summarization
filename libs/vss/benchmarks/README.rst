VSS Library performance validation
==================================

The goal is to measure how library compositions behave relative to existing VSS,
then isolate costs by changing one component at a time. A full video-to-summary
comparison measures the application's work, including differences in aggregation;
it does not by itself measure the overhead of modularity. Component comparisons
need identical input frames/captions, model settings and measurement boundaries.

Portable E2E entrypoint
-----------------------

Use ``run_perf.sh`` for planning, execution and spreadsheet export. It wraps one
Python controller; it contains no cloud provisioning, SSH, container deployment,
credentials, machine names or fixed experiment schedules. Services must already
be running and configured. No Brev account is required.

Live matrix execution requires POSIX process/signal support (Linux or macOS).
On Windows, use ``python -m benchmarks.matrix plan`` or ``export``; the live
``run`` command rejects that platform before creating output or calling services.
Private capture/credential-file diagnostics also require POSIX permissions.
Core library, schema and metric tests remain cross-platform.

Install Python 3.13+, FFmpeg/FFprobe and the workspace benchmark dependencies::

    uv sync --all-packages --group benchmarks
    export PERF_PYTHON="$PWD/.venv/bin/python"
    cp benchmarks/matrix/recipe.toml /tmp/my-perf.toml

Edit the copied recipe: replace model IDs/revisions and video path/duration,
configure endpoint environment variable names, and select workloads. Paths in a
recipe resolve relative to that recipe. The example intentionally contains no
usable service or media defaults. Optionally pin each video's SHA256; otherwise
the controller records its hash before admission and workers verify it again.

For CPU frame workflows, install OpenCV >=4.13. For GPU frame workflows,
install a compatible PyNvVideoCodec >=2.2 and NVIDIA driver/CUDA runtime on the
executor. These platform-specific decoders are not installed by the benchmark
dependency group.

Set the named endpoint/key variables privately in your environment. Do not put
credentials in recipes, command lines, URLs or tracked files. The controller does
not source a credentials file. Set OpenAI-compatible model URLs to their ``/v1``
base; RTVI and normal-VSS URLs identify their service roots.

First inspect the expanded matrix without contacting any services::

    benchmarks/run_perf.sh plan --recipe /tmp/my-perf.toml
    benchmarks/run_perf.sh plan --recipe /tmp/my-perf.toml \
      --profiles all --placements all --repetitions 1 --warmups 0

Run selected cells into a new private results directory::

    benchmarks/run_perf.sh run --recipe /tmp/my-perf.toml \
      --profiles normal-es,library-rtvi-memory,library-rtvi-elasticsearch \
      --placements local-local --streams 1,2 --repetitions 10 --warmups 1 \
      --output "$PWD/benchmarks-results/my-run"

Rebuild its spreadsheet without inference::

    benchmarks/run_perf.sh export --output "$PWD/benchmarks-results/my-run"

Available selectors are ``--profiles``, ``--exclude-profiles``, ``--placements``,
``--exclude-placements``, ``--workloads``, ``--chunk-durations``, ``--frames`` and
``--streams``. Lists are comma separated; profiles/placements/workloads accept
``all``. ``--repetitions``, ``--warmups``, ``--max-trials`` and ``--run-timeout``
override recipe budgets. Unknown or duplicate selections fail before execution.

Matrix and service configuration
--------------------------------

The E2E matrix contains these compositions:

.. list-table::
   :header-rows: 1
   :widths: 24 38 38

   * - Application
     - Caption path
     - Storage
   * - Existing normal VSS
     - Its configured RTVI/VLM pipeline
     - Its existing Elasticsearch integration
   * - Library
     - RTVI service
     - Library memory / file / Elasticsearch adapter
   * - Library
     - Library CPU decoder + frame-VLM client
     - Library memory / file / Elasticsearch adapter
   * - Library
     - Library GPU decoder + frame-VLM client
     - Library memory / file / Elasticsearch adapter

Profile names are ``normal-es`` and
``library-{rtvi,cpu,gpu}-{memory,file,elasticsearch}`` (ten profiles total).
Normal-VSS memory/file storage is outside this recipe: no supported switch has
been established. Library Elasticsearch uses the library's storage adapter,
with a fresh owned index per workflow, and deletes that index after completion.

Each profile can be selected for four model placements, in **VLM–LLM order**:
``local-local``, ``local-remote``, ``remote-local`` and ``remote-remote``. Add
``vlms.remote`` / ``llms.remote`` provider declarations to the TOML to enable
those model roles. A provider declares model, pinned revision, URL/key environment
names and optional engine, precision and generation overrides. Engine/precision
remain ``unknown`` if omitted. Defaults and declarations are not runtime proof.

RTVI is a video-caption service, separate from a frame-VLM endpoint. For each
VLM placement, ``rtvi_url_env`` must identify an RTVI service already configured
with that VLM. CPU/GPU paths use the provider's ``url_env`` directly. A remote
VLM alone does not supply an RTVI service.

Each normal-VSS placement needs its own ``normal.<placement>`` declaration,
pointing to an already configured normal deployment with the selected VLM/LLM.
Changing the benchmark client's URLs cannot reconfigure the normal server.
An optional ``deployment_manifest`` binds this declaration to a hashed JSON
file containing exactly these fields::

    {"schema_version":"perf_normal_deployment.1", "placement":"local-local",
     "vlm_model":"your-vlm", "llm_model":"your-llm",
     "vlm_revision":"pinned-revision", "llm_revision":"pinned-revision"}

Its values must match the recipe. This is deployment evidence supplied by the
operator; it does not prove effective server controls. The CSV marks normal
controls unverified. Normal's processed ES results remain in its shared state
after uploaded-asset deletion. Account for that state growth when interpreting
storage/concurrency runs; clean-state equivalence is not established.

Missing placements/connectors, unsupported configurations and missing local
prerequisites produce explicit blocked rows at admission. A decoder/driver
initialization error discovered later is a failed diagnostic. A request or output
validation failure produces a failed diagnostic. Neither counts as a measured success.
The CPU/GPU library frame backends currently fix sampling at eight frames per
chunk; other frame counts are blocked for those profiles. RTVI accepts a requested
count, but the runner never equates that request with verified inference delivery.

Controlled inputs and timing
-----------------------------

Within a recipe, model roles share caption/summary prompts, requested token caps,
sampling and generation controls. Provider-specific ``generation`` overrides
replace that role's global generation dictionary and are recorded explicitly.
Supported controls are temperature, top_p, seed, enable_reasoning and
reasoning_effort. Choose controls supported by the deployed model; omitted
controls leave provider defaults unknown. Actual output token counts can differ.

Library summary generation currently exposes a token cap but not all sampling
controls. The harness explicitly overlays the declared summary controls at its
HTTP transport and records that intervention. This runs the actual library
pipeline with benchmark configuration, not an unmodified default client. Its
cost is included; complete overhead qualification remains separate. Normal
VSS's internal LLM controls cannot be changed by that overlay and need deployment
configuration and independent verification.

RTVI workflows time upload and lookup through final events/summary. CPU/GPU
workflows time local video through final events/summary. These are different
boundaries; they must not be used as equivalent decoder-only measurements.
Storage setup, FFprobe/source admission, worker startup and cleanup are outside
the operation latency. Stage timers expose captioning, storage write/read,
aggregation/summary and normal-service wall time where visible. Optional
fixed-caption storage replay and CPU/GPU frame-equivalence tools remain available
in the lower-level workflow modules for component investigations.

Frame counts alone cannot qualify equal work: timestamps, pixels, windows,
resolution and actual endpoint consumption also matter. CPU/GPU sampling and
native RTVI termination/delivery remain separate qualification gates. Recipe
``frame_sampling = "shared_duration_grid.v1"`` enables the existing
benchmark FFprobe-duration overlay for CPU/GPU frame paths; the receipt records
that intervention. It does not establish endpoint delivery or fix the native
probe. The default is ``backend_native``. No source
in this runner patches a deployed RTVI service or changes the native GPU probe.

Scheduling, budgets and interruption
------------------------------------

Warmups are separate from measurements. Repeated rounds rotate/reverse cell
admission order; they are balanced counts, not a claim of paired statistical
independence. Concurrent streams use isolated worker processes and a shared
admission barrier. The Batches sheet reports measured overlap, completed batch
throughput and operation span. Host/GPU samples are shared-device observations,
not independently attributable utilization for each stream or model service.

Admission is bounded to 256 cells, eight concurrent workers, 100 repetitions,
five warmups and 5,000 total worker trials, subject to the recipe's smaller
``max_trials``. Media is bounded to 600 seconds, 1920x1080, 256 MiB and 64 caption
windows. The wall deadline stops new work and cancels active workers; bounded
cooperative cleanup follows (up to eight cleanup steps plus 65 seconds for
preflight/client teardown). The actual wall duration may therefore exceed the
admission limit by that cleanup reserve.

These are client/workflow limits, **not a hard model-provider cost cap**:
normal VSS/RTVI internal requests, retries and generated tokens are not fully
visible. Runtime receipts distinguish successful outer completion/owned cleanup
from interrupted or unsettled work. Successful outer completion still does not
prove every remote inference task has settled.

SIGINT/SIGTERM cancels concurrent workers together. Intent files are saved before
service calls; terminal receipts are bound to their intent and source hashes.
A forced termination, invalid receipt or unsettled cleanup stops further
admission. No automatic retry or resume can silently replace interrupted work.
Use a new output directory for a new run. Exports in the finally path retain
partial results; ``export`` can reconstruct them after controller failure.
Exit status is nonzero when any selected trial is blocked, failed or incomplete.

Results and metric schema
--------------------------

The local all-run measurement snapshot is
``benchmarks/results/M8_All_Runs_Master_20261022.xlsx``. Workbooks are excluded
from Git; this file is not included in a fresh checkout and must be shared
separately for review.
Start with Results Overview; Sources and Artifact Links record its evidence
references. This workbook describes the historical measured source epochs,
not live qualification of the newly cleaned runner. Raw captures stay local.

``results.xlsx`` contains Summary, Trials, Matrix, Batches, MetricDictionary and
Run sheets. ``trials.csv`` contains one normalized row per warmup/measurement
worker; ``summary.csv`` contains descriptive statistics per cell in this run.
Keep ``run.json``, intents, receipts and private output captures beside them for
reproduction and review. Spreadsheet exports verify recorded receipt/output
hashes; hashes establish consistency, not independent authenticity. Do not publish
private output captures or configuration without reviewing their content.

The canonical resource definitions are in ``benchmarks/scorecard.py``; the
normalized E2E field inventory is in ``benchmarks/matrix/export.py``. These include:

* Index: application/backend/storage, VLM/LLM model/revision/engine/precision,
  workload/source hash, placement, frames/chunk, chunk duration, concurrent
  streams, repetition, warmup status and source/config identity.
* Timing: operation latency and visible stages; mean/median/p90/p95 by cell,
  completed/incomplete counts, batch throughput and observed overlap.
* Generation: requested controls and caps, witnessed model-call input/output
  tokens and finish reasons. Missing internal normal-VSS metrics stay blank.
  RTVI-reported caption usage
  has an explicit service-reported scope, separate from direct frame-client
  completions and native model accounting. Outer summary usage is not
  substituted for unobserved model calls.
* NVML: GPU utilization, VRAM bytes/occupancy, memory-controller utilization and
  NVDEC utilization, including valid/missing sample counts and availability.
* psutil: worker CPU time/core equivalents/RSS and shared host CPU/RAM. Resource
  means and p90 use lifecycle samples, including possible setup and cleanup;
  resource p90 is not request-latency p90. Sampling intervals scale with the
  workflow/cleanup budget to stay within bounded traces and are recorded in
  each receipt. NVML currently supports one visible GPU.

Unsupported or uncollected metrics remain missing, never zero. GPU utilization
observed on the executor says nothing about a remote model provider's GPU.
Summary rows exclude warmups and never pool software epochs or runs. CSV cells
and XLSX settings prevent model/output text from executing spreadsheet formulas.
Acceptance thresholds remain unapproved; every result stays diagnostic until
quality, equivalent work, measurement overhead and acceptance gates are resolved.

Folder boundaries and historical evidence
-----------------------------------------

``matrix/`` and ``run_perf.sh`` are the reusable E2E front door. ``workflow/``
contains actual pipeline operations, output/frame witnesses and optional controls;
``e2e/`` retains portable two-arm fixture/component checks. Shared resources,
scorecard, identity and media helpers support them. The native decode,
backpressure, offline processing and generic component experiment drivers remain
because CI/wheel checks and component validation use them; they do not provision
services. For their focused interfaces use ``python -m benchmarks.<module> --help``.

The cloud-specific ``remote/`` operators, dated service setup scripts and the
native-only ``suite/`` controller have been retired. Before this cleanup, their
sources and tests were preserved under ignored
``benchmarks-results/cleanup-backup-20261022``. Published historical result
workbooks/decks and frozen source bundles are unchanged. The new runner is a new
source epoch and needs its own live qualification; old measurements do not
validate newly changed code.

For the most recent completed evidence, distinguish **remote executor** from
**remote model**. The prior E2E comparisons ran normal VSS/library on Brev using
models hosted on that same machine. InferenceHub capability probes were separate
remote-model requests, not a completed remote-model E2E matrix. The continuation
added seven E2E measurements, 60 matched storage controls and 40 observer controls.
Those additions belong in the results story; they do not fill the remote-provider
workflow cells. Frozen all-run exports are under
``benchmarks-results/m8-perf-closeout-20261022`` in the parent workspace.

MR delivery and acceptance evidence
-----------------------------------

This MR delivers per-library instrumentation, the repeatable benchmark harness,
and documentation of the local measurement snapshot. It makes no approved performance
pass/fail claim and does not close the entire M8 epic automatically. Jira state
is separate from the evidence below; the M8 tasks were In Progress when reviewed.

The local workbook is the numerical source of truth for the historical runs;
it is excluded from this MR. Start with Results
Overview and Coverage, then the relevant measurement sheet. Sources, Artifact
Links and Field Dictionary preserve provenance and interpretation. Raw model
text, full telemetry arrays and intermediate exports remain in local archives;
links to those local archives require access to the original results directory.
The retained workbook's SHA256 is::

    b41a8093f145f84e7f85cf21626722b25973dc54edf0d71c39666a5f9d0ed618

Completed measurement evidence includes 80 original measured E2E slots across
E1/E2/E3 (33/40/7), plus 24 warmups; the final seven are included in 80. It also
includes 60 repeated fixed-caption storage controls, 40 matched workflow observer
controls, native CPU/GPU sampling-policy experiments and historical concurrency
measurements. A separate 30-minute closed-loop live soak completed 99 operations
in 1807.99 seconds. These are different protocols and source epochs, never one
pooled set of independent trials. Native decoder/observer tests are not evidence
of successful full CPU/GPU frame-caption E2E delivery.

.. list-table:: M8 criterion-to-evidence mapping
   :header-rows: 1
   :widths: 20 45 35

   * - Jira task
     - Delivered evidence
     - Precise qualification limit
   * - `VIA-2936 <https://nvidia.atlassian.net/browse/VIA-2936>`_: corpus/environment
     - Media checksums/admission and environment/source identities; workbook
       Sources, native decode and current E2E sheets.
     - The demo/generated corpus is bounded; it is not a claim of production
       workload representativeness or equivalent hardware across epochs.
   * - `VIA-2937 <https://nvidia.atlassian.net/browse/VIA-2937>`_: baselines
     - Repeated native CPU/GPU, component, E2E and storage/observer measurements;
       explicit resource scopes and separate warmups.
     - Descriptive baselines exist. Counts, missing metrics and environment
       compatibility must be considered before statistical acceptance.
   * - `VIA-2938 <https://nvidia.atlassian.net/browse/VIA-2938>`_: schema/thresholds
     - Versioned metric/run schemas, units, missing-value handling and descriptive
       aggregation with configuration/source identities.
     - Numeric warning/failure thresholds remain deliberately unapproved;
       automatic regression acceptance is not implemented by the matrix runner.
   * - `VIA-2939 <https://nvidia.atlassian.net/browse/VIA-2939>`_: repeatable commands
     - Shell/TOML plan/run/export, retained partial artifacts and prerequisite
       checks; isolated wheel and owned local HTTP workflow tests.
     - The cleaned controller has offline/runtime-fixture verification, not new
       live GPU qualification. Prior cloud runs remain valid for their sources.
   * - `VIA-2940 <https://nvidia.atlassian.net/browse/VIA-2940>`_: processing metrics
     - Probe/decode/extract/encode/materialize instrumentation and native timing,
       cancellation, unit and collector-disabled-path tests.
     - Observer-control experiments exist; complete measurement-harness overhead
       and an approved negligible-overhead budget remain unqualified.
   * - `VIA-2941 <https://nvidia.atlassian.net/browse/VIA-2941>`_: ingestion metrics
     - Selection/progress, chunk, retry, status and token contracts; CPU/GPU,
       RTVI, embedding and multi-backend functional/metric tests.
     - Test coverage and native CPU/GPU timing do not establish service-internal
       frame consumption or complete live embedding/fanout performance coverage.
   * - `VIA-2942 <https://nvidia.atlassian.net/browse/VIA-2942>`_: summarization
     - Aggregation/LLM/event/token/total metrics, real per-request batching and
       empty/single/multiple-batch, failure and sync-wrapper verification.
     - Live single-summary measurements exist. Multiple-batch live performance
       and factual/event-coverage equivalence are not qualified by those tests.
   * - `VIA-2943 <https://nvidia.atlassian.net/browse/VIA-2943>`_: signal lineage
     - Bounded source/pipeline/model/backend/schema context, redaction,
       cross-library correlation tests and hashed run/receipt provenance.
     - External service/chunk attribution and effective remote settings remain
       explicitly unavailable or unverified where no witness exists.
   * - `VIA-2944 <https://nvidia.atlassian.net/browse/VIA-2944>`_: workflow latency
     - Repeated normal VSS and library RTVI workflows with memory/file/library-ES
       compositions; stage decomposition and fixed-caption stored-summary runs.
     - Normal/library aggregation differs. Full CPU/GPU frame-caption workflows
       need delivery/equivalence qualification; task latency is not modularity cost.
   * - `VIA-2945 <https://nvidia.atlassian.net/browse/VIA-2945>`_: shared decode
     - M7 fanout, cleanup/cancellation and shared-frame functional tests.
     - Matched repeated shared-versus-independent decode performance, including
       sessions, I/O, GPU work and memory, is still outstanding.
   * - `VIA-2946 <https://nvidia.atlassian.net/browse/VIA-2946>`_: backpressure
     - Controlled queue bounds, recovery/cancellation, concurrency checks and
       the 99-operation live soak; workbook Soak Operations/Workflow Modules.
     - These completed tests do not prove the specific M7 shared-frame
       queue/lease bounds with one deliberately slow consumer.
   * - `VIA-2947 <https://nvidia.atlassian.net/browse/VIA-2947>`_: reports
     - Retained master workbook with distributions, provenance, coverage and
       missing evidence; reusable CSV/XLSX export and private output captures.
     - Reports are descriptive. A threshold-based regression verdict requires
       approved budgets and compatible, qualified comparisons.

The remaining shared-decode/slow-consumer measurements and acceptance decisions
are explicit open M8 work, not reasons to repeat all completed measurements.
Remote-provider E2E, broader capacity/batching/shared execution and output-quality
review remain additional planned or partially tested comparison coverage. The
four model-placement paths have owned local stand-in routing tests; those tests
are not remote-provider performance results. Larger models may change quality,
but they do not automatically resolve factual or temporal correctness.
