#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

write_compose_env
load_compose_env
docker pull "$VIDEO_ANALYTICS_API_IMAGE"

# Jenkins #68: starting the whole graph waits on elasticsearch healthy. If the
# node dies in a few seconds, init/app never run and we get no ES logs. Bring
# ES up alone, wait until HTTP 9200 answers, then init and the API image.
if ! compose up -d --build --no-deps --force-recreate elasticsearch; then
    dump_es_debug
    exit 1
fi
wait_for_elasticsearch

if ! compose up -d --no-deps --force-recreate elasticsearch-init-container; then
    echo "elasticsearch-init-container failed to start" >&2
    docker logs $ES_INIT_CONTAINER >&2 || true
    dump_es_debug
    exit 1
fi

init_deadline=$((SECONDS + 180))
while (( SECONDS < init_deadline )); do
    init_status="$(docker inspect -f '{{.State.Status}}' $ES_INIT_CONTAINER 2>/dev/null || echo missing)"
    init_exit="$(docker inspect -f '{{.State.ExitCode}}' $ES_INIT_CONTAINER 2>/dev/null || echo 1)"
    if [[ "$init_status" == "exited" ]]; then
        if [[ "$init_exit" == "0" ]]; then
            echo "elasticsearch-init-container completed"
            break
        fi
        echo "elasticsearch-init-container exited $init_exit" >&2
        docker logs $ES_INIT_CONTAINER >&2 || true
        exit 1
    fi
    sleep 3
done
if [[ "$(docker inspect -f '{{.State.Status}}' $ES_INIT_CONTAINER 2>/dev/null || true)" != "exited" ]]; then
    echo "Timed out waiting for elasticsearch-init-container" >&2
    docker logs $ES_INIT_CONTAINER >&2 || true
    exit 1
fi

if ! compose up -d --no-deps --force-recreate video-analytics-api; then
    echo "video-analytics-api failed to start" >&2
    docker logs "$APP_CONTAINER" >&2 || true
    dump_es_debug
    exit 1
fi
