# Clip-level scoring (clip-relevance / drive)

Gallery inputs default to `--scoring clip-relevance`; segment datasets default
to `--scoring segment-overlap` for timestamp overlap. Explicit scorer overrides
remain supported. The clip-level backends instead score a
**clip gallery** (the Drive release format): each retrieved hit is resolved to a
gallery clip and relevance is binary. Use them with `--full-gallery` retrieval
and `--gallery-gt` + `--ingestion-manifest`.

## Inputs

`--gallery-gt` accepts either:

- a **release directory** containing `gt/queries_gt.json` and
  `additional_gt/clips_gt.json` (the Drive release layout); **or**
- a merged JSON file with top-level `gallery` and `queries`.

Gallery rows carry `chunk_id`; query rows need unique nonempty `query_id` values,
nonempty `query` text, and eligible `relevant_clip_ids`. Repeated query text is
valid: retrieval, decomposition evidence, replay, and scoring retain each ID.
Older artifacts without IDs can be scored only when their query text is unique
in the release; ambiguous legacy records fail explicitly. `--ingestion-manifest` is the clip→sensor map
recorded at ingest time and must map the gallery exactly (one entry per
`chunk_id`, via `dataset_id` / `sensor_id` / `filename`).

```bash
# Retrieve once (dense: every clip, cosine floor -1, no merge), score later.
--stage retrieve --gallery-gt /path/to/release --full-gallery --skip-ingest \
  --retrieval-out gal.retrieval.json
--stage score --retrieval-in gal.retrieval.json --scoring clip-relevance \
  --gallery-gt /path/to/release --ingestion-manifest ingestion.json
--stage score --retrieval-in gal.retrieval.json --scoring drive \
  --gallery-gt /path/to/release --ingestion-manifest ingestion.json \
  --drive-scorer-dir /path/to/original-drive-scripts
```

`drive` loads the original `evaluate_domain_test.py` from `--drive-scorer-dir`
(fingerprinted) and applies its exact functions for byte-level parity with the
Drive release's own baselines; it requires every gallery clip returned exactly
once. `clip-relevance` uses the vendored pure `ClipScorer`, tolerates partial
coverage, and labels it (`mAP_scope: observed-ranking` vs `full-gallery`).

## Metric definitions (`clip-relevance`)

One pure scorer (`flows/scoring/clip_scoring.py`, version `clips-v2`) serves the
clip-relevance backend. Canonical sensor IDs resolve clips; a known sensor may
carry a VST display filename with an upload suffix, so resolution never guesses
suffixes -- sensor IDs are authoritative. Raw scoring keeps the first
occurrence of each clip; critic-filtered scoring removes explicitly rejected
hits first, then deduplicates (unknown/unverified hits stay). It never
backfills or reorders.

- **AP** = sum of precision at relevant ranks / **all relevant clips**. Observed
  mAP averages this over queries.
- **AP@10** divides by `min(R, 10)` (the standard AP@k definition), with
  contributions limited to ranks 1–10. `clips-v2` makes this denominator
  explicit: a perfect top-10 with R > 10 relevant scores 1.0, not `10/R`. Full AP
  still divides by all R. The output records `scorer: clips-v2` so a reader can
  tell the methodology from a legacy `clips-v1` file.
- **nDCG** uses binary relevance and the full ground truth to build the ideal
  ranking; unreturned relevance gets no gain. nDCG@10 caps both at 10.
- **MRR** = `1 / (first relevant rank)`, or 0 if none.
- **Precision** among returned clips, **recall** over all relevant, **F1**. P@K
  divides by K even when fewer clips were returned; recall divides by all
  relevant. HIT, precision, recall, F1 and nDCG are reported at 1/3/5/10.
- No `full-gallery` mAP claim is made for incomplete rankings. Unanswered
  queries are listed; partial summaries show the answered/expected denominators.

## Frame-supported PAS ground truth (`--pas-frame-supported`)

When released track annotations are present, clip-relevance automatically applies
frame-supported PAS eligibility. `--pas-frame-supported` requires these annotations;
`--no-pas-frame-supported` explicitly keeps the released clip labels. Missing
annotations in automatic mode are recorded as unknown/not applied. This rewrites a PAS query's
`relevant_clip_ids` from the **released positive tracks whose representative
frame actually supports** the query's resolved attributes (PhysicalAI v1.1.2),
read from `additional_gt/tracks_pas_attributes.json` in the release root.

- It is a **scoring-input transform, not a retrieval change**: retrieval runs
  unchanged; only the ground truth the scorer reads is rewritten.
- An attribute requirement is *supported* only when some annotation value
  contains it (directional substring match, whitespace-collapsed,
  case-insensitive). Values in the unknown set (`unknown`, `not visible`,
  `none`, `n/a`, …) can never support a requirement, so a track whose only
  annotation is `not visible` supports nothing.
- Supporting tracks collapse to their **unique clip IDs**; a PAS query whose
  released positives none of which the frame supports is **excluded** and
  listed. Event-domain queries are scored unchanged.
- The derived ground truth drops the original temporal `segments` (they
  describe the released GT, not this derived one).

Requirements:

- the **release-directory layout** (a merged GT JSON has nowhere to put the
  tracks file); and
- PAS queries carry `query_domain: "pas"`, `resolved_attributes`, and
  `relevant_track_uids`.

The score summary carries a `cohort` block -- `protocol`
(`physicalai-pas-frame-supported-v1`), `input` / `eligible` per-domain counts,
and the `excluded` list -- and `queries_excluded`. `config.pas_frame_supported`
records that the derivation ran.

```bash
--stage score --retrieval-in gal.retrieval.json --scoring clip-relevance \
  --gallery-gt /path/to/release --ingestion-manifest ingestion.json \
  --pas-frame-supported
```

`--pas-frame-supported` is **not** valid with `--scoring drive` (the Drive
script applies its own eligibility); the run refuses it rather than silently
ignoring the flag.


## Coverage and comparison reporting

Clip summaries record the input, eligible, excluded, answered, and unanswered
query counts. Event queries follow Drive eligibility: near-universal queries and
queries with no gallery positives are excluded. A missing home clip is recorded
and does not exclude positives elsewhere in the gallery. PAS frame exclusions
remain separate, with their exact query identities and reasons.

Metrics are grouped by query domain, event slice, and released PAS difficulty;
missing metadata is shown as `unknown`. Each scenario's artifact and score summary
carry its scenario identity. Annotation-gallery coverage is measured after
first-occurrence deduplication; saved index inventory is separate evidence.
Complete annotation coverage does not claim all indexed candidates were scored.

Raw metrics remain primary. Critic coverage reports candidate, attempted,
decided, and unverified counts. A normalized placeholder for a missing critic is
not an attempt. Filtered metrics remove only explicit rejections from the saved
candidate pool, retain uncertainty and order, then apply the metric cutoff.
They report relevant/irrelevant clips removed and changes in mAP and mAP@10;
no replacement candidates are fetched. Timestamp, bbox, score, object identity,
and detailed verification diagnostics survive normalization and offline scoring.

Offline scoring preserves the retrieval artifact's unanswered-query tolerance
unless `--tolerate-unanswered` is explicitly supplied, including an explicit
zero. For gallery metrics, this gate counts failures in the selected eligible
cohort. Failures for excluded annotations remain in `excluded_failures` and do
not invalidate complete eligible coverage. Released PAS difficulty comes from
`pas_tier` (`easy`, `medium`, `hard`).
