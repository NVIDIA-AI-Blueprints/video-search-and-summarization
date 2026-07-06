# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

# Orin (Jetson) cross-compile toolchain for the UNIFIED aarch64 VIOS build.
# The R39 rootfs is Ubuntu 24.04 (noble) with gcc-13/14 (GLIBCXX_3.4.32); the host
# compiler must match, so base on ubuntu:24.04 (not 22.04).
# NOTE: this toolchain deliberately does NOT define JETSON_PLATFORM. VIOS now
# compiles a single AARCH64_PLATFORM binary and distinguishes Orin vs Thor/SBSA
# at runtime via isJetsonPlatform(); JETSON_PLATFORM is no longer a build flag.
FROM ubuntu:24.04

ADD full_linux_for_tegra.tbz2 /root

RUN apt-get update && \
	DEBIAN_FRONTEND=noninteractive  apt-get install -y --no-install-recommends \
	binutils \
	bzip2 \
	lbzip2 \
	sudo \
	qemu-user-static \
	binfmt-support \
	vim \
	make \
	g++-aarch64-linux-gnu \
	gcc-aarch64-linux-gnu \
	wget \
	xz-utils\
	librdkafka-dev \
	libhiredis-dev \
	libldap2-dev \
	libpq-dev \
	libpqxx-dev \
	protobuf-compiler-grpc \
	libprotobuf-dev

# Host-side protoc + grpc_cpp_plugin: the vms_media adaptor and notification_proto
# generate .pb.cc / .grpc.pb.cc during the build via `protoc` on the build host (the
# aarch64 rootfs protoc cannot run natively). protobuf-compiler-grpc (protobuf 3.21 /
# grpc 1.51 on noble) matches the rootfs versions and our prebuilt libprotobuf.so.32.0.12.
# libprotobuf-dev provides the well-known .proto files (google/protobuf/timestamp.proto
# etc.) that those .proto files import, on protoc's default host include path.

ENV JETSON_ROOTFS /jetson_rootfs

RUN mkdir -p ${JETSON_ROOTFS} && \
	cd /root/full_linux_for_tegra/  && \
	tar xjf linux_for_tegra.tbz2 &&  \
	tar xjf sample_fs.tbz2 -C ${JETSON_ROOTFS} && \
	tar xjf jetson_multimedia_api.tbz2 -C ${JETSON_ROOTFS} && \
	cd Linux_for_Tegra && \
	dpkg-reconfigure qemu-user-static && \
	{ ./apply_binaries.sh --root ${JETSON_ROOTFS} || echo "WARN: apply_binaries.sh non-zero (device-only GUI pkgs fail dpkg-configure under emulated chroot) - tolerated"; } && \
	rm -rf /root/full_linux_for_tegra

ADD install_target_packages.sh /root

ENV CROSS_COMPILE="aarch64-linux-gnu-"

# CUDA 13.2 aarch64 headers are installed into the rootfs by install_target_packages.sh
# (official ubuntu2404/sbsa repo). Point the VIOS Makefile's CUDA_TARGET_DIR at them so
# the unified build finds cuda.h / cuda_runtime_api.h for the (Orin-unused) CUDA path.
ENV CUDA_TARGET_DIR="/jetson_rootfs/usr/local/cuda-13.2/targets/sbsa-linux"
