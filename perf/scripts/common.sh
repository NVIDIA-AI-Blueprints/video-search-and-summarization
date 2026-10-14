#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

export VSS_PERF_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export VSS_REPO_ROOT="$(cd -- "$VSS_PERF_ROOT/.." && pwd)"
STATE_FILE="$VSS_PERF_ROOT/.runtime-state.env"
# The shared runner invokes teardown outside its withEnv block.
if [[ "${VSS_PERF_STAGE:-}" == teardown && -f "$STATE_FILE" ]]; then source "$STATE_FILE"; fi
export CONFIG_ID="${CONFIG_ID:?Set CONFIG_ID to a perf-configs.yaml entry}"
export PERF_CONFIG="${PERF_CONFIG:-$VSS_PERF_ROOT/configs/config.yaml}"
export PERF_CONFIGS="${PERF_CONFIGS:-$VSS_PERF_ROOT/configs/perf-configs.yaml}"
export VSS_DATA_DIR="${VSS_DATA_DIR:-$VSS_PERF_ROOT/.runtime/$CONFIG_ID}"
export VSS_APPS_DIR="$VSS_REPO_ROOT/deploy/docker"
# Preserve service mutations only for this exact source configuration tree.
_vios_config_hash="$(python3 - "$VSS_APPS_DIR/services/vios/configs" <<'PY'
import hashlib, pathlib, sys
root=pathlib.Path(sys.argv[1])
if not root.is_dir(): raise SystemExit('VIOS source configuration tree is missing')
digest=hashlib.sha256()
for path in sorted(root.rglob('*')):
    if path.is_file():
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b'\0')
        digest.update(path.read_bytes())
        digest.update(b'\0')
print(digest.hexdigest())
PY
)" || exit $?
export VST_CONFIG_PATH="$VSS_DATA_DIR/vios-configs-${_vios_config_hash:0:12}"
export VST_LOGS="$VSS_DATA_DIR/data_log/vst"
export VSS_PERF_VENV="${VSS_PERF_VENV:-$VSS_PERF_ROOT/.venv}"
# Installed wheels must track this checkout: the requirements alone do not
# change when CLI/core Python sources or package dependency declarations change.
_bootstrap_hash="$(python3 - "$VSS_REPO_ROOT" "$VSS_PERF_ROOT/benchmark/requirements.txt" <<'PY'
import hashlib, pathlib, sys
root=pathlib.Path(sys.argv[1])
paths=[pathlib.Path(sys.argv[2])]
workspace=root/'libs/vss/pyproject.toml'
if workspace.is_file(): paths.append(workspace)
for package in ('core','cli'):
    directory=root/'libs/vss'/package
    paths.append(directory/'pyproject.toml')
    paths.extend(directory.joinpath('src').rglob('*.py'))
digest=hashlib.sha256()
for path in sorted(paths):
    digest.update(path.relative_to(root).as_posix().encode())
    digest.update(b'\0')
    digest.update(path.read_bytes())
    digest.update(b'\0')
print(digest.hexdigest())
PY
)" || exit $?
ensure_uv() {
    if ! command -v uv >/dev/null; then
        if [[ ! -x "$VSS_PERF_ROOT/.bootstrap/bin/uv" ]]; then
            python3 -m venv "$VSS_PERF_ROOT/.bootstrap"
            "$VSS_PERF_ROOT/.bootstrap/bin/python" -m pip install 'uv>=0.8,<1'
        fi
        export PATH="$VSS_PERF_ROOT/.bootstrap/bin:$PATH"
    fi
}
if [[ ! -x "$VSS_PERF_VENV/bin/vss" || ! -f "$VSS_PERF_VENV/.cli-bootstrap-sha256" || "$(cat "$VSS_PERF_VENV/.cli-bootstrap-sha256")" != "$_bootstrap_hash" ]]; then
    ensure_uv
    if [[ ! -x "$VSS_PERF_VENV/bin/python" ]]; then uv venv --python 3.13 "$VSS_PERF_VENV"; fi
    # Git archives lack SCM metadata; the pipeline normally checks out Git.
    if ! git -C "$VSS_REPO_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then export SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0; fi
    uv pip install --python "$VSS_PERF_VENV/bin/python" --reinstall-package nvidia-vss-core --reinstall-package nvidia-vss-cli -r "$VSS_PERF_ROOT/benchmark/requirements.txt" "$VSS_REPO_ROOT/libs/vss/core" "$VSS_REPO_ROOT/libs/vss/cli"
    printf '%s\n' "$_bootstrap_hash" > "$VSS_PERF_VENV/.cli-bootstrap-sha256"
fi
export PATH="$VSS_PERF_VENV/bin:$PATH"
export PYTHON="$VSS_PERF_VENV/bin/python"

# Generate shell-quoted values from reviewed config data, never evaluate raw YAML.
_config_environment="$("$PYTHON" - "$PERF_CONFIG" "$PERF_CONFIGS" "$CONFIG_ID" <<'PY'
import pathlib, shlex, sys, yaml
global_config = yaml.safe_load(pathlib.Path(sys.argv[1]).read_text())['global']
configs = yaml.safe_load(pathlib.Path(sys.argv[2]).read_text())['perf_configs']
matches = [c for c in configs if c['id'] == sys.argv[3]]
if len(matches) != 1:
    raise SystemExit('CONFIG_ID must identify exactly one matrix entry')
entry = matches[0]
for name,value in {'MATRIX_COMPOSE_FILE':entry['composePath'],
                  'MATRIX_RT_CV_DEVICE_ID':entry.get('rtCvGpus','0'),
                  'MATRIX_HARDWARE_PROFILE':entry.get('hardwareProfile','OTHER'),
                  'MATRIX_VSS_PUBLIC_URL':global_config['vss_backend'],
                  'UPLOAD_AFTER_TEARDOWN':str(global_config.get('upload_after_teardown',False)).lower(),
                  'DEPLOY_TIMEOUT_SECONDS':60*global_config['stage_timeouts']['deploy'],
                  'POST_DEPLOY_TIMEOUT_SECONDS':60*global_config['stage_timeouts']['post_deploy'],
                  'PERF_STAGE_TIMEOUT_SECONDS':60*global_config['stage_timeouts'].get(__import__('os').environ.get('VSS_PERF_STAGE','pre_flight'),30)}.items():
    print('export '+name+'='+shlex.quote(str(value)))
PY
)" || exit $?
eval "$_config_environment"
export COMPOSE_FILE="${COMPOSE_FILE:-$VSS_REPO_ROOT/$MATRIX_COMPOSE_FILE}"
export RT_CV_DEVICE_ID="${RT_CV_DEVICE_ID:-$MATRIX_RT_CV_DEVICE_ID}"
export HARDWARE_PROFILE="${HARDWARE_PROFILE:-$MATRIX_HARDWARE_PROFILE}"
export VSS_PUBLIC_URL="${VSS_PUBLIC_URL:-$MATRIX_VSS_PUBLIC_URL}"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-vss-perf-compose-base}"
export COMPOSE_PROFILES="elasticsearch,elasticsearch-init-container,kafka,kafka-topic-init-container,broker-health-check,redis,centralizedb,sensor-ms,streamprocessing-ms,vst-ingress,logstash,vss-video-analytics-api,vss-haproxy-ingress"
export VSS_CONTAINER_REGISTRY="${VSS_CONTAINER_REGISTRY:-nvcr.io/nvstaging/vss-core}"
export VSS_CONTAINER_TAG="${VSS_CONTAINER_TAG:-nightly-20261010}"
export STREAM_TYPE=kafka
export HOST_IP="${HOST_IP:-127.0.0.1}"
export EXTERNAL_IP="${EXTERNAL_IP:-$HOST_IP}"
export VSS_PUBLIC_HOST="${VSS_PUBLIC_HOST:-$HOST_IP}"
export VSS_PUBLIC_PORT="${VSS_PUBLIC_PORT:-7777}"
export VSS_PUBLIC_HTTP_PROTOCOL=http
export VSS_PUBLIC_WS_PROTOCOL=ws
export NGC_CLI_API_KEY="${NGC_CLI_API_KEY:-${NGC_API_KEY:-}}"
export VSS_PERF_STREAM_URL="${VSS_PERF_STREAM_URL:-}"
if [[ -z "${VSS_PERF_VIDEO:-}" && -z "$VSS_PERF_STREAM_URL" ]]; then
    export VSS_PERF_VIDEO="$VSS_DATA_DIR/fixture/Camera_03.mp4"
fi
if [[ -n "${VSS_PERF_VIDEO:-}" ]]; then export VSS_PERF_STREAM_URL="${VSS_PERF_STREAM_URL:-rtsp://perf-media:8554/compose-base}"; fi
# Only verified promoted payloads may replace the deployment source/pins.
if [[ -f "$VSS_DATA_DIR/promoted-artifact.json" && ( -n "${ARTIFACT_REF:-${IMAGE_TAG:-}}" || "${VSS_PERF_STAGE:-}" == teardown ) ]]; then
    _artifact_environment="$("$PYTHON" - "$VSS_DATA_DIR/promoted-artifact.json" <<'PY'
import json, shlex, sys
report=json.load(open(sys.argv[1]))
if report.get('verified') is not True:
    raise SystemExit('Unverified promoted artifact cannot supply deployment settings')
requested=__import__('os').environ.get('ARTIFACT_REF') or __import__('os').environ.get('IMAGE_TAG')
if requested and report.get('artifact_ref') != requested:
    if __import__('os').environ.get('VSS_PERF_STAGE') == 'pre_flight':
        raise SystemExit(0)
    raise SystemExit('Cached promoted report does not match requested artifact')
print('export VSS_APPS_DIR='+shlex.quote(report['compose_root']))
print('export VST_CONFIG_PATH='+shlex.quote(__import__('os').environ['VSS_DATA_DIR']+'/vios-configs-'+report['compose_tree_sha256'][:12]))
for key,value in report['environment'].items():
    if not key.startswith('VSS_') or not (key.endswith('_IMAGE') or key.endswith('_TAG')):
        raise SystemExit('Unexpected variable in promoted deployment pins')
    print('export '+key+'='+shlex.quote(str(value)))
PY
)" || exit $?
    eval "$_artifact_environment"
fi
DOCKER_CMD=(docker)
if [[ "${DOCKER_USE_SUDO:-false}" == true ]]; then DOCKER_CMD=(sudo -n docker); fi
COMPOSE_CMD=("${DOCKER_CMD[@]}" compose --project-name "$COMPOSE_PROJECT_NAME" --env-file "$VSS_APPS_DIR/containers.env" --env-file "$VSS_APPS_DIR/services/vios/vst.env" -f "$COMPOSE_FILE")
if [[ -n "${VSS_PERF_VIDEO:-}" ]]; then COMPOSE_CMD+=(-f "$VSS_PERF_ROOT/setup/compose/media.yaml"); fi

if [[ "${VSS_PERF_STAGE:-}" == pre_flight ]]; then
    mkdir -p "$VSS_DATA_DIR"
    umask 077
    # Only lifecycle coordinates persist. Registry and host credentials never do.
    for key in CONFIG_ID PERF_CONFIG PERF_CONFIGS VSS_DATA_DIR COMPOSE_FILE RT_CV_DEVICE_ID HARDWARE_PROFILE VSS_PUBLIC_URL COMPOSE_PROJECT_NAME DOCKER_USE_SUDO VSS_PERF_VIDEO ARTIFACT_REF IMAGE_TAG PERF_OUTPUT_DIR PERF_SCENARIO RELEASE TRIGGERED_BY; do
        printf 'export %s=%q\n' "$key" "${!key:-}"
    done > "$STATE_FILE"
fi

record_stage_failure() {
    local status=$?
    trap - EXIT
    if [[ "$status" != 0 && -n "${PERF_OUTPUT_DIR:-}" ]]; then
        mkdir -p "$PERF_OUTPUT_DIR"
        local args=(--config "$PERF_CONFIG" --perf-config "$PERF_CONFIGS" --config-id "$CONFIG_ID" --output-json "$PERF_OUTPUT_DIR" --scenario "${PERF_SCENARIO:-quick_test}" --triggered-by "${TRIGGERED_BY:-artifact_promotion}" --release "${RELEASE:-}" --stage-failure "$VSS_PERF_STAGE" --failure-message "Lifecycle stage $VSS_PERF_STAGE exited $status")
        local existing
        existing="$("$PYTHON" - "$PERF_OUTPUT_DIR" "$CONFIG_ID" <<'PY'
import json, pathlib, sys
matches=[]
for path in pathlib.Path(sys.argv[1]).glob('*.json'):
    result=json.loads(path.read_text())
    if result.get('metadata',{}).get('config',{}).get('config_id') == sys.argv[2]:
        matches.append(str(path))
if len(matches)>1:
    raise SystemExit('Multiple standard results found for one shared run')
print(matches[0] if matches else '')
PY
)"
        [[ -z "$existing" ]] || args+=(--existing-result "$existing")
        if [[ "${UPLOAD_AFTER_TEARDOWN:-false}" != true && "${UPLOAD_TO_MINIO:-false}" == true && -n "${MINIO_ACCESS_KEY:-}" && -n "${MINIO_SECRET_KEY:-}" ]]; then args+=(--upload); fi
        "$PYTHON" "$VSS_PERF_ROOT/benchmark/core/vss_perf_benchmark.py" "${args[@]}" > "$PERF_OUTPUT_DIR/stage-failure.log" || true
    fi
    exit "$status"
}
# Local wrapper owns its result and teardown status. Direct shared hooks record
# failure at the outer timeout boundary, including when their child is killed.
if [[ "${VSS_PERF_WRAPPER:-false}" != true && "${VSS_PERF_BOUNDED_STAGE:-}" != "${VSS_PERF_STAGE:-}" ]]; then
    trap record_stage_failure EXIT
fi
