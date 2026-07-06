# Query Decomposition for `vss-cli search run`

Use this reference before every `vss-cli search run` call that is more complex than a plain object/action query. The CLI and `lib.search_core` do not call the VSS agent `/generate` API and do not run NAT query decomposition; the host agent must produce the same structured fields explicitly.

## Output Contract

Prefer passing one JSON object through `--decomposed-json`:

```json
{
  "query": "person in a white jacket climbing a ladder",
  "original_query": "Who climbed the ladder in the warehouse clip?",
  "source_type": "video_file",
  "video_sources": ["sample-warehouse-ladder"],
  "description": null,
  "timestamp_start": null,
  "timestamp_end": null,
  "top_k": 5,
  "min_cosine_similarity": 0.0,
  "attributes": ["white jacket"],
  "has_action": true,
  "object_ids": null,
  "use_critic": true
}
```

Required:
- `query`: concrete visual search text. Preserve the action/object noun; do not over-summarize.
- `source_type`: `video_file` for uploaded files, `rtsp` for stream embeddings.
- `agent_mode`: do not include it; the CLI always sets `agent_mode=false`.

Optional:
- `video_sources`: resolved source names or sensor IDs from `vss-manage-video-io-storage`.
- `attributes`: person/appearance attributes such as `white jacket`, `red hard hat`, `dark pants`.
- `has_action`: `true` when the request binds an action/event to the subject, `false` for appearance-only search.
- `object_ids`: integer tracked-object IDs when the user asks for similar objects.
- `use_critic`: omit to preserve the search runtime default. Set `true` when the user explicitly needs high-confidence verification, relational correctness, or asks to confirm candidates. Set `false` only for latency-sensitive searches where verification should be skipped.

## Routing

- **Embed-only**: no useful `attributes` and no `object_ids`. Example: `{"query":"forklift near the loading bay","source_type":"video_file"}`.
- **Attribute-only**: appearance query with no action. Use `attributes` and `has_action=false`. Example: `person wearing a white jacket`.
- **Fusion**: action plus attributes. Use both a complete `query` and `attributes`, with `has_action=true`. Example: `person in a white jacket climbing a ladder`.
- **Object re-search**: user names tracked IDs or asks for similar objects. Use `object_ids`; this searches behavior embeddings directly and skips query embedding.
- **Critic verification**: omit `use_critic` to follow the deployed profile default; set `use_critic=true` to require VLM wiring and verify final candidates with subject-anchored criteria; set it `false` to skip verification for latency.

## Attribute Rules

Keep attributes specific and visually detectable.

- Good: `white jacket`, `red hard hat`, `dark pants`, `blue shirt`, `carrying a backpack`.
- Bad: `person`, `forklift`, `ladder`, `running`. Single-word generic nouns and actions are not attribute filters.
- If the user asks for “red forklift,” keep it in `query`; do not put `red` alone in `attributes`.
- If the user asks for “person in a red jacket running,” use `query="person in a red jacket running"`, `attributes=["red jacket"]`, `has_action=true`.

## VLM Critic Media

The skill wrapper passes the deployed VLM service into the CLI as explicit flags.

- Local or local-shared VLM: use `--vlm-media-mode video-url` with internal VST URLs for lowest latency.
- Remote VLM, no audio requirement: use `--vlm-media-mode frame-base64`, mirroring NAT's remote-VLM frame sampling behavior.
- Remote audio-capable Omni-style VLM with `ENABLE_AUDIO=true`: use `--vlm-media-mode video-base64` and `--vst-clip-enable-audio` to preserve MP4 audio.
- Do not call `/generate` just to use the critic. The CLI critic path calls the configured OpenAI-compatible VLM endpoint directly.

## Mock Outcome

For a query like “find the person in a white jacket climbing a ladder and verify it,” a healthy mock run should show:

1. One Cosmos text-embedding call for the full action query.
2. One video-embedding Elasticsearch search.
3. One RTVI-CV text-embedding call for `white jacket`.
4. One behavior-index search for attribute/fusion reranking.
5. One VST clip URL request for each critic candidate.
6. One VLM chat-completions request per critic candidate.
7. Output rows with `object_ids` from attribute/fusion search. Surviving rows carry `critic_result.result` set to `confirmed` or `unverified`; a `rejected` verdict prunes that hit from the output (and, when `--search-max-iterations > 1`, excludes it and re-searches for a replacement) rather than leaving it annotated in place.
