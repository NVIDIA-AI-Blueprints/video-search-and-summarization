# `vss search run` reference

One CLI for Compose and Kubernetes. Endpoints come from the deployment recorded
by `vss configure`; the command takes none.

Run the `vss` console executable from the `vss` project in the checkout
(`--no-dev` keeps the sync runtime-only — no NAT or dev tooling):

```bash
VSS_REPO_ROOT="${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}"
test -f "${VSS_REPO_ROOT}/services/agent/pyproject.toml" || {
  echo "VSS checkout not found at ${VSS_REPO_ROOT}; set VSS_REPO_ROOT explicitly" >&2
  exit 1
}
cd "${VSS_REPO_ROOT}" &&
uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev --extra cli \
  vss search run <path> [options]
```

The executable is provided by that project and need not exist globally. Do not
use `which vss`; verify the supported entry point directly:

```bash
uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev --extra cli \
  vss search run --help
```

Keep `--extra cli` on every project-local invocation; the base meta package
does not install the `nvidia-vss-cli` distribution that declares `vss`.

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

## The four paths

| command | fields | services required |
|---|---|---|
| `run embed` | `--query` | Elasticsearch, RT-Embed |
| `run attribute` | `--attribute` (repeatable) | Elasticsearch, RT-CV |
| `run fusion` | `--query` + `--attribute` | Elasticsearch, RT-Embed, RT-CV |
| `run object` | `--object-id` (repeatable) | Elasticsearch, RT-CV |

Each path accepts only its own fields. `run embed` has no `--attribute`;
`run attribute` and `run object` have no `--query`. A path whose services are
absent exits 4 naming them, before any request.

## Query controls

Shared by every path: `--source-type`, `--video-source` (repeatable),
`--timestamp-start`, `--timestamp-end`, `--top-k`.

```bash
# Embed-only
run embed --query "red forklift" --source-type video_file --top-k 10

# Time-bounded named-source search
run embed --query "person at entrance" --video-source entrance-camera \
  --timestamp-start "2025-01-01T14:00:00" --timestamp-end "2025-01-01T15:00:00"

# Fusion
run fusion --query "person in white jacket running" --attribute "white jacket"
```

`--source-type` selects the index partition for a media kind from a fixed
uploads anchor (never a discovered index): `video_file` targets that anchor and
`rtsp` targets everything else, so `rtsp` returns only live-stream documents and
`video_file` only uploaded-file documents, whether scoped to a `--video-source`
or run unscoped, and regardless of ingestion order (stream-first, upload-first,
or mixed).

`--video-source` is matched **literally** against the index — the CLI does no
name↔id resolution or VST validation, so an unknown source silently returns
nothing (not an error). Validating a named source is the skill's job (SKILL.md
step 2), not the CLI's.

## Retrieval tuning

`--fusion-method weighted_linear|rrf|rrf_with_attribute_rank`, `--w-embed`,
`--w-attribute`, `--rrf-k`, `--rrf-w`, `--top-percent-filter`,
`--embed-confidence-threshold`, `--min-cosine-similarity`.

`--no-merge-adjacent` reports raw retrieval windows. By default contiguous
same-sensor windows merge into one result whose score is the mean of the merged
windows — expect fewer, longer results with averaged scores.

## Output and exits

JSON on stdout (`SearchOutput.data`). `--raw` compact, `--pretty` indented.

| exit | meaning |
|---|---|
| 0 | success |
| 2 | invalid input (unknown flag, bad value) |
| 3 | backend unreachable |
| 4 | configuration — not configured, foreign config, or a required service absent |
| 5 | not found — a searched index does not exist (nothing ingested yet) |

Search automatically attempts bounded visual verification through
`vss_core.critic` when `vss configure` discovered both VST and an RT-VLM model.
When those services are available, the critic attempts every returned hit.
Every hit contains `verification.result`: `confirmed`, `rejected`, or
`unverified`. Verification is fail-open: a missing VLM, inaccessible clip, or
critic failure does not fail retrieval and leaves the affected hit
`unverified`. There are no critic or VLM flags; deployment discovery remains
the single source of endpoints and model ids.

Only when every displayed hit is `unverified` may the host ask whether the user
wants them checked through the separate `vss-ask-video` workflow. If even one
hit is `confirmed` or `rejected`, do not offer or invoke that fallback.

Index names and model ids come from `vss configure show`. Never pass or infer an
index and never read `ELASTIC_SEARCH_INDEX`; it names only the embedding index
and must not be reused as the behavior or raw index.

Never provide secrets through CLI flags. Kubernetes Secret values are not read
by this command.

`vss search run` is read-only. For upload, registration, deletion, or
repair, use the agent-backed mutation workflows in the parent skill.
