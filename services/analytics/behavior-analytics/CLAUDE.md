# CLAUDE.md

Agent guide for the `vss-behavior-analytics` repo (aka `behavior-analytics`) — a Python streaming pipeline for spatial AI analytics (warehouse, smartcity, video search, etc.) that consumes frames from Kafka/Redis/MQTT, processes them, and emits behaviors/events/incidents back to the stream.

Read this before editing. For human docs, start at `README.md` → `docs/`.

## Orientation (1-minute read)

- **Library code**: `src/mdx/analytics/core/` — the `mdx.analytics.core` package. Organized by concern: `stream/` (I/O), `transform/` (frame/event logic), `schema/` (Pydantic configs + protobuf), `inference/` (Triton client), `app/` (`BaseApp` lifecycle + scheduler), `tools/` (dev-only utilities), `utils/`, `typings/` (stubs).
- **Apps**: `apps/<name>/main_<name>_app.py` — each app is a single script that subclasses `BaseApp`, composes core pieces in `__init__`, registers processors, and hands off to `app_runner.run(AppClass)` in `__main__`.
- **Configs**: `configs/*.json` — loaded via Pydantic `AppConfig` (see `src/mdx/analytics/core/schema/config.py`).
- **Tests**: `tests/unit/` (mirrors `src/mdx/analytics/core/` layout), `tests/integration/` (Docker Compose driven), `tests/dev/` (exploratory notebooks + scripts).

## Rules to follow when editing

### Always
- **SPDX header** on every new `.py` file (copy from any existing module). Copyright year = current year.
- **Logging**: `logger = logging.getLogger(__name__)` at module top. Never `print()` in library or app code.
- **Type hints**: modern syntax (`str | None`, `list[Foo]`, `dict[str, int]`). Existing code uses Python 3.13+ style.
- **Docstrings**: Sphinx-style (`:param`, `:return`, `:raises`, `:ivar`) — match the surrounding module.
- **Imports**: explicit only. No wildcard imports.
- **Pydantic**: config schema is Pydantic v2 — prefer `model_dump_json(...)`, field validators, `BaseModel` inheritance.
- **Dynamic-config compatibility**: if you store a value derived from `AppConfig.app[*]` or `AppConfig.sensors[*]` into an attribute at `__init__` time (e.g. `self.threshold = config.behavior_water_mark`), runtime config updates won't reach it — operators would need to restart the process to pick up the new value. **Read at use-time** (`self.config.behavior_water_mark` inside method bodies) so `AppConfig.invalidate_caches()` can refresh the cached property automatically. See `docs/dynamic-config.md` for the consumer-tier classification and what's auto-refreshed vs. restart-required.

### Never
- **Don't edit `src/mdx/analytics/core/schema/proto/*_pb2.py` or `*_pb2.pyi`** — generated from `.proto`. If you need schema changes, flag it — regenerating is a separate task.
- **Don't add `print()`** for anything that's not a dev tool (`src/mdx/analytics/core/tools/` is the only exception).
- **Don't catch bare `except:`** — catch specific exceptions, or `Exception as e` and log.
- **Don't drop SPDX headers** when rewriting a file.
- **Don't introduce wildcard imports** or barrel `__all__` exports — the codebase uses explicit imports.

### House style
- **Line length: 120 chars.** Configured in `pyproject.toml` under `[tool.ruff]` and `[tool.black]`. Don't pre-wrap shorter than necessary — let lines run to 120 before breaking. Format with `pipenv run ruff format <path>` and lint with `pipenv run ruff check <path>` if you want to verify.
- Module-level constants in `UPPER_SNAKE_CASE`. Classes `PascalCase`. Functions/vars `snake_case`.
- Exception: `src/mdx/analytics/core/utils/anomaly_util.py` uses `camelCase` for historical reasons — leave it alone, don't match it in new files.
- One logger per module. No global loggers.
- Prefer composition over inheritance. `BaseApp` is the one deliberate ABC.

## Common commands

```bash
# Install (dev)
python3 -m pip install --upgrade pip pipenv setuptools wheel
pipenv install --dev          # [packages] + [dev-packages]; the editable
                              # `vss-behavior-analytics` is declared in [dev-packages]

# Run an app
pipenv run python3 apps/analytics/main_analytics_2d_app.py \
  --config configs/warehouse_2d_config.json \
  --calibration configs/calibration_2d_i.json

# Unit tests
pipenv run pytest tests/unit/
pipenv run coverage run -m pytest tests/unit/ && pipenv run coverage report

# Full test pipeline (unit tests + Docker-Compose integration test) — see the `run-test` skill
# Prereqs: Docker + `nodejs`/`npm` (elasticdump uses `npx`)
# First-time only: `sudo chmod -R 777 docker_compose/apps_data` (kafka/elastic need
#                  writable bind-mounts; cleanup_all_datalog.sh resets perms on subsequent runs)
# Between runs: `./cleanup.sh <profile1> <profile2>` — always, dev mode leaves state behind
cd tests/integration && ./test.sh <profile1> <profile2> <mode>
# profile1: warehouse_2d | warehouse_3d | smart_city
# profile2: kafka | redis | mqtt
# mode:     dev (default, no cleanup on fail) | prod

# Docker
docker build -t py-analytics -f docker/Dockerfile .
docker run --network=host py-analytics python3 apps/playback/playback_frames.py
```

## Packaging

Package metadata lives in **`pyproject.toml`** at the repo root (PEP 621). A
thin `setup.py` at root exists solely so CI can inject a `VERSION_SUFFIX`
(e.g. `.dev42`) at build time — everything else (name, deps, classifiers,
license files, package discovery) is declared in `pyproject.toml`. When
working with packaging:

- Edit the base version in `setup.py` (currently `3.2.0`). Dependencies and
  all other metadata live in `pyproject.toml`.
- The editable install happens automatically via `pipenv install --dev`
  because `[dev-packages]` declares `vss-behavior-analytics = {path = ".", editable = true}`.
- Python version is **3.13**.
- **Runtime deps are duplicated between `pyproject.toml` (`[project].dependencies`)
  and `Pipfile` (`[packages]`)** — pipenv does not yet support pyproject.toml as
  a single source of truth (PEP 621 support is on its backlog). When you add,
  remove, or bump a runtime dep, **edit both files** and regenerate the lockfile
  with `rm Pipfile.lock && pipenv lock`. This locks **all** categories
  (`[packages]` and `[dev-packages]`) so `Pipfile.lock` includes pytest,
  coverage, ruff, flask, flask-cors, etc. Dev-only deps live in
  `Pipfile [dev-packages]`.

## Where to find things

For a full subpackage-by-subpackage map of `src/mdx/analytics/core/` (purpose,
key files, key classes, and "where to add X" guidance), see
**`docs/modules-overview.md`** — the authoritative module map. Use this
quick table for common tasks:

| Need to… | Read |
|---|---|
| Understand `BaseApp` lifecycle | `src/mdx/analytics/core/app/app_base.py`, `src/mdx/analytics/core/app/app_runner.py` |
| Implement an open-ended feature end-to-end | Invoke the `implement-feature` skill |
| Add a new incident type | `src/mdx/analytics/core/stream/state/frame/frame_state_management.py` + invoke the `new-incident` skill |
| Add a new app | Copy from `apps/analytics/main_analytics_2d_app.py` or use the `new-app` skill |
| Change a config field | `src/mdx/analytics/core/schema/config.py` (Pydantic models + defaults) |
| Wire dynamic-config support for a new component | `docs/dynamic-config.md` + `src/mdx/analytics/core/transform/config/` |
| Add a Kafka/Redis/MQTT source or sink | `src/mdx/analytics/core/stream/source/` or `.../sink/` (factory pattern) |
| Add a calibration transform | `src/mdx/analytics/core/transform/calibration/` |
| Debug latency | `src/mdx/analytics/core/tools/latency/latency_monitor.py` |
| Understand ROI/tripwire events | `src/mdx/analytics/core/transform/event/` |
| How anomaly detection works | `src/mdx/analytics/core/utils/anomaly_util.py`, `src/mdx/analytics/core/transform/detection/` |
| Run the full test pipeline (unit + integration) | Invoke the `run-test` skill |

## Deeper docs

- Building an app: `docs/building-mdx-analytics-app.md`
- Incident detection reference: `docs/incident-detection.md`
- Configuration guide: `docs/configuration.md`
- Dynamic configuration (runtime updates): `docs/dynamic-config.md`
- Modules overview: `docs/modules-overview.md`
- Testing: `docs/testing.md`
- Troubleshooting: `docs/troubleshooting.md`

## Pre-flight checklist for any change

Before reporting a task complete:

1. **Syntax-check** touched files: `python3 -c "import ast; ast.parse(open(PATH).read())"`.
2. **Run unit tests** for the touched subpackage: `pipenv run pytest tests/unit/<matching-path>/`.
3. **100% coverage on new code** — any new or modified line must be exercised by a test. Measure with `pipenv run coverage run --source=<touched-paths> -m pytest tests/unit/<matching-path>/ && pipenv run coverage report -m`. Use `# pragma: no cover` only for genuinely unreachable branches, with a one-line reason.
4. **Match the surrounding style** — don't reformat unrelated lines.
5. **Keep SPDX headers** on any new or rewritten file.
6. **No stray `print()`** or bare `except:`.
7. **Don't touch** generated files (`*_pb2.py`, `*_pb2.pyi`).
