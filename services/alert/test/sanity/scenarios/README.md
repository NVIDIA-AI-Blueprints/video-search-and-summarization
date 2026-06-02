# Sanity Drops — Agent Guide

A sanity drop injects a synthetic incident, waits, runs a probe, and records PASS/FAIL. Each `.case` file under `cases/` is one drop. The runner walks them sequentially.

Every drop scopes to a `RUN_ID`-suffixed synthetic sensorId so probes never collide with the deployment's natural traffic.

## Run

```bash
# All drops
ES_HOST=<host> ./runner.sh

# One drop
ES_HOST=<host> ./runner.sh cases/<name>.case

# JSON output
ES_HOST=<host> ./runner.sh --json
```

Common env knobs:

| Var | Default | Purpose |
|---|---|---|
| `ES_HOST` / `ES_PORT` | localhost / 9200 | Elasticsearch endpoint |
| `AB_HOST` / `AB_PORT` | localhost / 9080 | Alert Bridge REST endpoint |
| `BOOTSTRAP` | 127.0.0.1:9092 | Kafka bootstrap (for Kafka-inject drops) |
| `CATEGORY` | (unset → keep payload's) | Override `category` in injected payload to one the deployment recognizes (must match a configured `alert_type` in `alert_type_config.json`) |
| `SANITY_ALLOW_RESTART` | 0 | Set `1` to permit drops whose `apply()` restarts AB |
| `SANITY_PER_CASE_TIMEOUT` | 300 | Per-case wall-clock timeout |

## Two modes

### Probe-only (default)
Set only `ES_HOST` (and `METRICS_HOST` if probing Prometheus). The runner skips the apply phase and probes whatever AB is already deployed. Use when the deployment is pre-configured.

### Full lifecycle
Also set:

| Var | Purpose |
|---|---|
| `AB_COMPOSE_FILE` | Path to the docker-compose.yml that owns `alert-bridge` |
| `AB_CONFIG_HOST_DIR` | Host dir mounted into AB at the location where `alert_type_config.json` lives |
| `AB_HOST`, `AB_PORT` | AB health endpoint (default `localhost:9080`) |

When set, `apply()` in each case copies fixtures, restarts the container, and polls for ready before the probe runs.

## Layout

```
scenarios/
  runner.sh             # sequential walker
  lib/
    runner_helpers.sh   # RUN_ID, state dirs, sensor-id factory, restart gate
    inject.sh           # inject_kafka_incident / inject_rest_incident / replay_last_kafka_inject
  cases/                # one .case per drop
  probes/               # PASS/FAIL assertion scripts
  fixtures/             # config snapshots used by apply()
```

## `.case` file contract

A `.case` is bash. It declares:

| Var / function | Required | Purpose |
|---|---|---|
| `PROBE` | yes | Path to probe script (relative to scenarios/) |
| `PROBE_ENV_*` | yes | Each var becomes an env arg to the probe (prefix stripped) |
| `WAIT_TRAFFIC_SECONDS` | no | Sleep before probe (default 0) |
| `apply()` | no | Function that mutates AB state. Returns 0 on success, 1 on failure |

If `apply()` is defined, the runner calls it before the probe. If the function detects required env vars are missing (probe-only mode), it should `return 0` cleanly so the probe still runs.

## Probe contract

Any bash script that:

1. Reads its parameters from env vars (set by the runner from `PROBE_ENV_*`)
2. Does whatever it needs (ES query, HTTP scrape, redis-cli, log grep, etc.)
3. Calls `pass "$NAME" "$DETAIL"` or `fail "$NAME" "$DETAIL"` from `lib/common.sh`
4. Exits 0 (PASS) or 1 (FAIL)

Beyond those four points, every probe is free-form code.

## Adding a sanity drop

1. Add a `.case` file in `cases/`
2. If a new kind of verification is needed, add a probe in `probes/`. Otherwise reuse an existing probe with different `PROBE_ENV_*`.
3. If apply needs a new config snapshot, add it under `fixtures/`.

Runner code does not change.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Probe fails: 0 docs in window | No matching traffic in lookback, or expected value doesn't match what the deployment produces |
| Probe skipped: endpoint unreachable | The feature isn't enabled on the deployment, or wrong host/port |
| Apply phase fails: AB not ready | `AB_HOST:AB_PORT/health` didn't respond in 60s — check container logs |
| All drops PASS but you expected FAIL | `WAIT_TRAFFIC_SECONDS` too short — bump it so traffic has time to flow through |
