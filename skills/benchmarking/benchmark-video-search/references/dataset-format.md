# What a dataset must contain

Ground truth, decompositions, and the layout the runner expects.

## The dataset

Two files per dataset, under `<--data-dir>/<--dataset>/`:

```
<data-dir>/warehouse/dataset.json     queries, ground truth, decompositions
<data-dir>/warehouse/videos/          the .mp4 files to ingest
```

`--data-dir` defaults to `~/.cache/vss-devx-search` (where DSS downloads land;
honours `XDG_CACHE_HOME`). It used to be `/tmp/vss-devx-search` — if you have
data there, `mv /tmp/vss-devx-search ~/.cache/` rather than re-downloading.
`--subset` selects a different JSON file in the same folder; **all subsets share
one `videos/` directory.**


### Two supported shapes

**Legacy** — the value *is* the ground-truth segment list. Every existing
dataset. Works as-is, but every query uses one fixed retrieval path:

```json
{"queries": {
  "Person dropping a box": [
    {"video_name": "warehouse_sample",
     "start_time": "2025-01-01T00:00:05Z",
     "end_time": "2025-01-01T00:00:10Z"}
  ]
}}
```

**Extended** — adds the decomposition, which is what enables per-query routing:

```json
{"schema_version": 2,
 "queries": {
  "Person wearing a hardhat dropping a box": {
    "segments": [
      {"video_name": "warehouse_sample",
       "start_time": "2025-01-01T00:00:05Z",
       "end_time": "2025-01-01T00:00:10Z"}
    ],
    "decomposition": {
      "query": "person dropping a box wearing a hardhat",
      "attributes": ["person wearing a hardhat"],
      "has_action": true,
      "source_type": "video_file"
    },
    "expected_path": "fusion"
  }
}}
```

Both load. The script normalizes them, so the scoring code never learns which
form it came from. **Note `run_eval.py` cannot read the extended shape** — it
expects the value to be the segment list.

### Why decompositions exist

The agent runs an **LLM query-decomposition step** before retrieval. The CLI
does not — it takes an already-structured request. So feeding raw natural
language to the CLI would compare *"decomposed then retrieved"* against
*"retrieved raw"*, which is not a retrieval comparison at all.

The decomposition is what the agent would have produced. Its contract is
`QUERY_DECOMPOSITION_PROMPT` in `vss_agents/tools/search.py`:

| field | meaning |
|---|---|
| `query` | rewritten description — actions **and** attributes |
| `attributes` | person-appearance only; never bare `"person"` |
| `has_action` | true when an action/event is described |
| `video_sources` | named sources, empty if none |
| `source_type` | `video_file` or `rtsp` |
| `timestamp_start` / `timestamp_end` | ISO-8601, base date 2025-01-01 |
| `object_ids` | explicit tracked-object ids |
| `top_k` | only when the query says so |

### How the path is chosen

Derived from the decomposition — no guessing:

| `object_ids` | `attributes` | `has_action` | → path |
|---|---|---|---|
| present | — | — | `object` |
| — | present | `true` | `fusion` |
| — | present | `false` | `attribute` |
| — | empty | — | `embed` |

`attribute` is the no-action case because that path cannot express an action.
`fusion` is the both case: the embedding leg carries the action, the attribute
leg re-ranks by appearance. A decomposition missing `has_action` routes to
`fusion`, because fusion still returns the embedding leg's candidates while
`attribute` would silently drop every action-based match.

> **Attributes are matched semantically, not literally.** Attribute search
> embeds your attribute text with RTVI-CV and runs kNN against per-object visual
> embeddings. There is no attribute vocabulary to match against, and none is
> needed. This also means `--attribute person` is useless: it is close to every
> detected person, so it ranks nothing.

---

## Clip-level retrieval (schema_version 3, `task: "clip"`)

Some datasets (e.g. `physicalAI-event-videos-test`) are **clip-level**: the gallery is a set of short clips and a query is relevant to whole clips, not time-bounded segments within a long video. The scorer matches by `video_name` (no overlap, no 5-second grid), so the ground truth is a list of clip **stems** (no `.mp4`): the search core returns `<stem>_<timestamp>_<hash>.mp4`, and `video_name_matches` strips `.mp4` from the retrieved name and prefix-matches the GT string -- a GT string with `.mp4` would never match.

```json
{"schema_version": 3, "task": "clip",
 "queries": {
   "<query text>": {
     "relevant_clips": ["CHAD_1_086_1_0", "..."],
     "query_domain": "event", "query_slice": "specific",
     "query_id": "event_0000", "n_relevant": 1,
     "decomposition": {"query": "...", "attributes": [], "has_action": true, "source_type": "video_file"}
   }
 }}
```

- `relevant_clips` are stems (no extension). The preprocessing adapter (`flows/preprocess/make_clip_dataset.py`) resolves the raw DSS `chunk_id` -> clip path -> stem, and runs once after `--skip-download` is omitted.
- `decomposition` is synthesized from `query_domain` (event -> `has_action: true` -> fusion; pas -> `attributes: [query]` -> attribute) and used **only** as the fallback when the live decomposer is down. Live decomposition stays the default.
- `near_universal` queries (relevant to almost every clip) do not discriminate retrieval and are excluded from scoring; the run summary records the count.
- **HIT@k is capped at `--top-k`.** Clip scoring has no 5-second segment expansion, so ranks are 1:1 with retrieved clips and `hit@k`/`recall@k` for `k > top-k` are unmeasurable. Default `--top-k 10` reports `HIT@{1,5,10}`; pass `--top-k 100` for the full curve (`HIT@{1,5,10,50,100}`).
- The anchor check (`verify_anchor`) is skipped for clip runs -- scoring is by `video_name`, not absolute-timestamp overlap.
- `event`/`pas` subsets are written as `dataset_event.json` / `dataset_pas.json` so `--subset event` or `--subset pas` selects a single query domain.
