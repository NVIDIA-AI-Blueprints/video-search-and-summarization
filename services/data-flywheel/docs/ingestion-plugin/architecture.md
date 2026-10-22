# Ingestion Plugin Architecture

This page describes the plugin system design, data flow, pipeline integration, state management, and where each piece of code lives. The system spans **two codebases**: the main Data Flywheel (DFW) and the **ingestion-plugins** container.

---

## Code Map

**Main DFW (orchestration, HTTP client):**

```
src/
├── lib/ingestion/                    # Models and client used by DFW
│   ├── __init__.py
│   ├── models.py                     # IngestionConfig, IngestionContext, IngestionResult
│   └── plugin_client.py              # IngestionPluginClient (HTTP calls to plugin container)
│
├── api/triggers/                     # Trigger API (references ingestion_config)
│   ├── models.py                     # RetrainingTrigger + ingestion_config
│   └── schemas.py
│
└── tasks/
    ├── ingestion/
    │   ├── service.py                # DataIngestionService (run_ingestion → client)
    │   └── __init__.py
    └── triggers/
        └── executor.py               # TriggerExecutor (calls DataIngestionService)
```

**Plugin container (ingestion-plugins repo / folder):**

```
ingestion-plugins/
├── src/
│   ├── lib/
│   │   ├── base.py                   # IngestionPlugin, IngestionContext, IngestionRecord
│   │   ├── registry.py               # PluginRegistry (auto-discovery, enabled via plugin.yaml)
│   │   ├── state.py                  # IngestionStateManager (watermarks in MongoDB)
│   │   ├── loader.py                 # DataLoader (batch → Elasticsearch)
│   │   ├── job_store.py              # Job tracking (MongoDB)
│   │   └── es_client.py              # Elasticsearch client
│   ├── api/
│   │   ├── endpoints.py              # POST /execute, GET /plugins, GET/DELETE /jobs/{id}
│   │   └── schemas.py                # Request/response schemas
│   ├── tasks.py                      # Celery task: run plugin → DataLoader
│   └── celery_app.py
├── plugins/                          # User-editable plugin folder
│   ├── bugnemo/
│   │   ├── __init__.py               # Exports BugNemoPlugin
│   │   ├── plugin.py                 # BugNemoPlugin implementation
│   │   ├── config.py                 # BugNemoConfig (Pydantic)
│   │   └── README.md
│   └── my_custom/
│       ├── __init__.py
│       ├── plugin.py
│       └── README.md
├── plugin.yaml                       # enabled: [bugnemo, ...]
├── Dockerfile
└── README.md
```

---

## Plugin System Design

### Directory Structure (Plugin Container)

Each plugin lives in `ingestion-plugins/plugins/<name>/` with at least:

- `__init__.py` — exports the plugin class
- `plugin.py` — class inheriting from `IngestionPlugin`
- Optional: `config.py` (Pydantic config), `README.md`

Plugins are **enabled** via the `enabled` list in `ingestion-plugins/plugin.yaml`. Only enabled plugins are loaded at startup; the registry also runs each plugin’s `validate_environment()` for fail-fast checks.

### Job Types & Dataset Types

The trigger’s `job_type` and `ingestion_config.dataset_type` determine how the plugin container’s **DataLoader** routes and validates records.

| job_type       | dataset_type           | Target indices |
|----------------|------------------------|----------------|
| `embedding_job`| `bug_pairs`, `triplet`, `bug_pairs_separated` | `flywheel_embeddings_data`, `flywheel_embeddings_similar_data` |
| `llm_job`      | `completion`, `tool_calling` | `flywheel_llm_data` |
| `llm_job`      | `dpo`                  | `flywheel_dpo_data` |

Validation (e.g. required fields for `bug_pairs`: `bugid`, `similar_bug`, `bugdata`, `similarbug_data`) is implemented in the plugin container’s DataLoader to match DFW’s loading logic.

### Plugin Interface (`ingestion-plugins/src/lib/base.py`)

All plugins inherit from `IngestionPlugin` and implement:

- `name: str` — plugin identifier (e.g. `"bugnemo"`).
- `validate_config(self, config: dict) -> bool` — validate config structure.
- `ingest(self, context: IngestionContext) -> Iterator[IngestionRecord]` — generator that yields records.

Optional: `validate_credentials()` or `validate_environment()` for worker-side checks. Context includes `workload_id`, `client_id`, `last_sync_watermark`, and plugin `config`. Records have `data`, `timestamp`, and `source_id`.

### Auto-Discovery (`ingestion-plugins/src/lib/registry.py`)

The registry scans `plugins/` only for **enabled** plugin packages (from `ingestion-plugins/plugin.yaml`: the `enabled` list). Plugins are loaded once at startup. Duplicate plugin names raise `PluginRegistryError`. Config values with an `env:VAR` prefix are resolved at runtime on the worker when the plugin's `validate_credentials()` method runs.

---

## Configuration

### Trigger Model (DFW)

`RetrainingTrigger` includes:

- `ingestion_config: IngestionConfig | None` — plugin name, dataset_type, batch_size, and plugin-specific `config` (use `env:VAR` for secrets). dataset_type must be valid for the trigger’s `job_type` (e.g. `embedding_job` → `triplet`, `bug_pairs`, `bug_pairs_separated`; `llm_job` → `completion`, `tool_calling`, `dpo`).

See [Retraining Triggers](../15-retraining-triggers.md) and [BugNemo](bugnemo.md) for examples.

---

## Data Flow

1. **Trigger fires** (Celery Beat or manual) → `TriggerExecutor` runs.
2. **DataIngestionService.run_ingestion()** (DFW):
   - Calls **IngestionPluginClient** → `POST /execute` on the plugin container with `plugin_name`, context, `job_type`, `dataset_type`.
   - Polls `GET /jobs/{id}` until completion.
   - Optional cleanup via `DELETE /jobs/{id}`.
3. **Plugin container**:
   - Enqueues Celery task; task resolves the watermark from MongoDB via `IngestionStateManager`, loads plugin via `PluginRegistry.get_plugin()`, and runs `plugin.ingest(context)`.
   - **DataLoader** consumes the record stream and writes to Elasticsearch (and similar/mapping indices as per job/dataset type).
   - Updates the watermark after completion.
   - Job status and metrics stored in MongoDB (`ingestion_plugins.jobs`).

---

## Pipeline Integration (Key Classes)

| Class | Location | Responsibility |
|-------|----------|----------------|
| **DataIngestionService** | `src/tasks/ingestion/service.py` | Orchestrates run: call plugin service → poll → cleanup |
| **IngestionPluginClient** | `src/lib/ingestion/plugin_client.py` | HTTP client for plugin container (execute, poll job, delete job) |
| **TriggerExecutor** | `src/tasks/triggers/executor.py` | Runs triggers; calls DataIngestionService when ingestion is enabled |
| **IngestionStateManager** | `ingestion-plugins/src/lib/state.py` | Read/write watermark state in `ingestion_watermarks` collection |
| **PluginRegistry** | `ingestion-plugins/src/lib/registry.py` | Discover and load plugins enabled in `plugin.yaml` |
| **DataLoader** | `ingestion-plugins/src/lib/loader.py` | Route records to Elasticsearch by job_type / dataset_type |

---

## State Management (Watermarks)

Watermarks are stored in the **plugin container's** MongoDB `ingestion_watermarks` collection, keyed by `(workload_id, client_id, plugin_name)`. The plugin container owns this state so that both trigger-based and manual API ingestion update the same watermarks. This allows:

- Incremental sync: plugin receives `last_sync_watermark` and fetches only newer data.
- Shared state across triggers and jobs for the same workload/client/plugin.
- Auditing (e.g. `last_ingestion_at`, `total_records_ingested`).

For `data_threshold` triggers, the executor can query how many records were ingested in a time window (e.g. from Elasticsearch or from ingestion metadata) to decide if enough new data exists for training.

---

## File Structure Summary

- **DFW**: `src/lib/ingestion/` (models, plugin_client), `src/tasks/ingestion/service.py`, `src/api/triggers/` (trigger model and API).
- **Plugin container**: `ingestion-plugins/src/lib/` (base, registry, state, loader, job_store, es_client), `ingestion-plugins/src/api/`, `ingestion-plugins/plugins/<name>/`.

---

## Add a New Plugin

1. Create `ingestion-plugins/plugins/<plugin_name>/` with `__init__.py` and `plugin.py` (class inheriting `IngestionPlugin`, with `name`, `validate_config`, `ingest`).
2. Add `<plugin_name>` to the `enabled` list in `ingestion-plugins/plugin.yaml`.
3. Rebuild/restart the plugin container. No changes are required in the main DFW API or triggers; use the same `ingestion_config` with `plugin_name: "<plugin_name>"` and the appropriate `dataset_type` for the trigger’s `job_type`.

---

## Three Ways to Use Plugins

### 1. Manual: direct POST to the plugin service

Call the plugin container’s `/execute` endpoint directly (e.g. for ad‑hoc syncs or testing). DFW is not involved; you manage watermarks and downstream use of the data yourself.

```bash
curl -X POST http://localhost:8001/execute \
  -H "Content-Type: application/json" \
  -d '{
    "plugin_name": "bugnemo",
    "context": {"workload_id": "my_workload", "client_id": "my_client", "config": {...}},
    "job_type": "embedding_job",
    "dataset_type": "bug_pairs"
  }'
```

Poll `GET /jobs/{job_id}` for status; optionally `DELETE /jobs/{job_id}` to clean up.

### 2. As part of a DFW job

Run ingestion **before** the job pipeline so the job uses freshly synced data. Supply config either **per run** in the API request (overrides job config) or **in the job’s config file** (`enabled: true` + `plugin_name`, `dataset_type`, `config`). The job task calls DataIngestionService, then continues with the rest of the pipeline; if ingestion fails, the job continues with existing ES data (warning logged).

**Example — per-run (API request):**

```json
{
  "workload_id": "bugnemo_prod",
  "client_id": "my_client",
  "job_type": "embedding_job",
  "config_name": "embedding_config",
  "ingestion_config": {
    "plugin_name": "bugnemo",
    "dataset_type": "bug_pairs",
    "config": {
      "server": "env:NVBUGS_DBSERVER",
      "database": "env:NVBUGS_DBNAME",
      "username": "env:NVBUGS_DBUSER",
      "password": "env:NVBUGS_DBPWD"
    }
  }
}
```

### 3. As part of a retraining trigger

Enable ingestion on a [Retraining Trigger](../15-retraining-triggers.md) by setting `enable_data_ingestion: true` and providing `ingestion_config` (e.g. `plugin_name`, `config`, optional `batch_size`). The trigger’s `job_type` and `ingestion_config.dataset_type` must match the plugin’s output. When the trigger runs, **TriggerExecutor** calls **DataIngestionService.run_ingestion()**, which uses the plugin container and then updates watermarks. This is the typical way to run continuous ingestion on a schedule or when a condition (e.g. `data_threshold`) is met.

---

## See Also

- [Data Ingestion Plugins (index)](index.md)
- [BugNemo Plugin](bugnemo.md)
- [Deployment (container)](deployment.md)
- [Troubleshooting](troubleshooting.md)
