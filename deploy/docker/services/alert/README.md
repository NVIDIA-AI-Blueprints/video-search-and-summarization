# Alerts microservice (Compose definition)

`compose.yml` here is the shared service definition for the Alerts
microservice. It is not a standalone stack: it declares dependencies on Kafka,
Elasticsearch and the VLM that are defined in other service files, and it is
activated by the `alert-bridge` Compose profile listed in a deployment
profile's `COMPOSE_PROFILES`.

## Deploy

Deploy the `alerts` profile through
[`Build Vision AI`](../../../../skills/vss-build-vision-ai/SKILL.md), or use the
[manual Compose workflow](../../README.md#developer-profiles) with an edited
`developer-profiles/dev-profile-alerts/user-overrides.env`.

Select `MODE=2d_cv` for CV-triggered verification or `MODE=2d_vlm` for real-time
RT-VLM alerts. Keep the service set and accompanying mode settings aligned as
specified in the [Alerts mode reference](../../../../skills/vss-build-vision-ai/references/profiles/alerts.md#mode-selected-backend-configuration);
changing `MODE` alone is insufficient. The industry profiles under
`deploy/docker/industry-profiles/` include the same service.

## Configuration

There is no `configs/` directory here. Every config file is owned by the
deployment profile and mounted in by path, so which file you get depends on the
profile you deployed:

| Variable | Mounted at | Contents |
|----------|------------|----------|
| `VLM_AS_VERIFIER_CONFIG_FILE` | `/app/configs/config.yml` | Main service config |
| `VLM_AS_VERIFIER_CONFIG_FILE_REALTIME` | `/app/configs/realtime-config.yml` | Always-on alert rules |
| `VLM_AS_VERIFIER_ALERT_TYPE_CONFIG_FILE` | `/app/alert_type_config.json` | Per-alert-type verification config |

The profile templates declare these paths. Override them in the manual
`user-overrides.env` or the Build Vision AI build's `override.env`. Edit the file the
variable points at; editing anything under this directory has no effect.

The entrypoint runs `scripts/env-substitute.py` first, which expands `${...}`
references in the mounted configs and writes the rendered results to
`/app/runtime/`, a tmpfs. That rendered copy is what the service reads, which is
why the mounts are read-only.

Request defaults — the `vlm_params` and `request_defaults` in
`alert_request_defaults.yaml` — are not part of this contract, and there is no
mount or variable here to replace the copy baked into the image. Nothing is
lost by that: the file feeds the request-entity validator, which the
verification pipeline does not call, so its values never reach a VLM call.
Per-request VLM tuning belongs in the per-alert-type verification config,
served from Elasticsearch and changeable at runtime through
`PUT /api/v1/verification/config/{alert_type}`. The file itself is documented in
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
- `webhook` / `cors` — outbound notification and the CORS policy of the HTTP
  API.
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
