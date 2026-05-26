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

BASE_IMG_NAME:=$(shell ./get_base_docker_img.sh)
$(info Using base image $(BASE_IMG_NAME))

GPU_NAME:=$(shell command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi --query-gpu=gpu_name --format=csv,noheader -i 0 2>/dev/null || true)

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
	@while true; do docker ps >/dev/null 2>&1 && break; echo "Docker daemon still not up. Sleeping..."; sleep 5; done
	@echo "Attempting to pull base image $(BASE_IMG_NAME)..."
	@if docker pull $(BASE_IMG_NAME) 2>/dev/null; then \
		echo "Successfully pulled base image from registry"; \
	else \
		echo "Pull failed, building base image locally..."; \
		if [ -z "$(ARTIFACTORY_USER)" ] || [ -z "$(ARTIFACTORY_TOKEN)" ]; then \
			echo "Error: ARTIFACTORY_USER and ARTIFACTORY_TOKEN must be set to build the base image"; \
			echo "Please export ARTIFACTORY_USER and ARTIFACTORY_TOKEN before building"; \
			exit 1; \
		fi; \
		NETRC_FILE=$$(mktemp); \
		echo "Generating temporary .netrc file..."; \
		echo "machine artifactory.nvidia.com" > $$NETRC_FILE; \
		echo "login $(ARTIFACTORY_USER)" >> $$NETRC_FILE; \
		echo "password $(ARTIFACTORY_TOKEN)" >> $$NETRC_FILE; \
		chmod 600 $$NETRC_FILE; \
		echo "Building base image with Artifactory credentials..."; \
		DOCKER_BUILDKIT=1 docker build \
			--network host \
			--secret id=netrc,src=$$NETRC_FILE \
			--build-arg ARM_PLATFORM=$(ARM_PLATFORM) \
			-t $(BASE_IMG_NAME) \
			--platform=$(BUILD_PLATFORM) \
			-f Dockerfile ../..; \
		BUILD_EXIT=$$?; \
		rm -f $$NETRC_FILE; \
		echo "Cleaned up temporary .netrc file"; \
		exit $$BUILD_EXIT; \
	fi
