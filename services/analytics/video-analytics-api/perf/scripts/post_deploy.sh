#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

load_compose_env
es_url="${ES_URL:-http://127.0.0.1:${ELASTICSEARCH_HOST_PORT}}"
api_url="${VA_API_URL:-http://127.0.0.1:${VIDEO_ANALYTICS_API_HOST_PORT}}"

if ! va_api_wait_for_elasticsearch "$es_url" 36 5; then
    dump_es_debug
    exit 1
fi

if ! va_api_verify_ingest_pipeline "$es_url"; then
    exit 1
fi

if ! va_api_load_elasticsearch_dumps "$es_url" "$DUMP_DIR"; then
    echo "Elasticsearch data dump load failed" >&2
    dump_es_debug
    exit 1
fi

if ! va_api_wait_for_api "$api_url" 30 3; then
    exit 1
fi
echo "Video Analytics API is running at $api_url"
