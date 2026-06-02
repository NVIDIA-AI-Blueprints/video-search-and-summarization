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
- **The skill does NOT generate or fetch video content on its own.** The canonical default sample for IN-1 / streaming-warehouse work is **`warehouse_safety_0001.mp4`** (H.264, warehouse content, matches the IN-1 prompt). Stage it into `${VSS_DATA_DIR}/videos/<build-name>/`; on the validated eval host it is sourced from `/home/ubuntu/sochoa/dev-profile-sample-data/warehouse_safety_0001.mp4`. If that file is unavailable, prompt the operator for a path to any whitespace-free H.264/H.265 sample. In eval-harness mode, the harness env documents the sample path.
- NvStreamer **auto-discovers** files present in the videos directory at startup (or on `POST /sensor/scan`). For auto-discovered files, `sensorId == streamId == name == filename-without-extension` (`nvstreamer-api-reference.md § 2` / § 7). No upload call is required — staging the file IS the registration.

---

## 4. The smoke sequence — NvStreamer → VIOS → RT-VLM (Step 6 deploy skill / Step 8)

This is the canonical streaming-path validation. It is emitted into the generated `deploy-<flag-slug>` skill's post-deploy smoke test and is the basis for the eval's live-path checks. All endpoints below assume `network_mode: host`, so use `${HOST_IP}` (NOT `localhost`) anywhere a URL is handed to *another container* (VIOS Finding 12 — VIOS-proxied RTSP fed to RT-VLM must use `${HOST_IP}`).

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

# 4. VIOS now proxies the stream at rtsp://${HOST_IP}:30554/live/<SID>. Feed THAT to RT-VLM (use ${HOST_IP}).
#    /v1/streams/add takes the {"streams":[{...}]} ENVELOPE (a flat {"liveStreamUrl":...} body is
#    rejected with InvalidParameters "('body', 'streams'): Field required" — Finding F-D, 2026-06-02;
#    matches integrate-rt-vlm.md § Inputs). The stream_id comes back under .results[0].id.
PROXY="rtsp://${HOST_IP}:30554/live/$SID"
STREAM_ID=$(curl -s -X POST "$RTVLM/v1/streams/add" \
  -H "Content-Type: application/json" \
  -d "{\"streams\": [{\"liveStreamUrl\": \"$PROXY\", \"description\": \"nvstream validation\"}]}" \
  | jq -r '.results[0].id')

# 5. Drive captions on the live stream by the stream_id from step 4 (id field; stream:true and a
#    non-empty prompt are both mandatory per RT-VLM /v1/generate_captions). chunk_duration > 0.
curl -s -N -X POST "$RTVLM/v1/generate_captions" \
  -H "Content-Type: application/json" \
  -d "{\"id\": \"$STREAM_ID\", \"model\": \"<resolved-model-id>\", \"stream\": true, \"prompt\": \"Describe the scene.\", \"chunk_duration\": 10}"

# 6. Assert captions land on Kafka mdx-vlm-captions AND in ES default_<id> FROM THE LIVE PATH.
docker exec kafka kafka-get-offsets --bootstrap-server localhost:9092 --topic mdx-vlm-captions   # offsets > 0
curl -sf 'http://localhost:9200/_cat/indices?h=index,docs.count&v' | awk '$1 ~ /^default_/ && $2+0 > 0'
```

### Gotchas (cite when debugging)

- **Read the RTSP port from the API.** The 315xx RTSP port is assigned by NvStreamer's load balancer at start-up; it is NOT tied to filename or alphabetic order (`nvstreamer-api-reference.md § 2`). Constructing `rtsp://host:31554/...` will intermittently hit the wrong port.
- **`sensorUrl`, not `url`.** VIOS `POST /sensor/add` takes `sensorUrl` (`integrate-vios-service.md § API Schema` line ~172 / `api-reference.md § 6`). Using `url` silently fails parameter validation.
- **`${HOST_IP}`, not `localhost`, for the VIOS proxy URL handed to RT-VLM.** Both run `network_mode: host`, but a `localhost` URL passed into the RT-VLM container resolves to the container's own loopback (VIOS Finding 12). Use `${HOST_IP}`.
- **Discovery latency.** The streams list and codec metadata populate asynchronously (~5 s for the sensor to appear, ~15–30 s for `metadata.codec`). Retry with backoff (step 1); if you need codec immediately, call `GET $NV/storage/file/mediainfo?sensorId=$STEM`.
- **No `/sensor/add` on NvStreamer.** Registration with VIOS happens on the VIOS port (30888), not on NvStreamer (31000). NvStreamer has no upstream-camera concept (`nvstreamer-api-reference.md § 3`).
- **Register with VIOS only after the full SDRC/cluster is healthy (Finding F-E, 2026-06-02).** A sensor registered while `sdr-controller` or Redis is still unhealthy reports `state: online` on `/sensor/<id>/status` but its VIOS live proxy (`rtsp://${HOST_IP}:30554/live/<id>`) silently does NOT serve — RT-VLM `/v1/streams/add` then fails with `Could not connect to the RTSP URL or there is no video stream`. Confirm `sdr-controller` logs show `Redis Listener started` (not a `ConnectionRefusedError` crash-loop) before `POST /sensor/add`. If the proxy fails to serve, delete and re-add the sensor once the cluster is healthy — the re-registration produces a working proxy.
- **ES doc count is mandatory** (`feedback_smoke_test_completeness.md`). Kafka offset advance alone misses topic-name misconfigurations — assert `default_<id>` doc count from the live path, distinct from any VOD/upload check.

---

## 5. Step 6.5 patch interactions (summary)

- **Patch 0** (orphan grep): include `vss-vios-nvstreamer` in the orphan-container container_name patterns so a stale streamer from a prior generation (holding port 31000 / RTSP pool under `network_mode: host`) is detected and offered for `docker rm -f` before bring-up.
- **Patch 1** (flag insertion): add the invented flag to the `nvstreamer-validation` service's `profiles:` list. The harness rides the **same** flag as the rest of the build (no separate flag).
- **Patch 2** (depends_on strip): NvStreamer's only `depends_on` is `broker-health-check` (defined when ELK is present → kept). Strip it only if the build has no ELK.
- **Patch 3** (materialize binds): copy the two `nvstreamer/configs/*.json` files into `<BUILD_DIR>/patched/nvstreamer/configs/` (§ 3 above).

Cross-refs: `references/standalone-compose-patches.md` (Patch 0 / Patch 3 cases), `references/allow-list-sidecar.md` (`validation_harness:` key), `skills/vss-manage-video-io-storage/references/nvstreamer-api-reference.md` (REST surface), `skills/vss-manage-video-io-storage/references/integrate-vios-service.md § Outputs` (NvStreamer → VIOS handoff), `deploy/docker/developer-profiles/dev-profile-alerts/compose.yml § nvstreamer-alerts` (service-block model).
