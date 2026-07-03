# tools/

One-off operational scripts. Nothing in here is imported by the
request-handling path; each script is meant to be invoked explicitly by
an operator or during deploy.

There are currently no scripts in this package. The former
`migrate_alert_config_redis_to_es.py` migration was removed together with
the Redis dependency — alert verification configs are stored in
Elasticsearch (the source of truth), so no Redis→ES migration is needed.
