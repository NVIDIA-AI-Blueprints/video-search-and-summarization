# Flags

The flags that change what a run means, and the ones that do not.

## Querying

`--query-flow cli` is the only backend today.

Runs `vss search run <path>` as a subprocess per query. Before the first query
the script resolves the CLI, runs `vss --version` to catch a broken install,
and points `~/.vss/config.json` at this deployment.

With a dataset carrying decompositions it routes per query. Without them, every
query uses `--search-path` (default `embed`).

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

# Fresh index (destructive)
--dataset warehouse --clear

# Shared box — do not touch others' data
--dataset warehouse --skip-existing

# One fixed path instead of routing
--search-path fusion --attribute "person wearing a hardhat"

# Name the results file, for comparing two runs deliberately
--dataset warehouse --skip-download --name embed-baseline

# See what would run, touching nothing
--dry-run
```

Add `--concurrency 3` — the default is 1, and CLI queries take ~8 s each.

---

