> Part of behavior-analytics docs. See `../README.md` for the overview.
# Docker and Deployment

## Build
```bash
docker build -t behavior-analytics -f docker/Dockerfile .
```

## Run with a mounted config (host network)
```bash
docker run --network=host \
  -v /path/to/config.json:/behavior-analytics/config.json \
  behavior-analytics python3 apps/playback/playback_frames.py --config /behavior-analytics/config.json
```
> The image's WORKDIR is `/behavior-analytics`; mount paths and CLI args must line up.

## Pre-built image example
```bash
docker run --network=host \
  nvcr.io/nv-metropolis-dev/metropolis-analytic/vss-behavior-analytics:3.3.0 \
  python3 src/mdx/analytics/core/tools/latency/latency_monitor.py
```

## Notes
- `--network=host` is for local Kafka/Redis/MQTT; adjust/remove if using remote brokers.
- Run other apps similarly: `python3 apps/<name>/main_<name>_app.py --config /behavior-analytics/config.json` (e.g. `apps/analytics/main_analytics_2d_app.py`).
- For MQTT/Redis/Kafka bridges, see integration test compose profiles (`tests/integration/docker_compose/`).
