# Alerts microservice (Compose definition)

`compose.yml` here is the shared service definition for the Alerts
microservice. It is not a standalone stack: it declares dependencies on Kafka,
Elasticsearch and the VLM that are defined in other service files, and it is
activated by the `alert-bridge` Compose profile listed in a deployment
profile's `COMPOSE_PROFILES`.

## Deploy

Bring it up through the profile helper, not from this directory:

```bash
./deploy/docker/scripts/dev-profile.sh up \
  --profile alerts \
  --mode verification \
  --hardware-profile H100
```

Use `--mode real-time` for RTVI-VLM alert generation instead of CV-triggered
verification. The industry profiles under `deploy/docker/industry-profiles/`
include the same service. See [`deploy/docker/README.md`](../../README.md) for
the env-file precedence and the full profile list.

## Configuration

There is no `configs/` directory here. Every config file is owned by the
deployment profile and mounted in by path, so which file you get depends on the
profile you deployed:

| Variable | Mounted at | Contents |
|----------|------------|----------|
| `VLM_AS_VERIFIER_CONFIG_FILE` | `/app/configs/config.yml` | Main service config |
| `VLM_AS_VERIFIER_CONFIG_FILE_REALTIME` | `/app/configs/realtime-config.yml` | Always-on alert rules |
| `VLM_AS_VERIFIER_ALERT_TYPE_CONFIG_FILE` | `/app/alert_type_config.json` | Per-alert-type verification config |

The profiles set these in their `overrides.env` — for example
`developer-profiles/dev-profile-alerts/overrides.env`. Edit the file the
variable points at; editing anything under this directory has no effect.

The entrypoint runs `scripts/env-substitute.py` first, which expands `${...}`
references in the mounted configs and writes the rendered results to
`/app/runtime/`, a tmpfs. That rendered copy is what the service reads, which is
why the mounts are read-only.

Request defaults — the `vlm_params` and `request_defaults` applied to fields a
request omits — are not part of this contract. Profile deployments use the
`alert_request_defaults.yaml` baked into the image; there is no mount or
variable here to replace it. To change them, edit the per-alert-type
verification config through the API, or run the service through
`services/alert/deploy_docker-compose.yml`, which does support an override.
The file itself is documented in
[`services/alert/src/schemas/config/README.md`](../../../../services/alert/src/schemas/config/README.md).

### Sections of `config.yml`

- `vst_config` — VIOS/VST base URL, clip-window anchoring and duration, plus the
  `storage` sub-block used to resolve a recorded media path by id.
- `vlm` — the OpenAI-compatible VLM endpoint and model used for verification,
  and frame sampling for the clip.
- `rtvi_vlm` — RTVI-VLM endpoint for real-time alert rules.
- `kafka` — brokers, group id, input/output topics, `max_poll_records`.
- `event_bridge` — source and sink selection; Kafka is the default.
- `alert_agent` — pipeline mode, concurrency, clip duration bounds, event
  filters, and the `always_on` gate driven by `ALERT_AGENT_ALWAYS_ON`.
- `prompt` — whether a payload-provided prompt takes precedence.
- `alert_type_config_file` — path to the per-alert-type config.
- `webhook` / `websocket` / `cors` — outbound notification and realtime
  broadcast surfaces.
- `elastic` / `persistence` / `vlm_enhanced_sink` — Elasticsearch target and
  persistence of verification results.
- `logging` — global level and format.

Clip resolution is VIOS/VST plus a direct VLM call; there is no Alert-side VSS
service to configure. Parameter-level reference for these sections is in
[`services/alert/README.md`](../../../../services/alert/README.md).

## Files

| Path | Purpose |
|------|---------|
| `compose.yml` | The service definition |
| `alert.env` | Shared host-side defaults (port, OTel switches) |
| `scripts/env-substitute.py` | Renders `${...}` in the mounted configs at startup |
| `scripts/tests/` | Unit tests for the renderer |
