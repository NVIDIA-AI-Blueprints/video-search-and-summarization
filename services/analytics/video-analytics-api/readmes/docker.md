# Docker and Deployment

## Build
```bash
docker build -t video-analytics-api -f docker/Dockerfile .
```

The build uses `docker/Dockerfile.dockerignore` for Dockerfile-specific context exclusions.

## Run (host network)
```bash
docker run --network=host video-analytics-api
```

## Run with custom config
```bash
docker run --network=host \
  -v /path/to/config.json:/resources/config.json:ro \
  video-analytics-api node index.js --config /resources/config.json
```
> The image's WORKDIR is `/web-api-app`; mount paths and CLI args must line up.

## Pre-built image example
```bash
docker run --network=host \
  nvcr.io/nv-metropolis-dev/metropolis-analytic/vss-video-analytics-api:3.2
```

## Notes
- `--network=host` is for local Elasticsearch/Kafka; adjust/remove if using remote services.
- Kafka is optional; the server starts without it if no brokers are configured.
- The default bootstrap config is baked into the image at `/configs/default-configs/config.json`.
- For integration test compose setup, see `test/integration-test/docker_compose/`.
