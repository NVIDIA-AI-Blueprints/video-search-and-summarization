---
name: timeline
description: Build a cross-camera timeline for a person or object using embedding-based re-identification. Runs a semantic search, has the user confirm the correct candidate, then uses the object's behavior embedding to find further appearances across cameras via KNN cosine similarity in Elasticsearch. Fetches the matching clips from VST, analyzes each with the VLM, and synthesizes a chronological timeline. Use for queries like "timeline for the person in green vest", "track white-shirt worker across cameras", "where did the forklift go". Requires the search profile to be deployed.
version: "3.1.0"
license: "Apache License 2.0"
---

<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Track Subject Across Cameras

Orchestrates `video-search` (initial fusion search) → Elasticsearch KNN re-ID → `vios` (clip retrieval) → `video-summarization` (per-clip VLM analysis) to produce a chronological timeline of a specific subject's appearances across multiple cameras.

The default flow has two user-facing moments: confirm the correct candidate (Step 1), then return a table of sightings (Step 8). Steps 2–7 run internally. The user can then opt in to additional outputs — timeline summary, Gantt chart, PDF report, or a specific sub-clip — as separate follow-up requests. See [`../AGENTS.md`](../AGENTS.md) for chat-channel output conventions.

## Inputs

Subject description (required) plus optional filters (`top_k`, `video sources`, `similarity_threshold`), all parsed from the natural-language query. See the `video-search` skill for fusion-search axes. `similarity_threshold` (default **0.9**) controls the KNN cutoff in Step 3.

## Endpoint resolution

Resolve once and cache for the run:

| Endpoint | Source |
|---|---|
| Search agent | host URL, local or remote |
| Elasticsearch | same host as search agent, port `9200` |
| VST | inferred from `screenshot_url` in search results |
| VLM | `VLM_BASE_URL` env → deployment `.env` → ask user |

Use the same VLM as the active search deployment so descriptions remain consistent with the search critic.

---

## Workflow

### 1. Candidate confirmation

Call `video-search` with the subject description and `top_k` (default `5`). Walk top hits in similarity order; for each candidate:

1. Fetch a short overlay clip via `vios` with `configuration.overlay.bbox` set to the hit's `object_ids` and `showObjId: true`.
2. Reply with exactly two lines: a `MEDIA:` line attaching the overlay clip, and the question "Is this the subject you want tracked? (yes/no)". No other text — no preface like "Got 5 candidates. Let me fetch an overlay clip…" or "Showing candidate #1 for confirmation". Extracting search results, picking a candidate, and fetching the overlay are internal steps; do not narrate them.
3. On `yes`: save `(video_name, object_id, start_time, end_time)` and proceed to Step 2.
4. On `no`: continue to the next candidate; if exhausted, report "no more candidates, refine the description".

Extract the VST host from any `screenshot_url` for later clip URL fetches.

### 2. Fetch the seed behavior embedding

Query `mdx-behavior-*` for the seed embedding, pinning to the exact `(video_name, object_id, time window)` from Step 1:

```bash
cat > /tmp/seed_query.json <<'JSON'
{
  "size": 1,
  "query": {
    "bool": {
      "filter": [
        { "term":  { "sensor.id.keyword": "<video_name>" } },
        { "term":  { "object.id.keyword": "<object_id>" } },
        { "range": { "timestamp": { "gte": "<start>", "lte": "<end>" } } }
      ]
    }
  },
  "_source": ["sensor.id", "object.id", "timestamp", "end", "embeddings.vector"]
}
JSON
curl -s -X POST "<es-endpoint>/mdx-behavior-*/_search" -H "Content-Type: application/json" -d @/tmp/seed_query.json
```

Save `hits[0]._source.embeddings[0].vector` to `/tmp/seed_vec.json`. Behavior events are already condensed by object-tracking and provide stable seed vectors; the per-frame `mdx-raw-*` index is too noisy for this purpose.

If no embedding is available, ask the user to pick a different candidate, or proceed using only the Step 1 candidates without re-ID expansion.

### 3. KNN similarity search

Use the seed vector against `mdx-behavior-*`, applying any `video sources` filter from the original query and `min_score` from `similarity_threshold`:

```bash
cat > /tmp/build_knn.py <<'PY'
import json
v = json.load(open("/tmp/seed_vec.json"))
body = {
    "knn": {
        "field": "embeddings.vector",
        "query_vector": v,
        "k": 500,
        "num_candidates": 1000,
        "filter": [
            { "terms": { "sensor.id.keyword": ["<video_name_1>", "<video_name_2>"] } }
        ]
    },
    "min_score": 0.9,
    "_source": ["sensor.id", "object.id", "timestamp", "end"],
    "size": 500
}
json.dump(body, open("/tmp/knn_body.json", "w"))
PY
python3 /tmp/build_knn.py
curl -s -X POST "<es-endpoint>/mdx-behavior-*/_search" -H "Content-Type: application/json" -d @/tmp/knn_body.json
```

Score interpretation: `1.0` self-match, `0.9+` same subject, `0.8–0.9` same subject in short or noisy tracks, `<0.8` likely different. Each hit becomes a `(video_name, object_id, start_time, end_time, similarity)` tuple.

Map `video_name` → VST sensor UUID once via `GET http://<vst-host>/vst/api/v1/sensor/list` (matching `name == video_name`). The `vios` clip-URL endpoint requires the UUID.

### 4. Group windows per sensor

Merge two windows only when both conditions hold:
- Same `sensor_id`.
- Time ranges overlap or are directly adjacent.

Windows from different sensors with overlapping wall-clock times are simultaneous observations and must remain separate. Windows on the same sensor with a time gap between them are distinct visits and must not be merged.

### 5. Fetch clip URLs

For each merged `(sensor_id, start, end)`:

```bash
curl -s "http://<vst-host>/vst/api/v1/storage/file/<sensor_id>/url?startTime=<start>&endTime=<end>&container=mp4&disableAudio=true"
```

Carry the returned `videoUrl` together with `(sensor_id, start, end, video_name)` through Steps 6 and 7. Verify the response's `streamId` matches the requested `sensor_id`; discard any row where the returned `startTime` differs from the requested one by more than a few seconds.

### 6. VLM analysis per clip

Use the `video-summarization` skill's VLM-direct path. Skip its HITL prompt-confirmation gate — timeline supplies a fixed prompt below; pass it directly without prompting the user.

Run requests concurrently with a cap of 6 (`ThreadPoolExecutor(max_workers=6)`, `asyncio.Semaphore(6)`, or `xargs -P 6`).

Prompt (substitute `<subject>` from the user's query):

```
Focus only on <subject>. For this clip:
1. If the subject is NOT present, respond with exactly: SUBJECT NOT FOUND
2. Otherwise describe what they do, where they move, and what they interact with in 1–3 sentences.
3. Then append a structured event list:

EVENTS:
- <start_sec>-<end_sec>s: <short event description>
- <start_sec>-<end_sec>s: <short event description>

Use seconds-into-clip (not wall-clock). One line per discrete event. Keep descriptions under 10 words.
```

The `EVENTS` block enables Step 9 (sub-clip extraction) by mapping textual event references to time offsets.

### 7. Filter mismatches

Discard clips whose response is `SUBJECT NOT FOUND`. A high rejection rate usually indicates over-strict attribute terms in the prompt (per Step 6) rather than identity mismatch.

### 8. Default output: sightings table

Send a single reply consisting of:

- Title line: `Timeline Analysis — <subject>`
- A table with columns: Time (UTC), Sensor, Summary. One row per surviving sighting, ordered by `start_time`. Cells should be short (≤15 words).

That is the entire default reply. After it, the user can ask follow-up questions — summary, chart, report, or a specific clip — each handled by a step below.

Retain in memory across the session: the surviving sighting tuples `(video_name, sensor_uuid, start_time, end_time, similarity)` and the per-clip VLM responses (free-form prose plus the `EVENTS` block) so follow-ups can be served without re-running Steps 2–7.

---

## Follow-up steps (on user request)

### 9. Timeline summary

When the user asks for a summary, narrative, or how the subject moved across cameras, return a single paragraph combining the per-clip VLM captions chronologically. Describe transitions between sensors, simultaneous appearances, dwell time, repeat visits, and the final exit. The reader should be able to reconstruct the subject's full path and activities from the summary alone.

### 10. Gantt chart

When the user asks for a chart or visual of the timeline, render a PNG Gantt: sensors on the y-axis, time on the x-axis, one bar per sighting. Save the PNG to the working directory and attach it via a `MEDIA:` line pointing at that path.

### 11. PDF report

When the user asks for a report or PDF, generate one containing the title, the Step 8 table, the Step 9 summary, the Step 10 Gantt chart, and one screenshot per surviving sighting (peak-similarity timestamp on each sensor, fetched via VST's `replay/stream/<sensor_uuid>/picture` endpoint). Save the PDF to the working directory and attach it via a `MEDIA:` line pointing at that path.

### 12. Sub-clip extraction

When the user asks for a clip of a specific moment (e.g. "send me the clip where they place the box on shelf D"):

1. Match the request against `EVENTS` lines from Step 6.
2. Compute the wall-clock window: parent clip's `start_time` + the matching event's `start_sec`/`end_sec`, padded by ±1–2 seconds.
3. Fetch via `vios`, optionally with `configuration.overlay.bbox` to highlight the subject.
4. Attach the clip via `MEDIA:`.

---

## Notes

- `similarity_threshold` of `0.9` is the validated default. Lower values include more track fragments; higher values may drop legitimate distant sightings.
- After Step 4's strict merge, same-sensor windows within ~10 seconds of each other can optionally be combined into a single appearance cluster to absorb brief tracker-reset fragments without merging genuinely distinct visits.
- If KNN returns only the self-match, fall back to using the Step 1 candidates without re-ID expansion.
- All timestamps are ISO 8601 UTC; retain the trailing `Z`.

## Related skills

- `video-search` — Step 1 (fusion search and candidate selection)
- `vios` — Step 5 (clip URL retrieval)
- `video-summarization` — Step 6 (VLM-direct path; the timeline skill bypasses the HITL gate and supplies its own fixed prompt)
- `deploy` — provides `ELASTIC_SEARCH_PORT` and `VLM_BASE_URL` for the active deployment
