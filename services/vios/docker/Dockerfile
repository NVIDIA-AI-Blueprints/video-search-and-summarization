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

# Toolchain packages (from Dockerfile.devel). No-op if TOOLCHAIN_IMAGE already
# has them (a prebuilt toolchain), so this stays valid for both paths.
RUN apt-get update && apt-get install -y --no-install-recommends \
        g++ lbzip2 libcurl4-openssl-dev libcurl3-gnutls libgtest-dev libjsoncpp-dev \
        libssl-dev libxml2-dev libxml2 sqlite3 libsqlite3-dev uuid uuid-dev \
        iputils-ping gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
        gstreamer1.0-plugins-ugly gstreamer1.0-plugins-bad libgstreamer1.0-0 \
        libgstreamer-plugins-base1.0-dev libboost-all-dev make build-essential \
        autoconf libtool git pkg-config wget librdkafka-dev libopencv-dev python3 \
        python3-pip npm ca-certificates gnupg lsb-release ntp ntpstat \
        libhiredis1.1.0 libldap2-dev libpq-dev libpqxx-dev libpaho-mqttpp-dev \
        libpaho-mqtt-dev \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
RUN pip3 install --break-system-packages requests

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
    MODULE="${MK_MODULE}" make package

# -----------------------------------------------------------------------------
# runtime -- base (Dockerfile.base) + app packaging (Dockerfile.app)
# -----------------------------------------------------------------------------
FROM nvcr.io/nvidia/cuda:${CUDA_VERSION}-runtime-${UBUNTU_VERSION} AS runtime
ARG TARGETARCH
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        bzip2 \
        libcurl4 libxml2 libssl-dev libjsoncpp-dev sqlite3 uuid \
        gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-ugly \
        gstreamer1.0-plugins-bad gstreamer1.0-libav libgstreamer1.0-0 \
        libgstreamer-plugins-base1.0 libboost-filesystem-dev libboost-regex-dev \
        libboost-thread-dev libyaml-cpp-dev gstreamer1.0-tools libjansson-dev \
        librdkafka-dev libcogl-pango-dev libopencv-dev libpq-dev libpqxx-dev \
        libhiredis1.1.0 libpaho-mqttpp-dev \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Resolve arch-specific paths once: DEB = multiarch triplet, VST = prebuilts dir.
RUN set -eux; case "${TARGETARCH}" in \
        amd64) echo x86_64-linux-gnu > /tmp/deb_arch; echo x86_64 > /tmp/vst_arch;; \
        arm64) echo aarch64-linux-gnu > /tmp/deb_arch; echo aarch64 > /tmp/vst_arch;; \
        *) echo "unsupported arch ${TARGETARCH}" >&2; exit 1;; esac

ENV NVIDIA_DRIVER_CAPABILITIES=$NVIDIA_DRIVER_CAPABILITIES,video,compute,graphics,utility
RUN set -eux; DEB="$(cat /tmp/deb_arch)"; \
    ln -sf "/usr/lib/${DEB}/libnvcuvid.so.1" "/usr/lib/${DEB}/libnvcuvid.so"; \
    ln -sf "/usr/lib/${DEB}/libnvidia-encode.so.1" "/usr/lib/${DEB}/libnvidia-encode.so"

# OSRB: strip GPL / patent-encumbered codec, FFmpeg, and Intel MediaSDK libs
# (transcribed from Dockerfile.base; `|| true` since a lib may be absent per arch).
RUN set -eux; DEB="$(cat /tmp/deb_arch)"; L="/usr/lib/${DEB}"; G="${L}/gstreamer-1.0"; \
    rm -f "${G}"/libgstaudioparsers.so* "${G}"/libgstx264.so* "${G}"/libgstfaad.so* "${G}"/libgstvoaacenc.so* \
          "${L}"/libavresample* "${L}"/libavutil* "${L}"/libavcodec* "${L}"/libavformat* "${L}"/libavfilter* \
          "${G}"/libgstde265.so* "${G}"/libgstx265.so* "${L}"/libde265.so* "${G}"/libgstvpx.so* \
          "${L}"/libmpeg2.so* "${L}"/libmpeg2encpp-2.1.so* "${L}"/libmpg123.so* "${L}"/libx265.so* "${L}"/libx264.so* \
          "${L}"/libvpx.so* "${L}"/libmpeg2convert.so* "${G}"/libgstopenh264.so* "${G}"/libgstnvcodec.so* \
          "${G}"/libgstuvch264.so* "${L}"/libopenh264.so* "${L}"/libvo-aacenc.so* "${L}"/libmp3lame.so* "${L}"/libfaad* \
          "${L}"/libFLAC.so* "${L}"/libmjpegutils-2.1.so.0* "${L}"/libxvidcore.so* "${G}"/libgstmpegpsmux.so* \
          "${G}"/libgstflac.so* "${G}"/libgstmpeg2enc.so* "${G}"/libgstmpeg2dec.so* \
          "${L}"/libmfx.so* "${L}"/libmfxhw64.so* "${L}"/libmfx-tracer.so* "${G}"/libgstmsdk.so* "${G}"/libgstqsv.so* \
          "${L}"/libswscale.* "${L}"/libswresample.* "${L}"/libpostproc.* \
          "${L}"/liba52* "${L}"/libcdio.so* "${L}"/libsidplay.so* "${L}"/liblrdf.so* "${L}"/libneon.so* "${L}"/libbs2b.so* \
          "${L}"/libdca.so* "${L}"/libdvdnav.so* "${L}"/libdvdread.so* "${L}"/libmplex2-2.1.so* "${L}"/libsbc.so* "${L}"/libzvbi* \
          "${L}"/mfx/libmfx_*_hw64.so* || true; \
    rm -rf "${L}/mfx" /usr/share/doc/libreadline8; rm -f "${L}"/libreadline*

LABEL com.nvidia.mm.nspect="NSPECT-1H82-JYCQ"

# App layer (Dockerfile.app): unpack the compiled release and wire prebuilt libs.
RUN useradd -ms /bin/bash vst
COPY --from=builder /root/out/*/vst_release.tbz2 /tmp/vst_release.tbz2
RUN set -eux; VST="$(cat /tmp/vst_arch)"; DEB="$(cat /tmp/deb_arch)"; PB="/home/vst/vst_release/prebuilts/${VST}"; \
    tar -xjf /tmp/vst_release.tbz2 -C /home/vst; rm -f /tmp/vst_release.tbz2; \
    if [ "${VST}" = "x86_64" ]; then \
        ln -sf "${PB}/deepstream/libcuvidv4l2_plugin.so" "/usr/lib/${DEB}/libv4l/plugins/libcuvidv4l2_plugin.so"; \
        ln -sf "${PB}/deepstream/libnvbufsurface.so" "/usr/lib/${DEB}/libnvbufsurface.so"; \
        ln -sf "${PB}/deepstream/libnvbufsurftransform.so" "/usr/lib/${DEB}/libnvbufsurftransform.so"; \
        ln -sf "${PB}/deepstream/libgstnvdsseimeta.so" "/usr/lib/${DEB}/libgstnvdsseimeta.so"; \
        ln -sf "${PB}/deepstream/libnvbuf_fdmap.so" "/usr/lib/${DEB}/libnvbuf_fdmap.so"; \
        rm -rf "/usr/lib/${DEB}/libv4l2.so.0.0.0"; mv "${PB}/deepstream/libv4l2.so" "/usr/lib/${DEB}/libv4l2.so.0.0.0"; \
    else \
        mkdir -p "/usr/lib/${DEB}/libv4l/plugins/nv"; \
        ln -sf "${PB}/sbsa/libnvv4l2.so" "/usr/lib/${DEB}/libv4l2.so.0.0.999999"; \
        ln -sf "/usr/lib/${DEB}/libv4l2.so.0.0.999999" "/usr/lib/${DEB}/libv4l2.so.0"; \
        ln -sf "/usr/lib/${DEB}/libv4l2.so.0" "/usr/lib/${DEB}/libv4l2.so"; \
        ln -sf "${PB}/sbsa/libv4l2_nvcuvidvideocodec.so" "/usr/lib/${DEB}/libv4l/plugins/nv/libv4l2_nvcuvidvideocodec.so"; \
    fi; \
    chown -R 1000:1000 /home/vst

# Both arch prebuilt dirs on the paths; only the present one resolves.
ENV LD_LIBRARY_PATH=/home/vst/vst_release/prebuilts/x86_64/deepstream/:/home/vst/vst_release/prebuilts/x86_64/gst-plugins/:/home/vst/vst_release/prebuilts/x86_64/:/home/vst/vst_release/prebuilts/x86_64/aws:/home/vst/vst_release/prebuilts/aarch64/deepstream/:/home/vst/vst_release/prebuilts/aarch64/gst-plugins/:/home/vst/vst_release/prebuilts/aarch64/:/home/vst/vst_release/prebuilts/aarch64/sbsa
ENV GST_PLUGIN_PATH=/home/vst/vst_release/prebuilts/x86_64/deepstream/gst-plugins:/home/vst/vst_release/prebuilts/x86_64/gst-plugins:/home/vst/vst_release/prebuilts/aarch64/deepstream/gst-plugins:/home/vst/vst_release/prebuilts/aarch64/gst-plugins
ENV CUDA_CACHE_DISABLE=0

WORKDIR /home/vst/vst_release
USER 1000:1000
ENTRYPOINT ["/home/vst/vst_release/launch_vst"]
