#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Usage: summarize_runs.sh <run-name>...
HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE/../cli_eval_result" || exit 1
for n in "$@"; do
  f="$n.json"; [ -f "$f" ] || { echo "== $n (missing)"; continue; }
  echo "== $n"
  jq -c 'def m(a): (a|add/length*1000|round/1000); [.query_results[]] as $r |
    {R:m([$r[].recall]), P:m([$r[].precision]), mAP:m([$r[].average_precision]), h1:m([$r[].hit_at_k["1"]]), h5:m([$r[].hit_at_k["5"]]), mrr:m([$r[].reciprocal_rank]), lat:(([$r[].latency_s]|add/length)*100|round/100)}' "$f"
  jq -c -f "$HERE/perpath.jq" "$f" | grep -E 'attribute|fusion'
  grep -hE 'Full ingestion' "/tmp/eval-$n.log" 2>/dev/null || true
done
