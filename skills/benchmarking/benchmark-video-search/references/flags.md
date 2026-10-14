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
```

`--concurrency` defaults to 1 and should stay there for any run whose latency
numbers you intend to read: concurrent queries contend for the same VLM and
embedding services, so every per-stage figure inflates. Raise it only when you
want wall-clock and are reporting retrieval quality alone — CLI queries take
~8 s each, so a 673-query set is hours at 1.

---
## Drive-compatible clip scoring

`--scoring drive` evaluates a complete CLI embedding-search ranking using the
original Drive metric functions. It requires NumPy, an already-ingested gallery,
and these inputs:

- `--drive-data`: original release root or merged `{gallery, queries}` JSON.
- `--drive-scripts-dir`: directory containing the original `evaluate_domain_test.py`.
- `--ingestion-manifest`: `ingest_subset.py`'s `ingestion.json`, mapping exact clip IDs to sensors.
- `--skip-ingest`: required; this mode evaluates the supplied gallery only.

The mode sets top-K to the gallery size (at most 1,000), similarity threshold to
`-1`, and source type to `video_file`. It searches the selected source names,
preserves each original query row, and disables decomposition and adjacent-window
merging. Every response must include each gallery clip exactly once; missing,
duplicate, foreign, or malformed hits fail the run instead of publishing partial
metrics. Critic verdicts do not filter the ranking; the CLI's optional verification
is capped at one result per query to bound work on full-gallery requests.

Results contain full-precision `metrics` with Drive's overall/per-slice AP,
recall@1/5/10, hit@1/5/10, nDCG@1/5/10, first-relevant mean/median rank, and AUC.
`summary` is the overall block, `event_summary` reports event-only metrics, and
`query_results` retains complete clip rankings. Original `near_universal` and
no-gallery-relevance exclusions apply. The scorer hash and NumPy version are
recorded. CLI response order supplies ranks; reported scores are never sorted to
rebuild the ranking. AUC uses the original scorer's score/tie handling.

```bash
python scripts/run_eval_flows.py --endpoint "$AGENT_ENDPOINT" \
  --scoring drive --skip-ingest --skip-vss-configure \
  --drive-data /path/to/subset/subset.json \
  --drive-scripts-dir /path/to/original-drive-scripts \
  --ingestion-manifest /path/to/ingestion/ingestion.json \
  --output-file /path/to/drive-cli-metrics.json
```

Use a CLI installed from this checkout: embedding search now retains unrounded
cosine values for output and threshold comparisons. Scores near a threshold may
be included differently than with the previous two-decimal comparison. The
Drive metrics match for identical input rankings/scores; Elasticsearch kNN can
still differ from Drive's exhaustive FP32 matrix ranking. Default segment scoring
and its output format remain available through `--scoring segments`.
