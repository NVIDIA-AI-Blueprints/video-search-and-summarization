# VSS BEHAVIOR ANALYTICS STREAM
Python vss behavior analytics streaming pipeline for spatial AI analytics, Smart City and more.

## Documentation Map
- Installation & usage: `readmes/installation-and-usage.md`
- Docker & deployment: `readmes/docker.md`
- Configuration guide: `readmes/configuration.md`
- Testing guide: `readmes/testing.md`
- Troubleshooting & logs: `readmes/troubleshooting.md`
- Modules overview: `readmes/modules-overview.md`
- Building an analytics app: `readmes/building-mdx-analytics-app.md`
- Incident detection reference: `readmes/incident-detection.md`
- Dev example walkthrough: `readmes/cursor-dev-example-walkthrough.md`
- Config defaults and helpers: `src/mdx/analytics/core/schema/config.py`

## Quick Start
```bash
git clone <repository-url>
cd behavior-analytics
python3 -m pip install --upgrade pip pipenv setuptools wheel
pipenv install --dev
pipenv run python3 apps/playback/playback_frames.py
```

## Installation
See `readmes/installation-and-usage.md` for prerequisites, install modes, and running apps.

## Run Applications
- Quick commands: `pipenv run python3 apps/<name>/main_<name>_app.py [--config <path>]`
- Details and app list: `readmes/installation-and-usage.md`

## Docker
Short commands:
- Build: `docker build -t py-analytics -f docker/Dockerfile .`
- Run playback: `docker run --network=host py-analytics python3 apps/playback/playback_frames.py`
- Run with config: mount into `/behavior-analytics/config.json` (matches the image WORKDIR)

More details: `readmes/docker.md`

## Configuration
- Defaults/helpers: `src/mdx/analytics/core/schema/config.py`
- Examples: `configs/`
- Incident details: `readmes/incident-detection.md`
- Full guide: `readmes/configuration.md`

## Testing
- Unit: `pipenv run pytest tests/unit/`
- Coverage: `pipenv run coverage run -m pytest tests/unit/ && pipenv run coverage report`
- Integration (Docker compose): `cd tests/integration && ./test.sh [profile] [stream] [mode]`
- More details: `readmes/testing.md`

## Troubleshooting & Logs
- Logging config example: `configs/logging_config.json`
- Latency monitor: `src/mdx/analytics/core/tools/latency/latency_monitor.py`
- More tips: `readmes/troubleshooting.md`

## Where to go next
- Build your own app: `readmes/building-mdx-analytics-app.md`
- Understand incidents: `readmes/incident-detection.md`
- See the dev example: `readmes/cursor-dev-example-walkthrough.md`
