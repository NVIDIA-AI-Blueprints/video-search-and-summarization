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

if [[ $MODEL_NAME_2D == "GDINO" ]]; then
    cp /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/models/gdino/*.onnx /opt/storage/
fi

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

echo "##### Batch size configurations updated successfully in $CONFIG_FILE... #####"

if [[ $MODEL_NAME_2D == "GDINO" ]]; then
    echo "##### Building engine file for /opt/storage/mgdino_mask_head_pruned_dynamic_batch.onnx ... #####"
    /usr/src/tensorrt/bin/trtexec --onnx=/opt/storage/mgdino_mask_head_pruned_dynamic_batch.onnx \
    --minShapes=inputs:1x3x544x960,input_ids:1x256,attention_mask:1x256,position_ids:1x256,token_type_ids:1x256,text_token_mask:1x256x256 \
    --optShapes=inputs:1x3x544x960,input_ids:1x256,attention_mask:1x256,position_ids:1x256,token_type_ids:1x256,text_token_mask:1x256x256 \
    --maxShapes=inputs:${NUM_SENSORS}x3x544x960,input_ids:${NUM_SENSORS}x256,attention_mask:${NUM_SENSORS}x256,position_ids:${NUM_SENSORS}x256,token_type_ids:${NUM_SENSORS}x256,text_token_mask:${NUM_SENSORS}x256x256 \
    --useCudaGraph \
    --fp16 \
    --saveEngine=/opt/storage/model_gdino_trt.plan
    cp /opt/storage/model_gdino_trt.plan /opt/nvidia/deepstream/deepstream/sources/TritonGdino/triton_model_repo/gdino_trt/1/model.plan
    echo "##### Engine file for /opt/storage/mgdino_mask_head_pruned_dynamic_batch.onnx  built successfully... #####"
    
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
