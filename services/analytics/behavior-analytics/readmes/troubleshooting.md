> Part of behavior-analytics docs. See `../README.md` for the overview.
# Troubleshooting & Logs

## Logging
- Example config: `configs/logging_config.json`
- Adjust levels/handlers there; load with `logging.config.dictConfig`.
- Common tweak: set module logger to DEBUG for noise-sensitive debugging.

## Latency monitor
- Script: `src/mdx/analytics/core/tools/latency/latency_monitor.py`
- Kafka example:
```bash
pipenv run python src/mdx/analytics/core/tools/latency/latency_monitor.py \
  --source-type kafka --host localhost --port 9092 --topics raw frames behavior \
  --percentiles 50 75 90 95 99
```
- Redis example:
```bash
pipenv run python src/mdx/analytics/core/tools/latency/latency_monitor.py \
  --source-type redisStream --host localhost --port 6379
```

## Common issues
- No incidents: verify incident configs enabled; see `readmes/incident-detection.md`.
- Incidents stay off unless `...IncidentEnable` flags are set (defaults are false per docs).
- Too many false incidents: raise thresholds or expiration windows.
- Playback not emitting: check topic names in config and broker connectivity.
- Docker networking: ensure `--network=host` when using local Kafka/Redis/MQTT.
- Startup failures: many apps need both `--config` and `--calibration <path>`.

## Where to look
- Config defaults/helpers: `src/mdx/analytics/core/schema/config.py`
- Sample configs: `configs/`
- App-specific logic: `apps/` entrypoints and `src/mdx/analytics/core` modules.
