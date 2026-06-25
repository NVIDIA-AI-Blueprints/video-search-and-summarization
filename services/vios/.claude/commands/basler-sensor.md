---
name: "basler-sensor"
description: "Build, deploy, verify, and troubleshoot Basler camera support (discovery adaptor + stream producer, pylon SDK)"
metadata:
  authors:
    - "Rahul Bhagwat <rbhagwat@nvidia.com>"
    - "Divy Sitlani <dsitlani@nvidia.com>"
  tags:
    - basler
    - pylon
    - discovery
    - producer
    - sensor
    - adaptor
  languages:
    - cpp
    - bash
  domain: backend
---

# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

The user wants to build, deploy, verify, or troubleshoot Basler camera support. Basler is **opt-in**: pylon is an optional dependency, every `.so` loads cleanly without it, and basler activates only when the SDK is present at build time (headers) and runtime (libs).

Pick the section that matches the user's intent. For an end-to-end bring-up, run §2 (Build) then §3 (Deploy), then verify with §5.

---

## 1. Architecture (read first)

Basler spans the two microservices, mirroring the split topology:

**Control plane — `sensor-ms`** (discovery)
- Source: `src/adaptors/sensors_discovery/basler/{basler_discovery.h,basler_discovery.cpp,Makefile}`
- Artifact: `prebuilts/<arch>/libbasler_discovery.so` (packaged under `MODULE=sensor`)
- Loaded by `adaptor_loader.cpp` via `dlopen` + `dlsym(createObject/destroyObject)` — the standard adaptor plugin path.
- Type constant: `SENSOR_TYPE_BASLER = "sensor_basler"` (`include/sensor_info.h`); sensor ids carry the `basler-` prefix (`buildSensorIdFromSerial`).

**Data plane — `streamprocessing`** (grab/encode → recording, RTSP, live)
- `BaslerStreamProducer` (`src/framework/media/video_source/producers/basler/`) — pylon grab → Bayer8→I420 → H.264 encode → `distributeToConsumers`. Built as a **separate** `prebuilts/<arch>/libbasler_producer.so` (packaged only under `MODULE=streamprocessing`).
- `BaslerStreamMonitor` (`producers/basler_stream_monitor.{h,cpp}`) — a pylon-free singleton registry compiled into `libnvvideo_source.so` (ships in every image). It lazily `dlopen`s `libbasler_producer.so` on first use, owns one producer per camera, and exposes `getProducer`/`getVideoCodec`/`getVideoHeaders` to the RTSP (`NvMediaSource`), recorder, and live paths.
- The dlopen split keeps the shared core lib pylon-free and lets the streamprocessing image run with **or** without pylon (absent → basler simply does not start).

**Config:** `adaptor_config.json` (under `deployment/*/configs/` and `configs/`) has a dedicated, discovery-only `basler` entry (`discovery_adaptor_lib_path: prebuilts/arch/libbasler_discovery.so`, no control adaptor). It is selected by name via `VST_ADAPTOR=basler`.

**Pylon contract:** every basler `.so` compiles against pylon headers (`-isystem`) but does **not** link pylon (no DT_NEEDED). Pylon is brought into the process at runtime by `LD_PRELOAD` (exported from the entrypoint when `INSTALL_PYLON=1`); C++ typeinfo (`GenericException`) cannot bind lazily, so pylon must be in the symbol space before the basler `.so` is dlopened.

---

## 2. Build

### 2.1 Compilation container (x86) — must have pylon

The C++ compile runs **inside** the x86 build image referenced by `X86_BUILD_IMAGE` in the top-level `Makefile` (it does `docker run --rm -v $(TOP):/root $(X86_BUILD_IMAGE) ... make`). For basler this image must include the **pylon SDK**, because the discovery and producer Makefiles `#include` pylon headers (and hard-error if `pylon-config` is missing). Read the configured value from the Makefile rather than assuming it — it varies by environment:

```bash
IMG=$(grep -E '^X86_BUILD_IMAGE' Makefile | head -1 | sed -E 's/^[^=]*=[[:space:]]*//')
echo "X86_BUILD_IMAGE = $IMG"
```

**Step 1 — check if that build image is present.** Check yourself, and if unsure ask the user:
```bash
docker image inspect "$IMG" >/dev/null 2>&1 && echo "present" || echo "ABSENT - build it (step 3)"
```

**Step 2 — if present:** confirm it actually has pylon (`docker run --rm "$IMG" /opt/pylon/bin/pylon-config --version` — `pylon-config` is **not** on `PATH`, it lives under `/opt/pylon/bin`). It is already wired into the Makefile, so just proceed to §2.2. Override per-invocation if needed: `make cc=0 X86_BUILD_IMAGE=<image> ...`.

**Step 3 — if absent:** create it by overlaying the pylon SDK onto a base x86 build image (same toolchain, no pylon). **This needs the pylon SDK present on the host** (extracted dir or tarball from your Basler mirror).

First check whether a base build image is **already present** — if so, reuse it and **skip the `docker build`**. A common convention is the same tag with the pylon suffix stripped:
```bash
BASE_IMG="${IMG%_pylon}"   # candidate base = X86_BUILD_IMAGE without a trailing _pylon
docker image inspect "$BASE_IMG" >/dev/null 2>&1 \
  && echo "base present: $BASE_IMG (skip docker build)" \
  || echo "base absent - build it from the devel Dockerfile"
```

Build the base image **only if it is absent** (defined by `cicd_files/x86_64/devel/Dockerfile.devel`):
```bash
docker build -t "$BASE_IMG" -f cicd_files/x86_64/devel/Dockerfile.devel cicd_files/x86_64/devel
```

Then overlay pylon onto the base image and commit it as `$IMG` (assumes the extracted SDK is at `/opt/pylon` on the host):
```bash
cid=$(docker create "$BASE_IMG" sleep infinity)
docker cp /opt/pylon "$cid:/opt/pylon"
docker start "$cid"
docker exec "$cid" bash -c 'echo /opt/pylon/lib > /etc/ld.so.conf.d/pylon.conf && ldconfig && /opt/pylon/bin/pylon-config --version'
docker commit "$cid" "$IMG"     # $IMG = the X86_BUILD_IMAGE value read above
docker rm -f "$cid"
```
If you tag the result differently, set `X86_BUILD_IMAGE` in the `Makefile` to your tag (or pass `X86_BUILD_IMAGE=<tag>` on the build invocation).

### 2.2 Build the sensor and streamprocessing app containers

The app images are built `FROM ${IMAGE_REGISTRY:-vios}/vst-base:<base-tag>` (the runtime base). `build.sh container` does **not** build that base — it must already exist locally or be pullable, otherwise the app `docker build` fails at the `FROM`. On a fresh system, build the base once first (it needs no pylon):

```bash
./build.sh base-container base-tag=<base-tag>      # produces ${IMAGE_REGISTRY:-vios}/vst-base:<base-tag>
```

Then build the app containers — **ask the user for `base-tag`** (defaults to `2.1.0-runtime-26.04.1` if omitted):

```bash
./build.sh container module=sensor,streamprocessing tag=basler-branch base-tag=<base-tag>
```

- On x86 this compiles via `make cc=0` **inside `X86_BUILD_IMAGE`** (the pylon build image from §2.1), then builds the app images `FROM` the base. (arm64 uses `cc=1` and the aarch64 cross-compiler image.)
- Produces `${IMAGE_REGISTRY:-vios}/vst-sensor:basler-branch` and `${IMAGE_REGISTRY:-vios}/vst-streamprocessing:basler-branch`. `IMAGE_REGISTRY` defaults to `vios`; override it via the env var if needed.
- Only these two modules are needed: `sensor` (discovery) and `streamprocessing` (producer + RTSP + recorder + live).

---

## 3. Deploy

`compose.env` lives at `deployment/stream-processing/docker-compose/compose.env`. Set the pylon switches and the host/path/adaptor values, then bring the stack up.

### 3.1 Edit `compose.env`

```env
# --- pylon (basler) ---
INSTALL_PYLON=1                         # entrypoint runs tools/install_pylon.sh and exports LD_PRELOAD
PYLON_HOST_PATH=<host path to the extracted pylon SDK>   # bind-mounted read-only at /opt/pylon

# --- adaptor selection ---
VST_ADAPTOR=basler                      # selects the dedicated basler discovery entry in adaptor_config.json
```

For `HOST_IP`, `VST_CONFIG_PATH`, `VST_VOLUME`, copy the oneclick deployment logic (`deployment/oneclick_dc_deployment_for_dev.py`):

- `HOST_IP` — auto-detect:
  ```bash
  ip route get 8.8.8.8 2>/dev/null | grep -oP 'src \K[0-9.]+' | head -1   # fallback: hostname -I | awk '{print $1}'
  ```
- `VST_CONFIG_PATH` — `<docker-compose dir>/configs` (i.e. `deployment/stream-processing/docker-compose/configs`).
- `VST_VOLUME` — `<docker-compose dir>/vst_volume`.

Point the image vars at what you built in §2.2 (note the `vios/` registry prefix from `IMAGE_REGISTRY`):
```env
VST_SENSOR_IMAGE=vios/vst-sensor:basler-branch
VST_STREAM_PROCESSOR_IMAGE=vios/vst-streamprocessing:basler-branch
```

### 3.2 Fresh-system prerequisites (before bringing the stack up)

- Create the bind-mount dirs so Docker does not create them as root: `mkdir -p "$VST_CONFIG_PATH" "$VST_VOLUME"`. `VST_CONFIG_PATH` must contain the runtime configs (`adaptor_config.json`, `vst_config.json`, …) — the repo's `configs/` dir already has them, which is why it is the default.
- Docker with the **NVIDIA Container Toolkit** must be installed (the services declare `runtime: nvidia`).
- The pylon SDK must be **extracted** at `PYLON_HOST_PATH` on the host (bind-mounted read-only to `/opt/pylon`; `install_pylon.sh` then detects it and skips extraction).

### 3.3 Bring up the stack

```bash
cd deployment/stream-processing/docker-compose
docker compose -f docker-compose.yaml --env-file ./compose.env up --force-recreate -d
```

Deploy NVStreamer separately if needed (its own compose under `nvstreamer/`). On startup the entrypoint runs `install_pylon.sh` (because `INSTALL_PYLON=1`), which detects the bind-mounted `/opt/pylon`, runs `ldconfig`, registers `ld.so.preload`, and then `LD_PRELOAD` is exported before `launch_vst`.

---

## 4. Verify build artifacts

```bash
# discovery adaptor (sensor) and producer (streamprocessing)
ls prebuilts/x86_64/libbasler_discovery.so prebuilts/x86_64/libbasler_producer.so
readelf -d prebuilts/x86_64/libbasler_discovery.so | grep NEEDED
```

Expected NEEDED: only `libstdc++.so.6`, `libgcc_s.so.1`, `libc.so.6` — **no pylon / GenApi / GenICam** entries (pylon is deferred to runtime). Confirm pylon symbols are referenced but unresolved:
```bash
nm -D --undefined-only prebuilts/x86_64/libbasler_producer.so | c++filt | grep -E "Pylon::|GenICam|GenApi" | head
```

---

## 5. Verify runtime

**Discovery (`sensor-ms`):**
```
adaptor_loader.cpp: Loading Discovery adaptor: prebuilts/arch/libbasler_discovery.so
basler_discovery.cpp: Started Basler sensor discovery task
basler_discovery.cpp: BaslerDiscovery: added sensor serial=<S> model=<M> ip=<IP>
sensor_monitoring.cpp: Added sensor successfully: basler-<S>
```
```bash
curl -s http://<HOST_IP>:30000/api/v1/sensor/list | jq '.[] | select(.sensorId|startswith("basler-"))'
```

**Data plane (`streamprocessing-ms-1`):**
```
basler_stream_monitor.cpp:   BaslerStreamMonitor: started basler stream basler-<S> serial <S>
basler_stream_producer.cpp:  BaslerStreamProducer[basler-<S>]: encode pipeline started
basler_stream_producer.cpp:  BaslerStreamProducer[basler-<S>]: cached SPS/PPS for SDP (sps=... pps=...)
basler_stream_producer.cpp:  BaslerStreamProducer[basler-<S>]: grabbed frame N
```
RTSP playback (stricter than VLC; proves DESCRIBE→SETUP→PLAY and the SDP carry SPS/PPS):
```bash
ffprobe -rtsp_transport tcp -i "rtsp://<HOST_IP>:30554/basler_sensor/basler-<S>?videoOnly=true"
```

---

## 6. Troubleshooting

**`undefined symbol: _ZTIN24GenICam_3_1_Basler_pylon16GenericExceptionE`**
The basler `.so` failed to load: C++ typeinfo cannot bind lazily, so pylon must be in the process before the dlopen. Ensure `INSTALL_PYLON=1` (entrypoint exports `LD_PRELOAD=/opt/pylon/lib/libpylonbase.so:/opt/pylon/lib/libpylonutility.so`) and that `/opt/pylon` is populated. Confirm: `docker exec -u 0 <container> ldconfig -p | grep -i pylon`.

**`BaslerDiscovery: pylon SDK not available; basler discovery disabled`**
Expected graceful degradation when pylon is absent. Not an error — install pylon (`INSTALL_PYLON=1` + `PYLON_HOST_PATH`) or leave basler off.

**Build fails: `pylon SDK not found at /opt/pylon`**
The basler discovery/producer Makefiles hard-error without `pylon-config`. The compile must run in the `_pylon` build image (§2.1). The `src/adaptors/Makefile` and `video_source/Makefile` guard the basler recursion on `$(wildcard $(PYLON_ROOT)/bin/pylon-config)`, so a non-pylon build skips basler rather than failing — if it failed, the build image lacks pylon.

**Build fails inside basler with `-Werror=overloaded-virtual` (pylon headers)**
The Makefiles convert pylon `-I` to `-isystem` via `$(patsubst)` to suppress warnings in pylon's own headers. Verify that conversion is still present.

**Discovery runs but no cameras appear**
- Reachability: `docker exec -u 0 <container> /opt/pylon/bin/pylonipconfigurator list` (GigE) or `lsusb` (USB).
- GigE needs host routing to the camera subnet; `sensor-ms` uses `network_mode: host`.
- Vendor must be exactly "Basler" — `basler_discovery.cpp` filters on `vendor.find("Basler")`.

**VLC/ffprobe stops right after DESCRIBE (no video track in SDP)**
`createNewSMS` must build a video-only subsession for `basler-` streams; otherwise the SDP has no media. Confirmed working when `ffprobe` shows `m=video … H264/90000` with `sprop-parameter-sets` and decodes frames.

**Live fails after a DB cache rebuild (status flips ONLINE)**
Known: the discovery loop re-stamps `stream_status=ONLINE` every ~5s, which can clobber `STREAMING`. See the `basler-streaming-status-volatile` note.

---

## 7. Quick commands

```bash
# Is the configured x86 build image present? (read the tag from the Makefile)
IMG=$(grep -E '^X86_BUILD_IMAGE' Makefile | head -1 | sed -E 's/^[^=]*=[[:space:]]*//')
docker image inspect "$IMG" >/dev/null 2>&1 && echo "present: $IMG" || echo "absent: $IMG"

# Build sensor + streamprocessing (ask user for base-tag)
./build.sh container module=sensor,streamprocessing tag=basler-branch base-tag=<base-tag>

# Deploy
cd deployment/stream-processing/docker-compose && \
  docker compose -f docker-compose.yaml --env-file ./compose.env up --force-recreate -d

# Logs
docker logs sensor-ms 2>&1 | grep -iE "basler|pylon|discovery"
docker logs streamprocessing-ms-1 2>&1 | grep -iE "basler|pylon|grabbed frame"

# Cameras via API
curl -s http://${HOST_IP}:30000/api/v1/sensor/list | jq '.[] | select(.sensorId|startswith("basler-"))'
```
