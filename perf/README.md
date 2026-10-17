<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->
# compose-base performance

Benchmarks Hugo's RT-CV, VIOS and video-analytics-api profile using production
Compose recipes and the VSS CLI from this checkout. The shared
`benchmark-perf-test` job calls the lifecycle hooks and benchmark directly.

## Run locally

Use an exclusive Linux amd64 RTX PRO 6000 Blackwell Server Edition host with
NVIDIA Container Toolkit, Docker Compose v2, GNU `timeout`, `uv`, NGC CLI and registry/model
access. Set `DOCKER_USE_SUDO=true` if Docker needs passwordless sudo.
Preflight installs Python 3.13 and the editable CLI/core into `perf/.venv`.

```bash
export CONFIG_ID=rtxpro6000bw-1-compose-base-rtdetr-warehouse
export NGC_API_KEY   # provision through the environment or secret manager
export VSS_PERF_VIDEO=/absolute/path/to/Camera_03.mp4
./compose/run_benchmark.sh -s quick_test -O perf/results/local -r 3.3.0 -d
```

Without `VSS_PERF_VIDEO`, preflight acquires the pinned warehouse dataset from
`configs/config.yaml`. The H.264/H.265 fixture loops through MediaMTX without
transcoding. `sustained_test` measures five minutes instead of thirty seconds.
The matrix selects GPU 0; `RTXPRO6000BW-SE` pool availability needs DevOps confirmation.

## Results and cleanup

Readiness requires positive RT-CV FPS. Kafka initialization completes separately;
strongly typed RT-DETR retains graph FP16 types with `network-mode: 0`.
Results contain actual RT-CV FPS, VIOS/analytics CLI wall-time latency, host/GPU
resources, fixture facts and image IDs. Initial 1 FPS and 10-second CLI p95
limits check onboarding; an empty successful analytics sensor listing is valid.

Every run tears down before writing/uploading one schema-validated UTC file:
`compose-base_<config_id>_<YYYYMMDD>_<HHMMSS>.json`. Failures remain failures.
Stage limits are in `configs/config.yaml`; teardown is idempotent and removes
only this project's containers, networks and VIOS sensor database volume.
Models, engines, video and APT caches remain. Use a fresh output directory per run.

Source runs default to `nvcr.io/nvstaging/vss-core/*:nightly-20261010` and accept
`VSS_CONTAINER_REGISTRY`/`VSS_CONTAINER_TAG` overrides. When Jenkins supplies
`ARTIFACT_REF` or `IMAGE_TAG`, preflight downloads that exact NGC Compose resource,
rejects unsafe archives/layouts and missing or moving image pins, and uses its
published environment layers. Source validation does not establish a promoted baseline.

For publication, set `UPLOAD_TO_MINIO=true`, `MINIO_ACCESS_KEY` and
`MINIO_SECRET_KEY`; Jenkins injects them. `MINIO_ENDPOINT` overrides the configured
endpoint. Results go under `perf-results/compose-base/` for the
[dashboard](http://10.111.52.248:8080/).

Onboarding still needs source/promotion merges, DevOps approval, the private
artifact run and matching shared-pipeline/dashboard results. Tracker access and
Chandrahas's authoritative schema v2.0 review are deferred; the available result
contract is schema `0.1`.
