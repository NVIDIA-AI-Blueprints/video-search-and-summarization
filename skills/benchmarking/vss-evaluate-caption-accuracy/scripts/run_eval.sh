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
# End-to-end VLM caption accuracy evaluation.
#
#   gt       ground truth from gpt-4.1 (openai-compat). Run once per scene set;
#            expensive, so later runs point at it with GT_SRC.   IN-CONTAINER.
#   capture  paired REF (no frame selection) + HYP (CHOOSE_FSELECT=true) per scene,
#            back to back in one session, because REF is nondeterministic run to
#            run.                                                IN-CONTAINER.
#   judge    claude-opus-4-8 scores REF and HYP against GT, per chunk. Uses the
#            local `claude` CLI, which is NOT in the container.  ON THE HOST.
#   table    accuracy + time-saved markdown, optionally vs a baseline. Either.
#
# The stages run in different places, so invoke them separately:
#
#   docker exec -e DESC=my-run -w /workspace $RTVI_CONTAINER \
#     bash skills/benchmarking/vss-evaluate-caption-accuracy/scripts/run_eval.sh capture scene-a
#   DESC=my-run bash .../scripts/run_eval.sh judge scene-a     # on the HOST
#   DESC=my-run bash .../scripts/run_eval.sh table scene-a
#
# Everything for one run lands in results/<DESC>/:
#   captions/<scene>/{gt,ref,hyp_<desc>_vN}.txt, server_logs/, judge/, summary.md
set -u

# Self-contained: the engines live beside this script.
SKILL=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# skills/<category>/<skill>/ -> repo root is three levels up.
REPO=${REPO:-$(cd "$SKILL/../../.." && pwd)}
CAPTURE=$SKILL/scripts/run_captioning.py
JUDGE=$SKILL/scripts/multi_judge.py
SCORE=$SKILL/scripts/score.py
AGGREGATE=$SKILL/scripts/aggregate_table.py

DESC=${DESC:-eval-$(date +%Y-%m-%d)}
HYP_VER=${HYP_VER:-v1}
# Directory holding the source videos. Override for your own corpus.
DEDUP_DIR=${DEDUP_DIR:-$REPO/videos}
MAX_WORKERS=${MAX_WORKERS:-32}

RESULTS_ROOT=${RESULTS_ROOT:-$SKILL/results}
RESULTS=$RESULTS_ROOT/$DESC
LOGDIR=$RESULTS/server_logs
JUDGE_ROOT=${JUDGE_ROOT:-$RESULTS/judge}
# Where `capture` looks for a gt.txt to reuse. Defaults to this run (i.e. the gt
# stage was run first); set to another DESC to share one ground truth.
GT_SRC=${GT_SRC:-$DESC}

STAGE=${1:-}; shift 2>/dev/null || true
SCENES="${*:-admin new_warehouse}"
[ -n "$STAGE" ] || { echo "usage: $0 {gt|capture|judge|table} [scene ...]"; exit 2; }

# Domain context steers the judge; a wrong one costs recall on entity/event F1.
ctx_for() {
  case "$1" in
    bus*)      echo "Roadside / intersection traffic surveillance from a transit vantage. Entities are buses, cars, trucks, motorcycles, pedestrians, traffic signals. Critical events are near-misses, jaywalking, signal violations, congestion, stops." ;;
    admin*)    echo "Office / administrative building interior surveillance (reception, lobby, corridors). Entities are staff, visitors, desks, doors, badges. Critical events are tailgating, unauthorized access, loitering, unattended items." ;;
    hospital*) echo "Hospital corridor / ward surveillance. Entities are patients, clinicians (scrubs/PPE), wheelchairs, beds, carts, IV stands. Critical events are falls, unattended patients, restricted-area entry, congestion." ;;
    its*)      echo "Intelligent-transportation intersection surveillance (roadside camera). Entities are cars, trucks, buses, motorcycles, pedestrians, traffic signals, lane markings. Critical events are red-light running, near-misses, jaywalking, congestion, stalled vehicles." ;;
    *)         echo "Warehouse aisle / loading-dock surveillance. Entities are forklifts, pallets, workers (with PPE). Critical events are pallet drops, PPE violations, restricted-zone entry, forklift-pedestrian near-misses." ;;
  esac
}

# run_captioning.py resolves its results root from its own location, so point it
# at this skill's results dir explicitly.
capture_py() { python3 "$CAPTURE" --results-root "$RESULTS_ROOT" "$@"; }

do_gt() {
  echo "=== gt: DESC=$DESC scenes='$SCENES' (gpt-4.1, needs OPENAI_API_KEY in .env) ==="
  for s in $SCENES; do
    capture_py --desc "$DESC" --runs gt --scenes "$s" --dedup-dir "$DEDUP_DIR"
    local f=$RESULTS/captions/$s/gt.txt
    [ -f "$f" ] && echo "[check] $s gt.txt: $(wc -l < "$f") lines" || echo "[check] $s GT FAILED"
  done
  echo "ALL DONE: gt -> $RESULTS"
}

do_capture() {
  [ -n "${SFC:-}" ] && export NVDS_FSELECT_STATIC_FRAME_COUNT=$SFC
  echo "=== capture: DESC=$DESC sfc=${SFC:-plugin-default} scenes='$SCENES' ==="
  for s in $SCENES; do
    echo "======== PAIR scene=$s ========"
    for run in hyp ref; do
      capture_py --desc "$DESC" --runs $run --scenes "$s" \
          --hyp-mode fselect-only --dedup-dir "$DEDUP_DIR"
      # run_captioning.py overwrites <run>_server.log each time; keep a per-scene
      # copy so `table` can read chunk counts and wall clock back out later.
      if [ -f "$LOGDIR/${run}_server.log" ] && cp "$LOGDIR/${run}_server.log" "$LOGDIR/${run}_${s}.log"; then
        local caps t errs
        caps=$(grep -c 'VLMCaption' "$LOGDIR/${run}_${s}.log")
        t=$(grep -o 'total processing time - [0-9.]*' "$LOGDIR/${run}_${s}.log" | tail -1 | grep -o '[0-9.]*$')
        errs=$(grep -c 'Decode error' "$LOGDIR/${run}_${s}.log")
        echo "[check] $s $run: captions=$caps time=${t}s errors=$errs"
      else
        echo "[check] ERROR: no server log preserved for $s $run"
      fi
    done
    mkdir -p "$RESULTS/captions/$s"
    if [ -f "$RESULTS/captions/$s/gt.txt" ]; then
      echo "[check] $s gt.txt present ($(wc -l < "$RESULTS/captions/$s/gt.txt") lines)"
    elif cp "$RESULTS_ROOT/$GT_SRC/captions/$s/gt.txt" "$RESULTS/captions/$s/gt.txt" 2>/dev/null; then
      echo "[check] $s gt.txt staged from $GT_SRC ($(wc -l < "$RESULTS/captions/$s/gt.txt") lines)"
    else
      echo "[check] $s GT MISSING — run the 'gt' stage, or set GT_SRC to a run that has it"
    fi
  done
  echo "ALL DONE: capture -> $RESULTS"
}

do_judge() {
  command -v claude >/dev/null 2>&1 || {
    echo "ERROR: the 'claude' CLI is not on PATH. The judge runs on the HOST," \
         "not inside the rtvi_vlm container." >&2
    exit 1
  }
  mkdir -p "$JUDGE_ROOT"
  echo "=== judge: DESC=$DESC scenes='$SCENES' -> $JUDGE_ROOT ==="
  for s in $SCENES; do
    local GT=$RESULTS/captions/$s/gt.txt
    local REF=$RESULTS/captions/$s/ref.txt
    local HYP=$RESULTS/captions/$s/hyp_${DESC}_${HYP_VER}.txt
    if ! { [ -f "$GT" ] && [ -f "$REF" ] && [ -f "$HYP" ]; }; then
      echo "skip $s (GT=$([ -f "$GT" ]&&echo y||echo n) REF=$([ -f "$REF" ]&&echo y||echo n) HYP=$([ -f "$HYP" ]&&echo y||echo n))"
      continue
    fi
    echo "======== SCENE=$s ========"
    # Unset both API keys so ONLY the claude-CLI judge registers; otherwise the
    # anthropic-API judges join too and the run stops being single-judge.
    env -u OPENAI_API_KEY -u ANTHROPIC_API_KEY python3 -u "$JUDGE" \
      --gt "$GT" --ref "$REF" --hyp "$HYP" \
      --server-log-dir "$LOGDIR" \
      --out-root "$JUDGE_ROOT" --run-id "$s" --max-workers "$MAX_WORKERS" \
      --context "$(ctx_for "$s")"
    python3 -u "$SCORE" --run-dir "$JUDGE_ROOT/$s" --gt "$GT" --ref "$REF" --hyp "$HYP"
  done
  echo "ALL DONE: judge -> $JUDGE_ROOT"
}

do_table() {
  local args=(--run-dir "$RESULTS" --judge-root "$JUDGE_ROOT"
              --scenes $SCENES --label "$DESC" --out "$RESULTS/summary.md")
  if [ -n "${BASELINE:-}" ]; then
    local brd=${BASELINE_RUN_DIR:-$RESULTS_ROOT/$BASELINE}
    local bjr=${BASELINE_JUDGE_ROOT:-$brd/judge}
    [ -d "$bjr" ] || echo "warning: baseline judge root not found: $bjr" \
      "(set BASELINE_JUDGE_ROOT; its accuracy columns will be blank)" >&2
    args+=(--baseline-run "$brd" --baseline-judge-root "$bjr" --baseline-label "$BASELINE")
  fi
  python3 "$AGGREGATE" "${args[@]}"
}

case "$STAGE" in
  gt)      do_gt ;;
  capture) do_capture ;;
  judge)   do_judge ;;
  table)   do_table ;;
  *) echo "unknown stage '$STAGE' (want gt|capture|judge|table)"; exit 2 ;;
esac
