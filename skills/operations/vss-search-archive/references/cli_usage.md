# `vss search run` reference

One CLI for Compose and Kubernetes. Endpoints come from the deployment recorded
by `vss configure`; the command takes none.

Run the `vss` on `PATH` — shipped in the harness images, otherwise installed
from this skill's checkout with `uv tool install <checkout>/libs/vss/cli`:

```bash
vss search run <path> [options]
```

Verify the entry point directly:

```bash
vss search run --help
```

If preflight fails, report its error and stop. Do not manually call
Elasticsearch, embedding, or search endpoints.

Do not invoke it through `docker exec`, `kubectl exec`, or a pod shell.

## Configure once

```bash
vss configure --base-url "${VSS_ORIGIN}"   # probe + record ~/.vss/config.json
vss configure show                          # recorded deployment (indices, models)
vss configure check                         # re-probe; exit 3 if a route went away
```

`~/.vss/config.json` is written 0600 and holds no credentials. Re-run
`configure` after any deployment change.

## The five paths

| command | fields | services required |
| --- | --- | --- |
| `run embed` | `--query` | Elasticsearch, RT-Embed |
| `run attribute` | `--attribute` (repeatable) | Elasticsearch, RT-CV |
| `run tag` | `--query` + `--video-source` (repeatable, optional) | Elasticsearch |
| `run fusion` | `--query` + `--video-source` (repeatable, optional) + optional `--attribute` / `--description` / `--min-cosine-similarity` | Elasticsearch, RT-Embed (RT-CV optional) |
| `run object` | `--object-id` (repeatable) | Elasticsearch, RT-CV |

Each path accepts only its own fields. `run embed` has no `--attribute`;
`run attribute` and `run object` have no `--query`; `run tag` has no
`--attribute`. A path whose services are absent exits 4 naming them, before any
request.

VST is not required by any path: it only mints `screenshot_url` media links
and resolves source names to stream ids. A deployment that exposes
Elasticsearch and the path's retrieval services but not VST still searches;
hits return with an empty `screenshot_url`, and a named `--video-source`
that VST cannot resolve narrows to an empty result rather than failing.

## Query controls

Shared by every path: `--source-type`, `--video-source` (repeatable),
`--timestamp-start`, `--timestamp-end`, `--top-k`, and `--original-query`.
When a caller decomposes the request, `--original-query` carries the exact
pre-decomposition user sentence to the critic; retrieval continues to use the
path-specific query, attributes, or object IDs.

```bash
# Embed-only
run embed --query "red forklift" --source-type video_file --top-k 10

# Time-bounded named-source search
run embed --query "person at entrance" --video-source entrance-camera \
  --timestamp-start "2025-01-01T14:00:00" --timestamp-end "2025-01-01T15:00:00"

# Tag-only (BM25 over VLM tag documents)
run tag --query "forklift loading pallet" --source-type video_file \
  --video-source warehouse_sample --top-k 10

# Fusion (tag + embed + optional attribute)
run fusion --query "person in white jacket running" --attribute "white jacket" \
  --source-type video_file --video-source warehouse_sample
```

`--video-source` is matched **literally** against the index for `embed`,
`attribute`, and `object` — the CLI does no name↔id resolution or VST
validation, so an unknown source silently returns nothing (not an error). For
only `tag` resolves a source name to its VST sensor ID (passing an already-id through); `fusion` matches the sensor ID literally — hand it the preserved sensor ID so the embedding leg's literal filter matches (the tag leg accepts IDs too). For `tag`, an unresolved source is dropped (not carried forward) and yields an empty, narrowed result (exit 0), not an error.
Validating a named source against `vss vios list` is the skill's job (SKILL.md
step 2) either way.

## Retrieval tuning

`--fusion-method weighted_rrf|rrf`, `--w-tag`, `--w-embed`, `--w-attribute`,
`--rrf-k`, `--rrf-w`, `--top-percent-filter`,
`--embed-confidence-threshold`, `--min-cosine-similarity`. At least one
provider weight must be positive; library defaults are `w_tag=0` (VLM tag leg
off by default), `w_embed=0.35`, `w_attribute=0.55`, `rrf_k=60`,
`rrf_w=0.5`, `fusion_method=rrf` (legacy embed + attribute RRF,
no tag leg). Opting into the VLM tag leg with `--w-tag > 0`
auto-selects `weighted_rrf` (the only method that fuses a tag leg);
an explicit `--fusion-method rrf --w-tag > 0` is an input error.

`--no-merge-adjacent` reports raw retrieval windows. By default contiguous
same-sensor windows merge into one result whose score is the mean of the merged
windows — expect fewer, longer results with averaged scores.

## Output and exits

JSON on stdout (`SearchOutput.data`). `--raw` compact, `--pretty` indented.

| exit | meaning |
| --- | --- |
| 0 | success |
| 2 | invalid input (unknown flag, bad value) |
| 3 | backend unreachable |
| 4 | configuration — not configured, foreign config, or a required service absent |
| 5 | not found: a searched index that is not the uploads anchor is missing (an absent anchor returns exit 0 with empty results) |

Search automatically attempts bounded visual verification through
`vss_core.search_core.critic` when `vss configure` discovered both VST and an RT-VLM model.
When those services are available, the critic attempts every returned hit.
Every hit contains `verification.result`: `confirmed`, `rejected`, or
`unverified`. Verification is fail-open: a missing VLM, inaccessible clip, or
critic failure does not fail retrieval and leaves the affected hit
`unverified`. There are no critic or VLM flags; deployment discovery remains
the single source of endpoints and model ids.

Only when every displayed hit is `unverified` may the host ask whether the user
wants them checked through the separate `vss-ask-video` workflow. If even one
hit is `confirmed` or `rejected`, do not offer or invoke that fallback.

Model ids come from `vss configure show`; the CLI never accepts an index. Bases
and family wildcards are pinned, and host-side ES checks use the family
wildcards. Never pass or infer an index and never read `ELASTIC_SEARCH_INDEX`; it
names only the embedding index and must not be reused as the behavior or raw
index.

Never provide secrets through CLI flags. Kubernetes Secret values are not read
by this command.

`vss search run` is read-only. For upload, registration, deletion, or
repair, use the agent-backed mutation workflows in the parent skill. **VLM tag
ingestion** follows the build's notification config: where its tagging items are
enabled, registering the source is enough; where they are not, a caller drives
the controlled JSON-tag `generate_captions` leg — from any host that reaches the
origin on a build that fronts RT-VLM at `/rtvi-vlm`, otherwise loopback-only on
the deploy host. Both are in `vss-manage-video-io-storage`
`references/provision-vios-source.md`.
