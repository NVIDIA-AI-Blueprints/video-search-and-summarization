# VSS RT Config Adaptor

VSS RT Config Adaptor is a small Flask service that accepts DeepStream configuration metadata and writes runtime configuration files for downstream DeepStream workloads.

The service exposes `POST /config`. The request must use `Content-Type: application/json` and include the configured event and metadata fields. By default, the service reads `event.metadata.region`, `event.metadata.group`, and `event.metadata.topic-prefix`, writes those values to a CSV file, updates a DeepStream YAML config from a source template, and exits after the response is sent.

## Repository layout

```text
app/                    Flask application, runtime config, and distroless entrypoint
tests/                  Unit tests for the Flask API, YAML helpers, and env config
docker/Dockerfile       Container build for the distroless runtime image
docker/Dockerfile.dockerignore
                        Dockerfile-specific ignore rules for root-context builds
pyproject.toml          Python project dependencies managed by uv
uv.lock                 Locked Python dependency graph
```

## Runtime configuration

The service can be configured with environment variables:

| Variable | Default | Description |
| --- | --- | --- |
| `PORT` | `9002` | Flask listen port. The container exposes `5000`, and container deployments may override `PORT`. |
| `DS_CONFIG_PATH` | `/tmp/data/vss-rt-config-adaptor/config.csv` | CSV output path for `region,group,topic-prefix`. |
| `DS_CONFIG_YAML_SOURCE_PATH` | `/ds-config/config.yaml` | Source DeepStream YAML template path. |
| `DS_CONFIG_YAML_TARGET_PATH` | `/tmp/data/vss-rt-config-adaptor/config.yaml` | Target YAML output path. |
| `CALIB_FILE_PATH` | `/tmp/data/vss-rt-config-adaptor/calibration_grouped.json` | Calibration file path written into the target YAML. |
| `EVENT_OBJECT_FIELD` | `event` | Top-level request field containing lifecycle metadata. |
| `METADATA_OBJECT_FIELD` | `metadata` | Nested field containing `region`, `group`, and `topic-prefix`. |
| `LOG_LEVEL` | `INFO` | Entrypoint logging level. Note: the Flask app's log level is fixed at `INFO` and is not affected by this variable. |

The Flask app always writes a rotating log file to `/tmp/vss-rt-config-adaptor.log` (max 200 KB, 2 backups).

Example request:

```bash
curl -X POST http://localhost:9002/config \
  -H 'Content-Type: application/json' \
  -d '{"event":{"metadata":{"region":"r1","group":"g1","topic-prefix":"tp1"}}}'
```

## Local development

Install dependencies from the repository root:

```bash
uv sync --frozen
```

Run unit tests:

```bash
uv run pytest tests/ -v --tb=short
```

Run the service locally:

```bash
uv run python app/entrypoint.py
```

## Container build

Build from the repository root so the Dockerfile can copy `app/`, `pyproject.toml`, `uv.lock`, and the root legal artifacts directly:

```bash
docker build -f docker/Dockerfile -t vss-rt-config-adaptor:local .
```

The image uses `app/entrypoint.py` because the runtime stage is distroless and does not include a shell.

## Legal artifacts

The container build expects this root-level file to be present:

- `3rdParty_Licenses.md`

The Dockerfile copies it into `/usr/src/app/ThirdPartyLicences.txt` in the runtime image.
