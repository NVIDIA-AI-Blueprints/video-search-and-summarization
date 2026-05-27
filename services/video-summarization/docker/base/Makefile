# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

BASE_IMG_NAME ?= via-engine-base

BUILD_PLATFORM ?= $(shell uname -m | sed 's/x86_64/amd64/' | sed 's/aarch64/arm64/')
BUILD_PLATFORM := linux/$(BUILD_PLATFORM)

ifeq ($(origin ARM_PLATFORM), undefined)
ifneq (,$(findstring Thor,$(GPU_NAME)))
	ARM_PLATFORM := igpu
else
	ARM_PLATFORM := sbsa
endif
endif


ARM_PLATFORM ?= sbsa

build:
	@while true; do docker ps >/dev/null 2>&1 && break; echo "Docker daemon still not up. Sleeping..."; sleep 5; done && \
	DOCKER_BUILDKIT=1 docker build \
		--network host \
		--build-arg ARM_PLATFORM=$(ARM_PLATFORM) \
		-t $(BASE_IMG_NAME) \
		--platform=$(BUILD_PLATFORM) \
		-f Dockerfile ../..

