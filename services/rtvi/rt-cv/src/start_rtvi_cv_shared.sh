#!/usr/bin/env bash
set -euo pipefail

APP="${RTVI_CV_BINARY:-/opt/nvidia/deepstream/deepstream/bin/metropolis_perception_app}"
API="${RTVI_CV_SHARED_API:-/opt/nvidia/deepstream/deepstream/bin/rtvi_cv_shared_api.py}"
UPSTREAM_PORT="${RTVI_CV_NATIVE_API_PORT:-9001}"
PUBLIC_PORT="${RTVI_CV_API_PORT:-9000}"

args=("$@")
config=""
for ((index = 0; index < ${#args[@]}; index++)); do
    if [[ "${args[$index]}" == "-c" || "${args[$index]}" == "--cfg-file" ]]; then
        config="${args[$((index + 1))]:-}"
        config_index=$((index + 1))
        break
    fi
done
if [[ -z "$config" || ! -f "$config" ]]; then
    echo "start_rtvi_cv_shared requires -c <DeepStream config>" >&2
    exit 2
fi

runtime_dir="${RTVI_CV_SHARED_RUNTIME_DIR:-/tmp/rtvi-cv-shared}"
mkdir -p "$runtime_dir"
runtime_config="$runtime_dir/$(basename "$config")"
awk -v port="$UPSTREAM_PORT" '
    /^http-port[[:space:]]*=/ {$0 = "http-port=" port}
    {print}
' "$config" >"$runtime_config"
args[$config_index]="$runtime_config"

"$APP" "${args[@]}" &
app_pid=$!
cleanup() {
    kill "$app_pid" 2>/dev/null || true
    wait "$app_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

exec python3 "$API" \
    --port "$PUBLIC_PORT" \
    --upstream "http://127.0.0.1:$UPSTREAM_PORT"
