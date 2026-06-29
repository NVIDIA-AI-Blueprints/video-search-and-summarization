# Deployment Reference: RTVI-SOP

## Container Image

- **Image name** — `nvds-sop` (env `${RTVI_SOP_IMAGE:-nvds-sop:1.0.0}`). NOTE: build-vision-agent still names the microservice **RTVI-SOP** in the catalog (capability `sop-detection`); only the docker image is `nvds-sop:1.0.0`.
- **Tag** — `1.0.0`
- **Registry** — **local build only** (no registry). Built from the **public** `sop-inference-bp` source (`github.com/NVIDIA/sop-monitoring-blueprints`, subtree `sop-inference-bp/` at repo root, branch `main`): `docker compose -f deploy/compose.yaml build` → `nvds-sop:1.0.0` (manual: `docker build . -f docker/Docker.build -t nvds-sop:1.0.0`). The public build is **Kafka-output-only** — no annotated RTSP output (VIOS supplies the source-camera input, exactly like RT-VLM); the only code patch is `ddm_pytorch2.patch`, applied internally by `docker/Docker.build` for the DDM/GEBD model. See the `vss-build-ds-sop` skill (Step 0 clones the public source; Step 1 builds as-is; Step 2 standalone smoke test).
- **Base images** (pulled at build, need `docker login nvcr.io`) — `nvcr.io/nvidia/deepstream:8.0-triton-multiarch` + `nvcr.io/nvidia/blueprint/vss-engine:2.4.1`.
- **NGC pull requirements** — none at runtime (image is local); models are pulled separately (see Storage). The build step needs nvcr.io access.
- **Architecture** — x86_64 (Docker.build also supports aarch64/sbsa variants).

## GPU Requirements

- **GPU required?** — **yes**. Runs DDM-Net (TensorRT/Triton in-process) + Cosmos-Reason-1.1-7B via in-process vLLM, both on one GPU.
- **Minimum VRAM** — model load alone is ~15.6 GB; budget ≥ 28 GB for model + vLLM KV cache + DDM + DeepStream buffers. Fits one L40S (48 GB) or H100 (80 GB).
- **Supported GPU arch** — Ada Lovelace (L40S), Hopper (H100/H200), Ampere (A100).
- **GPU count per instance** — 1 (`NVIDIA_VISIBLE_DEVICES=0`, `RTVI_SOP_NUM_GPUS=1`).
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

Models are **not** baked into the image — stage on the host before bring-up. The owner's `vss-sop-deploy/scripts/download_assets.sh` is the in-repo data tool (**not in the public mirror** — the manual steps here are the equivalent): it **verifies** the models at `/opt/models/vlm/checkpoint` (Cosmos-Reason VLM — checks `…/config.json`), `/opt/models/gbed_models/ddm/checkpoint.pth.tar` (DDM), and `/opt/sop/configs/{actions.json,vlm_prompts.txt}` — it does **NOT** download the models. Obtain them by retraining via the SOP Training Blueprint (owner-recommended), or — as we verified — from NGC `nv-metropolis-dev/vss-industrial/sop-data:1.0` (~14.7 GB; lays VLM at `/opt/models/cosmos-reason1.1-7b/checkpoint`, `ddm_weights/ddm.ckpt`→DDM, `configs/*`→configs). Set `VLLM_MODEL_PATH` to the staged VLM path. The same script **downloads + transcodes the test video** (NGC `sop-server-fan-installation-data:1.0-260213` → `Install_1_h264_30fps.mp4`). (Stage models AFTER any tarball fully extracts — a mid-extract shard copy truncates → `safetensors ... incomplete metadata`.)

## Startup Behavior

- **Expected startup time** — ~60–120 s warm (DeepStream plugin init ~30 s → vLLM model load ~3 s + KV cache + CUDA-graph capture → DDM TRT init ~5 s → API server up).
- **Startup ordering** — `depends_on: kafka (service_started)`. Kafka must be healthy first.
- **Health / readiness** — `GET http://localhost:8300/v1/ready` returns `200` when ready (the owner's canonical readiness check). `GET /v1/models` also works and returns `{"data":[{"id":"ds_sop_model",...}]}`.
- **Log signatures of healthy startup**:
  - `DDM model initialized successfully`
  - `INFO: Application startup complete.` / `Uvicorn running on http://0.0.0.0:8300`
  - During processing: `VLM inference on chunk ... response: (N) <action>` and `chunk messaging delivered to mdx-vlm-captions [<partition>]`.

## Known Deployment Issues

- **`No available memory for the cache blocks`** → raise `VLLM_GPU_MEMORY_UTILIZATION` (0.6 on 48 GB). See GPU section.
- **`safetensors ... incomplete metadata, file not fully covered`** → a VLM shard was copied before extraction finished; re-copy the shard.
- **0 docs in ES** → most often `ENABLE_MESSAGING` not set to `1` (compose default is `false`, so nothing publishes to Kafka), or `DEFAULT_TOPIC` overridden away from `mdx-vlm-captions` (the code default). See `integrate-rtvi-sop.md`.
- **Kafka has messages but `mdx-vlm-captions-*` ES index is empty + Logstash logs `Google::Protobuf::ParseError`** → build-vision-agent's default ELK decodes this topic as PROTOBUF (RT-VLM), but RTVI-SOP emits JSON. **build-vision-agent CANNOT auto-wire this** — its Step 6.5 patches only edit compose YAML (`profiles:`, `depends_on:`, volume materialization); no patch type edits Logstash `pipelines.yml` or pipeline `.conf` files. So this is a **mandatory deploy-time step** the generated deploy skill (or operator) must run. Concrete procedure (verified working — yields 4255 docs):

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
  See `integrate-rtvi-sop.md` § Known Integration Constraints → "ELK indexing". (Long-term fix = a build-vision-agent enhancement that lets an integrate ref declare Logstash-pipeline artifacts, or a SOP-aware ELK catalog variant.)
- **Redis / data_log perms** (ELK/VIOS peers) → `chmod -R 777 <MDX_DATA_DIR>/data_log` after first up; redis perm crash cascades to envoy proxies.
- **`Failed to start recording` (HTTP 500) on VIOS sensor-add** → transient; the recorder retries succeed once the RTSP upstream is flowing.
- **Live-RTSP input degenerates to all `(10) not belong` (`cv_boundary_score≈0` on every chunk)** → the live path uses **non-blocking, leaky** frame intake (`is_live` → drop-oldest / skip-new in `ds_sop_process.py`; source `leaky:2`), so if DDM-Net can't process frames as fast as they arrive, the frame stream it sees has gaps and boundary detection collapses. This is **GPU-throughput-dependent — NOT a fixed FPS or NvStreamer limit** (NvStreamer just serves the video's native FPS). The **on-demand file path blocks instead of dropping**, so it's unaffected at any FPS (verified: 30 fps file → healthy boundary scores + real steps). Measured DDM CV time ≈ 3.6–7.4 s per 10 s @30 fps chunk on an RTX 6000 Pro (≈1.4–2.8× realtime), so a fast GPU should sustain 30 fps live; only on a GPU where DDM falls behind do you need to **cap the live source** to a sustainable rate — set `CAMERA_FPS_NUM`/`CAMERA_FPS_DEN` (e.g. `10`/`1`) or lower the source FPS. Prefer the on-demand path for deterministic validation regardless.
- **VIOS-proxy RTSP port is pool-assigned (NOT a fixed `:30554`)** → when feeding rtvi-sop a VIOS-proxied live stream, read the real URL from VIOS `GET :30888/api/v1/live/streams` (e.g. `rtsp://<host>:30561/live/<sensorId>`); probing the documented `:30554` returns 404. If the port looks stale right after `sensor/add`, **delete + re-add the sensor** and re-read the URL (verified live 2026-06-26).
