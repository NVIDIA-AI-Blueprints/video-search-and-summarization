# Incident Search & Reporting Capstone — Data Layer & Schema

This document defines the relational database schema (Supabase PostgreSQL), the object storage layout
(Cloudflare R2), and documented data layer edge cases in the `dev-profile-incident` architecture.

---

## 1. Supabase PostgreSQL Schema

There is one Supabase project per project phase ([decision](decisions.md)): **finals** is the live database and **midterm** (`rfevukimyftzbxlehwel`) is a frozen, read-only record. The finals schema is defined authoritatively by the baseline
[`supabase/migrations/20261020000000_finals_baseline.sql`](../supabase/migrations/20261020000000_finals_baseline.sql) and the later finals migrations in the same folder (see [`../supabase/README.md`](../supabase/README.md) and the `incident-manage-database` skill for how they are applied to finals):

- `20261023000000_report_revisions_schema.sql` — drops the relational report tables and retires `videos.selected_*`; creates `report_revisions`, the revision-keyed `evidence_crops` and `query_results`, and `videos.official_revision_id`.
- `20261023000100_report_revisions_functions.sql` — `save_human_revision`, `set_official_revision`, and the rewritten `search_evidence_items`, `list_incident_report_summaries` and `get_incident_dashboard`.
- `20261023000200_searchable_latest_revision.sql` — adds `videos.official_since`; makes a video with no official revision searchable through its newest revision (`set_revision_crops`, `searchable_revision_id`, `replace_video_crops`); and adds `officialSince`, `versionCount`, `evidenceCount` and `thumbnail` to the library cards.
- `20261023120000_report_revision_embeddings.sql` — adds `report_revisions.embedding` with its HNSW index, and `search_report_revisions`.
- `20261024090000_save_human_revision_with_crops.sql` — adds `save_human_revision_with_crops`.
- `20261028090000_r2_rename.sql` — adds `apply_r2_rename` (used once by [`supabase/r2/migrate_r2_names.py`](../supabase/r2/migrate_r2_names.py)) and lets it, and nothing else, change a revision's `evidence` column.
- `20261031090000_dashboard_official_else_newest.sql` — rewrites `get_incident_dashboard` to count official, else newest, with the official split and `p_scope`, and adds `not_official` to `list_incident_report_summaries`.
- `20261031120000_analysis_jobs.sql` — adds the background analysis queue: the `analysis_jobs` table and `claim_analysis_job`, `touch_analysis_job`, `finish_analysis_job`, `retry_analysis_job` and `cancel_analysis_job`.
- `20261101090000_video_blur_faces.sql` — the privacy blur: added `videos.blur_faces`, `analysis_jobs.reanalyse_video_id` and `analysis_jobs.blur_faces`, and `claim_queued_analysis_job` (removed by the next file).
- `20261102090000_drop_blur_faces.sql` — removes the privacy blur ([decision](decisions.md#2026-11-02-the-privacy-blur-is-removed-entirely)): cancels unfinished re-analysis jobs, restores the standalone `claim_analysis_job`, and drops the three blur columns and `claim_queued_analysis_job`. **Pending apply on finals** (see [status](status.md#3-database-status)); until then those columns still exist there and nothing reads them. Evidence entries and run notes written earlier may still carry `blur_regions`, `heads`, `privacy` or `blurFaces`; nothing reads them.
- `20261101120000_dashboard_byte_order_ties.sql` — the same `get_incident_dashboard`, with every tie between equal counts (types, entity, instrument and asset labels, models) broken in byte order (`COLLATE "C"`), whatever the database's collation; it also decides which tied labels make a top-3 or top-8 cut.

Which of these are applied to finals is in [`status.md`](status.md#3-database-status). The midterm history is archived, never to be pushed, in [`supabase/midterm/`](../supabase/midterm/README.md). The SQL harness in `supabase/tests/` builds the schema on a throwaway local Postgres.

Finals holds seven tables: `videos`, `queries`, `model_runs`, `report_revisions`, `evidence_crops`,
`query_results` and `analysis_jobs` (the console's analysis queue, not report data). Every report is a `report_revisions` snapshot; there are no relational report
tables (`incidents`, `reports`, `entities`, `instruments`, `assets`, `human_reports*`,
`review_status`, `gt_*`, `severity_eval_log` are gone).

### The report model

- **An upload analysis appends one AI revision.** `kind='ai'`, a new `model_run_id`, the full
  contract report as `payload` JSONB, the evidence list as `evidence` JSONB (one entry per item: `item_type`, `item_id`, `timestamp_seconds`, `frame` (R2 key of the whole video frame), `image` (R2 key of the crop, what evidence search embeds), `bounding_box` (the crop box in that frame), `detected_box` (the detector's own box before the 5% crop padding, for items located from 1 Nov 2026 on), `box_seconds` (the second the frame was read at), `located` (false when the detector found nothing: a frame, no crop, no box), `image_gap`; for a person matched to a track, with `INCIDENT_TRACKING_ENABLED` on, also `track` (R2 key of the track JSON), `track_first_seen_seconds` and `track_last_seen_seconds`, and `frame`, `image`, `bounding_box` and `box_seconds` are those of the track's first sample (the second the item is first seen) (an instrument or asset followed by the object tracker, with `INCIDENT_OBJECT_TRACKING_ENABLED` on, gets the same fields plus `track_source: "object-tracker"`; a person's entry has no `track_source`), and the zoomed second look's `zoom_checked` (true when the zoomed second look named the item from its close-up), `zoom_confidence` (the second look's 0-1 confidence, the lowest of its calls, null when unchecked), `zoom_seen` (what the two blind looks found: `{frame, close_up}`, names for objects and descriptions for people), `name_before` (the whole-clip name when the second look corrected it, else null; `description_before` beside it), `zoom_gap` (why the item was not checked or a correction was ignored: flag off, not located, below the 0.7 threshold, call failed); a correction also rewrites the item's name and description in `payload`, see [architecture.md](architecture.md#report-versions-view-edit-and-history)), and `parent_id` NULL.
  The agent writes it (`IncidentDB.insert_ai_revision`); the console never edits it.
- **A human edit appends a human revision.** `save_human_revision` writes `kind='human'`,
  `parent_id` = the revision the reviewer opened, the full edited report as `payload`, and the
  evidence list that revision uses. It inherits the parent's `model_run_id`. Earlier revisions are
  never changed.
- **Re-generation is a new AI branch.** A new AI revision has `parent_id` NULL and copies no earlier
  edits.
- **One revision per video is official.** `videos.official_revision_id` (composite FK to
  `report_revisions(video_id, id)`) names it; NULL means none. Selection never rewrites a revision.
  `evidence_crops` rows belong to the video's **searchable revision**: the official revision when
  there is one, otherwise the newest (`searchable_revision_id(video_id)`). See `evidence_crops`.
- **Revisions are append-only.** A trigger rejects `UPDATE` and `DELETE` (the exceptions are the
  `ON DELETE CASCADE` of the video, see below, and `apply_r2_rename`, which may change only `evidence`).

---

### Detailed Table Specifications

#### `videos`

Primary registry of ingested video assets.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `VARCHAR(20)` | PRIMARY KEY | Video identifier: `'v' + first 19 hex of sha256(random UUID)` for uploads (20 chars; rows uploaded before 2026-10-31 hashed the VST sensor id instead); clip-name ids (`Burglary012`, `RoadAccidents...`, `SYN-...`, `MOCK-...`) for fixture/eval rows. Raw sensor ids are never used directly as `id`. |
| `filepath` | `VARCHAR(1024)` | NULLABLE | R2 object key (`uploads/<original name>.mp4` for uploads since 2026-10-31, `uploads/<original file name>` before, `anomaly/<category>/<clip>.mp4`, or `normal_videos/<clip>.mp4`). Older rows may hold an absolute VST path — validate with `isValidR2Key` before presigning. The console owns this column; the agent does not write `videos`. |
| `uploaded_datetime` | `TIMESTAMP` | DEFAULT (now() AT TIME ZONE 'utc') | Naive UTC timestamp (`timestamp without time zone`) when uploaded. Some historical rows are NULL. |
| `duration` | `INTEGER` | NULLABLE | Video duration in integer seconds |
| `source` | `VARCHAR(512)` | NULLABLE | Ingestion source or camera stream name. NULL for console uploads since 2026-10-31 (uploads no longer go through VST); older upload rows keep the VST sensor id they were given. |
| `official_revision_id` | `BIGINT` | NULLABLE; composite FK `(id, official_revision_id)` → `report_revisions(video_id, id)` | The video's official report revision. NULL = awaiting selection. Set/cleared only by `set_official_revision`. The old `selected_model_run_id`, `selected_by` and `selected_at` columns are gone. |
| `official_since` | `TIMESTAMP` | NULLABLE | Naive UTC time the current official revision was marked; NULL when there is none. Set when the pointer moves to a different revision, kept when the same one is marked again, cleared with the pointer. Rows marked before the column existed were backfilled with the official revision's `created_at` (the real time was never recorded). |

#### `queries`

Tracks natural language queries submitted by users (search).

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `VARCHAR(20)` | PRIMARY KEY | Unique query identifier |
| `query_text` | `TEXT` | NOT NULL | Natural language query string |
| `submitted_datetime` | `TIMESTAMP` | DEFAULT (now() AT TIME ZONE 'utc') | Naive UTC timestamp when submitted |

#### `model_runs`

Tracks each execution of an AI pipeline run over an incident video.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `VARCHAR(20)` | PRIMARY KEY | Unique model execution identifier |
| `model_name` | `VARCHAR(128)` | NOT NULL | Model identifier (e.g. `nvidia/cosmos-3-nano-reasoner`, `incident_report_gen`, `vss-agent`) |
| `model_version` | `VARCHAR(128)` | NULLABLE | Optional version tag of the model |
| `prompt_version` | `VARCHAR(64)` | NULLABLE | Prompt template version tag |
| `run_datetime` | `TIMESTAMP` | DEFAULT (now() AT TIME ZONE 'utc') | Naive UTC execution timestamp |
| `notes` | `TEXT` | NULLABLE | Free text. The console writes the **immutable analysis-attempt record** `{"incidentConsoleV2":{recordType:"analysis_attempt", status, stage, failure, contractVersion, videoId, r2Key, attemptedAt, request, response, validation, repair, report?}}` for every attempt (see [`analysis-schema.md` §4](analysis-schema.md#4-storage)); caption-pass captions are never stored (older records may still hold `chunkTimeline`, `chunking`, `chunkAlerts` or `alerting`, unread); it is never modified afterwards. Failed attempts (`contract_failed` / `request_failed`) are `model_runs` rows with no revision. Older console rows hold `{"incidentConsoleV2":{report,editedReport?,rawModelOutput,normalizedModelOutput}}`; `mock-backend` stores a plain-text note; agent/seed runs leave it NULL. Count runs by outcome (`run_outcome(notes)`) or via AI revisions. |

#### `report_revisions`

Append-only report snapshots. This is the source of truth for every AI and human report.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `BIGINT` | GENERATED ALWAYS AS IDENTITY; PRIMARY KEY | Revision identifier. Search results and `query_results` store this. |
| `video_id` | `VARCHAR(20)` | NOT NULL; FK → `videos.id` ON DELETE CASCADE | The incident/video the report belongs to |
| `revision_no` | `INTEGER` | NOT NULL; UNIQUE `(video_id, revision_no)` | 1-based sequence per video. The agent uses `max + 1` and retries on a unique violation; `save_human_revision` locks the video row and assigns `max + 1`. |
| `kind` | `VARCHAR(8)` | NOT NULL; CHECK `IN ('ai','human')` | `ai` for a model output, `human` for a reviewer snapshot |
| `model_run_id` | `VARCHAR(20)` | NULLABLE; FK → `model_runs.id`; CHECK required when `kind='ai'` | The run whose output the revision derives from. A human revision inherits its parent's run. |
| `parent_id` | `BIGINT` | NULLABLE; composite FK `(video_id, parent_id)` → `report_revisions(video_id, id)` | The revision this one was edited from. NULL for an AI branch root. |
| `payload` | `JSONB` | NOT NULL | The full contract report (`incident`, `entities`, `instruments`, `assets`, `timeline`, `uncertainties`) |
| `evidence` | `JSONB` | NOT NULL, DEFAULT `'[]'` | The evidence items and R2 image keys this revision uses |
| `incident_type` | `VARCHAR(64)` | NULLABLE | Plain facet written by the caller, from `payload.incident.type` |
| `severity_level` | `INTEGER` | NULLABLE | Plain facet written by the caller, from `payload.incident.severity_level` |
| `start_seconds` | `INTEGER` | NULLABLE | Plain facet written by the caller, from `payload.incident.start_timestamp` |
| `end_seconds` | `INTEGER` | NULLABLE | Plain facet written by the caller, from `payload.incident.end_timestamp` |
| `created_by` | `VARCHAR(256)` | NULLABLE; CHECK required when `kind='human'` | Reviewer name for a human revision; NULL for AI |
| `created_at` | `TIMESTAMP` | NOT NULL, DEFAULT (now() AT TIME ZONE 'utc') | Naive UTC creation time |
| `note` | `TEXT` | NULLABLE | Free-text note attached to a human revision (empty/blank stored as NULL) |
| `embedding` | `extensions.vector(1024)` | NULLABLE | Summary vector of `incident.title` and `incident.description`, written by the agent in the same insert as an AI revision. NULL for human revisions (`save_human_revision` computes none) and when the embedding endpoint was unavailable. Never updated. |

Constraints and indexes:

- `report_revisions_pkey` on `id`; `report_revisions_video_rev_key` unique on `(video_id, revision_no)`; `report_revisions_video_id_id_key` unique on `(video_id, id)` (target of the official pointer and the parent link).
- `report_revisions_video_id_fkey` (CASCADE), `report_revisions_model_run_id_fkey` (no cascade: a revision needs its run row first), `report_revisions_parent_fkey`.
- `report_revisions_kind_check`, `report_revisions_ai_run_check`, `report_revisions_human_by_check`.
- Indexes: `(video_id, revision_no DESC)`, `incident_type`, `severity_level`, and `report_revisions_embedding_hnsw_idx` (HNSW cosine on `embedding`, `m = 16, ef_construction = 64`).
- Trigger `report_revisions_no_update_delete` (BEFORE UPDATE OR DELETE, `report_revisions_append_only()`): raises `report_revisions is append-only`. It permits a delete made by the video's `ON DELETE CASCADE` (trigger depth > 1), so deleting a video still removes its revisions.

#### `evidence_crops`

Searchable evidence of each video's **searchable revision**: its official revision, or, while the
video has none, its newest revision. A video has rows for at most that one revision. The primary
key and FK are revision-scoped; there is no `incident_id`/`model_run_id` here — both come through
`report_revisions`.

What moves the rows (the database cannot embed, so a writer always supplies them):

| Event | Searchable revision | Rows |
|---|---|---|
| `set_revision_crops(video, newest, crops)` on a video with no official revision | newest | stored for the newest revision, every other row of the video deleted |
| A human revision is saved with `save_human_revision_with_crops` and the video has **no** official revision | the new revision | replaced with the call's crop list in the same transaction |
| A newer revision is saved another way (`save_human_revision` or the agent's insert) and the video has **no** official revision | the new revision | the old rows stay but are never returned by search; the video is not searchable until the writer calls `set_revision_crops` for the new revision, which deletes the old rows |
| A newer revision is saved and the video **has** an official revision | unchanged | unchanged |
| `set_official_revision(video, rev, crops)` with a crop list | `rev` | replaced with `crops` |
| `set_official_revision(video, rev)` with no crop list | `rev` | `rev` keeps the rows it has; the video's other rows are deleted |
| `set_official_revision(video, NULL, ...)` (clear) | the newest revision | the newest revision keeps its rows (or takes the crop list); other rows deleted |

| Column | Type | Constraints | Description |
|---|---|---|---|
| `revision_id` | `BIGINT` | PRIMARY KEY (part); FK → `report_revisions.id` ON DELETE CASCADE | The searchable revision (official, else newest) these crops belong to |
| `item_type` | `VARCHAR(16)` | PRIMARY KEY (part); CHECK `IN ('entity','instrument','asset')` | Evidence category |
| `item_id` | `VARCHAR(20)` | PRIMARY KEY (part) | Item identifier in the revision (`E1..`, `I1..`, `A1..`) |
| `item_name` | `VARCHAR(256)` | NOT NULL | Item name or detector label |
| `description` | `TEXT` | NOT NULL | Item visual description |
| `threat_level` | `INTEGER` | NULLABLE | Threat rating (1-5; instruments only) |
| `holder_entity_id` | `VARCHAR(20)` | NULLABLE | Identifier of the holding entity (instruments only) |
| `timestamp_seconds` | `INTEGER` | NOT NULL | Whole-second offset from video start: the second the box was found at (the evidence entry's `box_seconds`, else its `timestamp_seconds`), so a search hit cuts the frame its box belongs to. An entity's first/last seen span (`incident-contract-v4`) is stored only in the revision payload and evidence, not here |
| `bounding_box` | `INTEGER[4]` | NOT NULL | Normalised `[ymin, xmin, ymax, xmax]` (0-1000) |
| `image` | `VARCHAR(1024)` | NOT NULL | R2 object key (`evidence/<video key>/run-<N>/<itemId>.jpg` or `.../edit-<N>/<itemId>.jpg`, where `<video key>` is `videos.filepath`) |
| `embedding` | `extensions.vector(1024)` | NULLABLE | 1024-dimensional embedding for cosine retrieval |
| `created_at` | `TIMESTAMP` | NOT NULL, DEFAULT (now() AT TIME ZONE 'utc') | Naive UTC insertion time |

Index `evidence_crops_embedding_hnsw_idx` is an HNSW cosine index on `embedding` (`m = 16,
ef_construction = 64`). RLS is on; only `service_role` can read or write.

#### `query_results`

Audit log and hit persistence for natural-language search and critic evaluation.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `VARCHAR(36)` | PRIMARY KEY | Unique result identifier (UUID or hex) |
| `query_id` | `VARCHAR(20)` | NOT NULL; FK → `queries.id` ON DELETE CASCADE | Associated search query |
| `revision_id` | `BIGINT` | NOT NULL; FK → `report_revisions.id` ON DELETE CASCADE | The searchable revision the hit came from |
| `item_type` | `VARCHAR(16)` | NOT NULL; CHECK `IN ('entity','instrument','asset')` | Evidence item type |
| `item_id` | `VARCHAR(20)` | NOT NULL | Evidence item identifier |
| `rank` | `INTEGER` | NOT NULL | Retrieval rank position (1-based) |
| `similarity_score` | `DOUBLE PRECISION` | NOT NULL | Cosine similarity (`1.0 - distance`) |
| `critic_verdict` | `VARCHAR(16)` | NOT NULL; CHECK `IN ('confirmed','rejected','unverified')` | VLM critic verdict |
| `critic_rationale` | `TEXT` | NULLABLE | Critic rationale |
| `retrieved_at` | `TIMESTAMP` | NOT NULL, DEFAULT (now() AT TIME ZONE 'utc') | Retrieval time (naive UTC) |

There is **no foreign key to `evidence_crops`**: changing the official revision keeps the search
audit trail. Indexes: `(revision_id, item_type, item_id)` and `(query_id, rank)`. RLS is on; only
`service_role` can read or write.

#### `analysis_jobs`

The console's background analysis queue: one row per uploaded video waiting for, running or finished with its analysis ([architecture](architecture.md#background-analysis-jobs)). Written only by the console, and only through the functions below apart from the insert.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `BIGINT` | GENERATED ALWAYS AS IDENTITY; PRIMARY KEY | Job identifier |
| `file_path` | `TEXT` | NOT NULL | The uploaded video's R2 key (`uploads/<name>.mp4`) |
| `filename` | `TEXT` | NOT NULL | The stored file name, shown in the jobs list |
| `model` | `VARCHAR(128)` | NULLABLE | The allowlisted model chosen at upload (the console stores the default when none was chosen); NULL runs the console's default |
| `status` | `VARCHAR(16)` | NOT NULL, DEFAULT `'queued'`; CHECK `IN ('queued','running','done','failed','cancelled')` | Where the job is |
| `attempts` | `INTEGER` | NOT NULL, DEFAULT 0; CHECK `>= 0` | Claims since the job was queued or last retried; a stale job with 2 fails instead of queueing again |
| `error` | `TEXT` | NULLABLE | The plain reason a job failed |
| `created_at` | `TIMESTAMP` | NOT NULL, DEFAULT (now() AT TIME ZONE 'utc') | Naive UTC enqueue time; claims take the oldest queued job first |
| `started_at` | `TIMESTAMP` | NULLABLE | Naive UTC start of the latest claim |
| `finished_at` | `TIMESTAMP` | NULLABLE | Naive UTC time it was done, failed or cancelled |
| `heartbeat_at` | `TIMESTAMP` | NULLABLE | Naive UTC time of the running claim's latest heartbeat (every 15 s); NULL when not running |
| `video_id` | `VARCHAR(20)` | NULLABLE; FK → `videos.id` ON DELETE SET NULL | The analysed video, once done |
| `revision_id` | `BIGINT` | NULLABLE; FK → `report_revisions.id` ON DELETE SET NULL | The new AI revision, once done |

Deleting a video keeps its upload job rows as history with `video_id` and `revision_id` set to NULL. Indexes: `(status, created_at, id)` for the claim, `(created_at DESC)` for the list. RLS is on; only `service_role` can read or write.

### Relationships

- `report_revisions.video_id` → `videos.id` (CASCADE). `videos.official_revision_id` is a composite
  FK `(id, official_revision_id)` → `report_revisions(video_id, id)`, so a video can only point at
  one of its own revisions.
- `report_revisions.model_run_id` → `model_runs.id` (no cascade). A revision needs its run row
  first; an AI revision requires it, a human revision inherits it from its parent.
- `report_revisions.parent_id` → `report_revisions(video_id, id)` (composite, no cascade), linking
  an edit to the revision it was made from.
- `evidence_crops.revision_id` → `report_revisions.id` (CASCADE): deleting a video cascades through
  its revisions to their crops.
- `query_results.query_id` → `queries.id` and `query_results.revision_id` →
  `report_revisions.id` (both CASCADE).
- `analysis_jobs.video_id` → `videos.id` and `analysis_jobs.revision_id` → `report_revisions.id`
  (both SET NULL); `analysis_jobs.reanalyse_video_id` → `videos.id` (CASCADE).

Deleting a `videos` row removes its `report_revisions`, their `evidence_crops` and any
`query_results` that reference those revisions. `model_runs` and `queries` rows are not deleted by
that cascade; upload `analysis_jobs` rows stay, with their links cleared.

---

### Functions

All functions are `SECURITY INVOKER`, `STABLE`/`VOLATILE` as noted, pin `SET search_path = ''`, and
are executable by `service_role` only. Helpers still available: `canonical_incident_type`,
`canonical_entity_type` (`person` → `human`), `normalized_name`, `run_outcome(notes)`,
`timestamp_seconds(text)` and `try_parse_jsonb(text)`.

#### `save_human_revision`

```
save_human_revision(
  p_video_id VARCHAR, p_parent_id BIGINT, p_created_by TEXT, p_note TEXT,
  p_payload JSONB, p_evidence JSONB,
  p_incident_type TEXT, p_severity_level INTEGER, p_start_seconds INTEGER, p_end_seconds INTEGER
) RETURNS JSONB
```

Appends a `kind='human'` revision with `revision_no = max + 1` for the video. Locks the `videos`
row so concurrent saves cannot take the same number. `p_parent_id` must be a revision of the same
video; the new revision inherits its `model_run_id`. The four facet arguments are written as given
(the caller derives them from the payload). `p_payload` must be an object with an `incident`; `p_evidence`
must be an array; `p_created_by` must be 1-256 characters after trimming. Returns
`{revisionId, revisionNo, videoId, kind, parentId, modelRunId, createdBy, createdAt, note}`.

#### `save_human_revision_with_crops`

```
save_human_revision_with_crops(<the ten save_human_revision arguments>, p_crops JSONB DEFAULT '[]') RETURNS JSONB
```

The console's Save. In one transaction it calls `save_human_revision`, then, when the video has no official revision, replaces the video's `evidence_crops` with `p_crops` for the new revision (same row format as `set_official_revision`). With an official revision `p_crops` is ignored and the official rows stay. `p_crops` must be a list. Returns the `save_human_revision` result plus `crops`, the rows now stored (0 when the video has an official revision). The console embeds the crops before the call, so a failed embedding writes nothing.

#### `set_official_revision`

```
set_official_revision(p_video_id VARCHAR, p_revision_id BIGINT, p_crops JSONB DEFAULT '[]') RETURNS JSONB
```

In one transaction: sets `videos.official_revision_id` and `official_since`, then makes the new
official revision (or, for NULL, the video's newest revision) the searchable one. A non-empty
`p_crops` replaces all the video's `evidence_crops` rows with rows for that revision. An empty
`p_crops` keeps the rows the revision already has and deletes the video's other rows, so marking
the newest revision official needs no new embeddings. There is no audit trail of previous
selections.

`p_crops` is the exact row list to store, one object per crop:

```
{item_type, item_id, image, bounding_box, timestamp_seconds,
 item_name, description, threat_level, holder_entity_id, embedding}
```

Entries without an `item_type` of `entity`/`instrument`/`asset`, without an `item_id` or without an
`image` are skipped. A missing/`null` `bounding_box` becomes `[0,0,1000,1000]`; a missing
`timestamp_seconds` becomes 0; `embedding` is a 1024-number array or null. Returns
`{videoId, officialRevisionId, crops}` where `crops` is the number of rows now stored.

The console obtains the embeddings (the agent's `POST /api/v1/incidents/evidence-embeddings`) and
passes them in `p_crops`; it writes no table directly.

#### `set_revision_crops`

```
set_revision_crops(p_video_id VARCHAR, p_revision_id BIGINT, p_crops JSONB DEFAULT '[]') RETURNS JSONB
```

Stores `p_crops` (same format as `set_official_revision`) for the video's searchable revision
without touching the official pointer: how a video with no official revision gets searchable
evidence, and how a writer refreshes the rows after a newer revision is saved. `p_revision_id`
must equal `searchable_revision_id(p_video_id)`, otherwise the call is refused, so a writer
holding a stale revision id cannot overwrite the rows of a newer one. The video's other rows are
deleted; an empty list clears them. Returns `{videoId, revisionId, crops}`.

`searchable_revision_id(video_id)`, `evidence_image(item)` and `replace_video_crops(...)` are
helpers the RPCs use; the console and agent have no reason to call them.

#### `search_evidence_items`

```
search_evidence_items(
  p_query_embedding extensions.vector(1024), p_top_k INTEGER DEFAULT 10,
  p_min_similarity DOUBLE PRECISION DEFAULT 0.2,
  p_item_types TEXT[] DEFAULT ARRAY['entity','instrument','asset'],
  p_incident_types TEXT[] DEFAULT NULL, p_min_severity INTEGER DEFAULT NULL,
  p_max_severity INTEGER DEFAULT NULL, p_video_id VARCHAR DEFAULT NULL
)
```

pgvector HNSW cosine search over `evidence_crops.embedding`, scoped to each video's searchable
revision (`searchable_revision_id(video_id) = evidence_crops.revision_id`: official, else newest).
`p_video_id` (migration `20261028090000_search_evidence_items_video_id.sql`) limits the scan to one
video before ranking, so the top `p_top_k` all belong to it. `p_query_embedding` may be NULL only
with `p_video_id`: the video's crops are then listed in video time order with `similarity_score` 0.
The migration drops the old 7-argument overload first, because two overloads would make PostgREST
calls ambiguous. Returns the incident (video) id, the
revision id, the revision's model run and facets, the crop, and `similarity_score`
(`1.0 - distance`). `incident_start_timestamp`/`incident_end_timestamp` are the revision's seconds
as text. Rows whose embedding is NULL are skipped.

#### `list_incident_report_summaries`

Paginated report library, one card per video: the official revision if there is one, otherwise the
newest revision (so reports awaiting selection still appear). Facets come from the revision columns,
description/title/confidence from `payload.incident`. Filters: `p_search`, `p_type`, `p_severity`
(`low`/`medium`/`high` or a number), `p_kind` (`ai`/`human`), date range, local time-of-day and
weekday (`p_timezone`, default UTC), entity/instrument/asset text or exact-name match,
`p_entity_types`, `p_sort`, and `p_scope` (`all`, `official` or `not_official`). `p_type`
`Unclassified` selects a NULL `incident_type`. Returns
`{reports: [...], totalItems, incidentTypes}`. Each card carries `revisionId`, `revisionNo`,
`videoId`, `modelRunId`, `kind`, `createdBy`, `note`, `isOfficial`, `outcome`, and the display
fields, plus `officialSince` (when the official revision was marked; null if none), `versionCount`
(revisions of the video), `evidenceCount` (items of the card revision's `evidence` list that have an
image) and `thumbnail` (the R2 key of the first such item, or null). The evidence figures come from
the revision's `evidence` list, not `evidence_crops`, so they do not depend on embeddings being
stored. It never returns `payload`, notes, signed URLs or video content.

#### `get_incident_dashboard`

Counts each video once through `searchable_revision_id`: its **official revision, else its newest**,
the same rule as the library and search (migration `20261031090000_dashboard_official_else_newest.sql`).
Ranked lists and types sort by count, then by label, type or model name in byte order (`COLLATE "C"`,
migration `20261101120000`), so ties rank the same on every database.
It only reads `videos.official_revision_id`, to report the split, and never sets it. Filtered by the
revision's `created_at` plus optional type, severity band, weekday and hour (in `p_timezone`, default
UTC) and `p_scope` (`all`, `official` or `not_official`; a caller that omits it gets `official`,
because a console from before this change labels its numbers official; the console always sends
it). Returns `total` with
`official` and `notOfficial`, all-time `videos` counts (`uploaded`, `withOfficial`,
`awaitingSelection`, `withoutReport`), `previous`, `severity` and `severityByScope`, `revisions`
(`ai`/`human`), types with per-type evidence, daily `trend` (days in `p_timezone`), `heatmap`,
`entityTypes`, `topInstruments`, `topAssets`, `threatLevels`, a confidence histogram with
`confidenceNotProvided`, and `modelCoverage` (attempts by outcome: failed attempts from `model_runs`,
successful ones from AI revisions). Each `types`, `trend`, `heatmap` and `topAssets` entry also
carries `official` and `notOfficial`. `videos` and `modelCoverage` ignore every filter except that
`modelCoverage` follows the period. A `type` of `Unclassified` selects a NULL `incident_type`.

---

### Analysis job functions

All `VOLATILE`, `SECURITY INVOKER`, `search_path = ''`, `service_role` only, and all on the database clock, so the stale rule does not depend on the console machine's clock. `p_attempt` is the `attempts` value a claim returned: it names one claim, so a runner whose job was recovered and claimed again cannot overwrite the newer claim.

| Function | Does | Returns |
|---|---|---|
| `claim_analysis_job(p_stale_seconds DEFAULT 180)` | First recovers stale running jobs (no heartbeat, else start, for `p_stale_seconds`): back to `queued` when `attempts < 2`, else `failed` with "The console stopped twice while analysing this video". Then claims the oldest queued job (`created_at`, `id`; `FOR UPDATE SKIP LOCKED`): `running`, `attempts + 1`, `started_at` and `heartbeat_at` now | The claimed row, or no rows |
| `touch_analysis_job(p_id, p_attempt)` | Heartbeat for a running claim | `true`, or `false` when the claim is no longer running |
| `finish_analysis_job(p_id, p_attempt, p_status, p_error, p_video_id, p_revision_id)` | `done` (needs the video and revision) or `failed` (blank reason becomes "The analysis failed"), only for the running claim | The row, or no rows |
| `retry_analysis_job(p_id)` | `failed` or `cancelled` back to `queued`, attempts 0, timestamps, reason and links cleared | The row, or no rows |
| `cancel_analysis_job(p_id)` | `queued` to `cancelled` | The row, or no rows |

### Revision search (`search_report_revisions`)

Cosine search over `report_revisions.embedding`, across every revision that has a vector (AI revisions; human revisions have none today). It returns one row per revision, best first, with the title and description from the payload and the facet columns. It is `SECURITY INVOKER`, pins `search_path`, and is executable by `service_role` only. The agent calls it through `IncidentDB.search_revisions` for `POST /api/v1/incidents/revisions/search`. It is separate from `search_evidence_items`, which searches the crops of each video's searchable revision.

### Agent report writes (`report_revisions`)

PostgREST has no client-held transactions, so the agent's report write is one `INSERT` into
`report_revisions` (`IncidentDB.insert_ai_revision`): `kind='ai'`, `model_run_id` (a required
foreign key), `parent_id` NULL, the full contract report as `payload`, the crop list as `evidence` (entity entries carry `first_seen_seconds` and `last_seen_seconds` from `incident-contract-v4`; `evidence_crops` has no columns for them, so they live only in the revision),
and the facet columns copied from the payload. `revision_no` is `max + 1` for the video; if a
concurrent writer takes the number first, the unique `(video_id, revision_no)` key rejects the
insert (SQLSTATE 23505) and the agent retries with the next number, five attempts at most with
jittered backoff. Rows are append-only, so a re-generation is a new row and never changes an earlier
revision. The same insert carries `embedding`: the agent embeds `incident.title` and `incident.description` first, and an embedding failure stores NULL rather than failing the write.

> **Insertion order for agent writes:**
> In console agent mode the console upserts the run (with its `notes` record) only after the agent
> returns, so the agent inserts a placeholder row with `ignore_duplicates`
> (`insert_model_run_if_absent`); the console's later upsert fills in `notes`. Search persists
> `query_results` against `revision_id`.

> **Cascades:**
> Deleting a `videos` row cascades to `report_revisions`, their `evidence_crops`, and the
> `query_results` that reference those revisions. Direct deletes/updates of `report_revisions` are
> rejected by the trigger; only the video cascade can remove them. Deleting a `model_runs` row is
> refused while a revision references it.

---

### Client Access Models & PostgREST/Direct-Postgres Split

Database access is split across two client implementations according to operational requirements:

1. **Console and laptop tooling:**
   `incident-console-v2` uses Supabase PostgREST (`src/lib/postgrest/client.ts`, reached only from the adapters). Laptop tooling (the `supabase` CLI, the copy and parity scripts) uses direct Postgres (`INCIDENT_DB_DSN`). The console is database-backed only; there is no offline mode.

2. **Agent Side & Service Writers (Supabase PostgREST):**
   `services/agent/src/vss_agents/utils/incident_db.py` is the agent-side counterpart: an async
   Supabase PostgREST CRUD helper (`supabase-py`'s `AsyncClient`/`acreate_client`, not
   `asyncpg`/direct-Postgres) against the same schema. It touches only `videos` (reads; the console
   owns the row), `model_runs` and `report_revisions`; the search store reads `evidence_crops` and
   writes `queries` and `query_results`.
   - **Configuration:** `INCIDENT_SUPABASE_URL` and `INCIDENT_SUPABASE_SERVICE_ROLE_KEY`
     (deliberately NOT `INCIDENT_DB_DSN`, which the direct-Postgres tooling uses); unset means the
     feature is unavailable, no fallback.
   - **Network Constraint / DPI Blocking Rationale:** This split exists because the deployment VM
     (`kwanz-ws`) DPI-blocks raw Postgres wire protocol on port 5432, so only the HTTPS-based
     PostgREST route works for the agent from there.
   - **Transaction Limits & RPC:** PostgREST has no client-held transactions or
     `SELECT ... FOR UPDATE`; the agent appends `report_revisions` rows with a `revision_no` of
     `max + 1` and retries when the unique key rejects a concurrent writer (SQLSTATE 23505).
     `save_human_revision` and `set_official_revision` provide the atomic server-side operations the
     console needs.

### Row-Level Security (RLS) & Access Control

**Finals is locked down from its first migration.** The baseline turns off Supabase's default grants before it creates any object, so nothing is ever exposed, even for a moment:

- **Default-deny:** RLS is on for all seven tables, with no policies, so `anon` and `authenticated` cannot read, insert, update or delete rows anywhere.
- **No grants to the public roles:** every privilege on every table, view, sequence and function in `public` is revoked from `PUBLIC`, `anon` and `authenticated`. Every function is `service_role`-only, `SECURITY INVOKER`, with `SET search_path = ''`.
- **Default privileges:** objects that `postgres` creates in `public` later get nothing for `anon`/`authenticated` and everything for `service_role`; new functions get no `EXECUTE` for `PUBLIC` in any schema. Objects created by other roles are not covered, so a new function should still be checked by `supabase/tests/security_check.sql`.
- **pgvector lives in `extensions`**, so none of its functions are REST RPCs.
- **Self-check:** the baseline fails (and rolls back) if any table has RLS off, anything is reachable by `anon`/`authenticated`, or any function is `SECURITY DEFINER` or lacks a fixed `search_path`. Run `supabase/tests/security_check.sql` after every applied migration (expect 0 rows), and `supabase/tests/probe_api.sh` to confirm over REST that the public key is refused.
- **Role bypass:** Application services (`incident-console-v2`, `vss-agent`, `mock-backend`) authenticate via HTTPS PostgREST using `INCIDENT_SUPABASE_SERVICE_ROLE_KEY` (`service_role`), or connect directly via the Postgres wire protocol using `INCIDENT_DB_DSN` (`postgres`). Both roles hold `BYPASSRLS`. On Supabase `postgres` is not a superuser; it skips RLS through `BYPASSRLS`, not superuser status. `FORCE ROW LEVEL SECURITY` is not applied: it only subjects table owners (here `postgres`) to RLS, and `BYPASSRLS` overrides it.
- **Signups:** create the finals project with email signups turned off; nobody uses Supabase Auth, and an open signup would let anyone with the public key mint an `authenticated` user (who could still reach nothing).

**Midterm** had RLS on only `evidence_crops` and `query_results` and gave `anon`/`authenticated` full access to the other tables. `supabase/midterm/freeze_midterm.sql` closes that at the cutover and makes midterm read-only ([`../supabase/midterm/README.md`](../supabase/midterm/README.md)).

---

## 2. Cloudflare R2 Storage Architecture

Cloudflare R2 provides S3-compatible, permanent object storage for all video media and evidence images.
Local disks on `kwanz-ws` are treated as temporary caches only.

### Bucket Key Tree Layout

```
anomaly-detection-dataset/            # bucket name comes from R2_BUCKET
├── anomaly/                         # classified dataset clips (seed/eval) only
│   ├── animal_attacks/  assault/  burglary/  explosion/
│   ├── fighting/  road_accidents/  shooting/  demo/
│   │   └── <clip-name>.mp4
├── normal_videos/                   # baseline non-incident clips
│   └── <clip-name>.mp4
├── uploads/                         # all new user uploads (the console's own R2 upload, both modes)
│   └── <original name>.mp4          # flat; always an H.264 MP4 (converted on upload unless already one); a taken name gets -2, -3 before .mp4
├── thumbnails/                      # browser-captured report-card screenshots for new uploads
│   └── <video-key>.webp             # e.g. thumbnails/uploads/Animal001_x264.mp4.webp
├── Report/                          # empty folder marker, unused by code
├── scratch/analyze/<video-key>/     # agent scratch, deleted by the agent when the step ends (success or failure)
│   ├── <run-id>/<index>.mp4         # caption pass windows (INCIDENT_CHUNKING_ENABLED)
│   └── <run-id>-marks/marked.mp4    # set-of-marks copy P1 sees (INCIDENT_SOM_ENABLED), deleted right after P1
├── scratch/<approved>/<video-key>/  # experiment caption windows (experiment_captions "r2"): only under the owner-approved INCIDENT_EXPERIMENT_SCRATCH_PREFIX (X16, pending), never scratch/analyze
│   └── exp-<id>/<index>.mp4         # each deleted right after its caption call; the rest when the pass ends
└── evidence/                        # evidence images: agent crops and reviewer replacements
    └── <video-key>/run-<N>/<itemId>.jpg    # AI crops (what evidence search embeds), one run-<N> per analysis
        <video-key>/run-<N>/<itemId>-frame.jpg  # the whole video frame the box was found in, native size (the console draws the box on it)
        <video-key>/run-<N>/<itemId>-track.json # person tracks, and object tracks with INCIDENT_OBJECT_TRACKING_ENABLED (track_source: "object-tracker" on the entry): [{t, box, score}], t in seconds, box [top,left,bottom,right] on 0..1000, at most 5 per second
        <video-key>/{run,edit}-<N>/<itemId>-clip.mp4 # evidence clip: about 4 s around the item's second, H.264, no audio, cut by the console on first play
        <video-key>/edit-<N>/<itemId>.jpg   # reviewer replacements, one edit-<N> per upload
```

> [!NOTE]
> The `anomaly/` subfolders are reserved for classified seed/evaluation data. New user uploads are classification-neutral and use `uploads/<original name>.mp4` in both modes (the console converts every upload to an H.264 MP4 at the original resolution unless it is one already, so the stored extension is always `.mp4`); the VLM's later incident classification does not move or rename the object.
> In addition to the five fixture categories, live R2 storage includes `assault/` and `shooting/`.
> The `Report/` prefix is a 0-byte directory marker and is unused by code.
> The `evidence/` tree holds two writers' images, both under `evidence/<video key>/` where `<video key>` is `videos.filepath` (item ids `E#`, `I#` and `A#` are unique across types). No key holds a hash or UUID, and no key is ever overwritten: each write takes the next free number, and the put is conditional (`If-None-Match: *`):
> - the analyze route's crops (`CropEvidenceService`, `services/agent/src/vss_agents/utils/crop_evidence.py`, uploaded by `R2EvidenceStore`, `run-<N>` with N one above the highest existing run of that video), listed in `report_revisions.evidence`; an item without an image carries an `image_gap` reason; with `INCIDENT_TRACKING_ENABLED` a person matched to a track also has `<itemId>-track.json` in the same run (`score` -0.1 marks a sample the tracker carried without a detection; the console reads it on the server through `GET /api/reports/[videoId]/track`, taking the key from the revision's evidence entry, because R2 has no CORS rule for the console origin, and keeps only `t` and `box`) ([decision](decisions.md#2026-10-31-person-tracks-give-matched-evidence-its-best-moment-and-a-track-file));
> - reviewer replacements, uploaded by the console's `POST /api/reports/<videoId>/evidence-image` during Save, as `edit-<N>` with the smallest free N.
> - reviewer items, cut by `PATCH /api/reports/<videoId>/edit` at Save from the stored video: `<itemId>.jpg` (the crop), `<itemId>-frame.jpg` and, when the item has a track, `<itemId>-track.json` under the first free `edit-<N>/`; the entry has `source: "reviewer"`, `track_source: "reviewer"`, `track_partial` (the track ended before its window, so the item is badged "Incomplete track") and `ai_proposal` (the model's reading of the box, kept for audit); the track file is `{source, samples:[{t, box, conf}], span, partial, lost_at, reason}` and the console route reads it like an AI track ([decision](decisions.md#2026-11-03-a-reviewers-box-item-is-cut-and-stored-on-the-server-at-save));
> - evidence clips, cut by the console's `POST /api/reports/<videoId>/evidence-clip` on an item's first Play clip and stored as `<itemId>-clip.mp4` in the folder of the item's frame, image or track (conditional put; a second request signs the stored clip). No revision names a clip: it is a cache derived from the video, so the sweep counts one older than `minAgeHours` as an orphan and removes it, and the next play cuts it again; deleting the video removes it with the rest of `evidence/<video key>/`.
> - Two analyses of one video that start at the same moment can pick the same `run-<N>`; the conditional put stops the overwrite, so one of them fails instead of replacing the other.
>
> Which revision's images are searchable is decided by the `evidence_crops` table above. No writer deletes an image. `POST /api/reports/<videoId>/evidence-sweep` removes only images no revision or `evidence_crops` row of the video names and that are older than `minAgeHours` (default 24, at least 1; dry run by default). Deleting a video removes its video and thumbnail objects, then every object under `evidence/<video key>/`, then the video row; if a listing or a removal fails the row stays, so the delete can be repeated. Uploads create no VST camera, so the delete calls nothing on VST. Videos deleted before 2026-10-25 left their images behind ([status](status.md#7-archive)), and the sweep refuses an unknown video.

### Track files

`evidence/<video key>/run-<N>/<itemId>-track.json` is an array of `{t, box, score}` (`t` video seconds, `box` `[ymin, xmin, ymax, xmax]` on 0-1000, `score` the detector's confidence, or -0.1 when the box was carried by a tracker and not confirmed). A person's track comes from the perception stack; an instrument's or asset's, with `INCIDENT_OBJECT_TRACKING_ENABLED` on, from the object tracker and its entry has `track_source: "object-tracker"`. The file is the same shape for both, so every reader of tracks reads either.

### Key Validation & Generation Rules

1. **Validity Rule (`isValidR2Key` in `incident-console-v2/src/lib/r2/key.ts`):**
   A valid R2 object key must:
   - Be a non-empty string.
   - Not start with `/` (absolute filesystem path).
   - Not start with `.` or contain `..` (relative filesystem path).
   - Guard against VST/NvStreamer `filePath` outputs (which return `./streamer/media/...` or local file paths).
2. **Upload & Evidence Key Patterns:**
   Direct uploads from `incident-console-v2/src/app/api/uploads/r2/route.ts` format keys as `uploads/<name>`
   (`src/lib/r2/names.ts`): the original file name is kept with its extension replaced by `.mp4` (every stored
   video is an H.264 MP4, `src/lib/video/transcode.ts`); a character outside letters, digits and
   `. _ - ( ) + ,` (a space included) becomes `_`; a name over 150 characters is cut (extension kept); a name that is
   already stored gets `-2`, `-3` before the extension, found with `HeadObject` and written with
   `If-None-Match: *` so a race returns 409 instead of overwriting. `POST /api/uploads/check` returns the same
   name, `unsafeChars`, `truncated` and `exists` before the upload, and the upload page warns on each.
   Thumbnail keys are deterministic: `thumbnailKeyForVideo(videoKey)` returns
   `thumbnails/${videoKey}.webp`; no database column is required.
   Agent crop keys come from `evidence_key(video_key, batch_id, item_id)` in
   `services/agent/src/vss_agents/utils/crop_evidence.py` (`batch_id` is `run-<N>`, allocated by
   `next_run_batch_id`); reviewer replacements from `uploadedEvidenceKey` in
   `incident-console-v2/src/modules/reports/domain/revision.ts` (`edit-<N>`).
   Objects written before 2026-10-28 were renamed by `supabase/r2/migrate_r2_names.py` (copy, verify, update
   the rows; the live rename has been run on finals and the old copies and orphaned objects were deleted by hand afterwards). `model_runs.notes` keeps the old key as history.
3. **Durability Verification:**
   `verifyR2Video` performs `HeadObject` after a direct upload (requiring the exact expected nonzero byte length)
   and again before analysis (requiring a nonempty object). A valid-looking key alone is not treated as a stored video.
4. **Presigned Media URLs:**
   Access is private by default. Video playback and up to six report-card thumbnail URLs are generated server-side using AWS SDK S3 client
   presigning (`getSignedUrl` with `GetObjectCommand`, `ResponseContentDisposition: 'inline'`, and a 1-hour
   expiration window). Agent-side evidence crops and video files are resolved via `services/agent/src/vss_agents/utils/r2.py` (`resolve_r2_url` / `get_r2_presigned_url`), generating boto3 S3 presigned URLs (1-hour expiration) or using public base URLs when configured (`INCIDENT_VIDEO_BASE_URL`).

---

For known schema issues and technical debt, see [`status.md`](status.md).
