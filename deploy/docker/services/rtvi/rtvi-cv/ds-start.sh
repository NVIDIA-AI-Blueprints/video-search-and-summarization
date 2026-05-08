#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Unified DeepStream perception entrypoint.
# Dispatches based on DS_MODEL_FAMILY env var: cnn, rtdetr, sparse4d.

set -euo pipefail

DS_MODEL_FAMILY="${DS_MODEL_FAMILY:?DS_MODEL_FAMILY must be set (cnn, rtdetr, sparse4d)}"
STREAM_TYPE="${STREAM_TYPE:-kafka}"
DS_MODE_FLAG="${DS_MODE_FLAG:-1}"
DS_MESSAGE_RATE="${DS_MESSAGE_RATE:-1}"
DS_TRACKER_REID="${DS_TRACKER_REID:-false}"
DS_SHOW_SENSOR_ID="${DS_SHOW_SENSOR_ID:-false}"

# Shared: build extra flags from env vars
build_extra_flags() {
    local flags=""
    [[ "$DS_TRACKER_REID" == "true" ]] && flags="$flags --tracker-reid"
    [[ "$DS_SHOW_SENSOR_ID" == "true" ]] && flags="$flags --show-sensor-id"
    echo "$flags"
}

# ---------------------------------------------------------------------------
# CNN family (warehouse-2d, search)
# ---------------------------------------------------------------------------
start_rtdetr_warehouse()
{
    echo "##### RT-DETR Warehouse models will be used. #####"
    cat /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/configs/ds-pgie-config.yml

    local config_file="/opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/configs/ds-main-config.txt"
    local extra_flags
    extra_flags=$(build_extra_flags)

    cat "$config_file"
    echo "Application starting with this command: ./metropolis_perception_app -c "$config_file" -m "$DS_MODE_FLAG" -t 0 -l 5 --message-rate "$DS_MESSAGE_RATE" $extra_flags"
    exec ./metropolis_perception_app -c "$config_file" \
        -m "$DS_MODE_FLAG" -t 0 -l 5 \
        --message-rate "$DS_MESSAGE_RATE" \
        $extra_flags
}

# ---------------------------------------------------------------------------
# RTDetr + GDINO family (alerts, smartcities)
# ---------------------------------------------------------------------------
start_rtdetr_gdino()
{
    echo "##### RT-DETR GDINO models will be used. #####"
    mkdir -p /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/configs
    cp /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/mounted-configs/cfg_kafka.txt /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/configs/cfg_kafka.txt
    cp /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/mounted-configs/coco_classmap.txt /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/configs/coco_classmap.txt
    cp /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/mounted-configs/rtdetr-960x544.txt /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/configs/rtdetr-960x544.txt
    cp /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/mounted-configs/rtdetr-960x544-labels.txt /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/configs/rtdetr-960x544-labels.txt
    cp /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/mounted-configs/config_triton_nvinferserver_gdino.txt /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/configs/config_triton_nvinferserver_gdino.txt
    cp /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/mounted-configs/run_config-api-rtdetr-protobuf.txt /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/configs/run_config-api-rtdetr-protobuf.txt

    local config_file="${DS_CONFIG_FILE:-run_config-api-rtdetr-protobuf700.txt}"
    NUM_SENSORS="${NUM_SENSORS:-30}"
    ENGINES_DIR="/opt/engines"
    mkdir -p "${ENGINES_DIR}/gdino" "${ENGINES_DIR}/rtdetr-its"
    GDINO_TRT_PLAN="${ENGINES_DIR}/gdino/model_gdino_trt.plan"

    cp models/rtdetr-its/resnet50_market1501.etlt \
       /opt/nvidia/deepstream/deepstream/samples/models/Tracker/resnet50_market1501.etlt

    if [[ "${MODEL_NAME_2D:-}" == "GDINO" ]]; then

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

        sed -i '/^\[primary-gie\]/,/^\[/{s|config-file=.*|config-file= /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/configs/config_triton_nvinferserver_gdino.txt|;}' "$config_file"
        sed -i '\#config-file= /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/configs/config_triton_nvinferserver_gdino.txt#a plugin-type=1' "$config_file"
        sed -i "s/max_batch_size: [0-9]\+/max_batch_size: ${NUM_SENSORS}/" /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/configs/config_triton_nvinferserver_gdino.txt

        for cfg in \
            /opt/nvidia/deepstream/deepstream/sources/TritonGdino/triton_model_repo/{ensemble_python_gdino,gdino_trt,gdino_postprocess,gdino_preprocess}/config.pbtxt; do
            [[ -f "$cfg" ]] && sed -i "s/^\s*max_batch_size\s*[:=]\s*[\"]*[0-9]\+[\"]*\s*$/max_batch_size: ${NUM_SENSORS}/" "$cfg"
        done

        DS_MODE_FLAG=4
    else
        DS_MODE_FLAG=7
        echo "##### RT-DETR model being used... #####"
        # RT-DETR nvinfer config: engine filename uses b<NUM_SENSORS> (e.g. b4, b8, b30)
        RTDETR_INFER_CONFIG="/opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/configs/rtdetr-960x544.txt"
        if [[ -f "$RTDETR_INFER_CONFIG" ]]; then
            sed -i "/^\[property\]/,/^\[/{s|^model-engine-file=.*|model-engine-file=${ENGINES_DIR}/rtdetr-its/model_epoch_035.fp16.onnx_b${NUM_SENSORS}_gpu0_fp16.engine|;}" "$RTDETR_INFER_CONFIG"
            sed -i "/^\[property\]/,/^\[/{s/^batch-size=.*/batch-size=${NUM_SENSORS}/;}" "$RTDETR_INFER_CONFIG"
        fi
        echo "##### RT-DETR nvinfer config updated successfully... #####"
        echo "##### Contents of $RTDETR_INFER_CONFIG: #####"
        cat $RTDETR_INFER_CONFIG
    fi

    sed -i "/^\[source-list\]/,/^\[/{s/^max-batch-size=.*/max-batch-size=${NUM_SENSORS}/;}" "$config_file"
    sed -i "/^\[streammux\]/,/^\[/{s/^batch-size=.*/batch-size=${NUM_SENSORS}/;}" "$config_file"
    sed -i "/^\[primary-gie\]/,/^\[/{s/^batch-size=.*/batch-size=${NUM_SENSORS}/;}" "$config_file"

    if [[ "${HARDWARE_PROFILE:-}" == "DGX-SPARK" || "${HARDWARE_PROFILE:-}" == "DGX-THOR" ]]; then
        # Replace or add msg-conv-msg2p-lib property in sink1 group
        echo "##### Setting msg-conv-msg2p-lib to libnvds_msgconv.so for sink1 group... #####"
        # First, remove any existing msg-conv-msg2p-lib line within [sink1] section
        sed -i '/^\[sink1\]/,/^\[/{/^msg-conv-msg2p-lib=/d;}' "$config_file"
        # Then add the new property after [sink1]
        sed -i '/^\[sink1\]/a msg-conv-msg2p-lib=/opt/nvidia/deepstream/deepstream/lib/libnvds_msgconv.so' "$config_file"
        # Set [primary-gie] interval=1 in $config_file
        sed -i '/^\[primary-gie\]/,/^\[/{s/^interval=.*/interval=1/;}' "$config_file"
    else
        # Replace or add msg-conv-msg2p-lib property in sink1 group
        echo "##### Setting msg-conv-msg2p-lib to libnvds_msgconv_mega2d.so for sink1 group... #####"
        # First, remove any existing msg-conv-msg2p-lib line within [sink1] section
        sed -i '/^\[sink1\]/,/^\[/{/^msg-conv-msg2p-lib=/d;}' "$config_file"
        # Then add the new property after [sink1]
        sed -i '/^\[sink1\]/a msg-conv-msg2p-lib=/opt/nvidia/deepstream/deepstream/lib/libnvds_msgconv_mega2d.so' "$config_file"
    fi

    if [[ "${HARDWARE_PROFILE:-}" == "DGX-THOR" ]]; then
        # Set compute-hw=2 under tracker section in config_file
        echo "##### Setting compute-hw=2 in tracker section of $config_file... #####"
        sed -i '/^\[tracker\]/,/^\[/{/^compute-hw=/d;}' "$config_file"
        sed -i '/^\[tracker\]/a compute-hw=2' "$config_file"
        # Replace or add low-latency-mode property in source-list section
        echo "##### Setting low-latency-mode to 0 for source-list section... #####"
        # Remove any existing low-latency-mode line within [source-list] section
        sed -i '/^\[source-list\]/,/^\[/{/^low-latency-mode=/d;}' "$config_file"
        # Then add the new property after [source-list]
        sed -i '/^\[source-list\]/a low-latency-mode=0' "$config_file"
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
            cat "$TRACKER_CONFIG"
        fi
    fi

    cat "$config_file"
    echo "Application starting with this command: ./metropolis_perception_app -c "$config_file" -m "$DS_MODE_FLAG" -t 0 -l 5 --message-rate "$DS_MESSAGE_RATE" --show-sensor-id"
    exec ./metropolis_perception_app -c "$config_file" \
        -m "$DS_MODE_FLAG" -t 0 -l 5 \
        --message-rate "$DS_MESSAGE_RATE" \
        --show-sensor-id
}

# ---------------------------------------------------------------------------
# Sparse4D family (warehouse-3d)
# ---------------------------------------------------------------------------
start_sparse4d_warehouse()
{
    echo "##### Sparse4D Warehouse models will be used. #####"
    cd /opt/nvidia/deepstream/deepstream/sources/sparse4d/configs

    if [ "${HARDWARE_PROFILE:-}" = "DGX-SPARK" ]; then
        export PATH=/usr/src/tensorrt/bin:$PATH
    fi
    export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}:$CUSTOM_LIB_PATH"
    export LD_PRELOAD="${LD_PRELOAD:-}:$CUSTOM_PRELOAD_LIB"

    bash sparse4d_setup.sh

    cd /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app

    local config_file="/opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/configs/ds-main-config.txt"

    cat "$config_file"
    echo "Application starting with this command: ./metropolis_perception_app -c "$config_file" -m "$DS_MODE_FLAG" -l 5"
    exec ./metropolis_perception_app -c "$config_file" -m "$DS_MODE_FLAG" -l 5
}

# ---------------------------------------------------------------------------
echo "===== DeepStream Perception ====="
echo "DS_MODEL_FAMILY=$DS_MODEL_FAMILY  STREAM_TYPE=$STREAM_TYPE  DS_MODE_FLAG=$DS_MODE_FLAG"

case "$DS_MODEL_FAMILY" in
    rtdetr-warehouse)       start_rtdetr_warehouse ;;
    rtdetr-gdino)           start_rtdetr_gdino ;;
    sparse4d-warehouse)     start_sparse4d_warehouse ;;
    *)        echo "Unknown DS_MODEL_FAMILY: $DS_MODEL_FAMILY"; exit 1 ;;
esac
