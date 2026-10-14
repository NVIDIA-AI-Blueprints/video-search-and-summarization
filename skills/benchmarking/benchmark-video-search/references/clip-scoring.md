# Clip evaluation and offline replay

Clip scoring is the default for `run_eval_flows.py`, `run_capture_campaign.py`,
and `replay_campaign.py`. CLI/library/deployment behavior is unchanged.

## New collection defaults

- `--retrieval-depth gallery`: request 1,000 raw hits (the existing CLI limit),
  embedding cosine floor -1, no adjacent merging or top-percent filter, and
  critic verification budget 1,000. Keep the original query for verification.
- Return order is authoritative. Fusion, attribute, and object searches keep
  their own semantics; model-generated source/time filters are not removed.
- Clip evaluation uses concurrency 1 and disables search-result persistence.
  Raw stdout/stderr and complete response envelopes remain in capture files.
- `--retrieval-depth top-k --top-k 10` explicitly requests bounded collection.
  Its default embedding cosine floor is 0. A fresh gallery run uses a 3,600-second
  capture timeout; `--search-timeout` changes it. Timeouts preserve evidence.
- Gallery mode requests the fullest ranking exposed by the current interface.
  It does **not** guarantee exhaustive retrieval: duplicate hits consume the
  limit, and sparse paths or filters can omit clips. Reports include request-cap
  saturation, unique coverage, and whether every gallery clip was returned.
- Critics are best-effort in the CLI. A budget covering every hit is not proof
  that every hit received a verdict; missing/null/unverified verdicts are reported.

## Inputs and online use

`--clip-data` accepts a release directory containing `gt/queries_gt.json` and
`additional_gt/clips_gt.json`, or a merged JSON containing `gallery` and `queries`.
Gallery rows have `chunk_id`; query rows require unique `query_id`, `query`, and
nonempty `relevant_clip_ids`, with optional `query_domain` (`event` or `pas`).
The ingestion manifest's `clips` must map the exact gallery through `dataset_id`,
`sensor_id`, and `filename`. Relevance outside the gallery is an error.

```bash
python scripts/run_eval_flows.py --endpoint "$VSS_AGENT_ORIGIN" \
  --clip-data /path/to/release --ingestion-manifest /path/to/ingestion.json \
  --skip-ingest --fixed-search-path --search-path embed \
  --output-file /path/to/new-run.json
```

The single-flow runner requires an existing ingestion mapping (`--skip-ingest`).
Use `--search-path attribute` for the original query as one attribute. Fusion and
object paths also work with explicit attributes or `--decompositions` as
appropriate. For live decomposition omit `--fixed-search-path` and supply the
recorded deployed `--llm-url` and `--llm-model`; failures do not fall back.
`--dry-run` validates metadata and prints the invocation without executing it.

Online output includes `new-run-capture/` and a separate `new-run-report/`.
The single-flow runner rejects existing destinations. Use `run_capture_campaign.py`
for the resumable four-mode, 600-query/80-clip benchmark. That runner maintains
inventory, index coverage, model/image/configuration checks between modes and
shares saved decomposition records between live-routed and forced fusion.
Existing campaigns retain recorded settings on resume; code compatibility and
sealed amendment checks remain enforced. Resume does not upgrade old top-10 runs.

## Metric definitions

One pure scorer (`flows/clip_scoring.py`) serves online and offline evaluation.
Canonical sensor IDs resolve clips; a known sensor can have a VST display filename
with an upload suffix. Filename-only resolution requires an exact mapped name;
no suffix guessing is performed. Conflicting known identities are errors.

Raw scoring keeps the first occurrence of each clip. Critic-filtered scoring
first removes explicitly rejected hits, then deduplicates. Unknown/unverified
hits stay. It never backfills or changes captured evidence.

- AP = sum of precision at relevant ranks / **all relevant clips**. Observed
  mAP averages this over queries, including successful empty results as zero.
- AP@10 uses the same denominator, with contributions limited to ranks 1–10.
- nDCG uses binary relevance and the full ground truth to construct the ideal
  ranking; unreturned relevance receives no gain. nDCG@10 caps both at 10.
- MRR uses the first relevant observed rank, or zero if absent.
- Precision among returned clips, recall, and F1 describe the observed output.
- P@K divides by K even if fewer clips were returned; recall divides by all
  relevant clips. Report HIT, precision, recall, F1 and nDCG at 1/3/5/10.
- No full-gallery mAP claim is made for incomplete rankings. Filtered metrics
  describe retained output. Unanswered queries are listed; partial summaries
  explicitly show answered/expected denominators.

Reports contain `report.json`, `comparison.md`, `map-summary.md`, `per-query.csv`,
and `native-vs-clips.md` (when native annotations are available). They include
routing, critic verdict coverage, relevant/irrelevant clips removed, duplicate
counts, and recorded inference latency. Event and PAS are reported separately;
total scores average queries directly, rather than averaging domain scores.

## Offline replay and compatibility

```bash
python scripts/replay_campaign.py /path/to/campaign \
  --report-dir /path/to/new-derived-report
```

Replay validates immutable capture checksums, uses only available saved hits,
and requires no network, deployment, LLM, or CLI. The destination must be new
and outside the evidence directory. Search latency comes from captured process
timings, never replay runtime. Reports record scorer/configuration/code hashes
and source campaign identity. Existing 2,400 top-10 records remain depth-limited
when rescored; duplicate removal cannot recover candidates never captured.

Use `--scorer native` (alias `segments`) for historical offline scoring, or
`--scoring segments` for legacy online segment datasets. Such datasets are not
silently interpreted as clip annotations. `--scoring drive` preserves the strict
original embedding-only full-gallery comparison and its historical behavior;
it is a compatibility mode, not the default multi-path scorer.
