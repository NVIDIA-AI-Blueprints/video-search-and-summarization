---
name: benchmark-video-search
description: Measure retrieval quality and latency of a deployed VSS search profile — ingest a labelled dataset, run queries through the vss CLI across the embed/attribute/fusion/object paths, and report precision, recall, mAP, HIT@k and a per-stage latency breakdown.
license: Apache-2.0
metadata:
  version: "3.6.0"
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
| User asks to reuse what is already ingested | Add `--skip-ingest`. Otherwise the default re-ingests |
| User asks to analyse an existing result file | Skip to **Step 6**; read the JSON, run nothing |
| Every query returns 0 hits | Stop. Diagnose with **Step 3** before reporting anything — this is nearly always missing indices, not poor retrieval |
| Critic-filtered metrics are all `NA` | The critic never ran. Check the clip-URL prerequisite below before concluding anything about verification quality |

## The default run

Unless the user says otherwise, this is the run. Ask which dataset; do not ask
about the rest.

```bash
python3 scripts/run_eval_flows.py --endpoint http://HOST:8000 \
    --data-dir /path/to/datasets --dataset DATASET \
    --only-dataset --name RUN_NAME
```

Three defaults, each load-bearing:

- **`--only-dataset`** deletes every source that is not one of this dataset's
  videos, then ingests. A foreign source cannot be retrieved by any query in the
  dataset, so it adds nothing but false-positive surface and ingest time. Report
  how many sources will be deleted before it runs.
- **Live decomposition is on by default.** The LLM origin is derived from
  `--endpoint` (same host, port 30081), so there is no flag to forget. Every
  query is decomposed the way the deployed agent decomposes it, and that choice
  picks the retrieval path. Override with `--llm-url` / `--llm-port`; turn it
  off only with `--no-decompose`.

  If the NIM is unreachable the run continues, in this order: decompositions the
  dataset carries, then its `devset_provenance.json` answer key, then
  `--search-path`. The first two still route per query, so every path is still
  exercised. The third measures **one** path — the run warns loudly and the
  result file records `live_decomposition.fell_back_to`. Read that field before
  quoting any number.
- **Concurrency stays at 1** (the script's default). Concurrent queries contend
  for the same VLM and embedding services, so per-stage latencies inflate and
  stop describing a single query.

Deviate only on request: `--skip-ingest` to reuse what is there, `--clear` to
wipe the whole deployment, `--subset` to narrow the slice, `--concurrency N` to
trade latency fidelity for wall-clock, `--no-decompose` for a single-path
baseline.

Never add `--no-decompose` to a run the user called an eval. It measures one
retrieval path instead of the product's routing, and nothing in the metrics
says so.

## Prerequisites

| Requirement | How to check |
|---|---|
| Search profile agent reachable | `curl -sf ${ENDPOINT}/health` returns 200 |
| Unified origin routes the services | `curl -sf ${ORIGIN}/vst/api/v1/sensor/version` returns 200 (ORIGIN is usually the endpoint host on port 7777) |
| `vss` CLI runs from this checkout | `vss search run --help` exits 0 |
| CLI points at the right origin | `vss configure show` reports the ORIGIN above |
| Dataset present locally | `test -f ${DATA_DIR}/${DATASET}/dataset.json` — layout in `references/dataset-format.md` |
| Python 3.10+ | `python3 --version` |
| LLM reachable (else the run falls back and says so) | `curl -sf http://HOST:30081/v1/models` returns 200 |
| ORIGIN reachable **from inside** the containers | `docker exec vss-rtvi-vlm curl -sf -o /dev/null -w '%{http_code}\n' ${ORIGIN}/vst/api/v1/sensor/version` returns 200. `localhost` fails this even when it passes from the host |

## Step 1 — Configure the CLI

The CLI reads `~/.vss/config.json`, which is separate state from `--endpoint`
and points at a **different port**: it discovers services by path prefix on the
unified origin, which the agent port does not route. Getting this wrong makes
every query exit 4.

```bash
vss configure --base-url http://HOST:7777
vss configure show
```

**If you are running on the deployment host, do not use `localhost`.** Use the
host's LAN IP anyway. The critic hands the VST clip URL to RT-VLM, which is a
*container*: `localhost` there resolves to the container itself, the fetch
fails, and because verification is best-effort every hit stays `unverified`
while retrieval looks perfectly healthy. Running off-host hides this, because
nothing but a routable address works in the first place.

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
- `--only-dataset` is the default and deletes only foreign sources. On a shared
  deployment say what it will delete before it runs.
- `--clear` deletes **every** source including other people's. Only on request.
- `--skip-existing` is the conservative middle: ingest what is missing, delete
  nothing.

Then re-run Step 3. Indices are lazy; they appear after `/complete`, not after
upload.

## Step 5 — Run the benchmark

```bash
python3 scripts/run_eval_flows.py --endpoint http://HOST:8000 \
    --data-dir /path/to/datasets --dataset DATASET --subset SUBSET \
    --skip-download --skip-ingest --name RUN_NAME
```

Decomposition needs no flag. An LLM turns the sentence into
`{query, attributes, has_action, ...}` and that decides which path runs — the
same call the deployed agent makes. The script derives the NIM origin from
`--endpoint`; if it cannot reach it the run falls back rather than stopping, and
says which fallback it took.

`fusion` cannot be a fallback. It needs an attribute per query and there is
nothing to supply one without decomposition — and feeding it the whole query as
its attribute is the failure this eval already measured: mAP −25%, HIT@1 halved,
latency +59%.

Leave `--concurrency` alone unless asked. It defaults to 1 because concurrent
queries contend for the same VLM and embedding services, which inflates every
per-stage latency in the report.

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
