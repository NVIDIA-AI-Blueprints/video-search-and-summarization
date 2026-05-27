#!/bin/bash

# SPDX-FileCopyrightText: Copyright (c) 2020-2020 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

trap cleanup EXIT

function cleanup {
	pushd ${JETSON_ROOTFS}
	sudo umount ./sys
	sudo umount ./proc
	sudo umount ./dev
	popd
}


pushd ${JETSON_ROOTFS}
cp /usr/bin/qemu-aarch64-static usr/bin/
cp /etc/resolv.conf etc

sudo mount /sys ./sys -o bind
sudo mount /proc ./proc -o bind
sudo mount /dev ./dev -o bind

sudo LC_ALL=C chroot . /bin/bash -c "
	apt-get update ; \
	DEBIAN_FRONTEND=noninteractive  apt-get install -y --no-install-recommends \
	cmake \
	g++ \
	lbzip2 \
	libcurl4-openssl-dev \
	libcurl3-gnutls \
	libegl1-mesa-dev \
	libgtest-dev  \
	libjsoncpp-dev \
	libssl-dev  \
	libxml2-dev libxml2 \
	sqlite3 libsqlite3-dev \
	uuid uuid-dev \
	gstreamer1.0-plugins-base \
	gstreamer1.0-plugins-good \
	gstreamer1.0-plugins-ugly \
	gstreamer1.0-plugins-bad \
	libgstreamer1.0-0 \
	libgstreamer-plugins-base1.0-dev \
	libboost-all-dev \
	libgrpc++-dev libgrpc-dev libprotobuf-dev protobuf-compiler-grpc && \
	wget http://cuda-repo/release-candidates/kitpicks/l4t-cuda-r13-0/13.0.0/021/local_installers/l4t-cuda-tegra-repo-ubuntu2404-13-0-local_13.0.0-1_arm64.deb && \
	dpkg -i ./l4t-cuda-tegra-repo-ubuntu2204-13-0-local_13.0.0-1_arm64.deb && \
	cp /var/l4t-cuda-tegra-repo-ubuntu2404-13-0-local/l4t-cuda-tegra-06D7ADF6-keyring.gpg /usr/share/keyrings/ && \
	rm ./l4t-cuda-tegra-repo-ubuntu2404-13-0-local_13.0.0-1_arm64.deb && \
	apt-get update && \
	apt install -y cuda-toolkit-13-0 && \
	cd /usr/src/gtest && \
	cmake . && \
	make && \
	mv libg* /usr/lib/
"
popd
