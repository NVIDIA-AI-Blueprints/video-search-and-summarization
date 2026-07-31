# Examples of discovery modes

Run host-side
`uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev vss search run <path>`
after `vss configure --base-url "${VSS_ORIGIN}"`. Resolve and validate
`VSS_REPO_ROOT` first. Do not invoke it in a container or pod. Compose and
Kubernetes use the same commands; only the origin differs. Never start a
port-forward.

To have the deployment's LLM decompose a natural-language request instead, POST
it to `${VSS_ORIGIN}/api/v1/search` with `agent_mode: true`.

The search command is retrieval-only. Inspect returned screenshots separately
when the user requests or pre-authorizes visual verification.

## Wide-net discovery — cast the widest net, fast

For exploratory searches when recall matters more than precision. Start broad with a high result count and low similarity threshold, then refine based on returned results.

```bash
--query "find unusual activity" \
  --source-type video_file \
  --top-k 100 \
  --min-cosine-similarity -1
```

Typical follow-ups:
- Take the most promising results and re-run with high-precision mode.
- Scope to cameras/time — if certain cameras or time windows surfaced interesting results, re-run narrowed to those specific video sources and time ranges.
- Search based on attributes — if a person of interest appeared in the results, follow up with `run attribute` or `run fusion` and `--attribute`.

## Narrow to specific cameras and/or time — scope to a known incident

When the camera location and time window are known, pass every known camera with `--video-source` and explicit ISO-8601 timestamps.

```bash
--query "person carrying a box" \
  --source-type video_file \
  --video-source loading_dock_cam \
  --video-source warehouse_entrance \
  --timestamp-start "2025-01-01T22:00:00" \
  --timestamp-end "2025-01-02T06:00:00" \
  --top-k 10
```

For RTSP camera streams, set `--source-type rtsp`.

## High-precision search — raise the similarity bar

When false positives are costly, use a lower result count and higher similarity threshold.

```bash
--query "person wearing high-visibility vest" \
  --source-type video_file \
  --top-k 5 \
  --min-cosine-similarity 0.5
```

## Attribute and fusion search — make decomposition explicit

`vss search run` does not decompose queries. If the user request has appearance attributes and actions, pass them explicitly.

```bash
run fusion \
  --query "person in a red jacket running" \
  --source-type video_file \
  --attribute "red jacket" \
  --top-k 10
```

For attribute-only searches:

`run attribute` takes no `--query`; the attributes are the query.

```bash
run attribute \
  --source-type video_file \
  --attribute "red jacket" \
  --top-k 10
```

## Metadata-based filtering — filter by camera tags

Only useful when cameras are tagged with location or category metadata. First check whether cameras have metadata or tags using the `vss-manage-video-io-storage` skill. If no tags exist, offer to add metadata tags before relying on this filter.

```bash
run embed \
  --query "person running" \
  --source-type video_file \
  --description "parking lot" \
  --top-k 10
```
