# BugNemo Plugin

The BugNemo plugin ingests bug similarity data from the NVBugs SQL Server database into the Data Flywheel for embedding training (for example, bug deduplication). It connects to NVBugs DW (and optionally NVBugs DotNet for richer bug content), fetches duplicate/similar bug relationships, and yields records in the `bug_pairs` format.

**Source:** `ingestion-plugins/plugins/bugnemo/`

---

## Prerequisites

- Access to the NVBugs database (Gremlins/DW). Optional: NVBugs DotNet for detailed bug content (synopsis, keywords).
- Trigger or job with `job_type: "embedding_job"` and `dataset_type: "bug_pairs"`.

---

## Configuration

The plugin uses a Pydantic config model (`BugNemoConfig`). Credentials can be supplied in the trigger/job `config` (with `env:VAR` for secrets) or via environment variables on the **plugin worker** container.

### Config Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `server` | string | Yes* | Database server (or set `NVBUGS_DBSERVER`) |
| `database` | string | Yes* | Database name (or set `NVBUGS_DBNAME`) |
| `username` | string | Yes* | Username (or set `NVBUGS_DBUSER`) |
| `password` | string | Yes* | Password (or set `NVBUGS_DBPWD`) |
| `max_pairs` | int | No | Max bug pairs to fetch (default: 1000) |
| `chunk_size` | int | No | DB query batch size (default: 500, range 100–2000) |
| `from_date` / `to_date` | datetime/str | No | Override date range (ISO); otherwise watermark-based |
| `default_start_date` | datetime/str | No | Start date when no watermark exists (default: 2020-01-01) |

\*Required either in config or via the corresponding env var on the worker.

### Example trigger ingestion_config

```json
{
  "plugin_name": "bugnemo",
  "dataset_type": "bug_pairs",
  "batch_size": 200,
  "config": {
    "server": "env:NVBUGS_DBSERVER",
    "database": "env:NVBUGS_DBNAME",
    "username": "env:NVBUGS_DBUSER",
    "password": "env:NVBUGS_DBPWD",
    "max_pairs": 2000
  }
}
```

---

## Environment Variables (Plugin Worker)

Set these on the **ingestion-plugins-worker** (Docker Compose or Helm) when using `env:` in config or when not passing credentials in config:

| Variable | Required | Description |
|----------|----------|-------------|
| `NVBUGS_DBSERVER` | Yes* | DB server (Gremlins/DW); same connection is used for NVBugsDW and NVBugs_DotNet views |
| `NVBUGS_DBNAME` | Yes* | Primary database name |
| `NVBUGS_DBUSER` | Yes* | Database username |
| `NVBUGS_DBPWD` | Yes* | Database password |

\*Required if not provided in the trigger/job config.

The plugin uses a single SQL Server connection. DotNet views (`NVBugs_DotNet.dbo.*`) are queried on the same server (e.g. cross-database); there are no separate `NVBUGS_DOTNET_*` env vars.

---

## Database Tables Used

| Table/View | Database | Purpose |
|------------|----------|---------|
| `DuplicateView` | NVBugsDW | Duplicate/similar bug relationships |
| `BugView` | NVBugsDW | Bug metadata and RequestDate |
| `vw_Bug_Copilot` | NVBugs_DotNet | Detailed bug content (optional) |
| `vw_Bug_Keywords` | NVBugs_DotNet | Bug keywords (optional) |

If DotNet is not configured, the plugin falls back to BugView-only content for the document string.

---

## Output Format

Each yielded record is an `IngestionRecord` with the following shape. The Data Loader expects the four keys inside `data` for the `bug_pairs` schema:

```json
{
  "data": {
    "bugid": "12345",
    "similar_bug": "12346",
    "bugdata": "summary: ... | synopsis: ... | description: ...",
    "similarbug_data": "summary: ... | synopsis: ... | description: ..."
  },
  "timestamp": "2024-01-15T10:30:00Z",
  "source_id": "12345-12346"
}
```

- **data.bugid**, **data.similar_bug**: string IDs.
- **data.bugdata**, **data.similarbug_data**: document strings built from the DB (DotNet or DW fallback); the loader indexes them into `flywheel_embeddings_data` and `flywheel_embeddings_similar_data`.
- **timestamp**: ISO 8601 (used for watermarking).
- **source_id**: optional identifier for the pair (e.g. `bugid-similar_bug`).

---

## Watermark and Date Range

- **Incremental sync:** The plugin receives `last_sync_watermark` from the ingestion state and fetches only records newer than that date.
- **First run:** If no watermark exists, the plugin uses `default_start_date` from config if set, otherwise a default of 2020-01-01.
- **Override:** You can force a date range with `from_date` and `to_date` in the config (ISO format).

---

## Validation and Credentials

- **validate_config:** Runs in the API container; validates config structure (e.g. Pydantic parsing). Does not require DB credentials.
- **validate_credentials:** Runs on the worker before ingestion; ensures required DB credentials are present (either in config or env). The worker’s `validate_environment()` at startup can also fail fast if env vars are missing.

---

## Usage Example (Trigger)

1. Set env vars on the plugin worker (or pass credentials in config with `env:`).
2. Create a trigger with `enable_data_ingestion: true` and `ingestion_config` as above; ensure `job_type: "embedding_job"` and `dataset_type: "bug_pairs"`.
3. Trigger executions will run ingestion, then evaluation/training as configured.

For more details, see [Retraining Triggers](../15-retraining-triggers.md) and [Data Ingestion Plugins (index)](index.md).

---

## See Also

- [Architecture](architecture.md) — plugin interface and data flow
- [Deployment](deployment.md) — plugin container and env vars
- [Troubleshooting](troubleshooting.md) — secrets, errors, data volume
- `ingestion-plugins/plugins/bugnemo/README.md` — in-repo plugin readme
