# Supabase schema — dev-profile-incident

There is one Supabase project per project phase:

| Project | Role | Schema source |
|---|---|---|
| **Finals** (new project) | Live database for every service | [`migrations/`](migrations/): one baseline file, then any later finals migration |
| **Midterm** (`rfevukimyftzbxlehwel`) | Frozen, read-only record | [`midterm/`](midterm/README.md): the 14 midterm migrations, archived. **Never push** |

The decision and its reasons: [`../.docs/decisions.md`](../.docs/decisions.md) ("One Supabase
project per phase"). Table and RPC reference: [`../.docs/data.md`](../.docs/data.md).

This directory is the `--workdir` target of the `supabase` CLI
(`--workdir deploy/docker/developer-profiles/dev-profile-incident`, which reads
`supabase/migrations/`). It is not a fully initialised CLI project: there is no `config.toml`, and
nobody runs `supabase init`/`link` here. `supabase db push --workdir <dir> --db-url <url>` works
against a bare `migrations/` folder.

## Layout

```
supabase/
  migrations/   20261020000000_finals_baseline.sql — builds finals from empty
                20261022123252_drop_unused_tables.sql — removes tables no live code uses
                20261023000000_report_revisions_schema.sql — versioned JSONB reports
                20261023000100_report_revisions_functions.sql — revision RPCs
                20261023000200_searchable_latest_revision.sql — crops for the newest revision, official_since, card fields
                20261023120000_report_revision_embeddings.sql — revision summary vectors, HNSW index, search RPC
                20261024090000_save_human_revision_with_crops.sql — a human save that stores its crops in the same call
                20261028090000_r2_rename.sql — apply_r2_rename: points rows at renamed R2 keys (evidence-only exception to append-only)
                20261028090000_search_evidence_items_video_id.sql — search_evidence_items gains p_video_id (one incident's evidence)
                20261031090000_dashboard_official_else_newest.sql — the dashboard counts official, else newest, with the split and p_scope
                20261031120000_analysis_jobs.sql — the console's background analysis queue (table + claim/heartbeat/finish/retry/cancel)
                20261101090000_video_blur_faces.sql — privacy blur columns and claim_queued_analysis_job (removed again by the next file)
                20261101120000_dashboard_byte_order_ties.sql — get_incident_dashboard ties broken in byte order (not applied to finals)
                20261102090000_drop_blur_faces.sql — drops the blur columns and claim_queued_analysis_job; cancels unfinished re-analysis jobs
  tests/        SQL harness (test_baseline.py, test_freeze.py), security_check.sql, supabase_stub.sql,
                parity_check.py, rehearse.py, probe_api.sh
  r2/           migrate_r2_names.py (+ test): copy every old R2 object to a readable key, verify, update the rows; never deletes
  copy/         copy_midterm_to_finals.py + transform.sql: the one-way midterm -> finals copy (historical)
  midterm/      frozen midterm record: migrations/ (14), midterm_base_tables.sql, freeze/unfreeze SQL
```

## Current finals schema

Tables, functions, security and R2 keys: [`../.docs/data.md`](../.docs/data.md); which migrations are applied to finals: [`../.docs/status.md`](../.docs/status.md#3-database-status).

## Tests (no Supabase, no network)

The harness runs on a throwaway local Postgres (`pgserver`: Postgres 16 + pgvector).
`tests/supabase_stub.sql` adds the Supabase API roles and **Supabase's default grants to
anon/authenticated**, so the lockdown is tested against what a new project starts with.

```bash
cd supabase/tests
# Finals schema: applies every file in ../migrations/ in order; 45 tests (security and behaviour)
uv run --no-project --python 3.12 --with pgserver --with 'psycopg[binary]' python test_baseline.py
# Midterm freeze/unfreeze on a local replica of midterm
uv run --no-project --python 3.12 --with pgserver --with 'psycopg[binary]' python test_freeze.py
```

`tests/rehearse.py <midterm env file>` is the historical cutover rehearsal (local Postgres → baseline
→ copy from midterm, read only → security audit → reader parity → re-copy with `--replace`). It
predates the revision migration, needs the midterm DSN and writes only to the local Postgres.

## Building finals

The schema push and copy commands are below; what points at which project is in
[`../.docs/architecture.md`](../.docs/architecture.md#two-supabase-projects-one-per-phase). In short, with `FINALS_DSN` the finals session-pooler URL (port
5432, plain `postgresql://`, no SQLAlchemy suffix):

```bash
W=deploy/docker/developer-profiles/dev-profile-incident   # run from the repo root
supabase db push --workdir $W --db-url "$FINALS_DSN" --dry-run
supabase db push --workdir $W --db-url "$FINALS_DSN"
psql "$FINALS_DSN" -f $W/supabase/tests/security_check.sql    # expect 0 rows
FINALS_URL=... FINALS_PUBLIC_KEY=... FINALS_SERVICE_KEY=... $W/supabase/tests/probe_api.sh
                                       # expect 401/403 for every public-key line, 200 for the service key
```

**Finals uses the new key types:** the server key is `sb_secret_...` and the public key
`sb_publishable_...`, not JWTs. They go in the `apikey` header only (`probe_api.sh` does this).
Before the switch, the VM's `supabase-py` must be proven to accept the `sb_secret_` key (the
agent's `uv.lock` locks 2.33.0, but the VM venv was built by hand, and older 2.x releases reject non-JWT keys), and the console and
eval stop sending the key as `Authorization: Bearer` (merged in PR 149). The connection test and the
table of every key sender are in the
[`incident-manage-database`](../skills/incident-manage-database/SKILL.md#finals-keys-sb_secret_)
skill.

The one-way midterm → finals copy in [`copy/`](copy/) was used for the original cutover and is kept
for the record. It targets the pre-revision schema; do not run it against the current finals schema.
Its design and the per-run decisions are in the copy script's comments.
R2 objects were never copied: same bucket, same keys.

## Adding a finals migration

Add `migrations/YYYYMMDDHHMMSS_<what>.sql` after the baseline, never edit an applied migration, and
never add files to `midterm/`. In the same PR:

- extend `tests/test_baseline.py` (it applies every file in `migrations/` in order);
- give every new table RLS and every new function `SET search_path = ''` (the harness and
  `security_check.sql` fail otherwise);
- update [`../.docs/data.md`](../.docs/data.md);
- after applying, run `tests/security_check.sql` against finals (expect 0 rows).

Migrations must be safe on the live database, which already has the revision schema: use
`IF NOT EXISTS`, `CREATE OR REPLACE`, guarded `DO` blocks and conditional drops so the same file
applies cleanly to a fresh database and to the existing one. `tests/test_baseline.py` reapplies the
revision migrations on the built schema to prove this.

Applying to the live database is "ask first" (profile [`AGENTS.md`](../AGENTS.md)). To record an
out-of-band change without running SQL:

```bash
supabase migration repair --workdir deploy/docker/developer-profiles/dev-profile-incident \
  --db-url "$FINALS_DSN" --status applied <YYYYMMDDHHMMSS>
```
