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

# -----------------------------------------------------------------------------
# GHCR build entrypoint for vss-rt-cv (GitHub-First; see .github/github-first.md).
#
# WORKAROUND — read before changing the base.
#
# The sibling x86/aarch64/sbsa Dockerfiles layer this app onto
# nvcr.io/nvstaging/vss-core/vss-rt-cv, i.e. they rebuild vss-rt-cv *from
# vss-rt-cv*. That base is not usable here: every nvcr.io/nvidia/vss-core and
# nvstaging/vss-core repository reports canGuestPull=false, so it needs NGC
# credentials, and by design no NGC credentials exist in GitHub Actions.
#
# So this file builds against the stock public DeepStream SDK instead, which is
# anonymously pullable (canGuestPull=true) and multi-arch. Everything
# src/Makefile reaches for outside this folder -- ../deepstream-app/*.c,
# ../../apps-common/src, deepstream-yaml, sources/libs/nvds_msgapi_common_src,
# sources/includes -- ships in the SDK image, as do gcc/g++/make, the four
# pkg-config deps (gstreamer-1.0, gstreamer-video-1.0, x11, json-glib-1.0) and
# yaml-cpp / gstrtspserver / crypto.
#
# CONSEQUENCE, and the reason this is a workaround rather than the fix: the
# private base also carries the models, TRT engines, tracker configs and
# ds-start.sh that make the container a working perception service. This image
# has the compiled binary and none of those. It is a valid *build* artifact --
# enough to put vss-rt-cv on the shared GHCR coordinate and exercise the
# build/tag/release-set flow -- but it is NOT yet a drop-in runtime replacement
# for the NGC image. Closing that gap means either vendoring those assets or
# fetching them at deploy time.
#
# Pin note: 9.1 is not incidental. src/Makefile hardcodes NVDS_VERSION:=9.1 for
# LIB_INSTALL_DIR/APP_INSTALL_DIR, and this image supplies CUDA 13.2, which is
# what the sibling Dockerfiles set CUDA_VER to. Moving the base off 9.1 without
# updating the Makefile silently installs into a directory that does not exist.
# -----------------------------------------------------------------------------
ARG BASE_IMAGE="nvcr.io/nvidia/deepstream:9.1-triton-multiarch"
FROM ${BASE_IMAGE}

ARG APP_DIR=/opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app

# The private base ships this folder already; the stock SDK does not.
RUN mkdir -p ${APP_DIR}

# Copy sources
COPY src/metropolis_perception_app.c ${APP_DIR}/metropolis_perception_app.c
COPY src/perception_utc.c ${APP_DIR}/perception_utc.c
COPY src/metropolis_perception_app.h ${APP_DIR}/metropolis_perception_app.h
COPY src/Makefile ${APP_DIR}/Makefile
COPY tests/ ${APP_DIR}/tests/

ENV CUDA_VER=13.2
WORKDIR ${APP_DIR}

# Build binary
RUN make
USER root:root
RUN make install

WORKDIR ${APP_DIR}

# Switch to this non-root user
USER 1000:1000
