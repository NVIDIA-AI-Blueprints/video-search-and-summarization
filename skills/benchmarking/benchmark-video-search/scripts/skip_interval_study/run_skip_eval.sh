#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Usage: run_skip_eval.sh <skip-interval> <run-name> [pgie-interval, default 1]
set -uo pipefail
SKIP="$1"; NAME="$2"; PGIE_INTERVAL="${3:-1}"
HOST_IP="${HOST_IP:-10.86.12.161}"
DATA_DIR="${DATA_DIR:-$HOME/.cache/vss-devx-search}"
REPO=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
CFG=$REPO/deploy/docker/developer-profiles/dev-profile-search/video-analytics-2d-app/deepstream/configs/ds-main-config.txt
APP_CFG=/opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/configs/ds-main-config.txt
ES=http://$HOST_IP:7777/elasticsearch
IDX="mdx-behavior-2025-01-01 mdx-raw-2025-01-01 mdx-embed-filtered-2025-01-01"
log() { echo "[$(date +%H:%M:%S)] $*"; }
counts() { curl -s -m10 "$ES/_cat/indices/mdx-*-2025-01-01?h=index,docs.count" | tr -s ' \n' ' '; }
clear_es() { for i in $IDX; do docker exec elasticsearch curl -s -m30 -X POST "localhost:9200/$i/_delete_by_query?conflicts=proceed&refresh=true" -H 'Content-Type: application/json' -d '{"query":{"match_all":{}}}' >/dev/null; done; }

log "== skip-interval=$SKIP pgie-interval=$PGIE_INTERVAL run=$NAME"
for t in video stream; do
  for n in $(vss vios list --type "$t" --raw | jq -r '.sensors[].name'); do
    vss vios delete --type "$t" --sensor "$n" --raw >/dev/null || { log "FAILED to delete $t $n"; exit 2; }
  done
done
log "sensors remaining: $(vss vios list --raw | jq -r .count)"

sed -i "/^\[visionencoder\]/,/^\[/{s/^skip-interval=.*/skip-interval=$SKIP/}" "$CFG"
sed -i "/^\[primary-gie\]/,/^\[/{s/^interval=.*/interval=$PGIE_INTERVAL/}" "$CFG"
grep -nE '^(skip-interval|interval)=' "$CFG"

docker restart vss-behavior-analytics vss-rtvi-cv >/dev/null
S=$(docker inspect -f '{{.State.StartedAt}}' vss-rtvi-cv)
for i in $(seq 1 90); do docker logs --since "$S" vss-rtvi-cv 2>&1 | grep -q 'Active sources' && break; sleep 5; done
log "rtvi-cv up after ~$((i*5))s; running config: $(docker exec vss-rtvi-cv grep -E '^(interval|skip-interval|smart-infer|ofa-predict)=' $APP_CFG | tr '\n' ' ')"
docker exec vss-rtvi-cv grep -q "^skip-interval=$SKIP$" $APP_CFG || { log "running config does not have skip-interval=$SKIP"; exit 2; }
docker exec vss-rtvi-cv grep -q "^interval=$PGIE_INTERVAL$" $APP_CFG || { log "running config does not have interval=$PGIE_INTERVAL"; exit 2; }

# behavior-analytics flushes tracks it held for deleted videos after restart; wait for that, clear, then require a stable empty index
sleep 60
log "before clear: $(counts)"
clear_es
for k in 1 2 3; do sleep 20; c=$(counts); log "after clear: $c"; done
total=$(echo "$c" | awk '{s=0; for(i=2;i<=NF;i+=2) s+=$i; print s}')
[ "$total" = 0 ] || { log "indexes not empty and stable: $c"; exit 2; }
log "gpu5: $(nvidia-smi -i 5 --query-gpu=temperature.gpu,clocks.sm,clocks_event_reasons.sw_thermal_slowdown --format=csv,noheader)"

cd $REPO/skills/benchmarking/benchmark-video-search
PYTHONUNBUFFERED=1 uv run -q --with requests python3 scripts/run_eval_flows.py \
  --endpoint "http://$HOST_IP:8000" --llm-url "http://$HOST_IP:30083" \
  --data-dir "$DATA_DIR" --dataset vad-r1-v2 --subset benchmark \
  --skip-download --ingest-require embed,raw,behavior --name "$NAME"
rc=$?
log "eval exit=$rc; behavior docs now: $(curl -s -m20 "$ES/mdx-behavior-2025-01-01/_search" -H 'Content-Type: application/json' -d '{"size":0,"track_total_hits":true,"aggs":{"u":{"cardinality":{"field":"id.keyword"}}}}' | jq -c '{docs: .hits.total.value, objects: .aggregations.u.value}')"
exit $rc
