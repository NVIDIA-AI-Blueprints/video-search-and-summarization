# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#!/bin/bash

# Alerts profile entrypoint (Helm). Aligned with deploy/docker/services/rtvi/rtvi-cv/ds-start.sh
# rtdetr-gdino path. ConfigMap files are copied to writable /wdm-scripts by the init container;
# do not sed ConfigMap subPath mounts under APP_ROOT (read-only).

CONFIG_FILE=${1:-"run_config-api-rtdetr-protobuf700.txt"}
WDM_CONFIGS="/wdm-scripts"
ENGINES_DIR="/opt/engines"
mkdir -p "${ENGINES_DIR}/gdino" "${ENGINES_DIR}/rtdetr-its"
GDINO_TRT_PLAN="${ENGINES_DIR}/gdino/model_gdino_trt.plan"
APP_ROOT="/opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app"
STORAGE_ROOT="/opt/storage"
GDINO_ONNX="${STORAGE_ROOT}/gdino/mgdino_mask_head_pruned_dynamic_batch.onnx"
RTDETR_ONNX="${STORAGE_ROOT}/rtdetr-its/model_epoch_035.fp16.onnx"
RTDETR_INFER_CONFIG="${WDM_CONFIGS}/rtdetr-960x544.txt"
GDINO_TRITON_CONFIG="${WDM_CONFIGS}/config_triton_nvinferserver_gdino.txt"
GDINO_TRITON_PLAN_DIR="/opt/nvidia/deepstream/deepstream/sources/TritonGdino/triton_model_repo/gdino_trt/1"

# Tracker model is bundled in the image; RT-DETR/GDINO ONNX files come from /opt/storage.
cp "${APP_ROOT}/models/rtdetr-its/resnet50_market1501.etlt" \
   /opt/nvidia/deepstream/deepstream/samples/models/Tracker/resnet50_market1501.etlt

NUM_SENSORS=${NUM_SENSORS:-30}
echo "##### Using NUM_SENSORS=${NUM_SENSORS} #####"

echo "##### Updating batch size configurations in $CONFIG_FILE with NUM_SENSORS=${NUM_SENSORS}... #####"
sed -i "/^\[source-list\]/,/^\[/{s/^max-batch-size=.*/max-batch-size=${NUM_SENSORS}/;}" "$CONFIG_FILE"
sed -i "/^\[streammux\]/,/^\[/{s/^batch-size=.*/batch-size=${NUM_SENSORS}/;}" "$CONFIG_FILE"
sed -i "/^\[primary-gie\]/,/^\[/{s/^batch-size=.*/batch-size=${NUM_SENSORS}/;}" "$CONFIG_FILE"

if [[ "${MODEL_NAME_2D:-}" == "GDINO" ]]; then
    if [[ ! -f "$GDINO_TRT_PLAN" ]]; then
        if [[ ! -f "$GDINO_ONNX" ]]; then
            echo "ERROR: GDINO ONNX not found at ${GDINO_ONNX}"
            exit 1
        fi
        echo "##### Building engine file for ${GDINO_ONNX} ... #####"
        if ! /usr/src/tensorrt/bin/trtexec --onnx="${GDINO_ONNX}" \
        --minShapes=inputs:1x3x544x960,input_ids:1x256,attention_mask:1x256,position_ids:1x256,token_type_ids:1x256,text_token_mask:1x256x256 \
        --optShapes=inputs:1x3x544x960,input_ids:1x256,attention_mask:1x256,position_ids:1x256,token_type_ids:1x256,text_token_mask:1x256x256 \
        --maxShapes=inputs:${NUM_SENSORS}x3x544x960,input_ids:${NUM_SENSORS}x256,attention_mask:${NUM_SENSORS}x256,position_ids:${NUM_SENSORS}x256,token_type_ids:${NUM_SENSORS}x256,text_token_mask:${NUM_SENSORS}x256x256 \
        --useCudaGraph \
        --fp16 \
        --saveEngine="${GDINO_TRT_PLAN}"; then
            echo "ERROR: GDINO TensorRT build failed for ${GDINO_ONNX}"
            exit 1
        fi
        if [[ ! -f "$GDINO_TRT_PLAN" ]]; then
            echo "ERROR: GDINO TensorRT engine was not created at ${GDINO_TRT_PLAN}"
            exit 1
        fi
        echo "##### Engine file for ${GDINO_ONNX} built successfully... #####"
    else
        echo "##### Skipping TensorRT build; engine already exists at ${GDINO_TRT_PLAN} #####"
    fi
    mkdir -p "$GDINO_TRITON_PLAN_DIR"
    cp "${GDINO_TRT_PLAN}" "${GDINO_TRITON_PLAN_DIR}/model.plan"
    if [[ ! -f "${GDINO_TRITON_PLAN_DIR}/model.plan" ]]; then
        echo "ERROR: GDINO model.plan was not copied to ${GDINO_TRITON_PLAN_DIR}/model.plan"
        exit 1
    fi
    echo "##### Copied GDINO plan to ${GDINO_TRITON_PLAN_DIR}/model.plan #####"

    echo "##### Modifying ${CONFIG_FILE} for GDINO configuration... #####"
    sed -i "/^\[primary-gie\]/,/^\[/{s|config-file=.*|config-file=${GDINO_TRITON_CONFIG}|;}" "$CONFIG_FILE"
    sed -i "\#config-file=${GDINO_TRITON_CONFIG}#a plugin-type=1" "$CONFIG_FILE"

    echo "##### Updating max_batch_size to ${NUM_SENSORS} in ${GDINO_TRITON_CONFIG}... #####"
    sed -i "s/max_batch_size: [0-9]\+/max_batch_size: ${NUM_SENSORS}/" "${GDINO_TRITON_CONFIG}"

    echo "##### Updating max_batch_size to ${NUM_SENSORS} in GDINO Triton model config files... #####"
    GDINO_CONFIG_FILES=(
        "/opt/nvidia/deepstream/deepstream/sources/TritonGdino/triton_model_repo/ensemble_python_gdino/config.pbtxt"
        "/opt/nvidia/deepstream/deepstream/sources/TritonGdino/triton_model_repo/gdino_trt/config.pbtxt"
        "/opt/nvidia/deepstream/deepstream/sources/TritonGdino/triton_model_repo/gdino_postprocess/config.pbtxt"
        "/opt/nvidia/deepstream/deepstream/sources/TritonGdino/triton_model_repo/gdino_preprocess/config.pbtxt"
    )
    for config_file in "${GDINO_CONFIG_FILES[@]}"; do
        if [[ -f "$config_file" ]]; then
            echo "Updating max_batch_size in $config_file"
            sed -i \
                -e "s/^\s*max_batch_size\s*:\s*[0-9]\+\s*$/max_batch_size: ${NUM_SENSORS}/" \
                -e "s/^\s*max_batch_size\s*:\s*\"\s*[0-9]\+\s*\"\s*$/max_batch_size: ${NUM_SENSORS}/" \
                -e "s/^\s*max_batch_size\s*=\s*[0-9]\+\s*$/max_batch_size = ${NUM_SENSORS}/" \
                -e "s/^\s*max_batch_size\s*=\s*\"\s*[0-9]\+\s*\"\s*$/max_batch_size = ${NUM_SENSORS}/" \
                "$config_file"
        else
            echo "Warning: Config file $config_file not found, skipping..."
        fi
    done
    echo "##### GDINO config files updated successfully... #####"
else
    echo "##### RT-DETR model being used... #####"
    # Match Docker: keep ONNX on /opt/storage and cache the generated TensorRT engine under /opt/engines.
    RTDETR_ENGINE="${ENGINES_DIR}/rtdetr-its/model_epoch_035.fp16.onnx_b${NUM_SENSORS}_gpu0_fp16.engine"
    if [[ ! -f "$RTDETR_ONNX" ]]; then
        echo "ERROR: RT-DETR ONNX not found at ${RTDETR_ONNX}"
        exit 1
    fi
    if [[ -f "$RTDETR_INFER_CONFIG" ]]; then
        sed -i "/^\[property\]/,/^\[/{s|^model-engine-file=.*|model-engine-file=${RTDETR_ENGINE}|;}" "$RTDETR_INFER_CONFIG"
        sed -i "/^\[property\]/,/^\[/{s|^onnx-file=.*|onnx-file=${RTDETR_ONNX}|;}" "$RTDETR_INFER_CONFIG"
        sed -i "/^\[property\]/,/^\[/{s/^batch-size=.*/batch-size=${NUM_SENSORS}/;}" "$RTDETR_INFER_CONFIG"
        sed -i "/^\[property\]/,/^\[/{s|^labelfile-path=.*|labelfile-path=${WDM_CONFIGS}/rtdetr-960x544-labels.txt|;}" "$RTDETR_INFER_CONFIG"
        sed -i "/^\[primary-gie\]/,/^\[/{s|config-file=.*|config-file=${RTDETR_INFER_CONFIG}|;}" "$CONFIG_FILE"
    else
        echo "Warning: RT-DETR infer config $RTDETR_INFER_CONFIG not found, skipping..."
    fi
    if [[ -f "$RTDETR_ENGINE" ]]; then
        echo "##### Using cached RT-DETR engine at ${RTDETR_ENGINE} #####"
    else
        echo "##### No cached RT-DETR engine; nvinfer will build ${RTDETR_ENGINE} (persisted under /opt/engines) #####"
    fi
    echo "##### RT-DETR nvinfer config updated successfully... #####"
    echo "##### Contents of $RTDETR_INFER_CONFIG: #####"
    cat "$RTDETR_INFER_CONFIG"
fi

if [[ "${HARDWARE_PROFILE:-}" == "DGX-SPARK" || "${HARDWARE_PROFILE:-}" == "DGX-THOR" ]]; then
    echo "##### Setting msg-conv-msg2p-lib to libnvds_msgconv.so for sink1 group... #####"
    sed -i '/^\[sink1\]/,/^\[/{/^msg-conv-msg2p-lib=/d;}' "$CONFIG_FILE"
    sed -i '/^\[sink1\]/a msg-conv-msg2p-lib=/opt/nvidia/deepstream/deepstream/lib/libnvds_msgconv.so' "$CONFIG_FILE"
    sed -i '/^\[primary-gie\]/,/^\[/{s/^interval=.*/interval=1/;}' "$CONFIG_FILE"
else
    echo "##### Setting msg-conv-msg2p-lib to libnvds_msgconv_mega2d.so for sink1 group... #####"
    sed -i '/^\[sink1\]/,/^\[/{/^msg-conv-msg2p-lib=/d;}' "$CONFIG_FILE"
    sed -i '/^\[sink1\]/a msg-conv-msg2p-lib=/opt/nvidia/deepstream/deepstream/lib/libnvds_msgconv_mega2d.so' "$CONFIG_FILE"
fi

if [[ "${HARDWARE_PROFILE:-}" == "DGX-THOR" ]]; then
    echo "##### Setting compute-hw=2 in tracker section of $CONFIG_FILE... #####"
    sed -i '/^\[tracker\]/,/^\[/{/^compute-hw=/d;}' "$CONFIG_FILE"
    sed -i '/^\[tracker\]/a compute-hw=2' "$CONFIG_FILE"
    echo "##### Setting low-latency-mode to 0 for source-list section... #####"
    sed -i '/^\[source-list\]/,/^\[/{/^low-latency-mode=/d;}' "$CONFIG_FILE"
    sed -i '/^\[source-list\]/a low-latency-mode=0' "$CONFIG_FILE"
fi

TRACKER_CONFIG="/opt/nvidia/deepstream/deepstream/samples/configs/deepstream-app/config_tracker_NvDCF_accuracy.yml"
echo "##### Updating minTrackerConfidence in $TRACKER_CONFIG... #####"
if [[ -f "$TRACKER_CONFIG" ]]; then
    sed -i '/^TargetManagement:/,/^[A-Z][a-zA-Z]*:/ {s/^[[:space:]]*minTrackerConfidence:.*/  minTrackerConfidence: 0.2513/;}' "$TRACKER_CONFIG"
    echo "##### Updated minTrackerConfidence to 0.2513 in TargetManagement section... #####"
    if [[ "${HARDWARE_PROFILE:-}" == "DGX-THOR" ]]; then
        echo "##### Updating VisualTracker section in $TRACKER_CONFIG... #####"
        sed -i '/^VisualTracker:/,/^[A-Z][a-zA-Z]*:/ {/^[[:space:]]*visualTrackerType:/d;}' "$TRACKER_CONFIG"
        sed -i '/^VisualTracker:/,/^[A-Z][a-zA-Z]*:/ {/^[[:space:]]*vpiBackend4DcfTracker:/d;}' "$TRACKER_CONFIG"
        sed -i '/^VisualTracker:/a \  visualTrackerType: 2' "$TRACKER_CONFIG"
        sed -i '/^[[:space:]]*visualTrackerType: 2/a \  vpiBackend4DcfTracker: 2' "$TRACKER_CONFIG"
        sed -i '/^TargetManagement:/,/^[A-Z][a-zA-Z]*:/ {s/^[[:space:]]*maxTargetsPerStream:.*/  maxTargetsPerStream: 50/;}' "$TRACKER_CONFIG"
        echo "##### Updated maxTargetsPerStream to 50 in TargetManagement section... #####"
    fi
else
    echo "Warning: Tracker config $TRACKER_CONFIG not found, skipping minTrackerConfidence update..."
fi

echo "##### Contents of $TRACKER_CONFIG: #####"
cat "$TRACKER_CONFIG"

echo "##### Batch size configurations updated successfully in $CONFIG_FILE... #####"

if [[ "${MODEL_NAME_2D:-}" == "GDINO" ]]; then
    M_PARAM=4
else
    M_PARAM=7
fi

if [ "$STREAM_TYPE" = "kafka" ]; then
    echo "Running metropolis_perception_app with kafka configuration..."
else
    echo "STREAM_TYPE not set or invalid. Defaulting to kafka configuration..."
fi
echo -e "\nds main configs\n"
cat "$CONFIG_FILE"
./metropolis_perception_app -c "$CONFIG_FILE" -m "$M_PARAM" -t 0 -l 5 --message-rate 1 --show-sensor-id
