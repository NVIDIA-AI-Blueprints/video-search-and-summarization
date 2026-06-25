# test_alert_config_es_hydration

Acceptance test that proves alert verification configs survive Redis data loss by being
rehydrated from Elasticsearch — both on cache-miss reads and on service
startup.

## What it covers

1. **Durable write path** — `POST /api/v1/verification/config` lands in
   Elasticsearch (`ab-alert_configs` index), not just Redis.
2. **Read-through cache** — after `DEL alert_config:<id>` the next GET
   still returns the record (via the ES fallback) and silently refills
   the Redis cache.
3. **Startup hydration** — with the Redis key wiped, restarting Alert
   Bridge repopulates the cache from ES before the service answers
   requests.

## Running against a shared deployment

Your deployment already provides Kafka, Redis, and Elasticsearch and a
*deployed* Alert Bridge that isn't built from this source. To test
**this source's** changes without disturbing the deployed AB, the script
starts its own AB process on a separate port (`9088` by default) and
points it at the same Redis/Kafka/ES.

Make the run stable by doing all of the following:

### 1. Use dedicated test infrastructure where possible

If you can run this against a staging cluster (same Redis/ES/Kafka but
no customer traffic), do that. If you must run against prod-like shared
infra, accept that:

- The test writes to the `ab-alert_configs` ES index with a unique
  `hydration_<epoch>_<pid>` ID per run.
- The test deletes only its own keys in cleanup — no `FLUSHDB`, no
  blanket ES wipe. Safe to run alongside the deployed AB.
- A run that crashes mid-way still cleans up via `trap cleanup EXIT`.

### 2. Start the test-owned Alert Bridge on a free port

The script exports `FASTAPI_PORT=$AB_PORT` (default `9088`) so the
deployed AB on `9080` keeps serving real traffic. Override if 9088 is
taken:

```bash
AB_PORT=9189 bash test/functional/p1/test_alert_config_es_hydration/run.sh
```

The test AB shares Redis/Kafka/ES with the deployed AB. If that worries
you, run the test AB in a container with `--network host` but disable
Kafka consumption so your test AB doesn't steal incidents from the
deployed one. Quick way: override `event_bridge.kafka_source.group_id`
in a custom config so both instances coexist on Kafka without
duplicate delivery. (Preview: the base test config already uses
`alert-bridge-vlm-group-p1`, not the deployed group id — check your
deployment's config to confirm.)

### 3. Point the test at your deployment's infrastructure

All addresses are env-overridable:

```bash
REDIS_HOST=my-redis.internal REDIS_PORT=6379 \
ES_HOST=http://es.internal:9200 \
BASE_CONFIG=/path/to/my_test_config.yaml \
bash test/functional/p1/test_alert_config_es_hydration/run.sh
```

`BASE_CONFIG` must have `persistence.enabled: true` (already the default
in `persistence/config.py`) and `elastic.hosts` pointing at the same ES
the test queries directly.

### 4. Avoid collisions with concurrent runs

Each run uses a unique `RUN_ID` (`hydration_<epoch>_<pid>`). Multiple
developers can run the test against the same cluster simultaneously
without stepping on each other's data, as long as cleanup runs (it does,
via `trap`).

### 5. Pre-flight your cluster

The test exits code 2 (fatal setup) and does not attempt writes if:

- ES `/` returns non-200 at startup.
- Redis at `$REDIS_HOST:$REDIS_PORT` isn't reachable.

It still cleans up whatever it managed to create. If you see exit 2,
fix infrastructure before rerunning.

### 6. Read the logs on failure

On non-zero exit the trap tails the last 40 lines of
`$PID_DIR/alert_bridge.log` (default `/tmp/alert_agent_p1_functional/`).
Common early failures:

- `Persistence layer enabled but Elasticsearch is unreachable` — the
  `fail-fast` branch of `_build_store()` in
  `alert-agent-web/app/api/alert_config_routes.py`. Check that your
  `BASE_CONFIG` points `elastic.hosts` at a reachable ES.
- `AB never became healthy` — AB crashed during startup. Check the log
  for stack traces.

## Environment variables

| Variable      | Default                             | Purpose                                   |
|---------------|-------------------------------------|-------------------------------------------|
| `AB_PORT`     | `9088`                              | Port for the test-owned Alert Bridge      |
| `AB_HOST`     | `http://localhost:$AB_PORT`         | Base URL for API calls                    |
| `REDIS_HOST`  | `127.0.0.1`                         | Redis host                                |
| `REDIS_PORT`  | `6379`                              | Redis port                                |
| `ES_HOST`     | `http://127.0.0.1:9200`             | Elasticsearch URL                         |
| `BASE_CONFIG` | `../shared/config_base.yaml`        | `config.yaml` handed to the test AB       |
| `PID_DIR`     | `/tmp/alert_agent_p1_functional`    | Scratch dir for pid + logs                |
| `RUN_ID`      | `hydration_<epoch>_<pid>`           | Unique suffix for the test `alert_type`   |

## What success looks like

```
⏳ Checking prerequisites
⏳ Starting test-owned Alert Bridge on port 9088
✓ Alert Bridge running (PID 12345)
⏳ POST http://localhost:9088/api/v1/verification/config with distinctive vlm_params
✓ Config created
✓ ES durable copy confirmed
✓ Redis cache populated
⏳ Scenario A — DEL Redis key then GET, expect data from ES
✓ Cache miss fell through to ES and returned correct data
✓ Redis cache refilled on miss
⏳ Scenario B — wipe Redis key, restart AB, verify hydration refills cache
✓ Alert Bridge running (PID 12456)
✓ Hydration restored the cache from ES on startup
✓ PASS: ES hydration + read-through cache honoured alert-config ES hydration semantics
```
