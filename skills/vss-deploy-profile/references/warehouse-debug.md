# Warehouse Debug Reference

Live debugging of an **already-running** VSS Warehouse deployment. Triage container health, perception FPS, GPU/CPU/disk resources, broker connectivity, and (3D / MV3DT) BEV camera timestamp synchronization via Elasticsearch. Identify root cause, propose a fix, then ask the user before applying it.

Companion to `warehouse.md`. Use this reference when the stack is already up but something is wrong — low FPS, containers restarting, streams missing, BEV out of sync, or general unhealthy state. For first-time install / redeploy / tear-down, go to `warehouse.md`.

Reference tables (container map, deps, log patterns, ES indices, GPU layout, endpoints, BEV thresholds) are in the top half; operational triage phases are in the bottom half.

---

## Container Dependency Chain

Failures propagate downstream. Always triage in this order — a broken upstream container is the root cause of all containers below it failing.

```
broker (kafka / redis)
  └── vss-configurator-<mode>-init   (one-shot broker gate; despite the name it renders no config)
        └── vss-configurator         (blueprint / stream / hardware config — must report HEALTHY)
              └── vss-vios-nvstreamer          (waits on the configurator: service_healthy)
                    └── vss-rtvi-cv            (perception — 2D RT-DETR or 3D Sparse4D, same
                        │                       container; starts after sdr-controller + sensor-ms)
                        ├── vss-rtvi-cv-config-adaptor (3D only — DeepStream config adaptor)
                        └── vss-behavior-analytics     (ROI, tripwire, proximity events)
                              └── (extended only: logstash, kibana, vss-video-analytics-api)

The configurator is UPSTREAM of nvstreamer, not below it: nvstreamer declares
`depends_on: bp-configurator-<mode>: condition: service_healthy`. If the configurator
never goes healthy, nvstreamer never starts and perception has no input — so triage the
configurator BEFORE concluding the stream source is broken.

NOTE: there is no `vss-rtvi-cv-sdr` container. Its service is commented out in
warehouse-3d-app.yml and it is in no COMPOSE_PROFILES_WH_* list. HAProxy's
/perception-sdr route still points at that hostname, so it answers 503 — that is
expected, not a fault.

`redis` runs in EVERY warehouse variant (it backs sdr-controller), so seeing redis
up does not mean STREAM_TYPE=redis. `vss-turnserver` (+ vss-turnserver-init) is also
in every list, for VST WebRTC playback.

MV3DT variant (MODE=mv3dt) — same dependency shape. The -mv3dt suffix applies ONLY to the
containers defined in warehouse-mv3dt-app.yml (nvstreamer, rtvi-cv, configurator + -init,
behavior-analytics, video-analytics-api, kibana-init, import-calibration-output). The
MV3DT-only mosquitto and vss-rtvi-cv-bev-fusion are UNsuffixed, as are the VST stack,
vss-turnserver, kafka/redis and vss-broker-health-check. mosquitto is defined in
services/infra/compose.yml, not in warehouse-mv3dt-app.yml:
  broker → vss-configurator-mv3dt-init → vss-configurator-mv3dt → vss-vios-nvstreamer-mv3dt
    → vss-rtvi-cv-mv3dt (per-camera perception; uses mosquitto/MQTT to exchange tracks
      │                  with the other per-camera trackers)
      └→ mdx-raw on kafka/redis → vss-rtvi-cv-bev-fusion (CPU-only) → mdx-bev
                                    → vss-behavior-analytics-mv3dt
  NOTE: bev-fusion does NOT speak MQTT. It reads mdx-raw from the broker.

Warehouse Auto-Calibration (BP_PROFILE=bp_wh_auto_calib) — minimal footprint:
  vss-vios-nvstreamer / vss-vios-nvstreamer-mv3dt → vss-configurator / vss-configurator-mv3dt
                      → vss-auto-calibration + vss-auto-calibration-ui
  (no broker, no perception, no analytics)

VST (VIOS) stack — independent of perception, feeds RTSP into it:
  vss-vios-postgres → vss-vios-sensor / vss-vios-streamprocessing
                    → vss-vios-ingress
                    → sdr-controller  (from services/infra/sdrc/ — combined WDM controller + Envoy
                                       router on :10000; replaces the deprecated vss-vios-sdr +
                                       vss-vios-envoy pair. vss-vios-mcp was also removed.)

elasticsearch — deployed when: BP_PROFILE=bp_wh (always; vss-agent storage), OR kafka/redis
extended, i.e. a COMPOSE_PROFILES_WH_* list WITHOUT the _MINIMAL suffix (ELK + bounding-box
overlays + analytics API; any mode).
NOTE: a _MINIMAL list does NOT deploy ES — so the mdx-bev index isn't persisted and the Phase 5
BEV-sync check has no data to read (applies to 3D and MV3DT).
NOTE: "minimal vs extended" is purely which COMPOSE_PROFILES_WH_* list is selected. MINIMAL_PROFILE
is not read by anything under deploy/docker — do not diagnose from its value.

monitoring (dcgm-exporter, prometheus, grafana, and node-exporter / cadvisor, which set no
container_name and so run as <COMPOSE_PROJECT_NAME>-node-exporter-1 / -cadvisor-1) — BP_PROFILE=bp_wh, or
2D/3D kafka/redis extended. The MV3DT lists contain no monitoring services.

`BP_PROFILE=bp_wh`-only stack (RTVI VLM + agent):
  vss-rtvi-vlm                                  (RTVI VLM — always local; rtvi-vlm is included in COMPOSE_PROFILES_WH_2D; VLM_MODE=none)
  vss-alert-bridge ← depends on vss-rtvi-vlm
  LLM NIM (varies — see below)
  vss-agent ← depends on LLM, vios
  vss-agent-ui ← depends on vss-agent
  vss-va-mcp
  phoenix

vss-haproxy-ingress — BP_PROFILE=bp_wh, BP_PROFILE=bp_wh_auto_calib, or kafka/redis extended (front-door on HAPROXY_HOST_PORT)
```

## Full Container List by Variant

`MODE` (`2d` / `3d` / `mv3dt`) and `BP_PROFILE` (`bp_wh` / `bp_wh_kafka` / `bp_wh_redis` / `bp_wh_auto_calib`) determine which explicit `COMPOSE_PROFILES_WH_*` service list from `generated.env` is active. Perception, behavior analytics, nvstreamer, and most other services use the **same container names** in 2D and 3D — no `-2d` / `-3d` suffix.

The **`-mv3dt` suffix is not universal** — it comes from each service's own `container_name:`, not from which file defines it. The deployed suffixed containers are exactly: `vss-vios-nvstreamer-mv3dt`, `vss-rtvi-cv-mv3dt`, `vss-configurator-mv3dt` (+ `-init`), `vss-behavior-analytics-mv3dt`, `vss-video-analytics-api-mv3dt`, `vss-kibana-init-mv3dt`, `vss-import-calibration-output-mv3dt`. `vss-rtvi-cv-bev-fusion` (declared in `warehouse-mv3dt-app.yml`) and `mosquitto` (defined in the shared `services/infra/compose.yml`, only referenced by the MV3DT app file via `depends_on`) are unsuffixed, as are the VST stack, `vss-turnserver`, `kafka`/`redis` and `vss-broker-health-check`. Both are MV3DT-only in practice — their profiles appear solely in the MV3DT Kafka/Redis lists.

### Warehouse CV core (2D and 3D variants)

| Container | Role |
|---|---|
| `kafka` (kafka variants) / `redis` (always deployed; also the broker when `STREAM_TYPE=redis`) | Message broker |
| `vss-broker-health-check` | One-shot gate — waits for broker, then exits `0`, releasing dependents |
| `vss-vios-nvstreamer` | RTSP stream server |
| `vss-rtvi-cv` | DeepStream perception (RT-DETR for 2D, Sparse4D for 3D) |
| `vss-rtvi-cv-config-adaptor` | DeepStream config adaptor (3D only) |
| `vss-configurator` | Stream and hardware config |
| `vss-behavior-analytics` | ROI / tripwire / proximity analytics |
| `vss-vios-postgres` / `-sensor` / `-streamprocessing` / `-ingress` + `sdr-controller` (from `services/infra/sdrc/`) | VST stack (legacy `-sdr` / `-mcp` / `-envoy` removed; SDR + Envoy roles now consolidated in `sdr-controller`) |
| `vss-turnserver` | TURN / WebRTC relay for VST playback |
| one-shot: `sdrc-init-dirs`, `sdrc-render-config`, `sdrc-wdm-env-from-config`, `sdrc-wait-for-redis`, `sdrc-wait-for-workloads`, `sensor-bp-wait-bp-configurator`, `vss-kafka-topics`, `vss-configurator-2d-init` / `-3d-init`, `vss-elasticsearch-init`, `vss-kibana-init`, `vss-import-calibration-output` | Init jobs — `Exited (0)` is success, not a failure. Only a **non-zero** exit is a finding |

### MV3DT CV core (`MODE=mv3dt` with `BP_PROFILE=bp_wh_kafka` or `bp_wh_redis`)

| Container | Role |
|---|---|
| `kafka` (kafka variants) / `redis` (always deployed; also the broker when `STREAM_TYPE=redis`) | Message broker |
| `vss-broker-health-check` | One-shot gate — waits for broker, then exits `0`, releasing dependents |
| `vss-vios-nvstreamer-mv3dt` | RTSP stream server |
| `vss-rtvi-cv-mv3dt` | DeepStream perception (per-camera) |
| `vss-rtvi-cv-bev-fusion` | BEV Fusion — fuses per-camera detections into unified 3D BEV frame. **CPU-only**: no `runtime: nvidia`, no device reservation. Reads `mdx-raw`, writes `mdx-bev` |
| `mosquitto` | MQTT broker for cross-camera messaging |
| `vss-configurator-mv3dt` | Stream and hardware config |
| `vss-behavior-analytics-mv3dt` | 3D spatial analytics |
| `vss-vios-postgres` / `sensor-ms-mv3dt` (container `vss-vios-sensor`) / `-streamprocessing` / `-ingress` + `sdr-controller` (from `services/infra/sdrc/`) | VST stack (legacy `-sdr` / `-mcp` / `-envoy` removed; SDR + Envoy roles now consolidated in `sdr-controller`) |

### Warehouse Auto-Calibration (`BP_PROFILE=bp_wh_auto_calib`)

| Container | Role |
|---|---|
| `vss-vios-nvstreamer` / `vss-vios-nvstreamer-mv3dt` | RTSP stream server |
| `vss-configurator` / `vss-configurator-mv3dt` | Blueprint configurator |
| `vss-auto-calibration` / `vss-auto-calibration-ui` | Camera auto-calibration |
| VST stack (subset) | Stream management for calibration |

Only the standalone `vss-auto-calibration,vss-auto-calibration-ui` service list and the `COMPOSE_PROFILES_WH_AUTO_CALIB_*` warehouse lists start the auto-calibration containers. Regular `bp_wh`, `bp_wh_kafka`, and `bp_wh_redis` variants do not.

> **2D:** Auto-Calibration adds blank `group` and `region` fields to `calibration.json`; remove those fields before redeploying. They are not required for 2D calibration.

> **3D / MV3DT:** When deploying calibration for 3D or MV3DT modes, generated calibration files must include a populated `sensors[].group` object on every camera sensor. For MV3DT, after generating `calibration.json`, also run the utility scripts under `tools/rtvi-cv-mv3dt-utils` to refresh `camInfo/<sensor_id>.yml`, `pub_sub_info_config.yml`, and the tracker `ObjectModelProjection.cameraModelFilepath` mappings. Then run camera clustering with `--n_clusters 1` for the standard single-BEV warehouse setup, and verify the group field is present under sensors in `calibration.json`. Use the standalone AMC service list to upload videos directly, or set `BP_PROFILE=bp_wh_auto_calib` and select `COMPOSE_PROFILES_WH_AUTO_CALIB_3D` / `_MV3DT` to calibrate against RTSP streams. See [Calibration Generation](warehouse.md#calibration-generation).

### Extended Kafka/Redis service lists (non-`_MINIMAL`, any mode) — add

| Container | Role |
|---|---|
| `logstash` | Log ingestion pipeline |
| `kibana` | Dashboard UI |
| `vss-video-analytics-api` / `vss-video-analytics-api-mv3dt` | REST API for analytics data |

`elasticsearch`, `kibana`, `logstash`, `vss-video-analytics-api` are also deployed for `BP_PROFILE=bp_wh` (always — independent of deployment size). See [Phase 1](#phase-1-stack-snapshot) for the consolidated trigger table.

### `BP_PROFILE=bp_wh` only — adds

| Container | Role |
|---|---|
| `vss-rtvi-vlm` | Real-time VLM (Cosmos Reason) — **always local**; its self-named `rtvi-vlm` profile is included in `COMPOSE_PROFILES_WH_2D`. Warehouse uses RTVI VLM instead of the standalone VLM NIM path, so keep `VLM_MODE=none` and `VLM_NAME_SLUG=none`. `vss-agent` connects to RTVI VLM directly |
| `vss-alert-bridge` | Drives realtime VLM alerts (POST/DELETE `/api/v1/realtime`) |
| LLM NIM (container name = `LLM_NAME_SLUG`, e.g. `nemotron-3.5-lightning-30b-a3b`) | LLM inference — only when `LLM_MODE=local` |
| `vss-agent` | Orchestrator |
| `vss-agent-ui` | Next.js UI |
| `vss-va-mcp` | Video Analysis MCP server |
| `vss-haproxy-ingress` | Front-door on `HAPROXY_HOST_PORT` (default `7777`). Also deployed in kafka/redis extended (proxies kibana + analytics API there) and in `BP_PROFILE=bp_wh_auto_calib` (where it carries no route to the auto-calibration UI — reach that on port `5000`) |
| `phoenix` | Telemetry / observability |

> **No VLM NIM container.** VSS has two VLM paths: standalone VLM NIM (`VLM_MODE` / `VLM_NAME_SLUG`) and integrated RTVI VLM (`vss-rtvi-vlm`). The `BP_PROFILE=bp_wh` variant uses **RTVI VLM only** — `vss-agent` connects to it directly. Keep `VLM_MODE=none` in the active `generated.env`. Kafka/Redis and auto-calibration warehouse variants deploy no VLM.

## Container Health Check Settings

| Container | Start period | Interval | Retries | Impact if failing |
|---|---|---|---|---|
| `vss-configurator` / `-mv3dt` | **60 s** | 10 s | 30 | Streams not configured — perception gets no input, since nvstreamer waits on this being healthy |
| `elasticsearch` | **60 s** | 10 s | 60 | BEV index unavailable (3D); no overlays (2D extended); agent storage broken |
| `vss-rtvi-cv-bev-fusion` (MV3DT) | 10 s | 2 s | 30 | Probes for `/tmp/fusion_ready`. Unhealthy means fusion never came up — no `mdx-bev`, so the Phase 5 sync check has nothing to read |

> `vss-configurator` failing in the **first 60 seconds** is expected — do not flag this as an error.

> **`vss-broker-health-check` is a one-shot job, not a long-running healthchecked service.** It has `restart: "no"`, polls the broker up to `MAX_RETRIES=60` every `RETRY_INTERVAL=2` s, and exits. Dependents wait on `service_completed_successfully`. So the healthy state is **`Exited (0)`** — treat `Up` as transient and a **non-zero exit** as the failure. If it exits non-zero, nothing downstream starts.

> **`vss-rtvi-cv` / `vss-rtvi-cv-mv3dt` define no healthcheck.** `docker ps` will never show `(healthy)` for perception — judge it from FPS/PERF log output (Phase 2), not container health.

## Key Log Patterns and Root Causes

| Log string | Container | Root cause |
|---|---|---|
| `model not found` / `No such file` | `vss-rtvi-cv` | `VSS_DATA_DIR` wrong or models not present |
| `CUDA out of memory` | `vss-rtvi-cv` / LLM NIM / `vss-rtvi-vlm` | Too many streams or wrong device assignment — reduce `NUM_STREAMS` or change device IDs |
| `GST pipeline error` / `Failed to start pipeline` | `vss-rtvi-cv` | No valid RTSP input — check `vss-vios-nvstreamer` first |
| `Connection refused` on broker port | `vss-broker-health-check` | Kafka/Redis not listening — broker crashed |
| `RTSP connection failed` / `Cannot open resource` | `vss-vios-nvstreamer` | RTSP source (camera / video file) unreachable |
| `Health check failed` (after 60 s) | `vss-configurator` | Stream config bad — check `NUM_STREAMS`, `MODE` and `HARDWARE_PROFILE` in the active `generated.env` (not the checked-in `.env`, which holds none of them) |
| `Error adding sensor <name>. Received status code 400 from VMS` repeating on **one** name | `vss-configurator` | Partial stream registration — see [Partial stream registration](#partial-stream-registration-400-from-vms) below. The stack stays fully healthy while running fewer streams than `NUM_STREAMS` |
| `authentication required` / `401` | any | `NGC_CLI_API_KEY` invalid or expired |
| `no space left on device` | any | Disk full — free space before redeploy |
| `OOMKilled` (exit code 137) | any | Container OOM — check RAM (`free -h`) and GPU memory |

> **Don't `docker restart vss-rtvi-cv` to "fix" stream issues during normal operation.** The SDR-to-CV stream re-registration after a CV restart is fragile — it often drops streams instead of recovering them. If perception is misbehaving, better to do a full clean redeploy.

### Partial stream registration (`400 from VMS`)

**Symptom.** Every container is `Up` or `Exited (0)`, nothing is unhealthy or restarting — but
`docker logs vss-rtvi-cv | grep "Active sources"` reports fewer than `NUM_STREAMS`, and only the
registered cameras appear on `mdx-raw`. No documented container-state check catches this.

**Cause.** `vss-configurator` discovers all streams correctly (`final_stream_count` in its log
confirms the intended number), then adds them to VST **sequentially**. If a transient
`VST sensor add API unreachable` lands mid-sequence after the sensor was already created, the
retry receives `400` (`Sensor exists already`) and the configurator loops on that one name
indefinitely — without honoring its own "Retrying in 30 seconds" backoff — never reaching the
remaining sensors.

**Diagnose:**

```bash
# Which name is it stuck on, and what did it intend to add?
docker logs vss-configurator 2>&1 | grep -aoE "Error adding sensor [A-Za-z_0-9]+" | sort | uniq -c
docker logs vss-configurator 2>&1 | grep -a "final_stream_count"
# What actually made it into VST:
curl -s http://localhost:30888/vst/api/v1/sensor/list | grep -oE '"name" : "[^"]+"'
```

**Recover.** Add only the *missing* sensors directly to VST. Do not restart `vss-configurator`
(it re-discovers and hits the same `400` on the already-added sensors), and do not restart
`vss-rtvi-cv` — see the warning above. Take the `sensorUrl` values from the configurator's own
discovery line; each stream has its own nvstreamer port:

```bash
curl -X POST http://localhost:30888/vst/api/v1/sensor/add \
  -H 'Content-Type: application/json' \
  -d '{"name":"Camera_01","password":"","sensorUrl":"rtsp://vss-vios-nvstreamer:31557/nvstream/tmp/nv_streamer/videos/Camera_01.mp4","username":""}'
```

Space adds ~20 s apart — back-to-back adds can stall the perception pipeline during caps
negotiation. Perception attaches each new source dynamically; expect `0.00000` FPS at first
while RTSP falls back from UDP to TCP (`Could not receive any UDP packets ... Retrying using a
tcp connection`), then a climb to the source framerate. Re-check `Active sources` to confirm.

The configurator keeps looping after this (roughly a quarter of a core) until the next redeploy;
its config-file generation has already completed, so the loop is noise rather than a blocker.

## Elasticsearch Indices

Logstash indexes each `mdx-*` stream into **date-suffixed** indices — `index => "%{type}-YYYY-MM-DD"`.
There is no index or alias with the bare name; always query the wildcard (`mdx-bev*`), or a bare name
returns HTTP 404 `index_not_found_exception`.

| Index pattern | Data source | Contains | Used for |
|---|---|---|---|
| `mdx-bev*` | `vss-rtvi-cv` (3D Sparse4D) / `vss-rtvi-cv-bev-fusion` (MV3DT) | BEV frame data, camera timestamps in `info`, detected objects | 3D / MV3DT BEV sync check, object history |
| `mdx-raw*` | perception (2D and MV3DT only — 3D publishes straight to `mdx-bev`) | Raw detection events per frame | Debugging detection pipeline |
| `mdx-events*` | `vss-behavior-analytics` | ROI / tripwire / proximity events | Event history and UI |

`logstash` is what actually writes the documents into Elasticsearch; the column above names the
service whose data lands there. Behavior analytics **consumes** `mdx-bev` — it does not produce it.

Query latest record from any index:

```bash
curl -s "http://localhost:9200/<index-pattern>/_search?size=1" \
  -H 'Content-Type: application/json' \
  -d '{"sort":[{"timestamp":{"order":"desc"}}]}' | python3 -m json.tool | head -60
```

Check index health:

```bash
curl -s "http://localhost:9200/_cat/indices?v"
```

## Kafka / Redis Topic Reference

Producer/consumer depends on `MODE` — the 2D pairing does not hold for 3D or MV3DT:

| Topic | Producer | Consumer | Contains |
|---|---|---|---|
| `mdx-raw` (2D) | `vss-rtvi-cv` | `vss-behavior-analytics` | Raw bounding boxes + tracking IDs per frame |
| `mdx-raw` (MV3DT) | `vss-rtvi-cv-mv3dt` | `vss-rtvi-cv-bev-fusion` | Per-camera detections awaiting fusion |
| `mdx-bev` (3D) | `vss-rtvi-cv` (Sparse4D — **not** `mdx-raw`) | `vss-behavior-analytics` | BEV frames |
| `mdx-bev` (MV3DT) | `vss-rtvi-cv-bev-fusion` | `vss-behavior-analytics-mv3dt` | Fused BEV frames |
| `mdx-events` | `vss-behavior-analytics` | downstream / UI | ROI, tripwire, proximity events |
| `mdx-vlm-incidents` | `vss-rtvi-vlm` | `vss-alert-bridge`, `vss-agent` | Realtime VLM incident detections (`bp_wh` only) |

**Check messages are flowing (Kafka):** the image is `confluentinc/cp-kafka`, whose CLI tools have
**no `.sh` suffix**. Use the internal listener `kafka:29092` — `localhost:9092` is the EXTERNAL
listener and advertises `${HOST_IP}:9092`, which does not route from inside the container.

Pick the topic for your `MODE` from the table above — **`mdx-raw` is empty on 3D**, where perception
publishes straight to `mdx-bev`. Consuming `mdx-raw` on a healthy 3D stack blocks until
`--max-messages` is satisfied and reads as "perception is dead" when it is fine.

```bash
TOPIC=mdx-raw     # 2D / MV3DT;  use mdx-bev for 3D
docker exec kafka kafka-console-consumer \
  --bootstrap-server kafka:29092 \
  --topic "$TOPIC" --from-beginning --max-messages 5 --timeout-ms 10000 2>/dev/null
```

**Check messages are flowing (Redis):** same per-mode topic choice. `XREVRANGE` on a missing or
empty stream returns an empty array immediately rather than blocking, so an empty result here means
"nothing published to this key", not "command failed".

```bash
docker exec redis redis-cli XREVRANGE mdx-raw + - COUNT 3   # use mdx-bev for 3D
```

## GPU Device Assignment

| Role | Env variable | Default device | Notes |
|---|---|---|---|
| RT-CV perception (RT-DETR for 2D, Sparse4D for 3D, per-camera MV3DT for mv3dt) | `RT_CV_DEVICE_ID` | `0` | Always local. `vss-rtvi-cv-bev-fusion` is **not** placed by this var — it is CPU-only |
| RTVI VLM | `RT_VLM_DEVICE_ID` | `1` | Always local; `bp_wh` only |
| LLM NIM (dedicated) | `LLM_DEVICE_ID` | `2` | `bp_wh` + `LLM_MODE=local` |

`LLM_MODE`: `local` | `remote` | `none` (for `MODE=2d`; `3d` / `mv3dt` accept only `none`). RTVI VLM has no mode — it is always deployed locally for `BP_PROFILE=bp_wh`. The `BP_PROFILE=bp_wh_auto_calib` variant uses no GPU for perception or LLM.

Check per-GPU process load:

```bash
nvidia-smi --query-compute-apps=gpu_uuid,pid,process_name,used_gpu_memory \
  --format=csv,noheader
```

## Service Access Points

Expected access points after a successful deploy.

**Standard (bare-metal / VM with reachable IP):**

```
HAProxy:             http://<host_ip>:7777
VSS UI:              http://<host_ip>:7777/            (bp_wh only; 503 otherwise)
VST:                 http://<host_ip>:7777/vst/        (proxied) or http://<host_ip>:30888/vst/
Kibana:              http://<host_ip>:7777/kibana      (direct: http://<host_ip>:5601/kibana)
Video Analytics API: http://<host_ip>:7777/video-analytics-api   (direct: :8081)
NvStreamer:          http://<host_ip>:31000            (no HAProxy route)
Grafana:             http://<host_ip>:35000            (no HAProxy route; not deployed for MV3DT)
Auto-calibration UI: http://<host_ip>:5000             (no HAProxy route; bp_wh_auto_calib)
```

**Brev (secure-link domain):**

The domain is `brevlab.com` on classic Brev, or `apps.run.brev.nvidia.com` on Skybridge/NetBird
instances (`netbird status -d` shows a `skybridge` / `brev.nvidia.com` marker). Substitute the
right one below.

```
Access Points (Brev):

HAProxy:             https://7777-<BREV_ENV_ID>.<brev-domain>
VSS UI:              https://7777-<BREV_ENV_ID>.<brev-domain>
VST:                 https://7777-<BREV_ENV_ID>.<brev-domain>/vst/
Kibana:              https://7777-<BREV_ENV_ID>.<brev-domain>/kibana
Video Analytics API: https://7777-<BREV_ENV_ID>.<brev-domain>/video-analytics-api
NvStreamer:          https://31000-<BREV_ENV_ID>.<brev-domain>

Brev Secure Links — only the ingress port is required:
  Port 7777  (HAProxy)     → https://7777-<BREV_ENV_ID>.<brev-domain>    [required]
  Port 30888 (VST direct)  → https://30888-<BREV_ENV_ID>.<brev-domain>   [optional]
  Port 31000 (NvStreamer)  → https://31000-<BREV_ENV_ID>.<brev-domain>   [optional]
  Port 35000 (Grafana)     → https://35000-<BREV_ENV_ID>.<brev-domain>   [optional]

HAProxy-routed paths (/, /vst, /storage, /kibana, /elasticsearch, /api, /chat,
/websocket, /static, /alert-bridge, /video-analytics-api, /behavior-analytics,
/rtvi-cv, /rtvi-vlm, /phoenix, /va-mcp) all go through the port-7777 secure link.
Direct-port services (NvStreamer, Grafana, auto-calibration UI) each need their own
secure link opened in the Brev dashboard.

Known limitation: VST live/recorded video playback does not render through a Brev
secure link — the UI loads and streams/recordings list correctly.
```

If URLs still show the old `http://...:7777` form, the `VSS_PUBLIC_*` overrides were not applied — see [`warehouse.md` § Brev Secure Link Overrides](warehouse.md#brev-secure-link-overrides).

VST **is** proxied: HAProxy routes `/vst` and `/vst/…` to `vst-ingress`, and `/storage` rewrites to `/vst/storage`. Port `30888` remains published for direct access.

For the full HAProxy ingress route table, direct-port diagnostics table, and
the `h_main` Host-header ACL rules, see
[`warehouse.md` § Access Points](warehouse.md#access-points). The canonical
tables live there to avoid drift when ports/services change.

## BEV Sync Thresholds

| Drift | Status |
|---|---|
| ≤ 34 ms | SYNCHRONIZED — healthy |
| 34 ms – 67 ms | WARNING — monitor; may affect 3D fusion accuracy |
| > 67 ms | OUT OF SYNC — restart `vss-vios-nvstreamer` / `vss-vios-nvstreamer-mv3dt`; verify RTSP sources |

## Documentation Reference

Use the version-agnostic **`/latest/`** paths — they track whatever release is published, so
they neither 404 before 3.3.0 ships nor go stale after. (`/latest/` serves 3.2.1 today; a pinned
`/3.3.0/` path 404s until that docs site publishes.)

- VSS docs root: https://docs.nvidia.com/vss/latest/index.html
- Warehouse overview: https://docs.nvidia.com/vss/latest/warehouse-docs/warehouse-toc.html
- 2D profile: https://docs.nvidia.com/vss/latest/warehouse-docs/2D-profile.html
- 2D profile with Agents: https://docs.nvidia.com/vss/latest/warehouse-docs/2D-profile-with-agents.html
- 3D profile: https://docs.nvidia.com/vss/latest/warehouse-docs/3D-profile.html
- RT-DETR model (2D): https://docs.nvidia.com/vss/latest/warehouse-docs/2D-single-camera-detection-and-tracking-RTDETR.html
- Sparse4D model (3D): https://docs.nvidia.com/vss/latest/warehouse-docs/3D-multi-camera-detection-and-tracking-Sparse4D.html
- MV3DT (multi-view 3D tracking): https://docs.nvidia.com/vss/latest/warehouse-docs/3D-multi-camera-detection-and-tracking-MV3DT.html

> The previous `RT-DETR.html` / `Sparse4D.html` / `mv3dt-profile.html` filenames in this list were
> wrong — they 404 at every published version. The pages above are the real ones, verified live.

---

## Setup

Before starting, collect two pieces of information (ask if unknown):

1. **`<repo>`** — path to the `video-search-and-summarization` checkout. All compose commands run from `<repo>/deploy/docker/`, with `-f compose.yml -f services/infra/compose-no-turn-tcp-relay.yml --env-file containers.env --env-file industry-profiles/warehouse-operations/.env --env-file industry-profiles/warehouse-operations/generated.env`. Cleanup reads `generated.env` because it carries the runtime data paths. Treat `<repo>` as a placeholder you replace before running each command (or `export REPO=<absolute-path>` and use `$REPO`).
2. **`MODE`** — `2d`, `3d`, or `mv3dt`. Read it from the active env file, which is authoritative: Compose interpolates `MODE` from there to select the profile lists, and it works whether or not any container is up.

```bash
grep "^MODE=" $REPO/deploy/docker/industry-profiles/warehouse-operations/generated.env \
  || grep "^MODE=" $REPO/deploy/docker/industry-profiles/warehouse-operations/overrides.env
```

To confirm against what is actually running, inspect the **configurator** — `MODE` is injected there, **not** into perception, so `docker inspect vss-rtvi-cv` returns nothing even on a healthy stack:

```bash
docker inspect vss-configurator --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
  | grep -i "^MODE="
# MV3DT: use vss-configurator-mv3dt
```

`vss-rtvi-cv` is the same container in 2D and 3D — you cannot tell them apart by container name alone. MV3DT uses `vss-rtvi-cv-mv3dt` instead — if that container exists, MODE is `mv3dt`.

---

## Phase 1: Stack Snapshot

Get the full picture of what is and isn't running.

```bash
echo "=== Stack Snapshot: $(date) ==="
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.RunningFor}}\t{{.Ports}}'
echo ""
echo "--- Exited / missing containers ---"
docker ps -a --filter "status=exited" --filter "status=dead" \
  --format 'table {{.Names}}\t{{.Status}}\t{{.ExitCode}}'
```

**Expected long-running containers (flag any missing or restarting).** One-shot jobs are *not* in this table — see the note below it; classifying a completed gate as missing is the most common false positive here.

| Variant | Required containers |
|---|---|
| 2D / 3D Kafka/Redis variants | broker (`kafka` and/or `redis`), `vss-vios-nvstreamer`, `vss-rtvi-cv`, `vss-configurator`, `vss-behavior-analytics`, `vss-turnserver`, the `vss-vios-*` VST stack + `sdr-controller` |
| 3D extra | `vss-rtvi-cv-config-adaptor` |
| MV3DT Kafka/Redis variants | broker, `vss-vios-nvstreamer-mv3dt`, `vss-rtvi-cv-mv3dt`, `vss-rtvi-cv-bev-fusion`, `mosquitto`, `vss-configurator-mv3dt`, `vss-behavior-analytics-mv3dt`, `vss-turnserver`, the `vss-vios-*` VST stack + `sdr-controller` |
| `BP_PROFILE=bp_wh_auto_calib` | `vss-vios-nvstreamer` / `vss-vios-nvstreamer-mv3dt`, `vss-configurator` / `vss-configurator-mv3dt`, `vss-auto-calibration`, `vss-auto-calibration-ui`, `vss-haproxy-ingress`, `redis`, `vss-turnserver`, VST stack (subset) — no broker, no broker health-check gate, no perception, no analytics |
| `BP_PROFILE=bp_wh` extra | `vss-rtvi-vlm`, `vss-alert-bridge`, `vss-agent`, `vss-agent-ui`, `vss-va-mcp`, `phoenix`, monitoring (`grafana`, `prometheus`, `dcgm-exporter`, plus `<project>-node-exporter-1` / `<project>-cadvisor-1`), LLM NIM (container name = `LLM_NAME_SLUG`) when `LLM_MODE=local` |
| Extended (kafka/redis, any mode) extra | `logstash`, `kibana`, `vss-video-analytics-api` / `vss-video-analytics-api-mv3dt`; monitoring too, but **2D/3D only** |
| `vss-haproxy-ingress` | `BP_PROFILE=bp_wh`, `BP_PROFILE=bp_wh_auto_calib`, **or** kafka/redis extended (any mode) |
| `elasticsearch` | `BP_PROFILE=bp_wh` (always), **or** kafka/redis extended (any mode). **A `…_MINIMAL` list does NOT deploy ES** |

**Expected `Exited (0)` — these are jobs, not services. A completed gate is a success, never a missing container:**

| Container | Why it exits |
|---|---|
| `vss-broker-health-check` | Polls the broker (`MAX_RETRIES=60`, `RETRY_INTERVAL=2` s), then exits. `restart: "no"`, and every dependent waits on `service_completed_successfully` — so `Exited (0)` is precisely what "the broker gate passed" looks like. It appears in the `docker ps -a` exited list on a **healthy** stack |
| `sdrc-init-dirs`, `sdrc-render-config`, `sdrc-wdm-env-from-config`, `sdrc-wait-for-redis`, `sdrc-wait-for-workloads` | SDR-controller setup / wait jobs |
| `sensor-bp-wait-bp-configurator` | Waits for the configurator before the sensor microservice starts |
| `vss-kafka-topics` | Creates the `mdx-*` topics |
| `vss-configurator-2d-init` / `-3d-init` / `-mv3dt-init` | Per-mode **broker readiness gate**, despite the name — polls Kafka/Redis with `MAX_RETRIES=60`, `RETRY_INTERVAL=2` s, then exits. Under `BP_PROFILE=bp_wh_auto_calib` the check no-ops and it exits `0` immediately. `vss-configurator` waits on it via `service_completed_successfully`. It renders no config |
| `vss-elasticsearch-init`, `vss-kibana-init` | Index templates / dashboard import |
| `vss-import-calibration-output` | Imports `calibration.json` |

Record as suspects only: containers that are **Down** or **Restarting**, long-running containers from the table above that are **missing**, and any container with a **non-zero** exit code. Do not open an investigation because a job from this second table shows `Exited`.

To get the authoritative expected-container list for the running deployment instead of reading it off a table, ask Compose.

> **Resolve and export `COMPOSE_PROFILES` first.** `generated.env` carries it as the literal
> `COMPOSE_PROFILES=${COMPOSE_PROFILES_WH_<VARIANT>}` — that is how the checked-in `overrides.env`
> ships it, so a straight copy inherits it — and not every Docker Compose version expands `${...}`
> inside an `--env-file` value. Unexpanded, it matches **no** service profiles and `config` returns a
> near-empty list — which reads as "almost nothing is deployed" and sends you chasing a
> non-existent outage. Run the resolve-env prelude below so the resolved value is exported
> into the same shell that runs Compose.

```bash
cd $REPO/deploy/docker

# --- resolve-env prelude (run once per shell, from $REPO/deploy/docker) ---
# A subshell, not `set -a; . file`: the warehouse .env holds an unquoted JSON value that
# shell quote-removal mangles, and the shell environment outranks --env-file in Compose
# interpolation, so the mangled value would silently win. Only these three are exported.
eval "$(
  set -a
  . industry-profiles/warehouse-operations/.env
  . industry-profiles/warehouse-operations/generated.env
  set +a
  printf 'COMPOSE_PROFILES=%q\nCOMPOSE_PROJECT_NAME=%q\n' \
    "$COMPOSE_PROFILES" "${COMPOSE_PROJECT_NAME:-}"
  # Only if the env files carry a key: overrides.env ships NGC_CLI_API_KEY='' , and
  # exporting that empty value would wipe a key already set in your shell.
  [ -n "${NGC_CLI_API_KEY:-}" ] && printf 'NGC_CLI_API_KEY=%q\n' "$NGC_CLI_API_KEY"
)"
export COMPOSE_PROFILES
[ -n "${COMPOSE_PROJECT_NAME:-}" ] && export COMPOSE_PROJECT_NAME
[ -n "${NGC_CLI_API_KEY:-}" ] && export NGC_CLI_API_KEY
# --- end prelude ---
echo "COMPOSE_PROFILES=$COMPOSE_PROFILES"   # must be a service list, not '${COMPOSE_PROFILES_WH_*}'

docker compose -f compose.yml -f services/infra/compose-no-turn-tcp-relay.yml \
  --env-file containers.env \
  --env-file industry-profiles/warehouse-operations/.env \
  --env-file industry-profiles/warehouse-operations/generated.env \
  config --format json | python3 -c 'import json,sys; [print(s.get("container_name","")) for s in json.load(sys.stdin)["services"].values()]' | sort
```

The same resolve-env prelude applies to **every** raw `docker compose` command in this reference —
`config`, `ps`, `logs`, `up`, `down`. If a Compose command reports far fewer services than you
expect, check `echo "$COMPOSE_PROFILES"` before concluding anything about the stack.

---

## Phase 2: Perception FPS

Check whether perception is producing output.

**2D / 3D** — same container regardless of MODE:

```bash
echo "--- Perception FPS (last 60 s) ---"
docker logs --since 60s vss-rtvi-cv 2>&1 | grep -aE "stream_name" | tail -10
echo "--- Active source count (must equal NUM_STREAMS) ---"
docker logs --since 60s vss-rtvi-cv 2>&1 | grep -a "Active sources" | tail -1
```

**MV3DT** — check per-camera perception and BEV Fusion separately:

```bash
echo "--- MV3DT Per-Camera Perception FPS ---"
docker logs --since 60s vss-rtvi-cv-mv3dt 2>&1 | grep -aE "stream_name" | tail -10
echo "--- MV3DT Active source count ---"
docker logs --since 60s vss-rtvi-cv-mv3dt 2>&1 | grep -a "Active sources" | tail -1
echo "--- BEV Fusion FPS ---"
docker logs --since 60s vss-rtvi-cv-bev-fusion 2>&1 | grep -i fps | tail -10
```

> **Match `stream_name`, not `fps`, for the DeepStream perception containers.** They print
> the string `FPS` only in a *header* row — `**PERF:  FPS 1 (Avg)	FPS 0 (Avg)` — while the
> numeric per-stream rows (`29.80000 (30.00634)	source_id : 3 stream_name Camera_01`)
> contain no `fps` at all. `grep -i fps` thus yields value-free header rows that look like
> healthy output regardless of actual throughput, and the "very low FPS" branch below can
> never trigger. The count of `FPS N` columns in that header *is* meaningful, though — it
> tracks the live source count. `vss-rtvi-cv-bev-fusion` is a separate binary and keeps the
> `fps` matcher.

- **FPS lines present and non-zero** → perception is running; issue is likely downstream (broker, analytics, BEV sync).
- **No FPS lines** → perception is stalled or not receiving streams. Proceed to Phase 3.
- **FPS present but very low** → GPU saturation or stream count too high. Check Phase 4.
- **FPS healthy but `Active sources` < `NUM_STREAMS`** → streams were never registered, not a perception fault. The containers will all look healthy. Go to [Key Log Patterns](#key-log-patterns-and-root-causes) and check `vss-configurator` for a repeating sensor-add error.
- **MV3DT: per-camera FPS OK but BEV Fusion FPS zero** → broker path problem, **not** MQTT: BEV Fusion consumes `mdx-raw` over Kafka/Redis and has no MQTT config. Confirm `vss-rtvi-cv-mv3dt` is publishing `mdx-raw` and that `vss-rtvi-cv-bev-fusion` can reach `kafka:29092` / `redis:6379`. (MQTT is used *between* per-camera trackers — suspect `mosquitto` when `vss-rtvi-cv-mv3dt` itself shows no FPS.)

---

## Phase 3: Per-Container Log Triage

For each container that is **Down**, **Restarting**, or suspected from Phase 1/2, run:

```bash
docker logs --tail 80 <container-name> 2>&1
```

Work through this order — earlier failures often cause downstream ones:

### 3.1 Broker

```bash
# Kafka
docker logs --tail 50 kafka 2>&1 | grep -E "ERROR|WARN|Exception" | tail -20
# Redis
docker logs --tail 50 redis 2>&1 | grep -E "ERROR|WARNING" | tail -20
```

If broker is unhealthy, all downstream services will fail. Fix broker first.

### 3.2 NvStreamer (VST source feed)

```bash
docker logs --tail 80 vss-vios-nvstreamer 2>&1 | grep -E "ERROR|error|fail|RTSP" | tail -20
```

Errors here → streams are not being served → perception gets no input.

### 3.3 Perception

**2D / 3D:**

```bash
docker logs --tail 100 vss-rtvi-cv 2>&1 | grep -E "ERROR|error|fail|GST|pipeline|model" | tail -30
```

**MV3DT:**

```bash
docker logs --tail 100 vss-rtvi-cv-mv3dt 2>&1 | grep -E "ERROR|error|fail|GST|pipeline|model" | tail -30
docker logs --tail 100 vss-rtvi-cv-bev-fusion 2>&1 | grep -E "ERROR|error|fail" | tail -20
docker logs --tail 50 mosquitto 2>&1 | grep -E "ERROR|error|fail" | tail -10
```

Common issues:
- `model not found` → `$VSS_DATA_DIR/models/` is missing or wrong path.
- `GST pipeline error` → stream input issue; check `vss-vios-nvstreamer` first.
- `CUDA out of memory` → GPU saturation; reduce `NUM_STREAMS`.
- MV3DT: MQTT connection errors in `vss-rtvi-cv-mv3dt` → check `mosquitto` container first.

### 3.4 Config Adaptor + SDR controller

There is no `vss-rtvi-cv-sdr` container in warehouse deployments — stream routing is handled by
`sdr-controller` (see 3.7).

```bash
# 3D only:
docker logs --tail 50 vss-rtvi-cv-config-adaptor 2>&1 | grep -E "ERROR|error|fail" | tail -20
```

### 3.5 Configurator

```bash
# 2D / 3D:
docker logs --tail 50 vss-configurator 2>&1 | grep -E "ERROR|error|fail" | tail -20
# MV3DT:
docker logs --tail 50 vss-configurator-mv3dt 2>&1 | grep -E "ERROR|error|fail" | tail -20
```

Note: `vss-configurator` / `vss-configurator-mv3dt` has a **60 s start period** — a health-check failure in the first minute is expected.

### 3.6 Behavior Analytics

```bash
# 2D / 3D:
docker logs --tail 50 vss-behavior-analytics 2>&1 | grep -E "ERROR|error|fail" | tail -20
# MV3DT:
docker logs --tail 50 vss-behavior-analytics-mv3dt 2>&1 | grep -E "ERROR|error|fail" | tail -20
```

### 3.7 VST / VIOS stack

```bash
for c in vss-vios-postgres vss-vios-sensor vss-vios-streamprocessing vss-vios-ingress sdr-controller; do
  echo "=== $c ==="
  docker logs --tail 30 "$c" 2>&1 | grep -E "ERROR|error|fail" | tail -10
done
```

### 3.8 `bp_wh` extras (RTVI VLM + agent)

Skip unless `BP_PROFILE=bp_wh`.

```bash
docker logs --tail 50 vss-rtvi-vlm     2>&1 | grep -E "ERROR|error|fail|CUDA" | tail -20
docker logs --tail 50 vss-alert-bridge 2>&1 | grep -E "ERROR|error|fail"      | tail -20
docker logs --tail 50 vss-agent        2>&1 | grep -E "ERROR|error|fail"      | tail -20
docker logs --tail 50 vss-agent-ui     2>&1 | grep -E "ERROR|error|fail"      | tail -20
docker logs --tail 50 vss-haproxy-ingress 2>&1 | grep -E "ERROR|error|fail"   | tail -20
# LLM NIM container name = LLM_NAME_SLUG from .env (e.g. nemotron-3.5-lightning-30b-a3b)
# Warehouse industry-profile compose commands read from .env directly
# Prefer generated.env after a deployment; fall back to overrides.env on a fresh checkout.
ENV_FILE="$REPO/deploy/docker/industry-profiles/warehouse-operations/generated.env"
[ -f "$ENV_FILE" ] || ENV_FILE="$REPO/deploy/docker/industry-profiles/warehouse-operations/overrides.env"
LLM_SLUG=$(grep '^LLM_NAME_SLUG=' "$ENV_FILE" | cut -d= -f2 | tr -d '"')
docker logs --tail 50 "$LLM_SLUG" 2>&1 | grep -E "ERROR|error|fail|CUDA" | tail -20
```

---

## Phase 4: System Resources

```bash
echo "=== System Resources: $(date) ==="

echo "--- GPU ---"
nvidia-smi --query-gpu=index,name,utilization.gpu,utilization.memory,memory.used,memory.total \
  --format=csv,noheader

echo "--- CPU ---"
top -bn1 | grep "Cpu(s)"

echo "--- Memory ---"
free -h

echo "--- Disk ---"
df -h / /tmp 2>/dev/null
```

**Flag these as root causes if observed:**

| Finding | Root cause |
|---|---|
| GPU memory usage ≥ 90 % | Too many streams for the GPU — reduce `NUM_STREAMS`, or move LLM/VLM to a different `LLM_DEVICE_ID` / `RT_VLM_DEVICE_ID` |
| GPU utilization sustained at 100 % | Same as above |
| Disk < 10 GB free on `/` | Insufficient space — containers may fail to write logs or temp files |
| RAM < 8 GB free | Memory pressure — broker or analytics OOM likely |

---

## Phase 5 (3D / MV3DT extended only): BEV Camera Timestamp Sync

For `MODE=3d` or `MODE=mv3dt` **on an extended (non-`_MINIMAL`) service list**, check that all cameras contributing to the BEV frame are synchronized. Skip this phase in 3D/MV3DT minimal: `elasticsearch` is not deployed there, so `mdx-bev` is never persisted and the query below will fail with a connection error.

Logstash writes **date-suffixed** indices (`index => "%{type}-YYYY-MM-DD"`), so query the wildcard
`mdx-bev*` — a request for the bare name `mdx-bev` returns HTTP 404 `index_not_found_exception`.
The response is written to a file rather than piped, because `curl … | python3 - <<'EOF'` does not
work: the heredoc replaces the pipe as stdin, so the script never sees the JSON and dies with
`JSONDecodeError`.

```bash
curl -s "http://localhost:9200/mdx-bev*/_search?size=1" \
  -H 'Content-Type: application/json' \
  -d '{"sort":[{"timestamp":{"order":"desc"}}]}' -o /tmp/bev.json

python3 - /tmp/bev.json << 'EOF'
import json, sys
from datetime import datetime

with open(sys.argv[1]) as f:
    data = json.load(f)
hits = data.get("hits", {}).get("hits", [])
if not hits:
    print("mdx-bev: no records found — Elasticsearch may be down or index empty")
    sys.exit(0)

src = hits[0]["_source"]
info = src.get("info", {})
record_ts = src.get("timestamp", "unknown")

timestamps = {}
for cam, ts in info.items():
    try:
        timestamps[cam] = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        pass

if not timestamps:
    print("mdx-bev: no valid camera timestamps in info field")
    sys.exit(0)

times = list(timestamps.values())
min_ts, max_ts = min(times), max(times)
drift_ms = (max_ts - min_ts).total_seconds() * 1000

print(f"mdx-bev record timestamp : {record_ts}")
print(f"Cameras checked          : {len(timestamps)}")
print(f"Earliest                 : {min_ts.isoformat()}")
print(f"Latest                   : {max_ts.isoformat()}")
print(f"Max drift                : {drift_ms:.1f} ms")

if drift_ms <= 34:
    print("STATUS: SYNCHRONIZED")
elif drift_ms <= 67:
    print("STATUS: WARNING — drift 34–67 ms, monitor closely")
    for cam, ts in sorted(timestamps.items(), key=lambda x: x[1]):
        delta = (ts - min_ts).total_seconds() * 1000
        print(f"  {cam}: {ts.isoformat()}  (+{delta:.1f} ms)")
else:
    print("STATUS: OUT OF SYNC — drift exceeds 67 ms")
    for cam, ts in sorted(timestamps.items(), key=lambda x: x[1]):
        delta = (ts - min_ts).total_seconds() * 1000
        print(f"  {cam}: {ts.isoformat()}  (+{delta:.1f} ms)")
EOF
```

- **SYNCHRONIZED** (≤ 34 ms) → BEV fusion healthy; issue is elsewhere.
- **WARNING** (34–67 ms) → minor drift; monitor. Check `docker logs vss-vios-nvstreamer` (3D) / `docker logs vss-vios-nvstreamer-mv3dt` (MV3DT) for lagging streams.
- **OUT OF SYNC** (> 67 ms) → restart `vss-vios-nvstreamer` / `vss-vios-nvstreamer-mv3dt`; verify RTSP source health for drifting cameras.
- **No records found** → `elasticsearch` container may be down or the `mdx-bev` index has not been written to yet.

---

## Phase 6: Root Cause Summary

After completing Phases 1–5, state the root cause clearly before proposing any action. Use this decision table:

| Evidence | Root cause | Proposed fix |
|---|---|---|
| Container exited, exit code non-zero | Container crash — see its logs | Fix config or missing file; redeploy |
| `model not found` in `vss-rtvi-cv` logs | `VSS_DATA_DIR` wrong, or `models/` missing/unwritable | Fix `VSS_DATA_DIR` in the active `generated.env` (it is not in the checked-in `.env`), then ensure `$VSS_DATA_DIR/models` exists and is `0777` — RT-CV weights are downloaded there by ds-start phase 0, not shipped in the app data (see `warehouse.md` → App Data) |
| `CUDA out of memory` on `vss-rtvi-cv` | Too many streams for GPU | Reduce `NUM_STREAMS`; redeploy |
| `CUDA out of memory` on LLM NIM or `vss-rtvi-vlm` | LLM and RTVI VLM colliding on the same GPU | Adjust `LLM_DEVICE_ID` / `RT_VLM_DEVICE_ID`; redeploy |
| Broker (Kafka/Redis) down | All downstream services lose messaging | Fix broker; redeploy |
| `vss-vios-nvstreamer` / `vss-vios-nvstreamer-mv3dt` errors / no RTSP | Streams not reaching perception | Fix stream config; redeploy |
| BEV OUT OF SYNC (3D / MV3DT) | One or more camera feeds lagging | Restart `vss-vios-nvstreamer` / `vss-vios-nvstreamer-mv3dt`; check camera RTSP sources |
| `mosquitto` down / MQTT connection refused (MV3DT) | Cross-camera messaging broken — BEV Fusion cannot receive per-camera detections | Fix mosquitto container; redeploy |
| `vss-rtvi-cv-bev-fusion` OOM or no output (MV3DT) | BEV Fusion cannot fuse per-camera detections | This container uses **no GPU** — check host RAM (`free -h`) and broker connectivity. Verify `mdx-raw` is being produced by `vss-rtvi-cv-mv3dt`, and that `MAX_EXPECTED_SENSORS` (= `NUM_STREAMS`) and `SENSOR_TIMEOUT_MS` match the deployed camera count. If `mdx-raw` is empty, the fault is upstream on `vss-rtvi-cv-mv3dt` (row above) |
| GPU 100 % sustained, low FPS | GPU oversaturated | Reduce `NUM_STREAMS`; redeploy |
| Disk < 10 GB | Write failures / container OOM | Free disk space; redeploy |
| `vss-configurator` failing after 60 s | Misconfigured streams or hardware profile | Verify the effective `.env` + `generated.env` values; redeploy |
| `vss-haproxy-ingress` up but UI 502 / report links broken | `EXTERNAL_IP` / `HAPROXY_HOST_PORT` not browser-reachable | Set `EXTERNAL_IP` to a real reachable hostname and verify `VSS_PUBLIC_PORT` matches the host-published ingress port (see `warehouse.md` Phase 5); redeploy |
| Brev: UI loads but API calls fail / mixed-content errors in browser console | `VSS_PUBLIC_*` overrides not applied — browser-facing URLs still use `http://7777-<BREV_ENV_ID>.brevlab.com:7777` instead of `https://7777-<BREV_ENV_ID>.brevlab.com` | Apply [Brev secure link overrides](warehouse.md#brev-secure-link-overrides): set `VSS_PUBLIC_HTTP_PROTOCOL=https`, `VSS_PUBLIC_WS_PROTOCOL=wss`, `VSS_PUBLIC_HOST=7777-<BREV_ENV_ID>.brevlab.com`, `VSS_PUBLIC_PORT=443`; redeploy |
| Brev: HAProxy returns 404 on all paths | `Host:` header in the request doesn't match HAProxy `h_main` ACL | Verify `VSS_PUBLIC_HOST` matches the Brev secure-link domain (`7777-<BREV_ENV_ID>.brevlab.com`); redeploy |
| Brev: WebSocket chat connection refused / falls back to HTTP | `VSS_PUBLIC_WS_PROTOCOL` still set to `ws` instead of `wss`, or `VSS_PUBLIC_PORT` not `443` | Fix the active `generated.env` overrides and redeploy |
| `error from registry: Incorrect Repository Format` during `docker compose up` | Docker version outside the tested range | Re-pin Docker into **[28.3.3, 29.5.0)** — the known-good set is CE 29.4.3 / buildx 0.33.0 / compose 5.1.3 / containerd 2.2.3 (warehouse.md §2.2). Do not downgrade an already-in-range engine. |
| Compose reports missing images / an image override in `containers.env` had no effect | `--env-file containers.env` was omitted from the compose invocation | Pass all three env files (`containers.env`, warehouse `.env`, `generated.env`) and both `-f` files (`compose.yml`, `services/infra/compose-no-turn-tcp-relay.yml`); redeploy |
| `/perception-sdr` or `/rtvi-embed` returns 503 through HAProxy | Those backends are not deployed by any warehouse variant | Not a fault — ignore |

Present the summary in this format:

```
=== Debug Summary ===
Root cause : <one-line description>
Evidence   : <which container / log line / metric revealed it>
Proposed fix: <what needs to change>
Requires redeploy: yes / no
```

---

## Phase 7: Redeploy (if required)

**Ask the user before taking any action:**

> "Root cause identified: `<root cause>`. Proposed fix: `<fix>`. Should I apply the fix and redeploy now? (yes / no)"

Only proceed on explicit **"yes"**.

If yes:

1. Apply deployment-specific fixes in the active warehouse `generated.env` (initialized from `overrides.env`), edit shared service defaults only when the defect is truly service-wide, or correct the missing resource. Keep the checked-in warehouse `.env` and `overrides.env` unchanged.
2. Tear down:

```bash
cd <repo>/deploy/docker

# Resolve the project name from generated.env FIRST. It is not in your shell, so a bare
# "${COMPOSE_PROJECT_NAME:-vss}" always falls back to `vss` — and on a host that renamed the
# project (overrides.env invites this to run two stacks side by side) that tears down nothing
# while reporting success. `:?` fails loudly instead of guessing.
# Run the resolve-env prelude first (see Phase 1: Stack Snapshot).
: "${COMPOSE_PROJECT_NAME:?not set by generated.env — resolve it before tearing down}"

# Confirm the resolved project is the one actually running before removing anything.
echo "Tearing down Compose project: $COMPOSE_PROJECT_NAME"
docker compose -p "$COMPOSE_PROJECT_NAME" ps --format '{{.Name}}' | head

# Project-scoped teardown: with -p the removal does not depend on COMPOSE_PROFILES
# resolving, so an unexpanded list cannot leave containers behind.
# `-v` drops EVERY named volume labeled with this project, not just the obvious data ones
# (vios_pg_data, logstash-libs, phoenix-data, vss-turn-password). That INCLUDES the model
# caches — RT-VLM HF/NGC caches, the per-model LLM NIM cache, the Triton model repo — so the
# next bring-up re-downloads multiple GB. Omit -v if you want to keep warm caches.
# `docker volume prune -f` is not a substitute: on Docker 23+ a bare prune removes anonymous
# volumes only. Note kafka/Elasticsearch DATA is bind-mounted under $VSS_DATA_DIR/data_log and
# survives `down -v` either way — the cleanup_all_datalog.sh line below is what clears it.
docker compose -p "$COMPOSE_PROJECT_NAME" down -v --remove-orphans
docker volume prune -f
docker system prune -f
bash ./scripts/cleanup_all_datalog.sh -e industry-profiles/warehouse-operations/generated.env
```

3. Bring up:

```bash
LOG=${LOG:-/tmp/warehouse-blueprint.log}
cd <repo>/deploy/docker

# Resolve COMPOSE_PROFILES into this shell before Compose runs -- generated.env stores it
# as the literal ${COMPOSE_PROFILES_WH_*}, which not every Compose version expands.
# Run the resolve-env prelude first (see Phase 1: Stack Snapshot).
case "$COMPOSE_PROFILES" in
  ''|*'${'*) echo "COMPOSE_PROFILES did not resolve: '$COMPOSE_PROFILES'" >&2; exit 1 ;;
esac

: "${NGC_CLI_API_KEY:?not set — export it, or put it in generated.env, before logging in}"
printf '%s' "$NGC_CLI_API_KEY" | docker login --username '$oauthtoken' --password-stdin nvcr.io
nohup docker compose -f compose.yml -f services/infra/compose-no-turn-tcp-relay.yml \
  --env-file containers.env \
  --env-file industry-profiles/warehouse-operations/.env \
  --env-file industry-profiles/warehouse-operations/generated.env \
  up --detach --pull always --force-recreate --build \
  > "$LOG" 2>&1 &
echo "Compose PID $! — logging to $LOG"
```

4. Monitor until all required containers show `Up`:

```bash
tail -20 "$LOG"
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
```

5. Re-run **Phase 2** (FPS check) and, for 3D / MV3DT, **Phase 5** (BEV sync) to confirm the issue is resolved.

If the issue persists after redeploy, consult the [Documentation Reference](#documentation-reference) links above and `warehouse.md` → Troubleshooting.
