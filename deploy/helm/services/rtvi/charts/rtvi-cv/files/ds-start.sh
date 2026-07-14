#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Unified DeepStream perception entrypoint.

set -euo pipefail

export LD_LIBRARY_PATH=/usr/lib64${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}

DS_MODEL_FAMILY="${DS_MODEL_FAMILY:?DS_MODEL_FAMILY must be set (rtdetr-warehouse, rtdetr-gdino, sparse4d-warehouse)}"
STREAM_TYPE="${STREAM_TYPE:-kafka}"
DS_MODE_FLAG="${DS_MODE_FLAG:-1}"
DS_MESSAGE_RATE="${DS_MESSAGE_RATE:-1}"
DS_TRACKER_REID="${DS_TRACKER_REID:-false}"
DS_SHOW_SENSOR_ID="${DS_SHOW_SENSOR_ID:-false}"
DS_VISION_ENCODER="${DS_VISION_ENCODER:-false}"

DS_APP_DIR="/opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app"
DS_CONFIG_DIR="${DS_APP_DIR}/configs"
DS_MOUNTED_CONFIGS_DIR="${DS_APP_DIR}/mounted-configs"

_ARCH="$(uname -m)"
export GST_PLUGIN_PATH="/opt/nvidia/deepstream/deepstream/lib/gst-plugins:/usr/lib/${_ARCH}-linux-gnu/gstreamer-1.0/deepstream${GST_PLUGIN_PATH:+:${GST_PLUGIN_PATH}}"
unset _ARCH

build_extra_flags() {
    local flags=""
    [[ "$DS_TRACKER_REID" == "true" ]] && flags="$flags --tracker-reid"
    [[ "$DS_SHOW_SENSOR_ID" == "true" ]] && flags="$flags --show-sensor-id"
    echo "$flags"
}

require_file() {
    local file_path="$1"
    local hint="$2"
    if [[ ! -f "$file_path" ]]; then
        echo "ERROR: Required file not found: ${file_path}" >&2
        [[ -n "$hint" ]] && echo "Hint: ${hint}" >&2
        exit 1
    fi
}

resolve_config_file() {
    local default_file="$1"
    local configured_file="${DS_CONFIG_FILE:-$default_file}"
    if [[ "$configured_file" = /* ]]; then
        echo "$configured_file"
    else
        echo "${DS_CONFIG_DIR}/${configured_file}"
    fi
}

stage_mounted_configs_if_present() {
    local has_files=false
    if [[ -d "$DS_MOUNTED_CONFIGS_DIR" ]]; then
        shopt -s nullglob dotglob
        local mounted_entries=("$DS_MOUNTED_CONFIGS_DIR"/*)
        shopt -u nullglob dotglob
        if ((${#mounted_entries[@]} > 0)); then
            has_files=true
        fi
    fi

    if [[ "$has_files" == "true" ]]; then
        mkdir -p "$DS_CONFIG_DIR"
        cp -a "${DS_MOUNTED_CONFIGS_DIR}/." "${DS_CONFIG_DIR}/"
        echo "##### Staged profile configs from ${DS_MOUNTED_CONFIGS_DIR} -> ${DS_CONFIG_DIR} #####"
    fi
}

patch_vision_encoder_configs_if_enabled() {
    if [[ "$DS_VISION_ENCODER" != "true" ]]; then
        return
    fi

    local vision_encoder_model="${VISION_ENCODER_MODEL:?VISION_ENCODER_MODEL must be set when DS_VISION_ENCODER=true}"
    local vision_encoder_version="${VISION_ENCODER_VERSION:?VISION_ENCODER_VERSION must be set when DS_VISION_ENCODER=true}"
    local vision_encoder_storage="/opt/storage"
    local vision_encoder_onnx_file="${vision_encoder_model}_${vision_encoder_version}.onnx"
    local vision_encoder_tokenizer_dir="${vision_encoder_model}_${vision_encoder_version}_tokenizer"
    local onnx_path="${vision_encoder_storage}/${vision_encoder_onnx_file}"

    require_file "$onnx_path" "Expected ONNX artifact for DS_VISION_ENCODER=true; the model download init step may not have completed."

    for cfg in "${DS_CONFIG_DIR}/ds-main-config.txt" "${DS_CONFIG_DIR}/ds-main-redis-config.txt"; do
        [[ -f "$cfg" ]] || continue
        echo "##### Patching vision encoder paths in $(basename "$cfg") #####"
        sed -i "/^\[text-embedder\]/,/^\[/{s|^onnx-model-path=.*|onnx-model-path=${onnx_path}|;}" "$cfg"
        sed -i "/^\[text-embedder\]/,/^\[/{s|^tokenizer-dir=.*|tokenizer-dir=${vision_encoder_storage}/${vision_encoder_tokenizer_dir}/|;}" "$cfg"
        sed -i "/^\[visionencoder\]/,/^\[/{s|^onnx-model=.*|onnx-model=${onnx_path}|;}" "$cfg"
        sed -i "/^\[visionencoder\]/,/^\[/{s|^tensorrt-engine=.*|tensorrt-engine=${onnx_path}_batch16.plan|;}" "$cfg"
    done
}

start_rtdetr_warehouse()
{
    echo "##### RT-DETR Warehouse models will be used. #####"
    require_file "${DS_CONFIG_DIR}/ds-pgie-config.yml" "Verify model/config mounts for RT-DETR warehouse."
    cat "${DS_CONFIG_DIR}/ds-pgie-config.yml"

    local config_file
    config_file="$(resolve_config_file "ds-main-config.txt")"
    require_file "$config_file" "Set DS_CONFIG_FILE or ensure staged/in-image configs are present."
    local extra_flags
    extra_flags=$(build_extra_flags)

    cat "$config_file"
    echo "Application starting with this command: ./metropolis_perception_app -c $config_file -m $DS_MODE_FLAG -t 0 -l 5 --message-rate $DS_MESSAGE_RATE $extra_flags"
    exec ./metropolis_perception_app -c "$config_file" \
        -m "$DS_MODE_FLAG" -t 0 -l 5 \
        --message-rate "$DS_MESSAGE_RATE" \
        $extra_flags
}

start_rtdetr_gdino()
{
    echo "##### RT-DETR GDINO models will be used. #####"
    local config_file
    config_file="$(resolve_config_file "run_config-api-rtdetr-protobuf700.txt")"
    require_file "$config_file" "Set DS_CONFIG_FILE or ensure GDINO runtime config is available."
    NUM_SENSORS="${NUM_SENSORS:-30}"
    ENGINES_DIR="/opt/engines"
    mkdir -p "${ENGINES_DIR}/gdino" "${ENGINES_DIR}/rtdetr-its"
    GDINO_TRT_PLAN="${ENGINES_DIR}/gdino/model_gdino_trt.plan"

    require_file "models/rtdetr-its/resnet50_market1501.etlt" "Required tracker artifact missing from image/models volume."
    cp models/rtdetr-its/resnet50_market1501.etlt \
       /opt/nvidia/deepstream/deepstream/samples/models/Tracker/resnet50_market1501.etlt

    if [[ "${MODEL_NAME_2D:-}" == "GDINO" ]]; then
        require_file "/opt/storage/gdino/mgdino_mask_head_pruned_dynamic_batch.onnx" "GDINO ONNX model must be available in shared storage."

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
        sed -i "s/max_batch_size: [0-9]\+/max_batch_size: ${NUM_SENSORS}/" "${DS_CONFIG_DIR}/config_triton_nvinferserver_gdino.txt"

        for cfg in \
            /opt/nvidia/deepstream/deepstream/sources/TritonGdino/triton_model_repo/{ensemble_python_gdino,gdino_trt,gdino_postprocess,gdino_preprocess}/config.pbtxt; do
            [[ -f "$cfg" ]] && sed -i "s/^\s*max_batch_size\s*[:=]\s*[\"]*[0-9]\+[\"]*\s*$/max_batch_size: ${NUM_SENSORS}/" "$cfg"
        done

        DS_MODE_FLAG=4
    else
        DS_MODE_FLAG=7
        echo "##### RT-DETR model being used... #####"
        RTDETR_INFER_CONFIG="${DS_CONFIG_DIR}/rtdetr-960x544.txt"
        if [[ -f "$RTDETR_INFER_CONFIG" ]]; then
            sed -i "/^\[property\]/,/^\[/{s|^model-engine-file=.*|model-engine-file=${ENGINES_DIR}/rtdetr-its/model_epoch_035.fp16.onnx_b${NUM_SENSORS}_gpu0_fp16.engine|;}" "$RTDETR_INFER_CONFIG"
            sed -i "/^\[property\]/,/^\[/{s/^batch-size=.*/batch-size=${NUM_SENSORS}/;}" "$RTDETR_INFER_CONFIG"
        fi
        echo "##### RT-DETR nvinfer config updated successfully... #####"
        echo "##### Contents of $RTDETR_INFER_CONFIG: #####"
        cat "$RTDETR_INFER_CONFIG"
    fi

    sed -i "/^\[source-list\]/,/^\[/{s/^max-batch-size=.*/max-batch-size=${NUM_SENSORS}/;}" "$config_file"
    sed -i "/^\[streammux\]/,/^\[/{s/^batch-size=.*/batch-size=${NUM_SENSORS}/;}" "$config_file"
    sed -i "/^\[primary-gie\]/,/^\[/{s/^batch-size=.*/batch-size=${NUM_SENSORS}/;}" "$config_file"

    if [[ "${HARDWARE_PROFILE:-}" == "DGX-SPARK" || "${HARDWARE_PROFILE:-}" == "DGX-THOR" ]]; then
        echo "##### Setting msg-conv-msg2p-lib to libnvds_msgconv.so for sink1 group... #####"
        sed -i '/^\[sink1\]/,/^\[/{/^msg-conv-msg2p-lib=/d;}' "$config_file"
        sed -i '/^\[sink1\]/a msg-conv-msg2p-lib=/opt/nvidia/deepstream/deepstream/lib/libnvds_msgconv.so' "$config_file"
        sed -i '/^\[primary-gie\]/,/^\[/{s/^interval=.*/interval=1/;}' "$config_file"
    else
        echo "##### Setting msg-conv-msg2p-lib to libnvds_msgconv_mega2d.so for sink1 group... #####"
        sed -i '/^\[sink1\]/,/^\[/{/^msg-conv-msg2p-lib=/d;}' "$config_file"
        sed -i '/^\[sink1\]/a msg-conv-msg2p-lib=/opt/nvidia/deepstream/deepstream/lib/libnvds_msgconv_mega2d.so' "$config_file"
    fi

    TRACKER_CONFIG="/opt/nvidia/deepstream/deepstream/samples/configs/deepstream-app/config_tracker_NvDCF_accuracy.yml"

    if [[ "${HARDWARE_PROFILE:-}" == "DGX-THOR" ]]; then
        echo "##### Setting compute-hw=2 in tracker section of $config_file... #####"
        sed -i '/^\[tracker\]/,/^\[/{/^compute-hw=/d;}' "$config_file"
        sed -i '/^\[tracker\]/a compute-hw=2' "$config_file"
        echo "##### Setting low-latency-mode to 0 for source-list section... #####"
        sed -i '/^\[source-list\]/,/^\[/{/^low-latency-mode=/d;}' "$config_file"
        sed -i '/^\[source-list\]/a low-latency-mode=0' "$config_file"
        echo "##### Updating VisualTracker section in $TRACKER_CONFIG... #####"
        if [[ -f "$TRACKER_CONFIG" ]]; then
            sed -i '/^VisualTracker:/,/^[A-Z][a-zA-Z]*:/ {/^[[:space:]]*visualTrackerType:/d;}' "$TRACKER_CONFIG"
            sed -i '/^VisualTracker:/,/^[A-Z][a-zA-Z]*:/ {/^[[:space:]]*vpiBackend4DcfTracker:/d;}' "$TRACKER_CONFIG"
            sed -i '/^VisualTracker:/a \  visualTrackerType: 2' "$TRACKER_CONFIG"
            sed -i '/^[[:space:]]*visualTrackerType: 2/a \  vpiBackend4DcfTracker: 2' "$TRACKER_CONFIG"
            sed -i '/^TargetManagement:/,/^[A-Z][a-zA-Z]*:/ {s/^[[:space:]]*maxTargetsPerStream:.*/  maxTargetsPerStream: 50/;}' "$TRACKER_CONFIG"
            echo "##### Updated maxTargetsPerStream to 50 in TargetManagement section... #####"
            echo "##### Contents of $TRACKER_CONFIG: #####"
            cat "$TRACKER_CONFIG"
        fi
    fi

    echo "##### Updating minTrackerConfidence in $TRACKER_CONFIG... #####"
    if [[ -f "$TRACKER_CONFIG" ]]; then
        sed -i '/^TargetManagement:/,/^[A-Z][a-zA-Z]*:/ {s/^[[:space:]]*minTrackerConfidence:.*/  minTrackerConfidence: 0.2513/;}' "$TRACKER_CONFIG"
        echo "##### Updated minTrackerConfidence in $TRACKER_CONFIG... #####"
    else
        echo "Warning: Tracker config $TRACKER_CONFIG not found, skipping minTrackerConfidence update..."
    fi

    echo "##### Contents of $TRACKER_CONFIG: #####"
    cat "$TRACKER_CONFIG"

    local extra_flags
    extra_flags=$(build_extra_flags)

    cat "$config_file"
    echo "Application starting with this command: ./metropolis_perception_app -c $config_file -m $DS_MODE_FLAG -t 0 -l 5 --message-rate $DS_MESSAGE_RATE $extra_flags"
    exec ./metropolis_perception_app -c "$config_file" \
        -m "$DS_MODE_FLAG" -t 0 -l 5 \
        --message-rate "$DS_MESSAGE_RATE" \
        $extra_flags
}

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
    cd "$DS_APP_DIR"

    local config_file
    config_file="$(resolve_config_file "ds-main-config.txt")"
    require_file "$config_file" "Set DS_CONFIG_FILE or ensure Sparse4D config exists."

    cat "$config_file"
    echo "Application starting with this command: ./metropolis_perception_app -c $config_file -m $DS_MODE_FLAG -l 5"
    exec ./metropolis_perception_app -c "$config_file" -m "$DS_MODE_FLAG" -l 5
}

echo "===== DeepStream Perception ====="
echo "DS_MODEL_FAMILY=$DS_MODEL_FAMILY  STREAM_TYPE=$STREAM_TYPE  DS_MODE_FLAG=$DS_MODE_FLAG"
echo "DS_VISION_ENCODER=$DS_VISION_ENCODER"

stage_mounted_configs_if_present
patch_vision_encoder_configs_if_enabled

case "$DS_MODEL_FAMILY" in
    rtdetr-warehouse)       start_rtdetr_warehouse ;;
    rtdetr-gdino)           start_rtdetr_gdino ;;
    sparse4d-warehouse)     start_sparse4d_warehouse ;;
    *) echo "Unknown DS_MODEL_FAMILY: $DS_MODEL_FAMILY"; exit 1 ;;
esac
