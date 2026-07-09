# Deployment Reference: Alert Verification (Alert Bridge)

Standalone deployment contract for the Alert Verification service (`vss-alert-bridge`,
"Alert Bridge" / "AB"). Companion to `integrate-alerts.md`. The `vss-build-vision-agent`
patch machinery (flag insertion, `depends_on` strip, realtime-config materialization,
`.env` overrides) lives in `vss-build-vision-agent/references/patch-alerts.md`.

## Image

| Field | Value |
|---|---|
| Image | `nvcr.io/nvidia/vss-core/vss-alert-verification` |
| Tag | `3.2.0` (GA; from `services/alert/compose.yml`) |
| Container name | `vss-alert-bridge` |
| Registry auth | `nvcr.io` — `docker login` with `NGC_CLI_API_KEY` (same key as the rest of the VSS stack) |
| Entrypoint | `python /app/env-substitute.py --source /app/configs/config.yml --output /app/runtime/config.yml -- python enhance_alert_with_vlm.py --config /app/runtime/config.yml` |

## GPU

**None.** AB is CPU-only. All VLM inference is delegated over HTTP to the existing
`rtvi-vlm` container (port 8018). No `deploy.resources.reservations.devices` block, no
`runtime: nvidia`. When layered on IN-1, AB adds **zero** GPU pressure — it reuses the
single `RT_VLM_DEVICE_ID` GPU already serving captions.

## CPU + Memory

- No explicit CPU/memory limits in the upstream compose. AB is a Python event-bridge +
  HTTP client; baseline footprint is modest (a few hundred MB RSS). `alert_agent.num_workers: 10`
  worker threads (config-tunable). Memory scales with in-flight clip downloads
  (`alert_agent.max_allowed_stream_size: 2` min) and async sink queues.
- `tmpfs: /app/runtime` capped at 10 MB (holds only the rendered `config.yml`).

## Storage

- **No persistent named volume.** AB persists realtime rules + prompt configs to
  **Elasticsearch** (`ab-` prefixed indices), not to disk.
- Config bind-mounts (read-only):
  - `${VLM_AS_VERIFIER_CONFIG_FILE}` → `/app/configs/config.yml`
  - `${VLM_AS_VERIFIER_CONFIG_FILE_REALTIME}` → `/app/configs/realtime-config.yml`
  - `${VLM_AS_VERIFIER_ALERT_TYPE_CONFIG_FILE}` → `/app/alert_type_config.json`
  - `services/alert/scripts/env-substitute.py` → `/app/env-substitute.py`
- `tmpfs` (RAM) at `/app/runtime` for the env-substituted config.

## Startup Behavior

1. Entrypoint runs `env-substitute.py`: reads `/app/configs/config.yml`, replaces every
   `${VAR}` with the container env value (emitting a stderr warning for any unset var),
   writes the result to the `/app/runtime/config.yml` tmpfs, then `exec`s the main app.
2. `depends_on` gates: waits for `kafka` healthy, `elasticsearch` healthy, `redis`
   started, `kafka-topic-init-container` completed. The VLM peers (`rtvi-vlm` + 8 sibling
   NIMs + `nvstreamer-alerts`) are `required: false`.
3. AB connects to Kafka (`localhost:9092`), subscribes to `mdx-incidents` / `mdx-alerts`,
   opens ES (`localhost:9200`), and starts the REST API on port 9080.
4. **No model warmup of its own** — AB does a lightweight RTVI readiness poll
   (`rtvi_vlm.health_check_interval_seconds: 30`) against rtvi-vlm `/v1/health/ready`; it
   becomes useful once rtvi-vlm is healthy (which on a cold RT-VLM boot can take up to
   1200 s — AB tolerates this and reconciles on RTVI recovery).
5. Startup is fast (seconds) — there is no large container init beyond config render.

## Known Deployment Issues

| # | Symptom | Cause | Fix |
|---|---|---|---|
| 1 | AB container mounts an empty directory at `/app/configs/realtime-config.yml`; always-on path 500s | `VLM_AS_VERIFIER_CONFIG_FILE_REALTIME` unset → compose default `/path/to/realtime-config.yml` (non-existent) → Docker creates an empty dir | Supply a real `realtime-config.yml` and set `VLM_AS_VERIFIER_CONFIG_FILE_REALTIME` to it (patch-alerts.md materializes the warehouse-operations sample into the build's patched tree). |
| 2 | rtvi-vlm returns HTTP 400 "No such model" | `VLM_NAME` set to friendly `cosmos-reason2-8b` | Use the runtime-generated id `nim_nvidia_cosmos-reason2-8b_hf-1208` (resolve via `GET /v1/models`). |
| 3 | rtvi-vlm returns HTTP 422 on verify | `VLM_MODEL_TYPE=nim` leaks `verify_ssl` into the body | Set `VLM_MODEL_TYPE=rtvi`. |
| 4 | `docker compose config` rejects the project — undefined `depends_on` service | The 8 sibling NIM keys + `nvstreamer-alerts` are `required: false` but still validated at project-load if not defined | Strip the undefined `depends_on` peers in the patched copy (patch-alerts.md Patch 2). |
| 5 | Verified docs never appear in ES | `mdx-incidents`/`mdx-alerts` have no producer (no CV pipeline) | Expected in a pure 2d_vlm build — drive the always-on REST path instead; the Kafka path needs an AN-2/CV producer. |
| 6 | host port 9080 already bound | another service or a prior AB instance holds 9080 | free the port before deploy (`ALERT_BRIDGE_PORT` is host-networked). |
| 7 | `env-substitute.py` warns "Environment variable X is not set or empty" | a `${VAR}` in `config.yml` has no value in AB's env | benign for optional knobs; ensure `RTVI_VLM_BASE_URL`, `VLM_NAME`, `VLM_BASE_URL` are set. |

## Prerequisites

- The full IN-1 ELK + VIOS + RT-VLM baseline running (Kafka, ES, Redis,
  kafka-topic-init, rtvi-vlm healthy).
- `NGC_CLI_API_KEY` valid; `docker login nvcr.io` done.
- Host port 9080 free.
- A `realtime-config.yml` staged and pointed to by `VLM_AS_VERIFIER_CONFIG_FILE_REALTIME`
  (for the always-on path).
- `config.yml` + `alert_type_config.json` staged and pointed to by their env vars.
- Docker Engine >= 28.2 + Compose plugin >= 2.36.

## Verify Deployment

```bash
# 1. Container is up
docker ps --format '{{.Names}}\t{{.Status}}' | grep vss-alert-bridge

# 2. REST API reachable
curl -sf "http://${HOST_IP}:9080/api/v1/realtime" && echo OK   # returns the (possibly empty) rule list

# 3. AB sees rtvi-vlm
docker logs vss-alert-bridge 2>&1 | grep -i "rtvi" | tail

# 4. Register a realtime rule
curl -sf -X POST "http://${HOST_IP}:9080/api/v1/realtime" \
  -H 'Content-Type: application/json' -d @rule.json

# 5. Rule persisted to ES
curl -sf "http://${HOST_IP}:9200/ab-alert-realtime-rules/_count" | jq .count   # > 0

# 6. After a camera event fires the rule, verified docs land in ES
curl -sf "http://${HOST_IP}:9200/mdx-vlm-alerts*/_count" | jq .count
curl -sf "http://${HOST_IP}:9200/mdx-vlm-incidents*/_count" | jq .count
```

(`mdx-vlm-incidents` / `mdx-vlm-alerts` are the direct `vlm_enhanced_sink` ES indices when
`type: elastic`; if the Kafka sink form is selected they become `mdx-vlm-*-YYYY-MM-DD`
date indices written by Logstash.)

## Tear Down

AB has no persistent volume of its own, so teardown is the standard project-level
`docker compose ... down`. The realtime rules + verified docs it wrote live in ES, which
is cleared by the project-level `down -v` (or by deleting the `ab-*` / `mdx-vlm-*` indices).

```bash
# Stop AB along with the rest of the profile (preserve ES/Kafka data):
docker compose --env-file <BUILD_DIR>/.env -f <BUILD_DIR>/compose.yml \
  --profile <invented-flag> down --remove-orphans

# Full wipe (also clears ES indices via the project volumes):
docker compose ... down -v          # WARN: wipes model caches + ES + Kafka
```

---

*Source tracing:*
- *Image, entrypoint, env, volumes, depends_on, tmpfs, network_mode: `deploy/docker/services/alert/compose.yml`.*
- *Config knobs (workers, sinks, persistence, rtvi_vlm): `dev-profile-alerts/vlm-as-verifier/configs/config.yml`.*
- *env-substitute behavior: `deploy/docker/services/alert/scripts/env-substitute.py`.*
- *Local met-blueprint-docs deploy guidance (`alert-verification-service.rst`) NOT readable this session (local Read denied); contract sourced from remote compose ground truth (authoritative for deploy-time). Re-trace when accessible.*
