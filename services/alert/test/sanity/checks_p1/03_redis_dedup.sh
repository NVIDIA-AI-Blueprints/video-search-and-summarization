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

# =============================================================================
# Check 03: Redis Dedup Fingerprints
# Mode: HTTP
# Description: Verify dedup fingerprints are being written to alert documents
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common.sh"

CHECK_NAME="Redis Dedup Fingerprints"

query="{
  \"size\": ${VERIFICATION_SAMPLE_SIZE},
  \"_source\": [\"info.fingerprint\"]
}"

response=$(es_query "/${ALERTS_INDEX_PATTERN}/_search?size=${VERIFICATION_SAMPLE_SIZE}" "$query" || true)
if [[ -z "$response" ]]; then
    fail "$CHECK_NAME" "Cannot query alerts index" || true
    exit 1
fi

docs_sampled=$(echo "$response" | jq -r '.hits.hits | length')

if [[ "$docs_sampled" -lt 5 ]]; then
    skip_check "$CHECK_NAME" "Too few documents to assess fingerprints ($docs_sampled found)"
    exit 0
fi

# Count docs with non-null fingerprint
with_fingerprint=$(echo "$response" | jq -r '[.hits.hits[]._source.info.fingerprint | select(. != null and . != "")] | length')

pct=$((with_fingerprint * 100 / docs_sampled))

if [[ "$pct" -ge 50 ]]; then
    pass "$CHECK_NAME" "${pct}% have fingerprint ($with_fingerprint/$docs_sampled sampled)"
else
    fail "$CHECK_NAME" "Only ${pct}% have fingerprint ($with_fingerprint/$docs_sampled sampled)" || true
    exit 1
fi
