# Query decomposition and control preservation

Use this reference before every `vss search run` call that is more complex than
a plain object/action query. The CLI does not decompose queries; the host agent
must produce the structured fields explicitly and choose the path.

To have the deployment's LLM decompose instead, POST the request to
`${VSS_ORIGIN}/api/v1/search` with `agent_mode: true`. It returns conversational
text, not `SearchOutput`.

## Output Contract

The path is the sub-action; the remaining fields are flags. `--json` accepts
the same fields as one object, which explicit flags then override:

```json
{
  "query": "person in a white jacket climbing a ladder",
  "original_query": "Who climbed the ladder in the warehouse clip?",
  "source_type": "video_file",
  "video_sources": ["warehouse-ladder"],
  "description": null,
  "timestamp_start": null,
  "timestamp_end": null,
  "top_k": 5,
  "min_cosine_similarity": 0.0,
  "attributes": ["white jacket"]
}
```

...passed as `vss search run fusion --json '<object>'`, or as flags:
`run fusion --query "..." --attribute "white jacket" --video-source warehouse-ladder --top-k 5`.

Required:
- `query`: concrete visual search text. Preserve the action/object noun; do not over-summarize.
- `source_type`: `video_file` for uploaded files, `rtsp` for stream embeddings.

Optional:
- `video_sources`: resolved source names or sensor IDs from `vss-manage-video-io-storage`.
- `attributes`: person/appearance attributes such as `white jacket`, `red hard hat`, `dark pants`.
- `object_ids`: integer tracked-object IDs when the user asks for similar objects (`run object`, `--object-id`).

There is no `search_mode` field. The sub-action is the route, and each path
accepts only its own fields — passing `--attribute` to `run embed`, or `--query`
to `run attribute`/`run object`, exits 2.

## Routing

- **`run embed`**: no useful attributes and no object ids. Example: `run embed --query "forklift near the loading bay" --source-type video_file`.
- **`run attribute`**: attributes only, no query. Example: `run attribute --attribute "white jacket"`.
- **`run fusion`**: a complete query *and* attributes. Example: `run fusion --query "person in a white jacket climbing a ladder" --attribute "white jacket"`.
- **`run object`**: user names tracked IDs or asks for similar objects. Searches behavior embeddings directly and skips query embedding. Example: `run object --object-id 42`.

## Attribute Rules

Keep attributes specific and visually detectable.

- Good: `white jacket`, `red hard hat`, `dark pants`, `blue shirt`, `carrying a backpack`.
- Bad: `person`, `forklift`, `ladder`, `running`. Single-word generic nouns and actions are not attribute filters.
- If the user asks for “red forklift,” keep it in `query`; do not put `red` alone in `attributes`.
- If the user asks for “person in a red jacket running,” use `run fusion --query "person in a red jacket running" --attribute "red jacket"`.

## Mock Outcome

For a query like “find the person in a white jacket climbing a ladder and verify it,” a healthy mock run should show:

1. One Cosmos text-embedding call for the full action query.
2. One video-embedding Elasticsearch search.
3. One RTVI-CV text-embedding call for `white jacket`.
4. One behavior-index search for attribute/fusion reranking.
5. Output rows with `object_ids` from attribute/fusion search.
6. If verification is authorized, separate screenshot downloads and grounded
   visual verdicts for the selected hits.
