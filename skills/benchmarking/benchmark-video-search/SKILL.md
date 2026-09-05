---
name: benchmark-video-search
description: Measure retrieval quality and latency of a deployed VSS search profile — ingest a labelled dataset, run queries through the vss CLI across the embed/attribute/fusion/object paths, and report precision, recall, mAP, HIT@k and a per-stage latency breakdown.
license: Apache-2.0
metadata:
  version: "3.3.0"
  author: "NVIDIA Video Search and Summarization Team"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint search retrieval benchmarking evaluation"
---

## Instructions

Run the steps below in order on a first run. For repeat runs against an already
ingested deployment, skip to **Query only**. Reference material lives in
`references/`; the runner and its backends live in `scripts/`.

This benchmark drives the **`vss` CLI**, which is the path the product uses:
the agent adapter shells out to `vss search run <mode> --raw` for every search.
Measuring the CLI therefore measures what ships, and the numbers stay valid as
the UI and agent layers change around it.

### 1. Check what you need

- A deployed **search profile** — agent on `:8000`, unified origin on `:7777`.
- A checkout of this repo (the CLI runs from source via `uv`).
- A dataset directory laid out as `<data-dir>/<dataset>/dataset.json` plus
  `<data-dir>/<dataset>/videos/`. See `references/dataset-format.md`.

Confirm the CLI resolves before anything else:

```bash
vss search run --help
```

### 2. See what a run would do, without touching the deployment

```bash
python3 scripts/run_eval_flows.py --endpoint http://HOST:8000 \
    --data-dir /path/to/datasets --dataset warehouse --dry-run
```

### 3. Ingest and query

```bash
python3 scripts/run_eval_flows.py --endpoint http://HOST:8000 \
    --data-dir /path/to/datasets --dataset warehouse --concurrency 3
```

Ingestion is the slow and failure-prone half. `POST /complete` returns 502
intermittently and is retried; a `Duplicate Camera id` warning from RT-CV does
**not** mean failure. See `references/troubleshooting.md`.

### Query only

Against media already indexed:

```bash
python3 scripts/run_eval_flows.py --endpoint http://HOST:8000 \
    --data-dir /path/to/datasets --dataset warehouse \
    --skip-download --skip-ingest --concurrency 3 --name my-run
```

### 4. Read the results

Written to `scripts/cli_eval_result/<name>.json`, or
`<dataset>_<subset>_<timestamp>.json` when `--name` is absent.

Report **Recall** and **HIT@k** as the quality signal. Precision and mAP are
dominated by `--top-k` — five results against roughly one relevant segment keeps
them low no matter how good retrieval is — so they compare runs, they do not
grade a deployment. `references/reading-results.md` explains the stage-latency
table and what each stage name means; `references/flags.md` covers which flags
change what a run means and which are escape hatches.

## Conventions

- **Never** report critic-filtered metrics as `0` when the response carried no
  verification block; the runner prints `NA` instead, and that distinction is
  the difference between "the critic rejected everything" and "the critic never
  ran".
- Queries do not merge adjacent windows by default. Upstream merging averages
  the scores of merged windows and changes the precision denominator, so a
  merged run is not comparable to an unmerged baseline.
- A non-zero CLI exit aborts the run rather than scoring 0.0. A stale virtualenv
  exits 1 on every invocation, and scoring that as "no results" would report a
  broken environment as a quality regression.
- The critic is an LLM and is not deterministic. Raw metrics repeat exactly;
  critic-filtered ones move a few points between runs. Gate on raw.
