# Incident Analysis Schema & Output Alignment

This document describes how incident analysis output is defined, validated, stored and shown.

> [!NOTE]
> Since `incident-contract-v3`, **eval, `incident-console-v2` (gateway and agent mode) and `vss-agent` share one contract**: the same prompt, schema and P1 settings in [`../contracts/`](../contracts/README.md). Decisions: [`decisions.md`](decisions.md).

---

## 1. Principle

1. **`contracts/` defines the complete model output.** `incident_report.schema.json` (JSON Schema draft 2020-12), `incident_extraction_prompt.txt` and `VERSION` are the source of truth for what the VLM must return and what counts as a valid response. The schema is strict-mode compatible: every object has `additionalProperties: false` and lists all its properties in `required`, so an optional value is a nullable type. `incident.duration` is not in the schema; every consumer derives it after validation, and a model that sends it fails the schema. In `incident-contract-v3`, entities, instruments, and assets require `timestamp_seconds` (integer >= 0) and `label` (short detector string) for temporal grounding and evidence crop localization. `incident-contract-v4` also requires `first_seen_seconds` and `last_seen_seconds` (integers >= 0) on every entity: the span in which it is seen, with `timestamp_seconds` (its clearest moment) inside it. The schema cannot express `first <= last` or the containment of `timestamp_seconds`, so the prompt asks for them and no cross-field rule enforces them; consumers treat the span as advisory. Reports stored under v2 or v3 lack the span: readers fall back to `timestamp_seconds` for both ends, and the console validates such a report against its own version (`src/modules/contract/domain/validate.ts`). Spatial bounding boxes are detector-produced and optional in search hits, not VLM outputs.
2. **`model_runs.notes` stores the complete validated report and its provenance, immutably.** Nothing ever edits it.
3. **A report is a JSONB snapshot with queryable facets.** `report_revisions.payload` holds the whole contract report and `report_revisions.evidence` the evidence list; only the fields statistics filter on are copied into columns (`incident_type`, `severity_level`, `start_seconds`, `end_seconds`, `revision_no`). `evidence_crops` is the search projection of each video's searchable revision ([`data.md`](data.md#evidence_crops)).
4. **What the report page shows follows reviewer usefulness**, drawing from one revision and its run. The console maps one `report_revisions` row and its `model_runs` row into a `ReportView` (`src/modules/reports/infrastructure/supabase/mappers/report.ts`, `src/modules/reports/application/report-view.ts`); nothing is reassembled from several tables.

## 2. The analysis request (identical to eval P1)

`incident-console-v2/src/modules/analysis/application/build-request.ts` builds exactly the request of `eval/scripts/eval_vlm_client.py:analyze_video_with_p1`:

- **Model:** one of the allowlisted models in [`contracts/inference.json`](../contracts/inference.json): `nvidia/cosmos-3-nano-reasoner`, `nvidia/cosmos-3-super-reasoner`, `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`.
- **`messages`:** one user message:
  1. the signed R2 video URL;
  2. the canonical extraction prompt, verbatim;
  3. only when a reviewer gave one, an **additional instruction** as a separate, fenced, lower-priority text part. It never edits the canonical prompt and cannot change the enforced schema; max 1,000 characters, control characters stripped.
- **Settings:** `temperature 0`, `max_tokens 16384`, `media_io_kwargs.video.num_frames 64` (Cosmos Super ignores `num_frames`).
- **`response_format`:** the contract schema, strict (`$schema`/`$id` removed), equal to `eval/contract.py:response_format()`.

## 3. Validation, ID-only repair and outcomes

`src/modules/contract/domain/validate.ts` is a strict port of `eval/contract.py`:
- `JSON.parse` of the raw content, with no fence stripping, brace extraction, aliasing or defaults;
- Ajv validation against the shared schema file;
- the cross-field rules;
- derived `incident.duration = end_timestamp - start_timestamp`.

Every violation carries a machine-readable code: `EMPTY_CONTENT`, `INVALID_JSON`, `SCHEMA_VIOLATION`, `WINDOW_END_BEFORE_START`, `ID_NOT_SEQUENTIAL_{ENTITY,INSTRUMENT,ASSET}`, `INSTRUMENT_HOLDER_UNKNOWN`, `TIMELINE_{START,END}_OUTSIDE_WINDOW`, `TIMELINE_NOT_CHRONOLOGICAL`, and the contextual `WINDOW_BEYOND_VIDEO` / `TIMELINE_BEYOND_VIDEO`. Shared fixtures in `contracts/fixtures/` (including `fixtures/policy/`) are checked by both the Python and the TypeScript validator.

**Validation policy `core-scored-v1`** (`validateCore`; eval's `evaluate_policy`; the agent's `core_violations`). Its id is recorded beside the contract version; it changed nothing sent to or returned by the model, so it carries no `VERSION` bump:
- **Decides the outcome:** the database-backed (Class A) fields.
- **Scope of each violation:**
  - The `TIMELINE_*` codes are *enrichment*: recorded with `scope: "enrichment"`, never fatal and never repaired.
  - Every other code is *core*.
  - Schema violations stay fatal.
- **Video length:** the analysis reads the video's length from its MP4 header in R2 (`src/lib/video/mp4-duration.ts`, ranged reads) and records it as `request.videoDurationSeconds`.
  - When known, a whole-second window time T must satisfy `0 <= T < length + 1` (`WINDOW_BEYOND_VIDEO`, core).
  - When unknown (`null`), the rule is not applied and `videoBoundsChecked` is `false`.
- **Recorded on each attempt:** `validation: { firstPass (all violations, each with scope), policy, fullContractValid, coreValid, videoBoundsChecked }`.
  - `fullContractValid` is incident-contract validity exactly as before.
  - `coreValid` is the original response's validity under the policy, before any ID repair.

**`id-normalization-v1`** (`src/modules/contract/domain/repair.ts`, mirrored in `vss_agents/incident_contract`) is the only repair the application applies:
- **When it runs:** only when *every* core first-pass violation is `ID_NOT_SEQUENTIAL_*`, or an `INSTRUMENT_HOLDER_UNKNOWN` that renumbering resolves, and the response passed the schema. Enrichment (timeline) violations neither block nor trigger it and are left as they are.
- **What it may change:** identifiers, `instruments[].entity_id` references and list order. Nothing else.
  - Well-formed IDs keep their number, and lists are reordered.
  - Malformed or out-of-range IDs get the free numbers.
- **When it refuses:**
  - a renamed ID, or its new value, appears in any free text (free text is never edited);
  - duplicate IDs;
  - an unresolvable holder.
- **Checks:** an invariance check proves only IDs and order changed. Core validation then runs again.
- **Eval:** eval never uses this and scores the original first response only.

Every attempt that reaches the model has one outcome:

| Outcome | Meaning | Rows written |
|---|---|---|
| `valid_first_pass` | the first response's database-backed fields were valid (enrichment issues, if any, are recorded) | AI revision + notes |
| `valid_after_structural_repair` | valid after `id-normalization-v1`; UI badge "Contract repair applied (IDs only)" | AI revision + notes |
| `contract_failed` | invalid; repair ineligible or re-validation failed | `model_runs` notes only |
| `request_failed` | no usable answer: gateway error or timeout, non-JSON or missing completion, empty content, or `finish_reason` other than `stop` (e.g. `FINISH_LENGTH`) | `model_runs` notes only |

Failed attempts never create a revision or evidence rows, and never appear as reports.

**Timeline removal (in progress):** the report timeline is being removed ([decision](decisions.md#2026-10-28-the-report-timeline-is-being-removed)); until it lands, the contract is unchanged; the console no longer shows the timeline (below).

**Unscored output is not displayed:** the console shows no timeline, no model-reported uncertainties and no list of unscored-field issues (run history and run details). The issues stay recorded in `model_runs.notes` under `validation.firstPass`.

## 4. Storage

### Immutable notes (`model_runs.notes`)

```json
{"incidentConsoleV2": {
  "recordType": "analysis_attempt",
  "status": "valid_first_pass | valid_after_structural_repair | contract_failed | request_failed",
  "stage": null,
  "failure": null,
  "contractVersion": "incident-contract-v4",
  "videoId": "…", "r2Key": "…", "modelRunId": "…", "attemptedAt": "…Z",
  "request": { "model": "…", "inferenceConfig": {}, "additionalInstruction": null, "promptSha256": "…", "schemaSha256": "…", "videoDurationSeconds": 0, "blurFaces": false },
  "response": { "content": "<original raw response, unaltered>", "reasoningContent": "…", "finishReason": "stop", "usage": {}, "httpStatus": 200, "errorBody": null, "tracking": {}, "privacy": {}, "object_tracking": {} },
  "validation": { "firstPass": [ { "code": "…", "path": "…", "message": "…" } ] },
  "repair": null,
  "report": {},
  "reportId": "…", "generatedAt": "…"
}}
```

Field notes:
- `stage`: `null` for successful attempts; otherwise `gateway`, `upstream_response`, `contract_validation` or `structural_repair`.
- `failure`: `null` for successful attempts; otherwise `{ "code": "…", "message": "…" }`.
- `repair`: `null`, or `{ eligible: false, reason, blockingCodes }`, or `{ eligible: true, ruleSet, operations, revalidation }`.
- `report`: the final validated contract report plus derived duration. Valid outcomes only.
- `reportId` and `generatedAt`: valid outcomes only.
- `request.videoDurationSeconds` and `request.blurFaces` and `response.httpStatus`, `response.errorBody` (a failed agent call), `response.tracking`, `response.privacy` and `response.object_tracking` are agent mode only, copied as returned.
- `response`: holds only the fields the gateway actually returned. The caption pass's captions are never stored ([vm-api-contracts.md](vm-api-contracts.md#caption-pass-chunked-captioning-incident_chunking_enabled)); runs recorded before 1 Nov 2026 may still carry `chunkTimeline`, `chunking`, `chunkAlerts` or `alerting`, which nothing reads.

Records written before attempt statuses existed lack `status`; they are shown as `valid_first_pass` (inferred). Pre-contract console records are "earlier analyses", never failures.

### Three classes of report fields

| Class | Fields | Source | Editable |
|---|---|---|---|
| **A: structured** | type, start/end (integer seconds as text), duration (derived), description, severity level, confidence (model-sourced), entities (`E#`, human/animal/unknown), instruments (`I#`, holder, threat 1-5), assets (`A#`), and each item's description, detail and image | `report_revisions.payload` and `.evidence` | Yes, into a new `human` revision only, except duration (recomputed) and confidence |
| **B: model output** | title, severity rationale, location (shown); timeline and uncertainties (stored in the report, not shown by the console since 2026-11-01) | the report in notes | No (read-only) |
| **C: provenance** | model, contract version, run id, instruction, settings, fingerprints, raw response, reasoning, finish reason, usage, repair record | notes + `model_runs` | No |

### Report revisions (AI and human)

A report is a list of immutable revisions in `report_revisions`; the columns, the append-only trigger and the official and searchable revisions are in [`data.md`](data.md#the-report-model). Entities carry `first_seen_seconds` and `last_seen_seconds` (contract v4), which a save keeps; the console's writer is `save_human_revision_with_crops`.

| Event | Revision written |
|---|---|
| Successful analysis | `kind='ai'`, `parent_id` NULL, `model_run_id` set |
| First Save as new version | `kind='human'`, `parent_id` = the revision opened, complete payload and evidence, `created_by` |
| Later saves | another `human` revision on the current one |
| Re-generation | a new `ai` revision with `parent_id` NULL; it copies no earlier edits |
| Mark official | none; `videos.official_revision_id` changes and `evidence_crops` is rebuilt |
| Delete video | cascade removes every revision |

- **The official revision** is the version statistics count.
- **The report page** opens `?revision=<id>`, else the official revision, else the newest one. History lists every revision newest first; opening one loads it into the editor.
- **Edits** are validated against the contract's own sub-schemas and the ID/holder/window rules before the call. Each item keeps the id of the item it came from through the editor's renumbering, so its evidence and image follow it.
- **Evidence images** are never overwritten; a replacement is uploaded on Save to a new key ([R2 layout](data.md#2-cloudflare-r2-storage-architecture)). Each AI item stores its whole video frame (`frame`) and the box (`bounding_box`, `box_seconds`) the console draws on it; the crop (`image`) is only what evidence search embeds. An item the detector could not find has `located: false`, a frame and no box. An item without a frame records an `image_gap` reason.
- **There is no verification status and no draft.** `kind='human'` with `created_by` and `created_at` says a person saved it; unsaved edits live only in the form.

The report page:
- shows "Model severity rationale";
- when a human revision changed the severity, adds "Model assessed N/5; reviewer set M/5";
- marks every other changed structured field ("Reviewer-edited after generation"). Entities, instruments and assets are compared with the stored payload of the AI revision the version derives from (its own for an AI version), not the first pass in the run notes, so the zoomed second look's corrections are never reviewer edits.

## 5. Legacy data

Older rows are shown in contract form for display and querying. The midterm-to-finals copy rewrote the stored `incidents.type`, `entities.type` and entity crop `item_name` values once; query-time folding stays as a guard. Notes and AI-row timestamps are never rewritten:
- `fighting` → `assault`, `animal` → `animal attack`;
- entity `person` → `human`;
- `M:SS` / `H:MM:SS` → seconds.

Their notes are parsed best-effort for the read-only Class B fields. Saving an edit of a legacy run writes a contract-form `human` revision (`e01` → `E1`, `"0:18"` → 18); its `model_runs.notes` are untouched.

## 6. Agent mode

`ANALYSIS_MODE=agent` posts to `vss-agent`'s `/api/v1/incidents/{id}/analyze` with `{ video_url, video_key?, model_run_id?, model?, reviewer_prompt?, skip_rp1?, video_duration_seconds?, blur_faces? }` (`video_key`: evidence crops are stored under `evidence/<video_key>/run-<n>/`; the console always sends it).
- `blur_faces: true` (sent for a video with `videos.blur_faces` on) pixelates people's heads in the stored evidence frames and crops; P1 and the report are unchanged (contract v4), each evidence entry gains `blur_regions`, and the console keeps the agent's `privacy` summary in the run's notes as `response.privacy`, with `request.blurFaces: true` ([data](data.md#privacy-blur)).
- `vss-agent` uses its vendored `incident-contract-v4` (`vss_agents/incident_contract/`) to run P1 (direct VLM call with JSON Schema response format) and RP1 (prose report generation). P1 and RP1 request settings come from the shared `contracts/inference.json` `p1` and `rp1` blocks (the agent reads its vendored copy; eval asserts its constants match). The request's `skip_rp1` flag runs P1 only; the console always sets it, because gateway mode runs P1 only too.
- The agent validates P1 with strict Pydantic models under the same `core-scored-v1` policy and `id-normalization-v1` repair as the console (timeline-only violations are not fatal), derives `incident.duration` and, unless `skip_rp1`, runs RP1 on the result. Only a schema or unrepairable core violation is a 422, whose `detail` carries `raw_output`, `finish_reason`, `model` and `contract_version`.
- With `INCIDENT_SOM_ENABLED` and `INCIDENT_TRACKING_ENABLED` on the agent, P1's `video_url` is a scratch copy of the clip with numbered person boxes drawn on it, and one more user message asks for the box number in brackets at the end of each human entity's `label`; the agent strips the number before the report is stored, so the prompt text, `inference.json` settings, schema and contract version are unchanged ([vm-api-contracts.md](vm-api-contracts.md#post-apiv1incidentsincident_idanalyze)).
- With `INCIDENT_CHUNKING_ENABLED=1` on the agent, a production request runs a caption pass before P1 (20 s windows at 2 s overlap, captioned by the same model, sent to P1 as one more user message that tells the model to trust the video); the P1 prompt text, `inference.json` settings and schema are unchanged, and experimental requests never run it ([vm-api-contracts.md](vm-api-contracts.md#caption-pass-chunked-captioning-incident_chunking_enabled)).
- The agent applies the video-bounds rules itself. The length is the request's `video_duration_seconds` (the console sends the length it read from the MP4 header) or, without one, the length the agent reads from the MP4 header through the signed `video_url` (ranged GETs, `vss_agents/utils/video_duration.py`, with no model key on those requests). A `video_duration_seconds` that is not a finite number above 0 is a 422. When neither gives a length the rules cannot apply, as in the console and eval; the 200 response says so (`video_bounds_checked: false`, `video_bounds_unchecked_reason`) and the agent logs a warning. A 200 also carries `video_duration_seconds` and `video_duration_source` (`request` or `mp4_header`).
- After a valid report the agent (given `video_key`, the video's R2 key, in the request) appends a `report_revisions` row (`kind='ai'`, `payload` = the report, `evidence`, facet columns, `parent_id` NULL) best-effort, for both agent variants (P1 only and P1+RP1). It locates each evidence item (retrying at +1, -1, +2 and -2 s when the detector finds nothing at the item's second; each second tried is one call against `max_crop_calls`), uploads the whole frame and the crop to R2 ([key layout](data.md#2-cloudflare-r2-storage-architecture)), asks the same VLM to name the located item again from a native-resolution close-up and applies a confident correction (at least 0.7) to the evidence entry and the payload item ([zoomed second look](architecture.md#report-versions-view-edit-and-history); never the id, label, timestamps or an entity's type) and records a plain `image_gap` reason in the evidence JSONB when it cannot (no detector, no R2 store, object not located at any second tried, upload failed). With a caller `model_run_id` it only inserts a placeholder `model_runs` row if absent (the console writes its `notes` record afterwards). Without one it creates its own run. A `reviewer_prompt` reaches the model run only; re-generation copies no earlier edits. It never writes `videos`.
- In `incident-console-v2`, `runContractAnalysis` routes the agent's raw P1 output (from a 200 or a 422) through the identical validation policy (`core-scored-v1`) and ID repair (`id-normalization-v1`) as gateway mode, records the attempt with the agent's model and finish reason (its own `videoBoundsChecked` included: the console still enforces the bounds on the agent's raw output, so an older agent or one that could not read the header is covered), and writes the `model_runs` notes itself (through the `AnalysisRunRepository` port); it inserts the AI revision too unless the agent's response carries `revision_id`, in which case it only verifies it. Its timeout is the model's, as in gateway mode, since only P1 runs. The chosen model is sent as `model` and a non-empty `additionalInstruction` as `reviewer_prompt`, so both work in both modes; the run records the model the agent reports. If the agent's `contract_version` (from a 200 or a 422) is missing or differs from the console's, the attempt is a `request_failed` with code `CONTRACT_VERSION_MISMATCH` (HTTP 502, agent output kept in the notes, no report rows).

## 7. Model inference findings

Measured when the contract was introduced; they explain the fixed P1 settings in `contracts/inference.json`.

- **Schema enforcement works** (`response_format: json_schema`) on `cosmos-3-nano-reasoner`, `cosmos-3-super-reasoner` and `nemotron-3-nano-omni-30b-a3b-reasoning`. Signed R2 URLs are fetched by the inference server; URL and base64 input gave identical results.
- **Frame sampling:** the gateway forwards `media_io_kwargs` (`{"video": {"num_frames": N}}`), but the default is about 24 frames per video whatever its length, so long clips are sampled every 5-10 s. `num_frames` takes effect on Nano and Nemotron; **Super ignores it** (flat ~1080 prompt tokens at 8 and 64 frames). Nano sees frames at thumbnail resolution (~44 tokens each).
- **Token budget:** Nemotron needs about 16k tokens to reason and then emit JSON (4096 ended with `finish_reason=length`), hence `max_tokens 16384` and `num_frames 64` for every model.
- **Timeline rule:** Cosmos models often include lead-up or aftermath events outside their own incident window (Super on Animal003, Assault018 and Burglary005; Nano on Animal003). Clarified prompt wording did not change this in a 12-call pilot, which is why `core-scored-v1` records `TIMELINE_*` failures without rejecting the report.
- **Correctness is separate from validity:** a strict pass says nothing about accuracy, and one Cosmos misclassification of a long explosion clip was most likely sparse sampling. Eval is the measure of each model.
