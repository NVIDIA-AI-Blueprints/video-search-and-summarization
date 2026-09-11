# Reading the results

Which metrics mean what, and how to read the per-stage latency breakdown.

## Reading the results

Results are written to `scripts/cli_eval_result/`, beside the
script rather than relative to your shell, so a run is findable regardless of
where it was started. The filename is `--name` if given, otherwise
`<dataset>_<subset>_<timestamp>`. `--output-file` overrides both with an exact
path. The directory is gitignored: a result describes one deployment at one
moment, and is not source.

Use `--name` when comparing runs deliberately -- `--name embed-baseline` and
`--name embed-after-fix` read better in a diff than two timestamps.

```
Search paths:       embed=6  fusion=3

                    Raw         Critic Filtered
mAP:                0.6449      0.4074
MRR:                0.7130      0.5556
Avg Recall:         0.9444      0.4167
HIT@5:              1.0000      0.5556
```

Each hit is split into 5-second segments (ground truth is 5s-aligned), matched
in rank order on video name + start time, then scored. Every metric is computed
twice: once on everything (**Raw**), once with critic-rejected hits removed
(**Critic Filtered**).

| metric | reading |
|---|---|
| **Recall / HIT@k** | the meaningful signals — did it find the right footage |
| **Precision / mAP** | dominated by `--top-k`; 5 results × ~5 segments vs ~1 relevant keeps these low by construction |
| **Critic Filtered** | `NA` when the response carried no verification block at all — deliberately not zero |

Two caveats worth internalising:

- **Recall is identical across retrieval paths when they find the same
  material.** If Raw recall matches between CLI and REST but mAP differs, that
  is usually the merge asymmetry, not a quality difference.
- **The critic is an LLM and is not deterministic.** Raw metrics repeat exactly
  run to run; critic-filtered ones move by a few points. Gate on Raw, or use
  repeats and a tolerance band.

### Where query latency goes

The `Latency` block splits each query into the part `search()` accounts for and
the part it cannot:

```
Latency (client-observed):
  Mean:             8.970s
    of which:
      search:       7.675s
      CLI startup:  1.295s
```

`CLI startup` is `latency_s` minus the search's own `total_s` — Python process
launch, imports, and the `~/.vss/config.json` read. It is harness cost: a
long-running caller would pay it once, not per query. It only appears when the
run reports timings; with nothing to subtract, the split is omitted rather
than guessed.

### Stage latency

Each query also reports where its time went, and the run prints an aggregate:

```
Stage latency (9 queries reported)
  stage                                               mean       total  queries
  --------------------------------------------  ----------  ----------  -------
  vlm: resolve VST clip url                         7.503s     67.528s        9
  vlm: inference                                    7.011s     63.101s        9
  critic: resolve VST timeline                      4.912s     44.207s        9
  attribute_search: generate text embedding         5.471s     16.412s        3
  embed_search: ES search execution                 1.058s      9.527s        9
```

### What the stage names mean

Read them as `component: action`. The prefix says which part of the system did
the work.

| Stage | What it does |
|---|---|
| `embed_search: generate query embedding` | RT-Embed turns the query text into a vector |
| `embed_search: build ES query` | Assembles the kNN request (microseconds) |
| `embed_search: ES search execution` | The kNN lookup against video-chunk embeddings in `mdx-embed-filtered-*` |
| `embed_search: process search hits` | Parses the response into results — merging windows, building URLs |
| `attribute_search: generate text embedding` | RT-**CV**'s text encoder turns the attribute into a vector (a different service from the query embedder) |
| `attribute_search: search behavior embeddings` | kNN against per-detected-object embeddings in `mdx-behavior-*` |
| `attribute_search: frame lookups` | Frame-level detail from `mdx-raw-*`, once per candidate |
| `attribute_search: deduplication` | Collapses several hits on the same tracked object |
| `search: fusion score combination` | Merges the embed and attribute rankings into one ordering (RRF or weighted-linear) |
| `critic: resolve VST timeline` | Looks up when the video was actually recorded, so clip offsets map onto real timestamps |
| `vlm: resolve VST clip url` | Asks VST to cut the clip's time window and return a playable URL |
| `vlm: prepare media` | Prepares the clip for the model — free with `media_mode=video_url`, a download and encode otherwise |
| `vlm: inference` | Sends clip and question to the vision model, reads back the verdict |

Three prefixes, three phases:

* **`embed_search:`** — matching the query against video chunks. Every query.
* **`attribute_search:`** — matching an attribute against detected people. Only
  `fusion` / `attribute` queries, which is why their `queries` count is lower.
* **`vlm:` / `critic:`** — verification: showing each hit to the vision model
  and reading back confirmed / rejected.

Three things to know when reading it:

* Times are **self time** -- what the stage spent itself, nested stages
  excluded. Wrapper stages (`search: embed search` merely contains the three
  `embed_search:` ones) therefore contribute ~0 and are not listed.
* `queries` is the denominator of `mean`. Attribute stages run only on fusion
  queries, so a 5.5s mean over 3 is not comparable to a 7.5s mean over 9.
* Stages **overlap** -- verification checks its hits under `asyncio.gather` --
  so they do not sum to query latency. The column shows where effort went, not
  a timeline.

One measured result worth knowing before reading the table: **fusion scoring is
free.** `search: fusion score combination` is ~0.2ms. Where a fusion query is
slow, it is waiting on the attribute leg (`attribute_search:` stages), never on
the merge itself.

#### Whose code produced these numbers

The CLI runs as `uv run --project <repo> ... vss`, so `search_core` executes
**locally, from your working tree** -- not on the host in `--endpoint`. That
host supplies RT-Embed, RT-CV, Elasticsearch, VST and the VLM; the search
orchestration, and therefore every stage timing, is local.

Two consequences:

* Editing `search_core` takes effect on the next run. No deploy, no push, not
  even a commit -- the working tree is what runs.
* A stage timing measures **your** code against **their** services. Comparing
  two runs is only meaningful if the checkout is the same; comparing across
  checkouts measures the diff, not the deployment.

The REST path is the opposite -- there the timings come from whatever the host
is running, and appear only once the deployment carries this change.

This is the CLI-path replacement for the Phoenix span breakdown, which does not
exist here — `search_core` is NAT-free and emits no OTel spans. It is finer
than Phoenix was (`embed_search` was one span there, four stages here) and
attributable to a specific query rather than joined back by timestamp.

The JSON keeps the full picture per stage — `self_total_s`, `inclusive_total_s`,
`calls` (some stages run per hit, not per query) and `concurrent_children` (set
when nested stages ran under `asyncio.gather`, as fusion's two legs do, where
self time is not meaningful). On a deployment without the change the section is
simply absent — never zero.

Results JSON carries `summary`, `config`, `query_results` (per-query, including
which ground-truth segments were missed and its `timings`) and `flow` (which
backends ran, ingest timings, readiness).

---


## How it fits together

Three independent stages. Each of the first two is swappable; the third never
changes, which is the whole point of the design.

```
   dataset            ingest                    query                  score
 ┌──────────┐   ┌──────────────────┐   ┌────────────────────┐   ┌──────────────┐
 │ videos   │──▶│ agent-3step      │──▶│ cli                │──▶│ normalize    │
 │ queries  │   │ legacy-put       │   │                    │   │ → metrics    │
 │ ground   │   │                  │   │                    │   │              │
 │  truth   │   │ (webhook, vst-   │   │ (openclaw—pending) │   │ flows/       │
 └──────────┘   │  direct — later) │   └────────────────────┘   │  metrics.py  │
                └──────────────────┘                            └──────────────┘
                  --ingest-flow            --query-flow
```

Why the split: ingest and query are changing independently in the product, so
each gets its own axis. Adding a new ingest flow (webhook, say) is one class
plus one registry line — no metric or runner changes.

---

