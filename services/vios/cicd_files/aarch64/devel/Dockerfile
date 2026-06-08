# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

# VIOS aarch64 cross-compilation container (NGC CUDA base + arm64 multiarch)
# Host:   x86_64 Ubuntu 24.04 (nvcr.io/nvidia/cuda:13.0.0-devel-ubuntu24.04)
# Target: aarch64 (Jetson Thor and DGX-Spark)

FROM nvcr.io/nvidia/cuda:13.0.0-devel-ubuntu24.04

ARG DEBIAN_FRONTEND=noninteractive

# ---------------------------------------------------------------------------
# 1. Enable arm64 as a foreign architecture for Ubuntu multiarch and point
#    the default sources at amd64 only (so apt does not try to fetch arm64
#    binaries from x86 mirrors, and does not pull unused i386 metadata).
#    arm64 packages are pulled from ports.ubuntu.com via a dedicated
#    sources file.
#
#    Ubuntu 24.04 uses /etc/apt/sources.list.d/ubuntu.sources (deb822
#    format) rather than the legacy /etc/apt/sources.list, so the
#    Architectures: line is added to that file.
# ---------------------------------------------------------------------------
RUN dpkg --add-architecture arm64 && \
    if [ -f /etc/apt/sources.list.d/ubuntu.sources ]; then \
        sed -i '/^Types:/a Architectures: amd64' /etc/apt/sources.list.d/ubuntu.sources ; \
    fi && \
    if [ -f /etc/apt/sources.list ]; then \
        sed -i 's|^deb |deb [arch=amd64] |g' /etc/apt/sources.list ; \
    fi && \
    printf '%s\n' \
        'deb [arch=arm64] http://ports.ubuntu.com/ubuntu-ports noble main restricted universe multiverse' \
        'deb [arch=arm64] http://ports.ubuntu.com/ubuntu-ports noble-updates main restricted universe multiverse' \
        'deb [arch=arm64] http://ports.ubuntu.com/ubuntu-ports noble-security main restricted universe multiverse' \
        > /etc/apt/sources.list.d/arm64-ports.list

# ---------------------------------------------------------------------------
# 2. Host-side tooling: cross GCC/G++, build tools, package fetchers.
#    crossbuild-essential-arm64 is the Debian/Ubuntu meta package that
#    pulls in gcc/g++/binutils for aarch64 in a multiarch-safe way.
# ---------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        wget \
        gnupg \
        sudo \
        vim \
        make \
        cmake \
        pkg-config \
        binutils \
        crossbuild-essential-arm64 \
        protobuf-compiler-grpc \
        lbzip2 \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# 3. arm64 target dependencies via multiarch. These land under
#    /usr/{include,lib}/aarch64-linux-gnu/ for the cross compiler to find.
#
#    Side effect: some arm64 -dev packages (e.g. libgtest-dev:arm64) pull
#    g++-13:arm64, which conflicts with the amd64 g++-13 the base image
#    pins. apt resolves this by removing the amd64 toolchain, taking
#    cuda-crt-13-0 (and its crt/ headers) with it; step 4 restores them.
#    The aarch64-linux-gnu-* cross toolchain from step 2 is unaffected.
# ---------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
        libcurl4-openssl-dev:arm64 \
        libcurl3-gnutls:arm64 \
        libegl1-mesa-dev:arm64 \
        libgtest-dev:arm64 \
        libjsoncpp-dev:arm64 \
        libssl-dev:arm64 \
        libxml2-dev:arm64 \
        libxml2:arm64 \
        libsqlite3-dev:arm64 \
        sqlite3:arm64 \
        uuid-dev:arm64 \
        uuid:arm64 \
        libboost-all-dev:arm64 \
        libgstreamer1.0-dev:arm64 \
        libgstreamer-plugins-base1.0-dev:arm64 \
        libgstreamer-plugins-bad1.0-dev:arm64 \
        gstreamer1.0-plugins-base:arm64 \
        gstreamer1.0-plugins-good:arm64 \
        gstreamer1.0-plugins-bad:arm64 \
        gstreamer1.0-plugins-ugly:arm64 \
        librdkafka-dev:arm64 \
        libhiredis-dev:arm64 \
        libldap2-dev:arm64 \
        libpq-dev:arm64 \
        libpqxx-dev:arm64 \
        libpaho-mqtt-dev:arm64 \
        libpaho-mqttpp-dev:arm64 \
        libgrpc++-dev:arm64 \
        libgrpc-dev:arm64 \
        libprotobuf-dev:arm64 \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# 4. Restore the CUDA crt/ headers (crt/host_defines.h etc., pulled in by
#    cuda_runtime_api.h) that step 3 evicted. A separate transaction
#    avoids re-triggering the gcc-13 conflict. nvcc is not reinstalled:
#    VIOS only compiles host code against the CUDA headers and dlopen's
#    the libraries at runtime.
# ---------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
        cuda-crt-13-0 \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# 5. Build environment for the cross toolchain.
#    Only CROSS_COMPILE is exported. The top-level Makefile builds the
#    cross tool names itself via `CXX := $(CROSS_COMPILE)$(CXX)` etc.,
#    so pre-setting CC/CXX/AR/LD here would double-prefix them.
#    PKG_CONFIG_* vars steer pkg-config at the arm64 sysroot so configure
#    scripts pick up aarch64 .pc files instead of x86_64 ones.
#
#    CUDA_TARGET_DIR points at the base image's CUDA install root. The
#    devel image places headers under /usr/local/cuda-13.0/include/
#    directly. The Makefile consumes this via `-I $(CUDA_TARGET_DIR)/include`.
# ---------------------------------------------------------------------------
ENV CROSS_COMPILE="aarch64-linux-gnu-"
ENV PKG_CONFIG_PATH="/usr/lib/aarch64-linux-gnu/pkgconfig:/usr/share/pkgconfig"
ENV PKG_CONFIG_LIBDIR="/usr/lib/aarch64-linux-gnu/pkgconfig:/usr/share/pkgconfig"
ENV CUDA_TARGET_DIR="/usr/local/cuda-13.0"
