#!/usr/bin/env bash
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

# One-shot deploy -> benchmark -> teardown for alert verification perf.
#
# Runs the same stage hooks the shared perf job runs, in the same order:
#   pre_flight.sh -> deploy.sh -> post_deploy.sh -> vss_perf_benchmark.py -> teardown.sh
# Use it for local runs, and as the single entry point if the shared job is
# configured with PERF_USE_RUN_BENCHMARK_SCRIPT=true (the LVS-style path).
set -euo pipefail
# shellcheck source=_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

usage() {
  cat <<EOF
Usage: $(basename "$0") -c <config-id> [options]

  -c ID     perf_configs id (configs/perf-configs.yaml)          [required]
  -s NAME   scenario from configs/config.yaml; repeat for several [default: quick_test]
  -O DIR    write CI-style result JSON(s) to DIR                 [default: vss-perf-results]
  -u        upload result JSON(s) to MinIO (needs MINIO_* env)
  -r VER    release recorded in the result                       [default: \$RELEASE or local]
  -t WHO    triggered_by recorded in the result                  [default: \$TRIGGERED_BY or manual]
  -k        skip deploy (stack already up from a previous run)
  -d        tear the stack down afterwards (always runs, even on failure)
  -h        help

Example:
  NGC_CLI_API_KEY=... ./run_benchmark.sh -c spark-1-rtdetr-cr3-bf16 -s quick_test -d
EOF
}

scenarios=()
out_dir="vss-perf-results"
upload=false
skip_deploy=false
teardown=false
release="${RELEASE:-local}"
triggered_by="${TRIGGERED_BY:-manual}"

while getopts ":c:s:O:r:t:ukdh" opt; do
  case "${opt}" in
    c) export PERF_CONFIG_ID="${OPTARG}" ;;
    s) scenarios+=("${OPTARG}") ;;
    O) out_dir="${OPTARG}" ;;
    r) release="${OPTARG}" ;;
    t) triggered_by="${OPTARG}" ;;
    u) upload=true ;;
    k) skip_deploy=true ;;
    d) teardown=true ;;
    h) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done
require_config_id
[[ ${#scenarios[@]} -gt 0 ]] || scenarios=("${PERF_SCENARIO}")

# The stack is sized (NUM_SENSORS, staged videos) for the largest stream count
# across all requested scenarios, so prepare for the scenario with the most.
export PERF_SCENARIO="${scenarios[0]}"
if [[ ${#scenarios[@]} -gt 1 ]]; then
  PERF_SCENARIO="$("${PERF_PYTHON}" - "${PERF_DIR}/configs/config.yaml" "${scenarios[@]}" <<'EOF'
import sys, yaml
cfg = yaml.safe_load(open(sys.argv[1]))["test_scenarios"]
def peak(name):
    return max(c for v in cfg[name].get("videos", []) for c in v.get("stream_counts", [1]))
print(max(sys.argv[2:], key=peak))
EOF
)"
fi

if [[ "${teardown}" == true ]]; then
  trap 'bash "${PERF_DIR}/scripts/teardown.sh"' EXIT
fi

if [[ "${skip_deploy}" != true ]]; then
  bash "${PERF_DIR}/scripts/pre_flight.sh"
  bash "${PERF_DIR}/scripts/deploy.sh"
  bash "${PERF_DIR}/scripts/post_deploy.sh"
fi

resolve_perf_config
_bench_args=(--config-id "${PERF_CONFIG_ID}"
             --cv-gpus "${PERF_CV_GPUS}" --vlm-gpus "${PERF_VLM_GPUS}"
             --output-json "${out_dir}"
             --triggered-by "${triggered_by}" --release "${release}")
[[ "${upload}" == true ]] && _bench_args+=(--upload)

rc=0
for _s in "${scenarios[@]}"; do
  log "benchmark: scenario=${_s}"
  "${PERF_PYTHON}" "${PERF_DIR}/benchmark/core/vss_perf_benchmark.py" --scenario "${_s}" "${_bench_args[@]}" || rc=$?
done
exit "${rc}"
