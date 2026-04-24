---
name: video-search
description: Search video archives using natural language — find events, objects, actions, and people across recorded video using Cosmos Embed1 semantic search. Use when asked to search for something in video, find events, locate objects, or query video archives. Requires the search profile to be deployed.
metadata:
  { "openclaw": { "os": ["linux"] } }
---

# Video Search Workflows

> **Alpha Feature** — not recommended for production use.

Search video archives by natural language using Cosmos Embed1 embeddings. Requires the search profile — deploy with the `deploy` skill (`-p search`).

## When to Use

- "Find all instances of forklifts"
- "When did someone enter the restricted area?"
- "Show me people near the loading dock"
- "Search for vehicles between 8am and noon"
- Any natural-language search across video archives

---

## How Search Works

1. **Ingest** — Videos are uploaded or streamed via VIOS. The RTVI-Embed service (Cosmos Embed1) generates vector embeddings for video segments.
2. **Index** — Embeddings are stored in Elasticsearch via the Kafka pipeline.
3. **Query** — Natural-language queries are embedded and matched against stored vectors by similarity.
4. **Results** — Timestamped video segments ranked by relevance, with clip playback links.

---

## Search via Agent UI

Open `http://<HOST_IP>:3000/` and type natural-language queries:

```
find all instances of forklifts
show me people near the loading dock
when did a truck arrive at the gate?
find someone wearing a red jacket
```

Results include timestamped clips with similarity scores.

---

## Search via REST API

```bash
curl -s -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"input_message": "find all instances of forklifts"}' | jq .
```

### More Examples

```bash
# Search by object
curl -s -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"input_message": "find vehicles in the parking lot"}' | jq .

# Search by action
curl -s -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"input_message": "show me people running"}' | jq .

# Search by time context
curl -s -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"input_message": "what happened at the entrance between 2pm and 3pm?"}' | jq .
```

---

## Tips

- Queries work best with **concrete visual descriptions** (objects, actions, locations)
- Embeddings are generated automatically on video upload/ingest — no manual indexing needed
- Use `sensor-ops` skill to upload videos or manage streams feeding into the search pipeline
- Use `video-analytics` skill to cross-reference search results with incident/alert data

---

## Interact via Browser (agent-browser)

```bash
npx agent-browser --auto-connect open http://localhost:3000
npx agent-browser --auto-connect wait --load networkidle
npx agent-browser --auto-connect snapshot -i
```

Find the chat input, enter a search query, and snapshot results.
