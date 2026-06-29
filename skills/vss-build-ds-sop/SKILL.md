---
name: vss-build-ds-sop
description: >-
  Build the DS-SOP DeepStream inference image `ds-sop:1.0.0` from the NVIDIA
  sop-monitoring-blueprints source and smoke-test it STANDALONE with no models via
  API_DUMMY_TEST. Use when asked to build the DS-SOP / ds-sop image, verify the SOP image
  works on its own, or produce the image that the build-vision-agent SOP profile
  (capability sop-detection) consumes.
---

# Build & Standalone-Test the DS-SOP Image (`ds-sop:1.0.0`)

The build-vision-agent SOP profile (microservice **DS-SOP**, capability `sop-detection`)
assumes the image `ds-sop:1.0.0` already exists. This skill **builds it** from source and
**proves it works standalone** — including a model-free smoke test, so you can validate the
image without staging the 14.7 GB SOP models.

> Source of truth: the **NVIDIA internal** repo `sop-monitoring-blueprints` (`sop-training-bp`,
> branch `main`), subtree `microservices/sop-inference-bp/` — cloned in **Step 0** (SSH access
> required). **Use this source, not the public GitHub mirror:** the public `sop-inference-bp` is
> **Kafka-output-only** (no `ENABLE_RTSP_OUTPUT` / `RTSPStreamingServer` in its code), whereas the
> SOP canonical flow needs DS-SOP to emit an **annotated RTSP output (`:8554/ds-out/<stream-name>`)** that
> VIOS records (see `integrate-ds-sop.md`). The internal source ships that feature (RTSP server +
> the apt packages in `docker/Docker.build`). The only patch (`ddm_pytorch2.patch`, a PyTorch1→2
> fix for the DDM/GEBD model) is applied **internally** by `docker/Docker.build`.

## When to use
- Build `ds-sop:1.0.0` on a host that lacks it (the SOP profile needs it before deploy).
- Confirm a freshly built image is valid (imports + API server) without models/GPU-heavy load.
- Re-build after a source update.

## Prerequisites
| Item | Why |
|---|---|
| **NVIDIA GitLab SSH** | clone the build source in Step 0 (`sop-monitoring-blueprints`, `sop-training-bp` — internal; the RTSP-output feature is not in the public GitHub mirror) |
| `docker login nvcr.io` | base images `nvcr.io/nvidia/blueprint/vss-engine:2.4.1` + `nvcr.io/nvidia/deepstream:8.0-triton-multiarch` are NGC-gated |
| **BuildKit / buildx** | `Docker.build` uses `RUN --mount=type=bind` (modern Docker has it on by default; else `DOCKER_BUILDKIT=1`) |
| **Internet** | build clones `github.com/MCG-NJU/DDM` @ `941e0fb595ab85dc86724a19ed0439ad6bc3632b` + `gst-plugin-pylon`, and auto-downloads the Basler Pylon SDK (~1.3 GB) |
| ~120 GB free disk | image is ~50 GB + base layers |
| GPU | only for the **real** run (smoke test needs no GPU compute) |

## Step 0 — Clone the source
The image is **built from source** (no registry image to pull). Clone the internal repo once —
everything the later steps reference lives inside it:
```bash
git clone ssh://git@gitlab-master.nvidia.com:12051/sop-training-bp/sop-monitoring-blueprints.git
cd sop-monitoring-blueprints/microservices/sop-inference-bp     # branch: main
```
> **Access:** internal NVIDIA GitLab over SSH (needs an account + SSH key). The public GitHub mirror
> `github.com/NVIDIA/sop-monitoring-blueprints` (subtree `sop-inference-bp/` at root) builds, but it
> is the **Kafka-output-only** variant — no `:8554` RTSP output — so it can't feed the canonical
> DS-SOP→VIOS flow; use the internal source here.

## Step 1 — Build
```bash
# from microservices/sop-inference-bp/ (Step 0)
mkdir -p binaries          # Docker.build bind-mounts ./binaries; Pylon SDK auto-downloads here at build time
NV_DS_SOP_IMAGE=ds-sop:1.0.0 docker compose -f deploy/compose.yaml build
# manual equivalent:  docker build . -f docker/Docker.build -t ds-sop:1.0.0
```
- Build **as-is** — `docker/Docker.build` already installs the RTSP GStreamer packages
  (`libgstrtspserver-1.0-dev`, `gir1.2-gst-rtsp-server-1.0`, `gstreamer1.0-plugins-ugly`,
  `gstreamer1.0-libav`) and the source carries the RTSP-output code, so **no extra patch** is needed
  for the annotated `:8554/ds-out` output. (The output is gated by `ENABLE_RTSP_OUTPUT`, default off
  — enable it at deploy for the DS-SOP→VIOS flow.)
- Image tag: **`ds-sop:1.0.0`** (set via `NV_DS_SOP_IMAGE`, as in the build command; override for any other name).
- ~20 min (base-image pull is the long pole). The `pip` dependency-resolver `ERROR` lines
  (version conflict with the base image's `tensorrt-llm`) are **warnings, not build failures**.
- Verify: `docker images ds-sop:1.0.0 --format '{{.Repository}}:{{.Tag}} {{.Size}}'` (~50 GB).

## Step 2 — Standalone smoke test (NO models, NO 14.7 GB download) ⭐
`API_DUMMY_TEST=true` skips `SOPProcessManager`/model load and boots only the API server —
ideal to prove the image is valid (all heavy imports OK + server serves) without any model.
```bash
docker run --rm -d --gpus all --network host \
  -e API_DUMMY_TEST=true -e API_SERVER_PORT=8300 \
  --name dssop-smoke ds-sop:1.0.0
# wait ~30–60 s for the heavy imports (torch / vLLM / DeepStream / GstRtspServer), then:
for ep in live ready models metadata; do echo "/v1/$ep:"; curl -s http://localhost:8300/v1/$ep; echo; done
docker rm -f dssop-smoke
```
**PASS criteria** (all must respond):
- `/v1/live` → `Service is live.`
- `/v1/ready` → `Dummy test mode,Service is ready.`  (note: no space after the comma — match loosely)
- `/v1/models` → `ds_sop_model`
- `/v1/metadata` → version `1.0.0` + model info (ddm + cosmos) + license

If the image were broken, it would crash on import (DeepStream/`pyds`/vLLM/`GstRtspServer`) before
serving. A clean smoke test = **build correct, container boots, API serves**.
> Note: this does NOT exercise real inference (DDM + Cosmos-Reason VLM are skipped). On a
> Blackwell host (sm_120, newer than the documented Ada/Hopper/Ampere), the only residual risk
> is whether vLLM in `vss-engine:2.4.1` has sm_120 kernels — that only surfaces with real models.

## Step 3 — Full standalone run (with models)
Models are NOT in the image — stage them on the host at runtime. (The owner's
`vss-sop-deploy/scripts/download_assets.sh` does this in-repo; the equivalent steps are inlined.)

1. **Models — pre-stage them (only *verified* at startup, not auto-pulled):**
   - `/opt/models/vlm/checkpoint` — Cosmos-Reason VLM (must contain `config.json`).
   - `/opt/models/gbed_models/ddm/checkpoint.pth.tar` — DDM checkpoint (not auto-downloadable).
   - `/opt/sop/configs/{actions.json,vlm_prompts.txt}`.
   Obtain by **retraining via the SOP Training Blueprint**, OR
   from NGC `nv-metropolis-dev/vss-industrial/sop-data:1.0` (~14.7 GB; lays the VLM at
   `/opt/models/cosmos-reason1.1-7b/checkpoint`). Set `VLLM_MODEL_PATH` to wherever you stage the VLM.
2. **Test video — pull + transcode:**
   ```bash
   ngc registry resource download-version nvidia/tao/sop-server-fan-installation-data:1.0-260213
   cd sop-server-fan-installation-data_v1.0-260213
   tar -xzf sop-sample-training-data.tar.gz          # → server_fan/raw/Install_1.MP4
   ffmpeg -y -i server_fan/raw/Install_1.MP4 -c:v libx264 -r 30 -an \
     server_fan/raw/Install_1_h264_30fps.mp4         # → h264 1920x1080 30 fps
   ```
3. **Edit `deploy/.env`** REQUIRED paths (`MODEL_ROOT_DIR`, `VLLM_MODEL_PATH`, `DDM_MODEL_PATH` — the
   two model paths must live UNDER `MODEL_ROOT_DIR`, the only bind-mounted folder). For the canonical
   **DS-SOP→VIOS** flow also set **`ENABLE_RTSP_OUTPUT=true`** + `RTSP_PORT=8554` (+ `SW_ENCODER=true`)
   so DS-SOP re-streams the annotated result at `rtsp://<host>:8554/ds-out/<stream-name>`. Then run:
   ```bash
   docker compose -f deploy/compose.yaml up -d
   docker compose -f deploy/compose.yaml logs -f --tail=200 nvds-action-sop
   ```
   Ready when logs show `Uvicorn running on http://0.0.0.0:8300`; `GET /v1/ready` → 200.

## Notes
- This image is consumed by build-vision-agent's DS-SOP catalog entry (`skills/vss-deploy-sop/`).
  Container workdir is `/opt/nvidia/nvds_sop`; key env: `API_SERVER_PORT=8300`,
  `DEFAULT_TOPIC=mdx-vlm-captions`, `SOP_MESSAGING_SCHEMA=JSON`, `VLLM_GPU_MEMORY_UTILIZATION=0.3`
  (raise to `0.6` on ≤48 GB GPUs), `ENABLE_MESSAGING=1`, and — for DS-SOP→VIOS — `ENABLE_RTSP_OUTPUT=1`
  + `RTSP_PORT=8554`. The service emits SOP records to Kafka **and** an annotated RTSP output VIOS records.
- Output: `ds-sop:1.0.0` (~50 GB) from the internal source; the Step 2 smoke test exercises all four endpoints.
