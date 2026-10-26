M6 storage and checkpoint tests
===============================

The storage suite makes the public generic adapter contract, typed caption
storage, ingestion checkpoints, and restart behavior reviewable independently.
Its red-test baseline was 136 generic storage cases and 43 domain/API cases.
Those expectations were written before the implementation; change a contract
assertion only when the design decision is recorded in
``docs/persistent_file_storage_plan.rst``.

Test map
--------

``test_adapters.py`` covers the memory/file adapter contract: create and
compare-and-swap writes, missing versus empty snapshots, JSON validation,
detachment, claims, close behavior, configured bounds, and construction.
``test_file_safety.py`` covers file layout, corruption rejection, atomic
publication, path safety, async worker bounds, lock behavior, crash recovery,
and commit faults. ``test_caption_storage.py`` checks caption record ordering,
projection, compatibility, facade ownership, restart summary, and public queue
cleanup behavior. ``test_checkpoint_pipeline.py`` checks real public VLM request
construction through mocked HTTP, source/request fingerprints, resume, empty
windows, durable publication, and failure before repeated inference.
``test_public_api.py`` checks exports, configuration validation, custom adapter
implementation, and optional-dependency import boundaries.

``test_lock_deadline.py`` covers file operation lock timeout configuration,
environment selection, a slow write beyond the original five-second deadline,
post-lock revision validation, expiry without mutation, and cancellation with
single-worker reuse and retained job ownership. It uses real local OS locks and
advances only the adapter's retry clock for the slow-write regression.

``test_lock_lifetimes.py`` verifies idle memory locks are released without forced
GC while active claims and contending operations share one lock. File lock paths
remain stable by design; pruning them requires a fully quiescent store.
``test_source_hashing.py`` verifies complete content hashing and cooperative
cancellation after an in-flight read, including repeated cancellation and file
closure.
``test_immutable_identity.py`` checks opt-in trusted-provider schemas, no-content-
read resolution, queue snapshot isolation, stat/size drift, cached checkpoint
replay and summary, mode/revision conflicts, cancellation and sanitized failures.
``test_bounded_file_locks.py`` verifies pool high-water reuse, exact-key claims,
unsupported pre-release and mixed-layout rejection, bounded initialization crash
debris, occupied metadata corruption, process death, multiprocess CAS and FD
cleanup. Lock timeout/fairness tests hold actual slot ownership. The worker-bound
test identifies admitted writers instead of assuming they start in submission
order after registry retries.

``test_rtvi_persistence.py`` also checks that the original caller identity survives
reopening storage after the uploaded asset is deleted, while caption wire identity
remains unchanged. Caption-storage regressions cover legacy origin-metadata
upgrade and conflicts that preserve the existing snapshot.

The tests use temporary roots and mocked VLM/summary endpoints. They require no
API key, GPU, external endpoint, or live inference service. They deliberately
check actual mocked request URLs and payloads, not just that a fingerprint was
computed. The ``functional`` marker is for separately opted-in live service
tests; it is not needed for the storage suite.

Storage is opt-in for existing ingestion calls. The example below selects a
file root explicitly; ordinary calls without storage configuration continue to
use the existing in-memory behavior. Elasticsearch is now an optional adapter;
the package storage README gives configuration and same-host claim requirements.
This example is file-specific and accepts local paths only; it does not download
HTTP media or migrate checkpoints into ES.

Elasticsearch contract and native acceptance
--------------------------------------------

``test_elasticsearch.py`` establishes the ES adapter contract using a stateful
client double: native CAS conditions/races, checksummed and checksum-valid corrupt
payloads, read/write limits, non-retrying client views, lossless index guards,
uncertain acknowledgments including SDK exceptions, asynchronous codecs,
same-host subprocess claims/process death, owned/injected client lifetime, and
public ingestion resume plus deferred summary. These are deterministic tests;
the SDK-specific serialization case skips when the optional client is absent.

``test_elasticsearch_native.py`` requires an explicitly opted-in disposable ES
server and ``vss[storage]``. It creates UUID-scoped indices and deletes only those
indices, never a wildcard or a pre-existing index. Its six cases cover actual
client/server realtime reads, CAS/restart, concurrent writes/index creation,
fresh-process retrieval, inference failure/checkpoint resume/deferred summary,
and config-selected checkpoint replay with decoding forbidden::

    RUN_VSS_ES_TESTS=1 VSS_STORAGE_ES_URL=http://127.0.0.1:9200 \
        python -m pytest tests/storage/test_elasticsearch_native.py -q -ra

For an authenticated cluster, set ``VSS_STORAGE_ES_API_KEY`` and optional
``VSS_STORAGE_ES_CA_CERTS`` in the caller environment. Set
``VSS_STORAGE_ES_TEST_INDEX_PREFIX`` to an approved disposable prefix; no dotenv
loader is provided. Tests need permission to create/read/write/delete these
dedicated indices and inspect their mappings/settings.

The GitLab native ES job passes ``elasticsearch`` before its ``-E`` arguments;
the image entrypoint does not accept bare flags as its command. It requests a
per-job bridge network through ``FF_NETWORK_PER_BUILD=1``. The Docker runner's
``[runners.docker].network_mode`` setting must be unset: host networking conflicts
with service links and does not provide the ``elasticsearch`` bridge alias.
Remove that runner setting or run this job on a service-capable runner. Increasing
fixture readiness timeouts cannot fix a crashed service or runner network setup.

The supplied ``byin-dev`` runner log confirms this configuration conflict:
``host type networking can't be used with links`` during service health-check
container creation. The flag is already enabled; changing it again cannot
override the pinned runner network. The runner owner must remove the setting
from that runner's ``config.toml`` (not replace it with ``bridge``), reload the
runner configuration and rerun the job. If other jobs require host networking,
use a separate runner configured for service networking and its actual tag.
Do not change the test URL to localhost or skip native tests as a workaround.
The ES job now requests the ``docker`` tag advertised by ``server1`` (runner
1268177), instead of ``os/linux``. Tags select every eligible matching runner;
they do not pin a runner ID or prove its executor/network configuration. To
select only server1, assign it a unique tag and use that tag on this job.

The job first runs ``.github/scripts/check_es_service.py`` against its disposable,
unauthenticated ``elasticsearch:9200`` service. It reports DNS resolution, socket
connection failures, HTTP status, and verified Elasticsearch identity without
printing response bodies or credentials. It saves ``es-service-check.json`` as
an always-uploaded artifact and fails before pytest when the service is absent.
This diagnoses infrastructure; it does not skip or replace native acceptance.
``test_es_ci_service.py`` covers these categories, startup retries and expiry.
If preflight fails, expand the runner's Docker executor preparation section and
inspect the Elasticsearch container's startup/health-check logs. A printed
``FF_NETWORK_PER_BUILD:true`` flag alone does not prove the bridge was created;
GitLab requires the runner's ``network_mode`` setting to be unset.

Local native acceptance passed with ES 8.19.3/Python client 8.19.3 on macOS.
Both supplied videos additionally passed actual CPU decoding with mocked VLM
and summary against native ES: three traffic chunks, five five-minute chunks,
eight JPEG images per chunk, preserved original identity and zero extra inference
on completed replay. Production TLS/RBAC, multi-node failover, older ES versions,
Windows local claims and cross-host takeover remain separate acceptance gates.

The combined M6/ES nonfunctional suite passed 834 tests in both source and
installed-wheel environments, with 72 optional checks skipped and six live
tests deselected. The 70 deterministic ES checks and six native ES checks passed.
Fresh-process native reads assert the same imported package path as the parent
and preserve isolated Python mode for wheel validation.

The immutable-provider and reusable-slot follow-up additionally covers default
checkpoint fingerprint compatibility, no-content-read replay, provider identity
conflicts, slot protocol process death/CAS, initialization crashes and typed
release failures. Primary operation/body errors and cancellation survive cleanup
failure. The new slot protocol has native macOS evidence; Linux/Windows matrix
acceptance remains required. Slots are the only supported lock protocol;
pre-release per-key and mixed roots are rejected, with their old files preserved.
No online inode deletion is added.

An explicit ``StorageConfig(backend="memory")`` or injected
``InMemoryStorageAdapter`` takes precedence over storage environment variables
and keeps results only in that adapter's process lifetime. Unset
``VSS_STORAGE_BACKEND`` means no persistence by default. The example's file
adapter is deliberately explicit and durable.

Runnable local-video example
----------------------------

Install the ingestion and summarization extras and the decoder/runtime required
for the chosen machine. Load the ignored ``.env`` into the caller's environment
using its existing trusted environment manager; this example does not parse
``.env`` or add a dotenv dependency. For the InferenceHub configuration, map
``INFERENCEHUB_TOKEN`` to both API-key variables without expanding or printing
the token, and set the compatible endpoint/model values::

    export VLM_BASE_URL=https://inference-api.nvidia.com/v1
    export VLM_MODEL=nvidia/qwen/qwen3.8-27b
    export VLM_API_KEY="$INFERENCEHUB_TOKEN"
    export VSS_SUMMARIZATION_BASE_URL="$VLM_BASE_URL"
    export VSS_SUMMARIZATION_MODEL="$VLM_MODEL"
    export VSS_SUMMARIZATION_API_KEY="$INFERENCEHUB_TOKEN"
    export VSS_SUMMARIZATION_API=chat_completions

The example never prints credentials, and deterministic tests do not require
or read them. The live Qwen run completed the full traffic video: five chunks
were durably stored, then a separate process aggregated seven events and made
a real summary request to the same model. Earlier HTTP 429 capacity failures
were recovered with the same job ID through checkpoint resume. The ignored
``demo_videos/m6-results/live-report.json`` retains captions and summary output.
This proves endpoint compatibility, not independently measured caption accuracy.

Ingest a local video into an explicit durable root. Use a stable source ID and
pipeline revision, plus a concrete decoder. The command prints the generated
job UUID and source ID before it waits, so retain them even if inference fails::

    python examples/persistent_video.py ingest /data/video.mp4 \
        --storage-root /data/vss-store \
        --source-id camera-17-video-2026-10-08 \
        --pipeline-revision vlm-deployment-2026-10-08 \
        --decoder opencv

If ingestion fails after confirming some chunks, retry with the same video,
source ID, decoder, model, pipeline revision, and reported job UUID, and add
``--resume`` to the ingest command. Use ``--resume`` only when a checkpoint was
written; if failure occurred before the first checkpoint, retry without it. The
source file must remain immutable for the full attempt; the library hashes local
content for identity checks. A new
process can summarize stored captions without the video or VLM endpoint::

    python examples/persistent_video.py ingest /data/video.mp4 \
        --storage-root /data/vss-store \
        --source-id camera-17-video-2026-10-08 \
        --pipeline-revision vlm-deployment-2026-10-08 \
        --decoder opencv \
        --job-id 0a2cfa79-fb5a-4ca9-880f-5722fa8a5fa1 \
        --resume

    python examples/persistent_video.py summarize \
        --storage-root /data/vss-store \
        --job-id 0a2cfa79-fb5a-4ca9-880f-5722fa8a5fa1

The summary command only needs the storage root and durable job ID. For direct
access to stored captions, ``await AdapterChunkStore(storage).get_chunks(str(job_id))``
returns the detached ordered response list. Both commands close their adapters
through an async context manager; ingestion also waits for ``job.wait_closed()``
before leaving the context so canceled or failed work has finished cleanup.

``pipeline_revision`` is the caller's revision for endpoint behavior, backend
defaults, mutable model weights, and native decoder builds. Change it whenever
one of those can change outputs; the library cannot discover a compatible
revision for remote weights or caller-installed decoder binaries. The local
source must remain immutable throughout ingestion, not only while its content
hash is calculated.

Run the deterministic suite with::

    pytest tests/storage

The native decoder demonstration is separate from mocked storage acceptance.
It requires a caller-installed decoder; ``pynv`` requires compatible NVIDIA
hardware and libraries. A live endpoint call happens only when a user runs the
example with configured credentials. The tests do not read those credentials
or invoke live inference.

CPU integration evidence
------------------------

The deterministic local run used OpenCV to decode the supplied traffic clip
and five-minute video, then sent the real JPEG frame payloads through the
OpenAI-compatible backend with ``httpx.MockTransport``. The mocked endpoint
returned typed captions; no remote inference or credentials were used. Both
jobs completed (3 and 5 chunks), persisted source nanosecond bounds and first/
last flags, and replayed with zero additional VLM requests. A separate process
summarized each job with OpenCV and PyNvVideoCodec imports blocked.

One complete typed observation snapshot took approximately 0.005, 0.013, and
0.081 seconds for 1, 100, and 1,000 captions, with JSONL sizes of 1,237,
93,857, and 941,662 bytes respectively. These are single-write measurements;
they do not measure cumulative checkpoint rewrites or a long-running memory
soak. Windows file-lock/durability behavior remains pending.

The subsequent cumulative file-checkpoint benchmark used synthetic successful
captions and the actual checkpoint codecs/CAS writes in fresh installed-wheel
processes. It wrote an initial frozen plan, one snapshot per confirmation, then
a complete manifest, and verified reopening and decoding the checkpoint::

    Captions   Commits   Seconds   Cumulative bytes   Final bytes   Peak RSS bytes
    1          3         0.020     4,743              1,881         39,649,280
    100        102       1.302     6,653,168          110,926       43,515,904
    1,000      1,002     76.377    656,635,980        1,119,831     82,362,368

Cumulative bytes count encoded snapshot payloads actually committed, excluding
filesystem journaling/lock traffic. The elapsed time includes encoding, writes,
fsync, final validation and reopening, without video decode or inference. Peak
RSS is the process high-water mark, not retained heap. These local APFS results
confirm the documented write amplification; they do not establish ES throughput
or a sustained memory-soak bound. Configured record/byte limits are covered by
the contract tests. Incremental commits remain an implementation follow-up.

File-first validation before the ES follow-up: 640 nonfunctional tests passed
with CPU checks enabled;
57 checks were skipped and 6 live tests deselected. Native processing acceptance
passed 350 tests, with 24 platform/GPU skips and the two documented Mac color
expected failures. A clean installed wheel passed 631 nonfunctional tests with
66 optional-native skips; its imports resolved from site-packages with
PYTHONPATH unset. A separate base-only environment passed file/memory storage
and deferred summary smoke checks without OpenCV, PyNvVideoCodec, HTTPX or OpenAI.
Ruff, formatting, Pyright and import direction checks passed.

Additional regressions cover bound plan/grid alteration, serializer exclusion
for transient cached frames, off-loop domain codecs, cancelled queue waiters,
RTVI wire errors and cleanup precedence, operation lock fairness and shutdown
across retry intervals. JSON nesting is limited to 256 levels. These checks
retain the original assertions; the fairness fixture was corrected to block
only its holder's fsync, so the unrelated writer remains a meaningful oracle.
Submission-time settings include absent credentials/model configuration: adapter
constructors cannot re-read later environment changes for a queued managed job.

Review amendments
-----------------

The approved contract now distinguishes source identity from durable job UUID,
captures effective backend/model/decoder settings at submission, rejects
resume for transient memory storage, and preserves the awaitable job lifecycle
with ``wait_closed()``. Optional transport imports remain outside storage and
contract-only imports. A complete observation is reconstructed from ordinal
records, and failed/incomplete/corrupt data cannot be summarized as a successful
partial result. These are deliberate API expectations, not implementation
details that tests should silently relax.

Post-rebase processing integration
----------------------------------

The main-branch API separation is preserved: ordinary ingestion dispatches to
RTVI/custom whole-request backends using VSS_INGESTION_BACKEND, and local frame
inference lives in ingestion.vlm. Stored local jobs use the same ingestion-owned
loop and concurrency limiter through private structural checkpoint callbacks.
The existing checkpoint tests change only relocated import/monkeypatch targets;
the provider-error case without checkpoints now uses transient memory storage.
The private processing primitive retains custom-backend error-response behavior.
Confirmation, replay, corruption, cancellation and secret-sanitization assertions
are retained. test_rebase_processing.py adds environment selection, permit
contention/cancellation, completed replay and fractional-window regressions.

Rebase acceptance (2026-10-09, main f19f0e7): the source checkout and an isolated
installed wheel each passed 847 non-functional tests (72 opt-in skips and six
live-service deselections). The two native CPU checkpoint/resume/summary cases
passed against both source and wheel. Elasticsearch 8.19.3 passed 70 deterministic
contracts plus six native source checks, and six native installed-wheel checks.
Ruff, formatting, Pyright, all three import contracts, lockfile consistency and
the base-only wheel smoke check passed. This is local Mac acceptance; it does not
replace the Docker runner or GPU/platform gates in CI.

Windows test portability (2026-10-09)
-------------------------------------

Windows CI at 247ccfe exposed seven test failures: one locale-dependent child
stdout decode, one POSIX-only path assertion, four timer tick-count assertions,
and a same-size mutation that did not produce observable file metadata drift.
The amendments preserve the contracts: exact Unicode equality uses explicit
UTF-8 at both process boundaries; the home default uses Path equality; the stat
drift test explicitly advances mtime and verifies that it changed.

The four off-loop cases use a shared worker gate instead of timing a 2ms
heartbeat. It asserts a distinct worker thread, then requires the event loop to
observe and release the still-blocked operation. Failure cleanup releases and
gathers the operation. A temporary negative control that executed storage work
inline caused all four tests to fail immediately on the thread assertion. No
production code, skips, or expected-failure markers changed. All seven cases
and the full source suite passed locally; native Windows acceptance requires
rerunning the Windows CI job.

Shared ARM runner watchdog (2026-10-09)
---------------------------------------

The ARM job at 4c076e9 failed only the concurrent replacement integrity test:
its 10-second outer watchdog canceled registry backoff before the adapter's
30-second lock deadline expired. A temporary 250ms registry-I/O delay reproduced
the same timeout locally; the unchanged operations completed with a 60-second
watchdog. The test retains all 14 replacements, 35 reads and per-read revision,
record-count and generation assertions, and additionally checks the final
revision and records. Dedicated lock-expiry and cancellation tests retain their
original budgets. No production locking code changed; ARM CI must rerun.
