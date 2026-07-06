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
	# apply_binaries.sh leaves a few device-only GUI packages half-configured under
	# the emulated chroot (they pull GUI deps that cannot configure here). Clear that
	# broken dpkg state before installing dev packages, else apt refuses with
	# 'Unmet dependencies'. See handoff notes.
	dpkg --remove --force-remove-reinstreq --force-depends \
		nvidia-l4t-bsp \
		nvidia-l4t-jetsonpower-gui-tools \
		nvidia-l4t-nvpmodel-gui-tools 2>/dev/null || true ; \
	dpkg --configure -a || true ; \
	apt-get update ; \
	apt-get --fix-broken install -y || true ; \
	DEBIAN_FRONTEND=noninteractive apt-get upgrade -y || true ; \
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
	libgstreamer-plugins-bad1.0-dev \
	libboost-all-dev \
	libpaho-mqtt-dev \
	libpaho-mqttpp-dev \
	libprotobuf-dev \
	protobuf-compiler \
	protobuf-compiler-grpc \
	libavformat-dev \
	libavcodec-dev \
	libswscale-dev \
	librdkafka-dev \
	libopencv-dev \
	libgrpc++-dev \
	libgrpc-dev \
	libpq-dev \
	libpqxx-dev \
	libhiredis-dev \
	libldap2-dev \
	make \
	wget ca-certificates gnupg ; \
	( cd /usr/src/gtest && cmake . && make && mv libg* /usr/lib/ ) || echo 'WARN: gtest build skipped (non-fatal, not needed for streamprocessing/sensor)' ; \
	cd /tmp && \
	wget -qO cuda-keyring.deb https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/sbsa/cuda-keyring_1.1-1_all.deb && \
	dpkg -i cuda-keyring.deb && \
	apt-get update && \
	DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
		cuda-cudart-dev-13-2 cuda-crt-13-2 cuda-cccl-13-2 ; \
	# OpenCV dev-symlink repair. The R39 rootfs ships the OpenCV *runtime* libs
	# (libopencv_*.so.4.6.0 in the multiarch dir, from the L4T BSP) but the
	# link-time dev symlinks are missing there, and the ones under /usr/lib
	# dangle (they point at a .408 soname that isn't present). The overlays
	# module links -lopencv_core/-lopencv_imgproc/-lopencv_imgcodecs via
	# '-L \$(SYSROOT)/usr/lib/aarch64-linux-gnu/', so recreate valid dev
	# symlinks against the actually-installed soname. This mirrors the sbsa
	# toolchain, where libopencv-dev provides libopencv_core.so -> .so.406.
	ML=/usr/lib/aarch64-linux-gnu ; \
	for real in \$(ls \${ML}/libopencv_*.so.*.*.* 2>/dev/null); do \
		base=\$(basename \"\$real\") ; stem=\${base%%.so.*} ; \
		ln -sf \"\$base\" \"\${ML}/\${stem}.so\" ; \
	done ; \
	# Drop stale dangling dev symlinks under /usr/lib so the multiarch ones win.
	for stale in \$(ls /usr/lib/libopencv_*.so 2>/dev/null); do \
		[ -e \"\$stale\" ] || rm -f \"\$stale\" ; \
	done ; \
	echo 'opencv dev symlinks after repair:' ; ls -la \${ML}/libopencv_core.so \${ML}/libopencv_imgproc.so \${ML}/libopencv_imgcodecs.so 2>&1
"
popd

# CUDA dev headers (cuda.h / cuda_runtime_api.h) are installed above into the aarch64
# rootfs from the official NVIDIA CUDA repo (ubuntu2404/sbsa), version 13.2 to match
# the DeepStream 9.1 / perception runtime (13.2.75). The unified VIOS build compiles
# the CUDA path on all aarch64 targets (types only; the CUDA runtime is dlopen'd, so
# no CUDA lib is linked). Headers land under:
#   ${JETSON_ROOTFS}/usr/local/cuda-13.2/targets/sbsa-linux/include/
# which is why the toolchain sets CUDA_TARGET_DIR to that prefix.
