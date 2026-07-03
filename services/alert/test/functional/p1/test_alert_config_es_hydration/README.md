# test_alert_config_es_hydration

Acceptance test that proves alert verification configs survive an Alert
Bridge restart by being rehydrated from Elasticsearch.

> Redis has been removed from Alert MS. The alert-config cache is now an
> in-process store and **Elasticsearch is the durable source of truth**.
> This test therefore no longer touches Redis — it validates ES durability
> and restart hydration only.

## What it covers

1. **Durable write path** — `POST /api/v1/verification/config` lands in
   Elasticsearch (`ab-alert_configs` index).
2. **Read-through** — a GET returns the record served from ES (the default
   in-process cache is read-through, so the pipeline always sees fresh data).
3. **Startup hydration** — after restarting Alert Bridge (the in-process
   cache is empty), the config re-appears from ES during hydration before
   the service answers requests.

## Running against a shared deployment

Your deployment already provides Kafka and Elasticsearch and a *deployed*
Alert Bridge that isn't built from this source. To test **this source's**
changes without disturbing the deployed AB, the script starts its own AB
process on a separate port (`9088` by default) and points it at the same
Kafka/ES.

Make the run stable by doing all of the following:

### 1. Use dedicated test infrastructure where possible

If you can run this against a staging cluster (same ES/Kafka but no
customer traffic), do that. If you must run against prod-like shared
infra, accept that:

- The test writes to the `ab-alert_configs` ES index with a unique
  `hydration_<epoch>_<pid>` ID per run.
- The test deletes only its own keys in cleanup — no blanket ES wipe.
  Safe to run alongside the deployed AB.
- A run that crashes mid-way still cleans up via `trap cleanup EXIT`.

### 2. Start the test-owned Alert Bridge on a free port

The script exports `FASTAPI_PORT=$AB_PORT` (default `9088`) so the
deployed AB on `9080` keeps serving real traffic. Override if 9088 is
taken:

```bash
AB_PORT=9189 bash test/functional/p1/test_alert_config_es_hydration/run.sh
```

### 3. Point the test at your deployment's infrastructure

All addresses are env-overridable:

```bash
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

The test exits code 2 (fatal setup) and does not attempt writes if ES `/`
returns non-200 at startup. No Redis is required.

### 6. Read the logs on failure

On non-zero exit the trap tails the last 40 lines of
`$PID_DIR/alert_bridge.log` (default `/tmp/alert_agent_p1_functional/`).
Common early failures:

- `Persistence layer enabled but Elasticsearch is unreachable` — the
  fail-fast branch of the alert-config factory. Check that your
  `BASE_CONFIG` points `elastic.hosts` at a reachable ES.
- `AB never became healthy` — AB crashed during startup. Check the log
  for stack traces.

## Environment variables

| Variable      | Default                             | Purpose                                   |
|---------------|-------------------------------------|-------------------------------------------|
| `AB_PORT`     | `9088`                              | Port for the test-owned Alert Bridge      |
| `AB_HOST`     | `http://localhost:$AB_PORT`         | Base URL for API calls                    |
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
⏳ Scenario A — GET returns data served from ES
✓ GET returned correct data from ES
⏳ Scenario B — restart AB (in-process cache empty), verify hydration from ES
✓ Config survived restart — hydrated from ES
✓ PASS: ES durability + restart hydration honoured (no Redis)
```
