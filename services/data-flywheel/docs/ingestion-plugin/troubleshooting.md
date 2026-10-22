# Ingestion Plugin Troubleshooting

Common issues and best practices when running data ingestion plugins.

---

## Security & Secrets

1. **Do not store passwords in trigger or job config.** Use the `env:VAR` prefix (for example, `"password": "env:NVBUGS_DBPWD"`). The plugin container resolves these at runtime from the **worker** environment. Only the worker needs the secrets; the main DFW never sees them.
2. **Read-only access:** The database user used by the plugin should have **read-only** permissions on the source database.
3. **Plugin container isolation:** Keep plugin credentials in the ingestion-plugins-worker env (or a secrets backend). Do not put them in the main DFW env.

---

## Error Handling

- **Transient errors:** Plugins (for example, BugNemo) may retry DB connections with backoff. After max retries, the job fails and the trigger execution will show the error.
- **Permanent errors:** Auth failure, schema change, or invalid config will fail the job. Check trigger execution history and plugin worker logs (`docker-compose logs ingestion-plugins-worker` or Kubernetes pod logs).
- **Validation:** Config validation runs in the API container; credential checks run on the worker. If the API accepts the request but the worker fails, ensure env vars are set on the **worker** pod/container.

---

## Data Volume and Timeouts

- **Large initial load:** For a very large first sync, consider a manual bootstrap or increase `ingestion_timeout_minutes` on the trigger. Extremely large one-off runs may need to be split (for example, by date range) to avoid timeouts.
- **Batch size:** The trigger's `ingestion_config.batch_size` and plugin options (for example, `max_pairs` for BugNemo) control how much data is processed per run. Tune for your source and timeout limits.

---

## Plugin Not Found or Not Loaded

- **Enabled list:** Plugins are loaded only if listed in the `enabled` list in `ingestion-plugins/plugin.yaml`. Add the plugin name and restart the plugin service.
- **Startup failure:** If the plugin container fails to start, check that each enabled plugin's `validate_environment()` (if implemented) can succeed (for example, required env vars set). Check API/worker logs for tracebacks.

---

## Job Stuck or Duplicate Jobs

- **One active job per (plugin, workload, client):** The plugin service allows only one active job per combination. A new `/execute` for the same combination may return the existing job ID. Wait for it to finish or check status with `GET /jobs/{id}`.
- **Cleanup:** Use `DELETE /jobs/{job_id}` to remove a job from the backend and MongoDB when needed.

---

## Watermark and Incremental Sync

- Watermarks are stored in the **plugin container's** MongoDB (`ingestion_watermarks` collection). If you change the plugin or reset state, delete or update the document for that `(workload_id, client_id, plugin_name)` in the plugin container's database to force a full re-sync from a given date (or rely on the plugin's `default_start_date` for first run).

---

## See Also

- [Deployment](deployment.md) — env vars and container setup
- [BugNemo Plugin](bugnemo.md) — config and env vars
- [Architecture](architecture.md) — data flow and key classes
