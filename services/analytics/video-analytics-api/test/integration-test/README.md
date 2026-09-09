# Web-API Integration Tests

Integration tests for video-analytics-api using Docker Compose. The stack runs **Elasticsearch** and the **video-analytics-api** image, then runs HTTP assertions against the API.

Elasticsearch is not defined here. `docker_compose/infra/video-analytics-api-infra.yml` includes the deployment's own
`deploy/docker/services/infra/compose.yml` and selects the `elasticsearch` and `elasticsearch-init-container` services
through `COMPOSE_PROFILES`, so the suite exercises the same Elasticsearch version, config, index templates and ingest
pipeline that a real deployment serves. Everything else that file defines — Kafka, Redis, logstash, Kibana, HAProxy —
stays defined but unstarted. Because the services come from the deployment, this suite **cannot run alongside a
deployment stack on the same host**: they collide on the `elasticsearch` container name and on port 9200.

## Prerequisites

- Docker and Docker Compose
- **Node.js** and `npm install` in the repo’s **test/** directory (required; integration tests run via Node only, no curl)
- Enough resources for Elasticsearch (e.g. 512MB heap)

## Layout

- **`docker_compose/`**
  - **`infra/`** – includes the deployment's infra compose; holds the generated `.env` (gitignored)
  - **`apps/`** – video-analytics-api service and integration config
- **`scripts/`** – `run_integration_tests.sh` (invokes Node), `run_integration_tests.js` (all HTTP checks and validate-then-upload via Node)
- **`generate_env.sh`** – generates `docker_compose/infra/.env`
- **`cleanup.sh`** – brings down Compose and removes this project's volumes
- **`test.sh`** – main integration test (build, up, wait, run tests, cleanup)
- **`test_all.sh`** – runs all integration test profiles (currently single profile)

## Usage

From the **video-analytics-api** repo, run from the integration-test directory:

```bash
cd video-analytics-api/test/integration-test
chmod +x test.sh test_all.sh cleanup.sh generate_env.sh scripts/run_integration_tests.sh
./test.sh [mode]
```

- **`mode`**: `dev` (default) – no cleanup on failure; `prod` – cleanup even on failure.
- **`./test_all.sh [mode]`** – runs the full integration suite (same as `test.sh` for now).

Manual steps:

```bash
# Generate env and start stack
source generate_env.sh
. docker_compose/infra/.env
cd docker_compose
docker compose -f infra/video-analytics-api-infra.yml -f apps/video-analytics-api-app.yml up -d

# After services are up, run HTTP tests
../scripts/run_integration_tests.sh http://localhost:8081

# Cleanup
docker compose -f infra/video-analytics-api-infra.yml -f apps/video-analytics-api-app.yml down --volumes
# Or from integration-test dir: ./cleanup.sh
```

## What the test does

1. **Build** – `docker build -t video-analytics-api:integration-test -f docker/Dockerfile .` from video-analytics-api root, using `docker/Dockerfile.dockerignore`.
2. **Compose up** – Starts the deployment's Elasticsearch on the Compose bridge network, runs
   `elasticsearch-init-container` to completion (ILM policies, index templates, the `insertion-timestamp-pipeline`
   ingest pipeline), then starts video-analytics-api. The app reaches ES at `http://elasticsearch:9200`; the harness
   reaches it on the published port 9200.
3. **Wait** – Waits for ES health, verifies the ingest pipeline landed, and waits for `GET /livez` to return 200.
4. **Assert** – Runs `scripts/run_integration_tests.sh`:
   - `GET /livez` → 200
   - **Config uploads (with schema check at request time)** – For each of calibration, road-network, usd-assets: the fixture is validated against the AJV schema in **web-api-core/schemas/ajv/**, then the same payload is immediately POSTed to `POST /config/upload-file/{docType}`. If validation fails, the request is not sent.
   - `GET /config/calibration`, `/config/calibration/last-modified-timestamp`, `/config/road-network`, `/config/usd-assets` → 200
5. **Cleanup** – `docker compose down --volumes` and volume prune.

## Environment variables

Generated in `docker_compose/infra/.env` by `generate_env.sh`:

- `INTEGRATION_TEST_DIR` – path to the integration-test directory
- `VIDEO_ANALYTICS_API_ROOT` – path to video-analytics-api repo root (where the Dockerfile is)
- `DATA_DIR` – `docker_compose/apps_data`
- `COMPOSE_PROJECT_NAME` – `video-analytics-api-integration`

Exported by `scripts/run_integration_tests.sh` so the controller suites know which
deployment profile they run against — the stack loads the warehouse 2D dump, so they
default to that profile and profile-gated cases (such as the VLM verified alert types
check on `/incidents`) run. Set them beforehand to exercise a different profile:

- `COMPOSE_PROFILE` – `bp_wh_2d`
- `BP_PROFILE` – `bp_wh`
- `DEPLOY_PROFILE` – `COMPOSE_PROFILES_WH_2D`

Optional overrides:

- `BUILD_TIMEOUT` – Docker build timeout in seconds (default 600).
- `COMPOSE_TIMEOUT` – Compose up timeout in seconds (default 300).

## Troubleshooting

**Elasticsearch container exits or is unhealthy**  
On Linux, Elasticsearch may require a higher `vm.max_map_count`:

```bash
sudo sysctl -w vm.max_map_count=262144
```

To make it persistent: add `vm.max_map_count=262144` to `/etc/sysctl.conf` (or a file under `/etc/sysctl.d/`) and run `sudo sysctl -p`.

## CI

To run in CI, use `mode=prod` and set timeouts if needed:

```bash
export BUILD_TIMEOUT=900 COMPOSE_TIMEOUT=600
./test.sh prod
```
