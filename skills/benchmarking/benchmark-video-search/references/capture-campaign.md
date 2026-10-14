# Four-path capture and offline replay

`run_capture_campaign.py` runs 600 ordered queries (350 event, 250 PAS) over an
existing 80-clip PhysicalAI subset. It never ingests or changes a deployment.
The dataset root must contain `gt/queries_gt.json`, `additional_gt/clips_gt.json`,
and the gallery media; the ingestion manifest must map all 80 clip IDs to
completed uploads. Media SHA-256 values are checked before collection.

```bash
python scripts/run_capture_campaign.py \
  --dataset-root /path/to/physicalAI-event-videos-test/subset \
  --ingestion-manifest /path/to/ingestion.json \
  --output-dir /path/to/new-campaign \
  --llm-url http://configured-ingress/llm \
  --llm-model DEPLOYED_MODEL --dry-run
```

Remove `--dry-run` to collect. The LLM URL must come from the deployment's
recorded ingress, and `vss configure show` must identify that same deployment.
`kubectl` read access to `--namespace` (default `vss-search`) is required for
image/configuration identity checks. Dry runs contact neither service nor CLI.

Modes run sequentially: embed, unchanged original query as one attribute,
live routing, then forced fusion. Live routing uses the product prompt,
examples and reasoning helpers, temperature zero and a 16,384-token limit.
Forced fusion references the same saved decomposition; only the retrieval path
changes. Empty attributes stay empty. Model source/time filters are preserved.
All modes use top-K 10, critic count 10, original query, no persistence, no
adjacent merging, cosine threshold zero. Fusion uses RRF (k 60, rrf_w .5,
attribute .55, embed .35, tag 0, confidence .1, no top-percent filtering).
PAS track IDs/resolved attributes are ground truth only. Native annotations map
each relevant clip to its ingested name and [00:00:00,00:00:05) on 2025-01-01.

Each attempt has a unique directory with `request.json` written and fsynced
before launch, binary `stdout.bin`/`stderr.bin`, `process.json`, and (on parsed
success) `response.json` and atomic `complete.json`. The complete response
includes every envelope field and ordered hit, including duplicate clips and
unchanged critic state representations. Binary output is authoritative.
Completion records checksum all evidence files. Disk-write errors stop collection.
Decompositions similarly retain the exact request body, raw HTTP response,
parsed object, model settings and elapsed time. Ground truth, query order,
ingestion mapping, local code/changes and package versions live in `snapshot/`.
Read-only checks before/between modes record source inventory, image IDs,
configuration hashes and index coverage. Configuration values that might include
credentials are represented by hashes rather than copied. No credentials or
process environment are collected.

Use the identical invocation with `--resume` after diagnosing a failure.
Inputs, settings, code and checksums must match. Successful attempts are reused;
intact exit-zero output is reparsed before any new search. Every failed attempt
remains. Attempts without proof of a complete successful response are explicitly
uncertain and may have executed remotely; resume does not promise exactly-once
execution. A lock prevents concurrent writers to one campaign. Search or
LLM errors pause collection, while scoring errors are saved separately and do
not invalidate successful searches. Existing campaigns and reports are never
overwritten. Source/gallery/deployment changes stop collection.

Offline scoring requires no CLI, deployment, LLM or network:

```bash
python scripts/replay_campaign.py /path/to/campaign \
  --report-dir /path/to/new-report --scorer native
```

The report destination must be outside the campaign and must not already exist.
Replay verifies checksums and writes JSON query-level metrics and Markdown
comparison tables for overall/event/PAS. It calls the existing native segment
metric functions. Critic filtering removes only `rejected`, retains missing,
null and unverified states, and does not backfill. Overall metrics average queries
directly. Partial reports show answered/expected denominators and unanswered
query IDs; missing searches never become zero scores. Latency is saved inference
latency, never replay duration. Decomposition timing is separate and remains the
recorded original call duration even when the forced-fusion run reuses it.

The capture guarantee covers inputs and outputs exposed at retrieval depth 10.
Unreturned candidates, internal fusion component scores, embeddings and hidden
critic prompts cannot be reconstructed. Completion requires 2,400 sealed
successful records, stable final inventory, and successful native reports.

## Clip scoring defaults

New campaigns now default to full-gallery collection (up to the CLI's 1,000 raw
hits), verification of all returned hits, and shared clip scoring. Existing
campaigns keep their recorded settings. See [clip-scoring.md](clip-scoring.md)
for metric definitions, coverage limitations, offline replay, and explicit
legacy segment scoring. The original top-10 campaign protocol above remains the
provenance description for previously captured runs.
