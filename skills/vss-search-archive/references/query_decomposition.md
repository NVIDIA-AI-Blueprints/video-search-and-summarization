# Query decomposition and control preservation

For Docker, use this reference before every `vss search run` call that is more
complex than a plain object/action query. The CLI and `lib.search_core` do not
run NAT query decomposition; the host agent must produce the structured fields
explicitly.

For Kubernetes, the VSS Agent performs decomposition behind the public
`${VSS_PUBLIC_URL}/generate` route. Preserve the same fields as explicit prose
in `SEARCH_PROMPT`—especially the resolved source, mode, attributes, time
bounds, and result limit—rather than using the Kubernetes CLI selector or
private backend access.

## Output Contract

Prefer passing one JSON object through `--decomposed-json`:

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
  "search_mode": "fusion",
  "attributes": ["white jacket"],
  "object_ids": null
}
```

Required:
- `query`: concrete visual search text. Preserve the action/object noun; do not over-summarize.
- `source_type`: `video_file` for uploaded files, `rtsp` for stream embeddings.

Optional:
- `video_sources`: resolved source names or sensor IDs from `vss-manage-video-io-storage`.
- `attributes`: person/appearance attributes such as `white jacket`, `red hard hat`, `dark pants`.
- `search_mode`: explicit route: `embed`, `attribute`, `fusion`, or `object`. Do not infer routing inside the library.
- `object_ids`: integer tracked-object IDs when the user asks for similar objects.

## Routing

- **Embed-only**: no useful `attributes` and no `object_ids`. Example: `{"query":"forklift near the loading bay","source_type":"video_file"}`.
- **Attribute-only**: use `search_mode="attribute"` with `attributes`. Example: `person wearing a white jacket`.
- **Fusion**: use `search_mode="fusion"` with both a complete `query` and `attributes`. Example: `person in a white jacket climbing a ladder`.
- **Object re-search**: user names tracked IDs or asks for similar objects. Use `search_mode="object"` with `object_ids`; this searches behavior embeddings directly and skips query embedding. Example: `{"query":"find objects similar to tracked object 42","search_mode":"object","object_ids":[42]}`.

## Attribute Rules

Keep attributes specific and visually detectable.

- Good: `white jacket`, `red hard hat`, `dark pants`, `blue shirt`, `carrying a backpack`.
- Bad: `person`, `forklift`, `ladder`, `running`. Single-word generic nouns and actions are not attribute filters.
- If the user asks for “red forklift,” keep it in `query`; do not put `red` alone in `attributes`.
- If the user asks for “person in a red jacket running,” use `query="person in a red jacket running"`, `search_mode="fusion"`, and `attributes=["red jacket"]`.

## Mock Outcome

For a query like “find the person in a white jacket climbing a ladder and verify it,” a healthy mock run should show:

1. One Cosmos text-embedding call for the full action query.
2. One video-embedding Elasticsearch search.
3. One RTVI-CV text-embedding call for `white jacket`.
4. One behavior-index search for attribute/fusion reranking.
5. Output rows with `object_ids` from attribute/fusion search.
6. If verification is authorized, separate screenshot downloads and grounded
   visual verdicts for the selected hits.
