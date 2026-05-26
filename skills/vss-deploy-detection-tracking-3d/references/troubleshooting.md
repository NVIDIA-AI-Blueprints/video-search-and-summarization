# MV3DT troubleshooting

Parent: [`../SKILL.md`](../SKILL.md). MV3DT-specific failure modes. For broader warehouse issues that apply to 2D/3D/MV3DT alike, the deeper reference is [`../../vss-deploy-profile/references/warehouse-debug.md`](../../vss-deploy-profile/references/warehouse-debug.md).

## Top failure modes (in order of frequency)

### Only a fraction of cameras actually running (silent stream-count cap)

**Symptom:** You set `NUM_STREAMS=4` but `mdx-raw` only shows 2 sensors, perception logs 2 FPS lines, the VST sensor list has 2 entries. No error in any log.

**Cause:** `vss-configurator-mv3dt` computes `final_stream_count = min(NUM_STREAMS, max_streams_supported[HARDWARE_PROFILE].mv3dt)` and runs a `keep_count` op against `${VSS_DATA_DIR}/videos/${SAMPLE_VIDEO_DATASET}/` — silently deleting `.mp4` files beyond the cap (lex-sorted, last N kept). Per-GPU caps live in `blueprint-configurator/blueprint_config.yml:592-642`; see the table in `SKILL.md` Prerequisites §3.

Two common variants of this trap:
- User set `HARDWARE_PROFILE` to an invalid slug (e.g. `A6000` instead of `RTXA6000`) — the configurator falls back to defaults and may apply an unintended cap.
- User has more cameras than the GPU's `mv3dt` cap supports — the configurator silently crops the dataset to the cap and never emits a warning.

**Diagnose:**
```bash
ls "${VSS_DATA_DIR}/videos/${SAMPLE_VIDEO_DATASET}/"*.mp4 | wc -l
grep '^HARDWARE_PROFILE=' "${VSS_APPS_DIR}/industry-profiles/warehouse-operations/.env"
docker logs vss-configurator-mv3dt 2>&1 | grep -iE 'keep_count|final_stream_count|max_streams'
```

**Fix:** Either accept the cap (and tell the user explicitly), or move to a GPU with a higher cap. Re-source missing `.mp4` files from a backup; the configurator will trim again on next deploy unless `HARDWARE_PROFILE` covers your camera count. See [`configure-cameras.md`](configure-cameras.md) Step 2 for the lookup table.

### `vss-rtvi-cv-bev-fusion` not healthy / `/tmp/fusion_ready` missing

**Cause(s):**
- Broker not ready — `broker-health-check` hasn't completed yet, so `mdx-raw` topic doesn't exist.
- `MAX_EXPECTED_SENSORS` (= `NUM_STREAMS`) higher than actual streams — fusion buffers and waits.
- `STREAM_TYPE` in `.env` doesn't match the broker that's actually up (e.g. `.env` says `kafka` but `redis` is deployed because user set `BP_PROFILE=bp_wh_redis`).

**Diagnose:**
```bash
docker ps --filter name=broker-health-check          # must show Exited (0)
docker logs --tail 100 vss-rtvi-cv-bev-fusion 2>&1 | tail -30
docker exec kafka kafka-topics --bootstrap-server localhost:9092 --list 2>/dev/null \
  || docker exec redis redis-cli KEYS 'mdx*'

# Verify fusion health (NOT via docker exec ... test -f /tmp/fusion_ready — the image strips test out of PATH):
docker inspect --format '{{.State.Health.Status}}' vss-rtvi-cv-bev-fusion
```

**Fix:** Wait if `broker-health-check` is still `Up` (it can take 2–3 min). If it `Exited` non-zero, check broker logs (`docker logs kafka` or `docker logs redis`). If `MAX_EXPECTED_SENSORS` mismatch: walk [`configure-cameras.md`](configure-cameras.md) again.

### `vss-rtvi-cv-mv3dt` exits / `ds-start-mv3dt.sh` fails

**Cause(s):**
- `camInfo/cam_*.yaml` mount is missing or empty (calibration not landed).
- `NUM_STREAMS` doesn't equal the count of `camInfo/*.yaml` files — DeepStream batch size mismatches model expectations.
- `BodyPose3DNet` model files not at `${VSS_DATA_DIR}/models/mv3dt/BodyPose3DNet/` — perception can't load weights.

**Diagnose:**
```bash
DATASET="${SAMPLE_VIDEO_DATASET:?}"
CAL_DIR="${VSS_APPS_DIR}/industry-profiles/warehouse-operations/warehouse-mv3dt-app/calibration/sample-data/${DATASET}"

ls -l "${CAL_DIR}/camInfo/" | head
docker exec vss-rtvi-cv-mv3dt ls /tmp/camInfo/ 2>/dev/null   # what perception actually sees
docker exec vss-rtvi-cv-mv3dt ls /opt/storage/BodyPose3DNet/ 2>/dev/null
docker logs --tail 200 vss-rtvi-cv-mv3dt 2>&1 | tail -60
```

**Fix:** Re-walk [`calibration-workflow.md`](calibration-workflow.md) Step 4 and [`configure-cameras.md`](configure-cameras.md). For missing BodyPose3DNet, confirm `VSS_DATA_DIR` points at extracted `vss-warehouse-app-data` (see [`deploy-rtvi-cv-3d-stack.md`](deploy-rtvi-cv-3d-stack.md) — `${VSS_DATA_DIR}/models/mv3dt/BodyPose3DNet/` must exist).

### `mosquitto` unhealthy

**Cause(s):**
- `MQTT_HOST` / `MQTT_PORT` in `.env` don't match the mosquitto container's actual host/port.
- Mosquitto's bind port (`1883` by default) already in use on the host.

**Diagnose:**
```bash
grep -E '^MQTT_(HOST|PORT)=' "${VSS_APPS_DIR}/industry-profiles/warehouse-operations/.env"
ss -tlnp | grep ':1883'                         # port collision check
docker logs --tail 50 mosquitto 2>&1 | tail
```

**Fix:** Set `MQTT_HOST=localhost`, `MQTT_PORT=1883` (mosquitto uses `network_mode: host`). If another process has 1883, stop it (or pick a different `MQTT_PORT` and redeploy).

### BEV out of sync — frames look stale or duplicated

**Cause(s):**
- Camera clocks drift; per-camera frame timestamps fall outside `SENSOR_TIMEOUT_MS` window (default 100 ms).
- `BUFFER_DURATION_S` too short for the actual end-to-end latency.

**Diagnose:**
Watch `mdx-bev` rate vs `mdx-raw` rate over a minute. The shipped Kafka image is `confluentinc/cp-kafka:8.2.0` which uses `kafka-get-offsets` (not the older `kafka-run-class kafka.tools.GetOffsetShell` — that class is gone):
```bash
docker exec kafka kafka-get-offsets --bootstrap-server localhost:9092 --topic mdx-raw
docker exec kafka kafka-get-offsets --bootstrap-server localhost:9092 --topic mdx-bev
```
If `mdx-bev` grows much slower than `mdx-raw` × num cameras, fusion is dropping under-late frames.

**Fix:** Override the env in `services/rtvi/rtvi-cv/rtvi-cv-mv3dt/compose.yaml:52` (`SENSOR_TIMEOUT_MS`) and `:54` (`BUFFER_DURATION_S`) via env file:

```bash
# Add to industry-profiles/warehouse-operations/.env
echo 'SENSOR_TIMEOUT_MS=300' >> "${VSS_APPS_DIR}/industry-profiles/warehouse-operations/.env"
echo 'BUFFER_DURATION_S=3.0' >> "${VSS_APPS_DIR}/industry-profiles/warehouse-operations/.env"
```

Then `docker compose ... up -d` to apply. Tune upward incrementally.

### BodyPose3DNet TRT engine build hangs first start

**Symptom:** `vss-rtvi-cv-mv3dt` sits in `(starting)` for many minutes. No FPS lines yet.

**Normal:** First-start engine build takes 3–8 min on H100, 8–15 min on L4. Tail `docker logs -f vss-rtvi-cv-mv3dt` for `Build engine successfully`.

**Diagnose if it's truly stuck (>15 min):**
```bash
docker logs --tail 200 vss-rtvi-cv-mv3dt 2>&1 | grep -iE 'cuda|out of memory|killed|error' | tail -20
nvidia-smi
```
If GPU OOM appears, perception is competing with another workload on `RT_CV_DEVICE_ID`. Free the GPU (or change `RT_CV_DEVICE_ID` in `.env`) and redeploy.

### AMC MV3DT export ZIP missing `transforms.yml` / `camInfo/*.yaml`

**Cause(s):**
- `result_type=amc` requested but AMC didn't actually finish — `project_state != COMPLETED`.
- VGGT path requested (`result_type=vggt`) but VGGT wasn't run or didn't complete.

**Diagnose:**
```bash
curl -s "http://localhost:8010/v1/get_project_info/${project_id}" | jq '.project_info | {project_state, vggt_state}'
curl -s "http://localhost:8010/v1/amc/calibrate/${project_id}/log" | tail -60
```

**Fix:** Per [`calibration-workflow.md`](calibration-workflow.md) Step 2 — re-poll until `project_state == COMPLETED`. If VGGT requested, also check `vggt_state == COMPLETED` (VGGT only runs if the model file is staged).

### VST video wall (`:30888`) unreachable

**Cause(s):**
- VST stack didn't come up (sensor-ms / postgres in bad state).
- Firewall blocks port 30888 from the browser host.
- `HOST_IP` is `localhost` and you're trying to reach from a remote browser.

**Diagnose:**
```bash
docker ps | grep -E 'vios|sensor-ms|centralizedb'
ss -tlnp | grep ':30888'
curl -sf "http://localhost:30888/vst/api/v1/sensor/list"   # from the host itself
```

**Fix:** If VST containers are missing, the profile gating didn't activate them — confirm `COMPOSE_PROFILES` resolves to `bp_wh_kafka_mv3dt` (or `_redis_`). If `HOST_IP=localhost` in `.env`, change it to the actual reachable IP and redeploy (compose substitutes at start time). For firewall, port-forward via SSH (`ssh -L 30888:localhost:30888`) or open the port on the host.

### VST video wall: "Failed to create Video Source" despite a healthy pipeline

**Symptom:** VST UI loads at `http://<HOST_IP>:30888/vst` fine. Click play on any sensor → `Playback Error: Error 22: Failed to create Video Source`. Data is flowing — `mdx-raw` and `mdx-bev` offsets are growing, `vss-vios-streamprocessing` is writing per-minute mkv chunks to `${VSS_DATA_DIR}/data_log/`, `rtsp://<HOST_IP>:30554/live/<sensorId>` is serving valid H264.

**Cause:** WebRTC negotiation fails between the browser and VST. Two specific things VST needs that often get blocked:
- **Outbound STUN** to `stun.l.google.com:19302` (VST's default `stunurl_list`). Corp / VPN blocks Google STUN frequently.
- **Inbound UDP** on a random port range (VST's default `webrtc_port_range: {min:0, max:0}`). Corp / cloud / on-prem firewalls that don't pass arbitrary UDP make ICE negotiation fail.

**Cosmetic red herring.** While WebRTC is broken, `GET /vst/api/v1/sensor/list` may report `state: "offline"` and `url: null` for each sensor. That status is misleading — if `streamprocessing` is actively recording chunks, the pipeline is fine. Don't chase the offline-status.

**Diagnose:**
```bash
# Pipeline is healthy?
docker logs --tail 50 vss-vios-streamprocessing 2>&1 | grep -E 'write|mkv|chunk' | tail
ls -la "${VSS_DATA_DIR}/data_log/" | head

# RTSP source reachable?
ffprobe -v error -timeout 5000000 "rtsp://${HOST_IP}:30554/live/<sensorId>" 2>&1 | head

# Browser network access?
curl -fI "http://${HOST_IP}:30888/vst" -o /dev/null -w "%{http_code}\n"   # 200 = UI works
nc -zu stun.l.google.com 19302                                            # blocked? STUN unreachable
```

**Workarounds** (in order of effort):
1. **Run the browser on the host itself.** VNC, X-forwarding, or RDP — bypasses the WebRTC firewall entirely.
2. **Bypass VST UI, use RTSP directly.** `ffplay rtsp://<HOST_IP>:30554/live/<sensorId>` if port 30554 is reachable. No overlays, but you see the raw stream.
3. **Bypass UI entirely; consume `mdx-bev`.** Data is on the broker — write a downstream consumer.
4. **Self-host a TURN server** on TCP/443 and reconfigure VST's `stunurl_list` / `webrtc_port_range`. Heavyweight; out of scope for this skill.

### No bounding-box overlays in VST video wall

**Not a bug under `MINIMAL_PROFILE="true"`.** Overlays require Elasticsearch + `vss-video-analytics-api-mv3dt` + `vss-import-calibration-output-mv3dt`, all gated under `_extended`. None of them deploy in minimal mode. See [`verify-and-view.md`](verify-and-view.md) Step 5.

**Fix:** Tear down ([`teardown.md`](teardown.md)), set `MINIMAL_PROFILE=""` in `.env`, redeploy ([`deploy-rtvi-cv-3d-stack.md`](deploy-rtvi-cv-3d-stack.md)). There is no "minimal + just ELK" middle path in the current compose — the `_extended` services share a single gating suffix and come up together.

In the VST UI itself, overlays are off by default per stream — enable via the video player's options menu.

### Image pull 401 / 403 from `nvcr.io`

**Cause(s):**
- `docker login nvcr.io` not run (or token expired).
- `NGC_CLI_API_KEY` resolves to an org that doesn't have access to the image — `vss-core` lives in both `nvidia/` and `nvstaging/`, and your key may only see one.

**Diagnose:**
```bash
docker login --username '$oauthtoken' --password "${NGC_CLI_API_KEY}" nvcr.io
ngc registry image list "nvidia/vss-core/*"      2>&1 | head -5
ngc registry image list "nvstaging/vss-core/*"   2>&1 | head -5
```

**Fix:** Re-login. If neither org lists the image, your key doesn't have access — confirm with `ngc org list`. Then either set `PERCEPTION_IMAGE` and `BEV_FUSION_MV3DT_IMAGE` in `.env` to the org that works for you, or get a new key.

## When to drop down to `warehouse-debug.md`

For general warehouse-blueprint issues (NGC permissions, low FPS tuning beyond MV3DT, GPU saturation across multiple stacks, broker tuning, NGC app-data extraction), the deeper reference is [`../../vss-deploy-profile/references/warehouse-debug.md`](../../vss-deploy-profile/references/warehouse-debug.md). That's an MV3DT-aware reference too, just broader.

## Clean reset

If multiple things are off and you want to start clean: [`teardown.md`](teardown.md). Tear down, fix env, redeploy.
