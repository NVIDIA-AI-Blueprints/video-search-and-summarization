---
name: video-search
description: Search video archives using natural language — find events, objects, actions, and people across recorded video using fusion search (Cosmos Embed1 semantic search + CV attribute search). Use when asked to search for something in video, find actions and events, locate objects and people, or query video archives. For these types of questions, default to this top-level fusion search unless user specifies otherwise. Requires the search profile to be deployed.
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

This search orchestrated by VSS agent can lead to 3 behaviors:
- Attribute-only: when the LLM decomposes the query and finds only appearance attributes with no action (e.g. "person wearing red jacket")
- Embed-only: when the query has no extractable attributes (e.g. "show me forklifts")
- Fusion: when the query has both an action and attributes (e.g., "person in red jacket running"), it runs embed search first, then reranks using attribute search

---

## Mandatory workflow

When using this skill, ALWAYS follow this high-level workflow:
1. Resolve inputs from user instructions
2. Run the search(es) via approach chosen
3. Present the results to the user query. Format response as a professional inspection report but name it `Video Search Results`:
   — Use clear section headers
   - Organize findings individually with supporting detail, and close with a summary
   - Use tables where comparisons help. Write like a technical report, not a chat message.
4. CRITICAL: Verify the results and explain this to the user concisely. 
   If search fails, or returns unexpected results (i.e. videos that do not appear to match user query, zero matches, zero videos returned, error etc.), STOP. Do not proceed without reading [troubleshooting.md](references/troubleshooting.md) to iterate with feedback loops until proper results are found and presented like a professional inspection report.
5. Final verifications:
   - ALWAYS inform user that final and further verifications can be run. Present this as a `Verification Step`
   - ONLY IF user agrees, download screenshots using the `screenshot_url` of the best candidates (highest similarity scores) from the search hits (JSON results) to `/tmp`. Read them and verify if they correspond to the user query

## Input resolution

Infer these inputs only from the conversation or user query (no other files unless provided). If some cannot be inferred, ask the user immediately:
- $HOST_IP: where the VSS agent backend runs

---

## Gotchas

- ALWAYS step into the troubleshooting step of the workflow immediately if anything unexpected happens, read [troubleshooting.md](references/troubleshooting.md)
- Queries work best with **concrete visual descriptions** (objects, actions, locations). Augment user queries if needed to enhance the quality of the questions, expanding potential details
- User queries to do video search supposes videos are already uploaded. No need to search for them locally.
  Assume this unless the findings show the video is not uploaded or ingested yet
- Use `video-analytics` skill to cross-reference search results with incident/alert data

---

## Search via REST API

Default to using this REST API approach, unless user specifies otherwise.

```bash
curl -s -X POST http://${HOST_IP}:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"input_message": "find all instances of forklifts"}' | jq .
```

### More Examples

```bash
# Search by object
curl -s -X POST http://${HOST_IP}:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"input_message": "find vehicles in the parking lot"}' | jq .

# Search by action
curl -s -X POST http://${HOST_IP}:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"input_message": "show me people running"}' | jq .

# Search by time context
curl -s -X POST http://${HOST_IP}:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"input_message": "what happened at the entrance between 2pm and 3pm?"}' | jq .
```

### Advanced control knobs

If user query is ambiguous, user wants more guidance or when fine-grained control is needed, augment the user input by calling out explicitly certain options in plain-text and steering the agent in the desired direction. Available control axes: 

| Axes                 | Type      | Default | Description                                               |
|----------------------|-----------|---------|-----------------------------------------------------------|
| `video sources`      | string[]  | null    | Filter to specific cameras or sensor names                |
| `top k`              | int       | null    | Max results (default: around 10 from config)              |
| `minimum similarity` | float     | 0.0     | Min similarity threshold; raise (e.g. 0.3) to filter noise|
| `critic usage`       | bool      | true    | VLM verifies each result and removes false positives      |
| `description`        | string    | null    | Filter by camera metadata (e.g. location, category) if metadata is available|

Pick and choose some of these tuning options. Adjust them as needed for the user’s situation and query. 
For examples of discovery modes leveraging these, see [discovery_modes.md](references/discovery_modes.md).

---

## Search via Agent UI

Open `http://${HOST_IP}:3000/` and type natural-language queries:

```
find all instances of forklifts
show me people near the loading dock
when did a truck arrive at the gate?
find someone wearing a red jacket
```

Results include timestamped clips with similarity scores.

---

## Interact via Browser (agent-browser)

```bash
npx agent-browser --auto-connect open http://${HOST_IP}:3000
npx agent-browser --auto-connect wait --load networkidle
npx agent-browser --auto-connect snapshot -i
```

Find the chat input, enter a search query, and snapshot results.
