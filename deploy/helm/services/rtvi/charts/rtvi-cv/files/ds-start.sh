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

DS_APP_DIR="${DS_APP_DIR:-/opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app}"
DS_CONFIG_DIR="${DS_APP_DIR}/configs"
DS_MOUNTED_CONFIGS_DIR="${DS_APP_DIR}/mounted-configs"

_ARCH="$(uname -m)"
export GST_PLUGIN_PATH="/opt/nvidia/deepstream/deepstream/lib/gst-plugins:/usr/lib/${_ARCH}-linux-gnu/gstreamer-1.0/deepstream${GST_PLUGIN_PATH:+:${GST_PLUGIN_PATH}}"
unset _ARCH

# HARDWARE_PROFILE names each Thor board separately (IGX-THOR, AGX-THOR,
# DGX-THOR, ...) but the DeepStream tuning below is identical across the
# family. Match on the family the same way dev-profile.sh canonicalizes it
# (case-insensitive *thor*) so a new board name needs no change here.
is_thor_profile() {
    local profile="${HARDWARE_PROFILE:-}"
    [[ "${profile,,}" == *thor* ]]
}

build_extra_flags() {
    local flags=""
    [[ "$DS_TRACKER_REID" == "true" ]] && flags="$flags --tracker-reid"
    [[ "$DS_SHOW_SENSOR_ID" == "true" ]] && flags="$flags --show-sensor-id"
    echo "$flags"
}

require_file() {
    local file_path="$1"
    local hint="${2:-}"
    if [[ ! -f "$file_path" ]]; then
        echo "ERROR: Required file not found: ${file_path}" >&2
        [[ -n "$hint" ]] && echo "Hint: ${hint}" >&2
        exit 1
    fi
}

# Phase 0: manifest-driven NGC model acquisition (replaces Compose/Helm download init).
# DS_MODEL_DOWNLOAD=never skips; auto skips when no manifest is mounted.
ensure_models_from_manifest() {
    local mode="${DS_MODEL_DOWNLOAD:-auto}"
    [[ "$mode" == "never" ]] && return 0

    local manifest="${MODELS_MANIFEST_PATH:-}"
    if [[ -z "$manifest" || ! -f "$manifest" ]]; then
        if [[ "$mode" == "auto" ]]; then
            return 0
        fi
        echo "ERROR: MODELS_MANIFEST_PATH must point to an existing manifest when DS_MODEL_DOWNLOAD=${mode}" >&2
        exit 1
    fi

    if [[ "$(id -u)" -ne 0 ]]; then
        echo "ERROR: model download requires root (Option A); start the container as UID 0" >&2
        exit 1
    fi

    local script="${DOWNLOAD_MODELS_SCRIPT:-}"
    if [[ -z "$script" || ! -f "$script" ]]; then
        for candidate in /opt/scripts/download-models.sh /startup-script/download-models.sh; do
            if [[ -f "$candidate" ]]; then
                script="$candidate"
                break
            fi
        done
    fi
    if [[ -z "$script" || ! -f "$script" ]]; then
        echo "ERROR: download-models.sh not found (expected /opt/scripts or /startup-script)" >&2
        exit 1
    fi

    echo "##### Model download phase (manifest=${manifest}, script=${script}) #####"
    bash "$script"
}

# Append DeepStream samples/<subdir> dirs under both the deepstream/ entry
# point and any versioned deepstream-N.M/ tree (same layout split as Tracker).
# Caller passes a nameref array name; keeps failures in-process under set -e.
append_deepstream_sample_dirs() {
    local subdir="$1"
    local -n _dirs_ref="$2"
    local canonical="/opt/nvidia/deepstream/deepstream/samples/${subdir}"
    local d canonical_real other_real

    _dirs_ref+=("$canonical")
    canonical_real="$(readlink -f "$canonical" 2>/dev/null || true)"

    shopt -s nullglob
    for d in /opt/nvidia/deepstream/deepstream-[0-9]*/samples/"${subdir}"; do
        [[ -d "$d" ]] || continue
        other_real="$(readlink -f "$d" 2>/dev/null || true)"
        # Deduplicate only when both resolve to the same path; empty readlink
        # results must not look like a match.
        if [[ -n "$canonical_real" && -n "$other_real" && "$canonical_real" == "$other_real" ]]; then
            continue
        fi
        _dirs_ref+=("$d")
    done
    shopt -u nullglob
}

# Option A: after the privilege drop the app is STORAGE_UID, but several
# image-owned DeepStream paths remain unwritable (streams HTTP download dir,
# Tracker engine dir, app cwd) and HOME still points at /root. Prepare those
# once here — same contract as download-models.sh / setup-tracker-reid.sh.
prepare_runtime_user_environment() {
    local uid="${STORAGE_UID:-1001}"
    local gid="${STORAGE_GID:-1001}"
    local runtime_home="${RTVI_CV_RUNTIME_HOME:-/tmp/rtvi-cv-home}"
    local -a runtime_dirs=()
    local d

    # Redirect HOME/cache before setpriv so the dropped process inherits them
    # (setpriv does not rewrite HOME). Avoids dconf/GStreamer writes under /root.
    mkdir -p "${runtime_home}/.cache/gstreamer-1.0"
    export HOME="${runtime_home}"
    export XDG_CACHE_HOME="${runtime_home}/.cache"
    export GST_REGISTRY="${runtime_home}/.cache/gstreamer-1.0/registry.bin"

    if [[ "$(id -u)" -ne 0 ]]; then
        echo "##### Skipping runtime dir ownership (not root); HOME=${HOME} #####"
        return 0
    fi

    chown -R "${uid}:${gid}" "${runtime_home}"

    # Non-recursive: streams accumulates downloaded videos; only the dir must
    # be writable so fopen(..., "wb") for HTTP_DOWNLOAD_DIR succeeds.
    append_deepstream_sample_dirs streams runtime_dirs
    append_deepstream_sample_dirs models/Tracker runtime_dirs
    runtime_dirs+=("${DS_APP_DIR}")
    for d in "${runtime_dirs[@]}"; do
        [[ -n "$d" ]] || continue
        install -d -m 0755 "$d"
        chown "${uid}:${gid}" "$d"
        echo "##### Prepared runtime-writable dir ${d} -> ${uid}:${gid} #####"
    done
}

# Supplementary groups the runtime user needs for GPU access. Tegra ships
# /dev/nvmap and /dev/nvhost-* group-restricted, so --clear-groups costs the
# app CUDA entirely (NvRmMemInitNvmap "Permission denied" -> cudaErrorNoDevice).
# gids are read off the injected nodes — names/numbers differ across L4T/SBSA/x86.
# A supplementary gid 0 is legitimate: group access to a root:root 0660 node
# without granting uid 0.
collect_gpu_device_gids() {
    local -n _gids_ref="$1"
    local node gid seen=" "

    shopt -s nullglob
    for node in /dev/nvmap /dev/nvhost-* /dev/nvgpu/*/* /dev/nvsciipc* \
                /dev/nvidia[0-9]* /dev/nvidiactl /dev/nvidia-uvm*; do
        [[ -c "$node" || -b "$node" ]] || continue
        gid="$(stat -c '%g' "$node" 2>/dev/null || true)"
        [[ -n "$gid" ]] || continue
        if [[ "$seen" == *" ${gid} "* ]]; then
            continue
        fi
        seen+="${gid} "
        _gids_ref+=("$gid")
    done
    shopt -u nullglob
}

# Confirm the dropped identity can open the GPU device nodes. Used to decide
# between --clear-groups (x86 / world-accessible) and --groups (Tegra).
# Quiet on failure — the caller logs once when neither drop path works.
runtime_user_can_reach_gpu() {
    local -a priv_opts=("$@")
    local node

    for node in /dev/nvmap /dev/nvidiactl; do
        [[ -c "$node" ]] || continue
        if ! setpriv "${priv_opts[@]}" -- test -r "$node" ||
           ! setpriv "${priv_opts[@]}" -- test -w "$node"; then
            return 1
        fi
    done
    return 0
}

# Drop to STORAGE_UID/GID before exec'ing the perception binary (Option A).
# Prefer --clear-groups (prior x86 path). Only grant device groups when that
# leaves the GPU unreachable. RTVI_CV_PRIVILEGE_DROP: auto (default; fall back
# to root if neither drop works), force (exit 1 instead), off (never drop).
# Override via env / overrides.env — no compose wiring required.
exec_as_runtime_user() {
    local uid="${STORAGE_UID:-1001}"
    local gid="${STORAGE_GID:-1001}"
    local mode="${RTVI_CV_PRIVILEGE_DROP:-auto}"
    local groups_csv="" g node
    local -a gpu_gids=() priv_opts=() supp_opts=()

    prepare_runtime_user_environment
    if [[ "$(id -u)" -ne 0 ]]; then
        exec "$@"
    fi
    if [[ "$mode" == "off" ]]; then
        echo "##### RTVI_CV_PRIVILEGE_DROP=off; running the application as root #####"
        exec "$@"
    fi

    collect_gpu_device_gids gpu_gids
    if [[ ${#gpu_gids[@]} -gt 0 ]]; then
        groups_csv="$(IFS=,; echo "${gpu_gids[*]}")"
    fi

    if command -v setpriv >/dev/null 2>&1; then
        # Path 1: identical to pre-Tegra Option A. Succeeds on x86/SBSA where
        # /dev/nvidia* is typically world-accessible.
        priv_opts=(--reuid="$uid" --regid="$gid" --clear-groups)
        if runtime_user_can_reach_gpu "${priv_opts[@]}"; then
            echo "##### Dropping privileges to ${uid}:${gid} before application exec #####"
            exec setpriv "${priv_opts[@]}" -- "$@"
        fi

        # Path 2: Tegra — grant the gids that own the injected device nodes.
        if [[ -n "$groups_csv" ]]; then
            priv_opts=(--reuid="$uid" --regid="$gid" --groups "$groups_csv")
            if runtime_user_can_reach_gpu "${priv_opts[@]}"; then
                echo "##### Dropping privileges to ${uid}:${gid} (GPU groups: ${groups_csv}) before application exec #####"
                exec setpriv "${priv_opts[@]}" -- "$@"
            fi
        fi

        if [[ "$mode" == "force" ]]; then
            echo "ERROR: RTVI_CV_PRIVILEGE_DROP=force but ${uid}:${gid} cannot access the GPU devices" >&2
            for node in /dev/nvmap /dev/nvidiactl; do
                [[ -c "$node" ]] && ls -ld "$node" >&2 || true
            done
            exit 1
        fi
        echo "##### WARNING: ${uid}:${gid} cannot access the GPU devices; running as root instead. #####" >&2
        echo "#####          Files written to mounted volumes will be root-owned. #####" >&2
        for node in /dev/nvmap /dev/nvidiactl; do
            [[ -c "$node" ]] && ls -ld "$node" >&2 || true
        done
        exec "$@"
    fi

    # Fallbacks when setpriv is absent. runuser can take -G; gosu cannot.
    echo "##### Dropping privileges to ${uid}:${gid} before application exec #####"
    if command -v runuser >/dev/null 2>&1; then
        if [[ ${#gpu_gids[@]} -eq 0 ]]; then
            exec runuser -u "#${uid}" -g "#${gid}" -- "$@"
        fi
        for g in "${gpu_gids[@]}"; do
            supp_opts+=(-G "$g")
        done
        exec runuser -u "#${uid}" -g "#${gid}" "${supp_opts[@]}" -- "$@"
    fi
    if command -v gosu >/dev/null 2>&1; then
        exec gosu "${uid}:${gid}" "$@"
    fi
    echo "ERROR: no privilege-drop tool found (need setpriv, runuser, or gosu)" >&2
    exit 1
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
        cp -rL --no-preserve=all "${DS_MOUNTED_CONFIGS_DIR}/." "${DS_CONFIG_DIR}/"
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

    require_file "$onnx_path" "Expected ONNX artifact for DS_VISION_ENCODER=true; ensure_models_from_manifest may not have completed."

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
    echo "Application starting with this command: ./metropolis_perception_app -c $config_file -m $DS_MODE_FLAG -t 0 -l 5 --message-rate $DS_MESSAGE_RATE ${extra_flags:-}"
    exec_as_runtime_user ./metropolis_perception_app -c "$config_file" \
        -m "$DS_MODE_FLAG" -t 0 -l 5 \
        --message-rate "$DS_MESSAGE_RATE" \
        ${extra_flags:-}
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

    # NvDCF_accuracy ReID etlt. Prefer the copy download-models.sh fetched into
    # the shared model root; fall back to the copy bundled inside the image.
    #
    # The NGC image ships this under DS_APP_DIR/models. GHCR-built images do not
    # ship models at all, so there it arrives only via the models-download.json
    # manifest (nvidia/tao/reidentificationnet:deployable_v1.0). Checking the
    # download first means one code path serves both, and a profile that has
    # pinned an older in-image copy still gets the manifest's version.
    local reid_src=""
    local reid_cand
    for reid_cand in \
        "${MODELS_DEST_ROOT:-/opt/storage}/rtdetr-its/resnet50_market1501.etlt" \
        "${DS_APP_DIR}/models/rtdetr-its/resnet50_market1501.etlt"; do
        if [[ -f "$reid_cand" ]]; then
            reid_src="$reid_cand"
            break
        fi
    done
    if [[ -z "$reid_src" ]]; then
        echo "ERROR: ReID model resnet50_market1501.etlt not found." >&2
        echo "Hint: add nvidia/tao/reidentificationnet:deployable_v1.0 to this profile's" >&2
        echo "      models-download.json (destPath rtdetr-its/resnet50_market1501.etlt)," >&2
        echo "      or mount an image that bundles it under DS_APP_DIR/models." >&2
        exit 1
    fi
    echo "##### ReID model: ${reid_src} #####"
    ENGINE_CACHE_DIR="${ENGINE_CACHE_DIR:-/opt/engines}"
    export ENGINE_CACHE_DIR STORAGE_UID STORAGE_GID
    bash "${SETUP_TRACKER_REID_SCRIPT:-/startup-script/setup-tracker-reid.sh}" --src "$reid_src"

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

        # The path handed to the app and the file patched below must stay the same file,
        # so both derive from DS_CONFIG_DIR; splitting them silently drops the batch-size
        # patch whenever DS_APP_DIR is overridden.
        local gdino_triton_config="${DS_CONFIG_DIR}/config_triton_nvinferserver_gdino.txt"
        sed -i "/^\[primary-gie\]/,/^\[/{s|config-file=.*|config-file= ${gdino_triton_config}|;}" "$config_file"
        sed -i "\#config-file= ${gdino_triton_config}#a plugin-type=1" "$config_file"
        sed -i "s/max_batch_size: [0-9]\+/max_batch_size: ${NUM_SENSORS}/" "$gdino_triton_config"

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
            echo "##### RT-DETR nvinfer config updated successfully... #####"
            echo "##### Contents of $RTDETR_INFER_CONFIG: #####"
            cat "$RTDETR_INFER_CONFIG"
        else
            echo "Warning: RT-DETR infer config $RTDETR_INFER_CONFIG not found, skipping..."
        fi
    fi

    sed -i "/^\[source-list\]/,/^\[/{s/^max-batch-size=.*/max-batch-size=${NUM_SENSORS}/;}" "$config_file"
    sed -i "/^\[streammux\]/,/^\[/{s/^batch-size=.*/batch-size=${NUM_SENSORS}/;}" "$config_file"
    sed -i "/^\[primary-gie\]/,/^\[/{s/^batch-size=.*/batch-size=${NUM_SENSORS}/;}" "$config_file"

    if [[ "${HARDWARE_PROFILE:-}" == "DGX-SPARK" ]] || is_thor_profile; then
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

    if is_thor_profile; then
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

    if [[ -f "$TRACKER_CONFIG" ]]; then
        echo "##### Contents of $TRACKER_CONFIG: #####"
        cat "$TRACKER_CONFIG"
    fi

    cat "$config_file"
    echo "Application starting with this command: ./metropolis_perception_app -c "$config_file" -m "$DS_MODE_FLAG" -t 0 -l 5 --message-rate "$DS_MESSAGE_RATE" --show-sensor-id"
    exec_as_runtime_user ./metropolis_perception_app -c "$config_file" \
        -m "$DS_MODE_FLAG" -t 0 -l 5 \
        --message-rate "$DS_MESSAGE_RATE" \
        --show-sensor-id
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
    exec_as_runtime_user ./metropolis_perception_app -c "$config_file" -m "$DS_MODE_FLAG" -l 5
}

echo "===== DeepStream Perception ====="
echo "DS_MODEL_FAMILY=$DS_MODEL_FAMILY  STREAM_TYPE=$STREAM_TYPE  DS_MODE_FLAG=$DS_MODE_FLAG"
echo "DS_VISION_ENCODER=$DS_VISION_ENCODER"

ensure_models_from_manifest
stage_mounted_configs_if_present
patch_vision_encoder_configs_if_enabled

case "$DS_MODEL_FAMILY" in
    rtdetr-warehouse)       start_rtdetr_warehouse ;;
    rtdetr-gdino)           start_rtdetr_gdino ;;
    sparse4d-warehouse)     start_sparse4d_warehouse ;;
    *) echo "Unknown DS_MODEL_FAMILY: $DS_MODEL_FAMILY"; exit 1 ;;
esac
