# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

# syntax=docker/dockerfile:1
# -----------------------------------------------------------------------------
# vss-rt-cv — SBSA (Grace/DGX-Spark aarch64) image.
#
# WHY A SEPARATE FILE, and not another branch in docker/Dockerfile:
#   SBSA is linux/arm64 — the SAME TARGETARCH buildx reports for Jetson. buildx
#   offers no way to tell the two apart, so the multiarch file cannot select
#   between them. They also need different base images. Hence two files, one
#   image each, published under the same repo with an `-sbsa` tag suffix.
#
# Everything except the four points below is deliberately identical to
# docker/Dockerfile; keep them in sync when either changes.
#
#   1. Base image      — the SBSA DeepStream build, not the multiarch one.
#   2. prebuilts/sbsa  — SBSA-specific libmsda_fp16 / infercustomparser.
#                        The GDINO plugins are NOT SBSA-specific: SBSA reuses
#                        the _arm64 ones, matching ds9.1's sbsa.Dockerfile.
#   3. pip set         — open-clip-torch WITH deps (Jetson arm64 uses --no-deps)
#                        and CPU onnxruntime (no onnxruntime-gpu arm64 wheel).
#   4. nvidia-dali     — uninstalled after install_dependencies.sh, which pulls
#                        it in. SBSA only.
#
# LAYER ORDER IS COPIED VERBATIM FROM ds9.1 sbsa.Dockerfile AND IS LOAD-BEARING.
# The multiarch file hoists install_dependencies.sh to the front so that ~50 min
# layer stays cached. That reordering is NOT applied here: on SBSA the dali
# uninstall must follow install_dependencies.sh, and the interaction between
# that script and the pip set above it has not been verified under reordering.
# Layers are split for cacheability, which preserves execution order; the
# sequence itself is untouched.
# -----------------------------------------------------------------------------
ARG BASE_IMAGE="nvcr.io/nvidia/deepstream:rtvi_ds9.1plus-sbsa"
ARG DS_VERSION=9.1

# =============================================================================
# Stage 1 — build the perception binary
# =============================================================================
FROM ${BASE_IMAGE} AS ds-devel
ARG DS_VERSION
ARG REGISTRY
ARG TAG

# Precompiled libs. Unlike the multiarch file there is no arch to resolve here:
# SBSA always takes prebuilts/sbsa.
ADD prebuilts/sbsa/libmsda_fp16.so /opt/nvidia/deepstream/deepstream/sources/sparse4d/libmsda_fp16.so
ADD prebuilts/sbsa/libnvds_infercustomparser_tao.so /opt/nvidia/deepstream/deepstream/lib/libnvds_infercustomparser_tao.so
RUN ldconfig

# Copy sources
# models/ intentionally NOT vendored: it is empty upstream. Runtime models
# arrive via each profile's models-download.json (see download-models.sh).
ADD src/metropolis_perception_app.c /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/metropolis_perception_app.c
ADD src/perception_utc.c /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/perception_utc.c
ADD src/metropolis_perception_app.h /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/metropolis_perception_app.h
ADD src/Makefile /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/Makefile

ADD src/frame_dump.cpp /opt/nvidia/deepstream/deepstream/sources/apps/apps-common/src/frame_dump.cpp
ADD src/frame_dump.h /opt/nvidia/deepstream/deepstream/sources/apps/apps-common/includes/frame_dump.h

# Copy reference configs
ADD reference-configs /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/reference-configs

ADD TritonGdino/ /opt/nvidia/deepstream/deepstream/sources/TritonGdino/
ADD TritonMaskGdino/ /opt/nvidia/deepstream/deepstream/sources/TritonMaskGdino/
# SBSA reuses the arm64 GDINO plugins — there is no _sbsa variant.
RUN mv /opt/nvidia/deepstream/deepstream/sources/TritonGdino/prebuilts/libnvdstriton_custom_impl_gdino_arm64.so /opt/nvidia/deepstream/deepstream/sources/TritonGdino/prebuilts/libnvdstriton_custom_impl_gdino.so && \
    mv /opt/nvidia/deepstream/deepstream/sources/TritonMaskGdino/prebuilts/libnvdstriton_custom_impl_mgdino_arm64.so /opt/nvidia/deepstream/deepstream/sources/TritonMaskGdino/prebuilts/libnvdstriton_custom_impl_mgdino.so

# Copy license files for compliance
COPY 3rdParty_Licenses.md /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/3rdParty_Licenses.md
COPY NOTICE.txt /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/NOTICE.txt
COPY 3rdParty_Licenses.md /opt/mm/LICENSE.3rdparty

# -- build ---------------------------------------------------------------
# CUDA_VER: the Makefile builds -I/usr/local/cuda-$(CUDA_VER)/include, so this
#   must match the CUDA in the base image (9.1-triton ships 13.2).
# DS_VERSION: the Makefile derives LIB_INSTALL_DIR/APP_INSTALL_DIR from it and
#   errors out if neither it nor NVDS_VERSION is set.
# REGISTRY/TAG: baked into the binary so it can print its own provenance.
ENV CUDA_VER=13.2
ENV DS_VERSION=${DS_VERSION}
ENV REGISTRY=${REGISTRY}
ENV TAG=${TAG}
WORKDIR "/opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app"
RUN make && make install

# =============================================================================
# Stage 2 — runtime image
# =============================================================================
FROM ${BASE_IMAGE} AS ds-iot
ARG DS_VERSION
ARG REGISTRY
ARG TAG

# Exposed at runtime so the printed image registry/tag can be updated without
# recompiling the app (the binary reads REGISTRY/TAG env vars, falling back to
# the build-time defaults baked into it).
ENV REGISTRY=${REGISTRY}
ENV TAG=${TAG}

ENV LD_LIBRARY_PATH=/usr/lib64:${LD_LIBRARY_PATH}

LABEL com.nvidia.mm.nspect=NSPECT-L6GX-URGA

# -- artefacts from the build stage ---------------------------------------
# Only outputs are carried over; the toolchain stays behind in ds-devel.
# app tree + the installed binary on PATH
COPY --from=ds-devel /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/ /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/
COPY --from=ds-devel /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/metropolis_perception_app /opt/nvidia/deepstream/deepstream/bin/metropolis_perception_app

# frame_dump sources: shipped for reference/debug, already compiled into the binary
COPY --from=ds-devel /opt/nvidia/deepstream/deepstream/sources/apps/apps-common/src/frame_dump.cpp /opt/nvidia/deepstream/deepstream/sources/apps/apps-common/src/frame_dump.cpp
COPY --from=ds-devel /opt/nvidia/deepstream/deepstream/sources/apps/apps-common/includes/frame_dump.h /opt/nvidia/deepstream/deepstream/sources/apps/apps-common/includes/frame_dump.h

# Triton model repos, with the arch-correct GDINO plugin already renamed
COPY --from=ds-devel /opt/nvidia/deepstream/deepstream/sources/TritonGdino/ /opt/nvidia/deepstream/deepstream/sources/TritonGdino/
COPY --from=ds-devel /opt/nvidia/deepstream/deepstream/sources/TritonMaskGdino/ /opt/nvidia/deepstream/deepstream/sources/TritonMaskGdino/

# OCR config the sparse4d path reads at runtime
COPY --from=ds-devel /opt/nvidia/deepstream/deepstream/sources/sparse4d/configs/config_ocr.yaml /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/config_ocr.yaml

# Prebuilt libs + ldconfig so the loader can resolve them at runtime
COPY --from=ds-devel /opt/nvidia/deepstream/deepstream/sources/sparse4d/libmsda_fp16.so /opt/nvidia/deepstream/deepstream/sources/sparse4d/libmsda_fp16.so
COPY --from=ds-devel /opt/nvidia/deepstream/deepstream/lib/libnvds_infercustomparser_tao.so /opt/nvidia/deepstream/deepstream/lib/libnvds_infercustomparser_tao.so
RUN ldconfig

# Third-party licence text, at the path the compliance scan expects
COPY --from=ds-devel /opt/mm/LICENSE.3rdparty /opt/mm/LICENSE.3rdparty

# -- runtime dependencies -------------------------------------------------
# ds9.1 ran apt and all five pip invocations as ONE layer. Split into three
# here purely for cacheability -- the order of execution is unchanged.
#
# 1/3  apt runtime deps. NOTE both packages take --no-install-recommends on
#      SBSA; the multiarch file installs the GStreamer loader WITH recommends.
#      That asymmetry is inherited from ds9.1 and is preserved on purpose.
RUN --mount=type=cache,target=/var/cache/apt/archives,sharing=locked,id=apt-archives-sbsa \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked,id=apt-lists-sbsa \
    apt-get clean && apt-get update && \
    apt-get install -y --no-install-recommends netcat-openbsd gstreamer1.0-python3-plugin-loader && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# 2/3  PyTorch. Own layer because it is large and changes rarely. SBSA takes
#      the released cu130 build, same pin as Jetson arm64.
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked,id=pip-sbsa \
    pip3 install --default-timeout=100 --retries 20 \
      torch==2.9.1 torchvision==0.24.1 --index-url https://download.pytorch.org/whl/cu130

# 3/3  Remaining pip deps (small, change most often -> last).
#      open-clip-torch is installed WITH deps here, unlike Jetson arm64.
#      onnxruntime is the CPU build: there is no onnxruntime-gpu arm64 wheel.
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked,id=pip-sbsa \
    set -eux; \
    pip3 install --default-timeout=100 --retries 20 kafka-python psutil transformers==4.57.6 setuptools==78.1.1 numpy==1.26.4; \
    pip3 install --default-timeout=100 --retries 20 open-clip-torch sentencepiece onnx onnxruntime pillow==12.2.0; \
    pip3 install --default-timeout=100 --retries 20 huggingface_hub==0.36.2; \
    pip3 install --default-timeout=100 --retries 20 --ignore-installed wheel==0.46.2

# -- offline model assets -------------------------------------------------
# Bake the bert-base-uncased tokenizer in so the container never reaches the
# network at runtime. Only tokenizer/config JSON is fetched -- weights are
# excluded, which is why this stays small.
ENV HF_HOME=/opt/huggingface
RUN --mount=type=cache,target=/tmp/hf_cache,sharing=locked,id=hf-sbsa \
    mkdir -p /opt/huggingface && \
    HF_HOME=/tmp/hf_cache python3 -c "from huggingface_hub import snapshot_download; \
snapshot_download('bert-base-uncased', \
    allow_patterns=['tokenizer*.json', 'vocab.txt', 'config.json', '*.json'], \
    ignore_patterns=['*.safetensors', '*.bin', '*.pt', '*.pth', '*.h5', '*.onnx'])" && \
    cp -a /tmp/hf_cache/hub /opt/huggingface/ && \
    chmod -R 755 /opt/huggingface

# Force transformers/hub to the baked copy; any download attempt fails fast.
ENV TRANSFORMERS_OFFLINE=1
ENV HF_HUB_OFFLINE=1

# -- image hygiene and the runtime user -----------------------------------
# Drop the GStreamer registry cache (regenerated on first run) and pre-create
# the Tracker dir the ReID model is staged into. One layer, per nvbug #4162927.
RUN rm -rf ~/.cache/gstreamer-1.0 && mkdir -p /opt/nvidia/deepstream/deepstream/samples/models/Tracker && rm -rf /var/lib/apt/lists/*

# `nvidia` is created with -o (non-unique) at uid 1000, which the base image
# already uses for triton-server: the two names share the uid deliberately.
# The chowns cover every path the privilege-dropped process writes to.
ARG uid=1000
ARG gid=1000
RUN groupadd -r -f -g ${gid} nvidia && useradd -o -r -l -u ${uid} -g ${gid} -ms /bin/bash nvidia && \
    mkdir -p /opt/storage /opt/data && \
    chown -R nvidia /opt/storage /opt/nvidia/deepstream/deepstream/sources/ /opt/data /opt/nvidia/deepstream/deepstream/samples/ && \
    chown -R nvidia /opt/huggingface && \
    mkdir -p /home/triton-server/.cache/ && \
    chown -R nvidia /home/triton-server/

# sparse4d system deps (~50 min). Runs late on SBSA -- see the ordering note in
# the header. Do not hoist this above the pip layers without re-verifying.
RUN --mount=type=cache,target=/var/cache/apt/archives,sharing=locked,id=apt-archives-sbsa \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked,id=apt-lists-sbsa \
    /opt/nvidia/deepstream/deepstream/sources/sparse4d/configs/install_dependencies.sh

WORKDIR /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/

# install_dependencies.sh pulls in DALI, which SBSA does not use. Must stay
# after that script or it has nothing to remove.
RUN pip uninstall -y nvidia-dali-cuda130

# Remove patent-encumbered codecs. Shares strip-patented-codecs.sh with the
# multiarch file: the SBSA removal list is byte-identical to the arm64 one
# (56/56 packages), so TARGETARCH=arm64 is passed explicitly -- buildx does not
# set it for a single-platform build driven by an -sbsa tag.
COPY docker/strip-patented-codecs.sh /tmp/strip-patented-codecs.sh
RUN --mount=type=cache,target=/var/cache/apt/archives,sharing=locked,id=apt-archives-sbsa \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked,id=apt-lists-sbsa \
    TARGETARCH=arm64 /tmp/strip-patented-codecs.sh && rm -f /tmp/strip-patented-codecs.sh

# -- final runtime configuration ------------------------------------------
# Drop privileges. Numeric form (not `nvidia`) so it still resolves if the
# passwd entry is ever absent.
USER 1000:1000

# UTF-8 everywhere: DeepStream config parsing trips on the default C locale.
ENV PYTHONIOENCODING=utf-8
ENV LC_ALL=C.UTF-8
