# Ingestion Plugin Deployment

For production, ingestion plugins run in a **separate containerized service** (API + Celery worker). This provides security isolation (plugin secrets stay out of the main DFW), independent scaling, and the ability to add plugins without rebuilding the main application.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                        DFW Stack                                  │
│                                                                   │
│  Celery Worker:                                                   │
│    DataIngestionService                                           │
│      ├─1─► Call plugin service ──────────────┐                    │
│      │     (IngestionPluginClient)            │                    │
│      ├─2─► Poll until complete               │                    │
│      └─3─► Cleanup job                       │                    │
│                                              │                    │
│  DFW manages:                                 │                    │
│  - IngestionPluginClient (HTTP client)        │                    │
│  - Orchestration (trigger → run → poll)       │                    │
└──────────────────────────────────────────────┼────────────────────┘
                                               │
                                               ▼ HTTP
                                 ┌─────────────────────────────────┐
                                 │    Plugin Container             │
                                 │                                 │
                                 │  FastAPI API + Celery Worker    │
                                 │    GET  /health                 │
                                 │    GET  /plugins                │
                                 │    POST /execute                │
                                 │    GET  /jobs/{id}              │
                                 │    DELETE /jobs/{id}            │
                                 │                                 │
                                 │  IngestionStateManager          │
                                 │    └─ watermarks (MongoDB)      │
                                 │  DataLoader → Elasticsearch     │
                                 │  Job tracking → MongoDB         │
                                 │  PluginRegistry (plugins/)      │
                                 │    └─ bugnemo, ...               │
                                 └─────────────────────────────────┘
```

---

## Data Flow

1. Trigger fires (Celery Beat) or job starts.
2. **DataIngestionService.run_ingestion()** (DFW): **POST** to plugin service `/execute` with `plugin_name`, context, `job_type`, `dataset_type` → receives `job_id`, status `queued`.
3. **Plugin container**: Resolves watermark from MongoDB, Celery worker runs `plugin.ingest(context)` and **DataLoader** streams records in batches to Elasticsearch; worker updates watermark after completion.
4. DFW polls **GET /jobs/{job_id}** until status is `completed` or `failed`.
5. Optionally **DELETE /jobs/{job_id}** for cleanup.

---

## Docker Compose

The `deploy/docker-compose.yaml` defines two services for ingestion:

| Service | Role |
|---------|------|
| **ingestion-plugins-api** | FastAPI on port 8001; health, list plugins, execute, job status, delete job. |
| **ingestion-plugins-worker** | Celery worker on queue `ingestion_plugins`; runs plugins and loads data to Elasticsearch. |

The main DFW **celery_worker** (and parent worker) must have `PLUGIN_SERVICE_URL` set so **IngestionPluginClient** can reach the plugin API (e.g. `http://localhost:8001` for Compose).

Example env for **ingestion-plugins-api**:

- `ELASTICSEARCH_URL`, `REDIS_URL`, `MONGODB_URL`, `MONGODB_DB=ingestion_plugins`

Example env for **ingestion-plugins-worker** (includes plugin-specific secrets):

- Same as API, plus `ES_COLLECTION_NAME`, and for BugNemo: `NVBUGS_DBSERVER`, `NVBUGS_DBUSER`, `NVBUGS_DBPWD`, `NVBUGS_DBNAME`.

---

## Environment Variables

**DFW Celery worker**

| Variable | Description |
|----------|-------------|
| `PLUGIN_SERVICE_URL` | Base URL of the plugin service (e.g. `http://localhost:8001`). Required for ingestion. |

**Plugin container (API and worker)**

| Variable | Description |
|----------|-------------|
| `REDIS_URL` | Redis (Celery broker). |
| `ELASTICSEARCH_URL` | Elasticsearch for DataLoader. |
| `ES_COLLECTION_NAME` | ES collection name (worker; default from env or `flywheel`). |
| `MONGODB_URL` | MongoDB for job tracking. |
| `MONGODB_DB` | Database name (default: `ingestion_plugins`). |

**Plugin-specific (worker only)**  
For BugNemo, see [BugNemo Plugin](bugnemo.md). Other plugins may require their own env vars.

---

## Helm (Kubernetes)

The Helm chart deploys the same pattern: an ingestion API service and an ingestion worker deployment. Set `PLUGIN_SERVICE_URL` on the main DFW Celery workers to the ingestion API service URL (e.g. `http://df-ingestion-api-service:8001`). Values and image names (e.g. `dfw-ingestion-plugins`) are in `deploy/helm/data-flywheel/values.yaml` and related templates.

---

## Responsibilities

| Responsibility | DFW | Plugin Container |
|----------------|-----|-------------------|
| Watermark management | ❌ | ✅ |
| Elasticsearch indexing (DataLoader) | ❌ | ✅ |
| Job tracking (MongoDB) | ❌ | ✅ |
| Plugin discovery & execution | ❌ | ✅ |
| Plugin secrets (env vars) | ❌ | ✅ (worker only) |
| Orchestration (trigger → run → poll → update state) | ✅ | ❌ |

---

## Job Tracking (MongoDB)

Jobs are stored in the plugin MongoDB database (e.g. `ingestion_plugins.jobs`):

- **Deduplication**: At most one active job per `(plugin_name, workload_id, client_id)`; `/execute` returns the existing job if one is already queued or running.
- **Lifecycle**: `queued` → `running` → `completed` or `failed`.
- **Cleanup**: `DELETE /jobs/{job_id}` removes the job from Celery result backend and from MongoDB.

---

## Test the Container Setup

```bash
cd deploy
docker-compose up -d

# Health
curl http://localhost:8001/health
# Expected: {"status": "healthy", "plugins_loaded": 1, "active_jobs": 0, "redis_connected": true}

# List plugins
curl http://localhost:8001/plugins
# Expected: [{"name": "bugnemo", "description": "...", "class_name": "BugNemoPlugin", "module": "..."}]

# Execute (returns 202 with job_id)
curl -X POST http://localhost:8001/execute \
  -H "Content-Type: application/json" \
  -d '{"plugin_name": "bugnemo", "context": {"workload_id": "test", "client_id": "test", "config": {}}, "job_type": "embedding_job", "dataset_type": "bug_pairs"}'

# Job status
curl http://localhost:8001/jobs/<JOB_ID>

# Cleanup
curl -X DELETE http://localhost:8001/jobs/<JOB_ID>

# Logs
docker-compose logs -f ingestion-plugins-worker
```

---

## Migration from Local to Container

1. Build the ingestion-plugins image (or use the chart’s image).
2. Deploy the ingestion API and worker (Compose or Helm).
3. Set `PLUGIN_SERVICE_URL` on DFW Celery workers.
4. Configure plugin-specific env vars on the **ingestion-plugins-worker** (e.g. BugNemo DB credentials).
5. Triggers and jobs that use `enable_data_ingestion` and `ingestion_config` will then use the container via **IngestionPluginClient**.

---

## See Also

- [Architecture](architecture.md) — code map and data flow
- [BugNemo Plugin](bugnemo.md) — env vars and config
- [Troubleshooting](troubleshooting.md)
