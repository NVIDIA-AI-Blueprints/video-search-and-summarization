---
name: attribute-search
description: Search video archives by visual appearance attributes — clothing, colors, physical features. Use when asked to find a person by what they look like (no action involved): "person wearing red jacket", "man with blue backpack", "woman with long hair". Does not use embedding search — queries the CV attribute index directly.
metadata:
  { "openclaw": { "os": ["linux"] } }
---

# Attribute Search

> **Alpha Feature** — not recommended for production use.

Direct access to the CV attribute search API. Finds objects/people by visual appearance attributes (clothing, colors, physical features) without semantic embedding — queries the behavior index directly.

**Use the `video-search` skill instead** for general natural language queries. Use this only when the user explicitly wants to call the attribute search API directly.

---

## When to Use

- "Try the attribute search API for person wearing red jacket"
- "Run an attribute search for blue backpack"
- "Hit the attribute search endpoint directly"

Not for: "find a person running" (has action — use `video-search`) or general queries.

---

## API

```
POST http://localhost:8000/api/v1/attribute_search
```

### Parameters

| Field | Type | Default | Description |
|---|---|---|---|
| `query` | string or array | required | Attribute description(s) — e.g. `"person wearing red jacket"` or `["person", "red hat"]` |
| `source_type` | string | `"video_file"` | `"video_file"` for uploaded videos, `"rtsp"` for live streams |
| `top_k` | int | `1` | Max results to return |
| `min_similarity` | float | `0.3` | Minimum cosine similarity, 0.0–1.0 |
| `fuse_multi_attribute` | bool | `false` | See below |
| `video_sources` | array | `null` | Filter by source names or sensor IDs |
| `timestamp_start` | string | `null` | ISO 8601 start time filter |
| `timestamp_end` | string | `null` | ISO 8601 end time filter |
| `exclude_videos` | array | `[]` | List of `{sensor_id, start_timestamp, end_timestamp}` to exclude |

### `fuse_multi_attribute`

- **`false` (default for direct use)** — returns `top_k` results independently per attribute. Use this when calling attribute search directly.
- **`true` (used by fusion search)** — combines object IDs found across attributes into a single result when they appear in the same frame. Only meaningful over a narrow time window where the same objects can co-occur in a single frame. Do not set `true` over large time ranges.

---

## Examples

### Single attribute, video file

```bash
curl -s -X POST http://localhost:8000/api/v1/attribute_search \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "person wearing red jacket",
    "source_type": "video_file",
    "top_k": 5,
    "min_similarity": 0.3,
    "fuse_multi_attribute": false
  }' | python3 -m json.tool
```

### Multiple attributes (independent results per attribute)

```bash
curl -s -X POST http://localhost:8000/api/v1/attribute_search \
  -H 'Content-Type: application/json' \
  -d '{
    "query": ["person wearing red jacket", "person wearing blue jeans"],
    "source_type": "video_file",
    "top_k": 5,
    "min_similarity": 0.3,
    "fuse_multi_attribute": false
  }' | python3 -m json.tool
```

### RTSP stream with time filter

```bash
curl -s -X POST http://localhost:8000/api/v1/attribute_search \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "person wearing beige shirt",
    "source_type": "rtsp",
    "top_k": 5,
    "min_similarity": 0.3,
    "fuse_multi_attribute": false,
    "video_sources": ["warehouse_cam_1"],
    "timestamp_start": "2025-01-01T13:00:00Z",
    "timestamp_end": "2025-01-01T14:00:00Z"
  }' | python3 -m json.tool
```

---

## Tips

- Works best with specific visual descriptions: clothing color, item type, physical features
- Pass multiple attributes as an array to get independent `top_k` results per attribute
- Lower `min_similarity` (e.g. `0.1`) returns more results; raise it (e.g. `0.6`) to filter noise
- Use `video_sources` to limit search to specific cameras or uploaded files
- Keep `fuse_multi_attribute: false` unless calling from a fusion pipeline with a narrow time window
