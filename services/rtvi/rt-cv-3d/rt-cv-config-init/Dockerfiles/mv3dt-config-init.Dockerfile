# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
#
# Build with services/rtvi/rt-cv-3d/rt-cv-config-init as the context:
#
#   docker build \
#     -f services/rtvi/rt-cv-3d/rt-cv-config-init/Dockerfiles/mv3dt-config-init.Dockerfile \
#     -t <registry>/vss-rt-cv-mv3dt-config-init:<tag> \
#     services/rtvi/rt-cv-3d/rt-cv-config-init

# Rebuild bump: edit this block to re-trigger the CI image build.
#   [0]: initial GHCR onboarding

ARG PYTHON_VERSION=3.13
ARG DISTROLESS_TAG=${PYTHON_VERSION}-v4.0.5
ARG DISTROLESS_IMG=nvcr.io/nvidia/distroless/python

###################################################
# Builder stage
# Nothing from this stage ships except the directories explicitly COPYed below.
FROM python:${PYTHON_VERSION}-trixie AS builder

ARG TARGETARCH

WORKDIR /build

# `jq` is required by generate_metadata.sh; status.d must exist before the
# distroless final stage can COPY it in.
RUN apt-get update \
 && apt-get install -y --no-install-recommends jq \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /var/lib/dpkg/status.d

# Declare bundled OS-level binaries so the NGC/Anchore scanner can attribute
# them in the distroless image (the scanner reads /var/lib/dpkg/status.d).
# deps.json lists no binaries for this image, so the script's remaining job is
# to create /lib/distroless for the COPY in the final stage.
COPY --chmod=0644 deps.json /build/deps.json
COPY --chmod=0755 docker/generate_metadata.sh /build/generate_metadata.sh
RUN ./generate_metadata.sh deps.json

# GCC runtime libs (libgcc_s, libstdc++) are inherited by the distroless
# image; ship the GCC source for license compliance.
RUN echo "deb-src http://deb.debian.org/debian trixie main" >> /etc/apt/sources.list \
 && echo "deb-src http://deb.debian.org/debian trixie-updates main" >> /etc/apt/sources.list \
 && apt-get update \
 && apt-get install -y dpkg-dev \
 && mkdir -p /usr/share/source \
 && cd /usr/share/source \
 && apt-get source gcc-14 \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

# Source for native code vendored inside the Python wheels we install:
#   - numpy    (C extensions)
# tqdm and PyYAML are either pure Python or ship only a thin optional C
# extension whose source is fully contained in the installed site-packages.
RUN curl -fsSL -o /usr/share/source/numpy-2.2.6.tar.gz https://github.com/numpy/numpy/archive/refs/tags/v2.2.6.tar.gz

RUN curl -fsSL -o /usr/share/source/OpenBLAS-0.3.29.tar.gz https://github.com/OpenMathLib/OpenBLAS/archive/refs/tags/v0.3.29.tar.gz

# Upgrade build-time tools, materialize the pipenv-locked closure into a
# hash-pinned requirements.txt, install it with --no-deps (pipenv has already
# resolved every transitive), then uninstall the build-time tools so they do
# NOT ship in the COPY'd site-packages tree. pip must be uninstalled last.
RUN pip install --upgrade pip pipenv setuptools wheel

COPY --chmod=0644 Pipfile Pipfile.lock /build/
RUN pipenv requirements --hash > requirements.txt \
 && pip install --no-deps --no-cache-dir -r requirements.txt \
 && pip uninstall -y wheel pipenv setuptools pip


###################################################
# Final stage - NGC distroless runtime. No shell, no apt, no pip.
FROM ${DISTROLESS_IMG}:${DISTROLESS_TAG} AS py-deploy

# Re-declare ARG so it's visible to this stage's COPY paths.
ARG PYTHON_VERSION

ENV LD_LIBRARY_PATH=/lib/distroless/ \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Installed Python libraries.
COPY --from=builder /usr/local/lib/python${PYTHON_VERSION}/site-packages \
                    /usr/local/lib/python${PYTHON_VERSION}/site-packages

# Bundled OS-shipped libs that distroless needs (kept for parity with the
# reference pattern; safe even when empty).
COPY --from=builder /lib/distroless/ /lib/distroless/

# dpkg status.d entries so the NGC scanner can identify bundled packages.
COPY --from=builder /var/lib/dpkg/status.d /var/lib/dpkg/status.d

# Source-code compliance: GCC runtime source plus upstream tarballs for
# native code vendored inside the Python wheels (numpy).
COPY --from=builder /usr/share/source /usr/share/source

# --chmod keeps the image byte-identical regardless of the builder's umask;
# without it a restrictive umask ships 0600 files the nonroot user cannot read.
WORKDIR /app
COPY --chmod=0644 src/_linalg.py /app/_linalg.py
COPY --chmod=0644 src/generate_cam_info_configs.py /app/generate_cam_info_configs.py
COPY --chmod=0644 src/generate_pub_sub_configs.py  /app/generate_pub_sub_configs.py
COPY --chmod=0644 src/mv3dt-config-init.py /app/mv3dt-config-init.py
COPY --chmod=0644 3rdParty_Licenses.md /app/3rdParty_Licenses.md

# Reset any preset ENTRYPOINT from the distroless base so CMD is used directly.
ENTRYPOINT []
CMD ["python3", "-u", "/app/mv3dt-config-init.py"]
