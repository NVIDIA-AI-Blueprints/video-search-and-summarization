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

set -xe
if [[ -z ${TOP} ]]; then
	echo "ERROR: TOP is not set!"
	exit 1
fi

if [[ -z ${IMAGE_TAG} ]]; then
	echo "ERROR: IMAGE_TAG is not set!"
	exit 1
fi

if [[ -z "${ARCH}" ]]; then
	echo "ERROR: ARCH is not set!"
	exit 1
fi

build_dir=${TOP}/out/tmp
mkdir -p ${build_dir}
pushd "${build_dir}"

DOCKER=docker
DOCKERFILE=Dockerfile.app

if [[ "${PUSH_TO_NGC}" = "1" ]]; then
	REGISTRY_USER='$oauthtoken'
	REGISTRY_PASSWORD="${NGC_PASSWORD}"
	REGISTRY="nvcr.io"
	if [[ "${PROJECT}" = "mms" ]]; then
		REGISTRY_REPO="nvcr.io/metropolis/metropolis-analytic/mms"
	elif [[ "${PROJECT}" = "vst" ]]; then
		REGISTRY_REPO="nvcr.io/rxczgrvsg8nx/vst-dev/vst"
		if [[ "${PUSH_TO_PROD}" = "1" ]]; then
			REGISTRY_REPO="nvcr.io/rxczgrvsg8nx/vst-1-0/vst"
		fi
	elif [[ "${PROJECT}" = "nvstreamer" ]]; then
		REGISTRY_REPO="nvcr.io/metropolis/metropolis-analytic/nvstreamer"
	else
		echo "Error: Unsupported PROJECT !!"
		echo "Variable PROJECT should be set to one of the following:"
	        echo "{ vst , mms, nvstreamer, all}"
	        echo "Using default project { vst }"

		REGISTRY_REPO="nvcr.io/rxczgrvsg8nx/vst-dev/vst"
		if [[ "${PUSH_TO_PROD}" = "1" ]]; then
			REGISTRY_REPO="nvcr.io/rxczgrvsg8nx/vst-1-0/vst"
		fi
	fi
else
	REGISTRY_REPO="gitlab-master.nvidia.com:5005/l4tmm/vms_shim/vms_shim_release"
	REGISTRY_USER="${CI_REGISTRY_USER}"
	REGISTRY_PASSWORD="${CI_REGISTRY_PASSWORD}"
	REGISTRY="${CI_REGISTRY}"
fi

docker_image="${REGISTRY_REPO}:${IMAGE_TAG}_${ARCH}"
cp "${TOP}/out/${ARCH}/vst_release.tbz2" .
cp "${TOP}/cicd_files/${ARCH}/${DOCKERFILE}" .

${DOCKER} login -u ${REGISTRY_USER} -p ${REGISTRY_PASSWORD} ${REGISTRY}

if [[ "${NO_CACHE}" = "1" ]]; then
	${DOCKER} build \
		--network=host \
		-t "${docker_image}" \
		--build-arg PKG_LOCATION="." \
		-f ${DOCKERFILE} .
else
	${DOCKER} build \
		--network=host \
		-t "${docker_image}" \
		--build-arg PKG_LOCATION="." \
		--no-cache \
		-f ${DOCKERFILE} .
fi

${DOCKER} push "${docker_image}"
popd
rm -rf "${build_dir}"
