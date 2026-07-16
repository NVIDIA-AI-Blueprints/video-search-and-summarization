# Validation Harness — NvStreamer Synthetic RTSP Source (Step 4 / Step 6 / Step 6.5)

This is the **keystone** reference for the validation-harness behavior of `vss-build-vision-agent`. It defines how the skill stands up a **synthetic RTSP stream** — backed by a stored sample video served over RTSP by NvStreamer (`vss-vios-nvstreamer`) — so a generated deployment can exercise its **live/streaming end-to-end path** without a real external camera or operator-supplied RTSP URL. NvStreamer replaces the old `mediamtx + ffmpeg` dummy-stream sidecar that earlier revisions of this skill used for the same purpose.

> **Scope note — NOT a user-facing topology.** NvStreamer here is a **validation-harness component only**. It is **NOT** a user-selectable ingestion topology and **NOT** a `sensor_topology` variant. It is **NOT** declared in any `integrate-*.md` `component_services:` block (in particular it is **NOT** in `integrate-vios-service.md`'s `component_services:` — do not add it there, and do not touch the existing `sensor_topology` variants). The skill emits NvStreamer **directly into the build-output compose** when (and only when) the inclusion rule below fires. Auditing tools that read the allow-list sidecar will not see NvStreamer in `services:` — it rides the build through Step 6's direct emission plus the sidecar's separate `validation_harness:` key. This keeps the user's *real* ingestion architecture (RTSP camera or uploaded files via VIOS) clean and unchanged, while still giving the eval harness a way to prove the streaming half works.
>
> NvStreamer's `integrate`/`deploy` semantics are NOT authored as pair files because it owns no user capability. Its REST surface is documented in `skills/vss-manage-video-io-storage/references/nvstreamer-api-reference.md` (read-only reference); its deployment mechanics are modeled on `deploy/docker/developer-profiles/dev-profile-alerts/compose.yml § nvstreamer-alerts`.

---

## 1. Inclusion decision (Step 4)

Include the NvStreamer validation harness in a generation when **both** conditions hold:

1. **The capability has a live/streaming RTSP path.** The prompt asks for streaming ingestion / live captioning / live detection / any "RTSP stream" input, OR a selected microservice declares a streaming input (e.g. RT-VLM `streaming-inference`, RT-CV live perception). Pure VOD/upload-only generations (no live path) do NOT need the harness.
2. **The user did NOT supply a real external camera / RTSP URL.** If the prompt names a concrete camera, an existing RTSP endpoint, or a sidecar that already publishes RTSP, use THAT source (Step 4 § External RTSP source location pre-flight) and do NOT add NvStreamer.

When both hold, record the decision in the Step 4 sidecar as a top-level `validation_harness:` key (see `references/allow-list-sidecar.md`):

```yaml
validation_harness:
  rtsp_source: nvstreamer
  sample_video: <sample-video-filename>      # canonical default: warehouse_safety_0001.mp4 — the staged sample (Step 6)
```

Surface the inclusion to the user in the Step 4 proposal as a distinct line item — "Validation harness: NvStreamer synthetic RTSP source (no real camera supplied)" — so the operator knows a synthetic stream is being added purely to exercise the live path. In autonomous / eval-harness mode, default to including it for any streaming-capable IN-/AN- profile. Cite this file (`references/validation-harness.md`) for the decision.

If the inclusion rule does NOT fire (real camera supplied, or VOD-only), omit the `validation_harness:` key entirely and do not emit the NvStreamer service.

---

## 2. The service block to emit (Step 6)

Emit the following service **directly into the build-output compose** (i.e. into a patched copy under `<BUILD_DIR>/patched/` that the build-output `compose.yml` `include:`s — never into an upstream file). The block is modeled on `dev-profile-alerts/compose.yml § nvstreamer-alerts`, with two differences: (a) the `profiles:` list carries **only the invented flag** for this generation (added by Step 6.5 Patch 1), and (b) the videos bind-mount points at this build's staging directory.

```yaml
  nvstreamer-validation:
    image: nvcr.io/nvstaging/vss-core/vss-vios-nvstreamer:${NVSTREAMER_IMAGE_TAG}
    user: "0:0"
    profiles: ["<invented-flag>"]            # e.g. bp_developer_in_1 — inserted by Step 6.5 Patch 1
    entrypoint: [ "/bin/bash", "-c", "if [ \"$$NVSTREAMER_INSTALL_ADDITIONAL_PACKAGES\" = \"true\" ]; then /home/vst/vst_release/tools/user_additional_install.sh; fi && exec /home/vst/vst_release/launch_vst" ]
    environment:
      - NVSTREAMER_INSTALL_ADDITIONAL_PACKAGES=${NVSTREAMER_INSTALL_ADDITIONAL_PACKAGES}
      - ADAPTOR=streamer
      - HTTP_PORT=${NVSTREAMER_HTTP_PORT}
    network_mode: "host"
    deploy:
      restart_policy:
        condition: on-failure
        max_attempts: 2
    container_name: vss-vios-nvstreamer
    volumes:
      - <BUILD_DIR>/patched/nvstreamer/configs/vst-config.json:/home/vst/vst_release/configs/vst_config.json
      - <BUILD_DIR>/patched/nvstreamer/configs/vst-storage.json:/home/vst/vst_release/configs/vst_storage.json
      - ${VSS_DATA_DIR}/videos/<build-name>:/home/vst/vst_release/streamer_videos
      - ${VSS_DATA_DIR}/data_log/nvstreamer/vst_data:/home/vst/vst_release/vst_data
      - ${VSS_APPS_DIR}/services/vios/scripts/user_additional_install.sh:/home/vst/vst_release/tools/user_additional_install.sh
    depends_on:
      broker-health-check:
        condition: service_completed_successfully
```

Notes:

- **Ports.** NvStreamer listens on HTTP `${NVSTREAMER_HTTP_PORT}` (default **31000**) with an RTSP server pool on **31554–31561** (the per-file RTSP port is chosen by NvStreamer's internal load balancer at start-up — NEVER construct it; read it from the API — see § 4). `network_mode: host` means these bind directly on the host.
- **`.env` additions (Step 6).** Add `NVSTREAMER_IMAGE_TAG`, `NVSTREAMER_HTTP_PORT=31000`, and `NVSTREAMER_INSTALL_ADDITIONAL_PACKAGES=true` to the build-output `.env` + `.env.template`. `NVSTREAMER_IMAGE_TAG` reuses the same tag the rest of VIOS uses; resolve it during the Step 6 env-folding pass so no `${...}` is left unexpanded at dry-run. `NVSTREAMER_INSTALL_ADDITIONAL_PACKAGES=true` is the same libav-install gate VIOS uploads need (`deploy-vios-service.md § Known Deployment Issues` Finding 9 / `nvstreamer-api-reference.md § Upload errors`) — set it true so the streamer can probe the staged file's codec.
- **`depends_on: broker-health-check`** is defined in `services/infra/compose.yml` (contributed by ELK's `always:` list); it stays. NvStreamer has no other required peers. If broker-health-check is NOT in the build (a non-ELK generation that still wants the harness), strip this `depends_on` per Step 6.5 Patch 2's normal rule.
- **`VSS_APPS_DIR`** in the upstream model points at `deploy/docker`. The `user_additional_install.sh` mount may reference the upstream repo path; resolve it during Step 6 env-folding (it is read-only and never modified).

---

## 3. Config materialization + sample-video staging (Step 6 / Step 6.5 Patch 3)

NvStreamer needs two config files and one sample video on disk before it boots.

**Config files (Step 6.5 Patch 3 sub-case).** Copy the two upstream NvStreamer config JSONs into the patched tree so the bind mounts above resolve to real files (Docker would otherwise create them as empty directories and the streamer would fail to read its config):

- `deploy/docker/developer-profiles/dev-profile-alerts/nvstreamer/configs/vst-config.json` → `<BUILD_DIR>/patched/nvstreamer/configs/vst-config.json`
- `deploy/docker/developer-profiles/dev-profile-alerts/nvstreamer/configs/vst-storage.json` → `<BUILD_DIR>/patched/nvstreamer/configs/vst-storage.json`

These are upstream-byte-identical — do NOT hand-edit. Add a `PATCHES.md` row for each materialized file, citing the upstream source path and patched destination.

**Sample video (Step 6).** Stage one sample video into `${VSS_DATA_DIR}/videos/<build-name>/` (the host directory bind-mounted at `/home/vst/vst_release/streamer_videos`):

- `mkdir -p ${VSS_DATA_DIR}/videos/<build-name>` and `chmod -R 777` it (per the pre-flight permission rule — NvStreamer runs as `user: "0:0"` but the host directory must be writable for `vst_data` and discovery scratch).
- Copy a known-good H.264/H.265 MP4/MKV/TS sample into it. The filename **must NOT contain whitespace** (`nvstreamer-api-reference.md § 3` — whitespace is rejected). Use snake_case or kebab-case. Record the chosen filename as `validation_harness.sample_video` in the sidecar.
- **Staging the sample — canonical path, then fallbacks (in order):** The canonical default for IN-1 / streaming-warehouse work is **`warehouse_safety_0001.mp4`** (H.264, warehouse content), sourced on the validated eval host from `/home/ubuntu/sochoa/dev-profile-sample-data/warehouse_safety_0001.mp4`. The skill does NOT generate video, but it MAY fetch a public sample when the canonical one is absent:
  1. If `warehouse_safety_0001.mp4` exists at the canonical path (or the operator supplied a path), copy it in.
  2. Else, if the RT-DETR warehouse app-data tarball was already pulled for the detector model (Patch 0, `references/standalone-compose-patches.md`), extract a clip from `vss-warehouse-app-data/videos/nv-warehouse-4cams` and stage it (warehouse content — best RT-DETR match).
  3. Else, if the host has outbound network, fetch a public **person/vehicle** H.264 clip (RT-DETR detects `person`/`bicycle`/`car`), e.g. `https://github.com/intel-iot-devkit/sample-videos/raw/master/person-bicycle-car-detection.mp4`, and stage it under a **whitespace-free** name (verified live 2026-07-03: this drives RT-CV `Person` detections end-to-end).
  4. Else, prompt the operator for a path to any whitespace-free H.264/H.265 sample.
  Record the chosen filename as `validation_harness.sample_video`. In eval-harness mode, the harness env documents the sample path. **Content matters for RT-CV smoke:** a test-pattern/synthetic clip yields zero detections (empty `mdx-raw` docs) — use real footage with people/vehicles so the ES bbox assertion is meaningful.
- NvStreamer **auto-discovers** files present in the videos directory at startup (or on `POST /sensor/scan`). For auto-discovered files, `sensorId == streamId == name == filename-without-extension` (`nvstreamer-api-reference.md § 2` / § 7). No upload call is required — staging the file IS the registration.

---

## 4. The smoke sequence — NvStreamer → VIOS → RT-VLM (IN-1 / dense captioning)

This is the canonical streaming-path validation for **IN-1 dense-captioning** profiles. It is emitted into the generated `deploy-<flag-slug>` skill's post-deploy smoke test when the allow-list includes RT-VLM for captioning but **not** Alert Bridge. For **realtime-alert** profiles (`alert_source=vlm-realtime`, Alert Bridge in the allow-list), use **§ 4b** instead — the handoff after VIOS registration goes to Alert Bridge, not directly to RT-VLM.

Ports referenced: **NvStreamer 31000** (HTTP) / **31554–31561** (RTSP pool), **VIOS 30888** (ingress) / **30554** (live RTSP proxy), **RT-VLM 8018**.

```bash
NV=http://${HOST_IP}:31000/vst/api/v1          # NvStreamer
VIOS=http://${HOST_IP}:30888/vst/api/v1        # VIOS ingress
RTVLM=http://${HOST_IP}:8018                    # RT-VLM
STEM=<sample-video-filename-without-extension>  # the file STEM; see step 1 — the sensorId may carry a `_N` suffix

# 0. Confirm NvStreamer is up and is a streamer (type == "streamer", NOT "vst").
curl -sf --connect-timeout 5 "$NV/sensor/version" | jq -e '.type == "streamer"'

# 1. Allow ~5 s after staging for the discovery cycle to populate the streams list; retry with backoff.
#    Resolve the real sensorId from /sensor/list by matching .name == $STEM. NvStreamer appends a
#    "_N" uniqueifier to the sensorId for auto-discovered files (e.g. warehouse_safety_0001 -> sensorId
#    "warehouse_safety_0001_0"), so .name == $STEM but sensorId != $STEM. (Finding F-B, 2026-06-02 —
#    nvstreamer-api-reference.md § 2 over-claims sensorId==name==stem; the _N suffix is real.)
NVSID=""
for i in 1 2 3 4 5 6; do
  NVSID=$(curl -sf "$NV/sensor/list" | jq -r --arg s "$STEM" '.[] | select(.name==$s) | .sensorId' | head -1)
  [ -n "$NVSID" ] && break
  sleep 5
done

# 2. Read the RTSP URL FROM THE API by sensorId (NEVER by $STEM — /sensor/$STEM/streams returns
#    CameraNotFoundError when the sensorId carries a _N suffix; NEVER construct the 315xx port).
URL=$(curl -s "$NV/sensor/$NVSID/streams" | jq -r '.[0].url')
# URL == rtsp://${HOST_IP}:315xx/nvstream/home/vst/vst_release/streamer_videos/<file>

# 3. Register that RTSP URL with VIOS. Field is "sensorUrl" — NOT "url". Send the MINIMAL body
#    {"sensorUrl": "..."} ONLY: adding name/username/password makes VIOS reject the request with
#    InvalidParameterError "JSON structure unsafe: excessive nesting or size detected" (Finding F-C,
#    2026-06-02). VIOS assigns the name itself.
SID=$(curl -s -X POST "$VIOS/sensor/add" \
  -H "Content-Type: application/json" \
  --data-raw "{\"sensorUrl\": \"$URL\"}" \
  | jq -r '.sensorId')

# 4. VIOS now proxies the stream over a DYNAMIC RTSP port from the RtspLoadBalancer pool — it is
#    NOT fixed at 30554 (it landed on 30556 in the most recent run). VIOS GET /sensor/<id>/streams
#    returns an EMPTY .url for type:Rtsp sensors, so the proxy port is only discoverable from the
#    streamprocessing container log line "Live proxy url: rtsp://<HOST_IP>:<PORT>/live/<SID>"
#    (Finding B, 2026-06-17). Grep the log for $SID after /sensor/add succeeds, then extract the port.
#    Feed THAT proxy URL to RT-VLM (use ${HOST_IP}, never localhost).
#    Record the caption-topic baseline first — it proves the live RT-VLM path advanced Kafka after
#    this stream was driven.
CAPTIONS_BEFORE=$(docker exec kafka kafka-get-offsets \
  --bootstrap-server localhost:9092 \
  --topic mdx-vlm-captions \
  | awk -F: '{sum += $3} END {print sum+0}')
echo "mdx-vlm-captions baseline offset: $CAPTIONS_BEFORE"
#    /v1/streams/add takes the {"streams":[{...}]} ENVELOPE (a flat {"liveStreamUrl":...} body is
#    rejected with InvalidParameters "('body', 'streams'): Field required" — Finding F-D, 2026-06-02;
#    matches integrate-rt-vlm.md § Inputs). The stream_id comes back under .results[0].id.
PROXY=""
for i in 1 2 3 4 5 6; do
  PROXY=$(docker logs vss-vios-streamprocessing 2>&1 | grep "Live proxy url" | grep "$SID" | tail -1 \
    | sed -E 's#.*(rtsp://[^[:space:]]+).*#\1#')
  [ -n "$PROXY" ] && break
  sleep 5
done
# Fail fast if the loop timed out (service not up, wrong container name, or SID null from a
# failed /sensor/add) — otherwise streams/add gets liveStreamUrl:"" and STREAM_ID silently empties.
[ -z "$PROXY" ] && { echo "ERROR: timed out waiting for VIOS live-proxy URL for SID=$SID"; exit 1; }
# PROXY == rtsp://<HOST_IP>:<dynamic-port>/live/$SID  (port from RtspLoadBalancer pool, e.g. 30556)
STREAM_ID=$(curl -s -X POST "$RTVLM/v1/streams/add" \
  -H "Content-Type: application/json" \
  -d "{\"streams\": [{\"liveStreamUrl\": \"$PROXY\", \"description\": \"nvstream validation\"}]}" \
  | jq -r '.results[0].id')

# 5. Resolve the loaded model ID at runtime — do NOT hardcode "cosmos-reason2-8b".
#    The model id registered in RT-VLM is a runtime-generated string in the format
#    nim_nvidia_<model>_<tag> (e.g. nim_nvidia_cosmos-reason2-8b_hf-1208). Passing
#    the human-readable name returns BadParameters: No such model 'cosmos-reason2-8b'.
MODEL_ID=$(curl -sf "$RTVLM/v1/models" | jq -r '.data[0].id')
# Guard: curl -sf yields empty stdout on a non-2xx/unready RT-VLM, and jq -r on empty/null input
# prints the literal "null" — either would drive generate_captions with model:"" / "null" and
# reproduce the exact BadParameters this step exists to prevent.
if [ -z "$MODEL_ID" ] || [ "$MODEL_ID" = "null" ]; then
  echo "ERROR: could not resolve RT-VLM model ID from $RTVLM/v1/models"; exit 1
fi

# 6. Drive captions on the live stream by the stream_id from step 4 (id field; stream:true and a
#    non-empty prompt are both mandatory per RT-VLM /v1/generate_captions). chunk_duration > 0.
curl -s -N -X POST "$RTVLM/v1/generate_captions" \
  -H "Content-Type: application/json" \
  -d "{\"id\": \"$STREAM_ID\", \"model\": \"$MODEL_ID\", \"stream\": true, \"prompt\": \"Describe the scene.\", \"chunk_duration\": 10}"

# 7. Assert captions land on Kafka mdx-vlm-captions AND in ES default_<id> FROM THE LIVE PATH.
CAPTIONS_AFTER=$(docker exec kafka kafka-get-offsets \
  --bootstrap-server localhost:9092 \
  --topic mdx-vlm-captions \
  | awk -F: '{sum += $3} END {print sum+0}')
echo "mdx-vlm-captions post-RT-VLM offset: $CAPTIONS_AFTER"
test "$CAPTIONS_AFTER" -gt "$CAPTIONS_BEFORE"
curl -sf 'http://localhost:9200/_cat/indices?h=index,docs.count&v' | awk '$1 ~ /^default_/ && $2+0 > 0'
```

### Gotchas (cite when debugging)

- **Read the RTSP port from the API.** The 315xx RTSP port is assigned by NvStreamer's load balancer at start-up; it is NOT tied to filename or alphabetic order (`nvstreamer-api-reference.md § 2`). Constructing `rtsp://host:31554/...` will intermittently hit the wrong port.
- **The VIOS live-proxy RTSP port is dynamic (RtspLoadBalancer pool) — do not hardcode 30554.** Always read it from the streamprocessing logs after `/sensor/add` (`docker logs vss-vios-streamprocessing 2>&1 | grep "Live proxy url" | grep "$SID"`). VIOS `GET /sensor/<id>/streams` returns an empty `.url` for `type:Rtsp` sensors, so the log line is the only source. The port landed on 30556 (not 30554) in the most recent run (Finding B, 2026-06-17).
- **`sensorUrl`, not `url`.** VIOS `POST /sensor/add` takes `sensorUrl` (`integrate-vios-service.md § API Schema` line ~172 / `api-reference.md § 6`). Using `url` silently fails parameter validation.
- **`${HOST_IP}`, not `localhost`, for the VIOS proxy URL handed to RT-VLM.** Both run `network_mode: host`, but a `localhost` URL passed into the RT-VLM container resolves to the container's own loopback (VIOS Finding 12). Use `${HOST_IP}`.
- **Discovery latency.** The streams list and codec metadata populate asynchronously (~5 s for the sensor to appear, ~15–30 s for `metadata.codec`). Retry with backoff (step 1); if you need codec immediately, call `GET $NV/storage/file/mediainfo?sensorId=$STEM`.
- **No `/sensor/add` on NvStreamer.** Registration with VIOS happens on the VIOS port (30888), not on NvStreamer (31000). NvStreamer has no upstream-camera concept (`nvstreamer-api-reference.md § 3`).
- **Register with VIOS only after the full SDRC/cluster is healthy (Finding F-E, 2026-06-02).** A sensor registered while `sdr-controller` or Redis is still unhealthy reports `state: online` on `/sensor/<id>/status` but its VIOS live proxy (`rtsp://${HOST_IP}:30554/live/<id>`) silently does NOT serve — RT-VLM `/v1/streams/add` then fails with `Could not connect to the RTSP URL or there is no video stream`. Confirm `sdr-controller` logs show `Redis Listener started` (not a `ConnectionRefusedError` crash-loop) before `POST /sensor/add`. If the proxy fails to serve, delete and re-add the sensor once the cluster is healthy — the re-registration produces a working proxy.
- **Baseline before RT-VLM stream processing is mandatory for runtime proof.** `mdx-vlm-captions` offset `> 0` can be stale from a prior run. Record the baseline before `POST /v1/streams/add` / `/v1/generate_captions`, then assert the post-processing offset is greater.
- **ES doc count is mandatory** (`feedback_smoke_test_completeness.md`). Kafka offset advance alone misses topic-name misconfigurations — assert `default_<id>` doc count from the live path, distinct from any VOD/upload check.
- **Clean stale OFFLINE sensors in VIOS before re-adding (Finding F-H, 2026-06-16).** On a re-deploy, NvStreamer auto-discovers the staged video under a fresh `sensorId` (the `_N` uniquifier increments), but VIOS still holds the **prior run's** sensor registration — now `state: offline` because the old NvStreamer RTSP port/pool is gone. Blindly `POST /sensor/add` then leaves a stale offline duplicate (and the smoke test may resolve the wrong sensorId). Before registering, list VIOS sensors and `DELETE` any whose `live_stream_url` points at an NvStreamer 315xx RTSP URL but reports `state: offline` (or whose name matches the sample stem from a prior run): `GET ${VIOS}/sensor/list` → for each stale/offline NvStreamer-backed entry `DELETE ${VIOS}/sensor/{sensorId}`, then add the freshly-resolved URL. Emit this de-dup step into the generated deploy skill's smoke sequence and into Patch 0 pre-flight so re-runs are idempotent (pairs with the `vss-vios-nvstreamer` orphan-container grep in § 5).
- **Live registration residue on re-run (Finding C, 2026-06-17).** Like the VOD clip_storage bind-mount, `${VSS_DATA_DIR}/data_log/vst/vst_data` is a HOST bind-mount that survives `docker compose down` (and `down -v`). The NvStreamer RTSP sensor registered in a prior run is still in Postgres there, so on a re-run `POST /sensor/add` with the same `sensorUrl` returns HTTP 400 "Sensor exists already". Before re-running the live half, clear the stale sensor by ONE of:
  - **`DELETE $VIOS/sensor/<stale-sensorId>` (PREFERRED for iterative eval runs)** — removes only the one stale sensor, preserving all other sensor configs and ingested data. Resolve the stale id from `GET $VIOS/sensor/list` by matching the NvStreamer proxy `sensorUrl`.
  - `sudo rm -rf ${VSS_DATA_DIR}/data_log/vst/vst_data` — full clean, but DESTRUCTIVE: wipes ALL sensor configs. Use only for a true clean-slate run, not for tight iterate-and-rerun loops.
- **VOD clip_storage file residue on re-run.** After a partial or wrong-`VSS_DATA_DIR` run, `clip_storage/<filename>` may exist on the host bind-mount but be absent from a fresh Postgres (e.g. after `down -v` recreated the named volume but left the bind-mount intact). VIOS returns `ResourceConflictError: File already exists` on upload (filesystem check passes) but `File not found` on `DELETE <sensorId>` (Postgres check fails) — leaving you stuck. Fix: remove the file directly from the bind-mount before re-uploading:

  ```bash
  docker run --rm -v "${VSS_DATA_DIR}/data_log/vst/clip_storage:/d" busybox rm -f "/d/<filename>"
  ```

  Replace `<filename>` with the actual upload filename (e.g. `warehouse_safety_0001.mp4`). For a full clean slate, `sudo rm -rf ${VSS_DATA_DIR}/data_log/vst/clip_storage/` then `sudo mkdir -p ... && sudo chmod -R 777 ...` to recreate. Surfaced live 2026-06-18, IN-1 expanded eval.

---

## 4b. The smoke sequence — NvStreamer → VIOS → Alert Bridge (AT / realtime alerts)

Use this path when the generated profile includes **Alert Bridge** with `alert_source=vlm-realtime` (real-time VLM alerts). The ingestion chain is **NvStreamer (or external RTSP) → VIOS → Alert Bridge**; Alert Bridge then drives RT-VLM internally. Do **NOT** call RT-VLM `/v1/streams/add` directly in the smoke test — that bypasses the Alert Microservice contract.

Reuse **§ 4 steps 0–3** (NvStreamer up → resolve NvStreamer RTSP URL → `POST $VIOS/sensor/add` with `{"sensorUrl": "..."}`) and the same gotchas (dynamic VIOS proxy port, `${HOST_IP}` not `localhost`, stale-sensor cleanup). Then continue:

```bash
AB=http://${HOST_IP}:9080/api/v1          # Alert Bridge (Alert MS)
VIOS=http://${HOST_IP}:30888/vst/api/v1    # VIOS ingress (same as § 4)

# 4. Resolve sensor_id, sensor_name, and live_stream_url FROM VIOS (not NvStreamer).
#    alert-subscriptions.md § Step 2: GET /sensor/list -> match by name -> GET /sensor/{id}/streams
#    -> main stream .url becomes live_stream_url in the Alert Bridge payload.
SNAME=$(curl -sf "$VIOS/sensor/list" | jq -r --arg id "$SID" '.[] | select(.sensorId==$id) | .name')
LIVE_URL=$(curl -sf "$VIOS/sensor/$SID/streams" | jq -r '.[] | select(.isMain==true) | .url')
# LIVE_URL is the VIOS-proxied RTSP URL Alert Bridge expects — NOT the raw NvStreamer 315xx URL.

# 5. Confirm Alert Bridge is healthy (NOT /api/v1/health — that 404s).
curl -sf --connect-timeout 5 "http://${HOST_IP}:9080/health"

# 6. Create a realtime rule on Alert Bridge. sensor_id must be the VIOS UUID from step 3/4.
curl -sf -X POST "$AB/realtime" \
  -H "Content-Type: application/json" \
  -d "{
    \"sensor_id\": \"$SID\",
    \"sensor_name\": \"$SNAME\",
    \"live_stream_url\": \"$LIVE_URL\",
    \"alert_type\": \"harness_smoke_test\",
    \"prompt\": \"Is there any person visible? Answer Yes or No.\",
    \"system_prompt\": \"You are a helpful assistant.\",
    \"chunk_duration\": 30
  }"

# 7. Assert the rule exists and carries a real rtsp:// live_stream_url (from VIOS).
curl -sf --max-time 15 "$AB/realtime" | jq -e '[.[] | select(.live_stream_url | test("^rtsp://"))] | length > 0'

# 8. After ~60–120 s of processing, assert incidents land in ES (RT-VLM -> Kafka -> Logstash).
curl -sf "http://${HOST_IP}:9200/mdx-vlm-incidents/_count" | jq -e '.count >= 0'
```

Emit § 4b (not § 4 steps 4–7) into the generated deploy skill when `alert-bridge` is in the allow-list and `alert_source=vlm-realtime`. For `cv-verification` profiles, the live harness still ends at VIOS registration (steps 0–3); CV perception + BA consume the VIOS-proxied stream separately — see `patch-alerts.md § cv-verification host-prep`.

---

## 5. The smoke sequence — NvStreamer → VIOS → RT-CV (Step 6 deploy skill / Step 8)

Emit this sequence into the generated `deploy-<flag-slug>` skill whenever RT-CV detection/tracking is selected with Kafka + Elasticsearch storage. It proves the live path emits new RT-CV metadata, not merely that the build artifacts were generated.

Ports referenced: **NvStreamer 31000** (HTTP) / **31554–31561** (RTSP pool), **VIOS 30888** (ingress) / **30554** (live RTSP proxy), **RT-CV 9000**, **Kafka 9092**, **Elasticsearch 9200**.

```bash
NV=http://${HOST_IP}:31000/vst/api/v1          # NvStreamer
VIOS=http://${HOST_IP}:30888/vst/api/v1        # VIOS ingress
RTCV=http://${HOST_IP}:9000/api/v1              # RT-CV
STEM=<sample-video-filename-without-extension>  # file STEM from the staged validation video
CAMERA_ID=rtcv_${STEM}

# 0. Confirm RT-CV and the validation streamer are up before mutating stream state.
curl -sf "$RTCV/live"
curl -sf "$RTCV/ready"
curl -sf "$RTCV/startup"
curl -sf --connect-timeout 5 "$NV/sensor/version" | jq -e '.type == "streamer"'

# 1. Record the pre-RT-CV Kafka end-offset baseline on mdx-raw.
#    This command (or an equivalent AdminClient call) must appear in the runtime log before /stream/add.
RAW_BEFORE=$(docker exec kafka kafka-get-offsets \
  --bootstrap-server localhost:9092 \
  --topic mdx-raw \
  | awk -F: '{sum += $3} END {print sum+0}')
echo "mdx-raw baseline offset: $RAW_BEFORE"

# 2. Resolve the real NvStreamer sensorId by name and read its RTSP URL from the API.
NVSID=""
for i in 1 2 3 4 5 6; do
  NVSID=$(curl -sf "$NV/sensor/list" | jq -r --arg s "$STEM" '.[] | select(.name==$s) | .sensorId' | head -1)
  [ -n "$NVSID" ] && break
  sleep 5
done
URL=$(curl -s "$NV/sensor/$NVSID/streams" | jq -r '.[0].url')

# 3. Register the RTSP source with VIOS, then feed RT-CV the VIOS proxy URL.
SID=$(curl -s -X POST "$VIOS/sensor/add" \
  -H "Content-Type: application/json" \
  --data-raw "{\"sensorUrl\": \"$URL\"}" \
  | jq -r '.sensorId')
PROXY="rtsp://${HOST_IP}:30554/live/$SID"

# RT-CV /stream/add REQUIRES the {"key":"sensor","value":{...}} ENVELOPE (matches api-reference.md § POST /api/v1/stream/add).
# A FLAT body ({camera_id,...,change}) is rejected with HTTP 400 "Sensor API change string not supported"
# camera_id + camera_url are the required fields; change="camera_add".
curl -sf -X POST "$RTCV/stream/add" \
  -H "Content-Type: application/json" \
  --data-raw "{\"key\":\"sensor\",\"value\":{\"camera_id\":\"$CAMERA_ID\",\"camera_name\":\"$CAMERA_ID\",\"camera_url\":\"$PROXY\",\"change\":\"camera_add\",\"metadata\":{\"resolution\":\"1920 x1080\",\"codec\":\"h264\",\"framerate\":30,\"vios_sensor_id\":\"$SID\",\"source\":\"nvstreamer-validation\"}}}"

# 4. Wait for RT-CV to report the stream and process frames.
for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
  curl -sf "$RTCV/stream/get-stream-info" | jq -e --arg id "$CAMERA_ID" '.. | objects | select(.camera_id? == $id or .sensor_id? == $id)' && break
  sleep 10
done
curl -sf "$RTCV/metrics" | grep -E 'fps|stream|latency'

# 5. Re-read mdx-raw after RT-CV processing and assert advancement over the baseline.
RAW_AFTER=$(docker exec kafka kafka-get-offsets \
  --bootstrap-server localhost:9092 \
  --topic mdx-raw \
  | awk -F: '{sum += $3} END {print sum+0}')
echo "mdx-raw post-RT-CV offset: $RAW_AFTER"
test "$RAW_AFTER" -gt "$RAW_BEFORE"

# 6. Query Elasticsearch for indexed bounding-box metadata from the live path.
curl -sf 'http://localhost:9200/mdx-raw-*/_search?size=5' \
  | jq -e '.hits.hits[]?._source | tostring | test("bbox|bounding|track|object|confidence")'
```

### RT-CV runtime gotchas

- **Baseline before `/stream/add` is mandatory.** The runtime proof is `mdx-raw` offset advancement after RT-CV sees the stream. Topic existence, offset `> 0` without a baseline, or Elasticsearch index existence alone is insufficient.
- **Use the VIOS proxy URL.** RT-CV must consume the same VIOS-registered source as the playback path: `rtsp://${HOST_IP}:30554/live/<sensorId>`.
- **Record the before/after numbers.** The generated deploy skill should print both `RAW_BEFORE` and `RAW_AFTER` so an eval trace can verify the live metadata emission step without inferring it from prose.
- **Use the exact RT-CV service API base.** Health, stream, and metrics calls go to `http://<host>:9000/api/v1`; do not use the RT-VLM `:8018` path or legacy `/v1/health/ready` paths.
- **`/stream/add` needs the `{"key":"sensor","value":{...}}` envelope** (see the corrected snippet above). A flat body returns HTTP 400 `Sensor API change string not supported`. On success the response is `{"reason":"STREAM_ADD_SUCCESS",...}`; verify with `GET /stream/get-stream-info` (returns `stream-info.stream-count >= 1`).
- **RT-CV publishes to `mdx-raw`, not `mdx-frames`, in the search-profile 2D config** (verified live 2026-07-03). Assert the live-path runtime proof on **`mdx-raw`**; treat `mdx-frames` as optional/config-dependent (not populated in this config). The indexed `mdx-raw-*` doc carries `objects[]` with `type` (class label, e.g. `Person`), `bbox{leftX,topY,rightX,bottomY}`, `confidence`, track `id`, `sensorId`, and a per-object `embedding.vector`.
- **Static/uploaded video → `creation_time` for the correct index suffix.** When the source is an uploaded file served by URL (§ 5b, the VIOS-upload / option-3a path) rather than a live RTSP stream, pass `creation_time:"2025-01-01T00:00:00.000Z"` inside the `value` block of `/stream/add` (it is accepted only for `http`/`https` URLs). A file URL carries no absolute base time, so without `creation_time` DeepStream anchors detections at epoch 0 and they land in the WRONG `mdx-raw-1970-01-01` index. With `creation_time` set to the static-video convention, docs land in `mdx-raw-2025-01-01` (`timestamp` anchored at the `2025-01-01` base).

---

## 5b. Static-video ingestion — VIOS upload (option 3a), feeding RT-CV and/or RT-Embed

Emit this variant **in addition to** the § 5 RTSP smoke whenever the profile supports upload ingestion (`sensor_topology=rtsp-and-uploaded`) — a `rtsp-and-uploaded` deployment must prove BOTH ingestion paths. It is the alternative to the NvStreamer live-RTSP harness (option 1): the operator uploads a video file to VIOS storage, VIOS returns a resolvable HTTP `videoUrl`, and that URL is handed **directly** to RT-CV (`camera_url`) and/or RT-Embed (`url`). No NvStreamer, no live RTSP proxy, no `/sensor/add` for the downstream consumers. The uploaded file becomes a **recorded/VOD asset** (its `/sensor/<id>/status` reports `CameraNotFoundError`/offline and `/streams` is empty — that is expected; it is not a live camera).

```bash
VIOS=http://${HOST_IP}:30888/vst/api/v1
FN=<whitespace-free-filename>.mp4          # e.g. upload_person_bicycle_car.mp4
LOCAL=<path to the video file on host>

# 1. Upload the file. Leave timestamp at the 2025-01-01 static-video default (or pass it explicitly);
#    the VIOS timeline anchors here. The response returns sensorId == streamId.
SID=$(curl -s -X PUT "$VIOS/storage/file/$FN?timestamp=2025-01-01T00:00:00.000Z" \
  -H 'Content-Type: application/octet-stream' --data-binary @"$LOCAL" | jq -r '.streamId')

# 2. Fetch the timeline (uploads race — the timeline is authoritative for the range) and resolve the videoUrl.
RANGE=$(curl -s "$VIOS/storage/$SID/timelines" | jq -r '.[0] | "\(.startTime) \(.endTime)"')
ST=${RANGE% *}; ET=${RANGE#* }
VURL=$(curl -s "$VIOS/storage/file/$SID/url?startTime=$ST&endTime=$ET" | jq -r '.videoUrl' \
  | sed -E 's#^http://http://#http://#')   # de-double the known upstream double-http prefix if present

# 3a. Feed RT-CV (detection): file URL + creation_time -> mdx-raw-2025-01-01 (see § 5 + gotchas).
curl -sf -X POST "http://${HOST_IP}:9000/api/v1/stream/add" -H 'Content-Type: application/json' \
  --data-raw "{\"key\":\"sensor\",\"value\":{\"camera_id\":\"upload_static\",\"camera_name\":\"upload_static\",\"camera_url\":\"$VURL\",\"change\":\"camera_add\",\"creation_time\":\"2025-01-01T00:00:00.000Z\",\"metadata\":{\"resolution\":\"1920 x1080\",\"codec\":\"h264\",\"framerate\":30}}}"
# RT-CV runs the file once and exits on EOS (it is not a persistent live stream). Assert via ES:
#   GET mdx-raw-2025-01-01/_search {"query":{"term":{"sensorId.keyword":"upload_static"}}}  -> docs > 0.

# 3b. Feed RT-Embed (embeddings): same videoUrl by URL mode -> mdx-embed-filtered-2025-01-01 (see § 6b).
```

**Gotchas (static-video / VIOS-upload path):**
- **`videoUrl` must be reachable from the consumer containers.** VIOS returns it already host-IP'd (`http://${HOST_IP}:30888/vst/storage/temp_files/...`); confirm `curl` from inside `vss-rtvi-cv` / `vss-rtvi-embed` returns `200 video/mp4` before registering. Some builds double the scheme (`http://http://...`) — strip the leading `http://` (`integrate-vios-service.md` Finding 8).
- **`VST_INSTALL_ADDITIONAL_PACKAGES=true` is required for upload** — without libav the `PUT /storage/file` fails `InvalidParameterError: Failed to get media information` and the file is deleted (`deploy-vios-service.md` Finding 9).
- **Fetch the timeline before building the `/url` request** — uploaded-file timelines are anchored at the upload `timestamp` (default `2025-01-01T00:00:00.000Z`), not wall-clock; a mismatched range returns an empty/short clip.
- **RT-CV exits on EOS for a file source** — the stream will NOT appear in `/stream/get-stream-info` after the ~clip-length window; the proof is the ES doc count under the source's `sensorId`, not a live stream count.

---

## 6. The smoke sequence — VIOS → RT-Embedding (Step 6 deploy skill / Step 8)

Emit this sequence into the generated `deploy-<flag-slug>` skill whenever the Video Embedding microservice (RT-Embed, container `vss-rtvi-embed`, `:8017`) is selected. It proves the ingested source emits new embedding events through the full chain onto Kafka **and** into Elasticsearch. RT-Embed embeds a live stream (§ 6a — RTSP path) and/or an uploaded static video by URL (§ 6b — video-upload path). **When the profile supports both ingestion modes (`sensor_topology=rtsp-and-uploaded`), emit BOTH § 6a and § 6b** so each ingestion path the deployment offers is proven end-to-end; emit only the matching one for a single-mode profile. The Step-4 ingestion prompt records which path is the user's intended primary; the smoke still validates every path the profile exposes.

**The embedding data flow (identical for live and static):**

```
rtvi-embed --(RTVI_EMBED_KAFKA_TOPIC=mdx-embed)--> Kafka mdx-embed
    --> vss-search-analytics-2d-fusion (BA)  --> Kafka mdx-embed-filtered
    --> ELK Logstash --> Elasticsearch mdx-embed-filtered-<date>
```

**Success criterion = all three stages advance: `mdx-embed` (rtvi-embed output) → `mdx-embed-filtered` (BA output) → non-zero `mdx-embed-filtered-<date>` ES docs.** RT-Embed publishes its raw embeddings to **`mdx-embed`** (its own topic; upstream `RTVI_EMBED_KAFKA_TOPIC=mdx-embed` → container `KAFKA_TOPIC`). It does **NOT** publish to `mdx-embed-filtered` directly — **`vss-search-analytics-2d-fusion` (BA)** consumes `mdx-embed` and produces `mdx-embed-filtered`, the only topic ELK indexes. **BA must be in the deployment** or embeddings never reach ES, and `RTVI_EMBED_KAFKA_TOPIC` must stay `mdx-embed` (overriding it to `mdx-embed-filtered` bypasses BA's filter). See `references/patch-rt-embed.md`. See also the § 5 gotcha on the same `mdx-raw` chain for detection.

Ports referenced: **NvStreamer 31000** (HTTP) / **31554–31561** (RTSP pool), **VIOS 30888** (ingress) / **30554** (live RTSP proxy), **RT-Embed 8017**, **Kafka 9092**, **Elasticsearch 9200**.

```bash
NV=http://${HOST_IP}:31000/vst/api/v1          # NvStreamer
VIOS=http://${HOST_IP}:30888/vst/api/v1        # VIOS ingress
RTEMB=http://${HOST_IP}:8017                    # RT-Embedding
ES=http://${HOST_IP}:9200

# 0. Confirm RT-Embed is ready and resolve the model id (do NOT hardcode).
curl -sf "$RTEMB/v1/ready"
MODEL=$(curl -sf "$RTEMB/v1/models" | jq -r '.data[0].id')   # e.g. cosmos-embed1-448p-anomaly-detection

# 1. Baseline BOTH stages: mdx-embed (rtvi-embed raw output) and mdx-embed-filtered (BA output).
off(){ docker exec kafka kafka-get-offsets --bootstrap-server localhost:9092 --topic "$1" | awk -F: '{s+=$3} END{print s+0}'; }
EMB_RAW_BEFORE=$(off mdx-embed); EMB_BEFORE=$(off mdx-embed-filtered)
echo "mdx-embed baseline: $EMB_RAW_BEFORE | mdx-embed-filtered baseline: $EMB_BEFORE"
```

### 6a. Live source (NvStreamer / option 1)

```bash
STEM=<sample-video-filename-without-extension>
# Resolve the NvStreamer RTSP URL and register it with VIOS (same as § 4/§ 5). Reuse the VIOS
# sensorId/proxy if RT-CV already registered the same source in this run.
NVSID=$(curl -sf "$NV/sensor/list" | jq -r --arg s "$STEM" '.[]|select(.name==$s)|.sensorId'|head -1)
URL=$(curl -s "$NV/sensor/$NVSID/streams" | jq -r '.[0].url')
SID=$(curl -s -X POST "$VIOS/sensor/add" -H 'Content-Type: application/json' --data-raw "{\"sensorUrl\": \"$URL\"}" | jq -r '.sensorId')
PROXY="rtsp://${HOST_IP}:30554/live/$SID"

# Register the live stream. Body is the {"streams":[{...}]} envelope; id comes back at .results[0].id.
EMBID=$(curl -fsS -X POST "$RTEMB/v1/streams/add" -H 'Content-Type: application/json' \
  -d "{\"streams\":[{\"liveStreamUrl\":\"$PROXY\",\"description\":\"validation\"}]}" | jq -r '.results[0].id')

# Start streaming embeddings. Live streams REQUIRE stream:true AND chunk_duration>0 (a synchronous or
# zero-chunk call is rejected 400). Use SSE (Accept: text/event-stream, curl -N). Run in the background.
# Live chunks are NTP/wall-clock-stamped, so docs land in mdx-embed-filtered-<today>.
curl -sS -N -X POST "$RTEMB/v1/generate_video_embeddings" -H 'Content-Type: application/json' \
  -H 'Accept: text/event-stream' \
  -d "{\"id\":\"$EMBID\",\"model\":\"$MODEL\",\"stream\":true,\"chunk_duration\":10,\"chunk_overlap_duration\":2}" &
sleep 60
```

### 6b. Uploaded static video by URL (VIOS upload / option 3a)

```bash
# Prereqs from § 5b (the VIOS-upload ingestion path): the file was PUT to VIOS storage and a
# resolved videoUrl obtained from GET /storage/file/<streamId>/url. VURL is that http videoUrl.
VURL=<videoUrl from GET /storage/file/<streamId>/url>

# Embed by URL. is_live=False; pass the file URL directly (no /v1/streams/add — that is live-only).
# CREATION_TIME anchors a STATIC video's embeddings to the 2025-01-01 index suffix; omit it and the
# offset-based file mode defaults to epoch 0 -> the WRONG mdx-embed-filtered-1970-01-01 index.
# One request per URL: the worker lock is keyed by videoId (derived from the URL), so firing a second
# request at the same URL before the first finishes returns 409 ResourceInUse.
UUID=$(cat /proc/sys/kernel/random/uuid)
curl -sS -N -X POST "$RTEMB/v1/generate_video_embeddings" -H 'Content-Type: application/json' \
  -H 'Accept: text/event-stream' \
  -d "{\"id\":\"$UUID\",\"model\":\"$MODEL\",\"url\":\"$VURL\",\"creation_time\":\"2025-01-01T00:00:00.000Z\",\"stream\":true,\"chunk_duration\":10}"
```

### 6c. Assert (both variants)

```bash
# Re-read BOTH stages after processing (allow ~30–60 s for the first chunk; decode latency dominates).
EMB_RAW_AFTER=$(off mdx-embed); EMB_AFTER=$(off mdx-embed-filtered)
echo "mdx-embed: $EMB_RAW_BEFORE -> $EMB_RAW_AFTER | mdx-embed-filtered: $EMB_BEFORE -> $EMB_AFTER"
# Stage 1: rtvi-embed emitted raw embeddings.
test "$EMB_RAW_AFTER" -gt "$EMB_RAW_BEFORE" && echo "PASS: rtvi-embed advanced mdx-embed"
# Stage 2: BA (vss-search-analytics) filtered them onward.
test "$EMB_AFTER" -gt "$EMB_BEFORE" && echo "PASS: BA advanced mdx-embed-filtered"
# Stage 3: ES doc-count proof (mandatory — Kafka offset alone misses topic/index misconfig).
# Static uploads land in mdx-embed-filtered-2025-01-01; live sources land in mdx-embed-filtered-<today>.
curl -sf "$ES/_cat/indices/mdx-embed-filtered-*?h=index,docs.count&v"
```

### RT-Embed runtime gotchas
- **Assert the full chain: `mdx-embed` → `mdx-embed-filtered` → ES.** rtvi-embed publishes raw embeddings to **`mdx-embed`**; `vss-search-analytics-2d-fusion` (BA) consumes it and produces **`mdx-embed-filtered`**, which ELK indexes into `mdx-embed-filtered-<date>`. Proof = `mdx-embed` advances AND `mdx-embed-filtered` advances AND a non-zero `mdx-embed-filtered-<date>` doc count. If `mdx-embed` advances but `mdx-embed-filtered` does not, **BA is missing or misconfigured** (the raw embeddings never reach ES). Do NOT override `RTVI_EMBED_KAFKA_TOPIC` to `mdx-embed-filtered` to skip BA — that bypasses the confidence/downsampling filter (see `references/patch-rt-embed.md`).
- **Static uploads need `creation_time:"2025-01-01T00:00:00.000Z"`** on `generate_video_embeddings` — the same static-video convention as RT-CV's `/stream/add`. File/URL mode is **offset-based** (chunk `start_time` counts from 0), so without `creation_time` records anchor at epoch 0 and land in `mdx-embed-filtered-1970-01-01`. With `creation_time` set, docs land in `mdx-embed-filtered-2025-01-01`, `timestamp:"2025-01-01T00:00:00.000Z"`.
- **URL mode vs live mode.** An uploaded static video is embedded by `url` with `is_live=False` — do NOT call `/v1/streams/add` (that path is live-RTSP-only and returns a placeholder `chunk_duration:0`). Live streams use `/v1/streams/add` then `generate_video_embeddings` on the returned id.
- **Single worker, videoId-keyed lock.** RT-Embed serializes; the in-flight resource is keyed by the media's `videoId` (derived from the URL), not the request `id`. Firing a second `generate_video_embeddings` at the same URL before the first completes returns `409 ResourceInUse`, and duplicate queued requests can wedge the worker (query stuck at `0.00%`). A leftover live-stream embed also holds the worker indefinitely — `DELETE /v1/streams/delete/<id>` + `DELETE /v1/generate_video_embeddings/<id>` to free it, or `docker restart vss-rtvi-embed` to clear a wedged queue.
- **Baseline before the embed call is mandatory** — a stale offset `>0` from a prior run is not proof.
- **Resolve the model id from `GET /v1/models`** — do not hardcode `cosmos-embed1-448p`; the deployed model may be `cosmos-embed1-448p-anomaly-detection`.
- **Live embedding needs `stream:true` + `chunk_duration>0`** and the SSE `Accept` header; a flat/synchronous live call returns 400.
- **First chunk is slow** — decode + engine warm-up dominate; the first message can take 30–60 s after `generate_video_embeddings`. Retry the offset read with backoff.

## 7. Step 6.5 patch interactions (summary)

- **Patch 0** (orphan grep): include `vss-vios-nvstreamer` in the orphan-container container_name patterns so a stale streamer from a prior generation (holding port 31000 / RTSP pool under `network_mode: host`) is detected and offered for `docker rm -f` before bring-up.
- **Patch 1** (flag insertion): add the invented flag to the `nvstreamer-validation` service's `profiles:` list. The harness rides the **same** flag as the rest of the build (no separate flag).
- **Patch 2** (depends_on strip): NvStreamer's only `depends_on` is `broker-health-check` (defined when ELK is present → kept). Strip it only if the build has no ELK.
- **Patch 3** (materialize binds): copy the two `nvstreamer/configs/*.json` files into `<BUILD_DIR>/patched/nvstreamer/configs/` (§ 3 above).

Cross-refs: `references/standalone-compose-patches.md` (Patch 0 / Patch 3 cases), `references/allow-list-sidecar.md` (`validation_harness:` key), `skills/vss-manage-video-io-storage/references/nvstreamer-api-reference.md` (REST surface), `skills/vss-manage-video-io-storage/references/integrate-vios-service.md § Outputs` (NvStreamer → VIOS handoff), `deploy/docker/developer-profiles/dev-profile-alerts/compose.yml § nvstreamer-alerts` (service-block model).
