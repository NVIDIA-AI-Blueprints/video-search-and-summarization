#!/bin/bash
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

SCRIPT_DIR=$(dirname $0)

if [ "$#" -ne 4 ]; then
    echo "Error: Exactly four arguments required."
    echo "Usage: $(basename $0) <vila-1.5-model-dir> <max-batch-size> <fp16|int8> <output-dir>"
    exit 1
fi

# First argument should be a valid directory containing the HF/pytorch model
if [ ! -d "$1" ]; then
    echo "Error: $1 is not a valid directory"
    echo "Usage: $(basename $0) <vila-1.5-model-dir> <max-batch-size> <fp16|int8> <output-dir>"
    exit 1
fi

# Check if the second argument is a number
if ! [[ "$2" =~ ^[0-9]+$ ]]; then
    echo "Error: $2 is not a valid number"
    echo "Usage: $(basename $0) <vila-1.5-model-dir> <max-batch-size> <fp16|int8> <output-dir>"
    exit 1
fi

MODEL_DIR="$1"
BATCH_SIZE=$2
MODE=$3
OUTPUT_DIR="$4"

CONVERT_SCRIPT="convert_checkpoint.py"

# Third argument should be "int8/fp16" mode
if [[ "$MODE" == "int8" ]]; then
    echo "Selecting INT8 mode"
    DIR=int8
    CONVERT_EXTRA_ARGS="--use_weight_only --weight_only_precision int8"
    TRTLLM_BUILD_EXTRA_ARGS=""
    export TRTLLM_DISABLE_UNIFIED_CONVERTER=1
elif [[ "$MODE" == "fp16" ]]; then
    echo "Selecting FP16 mode"
    DIR=fp16
    CONVERT_EXTRA_ARGS=""
    TRTLLM_BUILD_EXTRA_ARGS="--use_fused_mlp enable"
    export TRTLLM_DISABLE_UNIFIED_CONVERTER=1
elif [[ "$MODE" == "fp8" ]]; then
    echo "Selecting FP8 mode"
    DIR=fp16
    CONVERT_EXTRA_ARGS="--qformat fp8 --calib_size 32 --kv_cache_dtype fp8"
    TRTLLM_BUILD_EXTRA_ARGS=""
    CONVERT_SCRIPT="quantize.py"
elif [[ "$MODE" == "int4_awq" ]]; then
    echo "Selecting INT4 AWQ mode"
    DIR=fp16
    CONVERT_EXTRA_ARGS="--qformat int4_awq --calib_size 32 --batch_size 8"
    TRTLLM_BUILD_EXTRA_ARGS=""
    CONVERT_SCRIPT="quantize.py"
else
    echo "Error: Mode $MODE is not one of \"fp16\", \"int8\" or \"int4_awq\""
    echo "Usage: $(basename $0) <vila-1.5-model-dir> <max-batch-size> <fp16|int8|int4_awq> <output-dir>"
    exit 1
fi

export PYTHONPATH="$(dirname $SCRIPT_DIR)/VILA"

TMP_CONV_DIR="$(mktemp -d)"
mkdir -p "${OUTPUT_DIR}"

TMP_SYMLINK="$(mktemp /tmp/tmp.vila.XXXXXXXX)"
ln -sf $MODEL_DIR $TMP_SYMLINK
MODEL_DIR=$TMP_SYMLINK

if [ ! -z "$VILA_LORA_PATH" ]; then
    TRTLLM_BUILD_EXTRA_ARGS+=" --lora_plugin float16 --lora_target_modules attn_q attn_k attn_v mlp_4h_to_h mlp_h_to_4h mlp_gate attn_dense "
fi

# Convert sharded checkpoints into a single checkpoint file
echo "Converting Checkpoint ..."
if ! python3 "${SCRIPT_DIR}/${CONVERT_SCRIPT}" --model_dir "${MODEL_DIR}" \
    --output_dir "$TMP_CONV_DIR" --dtype float16 $CONVERT_EXTRA_ARGS ; then
        echo "ERROR: Failed to convert checkpoint"
        rm -rf $TMP_CONV_DIR
        rm $TMP_SYMLINK
        exit 1
fi

rm -rf "${OUTPUT_DIR}"

# TRT-LLM does not understand the arch LitaLlamaForCausalLM, instead modify it to LlamaForCausalLM
sed -i 's/LitaLlamaForCausalLM/LlamaForCausalLM/g' $TMP_CONV_DIR/config.json

# Build the TRT-LLM engine for the model
if ! trtllm-build \
    --checkpoint_dir "$TMP_CONV_DIR" \
    --output_dir ${OUTPUT_DIR} \
    --gemm_plugin float16 \
    --use_fused_mlp enable \
    --max_batch_size ${BATCH_SIZE} \
    --max_input_len 4096 \
    --max_seq_len 2984 \
    --max_multimodal_len "$((BATCH_SIZE * 4096))" \
    $TRTLLM_BUILD_EXTRA_ARGS; then
        echo "ERROR: Failed to build TRT engine"
        rm -rf $TMP_CONV_DIR
        rm $TMP_SYMLINK
        exit 1
fi

# Build the TRT engine for the visual encoder model
if ! python3 "${SCRIPT_DIR}/build_visual_engine.py" --model_path "${MODEL_DIR}" \
    --model_type vila --vila_path "$PYTHONPATH" --output_dir "${OUTPUT_DIR}/visual_engines" --max_batch_size 16; then
        echo "ERROR: Failed to build visual engine"
        rm -rf $TMP_CONV_DIR
        rm $TMP_SYMLINK
        exit 1
fi

rm -rf $TMP_CONV_DIR
rm $TMP_SYMLINK

echo "**********************************************"
echo "TRT engines generated at: $OUTPUT_DIR"
echo "**********************************************"
