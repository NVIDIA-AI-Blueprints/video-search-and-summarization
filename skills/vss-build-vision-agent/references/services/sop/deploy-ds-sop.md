# Deployment Reference: DS-SOP

## Container Image

- **Image name** — `ds-sop` (env `${DS_SOP_IMAGE:-ds-sop:1.0.0}`). NOTE: the service is **DS-SOP** (capability `sop-detection`, self-named key `ds-sop`); only the docker image is `ds-sop:1.0.0`.
- **Tag** — `1.0.0`
- **Registry** — **local build only** (no registry). Built from `microservices/sop-inference-bp/` in `NVIDIA/sop-monitoring-blueprints` (branch `main`): `NV_DS_SOP_IMAGE=ds-sop:1.0.0 docker compose -f deploy/compose.yaml build` → `ds-sop:1.0.0` (manual: `docker build . -f docker/Docker.build -t ds-sop:1.0.0`). This source ships the **annotated RTSP output** (`:8554/ds-out`, gated by `ENABLE_RTSP_OUTPUT`) that DS-SOP re-streams for VIOS to record; the only code patch is `ddm_pytorch2.patch`, applied by `docker/Docker.build` during the build. See the `vss-build-ds-sop` skill (Step 0 clones the source; Step 1 builds as-is; Step 2 standalone smoke test).
- **Base images** (pulled at build, need `docker login nvcr.io`) — `nvcr.io/nvidia/deepstream:8.0-triton-multiarch` + `nvcr.io/nvidia/blueprint/vss-engine:2.4.1`.
- **NGC pull requirements** — none at runtime (image is local); models are pulled separately (see Storage). The build step needs nvcr.io access.
- **Architecture** — x86_64 (Docker.build also supports aarch64/sbsa variants).

## GPU Requirements

- **GPU required?** — **yes**. Runs DDM-Net (TensorRT/Triton in-process) + Cosmos-Reason-1.1-7B via in-process vLLM, both on one GPU.
- **Minimum VRAM** — model load alone is ~15.6 GB; budget ≥ 28 GB for model + vLLM KV cache + DDM + DeepStream buffers. Fits one L40S (48 GB) or H100 (80 GB).
- **Supported GPU arch** — Ada Lovelace (L40S), Hopper (H100/H200), Ampere (A100).
- **GPU count per instance** — 1 (`NVIDIA_VISIBLE_DEVICES=0`, `DS_SOP_NUM_GPUS=1`).
- **Can share GPU with other services?** — No. It expects a dedicated GPU; do not co-place with RT-VLM or another VLM NIM on the same GPU.
- **Device reservation** — uses `runtime: nvidia` + `NVIDIA_VISIBLE_DEVICES`, not `deploy.resources.reservations` (DeepStream container convention).

> **VRAM footgun:** the vLLM default `VLLM_GPU_MEMORY_UTILIZATION=0.3` is tuned for an 80 GB H100 (24 GB). On a 48 GB L40S, `0.3` (14.4 GB) is **smaller than the 15.6 GB model** → boot fails with `ValueError: No available memory for the cache blocks`. Set `VLLM_GPU_MEMORY_UTILIZATION=0.6` on 48 GB GPUs.

## CPU & Memory

- **`shm_size`** — `16gb` (required; DeepStream + vLLM).
- **`ulimits`** — `memlock: -1`, `stack: 67108864`.
- **`ipc`** — `host`.
- **Other** — `privileged: true`, `/dev/snd` device + pulse socket (only used when `ENABLE_ALERT_SOUND=1`; harmless otherwise).

## Storage

| Mount Path (host → container) | Purpose | Type | Size | Permissions |
|---|---|---|---|---|
| `${MODEL_ROOT_DIR:-/opt/models}` → same | DDM + cosmos-reason checkpoints | bind | ~18 GB | readable by uid 1001 |
| `${HOST_CACHE:-$HOME/.cache/ds_sop}` → `/opt/nvidia/nvds_sop/.cache` | vLLM / HF cache | bind | grows | **`chown -R 1001:1001`** (must be writable by container uid 1001) |
| `${ACTION_CONFIG_PATH}` → same | SOP action set JSON | bind (file) | tiny | readable |
| `${VLM_PROMPT_PATH}` → same | VLM prompt template | bind (file) | tiny | readable |

Models are **not** baked into the image — stage on the host before bring-up. DS-SOP only **verifies** them at startup: `/opt/models/vlm/checkpoint` (Cosmos-Reason VLM — checks `…/config.json`), `/opt/models/gbed_models/ddm/checkpoint.pth.tar` (DDM), and `/opt/sop/configs/{actions.json,vlm_prompts.txt}` — it does **NOT** download them. Obtain them by retraining via the SOP Training Blueprint, or from NGC `nv-metropolis-dev/vss-industrial/sop-data:1.0` (~14.7 GB; lays VLM at `/opt/models/cosmos-reason1.1-7b/checkpoint`, `ddm_weights/ddm.ckpt`→DDM, `configs/*`→configs). Set `VLLM_MODEL_PATH` to the staged VLM path. Test video: NGC `sop-server-fan-installation-data:1.0-260213` → transcode to `Install_1_h264_30fps.mp4`. (Stage models AFTER any tarball fully extracts — a mid-extract shard copy truncates → `safetensors ... incomplete metadata`.)

## Startup Behavior

- **Expected startup time** — ~60–120 s warm (DeepStream plugin init ~30 s → vLLM model load ~3 s + KV cache + CUDA-graph capture → DDM TRT init ~5 s → API server up).
- **Startup ordering** — `depends_on: kafka (service_started)`. Kafka must be healthy first.
- **Health / readiness** — `GET http://localhost:8300/v1/ready` returns `200` when ready. `GET /v1/models` also works and returns `{"data":[{"id":"ds_sop_model",...}]}`.
- **Log signatures of healthy startup**:
  - `DDM model initialized successfully`
  - `INFO: Application startup complete.` / `Uvicorn running on http://0.0.0.0:8300`
  - During processing: `VLM inference on chunk ... response: (N) <action>` and `chunk messaging delivered to mdx-vlm-captions [<partition>]`.
- **Annotated RTSP output** — with `ENABLE_RTSP_OUTPUT=true` + `RTSP_PORT=8554`, DS-SOP serves the overlaid stream at `rtsp://<host>:8554/ds-out/<stream-name>` **while a request is processing** — the server binds/tears down **per request** (and can drop mid-request under load without re-binding), so verify with `ffprobe rtsp://localhost:8554/ds-out/<stream-name>` during a live request. For unattended recording see § Known Deployment Issues → "Continuous (always-on) recording". Register it with VIOS for VST recording — see § Known Deployment Issues → "DS-SOP → VIOS recording".

## Known Deployment Issues

- **`No available memory for the cache blocks`** → raise `VLLM_GPU_MEMORY_UTILIZATION` (0.6 on 48 GB). See GPU section.
- **`safetensors ... incomplete metadata, file not fully covered`** → a VLM shard was copied before extraction finished; re-copy the shard.
- **0 docs in ES** → most often `ENABLE_MESSAGING` not set to `1` (compose default is `false`, so nothing publishes to Kafka), or `DEFAULT_TOPIC` overridden away from `mdx-vlm-captions` (the code default). Also check **listener addressing**: the stack's Kafka is dual-listener — bridge-network peers (logstash, the shipped `.conf` defaults) use `kafka:29092`, while the host-networked DS-SOP uses the host-published EXTERNAL listener `:9092`; pointing either side at the other's listener silently connects to nothing. Also confirm the topic is **created**: `mdx-vlm-captions` must be listed in `KAFKA_TOPICS` for the Kafka topic-init (and broker-health-check) — otherwise the producer gets `UnknownTopicOrPartitionException` and nothing lands. (In the VSS ELK/Kafka stack it is already a standard topic — RT-VLM uses it too — so a profile that unions ELK normally has it.) See `integrate-ds-sop.md`.
- **Kafka has messages but `mdx-vlm-captions-*` ES index is empty + Logstash logs `Google::Protobuf::ParseError`** → build-vision-agent's default ELK decodes this topic as PROTOBUF (RT-VLM), but DS-SOP emits JSON. **build-vision-agent CANNOT auto-wire this** — its Compose patches (`patches/<service>.yml`) only touch Compose YAML; no patch edits Logstash `pipelines.yml` or pipeline `.conf` files. So this is a **mandatory deploy-time step** build-vision-agent's deploy flow (or the operator) must run. Concrete procedure:

  ```bash
  # 1. drop the shipped JSON pipeline into the patched logstash kafka-pipelines dir
  #    (this dir mounts flat to /usr/share/logstash/pipelines/ in the container)
  cp <sop-ref-bundle>/sop-vlm-captions-json-logstash.conf \
     <BUILD_DIR>/patched/services/infra/elk/logstash/pipelines/kafka/
  # 2. register a SEPARATE pipeline-id in the patched pipelines-kafka.yml (do NOT merge into mdx-lvs)
  cat >> <BUILD_DIR>/patched/services/infra/elk/logstash/configs/pipelines-kafka.yml <<'YML'
  - pipeline.id: sop-vlm-captions-json
    path.config: "/usr/share/logstash/pipelines/sop-vlm-captions-json-logstash.conf"
  YML
  # 3. restart logstash, then verify
  docker restart logstash
  curl -s 'http://localhost:9200/_cat/indices/mdx-vlm-captions*?v'   # expect docs.count > 0
  ```
  See `integrate-ds-sop.md` § Known Integration Constraints → "ELK indexing". (A separate pipeline.id is additive — no upstream-config edit — and avoids the grok double-match/comma-laden-index issue of editing the shared conf in place.)
- **Redis / data_log perms** (ELK/VIOS peers) → `chmod -R 777 <MDX_DATA_DIR>/data_log` after first up; redis perm crash cascades to envoy proxies.
- **`Failed to start recording` (HTTP 500) on VIOS sensor-add** → transient; the recorder retries succeed once the RTSP upstream is flowing.
- **Live (realtime camera/source) input degenerates to all `(10) not belong` (`cv_boundary_score≈0` on every chunk)** → the live path uses **non-blocking, leaky** frame intake (`is_live` → drop-oldest / skip-new in `ds_sop_process.py`; source `leaky:2`), so if DDM-Net can't process frames as fast as they arrive (from the Basler camera or an RTSP source), the frame stream has gaps and boundary detection collapses. This is **GPU-throughput-dependent — NOT a fixed FPS limit**. The **on-demand file path blocks instead of dropping**, so it's unaffected at any FPS (a 30 fps file yields healthy boundary scores + real steps). On a capable GPU, DDM CV runs faster than realtime per 10 s @30 fps chunk, so a fast GPU sustains 30 fps live; only on a GPU where DDM falls behind do you need to **cap the source** to a sustainable rate — set `CAMERA_FPS_NUM`/`CAMERA_FPS_DEN` (e.g. `10`/`1`) or lower the source FPS. Prefer the on-demand path for deterministic validation regardless.
- **DS-SOP → VIOS recording is not auto-wired (mandatory deploy-time step)** → build-vision-agent composes DS-SOP + VIOS but never wires the video flow (true for RT-VLM too). Adding the sensor alone is **not enough**: VST's recorder SDR only provisions the stream after a Redis `camera_streaming` event, and recording must be started explicitly. Full sequence (`<stream-name>` = the **source stream's name** — the input video/camera id — not a VIOS sensorId; VST API base is `/vst/api/v1/...`, `/api/v1/...` 404s in vst nginx mode):
  ```bash
  # 1. add the DS-SOP :8554/ds-out stream as a VST sensor
  curl -X POST http://localhost:30888/vst/api/v1/sensor/add \
    -H 'Content-Type: application/json' \
    -d '{"sensorUrl":"rtsp://<HOST_IP>:8554/ds-out/<stream-name>","name":"<camera-id>"}'
  # 2. publish a `camera_streaming` event to Redis `vst.event` so the recorder/livestream SDRs
  #    provision the stream — in the VST microservices split this is NEVER published automatically,
  #    and DS-SOP's on-demand RTSP session does not auto-restart, so without it nothing records:
  #      XADD vst.event * sensor.id '<json: sensor.id (stream UUID), name, proxied RTSP url>'
  # 3. start recording (retry — the VST backend takes time to be ready); <stream_id> from /sensor/streams
  curl -X POST http://localhost:30888/vst/api/v1/record/<stream_id>/start
  ```
  `record/status` then reports the stream active (state `recording`/`active`; mode `user` because it was started here). VST's `always_recording` may auto-start it once the stream reaches recorder-ms — so record/start returning non-200 while `record/status` is already active is fine. **Easiest: run the blueprint's helper** `add-ds-sop-to-vst.py --rtsp_url rtsp://<HOST_IP>:8554/ds-out/<stream-name> --camera_id <name> --vst_endpoint http://localhost:30888/vst --record` (it does sensor/add → camera_streaming → record/start-with-retry). DS-SOP reads its camera/source **directly** — VIOS is NOT in the input path.
- **`record/status` never goes active (recorder SDR not provisioning the stream)** → VIOS recording has 3 common misconfigs: (1) the SDR recorder's `WDM_WL_ADD_URL` must be `/api/v1/record/stream/add` (**NOT** `/api/v1/proxy/stream/add`); (2) `WDM_WL_CHANGE_ID_ADD` must be `camera_streaming` (**NOT** `camera_proxy`); (3) `recorder-ms` needs a `STORAGE_MODULE_ENDPOINT` env var pointing at `storage-ms`. Debug: `docker logs <sdr-http-recorder> --tail 50` and `docker logs <recorder-ms> --tail 50`.
- **VST recording is truncated, or flips `off` while DS-SOP keeps running** → DS-SOP's in-pipeline RTSP output (`ENABLE_RTSP_OUTPUT`) is **not a persistent server**: it binds/tears down **per `/v1/chat/completions` request**, can hang the DeepStream pipeline on **no-NVENC GPUs**, and can **drop mid-request under load** — once it tears down mid-session it does **not** re-bind for the life of that request (only a **new** request re-binds `:8554`), while the inference session itself keeps producing Kafka chunks. Since VST never auto-re-provisions (see the `camera_streaming` note above), **one `:8554` blip = recording stays `off` until you re-arm**, even though DS-SOP looks healthy. Diagnostic signatures: `vss-vios-streamprocessing` logs `stream_monitor ... CURL error: Server returned nothing (no headers, no data) [52]` for the `:8554/ds-out` URL; DS-SOP chunks continue but with a **frozen, identical `cv_boundary_score`** and a repeating response — the pipeline is stalled. With a non-looping source (EOS after one pass) the symptom is a short fragment (~7 s of a 133 s clip). A **looping source** is necessary (keeps the input flowing) but **not sufficient** — it does not re-arm the output; for continuous recording see the next entry.
- **Continuous ("always-on") recording — two modes.** The blueprint's scripts are test-scoped (relays via `nohup`, a client that stops at its stream timeout, recording re-armed by hand); for unattended recording pick one of:
  1. **Standalone relay (the blueprint's canonical recording approach)** — do what `start_rtsp_server.sh` does: serve `:8554/ds-out/<stream-name>` from a **standalone looping RTSP relay** (`rtsp_server.py`-style, auto-restarts on EOS; run it as a `restart: always` service for unattended use) instead of DS-SOP's in-pipeline output. Immune to the in-pipeline stall/teardown above — this is exactly why the blueprint *"replaces the in-pipeline RTSP output"*. Trade-off: VST records a **stand-in of the source** (clip + clock overlay), **not** the SOP-annotated frames; SOP results still flow via Kafka → ES.
  2. **In-pipeline output + re-arm watcher (records the REAL annotated frames)** — keep `ENABLE_RTSP_OUTPUT=true` and run a small watcher loop: probe `:8554/ds-out` (or `record/status`); on failure (i) **restart the live `/v1/chat/completions` session** — a new request is the only thing that re-binds `:8554` — then (ii) **re-arm recording** (publish `camera_streaming` to Redis `vst.event`, then `POST /vst/api/v1/record/<stream_id>/start`). VST's built-in `always_recording` may auto-resume once the stream is re-registered with recorder-ms, but it is internal VST configuration (not exposed in the blueprint repo) — the explicit re-arm sequence is the reliable path. Short recording gaps at each blip are inherent to this mode. (The blueprint's deploy skill documents an equivalent self-healing loop — restart the DS-SOP container, reset the VST sensors, start a fresh session — not yet shipped in its scripts.)
- **ES has docs but Kibana shows nothing** → Kibana has no Data View for `mdx-vlm-captions-*`. The `sop-kibana-init` one-shot (in the ds-sop compose) imports the SOP data view + dashboard automatically after kibana is healthy — check it exited `0` (`docker ps -a | grep sop-kibana-init`). Note the stack's Kibana is served **under base path `/kibana`** (bare `:5601/api/...` 404s — this is standard for this stack; the upstream `vss-kibana-init` and the Kibana UI both use `:5601/kibana/...`); the one-shot hits `/kibana` first and falls back to a bare base path, and `KIBANA_URL` overrides the base. If it still failed (e.g. no internet to fetch the ndjson), import manually: download `sop-kibana-objects.ndjson` from `NVIDIA/sop-monitoring-blueprints` (`agentic/vss-sop-skills/vss-sop-build/references/deployments/sop/sop-app/kibana-dashboard/`) and `curl -X POST 'http://localhost:5601/api/saved_objects/_import?overwrite=true' -H 'kbn-xsrf: true' --form file=@sop-kibana-objects.ndjson` — or just create a Data View `mdx-vlm-captions*` with time field `@timestamp` in the Kibana UI. Also widen the time range (docs carry real wall-clock timestamps).
- **Responses come back free-form instead of `(N)` numbered SOP steps** → a custom `prompt` in the `/v1/chat/completions` request **overrides** `VLM_PROMPT_PATH` (USER_PROMPT_PRIORITY), so the VLM free-forms instead of classifying against the numbered SOP action set. For numbered SOP step classifications, send **no prompt** (let it use the configured `VLM_PROMPT_PATH` = `/opt/sop/configs/vlm_prompts.txt`) or pass that exact numbered prompt verbatim.

## Testing

- **Profile eval (this bundle):** `skills/vss-build-vision-agent/references/services/sop/evals/sop_deployment.json` — an operational
  eval (build-vision-agent convention: `{skills, resources, expects:[{query, checks}]}`) that
  validates the **delivered scope**: deploy the full profile → all containers Up → ELK
  `mdx-vlm-captions-*` shows REAL step responses (not all `(10)`), with the JSON Logstash pipeline
  registered. The VSS-Agent / report-generation phase is **out of scope** (see `integrate-ds-sop.md`
  § Scope & divergences).
- **Blueprint full suite (reference):** `vss-sop-skills/vss-sop-test` (`scripts/vss_sop_test.py`)
  runs 4 phases — service health, ELK pipeline, VIOS recording/livestream, and VSS-Agent end-to-end
  (incl. report generation). Phases 1–3 overlap this eval; Phase 4 (agent/report) only applies once
  build-vision-agent gains an agent/LLM-NIM/report-gen flow.
