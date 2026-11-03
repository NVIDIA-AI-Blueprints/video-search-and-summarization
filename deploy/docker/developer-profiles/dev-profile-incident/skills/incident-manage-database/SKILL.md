---
name: incident-manage-database
description: Use to inspect or change the incident Supabase Postgres schema, write, test and apply a migration under supabase/migrations/, run the SQL harness or security check, query incident/review tables, or locate and validate video objects in Cloudflare R2. Not for running an analysis (use incident-analyze-video) or searching evidence (use incident-search).
license: Apache-2.0
metadata:
  version: "0.2.1"
  profile: "dev-profile-incident"
  tags: "incident supabase postgres migration postgrest r2"
---
# Incident Manage Database

There is **one Supabase project per phase**:

- **Finals** is the live team database. There is no staging copy, so treat every write as production.
- **Midterm** (`rfevukimyftzbxlehwel`) is a frozen, read-only record. **Never write to it, push to it or apply anything to it**, apart from `supabase/midterm/freeze_midterm.sql` (once, at the cutover) or `unfreeze_midterm.sql` (rollback only).

Canonical facts: [`.docs/data.md`](../../.docs/data.md) (table specs, report revisions, RPCs, cascades, security, R2 layout). Schema files and harness: [`supabase/README.md`](../../supabase/README.md). Which component points at which project: [`.docs/architecture.md`](../../.docs/architecture.md#two-supabase-projects-one-per-phase).

## Access routing

| Caller / network | Client | Credentials |
|---|---|---|
| Agent on `kwanz-ws` (port 5432 DPI-blocked) | PostgREST over HTTPS (`supabase-py`) | `INCIDENT_SUPABASE_URL`, `INCIDENT_SUPABASE_SERVICE_ROLE_KEY` |
| `incident-console-v2` and `eval/` writers | PostgREST (console: the adapters in `src/modules/*/infrastructure/supabase/` over `src/lib/postgrest/client.ts`, see [`.docs/mvp2/console-hexagonal-architecture.md`](../../.docs/mvp2/console-hexagonal-architecture.md); `eval/db_postgrest.py`) | same |
| Laptop tooling, `supabase` CLI, copy and parity scripts | Direct Postgres | `INCIDENT_DB_DSN` (session pooler, 5432). Strip any `+psycopg2` suffix for the CLI. |

Only `service_role` and `postgres` reach anything: the public (`anon`) key is refused everywhere on finals, and on midterm after the freeze.

The report model is versioned and append-only. Every AI analysis appends one `report_revisions` row (`kind='ai'`, `model_run_id`, `parent_id` NULL, full contract report in `payload`, crops in `evidence`, facets from the payload). A reviewer edit calls `/rpc/save_human_revision_with_crops` (`kind='human'`, `parent_id` the revision opened, inherits the run; it wraps `save_human_revision` and stores the crops when the video has no official revision), which locks the video row and assigns `revision_no = max + 1`. A re-generation is a new AI branch (new run, `parent_id` NULL); no revision is ever edited or deleted, and the table's trigger enforces that. The agent writes AI revisions directly and computes `revision_no` as `max + 1`, retrying on a unique violation (SQLSTATE 23505), because PostgREST has no client-held transactions.
`videos.official_revision_id` names the one official revision per video (composite FK to that video's revision). Select it only through `/rpc/set_official_revision(video_id, revision_id, crops)`, which in one transaction sets the pointer (and `official_since`) and moves the video's `evidence_crops` to the new official revision; pass a NULL revision to clear the pointer, which leaves the newest revision searchable. A video with no official revision keeps its searchable rows on its newest revision, written through `/rpc/set_revision_crops(video_id, revision_id, crops)` (refused for any other revision). Never write `report_revisions`, `videos.official_revision_id` or `evidence_crops` by hand.
The report library reads filtered, paginated metadata through the service-role-only `/rpc/list_incident_report_summaries`; its `p_entities`, `p_instruments`, and `p_assets` arrays filter the shown revision's evidence by `(video_id, revision_id)`. It also takes `p_kind` (`ai`/`human`), `p_scope` (`all`/`official`/`not_official`), `p_entity_types`, `p_evidence_match` (`contains`/`exact`), and `p_timezone` (default `'UTC'`) for localised weekday and time-of-day filtering, and returns each card's `revisionId`, `revisionNo`, `kind`, analysis `outcome`, `isOfficial`, `officialSince`, `versionCount`, `evidenceCount` and `thumbnail`. The library path does not read or sign video.
The operations dashboard reads bounded aggregates through the service-role-only `/rpc/get_incident_dashboard`, counting each video once through its official revision, else its newest, reporting the official and not-yet-official split, and accepting `p_scope` (`all`/`official`/`not_official`) and `p_timezone` (default `'UTC'`) for local-time trend and heatmap bucketing; it never downloads full report notes or evidence rows.
Background analysis runs through the `analysis_jobs` table and its service-role functions; it is described in [`.docs/data.md`](../../.docs/data.md).
Natural-language search queries evidence items through the service-role-only `/rpc/search_evidence_items`, which performs a pgvector cosine distance search over `evidence_crops.embedding` scoped to each video's searchable revision (`searchable_revision_id(video_id) = evidence_crops.revision_id`: official, else newest) and shows the revision's text; hits return `video_id`/`incident_id` and `revision_id`, and the query and hits (with critic verdicts, `unverified` while the critic is off) are logged into `query_results` against that revision. The optional last argument `p_video_id` scopes the scan to one video; a NULL embedding with it lists that video's crops in time order (migration `20261028090000_search_evidence_items_video_id.sql`).
`report_revisions.embedding` (1024) holds each AI revision's summary vector, written in the insert; `search_report_revisions` searches it, and human revisions have NULL. `evidence_crops` holds the searchable revision of each video (official, else newest) and is written by `set_official_revision` or `set_revision_crops` from the crop list the console sends (the agent returns the embeddings via `POST /api/v1/incidents/evidence-embeddings`; it does not write the rows). The search route embeds queries with the same model, so a model change means re-embedding every row.
`model_runs` rows without a revision are normal: failed analysis attempts (`contract_failed` / `request_failed`) and runs whose revision was removed by a video delete. Never count `model_runs` rows as analyses without filtering by `run_outcome(notes)`.

## Rules

- **Migrations are the schema authority.** Finals is built by `supabase/migrations/20261020000000_finals_baseline.sql`; every change is a new timestamped file `supabase/migrations/YYYYMMDDHHMMSS_<what>.sql` after it. Never edit the baseline once finals has it, never add to `supabase/midterm/`, and never hand-edit the live DB without a matching migration file.
- **Test before applying:** extend and run the SQL harness (below) in the same PR. It applies every file in `supabase/migrations/` in order.
- **Dry-run first, then ask** before applying for real. Applying is live and shared.
- **After applying, run `supabase/tests/security_check.sql`** against finals: 0 rows expected.
- **Security on every new object:** RLS on every new table; `SET search_path = ''` and no `SECURITY DEFINER` on every function; nothing for `PUBLIC`/`anon`/`authenticated`. The harness and the security check fail otherwise. Default privileges only cover objects created by `postgres`.
- **Cascades are real.** Deleting a `videos` row cascades through its revisions, their `evidence_crops` and the `query_results` that name those revisions. `model_runs` and `report_revisions` are linked by a required foreign key, so a revision needs its run row first. The append-only trigger permits only the video cascade, so delete a video rather than a revision.
- **Changing a function signature:** drop the old signature in the same migration. PostgREST rejects ambiguous overloads that share parameter names.
- **Never** print or commit the service-role key, public key or DSN. Backend code uses the service role server-side only.

## Instructions

### Run the SQL harness (no Supabase, no network)

```bash
cd deploy/docker/developer-profiles/dev-profile-incident/supabase/tests
uv run --no-project --python 3.12 --with pgserver --with 'psycopg[binary]' python test_baseline.py   # finals schema
uv run --no-project --python 3.12 --with pgserver --with 'psycopg[binary]' python test_freeze.py     # midterm freeze files
```

### Apply a migration to finals

From the repo root, with `FINALS_DSN` the finals session-pooler URL (after the cutover this is `INCIDENT_DB_DSN` in `.env.local`). Finals' history is not in step with `supabase/migrations/` (the two `20261028090000` files were applied by hand without history rows), so a plain `db push` would re-run them. Until the history is repaired, apply one file at a time:

```bash
W=deploy/docker/developer-profiles/dev-profile-incident
supabase migration list --workdir $W --db-url "$FINALS_DSN"                          # 1. local vs remote; ask captain/lead before applying
psql "$FINALS_DSN" -v ON_ERROR_STOP=1 -f $W/supabase/migrations/<version>_<name>.sql  # 2. apply (the file holds its own BEGIN/COMMIT)
supabase migration repair --status applied <version> --workdir $W --db-url "$FINALS_DSN"  # 3. record it
psql "$FINALS_DSN" -f $W/supabase/tests/security_check.sql                           # 4. expect 0 rows
```

Target state, once the history is repaired: `supabase db push --workdir $W --db-url "$FINALS_DSN" --dry-run`, then without `--dry-run`.

`db push` at midterm fails ("remote migration versions not found"), because midterm's history names versions that are no longer in `supabase/migrations/`. Do not "fix" that with `migration repair` against midterm.

To record an out-of-band change on finals without re-executing SQL:

```bash
supabase migration repair --workdir $W --db-url "$FINALS_DSN" --status applied <YYYYMMDDHHMMSS>
```

**The laptop could not reach the pooler on 2026-10-31** (5432 and 6543 timed out), but `kwanz-ws` could. Tunnel through the VM and point the DSN's host and port at the tunnel (the pooler routes on the `postgres.<ref>` user name, so the host change is fine), then close the tunnel by PID:

```bash
ssh -f -N -o ExitOnForwardFailure=yes -L 127.0.0.1:15432:aws-0-ap-southeast-1.pooler.supabase.com:5432 kwanz-ws
# FINALS_DSN with @aws-0-ap-southeast-1.pooler.supabase.com:5432 replaced by @127.0.0.1:15432
```

### Read via PostgREST (works from anywhere with HTTPS)

```bash
curl -s "$INCIDENT_SUPABASE_URL/rest/v1/report_revisions?video_id=eq.<id>&select=*&order=revision_no.desc" \
  -H "apikey: $INCIDENT_SUPABASE_SERVICE_ROLE_KEY" \
  -H "Authorization: Bearer $INCIDENT_SUPABASE_SERVICE_ROLE_KEY"
```

With an `sb_secret_...` key (finals) send `apikey` only: drop the `Authorization` line.

### Finals keys (`sb_secret_`)

Finals has no legacy JWT keys: the server key is `sb_secret_...` and the public key `sb_publishable_...`. They are not JWTs, so the Supabase gateway rejects one sent as `Authorization: Bearer` ("invalid JWT"); send it in the `apikey` header only. Who sends the key, and whether it copes:

| Sender | How | Handles `sb_secret_`? |
|---|---|---|
| `vss-agent` (`utils/incident_db.py`, and through it `SupabaseSearchStore`) | `supabase-py` `acreate_client` | Depends on the installed `supabase` version: `pyproject.toml` only requires `supabase~=2.0` and `uv.lock` does not pin it. Older 2.x releases validate the key as a JWT and raise `Invalid API key` (2.15.x still does; 2.18.0 and later do not). Prove it with the VM key check (step 4) |
| `mock-backend/base_profile_mock` (mock mode) | Same `incident_db`, its own venv | Yes: its `uv.lock` pins `supabase` 2.31.0 |
| `incident-console-v2` `src/lib/postgrest/client.ts` | `apikey`; Bearer only for JWT-style (`eyJ...`) keys | Yes |
| `eval/db_postgrest.py` | `apikey`; Bearer only for JWT-style (`eyJ...`) keys | Yes |
| Archived `archived/incident-console/db_postgrest.py` | `apikey` + always `Authorization: Bearer` | No (archived; never point it at finals) |
| `supabase/tests/probe_api.sh` | `apikey`; Bearer only for `eyJ...` keys | Yes |
| `supabase/copy/`, `parity_check.py`, `supabase` CLI | Direct DSN, no key | Not affected |

Follow-ups (not in this change): pin a minimum `supabase` version in `services/agent/pyproject.toml` and lock it in `uv.lock`.

### R2 objects

- **One bucket for both phases:** finals and the midterm record name the same keys; nothing was copied. A delete in finals also removes the object the midterm record names.
- **Keys:** all new user uploads go to `uploads/<original name>.mp4` in both modes (the extension is always replaced by the H.264 conversion) (a taken name gets `-2`, `-3`; unsafe characters become `_`; max 150 characters; never overwritten). Classified seed/evaluation clips remain under `anomaly/<category>/<file>`.
- **Valid keys** (per `isValidR2Key`): not absolute, no `.`/`..` prefix. VST `./streamer/media/...` paths are not keys.
- **Durability check:** `verifyR2Video` uses `HeadObject`; direct uploads require an exact nonzero byte-length match, and analysis requires a nonempty object before inference.
- **Playback** is by 1-hour presigned GET only. The bucket is private.
- **Report-card thumbnails:** new uploads use `thumbnails/<video-key>.webp`, derived by `thumbnailKeyForVideo`; legacy videos may have no thumbnail and use the UI skeleton. Deleting a video deletes this derived object too.
- **Evidence images** live under `evidence/<video key>/run-<N>/` (AI crops, and `<itemId>-frame.jpg` beside each: the whole frame) and `evidence/<video key>/edit-<N>/` (reviewer images), plus `<itemId>-track.json` (tracks) and `<itemId>-clip.mp4` evidence clips the console caches on first play (no revision names them, so the sweep may remove them and the next play cuts again); `<video key>` is `videos.filepath`. A revision's `evidence` (and `evidence_crops.image`) names the keys it shows, and older revisions keep naming images a reviewer later replaced, so an image is only an orphan when no revision of that video names it. `POST /api/reports/<videoId>/evidence-sweep` (dry run by default) lists such orphans older than `minAgeHours` (default 24); `{"dryRun": false}` removes them. Deleting a video (`DELETE /api/reports/<videoId>/delete`) removes its video, thumbnail and every `evidence/<video key>/` object before the rows, and keeps the rows if any removal fails. Images of videos deleted before 2026-10-25 are still in R2, and the sweep cannot reach them (it needs the video row).

### Old R2 names

Objects written before 2026-10-28 were renamed once by `supabase/r2/migrate_r2_names.py`; see its README.

## After changing anything

In the same PR:
- Extend `supabase/tests/test_baseline.py` for the new behaviour.
- Update the table specs in [`.docs/data.md`](../../.docs/data.md).
- Update [`.docs/analysis-schema.md`](../../.docs/analysis-schema.md) if report fields moved.
- Update [`.docs/status.md`](../../.docs/status.md) if the migration was applied live.
- Add a [`.docs/decisions.md`](../../.docs/decisions.md) entry for any schema decision.
