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

BASE_IMG_NAME=$(shell base/get_base_docker_img.sh)
TEST_BASE_IMG_NAME=$(shell base/get_base_docker_img.sh)-$(shell md5sum Dockerfile.test-base | awk '{print $$1}')
TARGETARCH ?= $(shell uname -m | sed 's/x86_64/amd64/' | sed 's/aarch64/arm64/')
BUILD_COMMIT_SHA ?= $(shell git rev-parse --short=7 HEAD 2>/dev/null || echo "unknown")

build:
	$(MAKE) -C base build
	@while true; do docker ps >/dev/null 2>&1 && break; echo "Docker daemon still not up. Sleeping..."; sleep 5; done
	DOCKER_BUILDKIT=1 docker build --network host --build-arg "BASE_IMAGE=$(BASE_IMG_NAME)" --build-arg "TARGETARCH=$(TARGETARCH)" --build-arg "BUILD_COMMIT_SHA=$(BUILD_COMMIT_SHA)" -t via-engine-$(USER) -f Dockerfile ..

build_test_base_image:
	$(MAKE) -C base build
	@docker pull $(TEST_BASE_IMG_NAME) 2>/dev/null || DOCKER_BUILDKIT=1 docker build --network host --build-arg "BASE_IMAGE=$(BASE_IMG_NAME)" -t $(TEST_BASE_IMG_NAME) -f Dockerfile.test-base ..

build_test_image:
	$(MAKE) build_test_base_image
	DOCKER_BUILDKIT=1 docker build --network host --build-arg "BASE_IMAGE=$(TEST_BASE_IMG_NAME)" --build-arg "TARGETARCH=$(TARGETARCH)" --build-arg "BUILD_COMMIT_SHA=$(BUILD_COMMIT_SHA)" -t via-engine-$(USER)-test -f Dockerfile ..

# Multi-arch build target for CI/CD (build only, push handled separately)
# Usage: make -C docker buildx LOCAL_TAG=via-engine-lvs IMAGE_VERSION=0.0.1-multiarch BASE_IMAGE=nvcr.io/.../base:...
buildx:
	@echo "Building multi-arch image..."
	@echo "  Local tag: $(LOCAL_TAG):$(IMAGE_VERSION)-$(TARGETARCH)"
	@echo "  Base: $(BASE_IMAGE)"
	@echo "  Arch: $(TARGETARCH)"
	@echo "  Commit: $(BUILD_COMMIT_SHA)"
	docker buildx build \
		--tag $(LOCAL_TAG):$(IMAGE_VERSION)-$(TARGETARCH) \
		--build-arg "BASE_IMAGE=$(BASE_IMAGE)" \
		--build-arg "TARGETARCH=$(TARGETARCH)" \
		--build-arg "BUILD_COMMIT_SHA=$(BUILD_COMMIT_SHA)" \
		--load \
		-f Dockerfile \
		..

start:
	@./start_docker.sh

stop:
	@./stop_docker.sh

restart:
	@./stop_docker.sh
	@./start_docker.sh

run:
	$(MAKE) stop && $(MAKE) build && $(MAKE) start INTERACTIVE=1
