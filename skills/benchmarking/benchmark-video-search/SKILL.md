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

Follow the routing table, then the numbered steps. Execute each step in order on
a first run; for a repeat run against an already-indexed deployment go straight
to **Step 5**. Reference material is in `references/`, the runner is in
`scripts/`.

## Purpose

Answers two questions about a **search profile** deployment: does retrieval find
the right footage, and where does a query's time go. It drives the `vss` CLI —
the same path the product uses, since the agent adapter shells out to
`vss search run <mode> --raw` for every search — so the numbers describe what
ships rather than a REST endpoint that is being retired.

## Routing

| Situation | Action |
|---|---|
| No search profile deployed in this session | Deploy with `vss-deploy-profile -p search`, note the endpoint it returns, then return here |
| User did not give an endpoint | Ask for it. Do not guess, and do not default to localhost |
| User did not give a dataset | Ask which dataset and where its `--data-dir` is. Do not invent one |
| Elasticsearch has no `mdx-*` indices | Ingestion has not completed. Run **Step 4**; do not report zero scores as a quality result |
| Media already ingested and indexed | Skip to **Step 5** with `--skip-ingest` |
| User asks to analyse an existing result file | Skip to **Step 6**; read the JSON, run nothing |
| Every query returns 0 hits | Stop. Diagnose with **Step 3** before reporting anything — this is nearly always missing indices, not poor retrieval |

## Prerequisites

| Requirement | How to check |
|---|---|
| Search profile agent reachable | `curl -sf ${ENDPOINT}/health` returns 200 |
| Unified origin routes the services | `curl -sf ${ORIGIN}/vst/api/v1/sensor/version` returns 200 (ORIGIN is usually the endpoint host on port 7777) |
| `vss` CLI runs from this checkout | `vss search run --help` exits 0 |
| CLI points at the right origin | `vss configure show` reports the ORIGIN above |
| Dataset present locally | `test -f ${DATA_DIR}/${DATASET}/dataset.json` — layout in `references/dataset-format.md` |
| Python 3.10+ | `python3 --version` |
| LLM reachable, **only if** decomposing | `curl -sf ${LLM_URL}/v1/models` returns 200 |

## Step 1 — Configure the CLI

The CLI reads `~/.vss/config.json`, which is separate state from `--endpoint`
and points at a **different port**: it discovers services by path prefix on the
unified origin, which the agent port does not route. Getting this wrong makes
every query exit 4.

```bash
vss configure --base-url http://HOST:7777
vss configure show
```

## Step 2 — Dry run

Reports what would happen and contacts nothing.

```bash
python3 scripts/run_eval_flows.py --endpoint http://HOST:8000 \
    --data-dir /path/to/datasets --dataset DATASET --dry-run
```

## Step 3 — Verify the deployment is actually indexed

Registration with VST is **not** ingestion. A deployment can list every sensor
and still have an empty Elasticsearch, in which case every query returns zero
hits and every metric reads 0.0000 — which looks like catastrophic retrieval
quality and is not.

```bash
curl -s "http://HOST:7777/elasticsearch/_cat/indices?h=index,docs.count"
```

Expect `mdx-embed-filtered-*`, `mdx-behavior-*` and `mdx-raw-*` with non-zero
counts. `mdx-behavior-*` is what the `attribute` and `fusion` paths read; if it
is missing, only `embed` can score. If the indices are absent, go to Step 4.

## Step 4 — Ingest

Three steps per video against the agent API: `POST /api/v1/videos` for an upload
URL, the chunked upload, then `POST /api/v1/videos/{sensor_id}/complete`, which
is what triggers perception.

```bash
python3 scripts/run_eval_flows.py --endpoint http://HOST:8000 \
    --data-dir /path/to/datasets --dataset DATASET --skip-download --skip-existing
```

Expect this to be slow and occasionally noisy:

- `POST /complete` returns 502 intermittently and is retried. A retry that
  succeeds is a success — do not report it as a failure.
- `Duplicate Camera id` from RT-CV does **not** mean the ingest failed;
  embeddings still generate.
- Use `--skip-existing` so a shared deployment is not re-uploaded.
- Never pass `--clear` unless the user explicitly asked to wipe the deployment.
  It deletes every source, including other people's.

Then re-run Step 3. Indices are lazy; they appear after `/complete`, not after
upload.

## Step 5 — Run the benchmark

```bash
python3 scripts/run_eval_flows.py --endpoint http://HOST:8000 \
    --data-dir /path/to/datasets --dataset DATASET --subset SUBSET \
    --skip-download --skip-ingest --concurrency 3 --name RUN_NAME
```

Add `--llm-url http://HOST:30081` to decompose each query live, which is what
the deployment's agent does: an LLM turns the sentence into
`{query, attributes, has_action, ...}` and that decides which path runs. Without
it every query uses `--search-path` and routing is not exercised.

`--name` makes the result file findable; without it the name is
`<dataset>_<subset>_<timestamp>`.

## Step 6 — Report

Results land in `scripts/cli_eval_result/<name>.json`.

Report **Recall** and **HIT@k** as the quality signal. Precision and mAP are
dominated by `--top-k` — five results against roughly one relevant segment keeps
them low however good retrieval is — so they compare runs; they do not grade a
deployment.

State these alongside any number, because each one changes what it means:

- **Which paths ran.** `Search paths:` in the summary. A run that was all
  `embed` says nothing about `attribute`.
- **Whether decomposition was live.** If so, give its share of query time and
  what it `Routed to:` — a routing shift changes what was measured.
- **Whether the critic ran.** Critic-filtered metrics print `NA` when the
  response carried no verification block. `NA` is not `0`.

`references/reading-results.md` explains the stage-latency table and each stage
name; `references/flags.md` covers which flags change what a run means; and
`references/troubleshooting.md` covers ingestion failures and CLI exit codes.

## Conventions

- **Never report 0.0000 as a quality result without checking Step 3 first.** An
  unindexed deployment and a broken retriever look identical in the metrics and
  are not the same finding.
- A non-zero CLI exit aborts the run rather than scoring 0.0. A stale virtualenv
  exits 1 on every invocation, and scoring that as "no results" would report a
  broken environment as an accuracy regression.
- Queries do not merge adjacent windows by default. Upstream merging averages
  the scores of merged windows and changes the precision denominator, so a
  merged run is not comparable to an unmerged baseline.
- The critic is an LLM and is not deterministic. Raw metrics repeat exactly;
  critic-filtered ones move a few points between runs. Gate on raw.
- Live decomposition is not deterministic either. The same query can route
  differently between runs, so compare `Routed to:` before comparing scores.
