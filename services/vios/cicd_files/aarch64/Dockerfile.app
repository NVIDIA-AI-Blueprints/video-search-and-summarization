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

# Base image to build on top of.
# build.sh always passes this in for you: it builds a base locally, reuses an
# existing one, or uses a registry image you point it at (base-tag= /
# image-registry=). One tag works for both x86_64 and arm64 (if it is a multi-arch image).
ARG BASE_IMAGE=vios/vst-base:2.1.0-runtime-26.05.4
FROM ${BASE_IMAGE}

ARG PKG_LOCATION

# Add the package to /home/vst
RUN mkdir -p /home/vst
ADD ${PKG_LOCATION}/vst_release.tbz2 /home/vst

# Ensure all users can read and execute files in /home/vst/vst_release
RUN chmod -R 755 /home/vst/vst_release

# Unified aarch64 deployment entrypoint. The Tegra multimedia libs
# (libnvbufsurface / libnvbufsurftransform / libnvmm_jpeg / libnvv4l2) are
# needed by all aarch64 targets, but come from different sources:
#   - Jetson/Orin: nvidia-container-runtime injects the real Tegra libs into
#     /usr/lib/aarch64-linux-gnu/nvidia/ at container start.
#   - Discrete-GPU aarch64 (Thor / SBSA / DGX-Spark): not provided by the
#     driver, so they are symlinked from the sbsa prebuilts.
# Creating the sbsa symlinks at build time would SHADOW the device-injected
# Tegra libs on Jetson and break runtime platform detection (isJetsonPlatform()
# dlopens /usr/lib/aarch64-linux-gnu/nvidia/libnvbufsurface.so). So the symlinks
# are created at entrypoint time, and only on discrete GPUs — detected by the
# absence of the device-injected versioned Tegra libnvbufsurface.
RUN cat > /usr/local/bin/vst_entrypoint.sh <<'SH' && chmod +x /usr/local/bin/vst_entrypoint.sh
#!/bin/bash
set -e
NVDIR=/usr/lib/aarch64-linux-gnu/nvidia
V4LDIR=/usr/lib/aarch64-linux-gnu
PB=/home/vst/vst_release/prebuilts/aarch64/sbsa

# Present only on Jetson/Orin (injected from the device); absent on discrete.
if [ ! -e "$NVDIR/libnvbufsurface.so.1.0.0" ]; then
    echo "[vst_entrypoint] discrete-GPU aarch64: linking Tegra libs from sbsa prebuilts"
    mkdir -p "$NVDIR" "$V4LDIR/libv4l/plugins/nv"
    ln -sf "$PB/libnvv4l2.so"                   "$V4LDIR/libv4l2.so.0.0.999999"
    ln -sf "$V4LDIR/libv4l2.so.0.0.999999"      "$V4LDIR/libv4l2.so.0"
    ln -sf "$V4LDIR/libv4l2.so.0"               "$V4LDIR/libv4l2.so"
    ln -sf "$PB/libv4l2_nvcuvidvideocodec.so"   "$V4LDIR/libv4l/plugins/nv/libv4l2_nvcuvidvideocodec.so"
    ln -sf "$PB/libnvbufsurface.so.1.0.0"       "$NVDIR/libnvbufsurface.so"
    ln -sf "$PB/libnvbufsurftransform.so.1.0.0" "$NVDIR/libnvbufsurftransform.so"
    ln -sf "$PB/libnvmm_jpeg.so"                "$NVDIR/libnvmm_jpeg.so"
    ldconfig 2>/dev/null || true
else
    echo "[vst_entrypoint] Jetson/Orin: using device-injected Tegra libs"
fi

exec /home/vst/vst_release/launch_vst "$@"
SH

WORKDIR /home/vst/vst_release
ENV LD_PRELOAD=/usr/lib/aarch64-linux-gnu/libgomp.so.1:/lib/aarch64-linux-gnu/libGLdispatch.so.0
ENV LD_LIBRARY_PATH=/usr/lib/aarch64-linux-gnu/:/usr/lib/aarch64-linux-gnu/nvidia/:/home/vst/vst_release/prebuilts/aarch64/:/home/vst/vst_release/prebuilts/aarch64/gst-plugins/:/home/vst/vst_release/prebuilts/aarch64/sbsa/
ENV GST_PLUGIN_PATH=/home/vst/vst_release/prebuilts/aarch64/gst-plugins/:/home/vst/vst_release/prebuilts/aarch64/deepstream/gst-plugins/
ENV CUDA_CACHE_DISABLE=0

ENTRYPOINT  ["/usr/local/bin/vst_entrypoint.sh"]
