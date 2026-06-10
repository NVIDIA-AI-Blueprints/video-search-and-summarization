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

FROM ubuntu:22.04

ADD full_linux_for_tegra.tbz2 /root

RUN apt-get update && \
	DEBIAN_FRONTEND=noninteractive  apt-get install -y --no-install-recommends \
	binutils \
	lbzip2 \
	sudo \
	qemu \
	qemu-user-static \
	binfmt-support \
	vim \
	make \
	g++-aarch64-linux-gnu \
	gcc-aarch64-linux-gnu \
	wget \
	xz-utils\
	librdkafka-dev \
	libhiredis1.1.0 \
	libldap2-dev \
	libpq-dev \
	libpqxx-dev

ENV JETSON_ROOTFS /jetson_rootfs

RUN mkdir -p ${JETSON_ROOTFS} && \
	cd /root/full_linux_for_tegra/  && \
	tar xvjf linux_for_tegra.tbz2 &&  \
	tar xvjf sample_fs.tbz2 -C ${JETSON_ROOTFS} && \
	tar xvjf jetson_multimedia_api.tbz2 -C ${JETSON_ROOTFS} && \
	cd Linux_for_Tegra && \
	dpkg-reconfigure qemu-user-static && \
	./apply_binaries.sh --root ${JETSON_ROOTFS} && \
	rm -rf /root/*

ADD install_target_packages.sh /root

ENV CROSS_COMPILE="aarch64-linux-gnu-"
