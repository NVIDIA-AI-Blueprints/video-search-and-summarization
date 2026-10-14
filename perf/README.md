<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# compose-base performance onboarding

This package benchmarks Hugo Verjus's compose-base scope: RT-CV, VIOS and
video-analytics-api. It reuses the production Docker Compose service definitions
from this checkout and adds an isolated benchmark lifecycle. It does not deploy
the current agent/VLM developer base configuration.

The shared `benchmark-perf-test` job on the `a3ieng-devops-test` controller
invokes all four `perf/scripts/` hooks and the benchmark entry point directly.
`compose/run_benchmark.sh` provides the corresponding local lifecycle command.

## Hardware and source configuration

`configs/perf-configs.yaml` contains one enabled amd64 configuration using GPU 0
of an RTX PRO 6000 Blackwell Server Edition host. `RTXPRO6000BW-SE` is the existing
shared-pool machine-class label from the LVS reference. DevOps must confirm
machine availability in that pool. No run spans multiple GPUs.

The default first-party image coordinates are the VSS 3.3.0 manifest's
`nvcr.io/nvstaging/vss-core/*:nightly-20261010` pins. Override
`VSS_CONTAINER_REGISTRY`, `VSS_CONTAINER_TAG` or individual `VSS_*_IMAGE/TAG`
variables to test another promoted build. A moving tag is appropriate only for
source development; record the actual resolved image digests with its results.
The staged RT-DETR config uses `strongly-typed: 1` and `network-mode: 0`.
The ONNX model retains its FP16 tensor types; mode 0 avoids requesting a
contradictory TensorRT builder FP16 flag. TensorRT explicitly forbids that flag
for strongly typed networks ([NVIDIA precision documentation](https://docs.nvidia.com/deeplearning/tensorrt/10.x.x/inference-library/precision-control.html)).
This adjustment affects only the generated benchmark configuration.

The Compose artifact handle supplied by Jenkins as `IMAGE_TAG` is a resource
reference. Preflight downloads that exact NGC resource, rejects unsafe archive
paths and unresolved or moving image pins, and switches the service recipes to
the verified published Compose tree. Deployment configuration identity and image
pins are captured with the result. The benchmark explicitly mounts the reviewed
model acquisition helper from this checkout: NGC CLI 4.37 removed the legacy
model-download `--org` option. This compatibility helper and the actual ONNX
model files have separate SHA256 identities in runtime metadata. Local runs without an artifact reference use
this checkout's service definitions.

## Local run

Prerequisites: Linux amd64, NVIDIA driver compatible with the selected RT-CV
image, NVIDIA Container Toolkit, Docker with Compose v2, Python with venv support,
and registry/model access. Run as a Docker-enabled user, or set
`DOCKER_USE_SUDO=true` when passwordless sudo is available.

The lifecycle installs the benchmark dependencies and the CLI/core packages
from this same checkout into `perf/.venv`, using Python 3.13 through uv. The
standard shared runner's own Python environment remains supported by the
benchmark entry point.

From the repository root:

```bash
export CONFIG_ID=rtxpro6000bw-1-compose-base-rtdetr-warehouse
export NGC_API_KEY   # provision in the environment or secret manager
export VSS_PERF_VIDEO=/absolute/path/to/Camera_03.mp4
./compose/run_benchmark.sh -s quick_test -O perf/results/local -r 3.3.0 -d
```

The optional media recipe loops the supplied H.264/H.265 video through MediaMTX
using FFmpeg stream copy, avoiding additional video encoding GPU load. Its
`rtsp://perf-media:8554/compose-base` address is a deployment-internal source
URI, passed directly to VIOS registration and RT-CV source configuration.
To use an existing RTSP stream instead, set `VSS_PERF_STREAM_URL` and omit
`VSS_PERF_VIDEO`.

When neither source variable is provided, preflight downloads the pinned
`nvstaging/vss-warehouse/vss-warehouse-app-data:v3.3.0-09152026` NGC resource and
copies `videos/nv-warehouse-4cams/Camera_03.mp4` into the runtime fixture directory.
The source reference is saved beside the fixture. Initial dataset and model
downloads need NGC access; a cached video and ONNX model can be reused.

Run the longer scenario using `-s sustained_test`. Use a distinct output
directory for each invocation. The benchmark preserves the matrix config ID
exactly and produces one standard
`compose-base_<config_id>_<YYYYMMDD>_<HHMMSS>.json` result, with UTC time.

## Lifecycle

`pre_flight.sh` checks host prerequisites, stages writable VIOS and RT-CV
configuration copies, and validates Compose. `deploy.sh` starts VIOS,
Elasticsearch, Kafka, Redis, the analytics API and public ingress using Docker's
bounded readiness wait. `post_deploy.sh` configures the host-side VSS CLI against
the configured public origin, registers the stream through `vss vios add`, and
uses the returned sensor ID when starting RT-CV. The benchmark performs warm-up
requests before measuring. `teardown.sh` stops only this Compose project and
checks that its containers and networks are gone. It resets only the project's
VIOS PostgreSQL sensor database volume, discovered through Docker labels, because
VIOS retains sensor registrations there while its stream plane does not restore
them after a restart. Each clean run therefore provisions a fresh sensor fixture;
model, engine, video, and APT caches are preserved.

Runtime data lives under `perf/.runtime/<config_id>` by default. Model/engine
caches, service logs and fixture data persist for inspection. A sanitized
`perf/.runtime-state.env` allows the actual shared runner to execute teardown
outside its stage environment. Stage scripts enforce the limits in
`configs/config.yaml`; teardown runs after failures.

Source configuration is unchanged. Fixed container names in the production
recipes mean this benchmark must run exclusively on its selected host.

## Measurements and result contract

The benchmark measures actual RT-CV instantaneous FPS emitted in `**PERF` logs,
active VIOS streams, and VIOS/analytics command wall time. CLI latency includes
client startup and is labeled accordingly. Analytics sensor inventories are
calibration-backed: an empty successful response is valid and is not evidence
that a VIOS registration has entered the calibration inventory.

`quick_test` measures one active stream for 30 seconds after two warm-up
requests. `sustained_test` measures for 300 seconds. Initial pass criteria require
positive RT-CV throughput of at least 1 FPS and CLI p95 no greater than 10 seconds.
These are onboarding checks, not claimed production performance baselines.

The result is validated against the existing shared Pydantic schema and the
package's JSON Schema before writing. `tests/expected_result.json` demonstrates
the contract; measured timings are never compared with its example values.
Deployment failures create failed standard results in the local wrapper;
teardown failures update the same result rather than leaving a successful JSON.

To upload a local result, set `UPLOAD_TO_MINIO=true`, `MINIO_ACCESS_KEY` and `MINIO_SECRET_KEY`
in the environment. `MINIO_ENDPOINT` overrides the documented default in the config. Shared credentials
are injected by Jenkins. `global.upload_after_teardown: true` opts this package
into final publication after teardown, so a teardown failure cannot leave a
successful dashboard result. Results go to the `perf-results` bucket under
`compose-base/`. The documented dashboard is <http://10.111.52.248:8080/>.

The onboarding reference currently uses schema `0.1`. Schema v2.0 requires the
authoritative schema from Chandrahas and confirmation of dashboard acceptance;
the version must not be changed without validating that contract.

## Promotion integration

The repository source block should identify this repository, its merged branch,
and the repository root as `scm.folder`. The generic trigger uses
`PERF_SERVICE=compose-base`, `PERF_FOLDER=perf`, `IMAGE_ARCH=amd64`,
`NODE_LABELS=RTXPRO6000BW-SE`, `PERF_CONFIG_IDS=all`, `PERF_SCENARIO=quick_test`
and `UPLOAD_TO_MINIO=true`. The Compose resource is the artifact under test.

Onboarding is complete only after a merged source PR, approved promotion MR,
successful shared run, matching dashboard result and tracker evidence. Local
source validation alone does not establish those external integration outcomes.
