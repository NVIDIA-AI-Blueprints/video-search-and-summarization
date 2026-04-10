---
name: embed-search
description: Run raw semantic/embedding search against video archives using Cosmos Embed1 — no query decomposition, no attribute reranking. Use when asked for straight semantic search, embedding search, similarity search, or when the user wants fast results without the full fusion pipeline.
metadata:
  { "openclaw": { "os": ["linux"] } }
---

# Embed Search

> **Alpha Feature** — not recommended for production use.

Direct access to the Cosmos Embed1 semantic search API. Converts a natural language query to a vector embedding and finds the most similar video segments — no query decomposition, no attribute reranking.

**Use the `video-search` skill instead** for general queries. Use this only when the user explicitly wants to hit the embed search API directly or bypass the full fusion pipeline.

---

## When to Use

- "Try the embed search API directly"
- "Run a raw semantic search for forklifts"
- "Hit the embed search endpoint, skip the agent"

Not for: general "search for X" queries — those go through `video-search`.

---

## API

```
POST http://localhost:8000/api/v1/search
```

### Parameters

| Field | Type | Description |
|---|---|---|
| `query` | string | Natural language search query |
| `source_type` | string | `"video_file"` for uploaded videos, `"rtsp"` for live streams |
| `agent_mode` | bool | Set to `false` for direct API access (bypasses agent pipeline) |
| `top_k` | int | Max results to return |
| `start_time` | string | ISO 8601 start time filter |
| `end_time` | string | ISO 8601 end time filter |
| `video_sources` | array | Filter by source names or sensor IDs |

---

## Examples

### Semantic search, video file

```bash
curl -s -X POST http://localhost:8000/api/v1/search \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "forklift near shelving unit",
    "source_type": "video_file",
    "agent_mode": false,
    "top_k": 5
  }' | python3 -m json.tool
```

### RTSP stream with time filter

```bash
curl -s -X POST http://localhost:8000/api/v1/search \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "person running",
    "source_type": "rtsp",
    "agent_mode": false,
    "top_k": 10,
    "start_time": "2025-01-01T09:00:00Z",
    "end_time": "2025-01-01T10:00:00Z",
    "video_sources": ["building_a_cam"]
  }' | python3 -m json.tool
```

---

## Tips

- Results are ranked by cosine similarity — the closer to 1.0, the better the match
- Use `video_sources` to narrow search to specific cameras or files
- Use `start_time` / `end_time` to search within a time window
- For higher precision with person attributes, use `attribute-search` skill instead
