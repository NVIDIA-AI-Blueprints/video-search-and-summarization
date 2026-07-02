#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# preflight.sh — strong pre-flight checks before running the LVS benchmark.
#
# Verifies, and FAILS fast, that:
#   1. the config file exists, parses, and has the required fields;
#   2. the configured GPU ids are integers within the host's GPU range;
#   3. the LVS backend is reachable and the dev /files route is enabled;
#   4. YOUR LVS container actually owns the backend port (not another tenant) —
#      i.e. its server bound successfully and no other container holds the port;
#   5. the configured vlm_gpus/llm_gpus are reserved by running GPU containers,
#      and (best-effort) are the GPUs your LVS's VLM/LLM containers use.
#
# Required env:
#   LVS_BACKEND          your LVS /summarize endpoint (e.g. http://localhost:38111)
#   LVS_CONTAINER_NAME   your LVS container (e.g. vss-lvs or <project>-lvs-server-1)
#   VLM_GPUS / LLM_GPUS  GPU indices for the VLM / LLM (comma-separated; or VIA_VLM_GPUS/VIA_LLM_GPUS)
# Optional:
#   CONFIG               path to the benchmark config.yaml (default: config.yaml next to this script)
#
# Exit codes: 0 = all good (warnings allowed), 1 = hard failure (do not benchmark).

set -uo pipefail

LVS_BACKEND="${LVS_BACKEND:-}"
LVS_CONTAINER_NAME="${LVS_CONTAINER_NAME:-}"
VLM_GPUS="${VLM_GPUS:-${VIA_VLM_GPUS:-}}"
LLM_GPUS="${LLM_GPUS:-${VIA_LLM_GPUS:-}}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${CONFIG:-${SCRIPT_DIR}/config.yaml}"
PYBIN="${SCRIPT_DIR}/vss-bench-env/bin/python"
[[ -x "${PYBIN}" ]] || PYBIN="python3"

fail=0
# Helpers must return 0: callers chain them as `cond && ok ... || err ...`.
err()  { echo "  FAIL  $*"; fail=1; return 0; }
wrn()  { echo "  WARN  $*"; return 0; }
ok()   { echo "  ok    $*"; return 0; }

echo "=== 1. Required env ==="
for v in LVS_BACKEND LVS_CONTAINER_NAME VLM_GPUS LLM_GPUS; do
    [[ -n "${!v}" ]] && ok "$v=${!v}" || err "$v is not set"
done
[[ "$fail" -ne 0 ]] && { echo "PREFLIGHT: FAIL (missing env)"; exit 1; }

echo "=== 2. Config file valid ==="
if [[ ! -f "${CONFIG}" ]]; then
    err "config not found: ${CONFIG}"
else
    "${PYBIN}" - "${CONFIG}" <<'PY' && ok "config parses with required fields" || err "config invalid (see above)"
import sys, yaml
try:
    d = yaml.safe_load(open(sys.argv[1]))
except Exception as e:
    print(f"  FAIL  YAML parse error: {e}"); sys.exit(1)
if not isinstance(d, dict): print("  FAIL  config is not a mapping"); sys.exit(1)
g = d.get("global") or {}
for f in ("vss_backend", "vlm_gpus", "llm_gpus"):
    if f not in g: print(f"  FAIL  global.{f} missing"); sys.exit(1)
if not d.get("test_scenarios"): print("  FAIL  no test_scenarios"); sys.exit(1)
for f in ("vlm_gpus", "llm_gpus"):
    if not isinstance(g[f], list) or not all(isinstance(x, int) for x in g[f]):
        print(f"  FAIL  global.{f} must be a list of integers"); sys.exit(1)
sys.exit(0)
PY
fi

echo "=== 3. GPU ids within host range ==="
GPU_COUNT=$(nvidia-smi --query-gpu=index --format=csv,noheader 2>/dev/null | wc -l)
ok "host GPUs: ${GPU_COUNT}"
gpu_list="$(echo "${VLM_GPUS},${LLM_GPUS}" | tr ',' ' ')"
for g in ${gpu_list}; do
    if ! [[ "$g" =~ ^[0-9]+$ ]]; then err "GPU id '$g' is not an integer"
    elif [[ "$g" -ge "${GPU_COUNT}" ]]; then err "GPU id $g out of range (host has 0-$((GPU_COUNT-1)))"
    fi
done

echo "=== 4. Backend reachable + dev /files route enabled ==="
code=$(curl -s -o /dev/null -m 5 -w "%{http_code}" "${LVS_BACKEND}/v1/ready" 2>/dev/null)
[[ "$code" == "200" ]] && ok "LVS /v1/ready -> 200" || err "LVS /v1/ready -> ${code} (backend not ready)"
fcode=$(curl -s -o /dev/null -m 5 -w "%{http_code}" -X POST "${LVS_BACKEND}/files" 2>/dev/null)
if [[ "$fcode" == "404" ]]; then err "/files -> 404 (dev route off; enable VIA_DEV_API on your LVS)"
else ok "/files reachable (${fcode}, not 404)"; fi

echo "=== 5. YOUR LVS container owns the backend port ==="
if ! docker inspect "${LVS_CONTAINER_NAME}" >/dev/null 2>&1; then
    err "container '${LVS_CONTAINER_NAME}' not found"
else
    running=$(docker inspect -f '{{.State.Running}}' "${LVS_CONTAINER_NAME}")
    [[ "$running" == "true" ]] && ok "${LVS_CONTAINER_NAME} is running" || err "${LVS_CONTAINER_NAME} not running"
    # The decisive check: did OUR server fail to bind its port (another tenant owns it)?
    if docker logs "${LVS_CONTAINER_NAME}" 2>&1 | grep -qiE "address already in use|errno 98"; then
        err "${LVS_CONTAINER_NAME} logged 'address already in use' — its server did NOT bind the port;"
        err "      your requests are hitting a DIFFERENT instance. Free the port or use a unique one."
    else
        ok "no port-bind conflict in ${LVS_CONTAINER_NAME} logs"
    fi
    # Another container publishing the same host port = collision risk.
    port="${LVS_BACKEND##*:}"; port="${port%%/*}"
    others=$(docker ps --format '{{.Names}} {{.Ports}}' 2>/dev/null \
             | grep -E ":${port}->" | grep -v "^${LVS_CONTAINER_NAME} " | awk '{print $1}')
    [[ -n "$others" ]] && err "another container also publishes :${port}: ${others}" || ok "no other container publishes :${port}"
fi

echo "=== 6. Configured GPUs are reserved by your LVS's VLM/LLM ==="
# Map gpu id -> reserving container(s)
declare -A GPU_OWNER
for c in $(docker ps -q 2>/dev/null); do
    dr=$(docker inspect -f '{{json .HostConfig.DeviceRequests}}' "$c" 2>/dev/null)
    case "$dr" in *'"gpu"'*) : ;; *) continue ;; esac
    cn=$(docker inspect -f '{{.Name}}' "$c" | sed 's#^/##')
    for g in $(echo "$dr" | grep -oE '"[0-9]+"' | tr -d '"'); do
        GPU_OWNER[$g]="${GPU_OWNER[$g]:+${GPU_OWNER[$g]},}${cn}"
    done
done
# Endpoints our LVS points at (to identify which container should own which GPUs)
vlm_ep=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "${LVS_CONTAINER_NAME}" 2>/dev/null | grep -E '^RTVI_VLM_URL=|^VIA_VLM_ENDPOINT=' | head -1 | cut -d= -f2-)
llm_ep=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "${LVS_CONTAINER_NAME}" 2>/dev/null | grep -E '^LVS_LLM_BASE_URL=' | head -1 | cut -d= -f2-)
echo "  LVS VLM endpoint: ${vlm_ep:-<unknown>}"
echo "  LVS LLM endpoint: ${llm_ep:-<unknown>}"
echo "  GPU -> reserving container:"
for g in $(echo "${VLM_GPUS},${LLM_GPUS}" | tr ',' '\n' | sort -un); do
    echo "    GPU $g -> ${GPU_OWNER[$g]:-<NONE>}"
    [[ -z "${GPU_OWNER[$g]:-}" ]] && err "GPU $g is configured but no running container reserves it (you would measure 0%)"
done
echo "  (Verify the containers above are YOUR LVS's VLM/LLM and match the endpoints — not another tenant's.)"

echo ""
if [[ "$fail" -ne 0 ]]; then echo "PREFLIGHT: FAIL — do not benchmark until resolved."; exit 1; fi
echo "PREFLIGHT: PASS"
