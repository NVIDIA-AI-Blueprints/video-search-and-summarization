# Deployment Reference: DS-SOP

## Container Image

- **Image name** — `ds-sop` (env `${DS_SOP_IMAGE:-ds-sop:1.0.0}`). NOTE: build-vision-agent still names the microservice **DS-SOP** in the catalog (capability `sop-detection`); only the docker image is `ds-sop:1.0.0`.
- **Tag** — `1.0.0`
- **Registry** — **local build only** (no registry). Built from the **internal** `sop-inference-bp` source (`sop-monitoring-blueprints` / `sop-training-bp`, branch `main`, subtree `microservices/sop-inference-bp/` — NVIDIA GitLab SSH): `NV_DS_SOP_IMAGE=ds-sop:1.0.0 docker compose -f deploy/compose.yaml build` → `ds-sop:1.0.0` (manual: `docker build . -f docker/Docker.build -t ds-sop:1.0.0`). **Use the internal source, not the public GitHub mirror** — the public one is **Kafka-only** (no `:8554` RTSP output). This source ships the **annotated RTSP output** (`:8554/ds-out`, gated by `ENABLE_RTSP_OUTPUT`) that DS-SOP re-streams for VIOS to record; the only code patch is `ddm_pytorch2.patch`, applied internally by `docker/Docker.build`. See the `vss-build-ds-sop` skill (Step 0 clones the internal source; Step 1 builds as-is; Step 2 standalone smoke test).
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

Models are **not** baked into the image — stage on the host before bring-up. The owner's `vss-sop-deploy/scripts/download_assets.sh` is the in-repo data tool (**not in the public mirror** — the manual steps here are the equivalent): it **verifies** the models at `/opt/models/vlm/checkpoint` (Cosmos-Reason VLM — checks `…/config.json`), `/opt/models/gbed_models/ddm/checkpoint.pth.tar` (DDM), and `/opt/sop/configs/{actions.json,vlm_prompts.txt}` — it does **NOT** download the models. Obtain them by retraining via the SOP Training Blueprint (owner-recommended), or from NGC `nv-metropolis-dev/vss-industrial/sop-data:1.0` (~14.7 GB; lays VLM at `/opt/models/cosmos-reason1.1-7b/checkpoint`, `ddm_weights/ddm.ckpt`→DDM, `configs/*`→configs). Set `VLLM_MODEL_PATH` to the staged VLM path. The same script **downloads + transcodes the test video** (NGC `sop-server-fan-installation-data:1.0-260213` → `Install_1_h264_30fps.mp4`). (Stage models AFTER any tarball fully extracts — a mid-extract shard copy truncates → `safetensors ... incomplete metadata`.)

## Startup Behavior

- **Expected startup time** — ~60–120 s warm (DeepStream plugin init ~30 s → vLLM model load ~3 s + KV cache + CUDA-graph capture → DDM TRT init ~5 s → API server up).
- **Startup ordering** — `depends_on: kafka (service_started)`. Kafka must be healthy first.
- **Health / readiness** — `GET http://localhost:8300/v1/ready` returns `200` when ready (the owner's canonical readiness check). `GET /v1/models` also works and returns `{"data":[{"id":"ds_sop_model",...}]}`.
- **Log signatures of healthy startup**:
  - `DDM model initialized successfully`
  - `INFO: Application startup complete.` / `Uvicorn running on http://0.0.0.0:8300`
  - During processing: `VLM inference on chunk ... response: (N) <action>` and `chunk messaging delivered to mdx-vlm-captions [<partition>]`.
- **Annotated RTSP output** — with `ENABLE_RTSP_OUTPUT=true` + `RTSP_PORT=8554`, once a live request is processing DS-SOP serves the overlaid stream at `rtsp://<host>:8554/ds-out/<stream-name>` (verify with `ffprobe rtsp://localhost:8554/ds-out/<stream-name>`). Register this with VIOS for VST recording — see § Known Deployment Issues → "DS-SOP → VIOS output".

## Known Deployment Issues

- **`No available memory for the cache blocks`** → raise `VLLM_GPU_MEMORY_UTILIZATION` (0.6 on 48 GB). See GPU section.
- **`safetensors ... incomplete metadata, file not fully covered`** → a VLM shard was copied before extraction finished; re-copy the shard.
- **0 docs in ES** → most often `ENABLE_MESSAGING` not set to `1` (compose default is `false`, so nothing publishes to Kafka), or `DEFAULT_TOPIC` overridden away from `mdx-vlm-captions` (the code default). See `integrate-ds-sop.md`.
- **Kafka has messages but `mdx-vlm-captions-*` ES index is empty + Logstash logs `Google::Protobuf::ParseError`** → build-vision-agent's default ELK decodes this topic as PROTOBUF (RT-VLM), but DS-SOP emits JSON. **build-vision-agent CANNOT auto-wire this** — its Step 6.5 patches only edit compose YAML (`profiles:`, `depends_on:`, volume materialization); no patch type edits Logstash `pipelines.yml` or pipeline `.conf` files. So this is a **mandatory deploy-time step** the generated deploy skill (or operator) must run. Concrete procedure:

  ```bash
  # 1. drop the shipped JSON pipeline into the patched logstash kafka-pipelines dir
  #    (this dir mounts flat to /usr/share/logstash/pipelines/ in the container)
  cp <skill>/references/sop-vlm-captions-json-logstash.conf \
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
  See `integrate-ds-sop.md` § Known Integration Constraints → "ELK indexing". (Long-term fix = a build-vision-agent enhancement that lets an integrate ref declare Logstash-pipeline artifacts, or a SOP-aware ELK catalog variant.)
- **Redis / data_log perms** (ELK/VIOS peers) → `chmod -R 777 <MDX_DATA_DIR>/data_log` after first up; redis perm crash cascades to envoy proxies.
- **`Failed to start recording` (HTTP 500) on VIOS sensor-add** → transient; the recorder retries succeed once the RTSP upstream is flowing.
- **Live (realtime camera/source) input degenerates to all `(10) not belong` (`cv_boundary_score≈0` on every chunk)** → the live path uses **non-blocking, leaky** frame intake (`is_live` → drop-oldest / skip-new in `ds_sop_process.py`; source `leaky:2`), so if DDM-Net can't process frames as fast as they arrive (from the Basler camera or an RTSP source), the frame stream has gaps and boundary detection collapses. This is **GPU-throughput-dependent — NOT a fixed FPS limit**. The **on-demand file path blocks instead of dropping**, so it's unaffected at any FPS (a 30 fps file yields healthy boundary scores + real steps). On a capable GPU, DDM CV runs faster than realtime per 10 s @30 fps chunk, so a fast GPU sustains 30 fps live; only on a GPU where DDM falls behind do you need to **cap the source** to a sustainable rate — set `CAMERA_FPS_NUM`/`CAMERA_FPS_DEN` (e.g. `10`/`1`) or lower the source FPS. Prefer the on-demand path for deterministic validation regardless.
- **DS-SOP → VIOS output is not auto-wired (mandatory deploy-time step)** → build-vision-agent composes DS-SOP + VIOS but never wires the video flow (true for RT-VLM too). With `ENABLE_RTSP_OUTPUT=true`, DS-SOP re-streams the annotated result at `rtsp://<host>:${RTSP_PORT:-8554}/ds-out/<stream-name>`; **register it as a VIOS sensor** so VST records it:
  ```bash
  curl -X POST http://localhost:30888/vst/api/v1/sensor/add \
    -H 'Content-Type: application/json' \
    -d '{"sensorUrl":"rtsp://<HOST_IP>:8554/ds-out/<stream-name>","name":"ds-sop-annotated"}'
  # recording is AUTOMATIC (recording_status: alwayson) once the sensor is added — do NOT call record/start (returns 405)
  ```
  Notes: `<stream-name>` = the **source stream's name** (e.g. the input video/camera id) — the `/ds-out/` path key comes from the source, NOT a VIOS sensorId. The VST API base is **`/vst/api/v1/...`** (`/api/v1/...` 404s in vst nginx mode). DS-SOP reads its camera/source **directly** — VIOS is NOT in the input path.
- **Responses come back free-form instead of `(N)` numbered SOP steps** → a custom `prompt` in the `/v1/chat/completions` request **overrides** `VLM_PROMPT_PATH` (USER_PROMPT_PRIORITY), so the VLM free-forms instead of classifying against the numbered SOP action set. For numbered SOP step classifications, send **no prompt** (let it use the configured `VLM_PROMPT_PATH` = `/opt/sop/configs/vlm_prompts.txt`) or pass that exact numbered prompt verbatim.

## Testing

- **Profile eval (this bundle):** `skills/vss-deploy-sop/evals/sop_deployment.json` — an operational
  eval (build-vision-agent convention: `{skills, resources, expects:[{query, checks}]}`) that
  validates the **delivered scope**: deploy the full profile → all containers Up → ELK
  `mdx-vlm-captions-*` shows REAL step responses (not all `(10)`), with the JSON Logstash pipeline
  registered. The VSS-Agent / report-generation phase is **out of scope** (see `integrate-ds-sop.md`
  § Scope & divergences).
- **Owner's full suite (reference):** the owner's `vss-sop-skills/vss-sop-test` (`scripts/vss_sop_test.py`)
  runs 4 phases — service health, ELK pipeline, VIOS recording/livestream, and VSS-Agent end-to-end
  (incl. report generation). Phases 1–3 overlap this eval; Phase 4 (agent/report) only applies once
  build-vision-agent gains an agent/LLM-NIM/report-gen flow.
