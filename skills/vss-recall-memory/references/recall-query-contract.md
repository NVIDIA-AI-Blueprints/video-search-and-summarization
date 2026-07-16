# Recall query contract

Pass exactly one typed operation to `recall_memory.py` through standard input. Unknown fields are rejected.

## Exact record lookup

```json
{
  "operation": "get",
  "record_id": "summary:11111111-1111-4111-8111-111111111111",
  "record_type": "video_summary",
  "include_related": true
}
```

`record_type` is optional. Set `include_related` to `true` to reconstruct a summary with its event objects.

## Filtered or text search

```json
{
  "operation": "search",
  "query_text": "forklift near miss",
  "record_type": "video_event",
  "video_id": "22222222-2222-4222-8222-222222222222",
  "time_range": {"start_seconds": 0, "end_seconds": 60},
  "semantic": false,
  "limit": 10
}
```

Every search field is optional, but `semantic: true` requires `query_text`. Supported record types are
`video_summary`, `video_event`, `alert_record`, `search_session`, and `search_hit`; only summary and event persistence
are implemented in the first slice.

Success returns `status: complete` and a `results` array. An exact lookup with no match returns an empty array and is
not an execution failure. Semantic search searches nested summary and event passages, groups matches
by `summary_id`, and returns the complete parent summary with all related events rather than returning detached chunks.
