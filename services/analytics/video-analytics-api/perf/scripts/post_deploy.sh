#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

load_compose_env
es_url="http://127.0.0.1:${ELASTICSEARCH_HOST_PORT}"
api_url="http://127.0.0.1:${VIDEO_ANALYTICS_API_HOST_PORT}"

deadline=$((SECONDS + 180))
while (( SECONDS < deadline )); do
    if curl -sf "$es_url/_cluster/health" >/dev/null 2>&1; then
        echo "Elasticsearch is up at $es_url"
        break
    fi
    sleep 5
done
if ! curl -sf "$es_url/_cluster/health" >/dev/null 2>&1; then
    echo "Timed out waiting for Elasticsearch." >&2
    docker logs "$ES_CONTAINER" >&2 || true
    exit 1
fi

if ! curl -sf "$es_url/_ingest/pipeline/insertion-timestamp-pipeline" >/dev/null; then
    echo "insertion-timestamp-pipeline missing; elasticsearch-init-container did not complete" >&2
    docker logs va-api-perf-es-init >&2 || true
    exit 1
fi

python3 "$PERF_DIR/benchmark/dump_loader.py" --es-url "$es_url" --dump-dir "$DUMP_DIR"

deadline=$((SECONDS + 90))
while (( SECONDS < deadline )); do
    if curl -sf "$api_url/livez" >/dev/null 2>&1; then
        echo "Video Analytics API is running at $api_url"
        exit 0
    fi
    sleep 3
done

echo "Timed out waiting for $APP_CONTAINER /livez." >&2
docker logs "$APP_CONTAINER" >&2 || true
docker ps -a >&2 || true
exit 1
