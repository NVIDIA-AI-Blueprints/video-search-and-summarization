# Request Defaults Configuration

This directory holds the loader for `alert_request_defaults.yaml`, the file that
supplies default values for fields an incoming alert request may omit. It
covers two things and nothing else:

- **`vlm_params`** — sampling parameters sent to the VLM when the request does
  not carry its own.
- **`request_defaults`** — values for optional top-level request fields.

## What this file is not

Three different things configure VLM behavior, and only the second one lives
here:

| Concern | Where |
|---------|-------|
| VLM service endpoint, model name, frame sampling | `vlm` section of `config.yaml` |
| Per-request sampling defaults | `alert_request_defaults.yaml` (this guide) |
| Per-alert-type verification settings | `/api/v1/verification/config/{alert_type}` |

Editing the wrong one is the common failure. A wrong `base_url` belongs to
`config.yaml`; a `temperature` that should apply to every request belongs here.

## File resolution

`AlertsDefaultsConfigLoader` searches, in order:

1. `ALERT_BRIDGE_DEFAULTS_FILE`, if set and the path exists. A directory is
   accepted — the loader appends `alert_request_defaults.yaml` to it.
2. `alert_request_defaults.yaml` in the process working directory. In the
   container that is `/app`, which is where the shipped copy lands.

The first readable candidate wins; the result is cached for the process
lifetime, so a change requires a restart. If no candidate is readable the
service raises `FileNotFoundError` at startup rather than falling back to
built-in values.

In the service's own Compose definition, `ALERT_BRIDGE_DEFAULTS_FILE` selects
the host file bind-mounted over that second path:

```bash
ALERT_BRIDGE_DEFAULTS_FILE=./your-defaults.yaml \
  docker compose -f deploy_docker-compose.yml up -d
```

The profile deployments under `deploy/docker/` deliberately do **not** wire
this up: they run with the `alert_request_defaults.yaml` baked into the image.
Request-level tuning there belongs in the per-alert-type verification config,
which is served from Elasticsearch and changeable at runtime through
`PUT /api/v1/verification/config/{alert_type}` — no restart, no rebuild.

## Required sections

Startup fails unless both are present and non-empty:

```yaml
vlm_params:
  prompt: null
  system_prompt: null
  response_format:
    type: "text"
  max_tokens: 512
  temperature: 0.2
  top_p: 1.0
  top_k: 100
  seed: 10

request_defaults:
  confidence: 0.92
  meta_labels: []
  cv_metadata_path: null
```

### `vlm_params`

Every key is optional within the section, but a key you omit has no default
anywhere else — the field is simply sent as unset. Bounds are enforced by
`request_entity/models/parameters.py`:

| Parameter | Type | Bounds |
|-----------|------|--------|
| `prompt` | str \| null | ≤ 12000 chars |
| `system_prompt` | str \| null | ≤ 14000 chars |
| `response_format` | dict \| null | free-form, passed through |
| `max_tokens` | int \| null | > 0, ≤ 100000 |
| `temperature` | float \| null | 0.0 – 2.0 |
| `top_p` | float \| null | 0.0 – 1.0 |
| `top_k` | int \| null | > 0, ≤ 2048 |
| `seed` | int \| null | 0 – 2147483647 |

Unknown keys are ignored rather than rejected, so a stale field left in the
file is silent. A request may override any of these per field: the values here
are deep-merged with the payload's, and the payload wins on the fields it
carries. Accepted payload shapes are `vlm_params`, `vlmParams`, and — for
older clients — the nested `vss_params.vlm_params` / `vssParams.vlm_params`.

### `request_defaults`

Only three keys are read, one per optional request field:

| Key | Applied to |
|-----|------------|
| `confidence` | `confidence` |
| `cv_metadata_path` | `cv_metadata_path` |
| `meta_labels` | `meta_labels` |

The logic per field is:

1. Present in the request → the request value is used, always.
2. Absent from the request, defined here → this value is used.
3. Absent from the request, not defined here → the field stays absent on the
   entity.

Case 3 is the reason to *omit* a key rather than set it to `null`: `null`
produces a field explicitly set to `None`, which downstream consumers see,
while omission produces no field at all.

## Optional sections

`constraints` bounds parameter values and is enforced by the loader at startup.
It is keyed by section, then parameter, with `min` and `max`:

```yaml
constraints:
  vlm_params:
    max_tokens:
      min: 1
      max: 4096
```

A value outside its constraint raises `ValueError` and the service does not
start. The shipped file sets `constraints: {}`, so only the Pydantic bounds in
the table above apply.

`schema.version` is compared against `2.0.0`. A mismatch logs a warning and
loading continues.

## Inert sections

The shipped file also carries `validation` and `field_validation`. The loader
parses them and exposes them on `AlertsDefaultConfig`, but no current code path
reads either one — required-field enforcement lives in the Pydantic models, and
validation logging is not configurable. Changing them has no effect. They are
retained so an existing file keeps loading.

## Verifying a change

```bash
yamllint alert_request_defaults.yaml
```

```python
from schemas.config import AlertsDefaultsConfigLoader

loader = AlertsDefaultsConfigLoader()
config = loader.load_defaults()
print(loader.get_config_info())   # includes the resolved config_source path
```

`config_source` reports which file the loader actually resolved, which is how
you confirm an `ALERT_BRIDGE_DEFAULTS_FILE` override took effect rather than
being silently ignored. Under the Compose override it is not a useful signal:
the bind replaces the file at the path the loader would have used anyway, so
`config_source` reads the same either way and the content is what to check.

## Common startup errors

| Message | Cause |
|---------|-------|
| `Configuration file not found in search paths` | Neither candidate above was readable |
| `Missing required configuration sections: [...]` | `vlm_params` or `request_defaults` absent |
| `Configuration file is empty or invalid` | File parsed to nothing |
| `vlm_params.max_tokens value N above maximum M` | A `constraints` entry was violated |
| `Schema version mismatch` (warning) | `schema.version` is not `2.0.0` |
