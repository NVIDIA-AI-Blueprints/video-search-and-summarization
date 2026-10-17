#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#
# Example workflow for sampling embeddings
#
# This script demonstrates the pipeline:
# 1. Dump embeddings from ReID service
# 2. Run sampling
#

set -e  # Exit on error

# Configuration
BASE_URL="http://localhost:8000"
EMBEDDINGS_FILE="embeddings.csv"
OUTPUT_DIR="sampled"
# Set OBJECT_ID="" to process all objects, or OBJECT_ID=25 to process specific object
OBJECT_ID=""  # Empty = process all objects

echo "=========================================="
echo "ReID Embedding Sampling Workflow"
echo "=========================================="
echo ""

# Step 1: Dump embeddings
echo "[1/2] Dumping embeddings from ReID service..."
curl -X POST "${BASE_URL}/dump/file?filename=${EMBEDDINGS_FILE}&include_vectors=true&format=csv" \
    -H "Content-Type: application/json" \
    -s | python3 -m json.tool

echo ""
echo "✓ Embeddings dumped to ${EMBEDDINGS_FILE}"
echo ""

# Step 2: Sample embeddings
echo "[2/2] Running sampling..."
if [ -z "$OBJECT_ID" ]; then
    echo "Processing ALL objects in the CSV file"
    python3 sample_and_extract_patches.py \
        --embeddings "../${EMBEDDINGS_FILE}" \
        --output-dir "${OUTPUT_DIR}" \
        --outlier-threshold 3.0 \
        --cosine-threshold 0.9995 \
        --max-samples 50
else
    echo "Processing only object_id: ${OBJECT_ID}"
    python3 sample_and_extract_patches.py \
        --embeddings "../${EMBEDDINGS_FILE}" \
        --output-dir "${OUTPUT_DIR}" \
        --object-id ${OBJECT_ID} \
        --outlier-threshold 3.0 \
        --cosine-threshold 0.9995 \
        --max-samples 50
fi

echo ""
echo "=========================================="
echo "Workflow completed successfully!"
echo "=========================================="
echo "Sampled embeddings are in: ${OUTPUT_DIR}/sampled_embeddings.csv"
echo ""

