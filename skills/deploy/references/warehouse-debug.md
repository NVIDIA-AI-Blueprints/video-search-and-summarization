# Warehouse Debug Reference

Live debugging of an **already-running** VSS Warehouse deployment. Triage container health, perception FPS, GPU/CPU/disk resources, broker connectivity, and (3D only) BEV camera timestamp synchronization via Elasticsearch. Identify root cause, propose a fix, then ask the user before applying it.

Companion to [`warehouse.md`](warehouse.md). Use this reference when the stack is already up but something is wrong — low FPS, containers restarting, streams missing, BEV out of sync, or general unhealthy state. For first-time install / redeploy / tear-down, go to `warehouse.md`.

Reference tables (container map, deps, log patterns, ES indices, GPU layout, endpoints, BEV thresholds) are in the top half; operational triage phases are in the bottom half.

---

## Container Dependency Chain

Failures propagate downstream. Always triage in this order — a broken upstream container is the root cause of all containers below it failing.

```
broker (kafka / redis)
  └── vss-broker-health-check
        └── vss-vios-nvstreamer
              └── vss-rtvi-cv                  (perception — 2D RT-DETR or 3D Sparse4D, same container)
                    ├── vss-rtvi-cv-sdr        (stream data router)
                    ├── vss-rtvi-cv-config-adaptor (3D only — DeepStream config adaptor)
                    ├── vss-configurator       (blueprint / stream / hardware config)
                    └── vss-behavior-analytics (ROI, tripwire, proximity events)
                          └── (extended only: logstash, kibana, vss-video-analytics-api)

VST (VIOS) stack — independent of perception, feeds RTSP into it:
  vss-vios-postgres → vss-vios-sensor / vss-vios-streamprocessing
                    → vss-vios-sdr / vss-vios-mcp / vss-vios-ingress / vss-vios-envoy

elasticsearch — required for mdx-bev index (3D), bounding-box overlays (2D extended), and vss-agent storage

bp_wh-only stack (RTVI VLM + agent):
  vss-rtvi-embed
  vss-rtvi-vlm                                  (always local — no remote/none option)
  vss-alert-bridge ← depends on vss-rtvi-vlm
  LLM NIM (varies — see below)
  vss-agent ← depends on LLM, vios
  vss-agent-ui ← depends on vss-agent
  vss-va-mcp
  phoenix
  vss-haproxy-ingress                           (front-door for the VSS UI on HAPROXY_PORT)
```

## Full Container List by Profile

`MODE` (`2d` / `3d`) and `BP_PROFILE` (`bp_wh` / `bp_wh_kafka` / `bp_wh_redis`) select the active compose-profile slice. Perception, behavior analytics, configurator, and nvstreamer use the **same container names** in 2D and 3D — no `-2d` / `-3d` suffix.

### Warehouse CV core (all warehouse profiles)

| Container | Role |
|---|---|
| `kafka` or `redis` (`STREAM_TYPE`) | Message broker |
| `vss-broker-health-check` | Gate — waits for broker before releasing dependents |
| `vss-vios-nvstreamer` | RTSP stream server |
| `vss-rtvi-cv` | DeepStream perception (RT-DETR for 2D, Sparse4D for 3D) |
| `vss-rtvi-cv-sdr` | Stream data router |
| `vss-rtvi-cv-config-adaptor` | DeepStream config adaptor (3D only) |
| `vss-configurator` | Stream and hardware config |
| `vss-behavior-analytics` | ROI / tripwire / proximity analytics |
| `vss-vios-postgres` / `-sensor` / `-streamprocessing` / `-sdr` / `-mcp` / `-ingress` / `-envoy` | VST stack |
| `vss-auto-calibration` / `vss-auto-calibration-ui` | Camera auto-calibration |

### Extended profile only (`MINIMAL_PROFILE=""`) — adds

| Container | Role |
|---|---|
| `elasticsearch` | Search / analytics store (also required for 3D BEV) |
| `logstash` | Log ingestion pipeline |
| `kibana` | Dashboard UI |
| `vss-video-analytics-api` | REST API for analytics data |

### `bp_wh` only — adds

| Container | Role |
|---|---|
| `vss-rtvi-vlm` | Real-time VLM (Cosmos Reason) — **always local**, no mode toggle |
| `vss-rtvi-embed` | RTVI embedding service |
| `vss-alert-bridge` | Drives realtime VLM alerts (POST/DELETE `/api/v1/realtime`) |
| LLM NIM (container name = `LLM_NAME_SLUG`, e.g. `nvidia-nemotron-nano-9b-v2`) | LLM inference — only when `LLM_MODE=local` / `local_shared` |
| `vss-agent` | Orchestrator |
| `vss-agent-ui` | Next.js UI |
| `vss-va-mcp` | Video Analysis MCP server |
| `vss-haproxy-ingress` | Front-door on `HAPROXY_PORT` (default `7777`) |
| `phoenix` | Telemetry / observability |

> **No `vlm-nim` container.** The warehouse blueprint runs **only `vss-rtvi-vlm`** for vision-language inference, and it is always local. Do not search for a VLM NIM container — it does not exist in this stack.

## Container Health Check Settings

| Container | Start period | Interval | Retries | Impact if failing |
|---|---|---|---|---|
| `vss-broker-health-check` | 10 s | 5 s | 12 | All downstream containers will not start |
| `vss-configurator` | **60 s** | 10 s | 6 | Streams not configured — perception gets no input |
| `vss-rtvi-cv` | 30 s | 10 s | 6 | No detections produced |
| `elasticsearch` | 30 s | 10 s | 5 | BEV index unavailable (3D); no overlays (2D extended); agent storage broken |

> `vss-configurator` failing in the **first 60 seconds** is expected — do not flag this as an error.

## Key Log Patterns and Root Causes

| Log string | Container | Root cause |
|---|---|---|
| `model not found` / `No such file` | `vss-rtvi-cv` | `VSS_DATA_DIR` wrong or models not present |
| `CUDA out of memory` | `vss-rtvi-cv` / LLM NIM / `vss-rtvi-vlm` | Too many streams or wrong device assignment — reduce `NUM_STREAMS` or change device IDs |
| `GST pipeline error` / `Failed to start pipeline` | `vss-rtvi-cv` | No valid RTSP input — check `vss-vios-nvstreamer` first |
| `Connection refused` on broker port | `vss-broker-health-check` | Kafka/Redis not listening — broker crashed |
| `RTSP connection failed` / `Cannot open resource` | `vss-vios-nvstreamer` | RTSP source (camera / video file) unreachable |
| `Health check failed` (after 60 s) | `vss-configurator` | Stream config bad — check `.env` `BP_PROFILE` and `NUM_STREAMS` |
| `authentication required` / `401` | any | `NGC_CLI_API_KEY` invalid or expired |
| `no space left on device` | any | Disk full — free space before redeploy |
| `OOMKilled` (exit code 137) | any | Container OOM — check RAM (`free -h`) and GPU memory |

## Elasticsearch Indices

| Index | Written by | Contains | Used for |
|---|---|---|---|
| `mdx-bev` | `vss-behavior-analytics` (3D) | BEV frame data, camera timestamps in `info`, detected objects | 3D BEV sync check, object history |
| `mdx-raw` | perception via broker | Raw detection events per frame | Debugging detection pipeline |
| `mdx-events` | `vss-behavior-analytics` | ROI / tripwire / proximity events | Event history and UI |

Query latest record from any index:

```bash
curl -s "http://localhost:9200/<index>/_search?size=1" \
  -H 'Content-Type: application/json' \
  -d '{"sort":[{"timestamp":{"order":"desc"}}]}' | python3 -m json.tool | head -60
```

Check index health:

```bash
curl -s "http://localhost:9200/_cat/indices?v"
```

## Kafka / Redis Topic Reference

| Topic | Producer | Consumer | Contains |
|---|---|---|---|
| `mdx-raw` | `vss-rtvi-cv` | `vss-behavior-analytics` | Raw bounding boxes + tracking IDs per frame |
| `mdx-events` | `vss-behavior-analytics` | downstream / UI | ROI, tripwire, proximity events |
| `mdx-vlm-incidents` | `vss-rtvi-vlm` | `vss-alert-bridge`, `vss-agent` | Realtime VLM incident detections (`bp_wh` only) |

**Check messages are flowing (Kafka):**

```bash
docker exec kafka kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 \
  --topic mdx-raw --from-beginning --max-messages 5 2>/dev/null
```

**Check messages are flowing (Redis):**

```bash
docker exec redis redis-cli XREVRANGE mdx-raw + - COUNT 3
```

## GPU Device Assignment

| Role | `.env` variable | Default device | Notes |
|---|---|---|---|
| RT-CV perception (RT-DETR / Sparse4D) | `RT_CV_DEVICE_ID` | `0` | Always local |
| RTVI VLM | `RT_VLM_DEVICE_ID` | `1` | Always local; `bp_wh` only |
| LLM NIM (dedicated) | `LLM_DEVICE_ID` | `2` | `bp_wh` + `LLM_MODE=local` |
| LLM NIM colocated with RTVI VLM | `SHARED_LLM_VLM_DEVICE_ID` | `2` | `bp_wh` + `LLM_MODE=local_shared` |

`LLM_MODE`: `local` | `local_shared` | `remote` | `none`. RTVI VLM has no mode — always deployed locally for `bp_wh`.

Check per-GPU process load:

```bash
nvidia-smi --query-compute-apps=gpu_uuid,pid,process_name,used_gpu_memory \
  --format=csv,noheader
```

## Service Access Points

| Service | URL | Available in |
|---|---|---|
| **VSS UI (via HAProxy ingress)** | `http://<EXTERNAL_IP>:<HAPROXY_PORT>` (default `7777`) | `bp_wh` |
| VSS Agent API | `http://<HOST_IP>:8000` | `bp_wh` |
| VST / VIOS UI | `http://<HOST_IP>:30888/vst` | All |
| VST MCP | `http://<HOST_IP>:8001` | All |
| NvStreamer UI | `http://<HOST_IP>:31000` | All |
| Auto-Calibration UI | `http://<HOST_IP>:5000` | All |
| Elasticsearch API | `http://localhost:9200` | Extended / 3D |
| Phoenix telemetry | `http://<HOST_IP>:6006` | `bp_wh` |
| Kibana | `http://<HOST_IP>:5601` | Extended only |
| Video Analytics API | `http://<HOST_IP>:3002` | Extended only |

## BEV Sync Thresholds

| Drift | Status |
|---|---|
| ≤ 34 ms | SYNCHRONIZED — healthy |
| 34 ms – 67 ms | WARNING — monitor; may affect 3D fusion accuracy |
| > 67 ms | OUT OF SYNC — restart `vss-vios-nvstreamer`; verify RTSP sources |

## Documentation Reference

- Warehouse overview: https://docs.nvidia.com/vss/3.2.0/warehouse-docs/warehouse-toc.html
- 2D profile: https://docs.nvidia.com/vss/3.2.0/warehouse-docs/2D-profile.html
- 2D profile with Agents: https://docs.nvidia.com/vss/3.2.0/warehouse-docs/2D-profile-with-agents.html
- 3D profile: https://docs.nvidia.com/vss/3.2.0/warehouse-docs/3D-profile.html
- RT-DETR model (2D): https://docs.nvidia.com/vss/3.2.0/warehouse-docs/RT-DETR.html
- Sparse4D model (3D): https://docs.nvidia.com/vss/3.2.0/warehouse-docs/Sparse4D.html

---

## Setup

Before starting, collect two pieces of information (ask if unknown):

1. **`<repo>`** — path to the `video-search-and-summarization` checkout. All compose / cleanup commands run from `<repo>/deploy/docker/`, with `--env-file industry-profiles/warehouse-operations/.env`.
2. **`MODE`** — `2d` or `3d`. Detect automatically:

```bash
docker inspect --format '{{.Name}} {{range .Config.Env}}{{println .}}{{end}}' \
  $(docker ps -q vss-rtvi-cv 2>/dev/null) 2>/dev/null | grep -i "^MODE=" | head -1
```

If that returns nothing, fall back to reading `<repo>/deploy/docker/industry-profiles/warehouse-operations/.env`:

```bash
grep "^MODE=" <repo>/deploy/docker/industry-profiles/warehouse-operations/.env
```

`vss-rtvi-cv` is the same container in 2D and 3D — you cannot tell them apart by container name alone.

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

**Expected `Up` containers (flag any missing or restarting):**

| Profile | Required containers |
|---|---|
| All warehouse profiles | broker (`kafka` or `redis`), `vss-broker-health-check`, `vss-vios-nvstreamer`, `vss-rtvi-cv`, `vss-rtvi-cv-sdr`, `vss-configurator`, `vss-behavior-analytics`, the `vss-vios-*` VST stack |
| 3D extra | `vss-rtvi-cv-config-adaptor`, `elasticsearch` |
| Extended | `elasticsearch`, `logstash`, `kibana`, `vss-video-analytics-api` |
| `bp_wh` extra | `vss-rtvi-vlm`, `vss-rtvi-embed`, `vss-alert-bridge`, `vss-agent`, `vss-agent-ui`, `vss-va-mcp`, `vss-haproxy-ingress`, `phoenix`, LLM NIM (container name = `LLM_NAME_SLUG`) |

Record which containers are **Down**, **Restarting**, or have a non-zero exit code — these are the primary suspects.

---

## Phase 2: Perception FPS

Check whether perception is producing output. Same container regardless of `MODE`:

```bash
echo "--- Perception FPS (last 60 s) ---"
docker logs --since 60s vss-rtvi-cv 2>&1 | grep -i fps | tail -10
```

- **FPS lines present and non-zero** → perception is running; issue is likely downstream (broker, analytics, BEV sync).
- **No FPS lines** → perception is stalled or not receiving streams. Proceed to Phase 3.
- **FPS present but very low** → GPU saturation or stream count too high. Check Phase 4.

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

```bash
docker logs --tail 100 vss-rtvi-cv 2>&1 | grep -E "ERROR|error|fail|GST|pipeline|model" | tail -30
```

Common issues:
- `model not found` → `$VSS_DATA_DIR/models/` is missing or wrong path.
- `GST pipeline error` → stream input issue; check `vss-vios-nvstreamer` first.
- `CUDA out of memory` → GPU saturation; reduce `NUM_STREAMS`.

### 3.4 Perception SDR + Config Adaptor

```bash
docker logs --tail 50 vss-rtvi-cv-sdr 2>&1 | grep -E "ERROR|error|fail" | tail -20
# 3D only:
docker logs --tail 50 vss-rtvi-cv-config-adaptor 2>&1 | grep -E "ERROR|error|fail" | tail -20
```

### 3.5 Configurator

```bash
docker logs --tail 50 vss-configurator 2>&1 | grep -E "ERROR|error|fail" | tail -20
```

Note: `vss-configurator` has a **60 s start period** — a health-check failure in the first minute is expected.

### 3.6 Behavior Analytics

```bash
docker logs --tail 50 vss-behavior-analytics 2>&1 | grep -E "ERROR|error|fail" | tail -20
```

### 3.7 VST / VIOS stack

```bash
for c in vss-vios-postgres vss-vios-sensor vss-vios-streamprocessing vss-vios-sdr vss-vios-mcp vss-vios-ingress vss-vios-envoy; do
  echo "=== $c ==="
  docker logs --tail 30 "$c" 2>&1 | grep -E "ERROR|error|fail" | tail -10
done
```

### 3.8 `bp_wh` extras (RTVI VLM + agent)

Skip if `BP_PROFILE` is `bp_wh_kafka` or `bp_wh_redis`.

```bash
docker logs --tail 50 vss-rtvi-vlm     2>&1 | grep -E "ERROR|error|fail|CUDA" | tail -20
docker logs --tail 50 vss-alert-bridge 2>&1 | grep -E "ERROR|error|fail"      | tail -20
docker logs --tail 50 vss-agent        2>&1 | grep -E "ERROR|error|fail"      | tail -20
docker logs --tail 50 vss-agent-ui     2>&1 | grep -E "ERROR|error|fail"      | tail -20
docker logs --tail 50 vss-haproxy-ingress 2>&1 | grep -E "ERROR|error|fail"   | tail -20
# LLM NIM container name = LLM_NAME_SLUG from .env (e.g. nvidia-nemotron-nano-9b-v2)
LLM_SLUG=$(grep '^LLM_NAME_SLUG=' <repo>/deploy/docker/industry-profiles/warehouse-operations/.env | cut -d= -f2 | tr -d '"')
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

## Phase 5 (3D only): BEV Camera Timestamp Sync

For `MODE=3d`, check that all cameras contributing to the BEV frame are synchronized.

```bash
curl -s "http://localhost:9200/mdx-bev/_search?size=1" \
  -H 'Content-Type: application/json' \
  -d '{"sort":[{"timestamp":{"order":"desc"}}]}' | \
python3 - << 'EOF'
import json, sys
from datetime import datetime

data = json.load(sys.stdin)
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
- **WARNING** (34–67 ms) → minor drift; monitor. Check `docker logs vss-vios-nvstreamer` for lagging streams.
- **OUT OF SYNC** (> 67 ms) → restart `vss-vios-nvstreamer`; verify RTSP source health for drifting cameras.
- **No records found** → `elasticsearch` container may be down or the `mdx-bev` index has not been written to yet.

---

## Phase 6: Root Cause Summary

After completing Phases 1–5, state the root cause clearly before proposing any action. Use this decision table:

| Evidence | Root cause | Proposed fix |
|---|---|---|
| Container exited, exit code non-zero | Container crash — see its logs | Fix config or missing file; redeploy |
| `model not found` in `vss-rtvi-cv` logs | `VSS_DATA_DIR` path wrong or models not present | Correct `.env` path or re-acquire app data (see `warehouse.md` Phase 4) |
| `CUDA out of memory` on `vss-rtvi-cv` | Too many streams for GPU | Reduce `NUM_STREAMS`; redeploy |
| `CUDA out of memory` on LLM NIM or `vss-rtvi-vlm` | LLM and RTVI VLM colliding on the same GPU | Adjust `LLM_DEVICE_ID` / `RT_VLM_DEVICE_ID` / `SHARED_LLM_VLM_DEVICE_ID`; redeploy |
| Broker (Kafka/Redis) down | All downstream services lose messaging | Fix broker; redeploy |
| `vss-vios-nvstreamer` errors / no RTSP | Streams not reaching perception | Fix stream config; redeploy |
| BEV OUT OF SYNC | One or more camera feeds lagging | Restart `vss-vios-nvstreamer`; check camera RTSP sources |
| GPU 100 % sustained, low FPS | GPU oversaturated | Reduce `NUM_STREAMS`; redeploy |
| Disk < 10 GB | Write failures / container OOM | Free disk space; redeploy |
| `vss-configurator` failing after 60 s | Misconfigured streams or hardware profile | Verify `.env` values; redeploy |
| `vss-haproxy-ingress` up but UI 502 / report links broken | `EXTERNAL_IP` / `HAPROXY_PORT` not browser-reachable | Set `EXTERNAL_IP` to a real reachable hostname (see `warehouse.md` Phase 5); redeploy |

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

1. Apply the fix (edit `<repo>/deploy/docker/industry-profiles/warehouse-operations/.env` or correct the missing resource).
2. Tear down:

```bash
cd <repo>/deploy/docker
docker compose -f compose.yml --env-file industry-profiles/warehouse-operations/.env down
docker volume prune -f
docker system prune -f
bash ./scripts/cleanup_all_datalog.sh -e industry-profiles/warehouse-operations/.env
```

3. Bring up:

```bash
LOG=${LOG:-/tmp/warehouse-blueprint.log}
cd <repo>/deploy/docker
docker login --username '$oauthtoken' --password "${NGC_CLI_API_KEY}" nvcr.io
nohup docker compose -f compose.yml \
  --env-file industry-profiles/warehouse-operations/.env \
  up --detach --pull always --force-recreate --build \
  > "$LOG" 2>&1 &
echo "Compose PID $! — logging to $LOG"
```

4. Monitor until all required containers show `Up`:

```bash
tail -20 "$LOG"
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
```

5. Re-run **Phase 2** (FPS check) and, for 3D, **Phase 5** (BEV sync) to confirm the issue is resolved.

If the issue persists after redeploy, consult the [Documentation Reference](#documentation-reference) links above and `warehouse.md` → Troubleshooting.
