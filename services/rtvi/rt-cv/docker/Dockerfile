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
# vss-rt-cv — single Dockerfile for linux/amd64 and linux/arm64.
#
# Merged from the previously separate x86 and aarch64 files. buildx supplies
# TARGETARCH (amd64|arm64) per platform, so one file covers both. Three kinds of
# difference existed between them:
#
#   1. Pure token substitution -> ${TARGETARCH}
#        apt qualifiers    :amd64      / :arm64
#        gdino prebuilt    _amd64.so   / _arm64.so
#      The apt-remove package lists were otherwise byte-identical.
#
#   2. Different spelling for the same arch -> mapped in one place
#        prebuilts dir     x86         / aarch64      (NOT amd64/arm64)
#        library triple    x86_64-…    / aarch64-…    (avoided via /usr/lib/*-linux-gnu/)
#
#   3. Genuinely different behaviour -> explicit `case "$TARGETARCH"` branches
#        torch:        amd64 nightly + --force-reinstall; arm64 pinned 2.9.1
#        onnxruntime:  amd64 onnxruntime-gpu;             arm64 onnxruntime (CPU)
#        open-clip:    amd64 with deps;                   arm64 --no-deps
#        Intel MFX removal: amd64 only (does not exist on arm64)
#
# Layer order follows the aarch64 file: install_dependencies.sh runs before the
# pip layers so that ~50 min layer stays cached when only app or pip deps change.
#
# EVERY cache mount is scoped `id=...-${TARGETARCH}` and `sharing=locked`. Both
# platforms build CONCURRENTLY in one buildx invocation, and a cache mount
# defaults to sharing=shared -- so amd64 and arm64 would land in the same
# /var/lib/apt/lists and fight over apt's lock ("Could not get lock ... held by
# process 0", apt exit 100). Worse, that directory holds arch-specific package
# indices (archive.ubuntu.com vs ports.ubuntu.com), so sharing it would mix
# metadata between architectures. Same reasoning for the pip and HF caches.
#
# NOTE the prebuilts tree also ships an `sbsa` directory. SBSA is linux/arm64,
# the SAME TARGETARCH as Jetson, so it cannot be selected by a branch here — it
# needs its own image. See deploy/docker/container-inventory.json.
# -----------------------------------------------------------------------------
ARG BASE_IMAGE="nvcr.io/nvidia/deepstream:rtvi_ds9.1plus-triton"
ARG DS_VERSION=9.1

# =============================================================================
# Stage 1 — build the perception binary
# =============================================================================
FROM ${BASE_IMAGE} AS ds-devel
ARG DS_VERSION
ARG REGISTRY
ARG TAG
ARG TARGETARCH

# Precompiled libs. The whole prebuilts tree is copied and the arch picked at
# RUN time: the directory names are x86/aarch64, which do NOT match TARGETARCH
# (amd64/arm64), so a plain ${TARGETARCH} path would silently miss.
ADD prebuilts/ /tmp/prebuilts/
RUN set -eux; \
    case "$TARGETARCH" in \
      amd64) PREBUILT_DIR=x86 ;; \
      arm64) PREBUILT_DIR=aarch64 ;; \
      *) echo "unsupported TARGETARCH=$TARGETARCH" >&2; exit 1 ;; \
    esac; \
    cp "/tmp/prebuilts/${PREBUILT_DIR}/libmsda_fp16.so" \
       /opt/nvidia/deepstream/deepstream/sources/sparse4d/libmsda_fp16.so; \
    cp "/tmp/prebuilts/${PREBUILT_DIR}/libnvds_infercustomparser_tao.so" \
       /opt/nvidia/deepstream/deepstream/lib/libnvds_infercustomparser_tao.so; \
    rm -rf /tmp/prebuilts; \
    ldconfig

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
# These prebuilt names DO use amd64/arm64, so TARGETARCH substitutes directly.
RUN mv /opt/nvidia/deepstream/deepstream/sources/TritonGdino/prebuilts/libnvdstriton_custom_impl_gdino_${TARGETARCH}.so /opt/nvidia/deepstream/deepstream/sources/TritonGdino/prebuilts/libnvdstriton_custom_impl_gdino.so && \
    mv /opt/nvidia/deepstream/deepstream/sources/TritonMaskGdino/prebuilts/libnvdstriton_custom_impl_mgdino_${TARGETARCH}.so /opt/nvidia/deepstream/deepstream/sources/TritonMaskGdino/prebuilts/libnvdstriton_custom_impl_mgdino.so

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
ARG TARGETARCH

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

# Triton model repos, with the arch-correct GDINO plugin already renamed
COPY --from=ds-devel /opt/nvidia/deepstream/deepstream/sources/TritonGdino/ /opt/nvidia/deepstream/deepstream/sources/TritonGdino/
COPY --from=ds-devel /opt/nvidia/deepstream/deepstream/sources/TritonMaskGdino/ /opt/nvidia/deepstream/deepstream/sources/TritonMaskGdino/

# frame_dump sources: shipped for reference/debug, already compiled into the binary
COPY --from=ds-devel /opt/nvidia/deepstream/deepstream/sources/apps/apps-common/src/frame_dump.cpp /opt/nvidia/deepstream/deepstream/sources/apps/apps-common/src/frame_dump.cpp
COPY --from=ds-devel /opt/nvidia/deepstream/deepstream/sources/apps/apps-common/includes/frame_dump.h /opt/nvidia/deepstream/deepstream/sources/apps/apps-common/includes/frame_dump.h

# OCR config the sparse4d path reads at runtime
COPY --from=ds-devel /opt/nvidia/deepstream/deepstream/sources/sparse4d/configs/config_ocr.yaml /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/config_ocr.yaml

# Prebuilt libs + ldconfig so the loader can resolve them at runtime
COPY --from=ds-devel /opt/nvidia/deepstream/deepstream/sources/sparse4d/libmsda_fp16.so /opt/nvidia/deepstream/deepstream/sources/sparse4d/libmsda_fp16.so
COPY --from=ds-devel /opt/nvidia/deepstream/deepstream/lib/libnvds_infercustomparser_tao.so /opt/nvidia/deepstream/deepstream/lib/libnvds_infercustomparser_tao.so
RUN ldconfig

# Third-party licence text, at the path the compliance scan expects
COPY --from=ds-devel /opt/mm/LICENSE.3rdparty /opt/mm/LICENSE.3rdparty

# -- runtime dependencies -------------------------------------------------
# Ordering here is deliberate and load-bearing for build time: slowest and most
# stable first, so an app-only change never re-runs them. Do not merge these
# RUNs -- each split exists to keep a layer cached.
#
# 1/4  sparse4d system deps (~50 min). Slowest layer, changes almost never.
RUN --mount=type=cache,target=/var/cache/apt/archives,sharing=locked,id=apt-archives-${TARGETARCH} \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked,id=apt-lists-${TARGETARCH} \
    /opt/nvidia/deepstream/deepstream/sources/sparse4d/configs/install_dependencies.sh

# 2/4  apt runtime deps: GStreamer python plugin loader + netcat (healthchecks)
RUN --mount=type=cache,target=/var/cache/apt/archives,sharing=locked,id=apt-archives-${TARGETARCH} \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked,id=apt-lists-${TARGETARCH} \
    apt-get clean && apt-get update && \
    apt-get install -y gstreamer1.0-python3-plugin-loader && \
    apt-get install -y --no-install-recommends netcat-openbsd && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# 3/4  PyTorch. Own layer because it is large and changes rarely; keeping it
#      separate means a change to the small deps below does not refetch it.
#      amd64 tracks the cu130 nightly and force-reinstalls over the base
#      image's preinstalled torch; arm64 pins the released build.
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked,id=pip-${TARGETARCH} \
    set -eux; \
    if [ "$TARGETARCH" = "arm64" ]; then \
      pip3 install --default-timeout=100 --retries 20 \
        torch==2.9.1 torchvision==0.24.1 --index-url https://download.pytorch.org/whl/cu130; \
    else \
      pip3 install --default-timeout=100 --retries 20 --pre torch torchvision \
        --index-url https://download.pytorch.org/whl/nightly/cu130 --force-reinstall; \
    fi

# 4/4  Remaining pip deps (small, change most often -> last).
#      onnxruntime-gpu has no arm64 wheel, so arm64 gets the CPU build.
#      open-clip-torch is --no-deps on arm64 so pip cannot pull a torch that
#      conflicts with the pinned one above.
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked,id=pip-${TARGETARCH} \
    set -eux; \
    pip3 install --default-timeout=100 --retries 20 kafka-python psutil transformers==4.57.6 setuptools==78.1.1 numpy==1.26.4; \
    if [ "$TARGETARCH" = "arm64" ]; then \
      pip3 install --default-timeout=100 --retries 20 sentencepiece onnx onnxruntime pillow==12.2.0; \
      pip3 install --default-timeout=100 --retries 20 --no-deps open-clip-torch; \
    else \
      pip3 install --default-timeout=100 --retries 20 open-clip-torch sentencepiece onnx onnxruntime-gpu pillow==12.2.0; \
    fi; \
    pip3 install --default-timeout=100 --retries 20 huggingface_hub==0.36.2; \
    pip3 install --default-timeout=100 --retries 20 --ignore-installed wheel==0.46.2

# -- offline model assets -------------------------------------------------
# Bake the bert-base-uncased tokenizer in so the container never reaches the
# network at runtime. Only tokenizer/config JSON is fetched -- weights are
# excluded, which is why this stays small.
ENV HF_HOME=/opt/huggingface
RUN --mount=type=cache,target=/tmp/hf_cache,sharing=locked,id=hf-${TARGETARCH} \
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

WORKDIR /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/
# Remove patent-encumbered codecs. Body lives in strip-patented-codecs.sh --
# it was a 94-line RUN layer here (30% of this file). Still ONE layer, so the
# removals actually shrink the image rather than masking files from a lower one.
COPY docker/strip-patented-codecs.sh /tmp/strip-patented-codecs.sh
RUN --mount=type=cache,target=/var/cache/apt/archives,sharing=locked,id=apt-archives-${TARGETARCH} \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked,id=apt-lists-${TARGETARCH} \
    /tmp/strip-patented-codecs.sh && rm -f /tmp/strip-patented-codecs.sh

# -- final runtime configuration ------------------------------------------
# Drop privileges. Numeric form (not `nvidia`) so it still resolves if the
# passwd entry is ever absent.
USER 1000:1000

# UTF-8 everywhere: DeepStream config parsing trips on the default C locale.
ENV PYTHONIOENCODING=utf-8
ENV LC_ALL=C.UTF-8
