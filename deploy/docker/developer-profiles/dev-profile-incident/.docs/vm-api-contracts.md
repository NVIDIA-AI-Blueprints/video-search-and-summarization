# VM API contracts (frontend reference)

HTTP contracts of the native `vss-agent` on `kwanz-ws` and the SSH tunnel the frontend uses to reach them. VM side only. Source of truth: `services/agent/src/vss_agents/api/` (Pydantic models) and `custom_fastapi_worker.py` (route registration). Last checked against main on 2026-11-01; the live VM was last checked on 2026-10-24 (below).

## Tunnel

`./start.sh --mode vm` opens one backgrounded `ssh -N -L ...` (standalone: `.scripts/tunnel.sh`). The frontend always addresses `http://localhost:<port>`.

| Local port | VM target | Service | Frontend use |
|---|---|---|---|
| `8000` | `127.0.0.1:8000` (loopback) | native `vss-agent` (FastAPI) | all endpoints below; set `INCIDENT_AGENT_BASE_URL=http://localhost:8000` (start.sh exports it) |

- Nothing else is forwarded, by start.sh or `.scripts/tunnel.sh`. The console uploads each video straight to R2 through its own `/api/uploads/r2` route, so it never calls the VST upload or sensor API and creates no VST camera. The `:7777` proxy (`vss-haproxy-ingress`) and VIOS/VST, with the Redis they needed, are removed from the deployment: not started by start.sh and being deleted from the VM ([decision](decisions.md)). Phoenix (`:6006`, the trace viewer used for eval) stays.
- In mock mode `127.0.0.1:7777` is the mock backend instead (it serves the incident search, revision search, chat, analyze and evidence-embeddings routes). There is no tunnel.
- A laptop port 8000 already in use stops start.sh before the tunnel opens: `laptop port 8000 is already in use by <holder>`.
- After opening, start.sh polls `localhost:8000/health` for up to 60 s. Run `./.scripts/tunnel-check.sh` to check tunneled routes.

### When the VM is unreachable

start.sh probes SSH before it opens the tunnel and, when the VM or a tunnelled health check fails, prints the cause and the next step; the causes are listed in [`incident-profile-operations.md`](incident-profile-operations.md#ssh-username-resolution--troubleshooting).

## Common behaviour

- Base URL `http://localhost:8000`, JSON bodies, `Content-Type: application/json`. No authentication.
- Validation errors (FastAPI): `422` with `{"detail":[{"type","loc","msg","input"}]}`.
- Handler errors: `{"detail": "<message>"}` with the status given per endpoint.
- Routes are registered on every profile; all incident routes are unconditional.

## `GET /health`

Reachable at `/health` on `:8000`. Returns `200` `{"value":{"isAlive":true}}`. Use it for liveness only.

## `POST /api/v1/incidents/search`

Natural-language search over evidence crops (entity, instrument, asset) with the VLM critic off by default (`INCIDENT_SEARCH_CRITIC`). Contract: search-contract-v1. Incident search (`/revisions/search`) shares its embed-then-retrieve step (`semantic_search` in `incident_search.py`); the two routes differ only in what they retrieve.

Request (`extra` fields are rejected with 422):

| Field | Type | Default | Constraint |
|---|---|---|---|
| `query` | string | required, except with `filters.video_id` | 1-500 chars; may be empty only when `filters.video_id` is set (lists that incident's evidence, no embedding call) |
| `top_k` | int | 10 | 1-50 |
| `min_similarity` | float | 0.0 | 0.0-1.0 (no cutoff by default: top_k by score) |
| `hide_rejected` | bool | true | only matters when the critic is on: drop hits it marked `rejected` from `hits[]` (`rank` keeps its pre-filter value) |
| `filters.incident_types` | string[] | none | |
| `filters.min_severity`, `filters.max_severity` | int | none | 1-5 |
| `filters.item_types` | `"entity"\|"instrument"\|"asset"`[] | all three | |
| `filters.video_id` | string | none | 1-64 chars; searches only that video (`videos.id`), applied inside `search_evidence_items(p_video_id)` before ranking, so the top `top_k` all belong to it. With an empty `query` the hits are the video's crops in video time order, `similarity_score` 0 |

Response `200`:

| Field | Type | Notes |
|---|---|---|
| `query_id` | string | `q-<12 hex>` |
| `query` | string | echo |
| `total_hits` | int | `len(hits)`; 0 is a normal answer |
| `execution_time_ms` | float | server time |
| `hits[]` | | ranked, `rank` starts at 1 |
| `hits[].result_id` | string | `res-<32 hex>` (36 chars, fits `query_results.id` VARCHAR(36)) |
| `hits[].rank` | int | |
| `hits[].similarity_score` | float 0-1 | |
| `hits[].item` | object | `item_type`, `item_id`, `name`, `label`, `description`, `threat_level` (int or null), `holder_entity_id` (string or null), `crop_image_url`, `timestamp_seconds` (int), `bounding_box` (null or `[ymin, xmin, ymax, xmax]`, 4 normalised ints) |
| `hits[].incident` | object | `incident_id`, `model_run_id`, `incident_type`, `severity_level` (1-5), `title` (string or null), `description`, `playback_url`, `incident_window` (`[start_s, end_s]`) |
| `hits[].critic` | object | `verdict` (`confirmed`, `rejected`, `unverified`), `confidence` (0-1, null, or observed 0.0), `rationale` |

Behaviour:
- The critic is dormant: unless the agent runs with `INCIDENT_SEARCH_CRITIC` set to a truthy value (`1`, `true`, `yes`, `on`), no VLM call is made and every hit is `verdict: "unverified"` with the rationale `Critic not run`. `hide_rejected` then has nothing to hide.
- With the critic on, `hide_rejected` (default true) excludes hits marked `rejected` from `hits[]` (`rank` retains its pre-filter value), and a failing critic never drops a hit: it degrades to `verdict: "unverified"` with the failure in `rationale`.
- `filters.video_id` needs migration `20261028090000_search_evidence_items_video_id.sql`; the agent sends `p_video_id` only for a scoped search, so an agent ahead of the migration still serves unscoped searches.
- `crop_image_url` and `playback_url` are resolved from R2 by the agent. Use them as given.
- Errors: 422 on validation. `503` `{"detail":"Query embedding unavailable: ..."}` when the query cannot be embedded: an embedder error, or no embedding API key with the Supabase store (the deterministic placeholder embedding is only used with the in-memory store). A store failure is not caught, so expect `500` (plain-text `Internal Server Error`, not the `detail` shape).
- Timing: embedding plus one pgvector query, with no critic delay while the critic is off. With the critic on, calls are spaced by `INCIDENT_SEARCH_CRITIC_DELAY_SECONDS` (default 1.0 s between hits, 30 s timeout each), so latency grows by about one second per hit; use a client timeout of at least 90 s and a loading state in that case.
- Side effect: each call writes one `queries` row and one `query_results` row per hit (audit trail, failures only logged).

Example (illustrative hit shape built from the Pydantic models; the live VM returns hits as detailed in the retest reports):

```bash
curl -s localhost:8000/api/v1/incidents/search -H 'content-type: application/json' \
  -d '{"query":"edged weapon","top_k":5,"filters":{"item_types":["instrument"],"min_severity":3}}'
```

```json
{
  "query_id": "q-f563deb1f578",
  "query": "edged weapon",
  "total_hits": 1,
  "execution_time_ms": 3612.4,
  "hits": [{
    "result_id": "res-1a2b3c4d5e6f70818293a4b5c6d7e8f9",
    "rank": 1,
    "similarity_score": 0.89,
    "item": {
      "item_type": "instrument", "item_id": "I1", "name": "machete", "label": "machete",
      "description": "Long curved blade held by assailant during storefront altercation.",
      "threat_level": 4, "holder_entity_id": "E1",
      "crop_image_url": "https://<r2-host>/evidence/uploads/Assault007_x264.mp4/run-1/I1.jpg",
      "timestamp_seconds": 14, "bounding_box": [320, 450, 510, 600]
    },
    "incident": {
      "incident_id": "Assault007_x264", "model_run_id": "m86a5c319e26659804b0",
      "incident_type": "assault", "severity_level": 4,
      "title": "Physical altercation with edged weapon in ATM lobby",
      "description": "An altercation ensues where a perpetrator brandishes a large blade against a victim.",
      "playback_url": "https://<r2-host>/videos/anomaly/assault/Assault007_x264.mp4",
      "incident_window": [10.0, 28.0]
    },
    "critic": {"verdict": "confirmed", "confidence": 0.96, "rationale": "The crop shows a machete held in the assailant's hand."}
  }]
}
```

## `POST /api/v1/incidents/evidence-embeddings`

Called by the console when a revision becomes a video's searchable revision: made official, a new AI revision of a video with no official one, or, before a Save, the new human revision's evidence. It embeds each item with the model search uses (`nvidia/llama-nemotron-embed-vl-1b-v2`, 1024 dimensions, `passage` input of `"{item_name}: {description}"`). The agent writes nothing; the console writes the `evidence_crops` rows.

Request: exactly one of `{"revision_id": <int >= 1>}` (the route reads that revision's `evidence`) or `{"evidence": [{"item_type", "item_id", "item_name"?, "description"?}, ...]}` (nothing is read). Response `200`: a list of `{"item_type", "item_id", "embedding"}` (empty when there are no items). Errors: `404` unknown revision, `422` neither or both sources, `502` embedding service failed, `503` database or embedding key not configured.

## `POST /api/v1/incidents/revisions/search`

Searches saved report revisions by their summary embedding. The route embeds `query` with the search query model (1024 dimensions, `query` input type), then calls `search_report_revisions`. Only revisions with a stored vector are searched: AI revisions saved with an embedding; human revisions and AI revisions saved while the embedding endpoint was unavailable are not. The zero-GPU mocks answer this route from their finals snapshot.

Request: `{"query": <1-1000 chars>, "top_k": <1-50, default 10>, "min_similarity": <-1.0 to 1.0, default 0.0>}`. Response `200`: a list, best first, of `{revision_id, video_id, revision_no, kind, model_run_id, title, description, incident_type, severity_level, start_seconds, end_seconds, similarity_score}`; empty when nothing matches. Errors: `422` invalid body, `502` embedding service failed, `503` database or query embedding key not configured.

## `POST /api/v1/incidents/chat`

Answers a question from stored evidence (`chat-contract-v1`, [`contracts/chat-contract-v1/`](../contracts/chat-contract-v1/); code `services/agent/src/vss_agents/api/incident_chat.py`). The console calls it from `POST /api/chat`.

Request: `{"session_id"?: <1-128 chars>, "messages": [{"role": "user"|"assistant"|"system", "content": <non-empty>}, ...] (at least one `user` message), "context"?: {"scope": "global"|"incident", "incident_id"?, "evidence_crop_ids"?: [...]}, "version_id"?: <int >= 1>, "draft_report"?: {...}}`. With `version_id` (a `report_revisions.id`) the answer is grounded in that version's own evidence; `draft_report` (an unsaved edit of that version) requires `version_id`, else `422`.

Response `200`: `{"session_id", "message": {"role": "assistant", "content", "citations": [{"incident_id", "item_id", "item_type", "timestamp_seconds", "crop_image_url"}, ...]}}`. Errors: `400` no non-empty `user` message, or a `version_id` that belongs to another incident than `context.incident_id`; `404` unknown `version_id`; `502` evidence search or revision error; `503` report revisions unavailable.

## `POST /api/v1/incidents/charts`

Draws the figures it is given with the `chart_generator` tool. The dashboard's chart requests call it from the console server; the route chooses and computes nothing. It needs `chart_generator` in the agent's config (`dev-profile-base/vss-agent/configs/config.yml`). The mock has no such route, so chart requests show an error there.

Request: `{"charts_data": [<bar or pie>], "output_dir": "<dir>", "file_prefix": "<prefix>"}`. A bar is `{"title", "chart_file_format": "png", "x_categories": [...], "series": {"<name>": [...]}, "x_label", "y_label"}`; a pie is `{"title", "chart_file_format": "png", "sizes": [...], "labels": [...]}`.

Response `200`: one entry per chart, `{"success", "error_message", "object_store_key"}`. `GET /static/<object_store_key>` returns the image and `DELETE` the same path removes it. The store is in memory, so images do not survive an agent restart. `422` for a body that does not validate, `501` when the profile does not configure the tool, `500` when it fails.

## `POST /api/v1/incidents/{incident_id}/analyze`

Runs the `incident_report_gen` tool (P1 extraction, then RP1 prose report) on an uploaded video. Called by `incident-console-v2` in agent mode; `incident_id` is the console's `videos.id`. Field-level report schema: [analysis-schema.md](analysis-schema.md).

Request:

| Field | Type | Default | Notes |
|---|---|---|---|
| `video_url` | string | required | signed R2 URL, min 1 char |
| `video_key` | string | none | 1-900 chars; the video's R2 key (the console sends it). Evidence is stored under `evidence/<video_key>/run-<n>/`; without it the incident id names the folder |
| `model_run_id` | string | none | 1-20 chars; when given the caller owns the `model_runs` row (the agent inserts only a placeholder if it is absent) |
| `model` | string | agent `VLM_NAME` | a model id from the `contracts/inference.json` allowlist; anything else is `400` |
| `reviewer_prompt` | string | none | max 1000 chars (`422` above). Added to the report prompt for this run only, framed as `ADDITIONAL REVIEWER CONTEXT FOR THIS VIDEO`; copies nothing from earlier revisions |
| `skip_rp1` | bool | false | P1 only; `report_text` stays null |
| `video_duration_seconds` | number | none | video length in seconds as the caller read it; a finite number above 0, else `422`. Without it the agent reads the MP4 header from `video_url` (ranged GETs) |
| `experiment_prompt_id` | string | none | **Experimental**, prompt experiments only (below): a prompt id from `contracts/experiments/registry.json` |
| `skip_crops` | bool | false | **Experimental**, prompt experiments only (below): skip post-P1 evidence cropping |
| `max_crop_calls` | int 1-200 | null | **Experimental** (below): the crop-call cap of a crops-on experimental request (grounding HTTP attempts, retries included); required when an experimental request crops |
| `blur_faces` | bool | false | Privacy blur: pixelate the heads of the people in every stored evidence frame and crop (below). The console sends it (`true` only) for a video with `videos.blur_faces` on; model calls still see the unblurred images |

Response `200`: an `IncidentReportGenOutput` JSON object, not the bare report: `report` (the validated contract report under `core-scored-v1`), `report_text` and `report_text_error` (RP1 output; `null` when `skip_rp1`), `model`, `contract_version`, `raw_output` (the raw P1 text, which the console re-validates and records), `finish_reason`, `p1_attempts` (every P1 call of the request, in order: `attempt`, `outcome` `valid`/`retried`/`failed`, `finish_reason`, `output_chars`; two entries when the first output was cut off by length or over 30,000 characters and the identical request was retried once, never repaired), and, when the database is configured, `revision_id` (the new `report_revisions` row; `null` if the best-effort write failed) and `evidence` (one entry per entity, instrument and asset: `item_type`, `item_id`, `timestamp_seconds`, for entities also `first_seen_seconds` and `last_seen_seconds` (falling back to `timestamp_seconds` for reports without them), `frame` (R2 key of the whole frame, `<itemId>-frame.jpg`) or `null`, `image` (R2 key of the crop) or `null`, `bounding_box` (the padded crop box in that frame) or `null`, `box_seconds` (the second the frame was read at: the requested one, or +-1 or +-2 s when the detector found nothing there), `located` (`false` when the object was not found at any of those seconds: a frame, no crop, no box), `image_gap` reason when there is no crop, and the zoomed second look's `zoom_checked` (true when the zoomed second look named the item from its close-up), `zoom_confidence` (the second look's 0-1 confidence, the lowest of its calls, null when unchecked), `zoom_seen` (what the two blind looks found: `{frame, close_up}`, names for objects and descriptions for people), `name_before` (the whole-clip name when the second look corrected it, else null; `description_before` beside it), `zoom_gap` (why the item was not checked or a correction was ignored: flag off, not located, below the 0.7 threshold, call failed); `report` carries the corrected name and description too), and the video-bounds outcome: `video_duration_seconds` (the length `WINDOW_BEYOND_VIDEO` and `TIMELINE_BEYOND_VIDEO` used, or `null`), `video_duration_source` (`request`, `mp4_header` or `null`), `video_bounds_checked` and `video_bounds_unchecked_reason` (why the rules did not apply, e.g. `video length unknown: a ranged read of the video returned HTTP 403`). Takes tens of seconds to minutes (VLM calls); use a long timeout.

`tracking` (only when `INCIDENT_TRACKING_ENABLED` is on and the request is not experimental; otherwise the key is absent): `{enabled, matched, tracks, elapsed_ms, skipped}`. `matched` counts report entities given a track, `tracks` the person tracks found, and `skipped` is `null` or why tracking did not run (for example `no frames from perception`, `ffprobe not found on the agent host`, `perception failed: <error type>`). A tracking failure never fails the analysis: every entry keeps its ordinary evidence. The console copies the object as returned into the run's notes (`response.tracking` in `model_runs.notes`).

`privacy` (only when the request sent `blur_faces: true` and cropping ran; otherwise the key is absent): `{blur_faces, regions, frames, heads: {track, detector, box, none}, head_queries, failed}`. `regions` counts the head regions blurred over all stored frames and `frames` the frames with at least one; `heads` says where each person item's head came from (a person track covering it, one detector call for `the head of <label>`, the top of its own box when the detector found none, or `none` for a person neither located nor found); `head_queries` counts those detector calls (an experimental request's `max_crop_calls` counts them too) and `failed` the items whose images were dropped because the blur failed (an unblurred image is never stored instead). Each evidence entry gains `blur_regions`: the normalised `[ymin, xmin, ymax, xmax]` head regions pixelated in its frame (`[]` when none). Heads come from every person track's visible sample within 0.5 s of the frame's second (all tracks, not only matched ones; a track's tracker-only boxes more than 0.6 s after its last detection are drift and skipped) and from the person items at that second (a person item counts as covered by a track only when the track is solid: at least 2 detections and at least half its samples real; otherwise `heads.detector` or `heads.box`, and a detected head outside the item's own box is blurred along with the box's head); the head region of a box is its top 30 %, raised and widened by 10 %. The crop is cut from the blurred frame. The P1 call and the zoomed second look see the unblurred images. The console copies the object into the run's notes (`response.privacy`) and shows a **Faces blurred** badge for such a run ([data](data.md#privacy-blur)).

`tracking.marks` (set-of-marks prompting: only when `INCIDENT_SOM_ENABLED` is on as well; otherwise the key is absent): `{rendered, parsed, fallback, invalid, skipped, elapsed_ms}`. `rendered` is whether P1 saw the marked copy (numbered person boxes drawn on the clip); `parsed` counts entities given a track by the number in their label, `fallback` those given one by box overlap, `invalid` the numbers given that named no track present at the entity's second (or one already taken); `skipped` is `null` or why no copy was rendered (for example `no tracks to mark (tracking skipped: ...)`, `ffmpeg is not installed on the agent host`, `rendering the marked copy failed: ...`, `uploading the marked copy failed (<error type>)`), and `elapsed_ms` the tracking, rendering and upload before P1. With the copy rendered, P1 gets one more user message about the marks, and the bracketed number is stripped from every entity `label` in `report` (`raw_output` keeps it). The copy lives under `scratch/analyze/<video key>/<run id>-marks/` and is deleted right after P1. With the flag on, tracking runs before P1, so it no longer overlaps the locate step.
`object_tracking` (only when `INCIDENT_OBJECT_TRACKING_ENABLED` is on, the request is not experimental and crops are not skipped; otherwise the key is absent): `{enabled, seeds, tracked, tracker, elapsed_ms, time_cap_reached, skipped, detector, items}`. `seeds` counts the located instruments and assets, `tracked` those given a track, `tracker` the OpenCV tracker used (`CSRT`, `KCF` or `MIL`, whichever the installed build has), `time_cap_reached` whether the 120 s cap stopped the run, `skipped` is `null` or why it did not run (for example `no instrument or asset was located`, `no evidence image store is configured (R2 settings missing)`, `the installed OpenCV has no CSRT, KCF or MIL tracker`, `object tracking failed: <error type>`), `detector` the object tracker's own detector-call budget (`CropCallBudget.summary()`: `{cap, calls, refused_by_cap, cap_reached, tokens, calls_without_usage}`, separate from the locate step's) and `items` one object per seed keyed by item id: `{samples, forward_samples, backward_samples, detector_calls, detector_errors, reanchors, lost, capped, elapsed_ms, first_seen_seconds, last_seen_seconds}` (`lost` counts the passes, forwards and backwards, that ended on a lost item; `capped` is `null`, `time` or `detector-budget`). A tracked item's evidence entry gets `track`, `track_first_seen_seconds`, `track_last_seen_seconds` and `track_source: "object-tracker"`, and its `frame`, `image`, `bounding_box` and `box_seconds` are those of the track's best moment. A failure never fails the analysis. The report's own `first_seen`/`last_seen` are not changed. The console copies the object as returned into `model_runs.notes.response.object_tracking`.

Object tracking environment (agent process): `INCIDENT_OBJECT_TRACKING_ENABLED` (off unless `1`, `true`, `yes` or `on`), `INCIDENT_OBJECT_TRACKING_FPS` (4), `INCIDENT_OBJECT_TRACKING_DETECT_INTERVAL_SECONDS` (2.5), `INCIDENT_OBJECT_TRACKING_MIN_IOU` (0.3), `INCIDENT_OBJECT_TRACKING_SPAN_SECONDS` (12, each way from the seed), `INCIDENT_OBJECT_TRACKING_MAX_SECONDS` (120) and `INCIDENT_OBJECT_TRACKING_MAX_DETECTOR_CALLS` (80).

Person tracking environment (agent process): `INCIDENT_TRACKING_ENABLED` (off unless `1`, `true`, `yes` or `on`), `NVSTREAMER_BASE_URL` (default `http://localhost:31000`), `PERCEPTION_PROVISION_URL` (`http://localhost:9000`), `VIDEO_ANALYTICS_API_URL` (`http://localhost:8081`), `INCIDENT_TRACKING_MIN_IOU` (0.3) and `INCIDENT_TRACKING_END_MARGIN_SECONDS` (20) and `INCIDENT_SOM_ENABLED` (set-of-marks prompting; off unless `1`, `true`, `yes` or `on`, and needs R2 settings for the scratch copy). `ffmpeg` and `ffprobe` must be on the agent host's PATH (a missing one skips tracking; `native-services.sh` provides both). Each run uses its own sensor id `incident-<hash of the video key>-<run id>` and always removes its stream and its NvStreamer file.

Errors:

| Status | `detail` |
|---|---|
| 422 | validation (including an invalid `video_duration_seconds`), or contract violation (including a window beyond the video): object with `message`, `raw_output`, `finish_reason`, `model`, `p1_attempts` (the last rejected output is `raw_output`), `contract_version` (nothing persisted by the agent; the caller records the attempt) |
| 501 | string: `incident_report_gen tool is not configured on this profile` |
| 504 | string: `Incident analysis timed out: ...` (P1 timeout) |
| 502 | string: `Video model returned no answer: ...`: the VLM answered HTTP 200 with a null body or no choices (`VlmNoAnswerError`). The message says the video is probably in an unsupported format (for example 10-bit H.264) or unreadable. Nothing is stored; an 8-bit H.264 re-encode of the file is the likely fix (inferred, not proven) |
| 500 | string: `Analysis failed: ...` |

Example:

```bash
curl -s localhost:8000/api/v1/incidents/<videos.id>/analyze -H 'content-type: application/json' \
  -d '{"video_url":"https://<signed-r2-url>","model_run_id":"m86a5c319e26659804b0","skip_rp1":true}'
```

The agent logs the tool input with `video_url` reduced to its object key (no query, credentials or fragment), so a failed call leaves no usable presigned URL in the log. The field is a `str` subclass (`SignedUrl`) whose `repr` is redacted, because NAT logs a failed call's input as the plain kwargs dict (a model `__repr__` alone would not cover it).

Live check of the error shape: `{}` body returns `422` `{"detail":[{"type":"missing","loc":["body","video_url"],"msg":"Field required","input":{}}]}`.

### Caption pass (chunked captioning, `INCIDENT_CHUNKING_ENABLED`)

Off by default in code; `native-services.sh` starts the VM agent with it on (`1`) unless its env file sets `0`. Only the exact value `1` turns it on (`true` does not). With `INCIDENT_CHUNKING_ENABLED=1` in the agent's environment, a production request (no experimental field) runs a first pass before P1 ([`services/agent/src/vss_agents/utils/chunk_captions.py`](../../../../../services/agent/src/vss_agents/utils/chunk_captions.py)): the clip is downloaded once from `video_url`, cut with `ffmpeg` into windows of `INCIDENT_CHUNK_WINDOW_SECONDS` (20) overlapping by `INCIDENT_CHUNK_OVERLAP_SECONDS` (2) (a trailing window under 3 s is merged into the previous one), each window is uploaded to `scratch/analyze/<video_key>/<model_run_id>/<index>.mp4`, captioned by the P1 model with NVIDIA's stock caption prompt (`INCIDENT_CHUNK_NUM_FRAMES`, 32, requested; at most `INCIDENT_CHUNK_CONCURRENCY`, 4, calls in flight; 429, 5xx and transport errors retried three times), and the timed captions are sent to P1 as one more user message (`CONTEXT FROM A FIRST PASS OVER THE SAME VIDEO`, with the instruction to trust the video and to take timeline seconds from the video, not the window bounds; windows the model described as a static view with no activity are left out). The prompt file, `inference.json`, the schema and every other request field are unchanged. The scratch prefix is deleted when the pass ends, whether or not it succeeded. Clips of `INCIDENT_CHUNK_MIN_CLIP_SECONDS` (30) or less are not chunked. Why: [decision](decisions.md#2026-10-31-chunked-captioning-before-p1-the-gateways-fixed-2-fps-and-ignored-num_frames).

The captions are context for P1 only: the response does not return them and nothing stores them ([decision](decisions.md#2026-11-01-alert-rules-and-the-caption-display-are-removed-the-caption-pass-stays-as-report-context)). The agent log records the pass (`caption pass for <id>: N windows, N calls, N retries, N ms`) or why it was skipped (`caption pass skipped for <id>: <reason>`): the clip is at or under the minimum, the video length is unknown, `ffmpeg` is not on the agent host's `PATH`, R2 is not configured, a window call failed after its retries, or a setting is invalid. A skipped pass never fails the analysis: P1 runs without context. Experimental requests (`experiment_prompt_id`, `skip_crops` or `max_crop_calls`) never run it, so eval runs are exactly as before.

Host requirement: `ffmpeg` on the agent's `PATH`. The VM has no system `ffmpeg`; `native-services.sh` installs a static build into the repo-local `.tools/ffmpeg/bin` and puts it first on the agent's `PATH` ([operations](incident-profile-operations.md#default-analysis-stack-in-vm-mode)).

### Experimental settings (prompt experiments, PR 6B)

`experiment_prompt_id`, `skip_crops` and `max_crop_calls` exist for the eval prompt experiments and the end-to-end profile ([`eval/experiments/README.md`](../eval/experiments/README.md)). **A request without any of them is a production request: the same P1 body, persistence, cropping (uncapped) and response keys as before.**

- **Opt-in:** an experimental request is refused with **403** unless the agent process runs with `INCIDENT_EXPERIMENTS_ENABLED=1` (exactly `1`). The shared VM agent does not set it.
- **Prompt:** read from `contracts/experiments/registry.json` in the checkout, or from `INCIDENT_EXPERIMENT_REGISTRY`, and served only if the file matches its registered SHA-256 and the entry's contract version is the vendored one. Otherwise **400**. Only the prompt text of the P1 body changes.
- **400:** combined with `reviewer_prompt`; an experimental crops-on request without `max_crop_calls`; `max_crop_calls` with `skip_crops`. (Outside 1-200 it is 422.)
- **Crop-call cap** (`max_crop_calls`): the request's grounding calls share one counter, checked before every HTTP attempt (the detector's own retries included), so the request never sends more than the cap (each retry second of an item not found at its own second is one more call). An item that would exceed it keeps `image: null` and `image_gap: "crop-call cap reached: ..."`. The shared detector is never changed: each request uses a budgeted copy. The zoomed second look's calls (up to three per located item: two blind looks and, when they agree, a text-only merge; `INCIDENT_ZOOM_NAMES_ENABLED`) are not grounding calls and are outside this cap; with `skip_crops` nothing is located, so no second look runs.
- **409:** cropping is on (`skip_crops` false) while the agent has R2 settings. An experiment agent must hold no shared-storage settings at all.
- **Experimental crops** (`skip_crops` false, no R2): images go to a fresh in-memory store for that request only. They are discarded on return, never written to disk or R2, and `experiment.evidence_store` says so. Evidence `image` keys therefore point at nothing durable.
- **Never persisted:** an experimental request writes no `report_revisions` row and no `model_runs` placeholder (`revision_id` is `null`). With `skip_crops` every evidence entry has `image: null` and `image_gap: "evidence cropping skipped (skip_crops)"`.
- **Response additions:** `experiment` (`prompt_id` (null for the production prompt), `prompt_sha256`, `skip_crops`, `max_crop_calls`, `persisted: false`, and for a crops-on request `evidence_store` and `crop`: `elapsed_ms` (the crop stage's wall time), `items`, `images`, `gaps` (count per reason), `cap`, `calls`, `refused_by_cap`, `cap_reached`, `tokens` and `calls_without_usage`), `p1_elapsed_ms` (the P1 HTTP round trip) and `usage` (P1 token usage). A 422 contract violation of an experimental request carries the same three in `detail`.
- **Where it runs:** a local agent started for the experiment with no Supabase or R2 settings (plan decision D14 (b)). Restarting or reconfiguring the shared VM agent is ask-first.
