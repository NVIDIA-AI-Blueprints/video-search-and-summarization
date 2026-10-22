# Data Ingestion Plugins

Continuously ingest data from external sources (bug trackers, support systems, APIs) into the Data Flywheel. Data is transformed into the required format and indexed into Elasticsearch for training and evaluation.

The feature uses a **modular, plugin-based architecture** orchestrated by [Retraining Triggers](../15-retraining-triggers.md). A separate **plugin container** runs connectors (with optional **file-system auto-discovery**), so you can add new plugins without changing the core platform.

## Supported Plugins

| Plugin | Status | Guide |
|--------|--------|-------|
| **BugNemo** | **Functional** | [BugNemo Plugin](bugnemo.md) |

## Quick Start

### 1. Configure a trigger with ingestion

Create a retraining trigger that runs data ingestion before evaluation (see [Retraining Triggers](../15-retraining-triggers.md)):

```bash
curl -X POST http://localhost:8000/api/triggers \
  -H "Content-Type: application/json" \
  -d '{
    "name": "BugNemo Hourly Sync",
    "workload_id": "bugnemo_prod",
    "client_id": "nvidia_internal",
    "job_type": "embedding_job",
    "dataset_type": "bug_pairs",
    "trigger_type": "schedule",
    "eval_schedule": "0 * * * *",
    "config_name": "bugnemo_embedding_config",
    "enable_data_ingestion": true,
    "ingestion_config": {
      "plugin_name": "bugnemo",
      "batch_size": 200,
      "config": {
        "server": "env:NVBUGS_DBSERVER",
        "database": "env:NVBUGS_DBNAME",
        "username": "env:NVBUGS_DBUSER",
        "password": "env:NVBUGS_DBPWD",
        "max_pairs": 5000
      }
    }
  }'
```

Use `env:VAR` in config for secrets; they are resolved at runtime from the **plugin container** environment.

### 2. Deploy the plugin container

Ingestion runs in a separate service. See [Deployment](deployment.md) for Docker Compose and Helm setup. Set `PLUGIN_SERVICE_URL` on the DFW Celery worker so it can call the plugin API.

### 3. Verify

- Health: `curl http://localhost:8001/health` — expected: `{"status": "healthy", "plugins_loaded": 1, ...}`
- List plugins: `curl http://localhost:8001/plugins` — expected: `[{"name": "bugnemo", ...}]`
- Trigger executions show ingestion stats in trigger execution history.

---

## Further Reading

| Topic | Link |
|-------|------|
| BugNemo — config, DB tables, env vars, output format | [BugNemo Plugin](bugnemo.md) |
| Plugin system, data flow, state, code map | [Architecture](architecture.md) |
| Container deployment — Docker, Helm, env vars, job tracking | [Deployment](deployment.md) |
| Security, secrets, errors, data volume | [Troubleshooting](troubleshooting.md) |
