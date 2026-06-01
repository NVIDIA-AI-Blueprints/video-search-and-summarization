# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

ARG PYTHON_VERSION=3.13
ARG DISTROLESS_TAG=${PYTHON_VERSION}-v4.0.6
ARG DISTROLESS_IMG=nvcr.io/nvidia/distroless/python

###################################################
# Container used to build and download libraries
FROM python:${PYTHON_VERSION}-trixie AS builder

ARG TARGETARCH

WORKDIR /build

COPY docker/deps.json deps.json

# Install jq so generate_metadata.sh does not try to download it from GitHub (fails in CI)
# Ensure status.d exists for py-deploy COPY (python:3.13-trixie may not have it)
RUN apt-get update && apt-get install -y --no-install-recommends jq && rm -rf /var/lib/apt/lists/* && \
    mkdir -p /var/lib/dpkg/status.d

RUN curl -o generate_metadata.sh https://urm.nvidia.com/artifactory/sw-gpu-ucs-hardened-debian/tools/generate_metadata_v0.3.sh  && \
    chmod +x generate_metadata.sh && \
    ./generate_metadata.sh deps.json

# Download GCC source code
RUN echo "deb-src http://deb.debian.org/debian trixie main" >> /etc/apt/sources.list && \
    echo "deb-src http://deb.debian.org/debian trixie-updates main" >> /etc/apt/sources.list && \
    apt-get update && \
    apt-get install -y dpkg-dev && \
    mkdir -p /usr/share/source && \
    cd /usr/share/source && \
    apt-get source gcc-14 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Copy python app here
WORKDIR /behavior-analytics


# Resolves issue with installing python package fiona on arch64
RUN if [ "$TARGETARCH" = "arm64" ]; then \
        apt-get update && \
            apt-get install -y libgdal-dev; \
    fi

# Install pipenv and other tools
RUN pip install --upgrade pip pipenv setuptools wheel

# Install requirements and py analytics library itself
COPY . .
RUN pipenv requirements --hash > requirements.txt && \
    pip install --no-deps --no-cache-dir -r requirements.txt && \
    pip install --no-deps --no-cache-dir . && \
    pip uninstall -y wheel pipenv pip

# Prepare clean runtime directory with only required files
RUN mkdir -p /runtime && \
    cp -r apps /runtime/ && \
    cp -r src/mdx/analytics/core/tools /runtime/tools && \
    cp 3rdParty_Licenses.md /runtime/ && \
    chown -R 1000:1000 /runtime

# ###################################################
# Container to deploy and run the python app
FROM $DISTROLESS_IMG:$DISTROLESS_TAG AS py-deploy

ENV LD_LIBRARY_PATH=/lib/distroless/
# Copy installed python libraries
COPY --from=builder /usr/local/lib/python${PY_VERSION}/site-packages /usr/local/lib/python${PY_VERSION}/site-packages
# Copy necessary depedendencies
COPY --from=builder /lib/distroless/  /lib/distroless/
# Copy the status.d for scanning (directory created in builder if missing)
COPY --from=builder /var/lib/dpkg/status.d /var/lib/dpkg/status.d
# Copy GCC source code for license compliance
COPY --from=builder /usr/share/source /usr/share/source

# Copy only required runtime files (no source code, tests, or build artifacts)
# Single COPY ensures /behavior-analytics directory has correct 1000:1000 ownership
COPY --from=builder --chown=1000:1000 /runtime /behavior-analytics

WORKDIR /behavior-analytics
