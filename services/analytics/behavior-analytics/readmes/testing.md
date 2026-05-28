> Part of behavior-analytics docs. See `../README.md` for the overview.
# Testing Guide

## Unit tests
```bash
pipenv run pytest tests/unit/
```

### Coverage
```bash
pipenv run coverage run -m pytest tests/unit/
pipenv run coverage report
# Optional: pipenv run coverage html
```

## Integration tests (Docker Compose)
The integration test suite builds a local Docker image, brings up Docker Compose with infra containers and the vss behavior analytics container, plays back scenarios, and checks streamed output against expected data. Scripts live in `tests/integration/`:
- `dev.sh` for a quick manual loop (stand up stack + playback + local app running)
- `test.sh` for the single app
- `test_all.sh` for the full matrix (resource-heavy, ~90 minutes)

### Quick dev loop
```bash
cd tests/integration
bash ./dev.sh [datafile] [--playback-from-json]
```
- `dev.sh` starts infra services + playback for manual app testing.
- Once the stack is up, run your app locally to test and debug against playback.

### Test single app
```bash
./test.sh [profile1] [profile2] [mode]
# profile1: warehouse_2d (default) | warehouse_3d | smart_city
# profile2: kafka (default) | redis | mqtt
# mode: dev (default) | prod
```
- Dev mode keeps environment on failure; prod always cleans up.

### Run all profiles
```bash
./test_all.sh [mode]
```
- Runs 6 combinations: warehouse_2d (kafka/redis/mqtt), warehouse_3d (kafka/redis), smart_city (kafka). Takes ~90 minutes.

### Cleanup
If interrupted or hung:
```bash
cd tests/integration
./cleanup.sh [profile1] [profile2]
```

## Tips
- Ensure Docker and docker compose are installed.
- Set permissions once: `sudo chmod -R 777 tests/integration/docker_compose/apps_data`.
- For Smart City JSON playback, pass `--playback-from-json` to `dev.sh`.
