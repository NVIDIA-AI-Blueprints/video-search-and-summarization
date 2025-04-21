######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
######################################################################################################

ARG BASE_IMAGE="nvcr.io/nvidia/blueprint/vss-engine-base:2.3.0"
FROM $BASE_IMAGE

RUN git clone https://github.com/NVIDIA/context-aware-rag.git -b v0.5.0 /tmp/vss-ctx-rag
RUN pip install /tmp/vss-ctx-rag --no-deps && \
    rm -rf /tmp/vss-ctx-rag

RUN mkdir -p /opt/nvidia/TritonGdino/triton_model_repo
COPY TritonGdino/triton_model_repo/ /opt/nvidia/TritonGdino/triton_model_repo
COPY TritonGdino/config_triton_nvinferserver_gdino.txt /opt/nvidia/TritonGdino/config_triton_nvinferserver_gdino.txt

#Copy the python source code
RUN --mount=type=bind,source=src,target=/tmp/via-src/src \
    --mount=type=bind,source=docker,target=/tmp/via-src/docker \
    cd /tmp/via-src/ \
    && docker/copy_sources.sh src/ /opt/nvidia/via/

RUN --mount=type=bind,source=config,target=/tmp/via-src/config \
    --mount=type=bind,source=docker,target=/tmp/via-src/docker \
    cd /tmp/via-src/ \
    && docker/copy_configs.sh config/ /opt/nvidia/via/

#Copy tracker and metadata fusion config files
COPY config/MOT_EVAL_config_fusion.yml /opt/nvidia/via/config/
COPY config/config_tracker_NvDCF_accuracy_interval1.yml /opt/nvidia/via/config/default_tracker_config.yml
COPY config/config_tracker_NvDCF_accuracy_SAM2.yml /opt/nvidia/via/config/config_tracker_NvDCF_accuracy_SAM2.yml
COPY config/overrides/overrides_cv_accuracy_mode.yaml /opt/nvidia/via/config/overrides/overrides_cv_accuracy_mode.yaml

COPY start_via.sh /opt/nvidia/via/
#To allow helm chart start script to copy updated config:
RUN chmod -R 777 /opt/nvidia/via/guardrails_config/
COPY NVIDIA_Software_and_Model_Evaluation_License_Agreement.pdf /opt/nvidia/via/
ENV PATH=$PATH:/opt/nvidia/via/
ENV LD_LIBRARY_PATH=$LD_LIBRARY_PATH:/opt/tritonserver/lib/


WORKDIR /opt/nvidia/via/

ENTRYPOINT ["/opt/nvidia/via/start_via.sh"]
