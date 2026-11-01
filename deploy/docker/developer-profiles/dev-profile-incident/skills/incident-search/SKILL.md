---
name: incident-search
description: Use to search incident evidence, ask grounded follow-up questions, inspect critic verdicts, or populate and check evidence crops through the incident profile's local mock or VM agent. Not for generating a report (use incident-analyze-video), batch evaluation (use incident-run-eval), or database migrations (use incident-manage-database).
license: Apache-2.0
metadata:
  version: "0.1.1"
  profile: "dev-profile-incident"
  tags: "incident search chat evidence crops retrieval"
---
# Incident Search

Use this skill for MVP2 natural-language search across the incident archive, grounded chat about search results, and checking how evidence crops reach search. Search and chat are always-on in the profile; there is no `--search` startup flag.

Canonical facts:

- Architecture, storage and contracts: [`.docs/mvp2/README.md`](../../.docs/mvp2/README.md)
- Current corpus and live status: [`.docs/status.md`](../../.docs/status.md)
- Search request/response: [`search-contract-v1`](../../contracts/search-contract-v1/README.md)
- Chat request/response: [`chat-contract-v1`](../../contracts/chat-contract-v1/README.md); grounded mode (`INCIDENT_CHAT_MODE=grounded`, off by default) answers with [`chat-contract-v2`](../../contracts/chat-contract-v2/README.md)
- Crop implementation and R2 layout: [`.docs/data.md`](../../.docs/data.md) and [`.docs/incident-profile-operations.md`](../../.docs/incident-profile-operations.md)

## Prerequisites

1. Start the profile with [`incident-start`](../incident-start/SKILL.md):
   - `./start.sh --mode mock` uses the mock backend on `127.0.0.1:7777` and needs no GPU or SSH.
   - `./start.sh --mode vm` uses the native `vss-agent` through the tunnel on `127.0.0.1:8000`.
2. For live search or embeddings, the environment must point at the finals Supabase project. Never write to the frozen midterm project.
3. A search needs populated `evidence_crops`. An empty result set can be a healthy response, but it does not prove the crop corpus is loaded.

## Search saved report revisions

Finds whole incident reports by meaning, using one summary embedding per saved revision (the title and description). Revisions saved without a vector, including human revisions, do not appear. Run it against the VM agent (`localhost:8000`); the mocks answer it from their finals snapshot, so their results say nothing about relevance.

```bash
curl -sS -X POST http://localhost:8000/api/v1/incidents/revisions/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"fight at a bus stop","top_k":5}' | jq .
```

## Search evidence

Send a natural-language query to the backend. The route embeds the query with the same 1024-dimensional text embedding model used for crop metadata, queries `search_evidence_items`, and returns the ranked hits. The VLM critic is off by default (the agent's `INCIDENT_SEARCH_CRITIC`); every hit is then `unverified` with the rationale `Critic not run`.

```bash
# VM mode
curl -sS -X POST http://localhost:8000/api/v1/incidents/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"weapon used during a burglary","top_k":5}' | jq .

# Mock mode (the mock uses the same route shape)
curl -sS -X POST http://localhost:7777/api/v1/incidents/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"crowbar used in burglary","top_k":3}' | jq .
```

The request accepts `query`, `top_k` (1–50), `min_similarity`, `hide_rejected`, and optional `filters` for `item_types`, `incident_types`, `min_severity`, `max_severity` and `video_id` (one incident only; `query` may then be empty and lists that incident's evidence in video time order). Each hit includes:

- `item`: entity, instrument or asset metadata, `timestamp_seconds`, optional normalized `bounding_box`, and `crop_image_url`;
- `incident`: the parent incident, model run, playback URL and incident window; and
- `critic`: `unverified` unless the critic is on, then `confirmed`, `rejected` or `unverified`, with confidence and rationale.

Treat similarity as retrieval ranking, not proof. With the critic on, its verdict is the visual check; otherwise inspect the crop and the source video. A crop URL is a signed/private object URL and may expire; use the parent playback URL and `timestamp_seconds` to inspect the source video when needed. `hide_rejected` is true by default.

The route persists the query and ranked verdicts in `queries` and `query_results`. It does not reorder hits after critic evaluation. With the critic on, the VM critic is rate-limited by `INCIDENT_SEARCH_CRITIC_DELAY_SECONDS` (default 1 second; zero disables the delay); change that only as an explicit VM operations decision.

## Grounded chat

Use chat for a follow-up question rather than treating a free-form answer as an ungrounded report. Send the full conversation history and, when narrowing the question, the incident or evidence crop IDs in `context`.

The console's report page does this through its `/api/chat` route, with `context.scope: "incident"` and the report's video ID as `incident_id`. Its "Ask about this incident" panel also sends `version_id` (the version on screen, or the version being edited) and, while editing, the unsaved draft as `draft_report` (requires `version_id`); citations then come from that version or draft. A question about the official version with no draft finds no evidence, by decision ([`.docs/decisions.md`](../../.docs/decisions.md#2026-10-24-chat-about-the-official-version-uses-the-in-memory-chat-search-client-for-mvp2)).

The console's library (`/library`, Find evidence tab) runs search through its `POST /api/search` route (`{"query", "videoId"?}`), which calls the agent with `top_k` 20 (50 when `videoId` is set, passed as `filters.video_id`) and `min_similarity` 0, then adds each video's searched version to the hits. "See evidence" on a library card sends `videoId` with an empty query to list that incident's evidence.

```bash
curl -sS -X POST http://localhost:8000/api/v1/incidents/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "messages":[
      {"role":"user","content":"Which incidents show a weapon?"},
      {"role":"assistant","content":"I found several evidence items."},
      {"role":"user","content":"What does the crop at the first timestamp show?"}
    ],
    "context":{"scope":"global"}
  }' | jq .
```

Use `context.scope: "incident"` with `incident_id` for one incident, or include `evidence_crop_ids` to focus the answer. A successful response contains an assistant message and structured citations with `incident_id`, `item_id`, `item_type`, `timestamp_seconds`, and `crop_image_url`. When no evidence is relevant, the grounded response has `citations: []`; do not invent a citation. Invalid or empty messages are client errors (400/422).

## How evidence crops reach search

There is no loader script. The analyze route crops each evidence item and uploads it to R2 (`evidence/<video key>/run-<N>/<itemId>.jpg`, plus `<itemId>-frame.jpg` beside it; key tree in [`.docs/data.md`](../../.docs/data.md)), listing the keys in the new `report_revisions.evidence`. Search reads `evidence_crops`, which holds each video's searchable revision (the official one, else the newest): the console calls `POST /api/v1/incidents/evidence-embeddings`, gets `{item_type, item_id, embedding}` back (1024 dimensions, the model search uses) and writes the rows itself. Which call writes them for each event (mark official, analysis or re-run, Save) is in [`.docs/data.md`](../../.docs/data.md#evidence_crops).

```bash
curl -sS -X POST http://localhost:8000/api/v1/incidents/evidence-embeddings \
  -H 'Content-Type: application/json' -d '{"revision_id": 1}' | jq 'length, .[0] | del(.embedding)'
```

`404` means the revision does not exist; `503` means the database or embedding key is not configured. An empty `evidence_crops` table means no crops have been stored yet, not a broken search; a video whose newest revision has no rows is not searchable until its rows are written. Changing the embedding model means re-embedding every searchable revision.

## Verify

```bash
# Agent health and a bounded search smoke test
curl -sS http://localhost:8000/health | jq .
curl -sS -X POST http://localhost:8000/api/v1/incidents/search \
  -H 'Content-Type: application/json' -d '{"query":"weapon","top_k":5}' | jq '{total_hits, hits: [.hits[] | {rank, timestamp: .item.timestamp_seconds, verdict: .critic.verdict}]}'

# Mock-mode smoke test instead: replace :8000 with :7777.
```

For a live run, confirm that crop URLs return an image response, the timestamp seeks into the parent playback window, and `queries`/`query_results` grow in finals. If the route returns hits but query persistence warns, stop and hand off to [`incident-operate-vm`](../incident-operate-vm/SKILL.md); do not silently treat the search as fully verified.

## Boundaries

- Do not change search/chat contracts, Supabase migrations, embedding dimensions, or R2 keys here; those are database or owning-service changes.
- Do not use the search route as a substitute for `incident-analyze-video`; it searches existing evidence only.
- Do not run `supabase db push`, apply a migration, or modify live data without the database procedure and required approval.
