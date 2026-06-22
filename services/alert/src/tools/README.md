# tools/

One-off operational scripts. Nothing in here is imported by the
request-handling path; each script is meant to be invoked explicitly by
an operator or during deploy.

## migrate_alert_config_redis_to_es.py

Seeds Elasticsearch with alert verification configs that are currently
live in Redis only. Run once, immediately after the first deploy;
subsequent deployments do not need it.

```bash
# Inside the alert_agent container (or anywhere the venv + config.yaml exist):
python -m tools.migrate_alert_config_redis_to_es --config config.yaml

# Preview the records that would be migrated without writing to ES:
python -m tools.migrate_alert_config_redis_to_es --dry-run
```

Safety properties:

- **Idempotent.** Uses `set_if_absent`, so re-running never overwrites
  an existing ES document.
- **Read-only against Redis.** No keys are deleted or modified.
- **Exits non-zero** (code 1) when at least one record failed to
  migrate, and code 2 on fatal setup errors (ES unreachable, bad
  config). Wire this into your deploy pipeline.

Prefer running during a maintenance window if possible. The script is
safe to run against a live service, but a concurrent `PUT
/api/v1/verification/config` during migration could land in Redis only
and never reach ES (the service's pre-hydration wiring writes to ES
first, but the migration itself skips records that are mid-PUT in
Redis at the moment the key is scanned).
