# QA source build: Search, realtime Alerts and Filling analysis

This recipe reconstructs the nightly-20260922 custom application from source without the presenter's local image IDs or private Compose. Previous runtime validation used Linux x86_64 and four H100 GPUs. Packaging received structural checks; **fresh-host image builds and deployment have not been executed**. DGX Spark/ARM64 is untested.

## External assets

Obtain approved recordings and both checkpoints separately. The liquid model is custom source-adapted, not a stock downloadable VSS model. No media, credentials, weights, engine binaries or result caches are shipped.

| Asset | SHA256 |
| --- | --- |
| Original reference orangejuiceneww.mp4 | 21739924f607755906107affa28842df85a4c468e1a8a3a9f28afcb83f346d61 |
| RF-DETR-Seg 2XLarge bottle checkpoint | f6e06ac5ebafd3f9f6f01c55a7f53b8785a1697037063a6adeb737c4c6ce4838 |
| Custom RF-DETR-Seg Nano liquid checkpoint | 85af581d8a0e5fc619052a893de805338124523bf65c5620912b8daac6c5e135 |

streams.example.json contains the actual reviewed Sammy v3 camera calibration with invalid UUID/RTSP placeholders. After VIOS registration, replace them with the real stream UUID and reachable RTSP endpoint. Match the source hash to the v3 video; do not apply older balanced-video exterior regions. Prior-host calibration validation does not establish fresh-host acceptance.

## Build from repository root

Prerequisites: Docker/Compose v2+, NVIDIA Container Toolkit, authenticated access to required registries and adequate disk/GPU resources.

```bash
bash extensions/filling-analysis/deployment/qa/build.sh
```

Builds the CPU Filling API, recorded segmentation worker, live worker, native VSS UI and RT-VLM overlap derivative. Models load from runtime mounts; no training occurs. All image bases are registry-addressable. The GPU workers share the pinned dependency lock; the UI uses the repository npm lock. The CV startup patch aligns installed cuDNN libraries and engine naming. Build target-compatible TensorRT engines rather than copying H100 engines.

## Configure privately

```bash
mkdir -p "$HOME/vss-qa-private"
chmod 700 "$HOME/vss-qa-private"
cp extensions/filling-analysis/deployment/qa/env.example "$HOME/vss-qa-private/runtime.env"
cp extensions/filling-analysis/deployment/qa/streams.example.json "$HOME/vss-qa-private/streams.json"
chmod 600 "$HOME/vss-qa-private/"*
```

Edit runtime.env with absolute asset paths, public URL, host address, writable VSS data directory, GPU placement, NGC keys and a separately provisioned QA harness gateway/token. Set FILLING_STREAMS_FILE to your edited streams.json. Four-H100 placement defaults require review on another machine.

```bash
python3 extensions/filling-analysis/deployment/qa/check-assets.py --env-file "$HOME/vss-qa-private/runtime.env"
python3 extensions/filling-analysis/deployment/qa/prepare.py --env-file "$HOME/vss-qa-private/runtime.env" --output "$HOME/vss-qa-private/rendered"
```

The asset check covers original media and both model files. Verify Sammy v3 against the template source hash separately. prepare.py renders stock Search plus realtime Alerts and extensions with pinned release tags and schema validation. It does not launch containers. Example paths can render successfully without existing assets; that is not deployment acceptance. Keep resolved output private because it may include credentials.

After reviewing capacity, permissions and image availability, on a **dedicated QA host**:

```bash
docker compose -f "$HOME/vss-qa-private/rendered/resolved.yml" up -d --no-build
```

Do not run this against the presenter stack. Stock services use fixed ports/container names; changing project name alone is insufficient isolation.

## Harness and operation

Provision a new QA OpenClaw through this checkout's Build Vision AI/NemoClaw workflow. Stage the modified CLI and skills using skills/vss-build-vision-ai/scripts/stage_vss_src.py, follow pinned onboarding and set the resulting endpoint/token privately before rerendering. This recipe never copies the presenter's session or provider configuration.

Refresh the project-local VSS CLI against the public origin. Register/upload real media, verify ingestion and test Search, newly generated alerts and Filling queries. The operational guide is skills/operations/vss-inspect-filling/SKILL.md. No live inspection auto-starts.

Filling starts hidden. Set FILLING_TAB_VISIBLE=true, rerender, and recreate only vss-ui to expose it. The ingress routes /filling/ including streaming responses to the API. The v3 demonstration uses live RTSP inspection; recorded analysis supports only explicitly approved source hashes in backend/profiles.py.

## Limitations

GPU masks measure visible-height fraction, not volume. Exterior overflow is a separate calibrated CPU color signal. Missing masks remain unreadable. The liquid model uses weak labels and is camera-specific.

Healthy models and registered cameras do not prove current inference. Confirm fresh chunks and new incident evidence. The known RT-VLM EOS/replay lifecycle issue remains an engineering follow-up; global rule replay is not a routine health check.

Spark requires ARM64 image/dependency validation, CUDA support, revised memory/GPU placement and target engines. Fresh-host build/runtime and Spark acceptance are not claimed.
