# What a dataset must contain

Ground truth, decompositions, and the layout the runner expects.

## The dataset

Two files per dataset, under `<--data-dir>/<--dataset>/`:

```
<data-dir>/warehouse/dataset.json     queries, ground truth, decompositions
<data-dir>/warehouse/videos/          the .mp4 or .mkv files to ingest
```

`--data-dir` defaults to `$XDG_CACHE_HOME/vss-devx-search` when that variable
is set, otherwise `~/.cache/vss-devx-search` (where DSS downloads land).
It used to be `/tmp/vss-devx-search` — if you have data
there, create the selected cache directory and move the old folder into it,
or pass `--data-dir /tmp/vss-devx-search` directly rather than re-downloading.
`--subset` selects a different JSON file in the same folder; **all subsets share
one `videos/` directory.**


### Two supported shapes

**Legacy** — the value *is* the ground-truth segment list. Every existing
dataset works as-is. The default live decomposer can still route each query;
`--no-decompose` uses one fixed retrieval path:

```json
{"queries": {
  "Person dropping a box": [
    {"video_name": "warehouse_sample",
     "start_time": "2025-01-01T00:00:05Z",
     "end_time": "2025-01-01T00:00:10Z"}
  ]
}}
```

**Extended** — carries a stored decomposition for per-query routing when live
decomposition is off or unavailable:

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
form it came from. The legacy `run_eval.py` in the separate `ci-vss-oss`
repository cannot read the extended shape; use this skill's `run_eval_flows.py`
for it.

### Why decompositions exist

The agent runs an **LLM query-decomposition step** before retrieval. The CLI
does not — it takes an already-structured request. So feeding raw natural
language to the CLI would compare *"decomposed then retrieved"* against
*"retrieved raw"*, which is not a retrieval comparison at all.

Live decomposition uses the deployed LLM and replaces a stored decomposition
for that query. Run with `--no-decompose` to replay the dataset's stored route
for comparisons. The contract is
`QUERY_DECOMPOSITION_PROMPT` in
`services/agent/packages/vss_agents/src/vss_agents/tools/search.py`:

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
