> Part of behavior-analytics docs. See `../README.md` for the overview.
# Installation and Usage

## Prerequisites
- Python 3.13+
- pip and pipenv
- Optional: Anaconda for environment management

## Generating Pipfile.lock
By default, `pipenv lock` locks both production and dev-packages.

To regenerate `Pipfile.lock` with **only production** dependencies (no dev-packages), use the `default` category (the lock file key is `default`, not `packages`):
```bash
pipenv lock --categories default
```

To lock **both** production and dev-packages (e.g. for local development), run:
```bash
pipenv lock
```

## Installation Options
### Standard (recommended)
```bash
python3 -m pip install --upgrade pip pipenv setuptools wheel
pipenv install --dev
```

`vss-behavior-analytics` is declared in `[dev-packages]` of the Pipfile as
`{path = ".", editable = true}`, so `pipenv install --dev` installs it
editable automatically. The package metadata lives in `pyproject.toml` at the
repo root.

### NVIDIA registry
```bash
python3 -m pip install --extra-index-url=https://urm.nvidia.com/artifactory/api/pypi/sw-metropolis-pypi/simple vss-behavior-analytics
```

## Running Applications (pipenv)
```bash
# Playback
pipenv run python3 apps/playback/playback_frames.py --config <path>

# Smart City
pipenv run python3 apps/smart_city/main_smart_city_app.py --config <path>

# Spatial Analytics 2D/3D
pipenv run python3 apps/analytics/main_analytics_2d_app.py --config <path>
pipenv run python3 apps/analytics/main_analytics_3d_app.py --config <path>

# Public Safety
pipenv run python3 apps/public_safety/main_public_safety_app.py --config <path>

# Dev Example (incidents demo)
pipenv run python3 apps/dev_example/main_dev_example_app.py --config <path>
```
> Most apps expect a config JSON; see `configs/` examples or `docs/configuration.md`.
> Apps that use spatial calibration also require `--calibration <path>` (see calibration examples under `configs/`).

## Running Applications (pip)
If installed from the registry, drop `pipenv run`:
```bash
python3 apps/playback/playback_frames.py --config <path>
python3 apps/smart_city/main_smart_city_app.py --config <path>
python3 apps/analytics/main_analytics_2d_app.py --config <path>
python3 apps/analytics/main_analytics_3d_app.py --config <path>
python3 apps/public_safety/main_public_safety_app.py --config <path>
python3 apps/dev_example/main_dev_example_app.py --config <path>
```
> Add `--calibration <path>` for apps that need calibration files (e.g., spatial analytics, dev example).

## Available Applications
- Playback: `apps/playback/playback_frames.py`
- Smart City: `apps/smart_city/main_smart_city_app.py`
- Spatial Analytics 2D/3D: `apps/analytics/main_analytics_2d_app.py` / `_3d_app.py`
- Public Safety: `apps/public_safety/main_public_safety_app.py`
- Dev Example (incidents demo): `apps/dev_example/main_dev_example_app.py`
- Tools: under `src/mdx/analytics/core/tools/`

## Configuration files
- Examples in `configs/` (e.g., `smart_city_config.json`, `warehouse_2d_config.json`, `frame_playback_config.json`).
- Defaults and helpers: `src/mdx/analytics/core/schema/config.py`.
- Incident details: `docs/incident-detection.md`.
