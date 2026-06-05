<!--
SPDX-FileCopyrightText: Copyright (c) 2019-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

<h1>Media Service</h1>

<h2>Build and Launch Media Service</h2>

Built images are named from two environment variables, so no registry is hardcoded in the source tree:

- `IMAGE_REGISTRY` — registry/org prefix for all VST images (default `vios`, e.g. `vios/vst-sensor:latest`)
- `NVSTREAMER_IMAGE` — full repository for the NVStreamer image (default `nvstreamer`, e.g. `nvstreamer:latest`)

The defaults build images locally with no registry. To publish to your own registry, export these before building:

```bash
export IMAGE_REGISTRY=my-registry.example.com/vios
export NVSTREAMER_IMAGE=my-registry.example.com/nvstreamer
```

### A) Build the compile toolchain image (x86_64)

`build.sh` compiles every module inside a toolchain container. Build it once from the in-repo recipe and tag it to the name `build.sh` expects by default:

```bash
docker build -t vios-build:x86-24.04-cuda13.0.0 \
  -f cicd_files/x86_64/devel/Dockerfile.devel cicd_files/x86_64/devel
```

To use a prebuilt toolchain image instead, export `X86_BUILD_IMAGE` to point at it.

### A2) Build the compile toolchain image (aarch64 cross-compile)

Required once before any `./build.sh arch=arm64` or `make cc=1` invocation:

```bash
cd cicd_files/aarch64/devel
./build_cross_compile_container.sh
cd -
```

This produces `vios-build:aarch64-cross-compiler`, the default tag `build.sh` and `make cc=1` expect via `AARCH64_CC_IMAGE`. To use a prebuilt image instead, export `AARCH64_CC_IMAGE` to point at it.

### B) Build the runtime base container

The base image carries the system packages shared by every service image. Build it once, then reuse it for all subsequent module/container builds.

x86_64:

```bash
./build.sh base-container
```

aarch64:

```bash
./build.sh arch=arm64 base-container
```

Optional: tag and push the base image to the registry.

```bash
./build.sh base-container base-tag=<base-tag> push=1
```

### C) Build module containers

Build the `sensor` and `streamprocessing` module containers (clean first for a fresh build):

x86_64:

```bash
./build.sh clean
./build.sh container module=streamprocessing,sensor
```

aarch64:

```bash
./build.sh arch=arm64 clean
./build.sh arch=arm64 container module=streamprocessing,sensor
```

### D) Build the NVStreamer container

x86_64:

```bash
./build.sh clean
./build.sh nvstreamer container
```

aarch64:

```bash
./build.sh arch=arm64 clean
./build.sh arch=arm64 nvstreamer container
```

### E) Run Media Service

The compiled images are deployed via docker-compose. Pass the exact images you built to the one-click deployment (local builds are tagged `latest`). Use `--target all` so both the VST services and NVStreamer are deployed (the default `--target vios` brings up only the VST services and ignores the `--nvstreamer-*` flags). The command is identical for x86_64 and aarch64 — for aarch64, run it on the aarch64 target host where the arm64 images were built or loaded:

```bash
python3 deployment/oneclick_dc_deployment_for_dev.py deploy --target all \
  --streamprocessor-image vios/vst-streamprocessing --streamprocessor-tag latest \
  --sensor-image vios/vst-sensor --sensor-tag latest \
  --nvstreamer-image nvstreamer --nvstreamer-tag latest \
  --auto --force
```

See `deployment/1click_README.md` and `deployment/oneclick_dc_deployment_for_dev.py` for the full one-click deployment flow.

For all build options, run `./build.sh help`.

<h2>Quick Start</h2>
<p>To quickly test if Media Service is properly set up and launched, open the dashboard in any web browser.</p>
<h5>Browser</h5>
<ul>
<li>Launch web browser</li>
<li>In the address bar enter the IP Address of the host on which Media Service is running followed by the ingress port and path:
<ul>
<li>Example : <strong><em>&lt;IP_ADDRESS&gt;:30888/vst<br /></em></strong>Sample URL: <a href="http://localhost:30888/vst"><strong><em>http://localhost:30888/vst</em></strong></a></li>
</ul>
</li>
<li>It is expected that the web browser should load the Media Service dashboard</li>
</ul>

<h2>Troubleshooting</h2>

### `docker pull` fails with an "Incorrect Repository Format" / unsupported manifest error

The published images are multi-arch OCI image indexes (`application/vnd.oci.image.index.v1+json`) that also carry BuildKit attestation manifests (SBOM/provenance). Those attestations show up as `unknown/unknown` platform entries in the manifest list, and some Docker/containerd versions try to resolve them and fail with errors such as `Incorrect Repository Format`, `no matching manifest`, or `unsupported manifest media type`.

Fix: pull for an explicit platform so Docker resolves a single concrete image manifest instead of the full index.

```bash
# x86_64 hosts
docker pull --platform linux/amd64 <image>:<tag>

# Arm hosts (Grace / Jetson)
docker pull --platform linux/arm64 <image>:<tag>
```

For example:

```bash
docker pull --platform linux/amd64 nvcr.io/nvidia/vss-core/vss-vios-ingress:3.2.0
```
