---
name: benchmark-video-search
description: >-
  Measure retrieval quality and latency of a deployed VSS search profile by
  ingesting a labelled dataset and running the vss CLI across retrieval paths.
  Use when the user asks to "benchmark video search", "compare search recall
  between builds", or "profile search latency" on a deployed VSS profile.
license: Apache-2.0
metadata:
  version: "3.3.0-rc0"
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

## When not to use this skill

- For a single video-search question, use `vss-search-archive`.
- For an end-to-end benchmark of the OpenClaw chat route, this runner does not
  measure that path; do not present CLI timings as chat timings.
- For a historical benchmark of the retired agent REST search endpoint, use
  the external legacy `run_eval.py` noted in `references/troubleshooting.md`.

## Routing

| Situation | Action |
|---|---|
| User asks to benchmark the OpenClaw chat route | This runner does not measure it; explain the gap instead of presenting CLI timings as chat timings |
| User asks to benchmark the retired agent REST search endpoint | This runner does not measure it; for historical comparison, use the external legacy `run_eval.py` noted in `references/troubleshooting.md` |
| No search profile deployed in this session | Use `vss-build-vision-ai` to deploy the **stock search profile with its in-stack agent REST API and in-stack LLM retained**. Explicitly name both requirements in the build request so it skips the harness question (which would remove the agent and possibly the LLM); do not select a NemoClaw-only or CLI-only harness. Record the reachable agent endpoint and unified origin, then return here. Check agent `/health` and LLM `/v1/models`, run Step 3 to inspect indices, and complete Step 4 ingestion and its index probe before scoring |
| User did not give an endpoint | Ask for it. Do not guess, and do not default to localhost |
| Endpoint uses `localhost` or `127.0.0.1` | This is valid when the runner is on the deployment host. Verify the actual VST clip URL is reachable from RT-VLM before trusting critic-filtered metrics |
| User did not give a dataset | Ask which dataset and where its `--data-dir` is. Do not invent one |
| Elasticsearch has no `mdx-*` indices | Ingestion has not completed. Run **Step 4**; do not report zero scores as a quality result |
| User asks to reuse what is already ingested | Add `--skip-ingest`. It cannot be combined with `--clear` or `--only-dataset` |
| User asks to analyse an existing result file | Skip to **Step 6**; read the JSON, run nothing |
| Every query returns 0 hits | Stop. Diagnose with **Step 3** before reporting anything — this is nearly always missing indices, not poor retrieval |
| Critic-filtered metrics are all `NA` | The critic never ran. Check the clip-URL prerequisite below before concluding anything about verification quality |

## The default run

Unless the user says otherwise, this is the run. Ask which dataset to use and,
if missing, which deployment endpoint to target.

```bash
uv run --with requests python3 scripts/run_eval_flows.py --endpoint http://HOST:8000 \
    --data-dir /path/to/datasets --dataset DATASET \
    --skip-download --skip-existing --name RUN_NAME
```

Five defaults, each load-bearing:

- **`--skip-existing`** is non-destructive and avoids duplicate uploads on a
  shared deployment. `--only-dataset` and `--clear` are explicit destructive
  maintenance modes. Both require `--confirm-delete` in the same invocation;
  there is no interactive prompt. Use either only when the user requested
  deletion, and report the printed deletion inventory.
- **Live decomposition is on by default.** The LLM origin is derived from
  `--endpoint` (same host, port 30081), so there is no flag to forget. Every
  query is decomposed the way the deployed agent decomposes it, and that choice
  picks the retrieval path. Override with `--llm-url` / `--llm-port`; turn it
  off with `--no-decompose` (replays stored routes) or `--fixed-search-path`
  (ignores stored routes for a single-path baseline).

  If the NIM is unreachable the run replays dataset routes when present;
  otherwise it falls back to `embed`, which needs nothing but the query text.
  The fallback records `flow.query.live_decomposition.fell_back_to` in the
  result file. Check that field and `Search paths:` before quoting numbers.

  `--no-decompose` also replays dataset routes when present; it does not force
  one path. Use `--fixed-search-path` only for a deliberately single-path
  baseline. Nothing else is inferred: a
  `devset_provenance.json` sidecar is never loaded automatically. To use it as
  an answer key, pass its full path with `--decompositions`; scoring against
  perfect routing is a different experiment from scoring the live decomposer.
- **The pre-decomposition question is sent with every query** as
  `--original-query`. Retrieval ignores it; the critic verifies against it
  instead of a question the library rebuilds out of `--query` and `--attribute`.
  Without it the CLI's critic is asked a different question from the REST
  flow's, so the two flows' rejection rates are not comparable — and on the
  `object` path there is no question at all, so verification is skipped
  outright. Turn it off only with `--no-original-query`, and only to reproduce
  a result file captured before this existed.

  A `vss` older than the flag is detected by one `--help` probe at startup and
  the run continues without it, with a warning. Do not quote critic-filtered
  metrics from a run that printed that warning.
- **Ingest is `vst-direct`**: upload to VIOS, let its webhooks drive
  perception. That is what the UI does — it stopped calling the agent's ingest
  API — and it is the only flow that also triggers RT-VLM tagging. The
  timestamp anchor rides in the upload metadata, not in anything `/complete`
  does, so scores stay comparable with `agent-3step` baselines.

  What `agent-3step` gave for free was proof: `/complete` returns
  `chunks_processed`, and a zero failed the upload. `vst-direct` has no such
  step, so a **post-ingest index probe** replaces it — one embed query, retried,
  before the real run starts. Registered in VST is not the same as indexed in
  Elasticsearch, and without this check an unindexed deployment scores 0.0
  across the board and reads as a retrieval collapse.

  If the probe aborts a run, check the deployed VIOS notification config and
  that `RTVI_EMBED_MODEL` matches the webhook's model string — RT-Embed answers
  a mismatch with HTTP 200 and `inference: false`. The Docker and Helm search
  profiles enable webhooks; generic VIOS chart defaults may not. If the
  deployed webhooks are disabled, use `--ingest-flow agent-3step`.
  Never pass `--skip-index-probe` on a run whose numbers you intend to quote.
- **Concurrency stays at 1** (the script's default). Concurrent queries contend
  for the same VLM and embedding services, so per-stage latencies inflate and
  stop describing a single query.

Deviate only on request: `--skip-ingest` to reuse what is there, `--clear` to
wipe the whole deployment, `--subset` to narrow the slice, `--concurrency N` to
trade latency fidelity for wall-clock, `--no-decompose` to replay stored routes,
or `--fixed-search-path` for a single-path baseline.

Never add either decomposition override to a run the user called a live-routing
eval. Both measure a different flow; the result records the routing mode.

## Prerequisites

| Requirement | How to check |
|---|---|
| Search profile agent reachable | `curl -sf --connect-timeout 5 --max-time 10 ${ENDPOINT}/health` returns 200 |
| Unified HAProxy origin routes the services | `curl -sf --connect-timeout 5 --max-time 10 ${ORIGIN}/vst/api/v1/sensor/version` returns 200. ORIGIN is usually the endpoint host on port 7777; use it for `vss configure` and the Step 3 Elasticsearch probe. The runner separately derives VST's direct ingress on port 30888 (`--vst-port` default); do not pass the HAProxy port as `--vst-port` |
| `vss` CLI runs from this checkout | `vss search run --help` exits 0 |
| CLI points at the right origin | `vss configure show` reports the ORIGIN above |
| Dataset present locally | `test -f ${DATA_DIR}/${DATASET}/dataset.json` — layout in `references/dataset-format.md` |
| Python 3.10+ for this runner; Python 3.13–3.14 for a separately installed `vss` CLI | `python3 --version` for the runner; use a Python 3.13 or 3.14 environment when installing `libs/vss/core` and `libs/vss/cli` (their `requires-python` is `>=3.13,<3.15`), then check `vss search run --help` there |
| LLM reachable (else the run falls back and says so) | `curl -sf --connect-timeout 5 --max-time 10 http://HOST:30081/v1/models` returns 200 |
| LLM model selected unambiguously | If `/v1/models` lists multiple IDs, pass `--llm-model` matching the deployed agent's model |
| Critic clip URL reachable **from RT-VLM** | Inspect a returned VST `videoUrl` and test that exact URL from the RT-VLM container. The CLI uses `video_url_scope="internal"`; CLI ORIGIN may legitimately be localhost on the host |

Agent `/health` confirms the process is reachable, not that VIOS and the search
indices are ready. Step 3 and the post-ingest index probe are the retrieval
readiness gates.

## Step 1 — Configure the CLI

The CLI reads `~/.vss/config.json`, which is separate state from `--endpoint`
and points at a **different port**: it discovers services by path prefix on the
unified origin, which the agent port does not route. Getting this wrong makes
every query exit 4.

```bash
vss configure --base-url http://HOST:7777
vss configure show
```

`localhost` is valid here when the runner runs on the deployment host. This
URL controls where the CLI reaches the services; it does not determine the
clip URL sent to RT-VLM. If verification fails, inspect the VST-returned
`videoUrl` and test that exact address from the RT-VLM container.

## Step 2 — Dry run

Reports what would happen and contacts nothing.

```bash
uv run --with requests python3 scripts/run_eval_flows.py --endpoint http://HOST:8000 \
    --data-dir /path/to/datasets --dataset DATASET --dry-run
```

## Step 3 — Verify the deployment is actually indexed

Registration with VST is **not** ingestion. A deployment can list every sensor
and still have an empty Elasticsearch, in which case every query returns zero
hits and every metric reads 0.0000 — which looks like catastrophic retrieval
quality and is not.

```bash
curl -s --connect-timeout 5 --max-time 10 "http://HOST:7777/elasticsearch/_cat/indices?h=index,docs.count"
```

Expect `mdx-embed-filtered-*`, `mdx-behavior-*` and `mdx-raw-*` with non-zero
counts. `mdx-behavior-*` is what the `attribute` and `fusion` paths read; if it
is missing, only `embed` can score. If the indices are absent, go to Step 4.

## Step 4 — Ingest

The default `vst-direct` flow uploads to VIOS and lets its webhooks trigger
perception. It does not call the agent's `/complete` endpoint or receive a
`chunks_processed` count. The runner waits for VST registration, then probes
the search index before scoring. Use `--ingest-flow agent-3step` only when the
deployment cannot run the webhook flow; see `references/troubleshooting.md`.

```bash
uv run --with requests python3 scripts/run_eval_flows.py --endpoint http://HOST:8000 \
    --data-dir /path/to/datasets --dataset DATASET --skip-download --skip-existing
```

Expect this to be slow while the webhook pipeline indexes the videos:

- A registered source is not necessarily indexed. The post-ingest index probe
  must pass before treating zero hits as a retrieval result.
- If webhooks are disabled, select `--ingest-flow agent-3step` explicitly. That
  flow calls `/complete`, which can return a transient 502 and is retried.
- `--skip-existing` is the default: ingest what is missing and delete nothing.
- `--only-dataset` deletes foreign sources only when explicitly requested with
  `--confirm-delete`; report its candidate inventory before it runs.
- `--clear --confirm-delete` deletes **every** source including other people's.
  Use it only on explicit request.
- `--clear` and `--only-dataset` list sources via `--vst-url` but delete through
  the agent endpoint. If those URLs have different hosts, verify they belong
  to the **same deployment** before deletion; otherwise source IDs from one
  stack could be sent to another.

Then re-run Step 3. Indices are lazy; they appear after webhook processing,
not immediately after upload. See `references/flags.md` for ingest options.

## Step 5 — Run the benchmark

```bash
uv run --with requests python3 scripts/run_eval_flows.py --endpoint http://HOST:8000 \
    --data-dir /path/to/datasets --dataset DATASET --subset SUBSET \
    --skip-download --skip-ingest --name RUN_NAME
```

`--skip-ingest` reuses an already-indexed dataset; it cannot be combined with
`--clear` or `--only-dataset`. See `references/flags.md` before changing flags
that affect run meaning.

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

Read `references/reading-results.md` when interpreting stage latencies or
metrics. Read `references/flags.md` when a flag might change comparability.
Read `references/troubleshooting.md` when ingest fails or the CLI exits nonzero.
Read `references/dataset-format.md` when validating a dataset's layout.

## Error handling

A non-zero CLI exit aborts the run. A missing result envelope marks that
query unanswered and aborts by default; `--tolerate-unanswered` permits an
explicit partial run that excludes those queries from scoring. Never count
them as misses. Diagnose an all-zero run with Step 3 before reporting quality.
Critic-filtered `NA` means verification was not available, not zero quality. Use
`references/troubleshooting.md` for the specific failure and recovery steps.

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
