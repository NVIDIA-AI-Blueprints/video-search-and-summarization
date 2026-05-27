#!/bin/bash

# SPDX-FileCopyrightText: Copyright (c) 2023 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

if [[ -z ${HELM_CHART} ]]; then
	echo "ERROR: HELM_CHART is not specified!"
	exit 1
fi

if [[ -z "${PUSH_TO_NGC}" ]]; then
	echo "ERROR: PUSH_TO_NGC is not set!"
	exit 1
fi

HELM=helm
CHART_NAME=${HELM_CHART}

PROD_ORG_NAME=ngc-media-service
PROD_NGC_REPO=https://helm.ngc.nvidia.com/rxczgrvsg8nx/vst-1-0/

DEV_ORG_NAME=ngc-media-service-dev
DEV_NGC_REPO=https://helm.ngc.nvidia.com/rxczgrvsg8nx/vst-dev/

helm_chart_dir=${TOP}
pushd "${TOP}"

if [[ "${PUSH_TO_NGC}" = "1" ]]; then
	REGISTRY_USER='$oauthtoken'
	REGISTRY_PASSWORD="${NGC_PASSWORD}"

	${HELM} plugin install https://github.com/chartmuseum/helm-push
	if [[ "${PUSH_TO_PROD}" = "1" ]]; then
		${HELM} repo add ${PROD_ORG_NAME} ${PROD_NGC_REPO} --username=${REGISTRY_USER} --password=${REGISTRY_PASSWORD}
	else
		${HELM} repo add ${DEV_ORG_NAME} ${DEV_NGC_REPO} --username=${REGISTRY_USER} --password=${REGISTRY_PASSWORD}
	fi

	if [[ "${PROJECT}" = "mms" ]]; then
		helm_chart_dir=${TOP}/deployment/helm_charts/mms
	elif [[ "${PROJECT}" = "vst" ]] || [[ "${PROJECT}" = "vms" ]]; then
		helm_chart_dir=${TOP}/deployment/helm_charts/vst
	elif [[ "${PROJECT}" = "nvstreamer" ]]; then
		helm_chart_dir=${TOP}/deployment/helm_charts/nvstreamer
	else
		echo "Error: Unsupported PROJECT !!"
		echo "Variable PROJECT should be set to one of the following:"
	        echo "{ vms , mms , vst , nvstreamer }"
		exit 1
	fi
else
	echo "PUSH_TO_NGC is not set, Not pushing the helm chart"
	exit 1
fi

pushd "${helm_chart_dir}"

# Push the helm chart to ngc
if [[ "${PUSH_TO_PROD}" = "1" ]]; then
	helm cm-push ${HELM_CHART} ${PROD_ORG_NAME}
else
	helm cm-push ${HELM_CHART} ${DEV_ORG_NAME}
fi

popd
