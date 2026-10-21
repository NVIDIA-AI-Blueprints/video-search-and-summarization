# Focused search evaluation validation

## Revisions and baseline

The starting head was `eef43af7e6c98eff91803218ce7b6244b3646001`.
`backup/search-eval-before-focused-20261019` preserves that history. The two
commits rebased without conflicts onto `origin/develop` at
`f31e1359a98d77408b123e4e38788425a5b68c5f`; the rebased head is
`8a92e94667bed125cce6b37523e01bd1e6ee7e76`. No rewritten history was pushed.

The clean rebased baseline was tested in an isolated worktree:

- Benchmark scripts: 180 passed, 5 skipped.
- Search core, critic, CLI verification and search memory: 654 passed, 1 expected failure.

The pre-review implementation passed 204 benchmark tests and 673 core/CLI and
preprocessing tests, with 5 skips and 1 expected failure. Deep review identified
additional schema, recovery, and real-release integration gaps despite those
passing tests. The regression coverage below addresses them.

## Review fixes

| Finding | Implemented behavior | Regression coverage |
| --- | --- | --- |
| F01, F05, F08 | Exact attribute scans use deployed nested-vector scope; every page rejects partial results, invalid totals, duplicate documents, and budget overflow; source vectors are excluded. | Core exhaustive-scan and attribute-helper tests, including deployed mapping assertions. |
| F02 | Query IDs distinguish repeated texts across retrieval, evidence, replay, timings, scoring, and resume. Legacy text joins require unambiguous text. | [Evaluation integrity](../scripts/tests/test_evaluation_integrity.py), including the real full release. |
| F03, F04, F16 | Resolve and validate the declared gallery media before cleanup; upload exactly that list; preserve the prior identity manifest until all uploads and indexing checks succeed. | [Runner recovery](../scripts/tests/test_runner_recovery.py) and [gallery ingestion](../scripts/tests/test_gallery_ingest.py). |
| F06, F18 | Atomically checkpoint individual query records; save an aborted artifact on failure/interruption; resume only compatible answered records and rerun corrupt/missing entries. JSON evidence and artifacts publish atomically. | [Runner recovery](../scripts/tests/test_runner_recovery.py) and corrupt replay fixtures. |
| F07 | Index readiness probes disable critic and use cosine floor -1. | Probe argument regression in runner recovery. |
| F09 | Equivalent frame lookups share results; at most 16 lookups run concurrently per attribute lookup batch. | Core attribute-helper concurrency/alignment tests. |
| F10 | Default fusion carries evidence for its selected subject and sensor while preserving ranking math. | Core fusion identity/evidence tests. |
| F11, F12 | Malformed CLI payloads and incorrectly typed decompositions remain explicit failures; invalid replay cannot silently change routes. | [Evaluation integrity](../scripts/tests/test_evaluation_integrity.py). |
| F13, F14, F15 | PAS groups use released pas_tier; failure tolerance applies to eligible queries; explicit offline overrides have defined precedence. | Real-schema integrity and scoring tests. |
| F17 | Critic inference HTTP responses are captured before status/envelope parsing in task-local, bounded evidence; diagnostic failure cannot change a verdict. | Core critic and OpenAI VLM tests, including concurrent candidates and malformed HTTP responses. |

Resume checks query IDs/text, source revision and content hash, effective retrieval
and critic settings, CLI/model identifiers, actual prompt content hashes,
ground-truth file hashes, ingestion identity, and preprocessing provenance. Resume
cannot perform cleanup/re-ingestion or relabel an incompatible destination.
Legacy segment runs need a persistent ingestion manifest to establish identity;
unknown ingestion identity blocks reuse. See [resume usage](flags.md#interrupted-campaigns-and-resume).

## Post-review verification

- Core/search, critic, CLI verification/search memory and CPU preprocessing:
  699 passed, 1 expected failure.
- Complete benchmark suite: 256 passed, with the existing optional tests skipped.
- Final recovery-specific checks: 28 passed after the last hardening changes.
  These also cover latency settings (concurrency,
  timeout, working directory), source/target changes, failed evidence writes,
  incompatible destinations, and corrupt query checkpoints.
- Changed core/CLI/benchmark Python and new tests pass Ruff; whitespace checks
  pass. Modified lines in the two RT-Embed source modules add no new Ruff
  findings; those modules retain unrelated baseline lint debt.

Core/CLI tests use Python 3.14; standalone benchmark tests use Python 3.12.
Inference calls are mocked. Real-reference checks below require no deployment.

## Reference checks

Local released reference files are available under `../embed_debug` relative to
the checkout. The earlier implementation note describing released fixtures as
unavailable was incorrect; the integrity tests now exercise them when present:

- Both the 6,040-query full release and 600-query subset load with their stable
  IDs, including duplicate PAS query texts. Original Drive scoring remains an
  available backend.
- Frame-supported PAS eligibility agrees with the local PhysicalAI reference:
  full 2,316 eligible / 684 excluded; subset 189 eligible / 61 excluded, with
  identical supported-track sets.
- Using deterministic shuffled full-gallery rankings, full AP and nDCG@10 agree
  with the local Drive implementation to `1e-12`; AP@10 agrees with PhysicalAI's
  `min(R,10)` normalization. PAS difficulty groups use actual released tiers.

These checks use real annotations and synthetic rankings. They measure scoring
agreement, not deployed retrieval quality. Portable synthetic fixtures remain
available when the external local release/reference directories are absent.

## Preprocessing and resource bounds

Cosmos file preprocessing remains opt-in via `COSMOS_FILE_INDEX_SAMPLING=true`
and `COSMOS_FILE_TORCHVISION_RESIZE=true`. File retention is bounded by both
`COSMOS_FILE_MAX_RETAINED_FRAMES` (default 1024) and
`COSMOS_FILE_MAX_RETAINED_BYTES` (default 536870912, or 512 MiB, per decoder).
Oversized chunks fail explicitly and must be shortened or run with a deliberate
larger budget. This bounds retained tensor storage; decoder buffers, the next
incoming frame, model memory, and preprocessing workspace also require capacity.
Streaming preprocessing is unchanged.

Sampling preserves endpoints, VFR timestamp alignment, and repeated indices for
short clips. Resize uses bilinear antialiasing on HWC uint8 frames before the
existing processor. CPU tests cover sampling, retained frame/byte failure,
streaming bypass, and resize layout/configuration. Numeric GPU agreement with
the actual processor and measured peak memory/latency remain unverified because
GPU/GStreamer and local processor weights are unavailable. Both preprocessing
controls remain disabled by default.

Record effective ingestion/model/processor settings using
`--preprocessing-provenance settings.json`, and re-ingest before attributing
quality changes to preprocessing. Exact scans exclude source vectors and bound
indexed candidate count; large query/candidate pools still require disk and
runtime capacity planning. Live decomposition prompts include the source
inventory, so model context length should be checked before a full campaign.

## Deployment limits

There is no configured deployment (`~/.vss/config.json` is absent). No controlled
live quality, latency, throughput, memory, or pre-change candidate/critic baseline
was measured. Those checks still require the unchanged released dataset and a
configured stack. No metric improvement is claimed.

Fixed baselines distinguish indexed-vector completeness from annotation-gallery
coverage. Live routing retains the selected path's ordinary budgets and fusion
semantics; its rankings can be bounded. Raw metrics remain primary, and critic
execution is optional. Legacy segment scoring retains its existing
K-before-filter behavior; declared-pool-then-K comparison applies to clip scoring.

Detector/tracker crops and clustered PAS embeddings remain differences from the
released representative-crop protocol. The fixed annotation gallery also differs
from the reference PAS eligible-track candidate gallery; neither is silently
changed using annotation information during retrieval.

## Campaign readiness fixes (2026-10-20)

The subsequent deployment review found gaps not established by the earlier
unit/reference checks. Cleanup now checks the agent's response body and verifies
registration removal. All ingestion strategies verify embedding coverage;
attribute scenarios additionally check live behavior/raw counts and execute a
scoped attribute search. Counts report individual detection gaps without requiring
detections in every clip. Explicit legacy embedding-only runs remain usable
without attribute-index access. Webhook-driven ingestion requires an explicit
acknowledgment of deployed notification settings.

The opt-in Cosmos resize now occurs before frame retention. The subset MP4
sample tables confirm 16 clips with 150 samples at 1920×1080 or 1080×1920,
approximately 889.89 MiB native RGB per clip. Retaining 448×448 RGB instead is
approximately 86.13 MiB. These are arithmetic cache estimates, not GPU peak
measurements. CUDA storage ownership is retained across the asynchronous resize;
streaming and the 512 MiB per-decoder safety bound remain in place.

[Evaluation image instructions](../../../../deploy/docker/developer-profiles/dev-profile-search/EVALUATION_IMAGE.md)
provide a local-source Compose build override and running-image/settings/startup
verification receipt. The [isolated reset workflow](../../../../deploy/docker/developer-profiles/dev-profile-search/EVALUATION_RESET.md)
rejects shared storage, clears retained pipeline state while processing is stopped,
sets explicit retention (default 336 hours), creates upload anchors before CLI
discovery, and verifies zero search documents before and after application startup.
HAProxy now admits the exact clear-scroll DELETE endpoint while retaining other
method/path restrictions.

Verification completed:

- Core search and CLI regression suite: 1,221 passed.
- Runner, deployment, and preprocessing suite: 323 passed, 4 optional skips.
- Final readiness checks after stricter shard/embedding-only handling: 19 passed.
- Final reset/image checks after additional shared-volume safeguards: 34 passed.
- Temporary real HAProxy container: 12 ingress cases passed, including scroll
  release, query-string handling, and refusal of index/document deletion.
- Modified Python lint checks and `git diff --check` passed. Actual search-profile
  Compose rendering passed; no running evaluation deployment was modified.

The local CLI still reports no configured deployment. Building the GPU image,
performing the isolated reset, validating the running image and a full ingestion,
and measuring GPU memory/latency/quality remain deployment-side acceptance work.
No live quality or performance improvement is claimed.
