<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->
# compose-base performance

Hugo's compose-base scope is RT-CV, VIOS and video-analytics-api. This package
reuses their production Compose recipes. The shared `benchmark-perf-test` job
runs the four lifecycle hooks and the benchmark entry point directly;
`compose/run_benchmark.sh` runs the same lifecycle locally.

## Run locally

Use an exclusive Linux amd64 GPU host with NVIDIA Container Toolkit, Docker
Compose v2, `uv`, NGC CLI and registry/model access. Fixed production container
names prevent concurrent deployments on the host. Set `DOCKER_USE_SUDO=true`
only when Docker needs passwordless sudo. Preflight installs Python 3.13 and the
CLI/core from this same checkout into `perf/.venv` as editable packages.

```bash
export CONFIG_ID=rtxpro6000bw-1-compose-base-rtdetr-warehouse
export NGC_API_KEY   # provision through the environment or secret manager
export VSS_PERF_VIDEO=/absolute/path/to/Camera_03.mp4
./compose/run_benchmark.sh -s quick_test -O perf/results/local -r 3.3.0 -d
```

`-s sustained_test` measures five minutes instead of thirty seconds. Cleanup is
always performed; `-d` remains accepted for the documented local command.
Use a distinct output directory for each invocation.

A supplied H.264/H.265 video loops through MediaMTX using FFmpeg stream copy.
When no video is supplied, preflight downloads the pinned NGC dataset in
`configs/config.yaml` and
selects its warehouse `Camera_03.mp4`. Cached video and models are reused.

## Deployment and identity

The enabled matrix selects GPU 0 on an RTX PRO 6000 Blackwell Server Edition.
`RTXPRO6000BW-SE` is the existing shared-pool machine-class label; DevOps must
confirm pool availability. Each run uses one GPU.

Local source runs default to the VSS 3.3.0 manifest's
`nvcr.io/nvstaging/vss-core/*:nightly-20261010` coordinates. Source development
may override `VSS_CONTAINER_REGISTRY`, `VSS_CONTAINER_TAG`, or individual image
variables. Actual image IDs are recorded; a source run does not establish
results for a promoted artifact.

When Jenkins supplies the Compose resource handle as `IMAGE_TAG` (or
`ARTIFACT_REF`), preflight downloads that exact resource and uses its published
recipes and ordered environment files. It rejects unsafe archives, unsupported
layouts and missing/unresolved/moving service image pins. No source image pins
are substituted. The resource and deployment tree hashes accompany the result.

Two adjustments are required by the measured deployment: Kafka topic creation
finishes separately before Docker waits for long-running services; strongly
typed RT-DETR uses `network-mode: 0`, retaining the ONNX FP16 tensor types without
requesting the forbidden FP16 builder flag
([TensorRT precision documentation](https://docs.nvidia.com/deeplearning/tensorrt/10.x.x/inference-library/precision-control.html)).
The reviewed model acquisition helper is mounted from this checkout because
NGC CLI 4.37 removed model download's legacy `--org` option. Actual video
fixture and container identities are captured in runtime metadata.

## Lifecycle and results

Preflight creates fresh VIOS configuration and stages RT-CV configuration.
Deployment waits for Docker readiness. Post-deploy configures the VSS CLI,
registers the fixture through VIOS, reads its returned sensor ID, and waits for
positive RT-CV FPS before measurement. Operational requests use the VSS CLI;
CLI failures are reported without REST fallbacks or extra retry loops.

All stages have process-group limits from `configs/config.yaml`. Runtime data is
under `perf/.runtime/<config_id>` (or `VSS_DATA_DIR`). The private
`perf/.runtime-state.json` contains deployment coordinates, never API keys or
external RTSP URLs. Teardown selects every profile, verifies that this project's
containers/networks are gone, and removes only its Docker-labeled VIOS sensor
PostgreSQL volume. This prevents stale registrations after stream-plane restart.
Model, engine, video and APT caches remain available.

The benchmark always tears down before writing or uploading its single UTC-named
`compose-base_<config_id>_<YYYYMMDD>_<HHMMSS>.json` result. Failed stages and
cleanup failures produce failed results. The shared runner's later teardown is
idempotent. Results measure actual RT-CV FPS, VIOS stream inventory, host/GPU
resources and CLI wall-time latency. An empty successful analytics sensor listing
is valid because its inventory requires calibration data. The initial 1 FPS and
10-second CLI p95 limits are onboarding checks, not production performance targets.

Set `UPLOAD_TO_MINIO=true`, `MINIO_ACCESS_KEY` and `MINIO_SECRET_KEY` for local
publication. Jenkins injects its credentials. `MINIO_ENDPOINT` overrides the
configured endpoint; results use `perf-results/compose-base/`. The documented
dashboard is <http://10.111.52.248:8080/>. The supplied shared result contract is
schema `0.1`; schema v2.0 needs Chandrahas's authoritative contract and dashboard
acceptance before claiming compliance.

Onboarding still requires merged source/promotion changes, a successful shared
run, matching dashboard results and tracker evidence. Local hardware validation
alone does not establish those external outcomes.
