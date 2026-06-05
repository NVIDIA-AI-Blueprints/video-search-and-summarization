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
# 3. arm64 target dependencies via multiarch. These mirror the packages
#    that install_target_packages.sh used to install inside the chrooted
#    Jetson rootfs, but here they land natively on the x86 host under
#    /usr/{include,lib}/aarch64-linux-gnu/ for the cross compiler to find.
#
#    Side effect: some arm64 -dev packages (e.g. libgtest-dev:arm64)
#    depend on a compiler and pull g++-13:arm64 from ports.ubuntu.com.
#    That version differs from the amd64 g++-13 the CUDA base image
#    pins, and the two cannot co-install, so apt removes the amd64
#    toolchain - which cascades to cuda-nvcc-13-0 and cuda-crt-13-0 and
#    deletes /usr/local/cuda-13.0/include/crt/host_defines.h. Step 4
#    below puts the CUDA crt headers back. The aarch64-linux-gnu-*
#    cross toolchain from step 2 is unaffected (it is a distinct package
#    set from the native amd64 g++-13 that gets evicted here).
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
# 4. Restore the CUDA CRT (compile-time runtime) headers.
#    cuda_runtime_api.h includes "crt/host_defines.h" and friends. The
#    arm64 multiarch step above evicts cuda-crt-13-0 along with the
#    native amd64 toolchain (see the note on step 3). Reinstalling it in
#    a separate transaction here brings the crt/ header tree back under
#    /usr/local/cuda-13.0/include/crt/ without re-triggering the
#    gcc-13 amd64-vs-arm64 version conflict that breaks a combined
#    install. We do not install nvcc: VIOS only compiles host .cpp that
#    pulls in CUDA headers and dlopen's the libraries at runtime, so the
#    compile headers are all that is needed. The NVIDIA CUDA apt repo is
#    already configured in the base image.
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
