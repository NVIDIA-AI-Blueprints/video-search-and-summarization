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

To use a prebuilt toolchain image instead, export `X86_BUILD_IMAGE` (or `AARCH64_CC_IMAGE` for Jetson cross-compile) to point at it.

### B) Build the runtime base container (x86_64)

The base image carries the system packages shared by every service image. Build it once, then reuse it for all subsequent module/container builds.

```bash
./build.sh base-container
```

Optional: tag and push the base image to the registry.

```bash
./build.sh base-container base-tag=<base-tag> push=1
```

### C) Build module containers

Build the `sensor` and `streamprocessing` module containers:

```bash
./build.sh container module=streamprocessing,sensor
```

### D) Build the NVStreamer container

```bash
./build.sh nvstreamer container
```

### E) Run Media Service

The compiled images are deployed via docker-compose. If you built with the default `vios` / `nvstreamer` names, point the one-click deployment at them — local builds are tagged `latest`, so line the tags up too:

```bash
python3 deployment/oneclick_dc_deployment_for_dev.py deploy \
  --image-registry vios --all-tag latest \
  --nvstreamer-image nvstreamer --nvstreamer-tag latest \
  --auto --force
```

See `deployment/1click_README.md` and `deployment/oneclick_dc_deployment_for_dev.py` for the full one-click deployment flow.

For all build options, run `./build.sh help`.

<h2>Quick Start</h2>
<p>To quickly test if Media Service is properly set up and launched, one can test it with any web browser or curl command,
launch Media Service and perform any one of below mentioned tests.</p>
<h5>A) Browser</h5>
<ul>
<li>Launch web browser</li>
<li>In the address bar enter IP Address of host on which Media Service is running followed by port number followed by Media Service API to test.
<ul>
<li>Example : <strong><em>&lt;IP_ADDRESS&gt;:&lt;PORT_NUMBER&gt;/api/&lt;API NAME&gt;<br /></em></strong>Sample URL: <a href="http://192.168.1.23:81/api/help"><strong><em>http://192.168.1.23:81/api/help</em></strong></a></li>
</ul>
</li>
<li>It is expected that web browser should print the JSON response received from Media Service</li>
</ul>
<h5>B) Curl Command</h5>
<ul>
<li>Launch Linux Terminal</li>
<li>Execute curl command with IP Address of host on which Media Service is running followed by port number followed by Media Service API to test.
<ul>
<li>Example: <strong><em>curl &lt;IP_ADDRESS&gt;:&lt;PORT_NUMBER&gt;/api/&lt;API NAME&gt;</em></strong><br /> Sample curl command: <strong><em>curl </em></strong><a href="http://192.168.1.23:81/api/help"><strong><em>http://192.168.1.23:81/api/help</em></strong></a></li>
</ul>
</li>
<li>It is expected that the JSON response received from Media Service should be printed in terminal</li>
</ul>
