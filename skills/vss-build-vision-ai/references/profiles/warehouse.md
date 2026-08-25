# Warehouse Industry Profile

Warehouse is the only supported **industry** Foundation. Its runtime behavior,
host setup, and deployment procedure are owned by
`skills/vss-deploy-profile/references/warehouse.md`; this file carries only the
**composition surface** — what the Foundation is made of and what constrains it
— and is not a second source of truth for anything procedural.

`overrides.env` defines further service lists; only the nine below are
selectable here. Anything else routes to `vss-deploy-profile`.

Prefer expanding the selected variant unchanged. Deltas are permitted but
floor-guarded — see [`../composition.md`](../composition.md).

## Capabilities and routing cues

- Multi-camera warehouse perception — RT-DETR (2D) or Sparse4D (3D) — with
  behavior analytics over ROI, tripwire, and proximity events.
- Choose for warehouse, loading-dock, forklift/pallet, or depth-aware
  multi-camera requests. Do **not** choose it for generic detection or search —
  `search` is the developer Foundation for those.
- `MODE=2d` with `BP_PROFILE=bp_wh` is the only variant with an agent, UI, and
  RTVI VLM. Every other variant is headless perception plus analytics.

## Profile Service Set

Authoritative source:
`deploy/docker/industry-profiles/warehouse-operations/overrides.env`. Select one
list by variant; expand it verbatim into `COMPOSE_PROFILES` and record its name
in `FOUNDATION_VARIANT`. Nine of the file's lists are in scope:

| `MODE` | `BP_PROFILE` | Extended list | Minimal list |
|---|---|---|---|
| `2d` | `bp_wh` | `COMPOSE_PROFILES_WH_2D` | — |
| `2d` | `bp_wh_kafka` | `…_WH_KAFKA_2D` | `…_WH_KAFKA_2D_MINIMAL` |
| `2d` | `bp_wh_redis` | `…_WH_REDIS_2D` | `…_WH_REDIS_2D_MINIMAL` |
| `3d` | `bp_wh_kafka` | `…_WH_KAFKA_3D` | `…_WH_KAFKA_3D_MINIMAL` |
| `3d` | `bp_wh_redis` | `…_WH_REDIS_3D` | `…_WH_REDIS_3D_MINIMAL` |

Extended adds ELK, `vss-video-analytics-api-<mode>`, `vss-haproxy-ingress`,
`import-calibration-output-container-<mode>`, and monitoring (`dcgm-exporter`,
`prometheus`, `grafana`, `node-exporter`, `cadvisor`). Minimal lists carry none
of these.

> `MINIMAL_PROFILE` and `ELASTICSEARCH_MODE` are **dead knobs** on this path —
> read only by `blueprint-deploy.sh` and the launchable, never by the compose
> stack. Size is selected *only* by which list `COMPOSE_PROFILES` points at.

## Capability owners present

`<mode>` is `2d` or `3d`; the suffix is on the compose *service* name only
([`../services/vios.md`](../services/vios.md)).

| Owner | Service profile keys |
|---|---|
| RT-CV | `perception-2d` / `perception-3d`; 3D additionally requires `ds-configurator-3d` |
| Behavior Analytics | `vss-behavior-analytics-<mode>` |
| Configurator | `bp-configurator-<mode>`, `bp-configurator-<mode>-init` |
| ELK | `kafka`, `kafka-topic-init-container`, `redis`, `broker-health-check`, `elasticsearch`, `elasticsearch-init-container`, `kibana`, `logstash`, `kibana-init-container-<mode>` |
| VIOS | `nvstreamer-<mode>`, `sensor-ms-<mode>`, `streamprocessing-ms-<mode>`, `centralizedb`, `vst-ingress`, `sdr-controller`, `turnserver`, `turnserver-init`, `init-dirs`, `render-config`, `wdm-env-from-config`, `wait-for-redis`, `wait-for-docker-workloads`, `sensor-bp-wait-bp-configurator` |
| Video Analytics API | `vss-video-analytics-api-<mode>`, `import-calibration-output-container-<mode>` |
| Ingress | `vss-haproxy-ingress` |
| Monitoring | `dcgm-exporter`, `prometheus`, `grafana`, `node-exporter`, `cadvisor` |
| Agent / RT-VLM / LLM NIM | `bp_wh` only: `vss-agent`, `vss-ui`, `vss-va-mcp`, `phoenix`, `alert-bridge`, `rtvi-vlm`, `llm_${LLM_MODE}_${LLM_NAME_SLUG}` |

`redis` is in **every** warehouse list — it backs `sdr-controller` regardless of
broker choice, and is additionally the CV broker when `STREAM_TYPE=redis`.

`vios-apt-cache-init` resolves into every warehouse build without appearing in
any `COMPOSE_PROFILES_WH_*` list: it carries no `profiles:` gate and is a
`depends_on` of `streamprocessing-ms-*`. Expect it as a one-shot `Exited (0)`;
its absence from the service list is not a defect.

## Profile-specific environment knobs

| Knob | Purpose |
|---|---|
| `MODE`, `BP_PROFILE`, `STREAM_TYPE` | Select the variant. These three pick the `COMPOSE_PROFILES_WH_*` list; they are not free-form. |
| `SAMPLE_VIDEO_DATASET`, `NUM_STREAMS` | Must match each other and the variant — see Hard constraints. |
| `HARDWARE_PROFILE` | Selects perception tuning in `blueprint-configurator/blueprint_config.yml` and LLM NIM sizing. Not validated by Compose; an unrecognized value silently matches no tuning section. |
| `VSS_APPS_DIR`, `VSS_DATA_DIR` | Ship as `/path/to/…` sentinels — always set both. Their closure is listed in [`../composition.md`](../composition.md). |
| `RT_CV_DEVICE_ID` (0), `RT_VLM_DEVICE_ID` (1), `LLM_DEVICE_ID` (2) | GPU layout. |
| `LLM_MODE`, `LLM_NAME`, `LLM_NAME_SLUG`, `LLM_BASE_URL` | `bp_wh` + `MODE=2d` only; `none` everywhere else. For `remote`, `LLM_BASE_URL` is the endpoint root **without** a trailing `/v1` — the agent config appends it. |
| `VLM_MODE`, `VLM_NAME_SLUG` | Keep both `none`. Warehouse uses the integrated RTVI VLM, never the standalone VLM NIM path. |
| `PERCEPTION_TAG` | Must be `sbsa`-tagged when `HARDWARE_PROFILE=DGX-SPARK`. |
| `BP_CONFIGURATOR_ENV_FILE` | Point at the build's generated `configurator.env`. Without it the configurator reads the checked-in `overrides.env` and bakes the `<HOST_IP>` sentinel — see [`../services/configurator.md`](../services/configurator.md). |
| `NVSTREAMER_<MODE>_CONFIG_DIR`, `TURN_PUBLIC_HOST` | Easily-missed closure members. `TURN_PUBLIC_HOST` derives from `HOST_IP` only transitively, through `EXTERNAL_IP` and `VSS_PUBLIC_HOST`. |

## Hard constraints

Each of these fails at bring-up or silently at runtime, not at `docker compose
config` — `scripts/validate_warehouse_env.py` checks them before deploy.

| Constraint | Symptom if violated |
|---|---|
| `MODE` must be `2d` or `3d`, and `BP_PROFILE` one of `bp_wh`, `bp_wh_kafka`, `bp_wh_redis` | routes to a service list this skill does not support |
| `BP_PROFILE=bp_wh` is 2D-only | unsupported combination |
| `BP_PROFILE=bp_wh` is rejected on `IGX-THOR` and `DGX-SPARK` | configurator refuses |
| `HARDWARE_PROFILE=DGX-SPARK` requires an `sbsa` `PERCEPTION_TAG` | configurator refuses |
| `LLM_MODE=local` requires `services/nim/<LLM_NAME_SLUG>/hw-<HARDWARE_PROFILE>.env` | compose dies with a bare "no such file" |
| Dataset ↔ variant: `nv-warehouse-4cams` only with `bp_wh`+`2d` (4 streams); `warehouse-loading-dock-3cams-synthetic` with 2D kafka/redis (3); `warehouse-4cams-20mx20m-synthetic` with `3d` (4) | short stream count with every container healthy |
| `STREAM_TYPE=redis` iff `BP_PROFILE=bp_wh_redis` | no metadata reaches the broker |
| A custom `SAMPLE_VIDEO_DATASET` has no checked-in `calibration.json` | Docker creates a directory where a file is expected; perception emits nothing |
| `MODE=3d` on a `…_MINIMAL` list has no Elasticsearch | `mdx-bev` never persisted; BEV output unverifiable |

### Calibration is already in the repo

The shipped sample datasets **need no calibration run**. Each carries a
checked-in `calibration.json` that Compose bind-mounts by path:

```text
warehouse-<mode>-app/calibration/sample-data/${SAMPLE_VIDEO_DATASET}/calibration.json
```

All three shipped datasets carry one. 3D mounts it three ways — behavior
analytics reads `/resources/calibration.json`, `ds-configurator-3d` and
perception read `/opt/data/ds-configurator/calibration.json`. Nothing is staged
under `$VSS_DATA_DIR`.

Only a **custom** dataset needs a calibration run — produced by
`vss-generate-video-calibration` — dropped at the path above under its dataset
name. `scripts/validate_warehouse_env.py` fails the build when it is missing.

`import-calibration-output-container-<mode>` (extended lists only) imports
calibration into the analytics store; it does not produce calibration.

## Stock readiness checks

Container-state gating is the shared Gate 0 in [`../readiness.md`](../readiness.md);
warehouse's one-shot init containers are expected `Exited (0)` and pass it
unchanged. Warehouse additionally needs a **liveness** check — every container
can be `Up` while zero streams are processed:

```bash
docker logs --since 60s vss-rtvi-cv 2>&1 | grep -aE "stream_name" | tail -8
docker logs --since 60s vss-rtvi-cv 2>&1 | grep -a "Active sources" | tail -1
```

Expect one `stream_name` line per source at roughly source framerate, and an
active-source count equal to `NUM_STREAMS`. Do **not** `grep -i fps` —
DeepStream's only line containing that string is a valueless header, so it
reports success regardless.

HTTP probes, when the selected list ships them:

```bash
curl -sf "http://${HOST_IP}:${HAPROXY_HOST_PORT:-7777}/vst/"
curl -sf "http://${HOST_IP}:9200/_cluster/health"          # extended, or bp_wh
curl -sf "http://${HOST_IP}:8081/livez"                    # extended, or bp_wh
curl -sf "http://${HOST_IP}:5601/kibana/api/status"        # extended, or bp_wh
curl -sf "http://${HOST_IP}:8000/health"                   # bp_wh only
```

> Endpoint quirks that read as a dead service are in
> [`../services/elk.md`](../services/elk.md). `/behavior-analytics` and
> `/perception-sdr` always 503 — the first publishes no HTTP listener
> ([`../services/behavior-analytics.md`](../services/behavior-analytics.md)),
> the second names a container no warehouse list deploys.

## Sources

- `deploy/docker/industry-profiles/warehouse-operations/.env`
- `deploy/docker/industry-profiles/warehouse-operations/overrides.env`
- `deploy/docker/industry-profiles/warehouse-operations/compose.yml`
- `deploy/docker/industry-profiles/warehouse-operations/warehouse-{2d,3d}-app/`
- `deploy/docker/industry-profiles/warehouse-operations/blueprint-configurator/blueprint_config.yml`
- `deploy/docker/services/infra/compose-no-turn-tcp-relay.yml`
- `skills/vss-deploy-profile/references/warehouse.md` — authoritative for host
  setup, deployment procedure, access points, and app data
