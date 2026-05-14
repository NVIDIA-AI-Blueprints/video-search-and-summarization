#!/bin/bash

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

# Set default config file or use the first parameter if provided
CONFIG_FILE=${1:-"run_config-api-rtdetr-protobuf700.txt"}
ENGINES_DIR="/opt/engines"
mkdir -p "${ENGINES_DIR}/gdino" "${ENGINES_DIR}/rtdetr-its"
GDINO_TRT_PLAN="${ENGINES_DIR}/gdino/model_gdino_trt.plan"


cp /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/models/rtdetr-its/resnet50_market1501.etlt /opt/nvidia/deepstream/deepstream/samples/models/Tracker/resnet50_market1501.etlt

# Set default NUM_SENSORS if not defined in environment
NUM_SENSORS=${NUM_SENSORS:-30}
echo "##### Using NUM_SENSORS=${NUM_SENSORS} #####"

# Modify CONFIG_FILE with NUM_SENSORS values for batch sizes
echo "##### Updating batch size configurations in $CONFIG_FILE with NUM_SENSORS=${NUM_SENSORS}... #####"

# Update max-batch-size under [source-list] section
sed -i "/^\[source-list\]/,/^\[/{s/^max-batch-size=.*/max-batch-size=${NUM_SENSORS}/;}" $CONFIG_FILE

# Update batch-size under [streammux] section  
sed -i "/^\[streammux\]/,/^\[/{s/^batch-size=.*/batch-size=${NUM_SENSORS}/;}" $CONFIG_FILE

# Update batch-size under [primary-gie] section
sed -i "/^\[primary-gie\]/,/^\[/{s/^batch-size=.*/batch-size=${NUM_SENSORS}/;}" $CONFIG_FILE


if [[ $HARDWARE_PROFILE == "DGX-SPARK" || $HARDWARE_PROFILE == "DGX-THOR" ]]; then
    # Replace or add msg-conv-msg2p-lib property in sink1 group
    echo "##### Setting msg-conv-msg2p-lib to libnvds_msgconv.so for sink1 group... #####"
    # First, remove any existing msg-conv-msg2p-lib line within [sink1] section
    sed -i '/^\[sink1\]/,/^\[/{/^msg-conv-msg2p-lib=/d;}' $CONFIG_FILE
    # Then add the new property after [sink1]
    sed -i '/^\[sink1\]/a msg-conv-msg2p-lib=/opt/nvidia/deepstream/deepstream/lib/libnvds_msgconv.so' $CONFIG_FILE
else
    # Replace or add msg-conv-msg2p-lib property in sink1 group
    echo "##### Setting msg-conv-msg2p-lib to libnvds_msgconv_mega2d.so for sink1 group... #####"
    # First, remove any existing msg-conv-msg2p-lib line within [sink1] section
    sed -i '/^\[sink1\]/,/^\[/{/^msg-conv-msg2p-lib=/d;}' $CONFIG_FILE
    # Then add the new property after [sink1]
    sed -i '/^\[sink1\]/a msg-conv-msg2p-lib=/opt/nvidia/deepstream/deepstream/lib/libnvds_msgconv_mega2d.so' $CONFIG_FILE
fi

if [[ $HARDWARE_PROFILE == "DGX-THOR" ]]; then
    # Set compute-hw=2 under tracker section in CONFIG_FILE
    echo "##### Setting compute-hw=2 in tracker section of $CONFIG_FILE... #####"
    sed -i '/^\[tracker\]/,/^\[/{/^compute-hw=/d;}' $CONFIG_FILE
    sed -i '/^\[tracker\]/a compute-hw=2' $CONFIG_FILE
    # Replace or add low-latency-mode property in source-list section
    echo "##### Setting low-latency-mode to 0 for source-list section... #####"
    # Remove any existing low-latency-mode line within [source-list] section
    sed -i '/^\[source-list\]/,/^\[/{/^low-latency-mode=/d;}' $CONFIG_FILE
    # Then add the new property after [source-list]
    sed -i '/^\[source-list\]/a low-latency-mode=0' $CONFIG_FILE
    # Update VisualTracker section in config_tracker_NvDCF_accuracy.yml
    TRACKER_CONFIG="/opt/nvidia/deepstream/deepstream/samples/configs/deepstream-app/config_tracker_NvDCF_accuracy.yml"
    echo "##### Updating VisualTracker section in $TRACKER_CONFIG... #####"
    # Add or update visualTrackerType and vpiBackend4DcfTracker under VisualTracker section
    if [[ -f "$TRACKER_CONFIG" ]]; then
        # Remove existing visualTrackerType if present
        sed -i '/^VisualTracker:/,/^[A-Z][a-zA-Z]*:/ {/^[[:space:]]*visualTrackerType:/d;}' "$TRACKER_CONFIG"
        # Remove existing vpiBackend4DcfTracker if present
        sed -i '/^VisualTracker:/,/^[A-Z][a-zA-Z]*:/ {/^[[:space:]]*vpiBackend4DcfTracker:/d;}' "$TRACKER_CONFIG"
        # Add the properties after VisualTracker line with proper YAML indentation (2 spaces)
        sed -i '/^VisualTracker:/a \  visualTrackerType: 2' "$TRACKER_CONFIG"
        sed -i '/^[[:space:]]*visualTrackerType: 2/a \  vpiBackend4DcfTracker: 2' "$TRACKER_CONFIG"
        # Update maxTargetsPerStream to 50 in TargetManagement section
        sed -i '/^TargetManagement:/,/^[A-Z][a-zA-Z]*:/ {s/^[[:space:]]*maxTargetsPerStream:.*/  maxTargetsPerStream: 50/;}' "$TRACKER_CONFIG"
        echo "##### Updated maxTargetsPerStream to 50 in TargetManagement section... #####"
        echo "##### Contents of $TRACKER_CONFIG: #####"
        cat $TRACKER_CONFIG
    fi
fi

echo "##### Batch size configurations updated successfully in $CONFIG_FILE... #####"

if [[ $MODEL_NAME_2D == "GDINO" ]]; then

    if [[ ! -f "$GDINO_TRT_PLAN" ]]; then
        echo "##### Building engine file for /opt/storage/gdino/mgdino_mask_head_pruned_dynamic_batch.onnx ... #####"
        /usr/src/tensorrt/bin/trtexec --onnx=/opt/storage/gdino/mgdino_mask_head_pruned_dynamic_batch.onnx \
        --minShapes=inputs:1x3x544x960,input_ids:1x256,attention_mask:1x256,position_ids:1x256,token_type_ids:1x256,text_token_mask:1x256x256 \
        --optShapes=inputs:1x3x544x960,input_ids:1x256,attention_mask:1x256,position_ids:1x256,token_type_ids:1x256,text_token_mask:1x256x256 \
        --maxShapes=inputs:${NUM_SENSORS}x3x544x960,input_ids:${NUM_SENSORS}x256,attention_mask:${NUM_SENSORS}x256,position_ids:${NUM_SENSORS}x256,token_type_ids:${NUM_SENSORS}x256,text_token_mask:${NUM_SENSORS}x256x256 \
        --useCudaGraph \
        --fp16 \
        --saveEngine="$GDINO_TRT_PLAN"
        echo "##### Engine file for /opt/storage/gdino/mgdino_mask_head_pruned_dynamic_batch.onnx built successfully... #####"
    else
        echo "##### Skipping TensorRT build; engine already exists at $GDINO_TRT_PLAN #####"
    fi
    cp "$GDINO_TRT_PLAN" /opt/nvidia/deepstream/deepstream/sources/TritonGdino/triton_model_repo/gdino_trt/1/model.plan
    
    # Modify configuration files for GDINO
    echo "##### Modifying run_config-api-rtdetr-protobuf700.txt for GDINO configuration... #####"
    sed -i '/^\[primary-gie\]/,/^\[/{s/config-file=.*/config-file=config_triton_nvinferserver_gdino.txt/;}' $CONFIG_FILE
    sed -i '/config-file=config_triton_nvinferserver_gdino.txt/a plugin-type=1' $CONFIG_FILE
    
    # Update max_batch_size in GDINO config file
    echo "##### Updating max_batch_size to ${NUM_SENSORS} in config_triton_nvinferserver_gdino.txt... #####"
    sed -i "s/max_batch_size: [0-9]\+/max_batch_size: ${NUM_SENSORS}/" config_triton_nvinferserver_gdino.txt
    
    # Modify max_batch_size to NUM_SENSORS in GDINO Triton config files
    echo "##### Updating max_batch_size to ${NUM_SENSORS} in GDINO Triton model config files... #####"
    
    # Define config files to modify
    GDINO_CONFIG_FILES=(
        "/opt/nvidia/deepstream/deepstream/sources/TritonGdino/triton_model_repo/ensemble_python_gdino/config.pbtxt"
        "/opt/nvidia/deepstream/deepstream/sources/TritonGdino/triton_model_repo/gdino_trt/config.pbtxt"
        "/opt/nvidia/deepstream/deepstream/sources/TritonGdino/triton_model_repo/gdino_postprocess/config.pbtxt"
        "/opt/nvidia/deepstream/deepstream/sources/TritonGdino/triton_model_repo/gdino_preprocess/config.pbtxt"
    )
    
    # Modify each config file
    for config_file in "${GDINO_CONFIG_FILES[@]}"; do
        if [[ -f "$config_file" ]]; then
            echo "Updating max_batch_size in $config_file"
            # Handle different possible formats of max_batch_size
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
    # RT-DETR nvinfer config: engine filename uses b<NUM_SENSORS> (e.g. b4, b8, b30)
    RTDETR_INFER_CONFIG="/opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/rtdetr-960x544.txt"
    if [[ -f "$RTDETR_INFER_CONFIG" ]]; then
        sed -i "/^\[property\]/,/^\[/{s|^model-engine-file=.*|model-engine-file=${ENGINES_DIR}/rtdetr-its/model_epoch_035.fp16.onnx_b${NUM_SENSORS}_gpu0_fp16.engine|;}" "$RTDETR_INFER_CONFIG"
        sed -i "/^\[property\]/,/^\[/{s/^batch-size=.*/batch-size=${NUM_SENSORS}/;}" "$RTDETR_INFER_CONFIG"
    fi
    echo "##### RT-DETR nvinfer config updated successfully... #####"
    echo "##### Contents of $RTDETR_INFER_CONFIG: #####"
    cat $RTDETR_INFER_CONFIG

fi


# Set -m parameter based on MODEL_NAME_2D
if [[ $MODEL_NAME_2D == "GDINO" ]]; then
    M_PARAM=4
else
    M_PARAM=7
fi

# Check STREAM_TYPE and run appropriate command
if [ "$STREAM_TYPE" = "kafka" ]; then
    echo "Running metropolis_perception_app with kafka configuration..."
    echo -e "\nds main configs\n"
    cat $CONFIG_FILE
    ./metropolis_perception_app -c $CONFIG_FILE -m $M_PARAM -t 0 -l 5 --message-rate 1 --show-sensor-id
# elif [ "$STREAM_TYPE" = "redis" ]; then
#     echo "Running metropolis_perception_app with redis configuration..."
#     echo -e "\nds main configs\n"
#     cat ds-main-redis-config.txt
#     ./metropolis_perception_app -c ds-main-redis-config.txt -m 1 -t 0 -l 5 --message-rate 1
else
    echo "STREAM_TYPE not set or invalid. Defaulting to kafka configuration..."
    echo -e "\nds main configs\n"
    cat $CONFIG_FILE
    ./metropolis_perception_app -c $CONFIG_FILE -m $M_PARAM -t 0 -l 5 --message-rate 1 --show-sensor-id
fi
