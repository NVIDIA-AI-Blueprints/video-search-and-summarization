# Exhaustive embedding comparison

These six executable Python scripts compare indexed VSS embeddings with the pinned
Drive reference implementation. Each stage runs independently and consumes files
from the previous stage. The runner is optional. Neither deployment settings nor
loaded models are changed. Exported vectors retain their values; scoring normalizes
copies. Retrieval measures exhaustive scoring against the selected gallery, excluding
VSS candidate selection and additional search filters.

## Setup

Run commands below from this directory. Install dependencies and the VSS CLI from
the same checkout, using Python 3.13 or 3.14 as required by the VSS CLI:

```bash
python -m pip install -r requirements.txt
python -m pip install ../../../../../libs/vss/cli
```

Obtain the three original pinned Drive scripts from the dataset release owner and
place them together in `drive-scripts`. No authoritative public download URL is
available in this checkout. Preserve their filenames and record the exact version;
the adapters record script hashes. The reference stage needs the CUDA, PyTorch,
Transformers, decoding, and other dependencies required by `embed_cosmos.py`; install
those following its release instructions. Evaluation and the network stages run on
CPU. Reference generation can require substantial GPU memory. Supply an immutable
Hugging Face commit SHA as `--revision`.

The external adapters inspect `--help` and fail before invocation if their interface
is unsupported. `embed_cosmos.py` must advertise subset/data/dataset, output directory,
model, revision, frame count, and video/text batch options; hyphen and underscore
spellings and aliases in `generate_reference_embeddings.py` are supported.
It must export `text.npy`, `video.npy`, and canonical `video_ids.json` (optionally
`text_ids.json`). The retrieval script is identified by subset/text/video/output
options and must export `metrics.json` and rankings. The event summary script is
identified by metrics/output options and must export `event_summary.json`. These
contracts need verification against the supplied Drive release; the adapters do
not rewrite its algorithm or preprocessing.

Configure the existing deployment using its exposed origin. Docker example:

```bash
vss configure --base-url http://localhost:7777
vss configure check
```

Helm example (replace with your ingress origin):

```bash
vss configure --base-url https://vss.example.com
vss configure check
```

The remaining commands are identical for both. The standard VSS configuration
loader resolves agent, RT-embed, and Elasticsearch routes. Agent routes may already
include `/api`. The uploader follows the returned upload URL exactly. All configured
routes and returned upload URLs must be reachable from the machine running these
scripts; configure exposed routes before beginning. Route preflight errors identify
missing services. The orchestrator also verifies the selected Elasticsearch index
and explicitly named RT-embed model before uploads. HTTP and HTTPS are supported with normal certificate verification.
Use `--ca-bundle company-ca.pem` for a custom trust store.

Network scripts accept `--auth-config auth.json`, containing service-specific
credential environment-variable references, never literal secrets. Each top-level service (`agent`, `rt_embed`, `elasticsearch`, or `upload`)
contains `bearer_env`, or `headers_env` mapping HTTP header names to environment
variable names, or `username_env` and `password_env`. For example:

```json
{"rt_embed": {"bearer_env": "RT_EMBED_TOKEN"},
 "elasticsearch": {"username_env": "ES_USER", "password_env": "ES_PASSWORD"}}
```

Set those environment variables before invoking the scripts. Do not place passwords
in run configuration.
Reports exclude credentials and sensitive upload URLs. Credential references and
resolved deployment configuration are local inputs; protect the run directory.

Select the Elasticsearch embedding index or pattern from your search profile. Do
not infer it from a deployment type. Set both model identifiers explicitly: Docker
and Helm configurations can load different Cosmos models. Cosmos normally emits
768 components; actual dimensions are validated. Checkpoint parity is unverified
when deployment revision metadata is unavailable. Keep deployment model state
unchanged throughout a run and resume.

## Common contracts

Every script supports `--help` and `--log-level` (default `INFO`), exits zero on
success, and returns a nonzero status on failure. Preserve artifacts after failures
for diagnosis and resume. Metadata uses `schema_version`, `run_id`, and
`subset_fingerprint`; the subset stage establishes the latter two.

Network stages additionally accept:

| Optional flag | Default | Purpose |
| --- | --- | --- |
| `--deployment-config` | Standard VSS loader configuration | Deployment route configuration |
| `--auth-config` | None (unauthenticated) | Service credential environment references |
| `--ca-bundle` | System certificate trust | Custom CA file |

Canonical video identity is the dataset `chunk_id`; remote identity is the recorded
sensor ID. Exact filenames select clips. Gallery and query order follow the original
dataset; query strings are preserved exactly. Query IDs use original unique IDs or
original-row-index sidecars. Duplicate, missing, or ambiguous identities fail.

Each complete bundle contains:

| Artifact | Contract |
| --- | --- |
| `text.npy` | Float32 `(Q, D)` query embeddings |
| `video.npy` | Float32 `(N, D)` clip embeddings |
| `text_ids.json` | Ordered query identity sidecar |
| `video_ids.json` | Ordered dataset clip IDs |
| `bundle.json` | Fingerprint, identities, dimensions, hashes, model/execution provenance |

Both arrays must have compatible dimensions and finite values, with no zero-norm
rows. No failed row is silently dropped.

## 1. Prepare the subset

`prepare_subset.py` needs the dataset release and selected media locally. Create
`clips.txt` with one exact dataset clip filename per line.
The release contains `manifest.json`, `gt/queries_gt.json`, media, and normally
`additional_gt/clips_gt.json`. The latter supplies original gallery records and
ordering, including captions and its `video` paths; the manifest supplies media
hashes. Releases with gallery records directly in the manifest are also accepted.

| Mandatory flag | Meaning |
| --- | --- |
| `--data` | Dataset release root |
| `--video-list` | UTF-8 filename list |
| `--out` | Subset artifact directory |

There are no stage-specific optional flags.

```bash
python prepare_subset.py --data /data/release --video-list clips.txt --out run/subset
```

`subset.json` contains `{meta, gallery, queries}` for the original Drive scripts.
Gallery rows retain metadata and absolute `video_path` values. Only event queries
with a relevant selected clip remain; relevance intersects the selected gallery,
and original order and `near_universal` remain intact. `selection.json` records
ordered IDs, media paths/hashes, original query positions, original/retained labels,
and run/fingerprint metadata. Every requested clip must resolve exactly once and
at least one query must remain evaluable by the original scorer. Missing media,
duplicate filenames, ambiguous records, and empty evaluation fail. Correct the
input list and rerun; changing selection requires new downstream artifacts.

## 2. Ingest the subset

`ingest_subset.py` needs configured VSS agent routes and `requests`.

| Mandatory flag | Meaning |
| --- | --- |
| `--selection` | Selection manifest |
| `--out` | Ingestion directory |

| Optional flag | Exact default |
| --- | --- |
| `--upload-timestamp` | `2025-01-01T00:00:00` |
| `--complete-retries` | `3` attempts |
| `--complete-backoff` | `5` seconds |
| `--resume` | Disabled |

It also accepts the common network flags.

```bash
python ingest_subset.py --selection run/subset/selection.json --out run/ingestion
```

The sequential three-step flow requests an upload URL, posts media using returned
fields/headers, then submits the sensor ID to completion. URL acquisition times
out after 30 seconds; upload/completion after 900 seconds. `ingestion.json` records
clip ID, filename/hash, upload identifier, phase, sensor ID, completion body/outcome,
attempt counts, `chunks_processed`, timings, and errors. Recovery state is saved
before sending bytes and before completion. Success requires every clip to have a
known sensor ID and acknowledged or provisionally acknowledged completion.
`ingestion.private.json` stores complete request bodies with mode 0600 for recovery;
keep it beside the redacted public manifest and exclude it from shared reports.

```bash
python ingest_subset.py --selection run/subset/selection.json --out run/ingestion --resume
```

Resume retries completion for known sensors without uploading completed clips
again. An uncertain upload without a sensor ID requires reconciliation; it is not
automatically re-uploaded. Already-registered responses remain provisional until
collection proves vector availability. No unrelated upload is reused by filename
and no deployment data is deleted.

## 3. Collect VSS embeddings

`collect_vss_embeddings.py` needs agent-independent RT-embed and Elasticsearch
routes, `requests`, and NumPy.

| Mandatory flag | Meaning |
| --- | --- |
| `--subset` | Prepared ground truth |
| `--selection` | Selection manifest |
| `--ingestion` | Ingestion manifest |
| `--es-index` | Embedding index or pattern from the search profile |
| `--model` | Loaded RT-embed model identifier |
| `--out` | VSS bundle directory |

| Optional flag | Exact default |
| --- | --- |
| `--wait-timeout` | `1200` seconds |
| `--poll-interval` | `10` seconds |
| `--vector-field` | `llm.visionEmbeddings.vector` |
| `--sensor-field` | `sensor.id.keyword` |
| `--resume` | Disabled |

It also accepts the common network flags.

```bash
python collect_vss_embeddings.py --subset run/subset/subset.json \
  --selection run/subset/selection.json --ingestion run/ingestion/ingestion.json \
  --es-index video-embeddings --model nvidia/Cosmos-Embed1-224p --out run/vss
```

Exact sensor filters export every selected vector, independent of kNN/top-k results.
Across documents and nested entries, zero matching vectors wait until the deadline;
multiple matches fail. Scores cannot replace inaccessible vectors. Metadata records
model checks, source versus reconstructed values, index/document/vector provenance,
and mapping/settings snapshots. Query embeddings use each exact query string via
`/v1/generate_text_embeddings`, preserving order and checkpointing successful rows.
The complete bundle, `collection.json`, mapping/settings, and request/model provenance
are published only when exactly `N` video and `Q` text vectors validate. Use the same
command with `--resume` after an indexing timeout or a transient query failure.
Correct duplicate indexing or inaccessible vector fields before resuming.

## 4. Generate reference embeddings

`generate_reference_embeddings.py` requires the external Drive embedding script and
its environment. It invokes the original algorithm and preprocessing.

| Mandatory flag | Meaning |
| --- | --- |
| `--subset` | Prepared ground truth |
| `--selection` | Selection manifest |
| `--scripts-dir` | Directory with pinned Drive scripts |
| `--model` | Reference Hugging Face model identifier |
| `--revision` | Immutable checkpoint revision |
| `--out` | Reference bundle directory |

| Optional flag | Exact default |
| --- | --- |
| `--python` | Current Python interpreter |
| `--num-frames` | `8` |
| `--video-batch` | `2` |
| `--text-batch` | `64` |

```bash
python generate_reference_embeddings.py --subset run/subset/subset.json \
  --selection run/subset/selection.json --scripts-dir drive-scripts \
  --model nvidia/Cosmos-Embed1-224p --revision "$REFERENCE_COMMIT_SHA" \
  --out run/reference
```

Outputs include the complete bundle, original sanity metadata, command, script
hashes (including local preprocessing helpers and resources), environment versions,
logs, and timings. Selected media hashes are revalidated before generation. Execution uses a temporary output
directory; publishing occurs only after script success and complete row validation.
Interrupted generation restarts this stage. Fix environment/model/media errors and
rerun. No partially generated bundle should be used for evaluation.

## 5. Evaluate and report

`evaluate_and_report.py` requires NumPy and the original pinned retrieval scorer and
event-slice summary scripts. The Drive scripts may require additional CPU packages.

| Mandatory flag | Meaning |
| --- | --- |
| `--subset` | Common ground truth |
| `--vss-dir` | Complete VSS bundle |
| `--reference-dir` | Complete reference bundle |
| `--scripts-dir` | Pinned Drive scorer directory |
| `--out` | Report directory |

| Optional flag | Exact default |
| --- | --- |
| `--python` | Current Python interpreter |

```bash
python evaluate_and_report.py --subset run/subset/subset.json --vss-dir run/vss \
  --reference-dir run/reference --scripts-dir drive-scripts --out run/report
```

It validates fingerprints/hashes and aligns complete arrays by canonical identity.
The original scorer and event summary run separately for each approach, preserving
metric definitions and exclusions. Outputs retain original metrics, summaries, and
rankings; retrieval comparison CSV contains overall/slice values and
VSS-minus-reference deltas. Per-query text and per-clip video agreement CSVs contain
cosine similarity, raw/normalized L2, both norms, and maximum absolute component
difference, computed in float64. Reference/VSS/difference `(Q, N)` cosine matrices
and JSON/Markdown tables summarize count, minimum, mean, median, fifth/95th
percentiles, and maximum. Empty slices are unavailable. Agreement has no arbitrary
pass threshold. Incomplete bundles fail; repair upstream artifacts and rerun.

## Optional orchestration

`run_embedding_comparison.py` requires all stage dependencies above.

| Mandatory flag | Meaning |
| --- | --- |
| `--config` | JSON run configuration |
| `--out` | Complete run directory |

| Optional flag | Exact default |
| --- | --- |
| `--resume` | Disabled |
| `--dry-run` | Disabled |

Copy `config.example.json` and replace every dataset/model/index/reference value.
Required keys are `data`, `video_list`, `es_index`, `rt_embed_model`,
`reference_model`, `reference_revision`, and `scripts_dir`. Relative filesystem paths
resolve against the config file's directory. Optional snake_case keys correspond to
the documented stage flags: `deployment_config`, `auth_config`, `ca_bundle`,
`upload_timestamp`, `complete_retries`, `complete_backoff`, `wait_timeout`,
`poll_interval`, `vector_field`, `sensor_field`, `python`, `num_frames`, `video_batch`,
`text_batch`, and `log_level`, with the exact defaults in the tables.

```bash
python run_embedding_comparison.py --config config.json --out run --dry-run
python run_embedding_comparison.py --config config.json --out run
python run_embedding_comparison.py --config config.json --out run --resume
```

Dry-run validates local configuration, external script `--help` interfaces, and runs subset validation in a temporary
directory without network requests, uploads, or embedding generation. The runner
resolves deployment configuration once, preflights required services before ingest,
and explicitly forwards the same configuration to network stages. `run.json` records
stage statuses/dependencies, fingerprints, artifact hashes, timings, and error types.
A complete stage is reused only when all dependency and artifact hashes match.
Dataset/media, model, script, or configuration changes invalidate dependent results;
stage-specific fingerprints retain unrelated completed stages. External script
fingerprints include supporting files and resources; generated Python/tool caches
are excluded. Reference model/script
changes rerun reference and reports; RT-embed model/index/field changes rerun collection
and reports without uploading again. Superseded collection/reference/report artifacts
are retained under `.superseded`. The runner refuses to overwrite ingestion recovery
state after upload-affecting dependency changes or missing/modified subset artifacts;
use a fresh run directory. These checks run before replacing subset, deployment,
or run metadata. A nonempty run directory requires `--resume`. The output directory
must be separate from both the dataset and Drive scripts directories: it may
neither contain them nor be contained in either one. Upload recovery belongs to ingestion, indexing
waits/query checkpoints to collection. Success means all stages complete with
consistent fingerprints. Live acceptance requires the same small subset on both
Docker and Helm profiles and a resume that avoids repeat uploads; unit mocks alone
do not establish deployment compatibility.

## Verification

```bash
python -m pytest tests
python prepare_subset.py --help
python ingest_subset.py --help
python collect_vss_embeddings.py --help
python generate_reference_embeddings.py --help
python evaluate_and_report.py --help
python run_embedding_comparison.py --help
```
