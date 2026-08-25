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

# =============================================================================
# Unified, self-contained multi-stage build for ALL THREE VIOS images
# (sensor, streamprocessing, nvstreamer) -- one Dockerfile, any flavor, either
# arch. The only per-image knob is --build-arg MODULE.
#
# It folds the three separate build inputs into one image:
#   builder  = cicd_files/<arch>/devel/Dockerfile.devel  (the toolchain)
#              + `MODULE=<m> make package`               (the compile step
#                that build.sh runs OUTSIDE Docker today)
#   runtime  = cicd_files/<arch>/Dockerfile.base         (runtime base + OSRB
#              codec removals) + cicd_files/<arch>/Dockerfile.app (unpack the
#              compiled vst_release + wire prebuilt libs)
#
# Because the builder stage is discarded, the CUDA-devel toolchain + all the
# -dev packages never land in the published image -- so it adds NO new
# OSRB/scan surface beyond today's app image.
#
# Build (buildx sets TARGETARCH per platform):
#   docker buildx build --build-arg MODULE=streamprocessing \
#       -t vss-vios-streamprocessing --platform linux/amd64,linux/arm64 services/vios
#   docker buildx build --build-arg MODULE=sensor      -t vss-vios-sensor     ... services/vios
#   docker buildx build --build-arg MODULE=nvstreamer  -t vss-vios-nvstreamer ... services/vios
#
# nvstreamer differs only in the compile step: empty make MODULE (full lib set)
# plus a web-UI build staged into webroot/ -- both handled below off MODULE.
#
# TODO(validate on a real build):
#  - `MODULE=<m> make package` compiles + packages out/<arch>/vst_release.tbz2
#  - nvstreamer: `npm run build` of ui/vios-ui succeeds in the toolchain (Node 18)
#  - aarch64 app wiring is transcribed from Dockerfile.app (SBSA); verify
#  - grpc is compiled in the builder on every cache miss (slow). If CI build
#    time matters, publish the toolchain as a PRIVATE GHCR package and set
#    `--build-arg TOOLCHAIN_IMAGE=...` to skip rebuilding it (see below).
# =============================================================================

ARG CUDA_VERSION=13.2.0
ARG UBUNTU_VERSION=ubuntu24.04

# Optional: point at a prebuilt toolchain (private GHCR) to skip the ~grpc build.
# Defaults to the public CUDA devel image so a clean clone builds with no creds.
ARG TOOLCHAIN_IMAGE=nvcr.io/nvidia/cuda:${CUDA_VERSION}-devel-${UBUNTU_VERSION}

# -----------------------------------------------------------------------------
# builder -- toolchain (Dockerfile.devel) + compile the requested module
# -----------------------------------------------------------------------------
FROM ${TOOLCHAIN_IMAGE} AS builder
ARG TARGETARCH
ENV DEBIAN_FRONTEND=noninteractive

# CUDA include root for the Makefile (-I $(CUDA_TARGET_DIR)/include). The internal
# devel images export this via ENV (cicd_files/aarch64/devel/Dockerfile); the
# Makefile's `?=` fallback is /usr/local/cuda-13.0, which does NOT exist on the
# public CUDA 13.2 devel image (it ships /usr/local/cuda, -13, -13.2), so without
# this the public-toolchain build fails with "cuda.h: No such file or directory"
# on BOTH arches. Use the version-agnostic /usr/local/cuda symlink so a CUDA bump
# does not silently re-break this.
ENV CUDA_TARGET_DIR=/usr/local/cuda

# Toolchain packages (from Dockerfile.devel). No-op if TOOLCHAIN_IMAGE already
# has them (a prebuilt toolchain), so this stays valid for both paths.
RUN apt-get update && apt-get install -y --no-install-recommends \
        g++ lbzip2 libcurl4-openssl-dev libcurl3-gnutls libgtest-dev libjsoncpp-dev \
        libssl-dev libxml2-dev libxml2 sqlite3 libsqlite3-dev uuid uuid-dev \
        iputils-ping gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
        gstreamer1.0-plugins-ugly gstreamer1.0-plugins-bad libgstreamer1.0-0 \
        libgstreamer-plugins-base1.0-dev libboost-all-dev make build-essential \
        autoconf libtool git pkg-config wget librdkafka-dev libopencv-dev python3 \
        python3-pip ca-certificates gnupg lsb-release ntp ntpstat \
        libhiredis1.1.0 libldap2-dev libpq-dev libpqxx-dev libpaho-mqttpp-dev \
        libpaho-mqtt-dev \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
RUN pip3 install --break-system-packages requests

# Node 22 for the nvstreamer vios-ui build. Ubuntu 24.04 apt nodejs is 18 (EOL,
# below vite 8's ^20.19/>=22.12; the tooling imports node:util `styleText`,
# added in 20.12/22). Install from NodeSource via an explicit keyring -- same
# method as cicd_files/x86_64/devel/Dockerfile.devel, not the setup script.
# Arch-agnostic: NodeSource serves amd64 + arm64, so each native runner gets its
# own arch's Node. sensor/streamprocessing do not build the UI and ignore this.
ARG NODE_MAJOR=22
RUN install -d -m 0755 /usr/share/keyrings \
    && wget -qO- https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
        | gpg --dearmor -o /usr/share/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/nodesource.gpg] https://deb.nodesource.com/node_${NODE_MAJOR}.x nodistro main" \
        > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update && apt-get install -y --no-install-recommends nodejs \
    && apt-get clean && rm -rf /var/lib/apt/lists/* && node -v && npm -v

# cmake 3.19.6 (required by grpc) -- arch-aware asset
ENV MY_INSTALL_DIR=/usr/local
RUN set -eux; case "${TARGETARCH}" in amd64) CM=x86_64;; arm64) CM=aarch64;; *) echo "unsupported arch ${TARGETARCH}" >&2; exit 1;; esac; \
    wget -q -O /tmp/cmake-linux.sh "https://github.com/Kitware/CMake/releases/download/v3.19.6/cmake-3.19.6-Linux-${CM}.sh"; \
    sh /tmp/cmake-linux.sh -- --skip-license --prefix="${MY_INSTALL_DIR}"; rm -f /tmp/cmake-linux.sh

# grpc v1.50.0 (built once; cached across module builds until this layer changes)
RUN git clone --recurse-submodules -b v1.50.0 --depth 1 --shallow-submodules \
        https://github.com/grpc/grpc /var/local/git/grpc \
    && cd /var/local/git/grpc && mkdir -p cmake/build && cd cmake/build \
    && cmake -DgRPC_INSTALL=ON -DgRPC_BUILD_TESTS=OFF -DCMAKE_CXX_STANDARD=20 \
        -DCMAKE_CXX_STANDARD_REQUIRED=ON -DCMAKE_INSTALL_PREFIX="${MY_INSTALL_DIR}" ../.. \
    && make -j"$(nproc)" && make install \
    && rm -rf /var/local/git/grpc
RUN echo /usr/local/lib > /etc/ld.so.conf.d/usr-local-lib.conf \
    && case "${TARGETARCH}" in amd64) echo /usr/local/lib/x86_64-linux-gnu;; arm64) echo /usr/local/lib/aarch64-linux-gnu;; esac \
        >> /etc/ld.so.conf.d/usr-local-lib.conf \
    && ldconfig

# Compile the requested image.
#   MODULE selects the flavor (this is the ONLY per-image difference):
#     sensor | streamprocessing -> a module subset (package_vms.sh gates each
#                                  prebuilt lib on the module name)
#     nvstreamer                -> the FULL set (make MODULE empty makes every
#                                  `-z ${MODULE}` block fire) PLUS the web UI:
#                                  build ui/vios-ui and stage it into webroot/,
#                                  which packaging then packs into the image
#                                  (mirrors build.sh's build_vios_ui_webroot).
#   Target `package` = build $(APP)=launch_vst, then packaging/package_vms.sh
#   -> out/$(arch)/vst_release.tbz2. `arch` auto-detects from `uname -p`, so a
#   native amd64/arm64 runner produces the matching arch.
#   IMPORTANT: `cc` stays UNSET. In the Makefile cc=1 and cc=0 BOTH re-invoke
#   make via `docker run <toolchain>`; only cc-unset runs the real build rules --
#   and we already ARE the toolchain. PROJECT is not needed for `package`.
ARG MODULE
COPY . /root
WORKDIR /root
RUN set -eux; \
    case "${MODULE}" in \
      sensor|streamprocessing) MK_MODULE="${MODULE}";; \
      nvstreamer) MK_MODULE=""; \
        ( cd ui/vios-ui && npm run install:link && npm run build ); \
        rm -rf webroot/assets webroot/favicon webroot/index.html webroot/runtime-config.js; \
        cp -rf ui/vios-ui/dist/. webroot/;; \
      *) echo "MODULE must be one of: sensor | streamprocessing | nvstreamer" >&2; exit 1;; \
    esac; \
    MODULE="${MK_MODULE}" make package -j"$(nproc)"; \
    mkdir -p /stage/home/vst && tar -I lbzip2 -xf out/*/vst_release.tbz2 -C /stage/home/vst

# -----------------------------------------------------------------------------
# runtime -- base (Dockerfile.base) + app (Dockerfile.app), reproduced PER ARCH.
# The x86 and aarch64 originals genuinely differ (USER, several ENV vars, and the
# app symlink set), so shared static ENV/USER directives cannot express both. All
# common work + arch-branched RUN wiring lives in `runtime-base`; the per-arch
# ENV/USER live in runtime-amd64 / runtime-arm64, selected by
# `FROM runtime-${TARGETARCH}`.
# -----------------------------------------------------------------------------
FROM nvcr.io/nvidia/cuda:${CUDA_VERSION}-runtime-${UBUNTU_VERSION} AS runtime-base
ARG TARGETARCH
ENV DEBIAN_FRONTEND=noninteractive

# Runtime packages -- EXACT parity with cicd_files/<arch>/Dockerfile.base (runtime
# libs, NOT -dev; only gstreamer base+good, then the OSRB strip below). No extra
# packages: the payload is decompressed in the builder and COPYed in extracted.
# aarch64 base additionally runs `apt-get upgrade -y` (aarch64 Dockerfile.base:23);
# x86 does not. Per-arch extras are gated on TARGETARCH.
RUN apt-get update \
    && case "${TARGETARCH}" in arm64) apt-get upgrade -y;; esac \
    && apt-get install -y --no-install-recommends \
        libcurl4 libxml2 libssl3t64 libjsoncpp25 libsqlite3-0 libuuid1 \
        gstreamer1.0-plugins-base gstreamer1.0-plugins-good libgstreamer1.0-0 \
        libgstreamer-plugins-base1.0-0 libboost-filesystem1.83.0 libboost-thread1.83.0 \
        librdkafka1 libpqxx-7.8t64 libhiredis1.1.0 libpaho-mqttpp3-1 \
    && case "${TARGETARCH}" in \
         amd64) apt-get install -y --no-install-recommends \
                  gstreamer1.0-tools libjansson4 libcairo2 libpango-1.0-0 libpangocairo-1.0-0;; \
         arm64) apt-get install -y --no-install-recommends \
                  iptables v4l-utils libprotobuf32t64 libgrpc++1.51t64 kmod;; \
       esac \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Resolve arch-specific paths once: DEB = multiarch triplet, VST = prebuilts dir.
RUN set -eux; case "${TARGETARCH}" in \
        amd64) echo x86_64-linux-gnu > /tmp/deb_arch; echo x86_64 > /tmp/vst_arch;; \
        arm64) echo aarch64-linux-gnu > /tmp/deb_arch; echo aarch64 > /tmp/vst_arch;; \
        *) echo "unsupported arch ${TARGETARCH}" >&2; exit 1;; esac

# librdkafka linker-name symlink -- BOTH arches (Dockerfile.base x86:47 / arm:51).
# Kafka dlopen()s the unversioned name; the runtime package ships only the SONAME.
RUN set -eux; DEB="$(cat /tmp/deb_arch)"; \
    ln -sf "/usr/lib/${DEB}/librdkafka.so.1" "/usr/lib/${DEB}/librdkafka.so"

# OSRB: strip GPL / patent-encumbered / restricted codec, FFmpeg, Intel MediaSDK
# libs -- the COMPLETE union of every rm target in cicd_files/{x86_64,aarch64}/
# Dockerfile.base (mechanically extracted). MediaSDK entries only exist on x86;
# `|| true` no-ops them on arm. Includes the FluidSynth soundfont TimGM6mb.sf2.
RUN set -eux; DEB="$(cat /tmp/deb_arch)"; L="/usr/lib/${DEB}"; G="${L}/gstreamer-1.0"; \
    rm -f \
          "${G}"/libgst1394.so* "${G}"/libgstaom.so* "${G}"/libgstaudioparsers.so* "${G}"/libgstcdparanoia.so* \
          "${G}"/libgstchromaprint.so* "${G}"/libgstcolormanagement.so* "${G}"/libgstdc1394.so* "${G}"/libgstde265.so* \
          "${G}"/libgstdv.so* "${G}"/libgstfaad.so* "${G}"/libgstflac.so* "${G}"/libgstflite.so* \
          "${G}"/libgstfluidsynthmidi.so* "${G}"/libgstgme.so* "${G}"/libgstgsm.so* "${G}"/libgstlc3.so* \
          "${G}"/libgstldac.so* "${G}"/libgstmidi.so* "${G}"/libgstmodplug.so* "${G}"/libgstmpeg2dec.so* \
          "${G}"/libgstmpeg2enc.so* "${G}"/libgstmpegpsmux.so* "${G}"/libgstmsdk.so* "${G}"/libgstnvcodec.so* \
          "${G}"/libgstogg.so* "${G}"/libgstopenaptx.so* "${G}"/libgstopenexr.so* "${G}"/libgstopenh264.so* \
          "${G}"/libgstopenjpeg.so* "${G}"/libgstopenmpt.so* "${G}"/libgstopus.so* "${G}"/libgstopusparse.so* \
          "${G}"/libgstqsv.so* "${G}"/libgstspandsp.so* "${G}"/libgstspeex.so* "${G}"/libgstsvtav1.so* \
          "${G}"/libgstteletext.so* "${G}"/libgsttheora.so* "${G}"/libgsttwolame.so* "${G}"/libgstuvch264.so* \
          "${G}"/libgstvoaacenc.so* "${G}"/libgstvoamrwbenc.so* "${G}"/libgstvorbis.so* "${G}"/libgstvpx.so* \
          "${G}"/libgstwavpack.so* "${G}"/libgstwebp.so* "${G}"/libgstwildmidi.so* "${G}"/libgstx264.so* \
          "${G}"/libgstx265.so* "${L}"/libFLAC.so* "${L}"/libOpenEXR*.so* "${L}"/libSvtAv1Enc.so* \
          "${L}"/libWildMidi.so* "${L}"/liba52* "${L}"/libaom.so* "${L}"/libavc1394.so* \
          "${L}"/libavcodec* "${L}"/libavfilter* "${L}"/libavformat* "${L}"/libavresample* \
          "${L}"/libavutil* "${L}"/libbs2b.so* "${L}"/libcdda_interface.so* "${L}"/libcdda_paranoia.so* \
          "${L}"/libcdio.so* "${L}"/libchromaprint.so* "${L}"/libdc1394.so* "${L}"/libdca.so* \
          "${L}"/libde265.so* "${L}"/libdv.so* "${L}"/libdvdnav.so* "${L}"/libdvdread.so* \
          "${L}"/libfaad* "${L}"/libflite.so* "${L}"/libfluidsynth.so* "${L}"/libfreeaptx.so* \
          "${L}"/libgme.so* "${L}"/libgsm.so* "${L}"/libgssdp-1.6.so* "${L}"/libiec61883.so* \
          "${L}"/libinstpatch-1.0.so* "${L}"/libjbig.so* "${L}"/libjpeg.so* "${L}"/liblc3.so* \
          "${L}"/liblcms2.so* "${L}"/libldacBT_enc.so* "${L}"/liblrdf.so* "${L}"/libmfx-tracer.so* \
          "${L}"/libmfx.so* "${L}"/libmfxhw64.so* "${L}"/libmjpegutils-2.1.so.0* "${L}"/libmodplug.so* \
          "${L}"/libmp3lame.so* "${L}"/libmpcdec.so* "${L}"/libmpeg2.so* "${L}"/libmpeg2convert.so* \
          "${L}"/libmpeg2encpp-2.1.so* "${L}"/libmpg123.so* "${L}"/libmplex2-2.1.so* "${L}"/libneon.so* \
          "${L}"/libogg.so* "${L}"/libopenh264.so* "${L}"/libopenjp2.so* "${L}"/libopenmpt.so* \
          "${L}"/libopus.so* "${L}"/libpng16.so* "${L}"/libpostproc.* "${L}"/libreadline* \
          "${L}"/librom1394.so* "${L}"/libsbc.so* "${L}"/libsharpyuv.so* "${L}"/libsidplay.so* \
          "${L}"/libspandsp.so* "${L}"/libspeex.so* "${L}"/libswresample.* "${L}"/libswscale.* \
          "${L}"/libtheora*.so* "${L}"/libtiff.so* "${L}"/libtwolame.so* "${L}"/libv4lconvert.so* \
          "${L}"/libvo-aacenc.so* "${L}"/libvo-amrwbenc.so* "${L}"/libvorbis*.so* "${L}"/libvpx.so* \
          "${L}"/libwavpack.so* "${L}"/libwebp.so* "${L}"/libwebpmux.so* "${L}"/libx264.so* \
          "${L}"/libx265.so* "${L}"/libxvidcore.so* "${L}"/libzvbi* "${L}"/mfx/libmfx_*_hw64.so* \
          "${L}"/mfx/libmfx_h264la_hw64.so* \
      || true; \
    rm -rf "${L}/mfx" /usr/share/doc/libreadline8; \
    rm -f "${L}"/libreadline* /usr/share/sounds/sf2/TimGM6mb.sf2

LABEL com.nvidia.mm.nspect="NSPECT-1H82-JYCQ"

# App layer (Dockerfile.app), per arch. x86 wires the deepstream/ prebuilts +
# libnvcuvid/encode symlinks and owns /home/vst as uid 1000. aarch64 wires the
# sbsa/ prebuilts + the nvidia/ symlink set, runs ldconfig, chmods 755, and runs
# as ROOT (the aarch64 original has no USER directive).
COPY --from=builder /stage/home/vst/vst_release /home/vst/vst_release
RUN set -eux; VST="$(cat /tmp/vst_arch)"; DEB="$(cat /tmp/deb_arch)"; L="/usr/lib/${DEB}"; PB="/home/vst/vst_release/prebuilts/${VST}"; \
    if [ "${VST}" = "x86_64" ]; then \
        ln -sf "${PB}/deepstream/libcuvidv4l2_plugin.so" "${L}/libv4l/plugins/libcuvidv4l2_plugin.so"; \
        ln -sf "${PB}/deepstream/libnvbufsurface.so" "${L}/libnvbufsurface.so"; \
        ln -sf "${PB}/deepstream/libnvbufsurftransform.so" "${L}/libnvbufsurftransform.so"; \
        ln -sf "${PB}/deepstream/libgstnvdsseimeta.so" "${L}/libgstnvdsseimeta.so"; \
        ln -sf "${PB}/deepstream/libnvbuf_fdmap.so" "${L}/libnvbuf_fdmap.so"; \
        rm -rf "${L}/libv4l2.so.0.0.0"; mv "${PB}/deepstream/libv4l2.so" "${L}/libv4l2.so.0.0.0"; \
        ln -sf "${L}/libnvcuvid.so.1" "${L}/libnvcuvid.so"; \
        ln -sf "${L}/libnvidia-encode.so.1" "${L}/libnvidia-encode.so"; \
        chown -R 1000:1000 /home/vst; \
    else \
        chmod -R 755 /home/vst/vst_release; \
        mkdir -p "${L}/libv4l/plugins/nv"; \
        ln -sf "${PB}/sbsa/libnvv4l2.so" "${L}/libv4l2.so.0.0.999999"; \
        ln -sf "${L}/libv4l2.so.0.0.999999" "${L}/libv4l2.so.0"; \
        ln -sf "${L}/libv4l2.so.0" "${L}/libv4l2.so"; \
        ln -sf "${PB}/sbsa/libv4l2_nvcuvidvideocodec.so" "${L}/libv4l/plugins/nv/libv4l2_nvcuvidvideocodec.so"; \
        mkdir -p "${L}/nvidia"; \
        ln -sf "${PB}/sbsa/libnvbufsurface.so.1.0.0" "${L}/nvidia/libnvbufsurface.so"; \
        ln -sf "${PB}/sbsa/libnvbufsurftransform.so.1.0.0" "${L}/nvidia/libnvbufsurftransform.so"; \
        ln -sf "${PB}/sbsa/libnvmm_jpeg.so" "${L}/nvidia/libnvmm_jpeg.so"; \
        ldconfig; \
    fi

ENV CUDA_CACHE_DISABLE=0
WORKDIR /home/vst/vst_release
ENTRYPOINT ["/home/vst/vst_release/launch_vst"]

# ---- per-arch ENV / USER (differ between the x86 and aarch64 originals) ----
# x86: Dockerfile.base:50 (driver caps) + Dockerfile.app:47,49-50 (USER, paths).
FROM runtime-base AS runtime-amd64
ENV NVIDIA_DRIVER_CAPABILITIES=$NVIDIA_DRIVER_CAPABILITIES,video,compute,graphics,utility
ENV LD_LIBRARY_PATH=/home/vst/vst_release/prebuilts/x86_64/deepstream/:/home/vst/vst_release/prebuilts/x86_64/gst-plugins/:/home/vst/vst_release/prebuilts/x86_64/:/home/vst/vst_release/prebuilts/x86_64/aws
ENV GST_PLUGIN_PATH=/home/vst/vst_release/prebuilts/x86_64/deepstream/gst-plugins:/home/vst/vst_release/prebuilts/x86_64/gst-plugins
USER 1000:1000

# aarch64: Dockerfile.base:54,63 (driver caps=video, DISABLE_REQUIRE) +
# Dockerfile.app:49-51 (LD_PRELOAD, paths). Original had NO USER -> runs as root.
FROM runtime-base AS runtime-arm64
ENV NVIDIA_DRIVER_CAPABILITIES=$NVIDIA_DRIVER_CAPABILITIES,video
ENV NVIDIA_DISABLE_REQUIRE=1
ENV LD_PRELOAD=/usr/lib/aarch64-linux-gnu/libgomp.so.1:/lib/aarch64-linux-gnu/libGLdispatch.so.0
ENV LD_LIBRARY_PATH=/usr/lib/aarch64-linux-gnu/:/usr/lib/aarch64-linux-gnu/nvidia/:/home/vst/vst_release/prebuilts/aarch64/:/home/vst/vst_release/prebuilts/aarch64/gst-plugins/:/home/vst/vst_release/prebuilts/aarch64/sbsa/
ENV GST_PLUGIN_PATH=/home/vst/vst_release/prebuilts/aarch64/gst-plugins/:/home/vst/vst_release/prebuilts/aarch64/deepstream/gst-plugins/

# Select the arch-appropriate runtime as the final image.
ARG TARGETARCH
FROM runtime-${TARGETARCH} AS runtime
