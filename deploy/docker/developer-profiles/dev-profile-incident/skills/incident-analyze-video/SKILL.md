---
name: incident-analyze-video
description: Use to upload a video clip and generate, re-run, edit, version or mark official a structured incident report (type, severity, confidence, entities, instruments, assets) through incident-console-v2, in gateway (mock) or agent (vm) mode. Not for batch scoring against ground truth - use incident-run-eval.
license: Apache-2.0
metadata:
  version: "0.1.4"
  profile: "dev-profile-incident"
  tags: "incident analysis vlm report review"
---
# Incident Analyze Video

**Identity rule: a report is a revision.** Every analysis creates a new `model_run_id` and appends a `report_revisions` row (`kind='ai'`) for the video; earlier revisions are kept, and re-generation never carries edits across.

Canonical facts:
- Flows: [`.docs/architecture.md` §4](../../.docs/architecture.md#4-runtime-interaction-sequences), sequences A (gateway) and B (agent)
- Report fields and translation: [`.docs/analysis-schema.md`](../../.docs/analysis-schema.md)
- Tables: [`.docs/data.md`](../../.docs/data.md)

## Prerequisites

The stack is running via [`incident-start`](../incident-start/SKILL.md), and `curl -s localhost:3200/api/health` is ready. Prefer short clips, because inference is synchronous.

## Mode routing

| `ANALYSIS_MODE` | Set by | Inference | Who writes Supabase |
|---|---|---|---|
| `gateway` | `start.sh --mode mock` | console -> `vlm-gateway` :8600 -> Brev, with the model chosen in the UI from `contracts/inference.json` (default `VLM_MODEL`), on `incident-contract-v4` | Console writes `videos`, `model_runs` and the AI revision (gateway analysis does not crop, so its evidence entries have no image) |
| `agent` | `start.sh --mode vm` | console -> `vss-agent` `POST /api/v1/incidents/{id}/analyze` -> Brev. P1 only, on the model the console sends (checked against the `contracts/inference.json` allowlist; the agent's `VLM_NAME` applies only when none is sent). The console sends `skip_rp1: true`, matching gateway mode. The console validates the agent's raw P1 output under `core-scored-v1` exactly as in gateway mode. | The console writes `videos` and the `model_runs` record. The agent crops evidence to R2 and appends the AI `report_revisions` row itself (inserting a placeholder `model_runs` row only if the console has not yet). The request also takes `model` and `reviewer_prompt` (max 1000 chars, this run only); the console's reviewer-prompt field is sent as `reviewer_prompt`. |

## Instructions

### Preferred: through the UI

Open `http://localhost:3200`, choose the model, then select the clip (MP4, MKV, MOV or WebM; a phone HEVC `.MOV` is fine). Upload, conversion, analysis and persistence run in one go: after the bytes are up the **Uploading videos** step stays active and shows its elapsed time while the console converts the file to an H.264 MP4 at its original resolution (an H.264 MP4 is stored as it is, and the step says so). The analysis then runs as a background job on the console's server (one at a time across all uploads), so the tab may be closed once the file is uploaded; the job stays in the page's **Analysis jobs** list until it finishes (finished jobs leave it). When the job is done the UI redirects to `/reports/<video-id>?revision=<revision-id>`; a failed job shows its reason, and **Retry analysis** queues it again without re-sending the file. To analyse an already-uploaded video again (any model, optional reviewer prompt), open `/videos/<video-id>` or the report page's re-analysis button in the top bar. Every attempt is a new run and a new AI revision, failed attempts appear in the history only, and choosing the official revision is a separate explicit action.

To analyse several clips, select or drop them together (or press **Add videos**). The tab uploads and converts them one at a time; each then becomes its own background job with its own report, and one failure never stops the rest. Per-row states, **Retry**, **Cancel** and **Cancel batch**: [architecture.md](../../.docs/architecture.md#console-pages).

### Scripted: console API (same path the UI uses)

1. `POST /api/uploads/r2` (multipart `file`, `filename`) probes the file with `ffprobe`, converts it with `ffmpeg` to an H.264 MP4 at the original resolution with no audio unless it already is one (ISO MP4, 8-bit yuv420p H.264), stores the result in R2 and returns `{filePath: "uploads/<name>.mp4", name, converted}`. Uses the console's `ffmpeg` and `ffprobe` (bundled by `npm install`; `FFMPEG_PATH`/`FFPROBE_PATH` override; PATH fallback); a file they reject is 422 with their message, a missing binary 503, a file over 5 GB 413. This is the only upload: nothing goes to VST, no sensor id exists, and the console needs no `:7777` tunnel. Before it, the upload page calls `POST /api/uploads/check` with `{"filename"}` (returns `name` ending in `.mp4`, `unsafeChars`, `truncated`, `exists`) and warns when the name is already stored (the file would be saved as `name-2.mp4`; nothing is overwritten) or has unsafe characters (replaced with `_`); the user confirms with **Continue as <name>**. Upload success includes an R2 `HeadObject` check against the stored byte length. The browser then posts the thumbnail to `POST /api/uploads/thumbnail`.
2. The upload page queues the analysis: `POST /api/jobs` with `{"filepath": "<R2 key>", "filename", "model"?}` returns 201 `{job}` (400 for an unsupported model; the default model is stored when none is sent). The console's loop runs it; poll `GET /api/jobs` (`{jobs}`: running and queued, then the latest 10 finished, each with `status` `queued`/`running`/`done`/`failed`/`cancelled`, `error`, `videoId`, `revisionId`). `POST /api/jobs/<id>/retry` requeues a failed or cancelled job and `POST /api/jobs/<id>/cancel` cancels a queued one (422 otherwise). The loop runs the same analysis as the direct route below.
3. Direct, inside one request (for scripts; the page no longer uses it): `POST /api/analysis` with `{"filepath": "<R2 key>", "filename", "model"?}` (the body is the same in both modes). Before inference the route verifies that the R2 object exists and is nonempty. A 200 response means it also read back `videos`, `model_runs` and the AI `report_revisions` row and confirmed that `videos.filepath` equals the submitted R2 key. A missing object, row, or mismatched key returns an operation-specific 500 instead of a false success. Phone HEVC `.MOV` files no longer reach the video host as HEVC: the upload converted them, so the agent's 502 for an undecodable file is left for files whose H.264 the host still refuses.

Step 1 is awkward from a shell. For scripted re-analysis of an already-uploaded video use `POST /api/videos/<video-id>/runs` with `{"model", "additionalInstruction"?}`: it never touches the video row. Responses: 200 `{run: {..., outcome}}` (`valid_first_pass` or `valid_after_structural_repair`), 422 `contract_failed` with `codes`, 502/504 `request_failed` with `code`; failures are recorded in `GET /api/videos/<video-id>/runs`.

### Direct agent call (vm mode, for debugging the agent)

```bash
curl -s -X POST localhost:8000/api/v1/incidents/<incident_id>/analyze \
  -H 'Content-Type: application/json' \
  -d '{"video_url": "<signed R2 URL>", "model_run_id": "<=20 chars, optional>", "skip_rp1": false}'
```

The `videos` row must already exist (`report_revisions.video_id` references it); the agent does not write `videos`. Without `model_run_id` the agent creates its own run; with one, the caller owns the `model_runs` row and the agent only inserts a placeholder if it is absent. In both cases, when the database is configured, the agent appends the AI revision and returns its `revision_id` (`null` if the best-effort write failed). `skip_rp1` (default `false`) runs P1 only, leaving `report_text` null; otherwise RP1 runs on the model hardcoded in `incident_contract.rp1_request()`. It returns `{report, report_text, report_text_error, model, contract_version, raw_output, finish_reason, revision_id, evidence}`, where `report` is the contract shape with the derived `incident.duration`, after `core-scored-v1` validation and any `id-normalization-v1` repair. Never send `experiment_prompt_id`, `skip_crops` or any other experimental field (`max_crop_calls`, `max_zoom_calls`, `experiment_object_tracking`, `experiment_captions`, `max_caption_calls`) for a real analysis: they are eval-experiment settings, refused unless the agent runs with `INCIDENT_EXPERIMENTS_ENABLED=1`, and their runs are never saved as revisions; experimental crops go to a temporary in-memory store, never R2 ([vm-api-contracts.md](../../.docs/vm-api-contracts.md#experimental-settings-prompt-experiments-pr-6b)).

| Status | Meaning |
|---|---|
| 422 | P1 output failed `core-scored-v1` (a schema or core cross-field violation that `id-normalization-v1` cannot repair), or P1 hit `max_tokens` with no content. The agent persists nothing; `detail` carries `message`, `raw_output`, `finish_reason`, `model` and `contract_version`, and the console records a `contract_failed` (or `request_failed`) attempt in `model_runs.notes`. Timeline-only violations are not a 422. |
| 504 | Analysis timed out |
| 501 | `incident_report_gen` is not configured. The agent is on the wrong config: it must be the dev-profile-base config. |
| 500 | Any other failure. Check `./.scripts/logs.sh vss-agent` on the VM. |

## Revision lifecycle

There is no verify step and no review status. A report is its list of revisions; the report page's versions graph (`GET /api/reports/<videoId>/revisions`) lists them newest first.

- **Page and edit controls:** On desktop in both View and Edit, one vertical separator resizes the video and version-history widths, and the bottom horizontal separator resizes their shared height (190-600 px); the layout survives mode switching for the current page session. In Edit, **Undo** restores the preceding in-browser draft and **Revert all** restores the opened revision. None writes data. `PATCH /api/reports/<videoId>/edit` with `{"parentId", "editedBy", "edit", "note"?}` validates the structured edit, then appends a `human` revision whose parent is the revision that was opened (`save_human_revision_with_crops`). On a video with no official revision it first has the agent embed the new evidence and stores the crops in the same call; if the embedding fails it answers 502 and saves nothing. Every edited item keeps the id of the item it came from, so its evidence and image follow it; the AI revision and `model_runs.notes` are never changed.
- **Evidence image** (`POST /api/reports/<videoId>/evidence-image`, multipart `file` and `itemId`) uploads a reviewer image to a new R2 key (`evidence/<video key>/edit-<N>/<itemId>.jpg`) and returns `{image}`; Save calls it for each held crop before it writes the revision. No existing object is overwritten. A save that fails after its upload leaves an unreferenced image; `POST /api/reports/<videoId>/evidence-sweep` lists those (dry run by default) and with `{"dryRun": false}` removes only the ones no revision or `evidence_crops` row names and that are older than `minAgeHours` (default 24). Do not run it against the live finals bucket without the user's say-so.
- **Evidence display:** report cards and Edit cards show each item's whole video frame with its box drawn on it (object-contain), never a cut-out crop. A new AI item stores its frame (`evidence/<video key>/run-<N>/<itemId>-frame.jpg`) beside the crop; an older item's frame is cut from the stored video through the frame route at its second. An object the detector did not find (at its second or +-1, +-2 s) shows the frame with no box and an "Object not located" note (`located: false`; the former `[0,0,1000,1000]` fallback box reads the same way).
- **Zoomed second look (agent mode):** each located item is named again by the grounding VLM from two blind looks (the frame with its box drawn; that frame plus a native-resolution close-up) merged against the report's text; a confident correction (>= 0.7) rewrites the item's name and description in the evidence entry and the report, keeping `name_before`, `zoom_confidence` and `zoom_seen` (what each look named) on the entry (`zoom_checked`, `zoom_gap` say what happened). An entity's type never changes. Turn it off with `INCIDENT_ZOOM_NAMES_ENABLED=0` in the agent's environment; the RP1 prose still carries the first-pass name.
- **Frame and crop** (`GET /api/reports/<videoId>/frame?t=<seconds>` returns a JPEG; `POST /api/reports/<videoId>/evidence-crop` with `{"timestampSeconds", "region": {"x","y","w","h"}}`, fractions 0-1 of the frame, returns the crop as a JPEG and writes nothing to R2). Edit mode has no Replace image or frame picker; these routes serve the reviewer box tool. A timestamp past the video's end or a rectangle outside the frame is 422; 503 means `ffmpeg` is missing on the console host.
- **Evidence clip** (`POST /api/reports/<videoId>/evidence-clip?revision=<id>&type=<entity|instrument|asset>&item=<id>`) returns `{url}`, a signed URL of about 4 s of the video around the item's second (H.264, no audio). The first call cuts it with the console host's `ffmpeg` and stores `<itemId>-clip.mp4` beside the item's evidence; later calls reuse it. The pop-up's **Play clip** calls it; an item with no second or no stored evidence has no button. Never print the URL.
- **Official** (`PUT`/`DELETE /api/videos/<videoId>/official`) sets or clears `videos.official_revision_id`. Marking asks the agent for the revision's evidence embeddings and rebuilds `evidence_crops`; if the agent is unreachable the mark fails and nothing changes.
- **Re-generation** always makes a new AI revision and copies no earlier edits ([`.docs/analysis-schema.md`](../../.docs/analysis-schema.md#report-revisions-ai-and-human)).

## Verify

Run the [end-to-end checklist](../../.docs/incident-profile-operations.md#7-end-to-end-verification-checklist). Minimum checks after one analysis:

- The report page shows type, severity, confidence, summary, incident window and evidence, and the video plays from a fresh 1-hour signed R2 URL.
- With person tracking (agent `INCIDENT_TRACKING_ENABLED=1`), a matched person's box follows them over the video in View mode, each tracked card has a presence bar that seeks, and a card click selects its box. The samples come from `GET /api/reports/<videoId>/track?revision=<id>&type=entity&item=<E1>` (`{samples: [{t, box}]}`, 404 without a track); the run's notes keep the agent's `tracking` summary as `response.tracking`.
- `videos.filepath` is a classification-neutral **R2 key** (`uploads/<original name>.mp4` in both modes), **not** a `http://10.131.1.5:...` VST URL. The agent no longer writes `videos` (see [`.docs/decisions.md`](../../.docs/decisions.md) 2026-10-16).
- There is a new `model_runs` row and a new AI revision in History, and the previous runs still appear in the run picker.
- The analysis response was 200; `verifying the persisted report in PostgREST failed` means inference completed but the durable row graph is incomplete and must not be treated as a finished report.

## Known quirks

- Both gateway and agent modes use `incident-contract-v4` (with required `timestamp_seconds` and `label` grounding, plus `first_seen_seconds` and `last_seen_seconds` per entity, the same request as eval) and record every attempt immutably in `model_runs.notes` with its outcome under validation policy `core-scored-v1`. See [`.docs/analysis-schema.md`](../../.docs/analysis-schema.md).
- `anomaly/<category>` is reserved for classified seed/evaluation media. VLM output such as `incident_type: none` does not determine or change the uploaded object's key.
