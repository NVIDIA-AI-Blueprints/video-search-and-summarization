# Flags

The flags that change what a run means, and the ones that do not.

## Querying

`--query-flow cli` is the only backend today.

Runs `vss search run <path>` as a subprocess per query. Before the first query
the script resolves the CLI, runs `vss --version` to catch a broken install,
and points `~/.vss/config.json` at this deployment.

Live decomposition routes each query by default. Dataset-provided
decompositions are used when live decomposition is disabled or unavailable;
`--decompositions` explicitly replays a stored set. Without any decomposition,
every query uses `--search-path` (default `embed`).
`--no-decompose` disables only the live LLM; it still replays stored routes.
For a true single-path baseline, use `--fixed-search-path`, which ignores
dataset routes and cannot be combined with `--decompositions`.

Any non-zero exit **aborts the run**. There is no exit code meaning "this query
failed but the rest are fine" — continuing would just manufacture zeros that
read as a retrieval collapse.

### Merging

Upstream merges contiguous same-sensor windows by default and reports the mean
of their scores. That changes the number of scored segments — the denominator
of precision — so this script passes `--no-merge-adjacent` by default to stay
comparable with the REST baseline. `--merge-adjacent` opts back in.

## Pipeline stages

A run is three stages — **ingest → retrieve → score** — connected by on-disk
artifacts. `--stage {run,ingest,retrieve,score}` runs one and stops; `run` (the
default) chains them in memory and is byte-identical to the old behaviour.

The load-bearing artifact is the **retrieval artifact**: the raw hits plus the
run context, written by `--stage retrieve` (or a default run given
`--retrieval-out`). `--stage score --retrieval-in <artifact>` re-scores it
**without touching the deployment** — the same retrieval can be re-scored under
a different `--scoring` model or different flags. This is how you iterate on
metrics without re-querying.

`--ingest-out` / `--ingest-in` carry the ingest manifest between a standalone
ingest and a later retrieve. `--endpoint` is required for every stage except
`--stage score`, which is fully offline.

## Clip-level scoring (clip-relevance / drive)

`--scoring` selects the metric model. `segment-overlap` (default) scores
timestamp-overlap against segment ground truth. `clip-relevance` and `drive`
score a **clip gallery** (the Drive release format) instead of segments.

Both clip models need `--gallery-gt <release>` (a dir with `gt/queries_gt.json`

+ `additional_gt/clips_gt.json`, or a merged `{gallery, queries}` json) and
`--ingestion-manifest` (the clip→sensor map recorded at ingest). Retrieve for
them with `--full-gallery` (dense: every clip, cosine floor −1, no merge), then
score from the artifact. `drive` additionally needs `--drive-scorer-dir` (the
directory holding the original `evaluate_domain_test.py`, which it loads for
byte-level metric parity). `drive` requires every gallery clip returned exactly
once; `clip-relevance` tolerates partial coverage and labels it
(`mAP_scope: observed-ranking`).

`--pas-frame-supported` (clip-relevance only) rewrites PAS queries'
`relevant_clip_ids` from the released positive tracks whose representative frame
supports the resolved attributes (PhysicalAI v1.1.2); it needs the release-
directory layout (`additional_gt/tracks_pas_attributes.json`). PAS queries
with no supported positive are excluded and listed in a `cohort` block. See
`references/clip-scoring.md` for the metric definitions and the PAS derivation.

---

## Flags not covered here

This document explains the flags that change what a run *means*. The rest are
tuning and escape hatches -- retry budgets, readiness timeouts, explicit service
URLs, preflight skips -- and are described by `--help`:

```
--min-cosine-similarity  --source-type          --decompositions
--upload-timestamp       --complete-backoff     --complete-retries
--readiness-timeout      --skip-readiness-wait  --skip-vss-preflight
--skip-vss-configure     --vst-port             --vst-url
--vss-origin-port        --vss-base-url
```

Reach for them when a default is wrong for your deployment, not routinely: each
one moves a run further from the baseline everything else is compared against.

## Cookbook

```bash
# Everything: ingest + routed CLI queries
--dataset warehouse

# Query only
--dataset warehouse --skip-download --skip-ingest

# Use a local dataset instead of DSS
--data-dir ~/Desktop/vss-eval-datasets --dataset warehouse --skip-download

# Isolate this dataset by deleting foreign sources (explicit and destructive)
--dataset warehouse --only-dataset --confirm-delete

# Fresh index, wipes everyone's data (destructive)
--dataset warehouse --clear --confirm-delete

# Default/shared box — do not touch others' data
--dataset warehouse --skip-existing

# One fixed path instead of routing (baseline only -- not an eval)
--fixed-search-path --search-path fusion --attribute "person wearing a hardhat"

# Replay per-query routes stored in an extended dataset, without a live LLM
--no-decompose

# Ask the critic the reconstructed question, as runs before 3.8.0 did
--no-original-query

# The older agent-mediated ingest, when deployed VIOS webhooks are off
--ingest-flow agent-3step

# Longer wait for webhook-driven perception to populate the index
--index-probe-attempts 20 --index-probe-backoff 30

# Name the results file, for comparing two runs deliberately
--dataset warehouse --skip-download --name embed-baseline

# See what would run, touching nothing
--dry-run

# Retrieve once, score later -- and re-score without re-querying
--stage retrieve --skip-ingest --retrieval-out run.retrieval.json
--stage score --retrieval-in run.retrieval.json

# Clip-level scoring against a gallery (dense retrieve + clip/drive metrics)
--stage retrieve --gallery-gt /path/to/release --full-gallery --skip-ingest --retrieval-out gal.retrieval.json
--stage score --retrieval-in gal.retrieval.json --scoring clip-relevance --gallery-gt /path/to/release --ingestion-manifest ingestion.json
--stage score --retrieval-in gal.retrieval.json --scoring drive --gallery-gt /path/to/release --ingestion-manifest ingestion.json --drive-scorer-dir /path/to/drive/scripts

# Clip-relevance with frame-supported PAS ground truth (PhysicalAI v1.1.2)
--stage score --retrieval-in gal.retrieval.json --scoring clip-relevance --gallery-gt /path/to/release --ingestion-manifest ingestion.json --pas-frame-supported
```

`--concurrency` defaults to 1 and should stay there for any run whose latency
numbers you intend to read: concurrent queries contend for the same VLM and
embedding services, so every per-stage figure inflates. Raise it only when you
want wall-clock and are reporting retrieval quality alone — CLI queries take
~8 s each, so a 673-query set is hours at 1.

---
