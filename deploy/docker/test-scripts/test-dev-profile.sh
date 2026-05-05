#!/bin/bash
# Test script for deploy/docker/scripts/dev-profile.sh
# Uses --dry-run for positive cases so no docker compose is started.
#
# Coverage: help, positional/options validation, profile/hardware/mode/LLM/VLM
# rules, dry-run up for all profiles, dry-run down, generated.env contents.
# Gaps (see "Gaps" section below): getopt invalid usage, source .env missing,
# remote API model name failure, VLM custom weights path missing.

set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
DEV_PROFILE="${REPO_ROOT}/deploy/docker/scripts/dev-profile.sh"
# NGC key from env (required for 'up'); tests use a dummy unless already set.
# Must be exported: dev-profile.sh runs as a child process and only reads the environment.
NGC_CLI_API_KEY="${NGC_CLI_API_KEY:-test-key-for-dry-run}"
export NGC_CLI_API_KEY
# Skip hardware-profile vs nvidia-smi check so tests that pass a specific profile (e.g. DGX-SPARK) pass on CI without that GPU.
# Unset SKIP_HARDWARE_CHECK in tests that assert the fail-fast mismatch behavior.
export SKIP_HARDWARE_CHECK=true
# Per-test timeout (seconds); dry-run can be slow on first run
TEST_TIMEOUT="${TEST_TIMEOUT:-15}"
TESTS_PASSED=0
TESTS_FAILED=0

# Cleanup on exit or signal so we don't leave mock servers, temp dirs, or modified repo files
CLEANUP_PIDS=()
CLEANUP_RESTORES=()  # elements: "backup_file|dest_path"
CLEANUP_DIRS=()
cleanup() {
  local p pair b d
  set +e
  for p in "${CLEANUP_PIDS[@]}"; do
    kill "$p" 2>/dev/null || true
    wait "$p" 2>/dev/null || true
  done
  for pair in "${CLEANUP_RESTORES[@]}"; do
    IFS='|' read -r b d <<< "${pair}"
    [[ -n "${b}" ]] && [[ -f "${b}" ]] && mv "${b}" "${d}" || true
  done
  for d in "${CLEANUP_DIRS[@]}"; do
    [[ -n "${d}" ]] && [[ -d "${d}" ]] && rm -rf "${d}" || true
  done
  set -e
}
trap 'cleanup; exit 130' SIGINT SIGTERM
trap cleanup EXIT

run_test() {
  local name="$1"
  local expected_exit="${2:-0}"
  shift 2
  local out_file
  out_file="$(mktemp)"
  local err_file
  err_file="$(mktemp)"
  local exit_code=0

  cd "${REPO_ROOT}"
  set +e
  "$DEV_PROFILE" "$@" > "${out_file}" 2> "${err_file}"
  exit_code=$?
  set -e

  if [[ ${exit_code} -ne ${expected_exit} ]]; then
    echo "FAIL: ${name} (expected exit ${expected_exit}, got ${exit_code})"
    echo "  stdout:"
    sed 's/^/    /' "${out_file}"
    echo "  stderr:"
    sed 's/^/    /' "${err_file}"
    ((TESTS_FAILED++)) || true
    rm -f "${out_file}" "${err_file}"
    return
  fi

  # Optional: check stdout/stderr content (caller can run assertions after)
  export TEST_STDOUT="${out_file}"
  export TEST_STDERR="${err_file}"
  echo "PASS: ${name}"
  ((TESTS_PASSED++)) || true
  rm -f "${out_file}" "${err_file}"
}

assert_stdout_contains() {
  local name="$1"
  local pattern="$2"
  local out_file="${3:-$TEST_STDOUT}"
  if [[ -f "${out_file}" ]] && grep -q "${pattern}" "${out_file}"; then
    echo "PASS: ${name} (stdout contains expected pattern)"
    ((TESTS_PASSED++)) || true
  else
    echo "FAIL: ${name} (stdout did not contain: ${pattern})"
    ((TESTS_FAILED++)) || true
  fi
}

# Run a positive dry-run test and assert on output
run_dry_run_test() {
  local name="$1"
  shift
  local out_file
  out_file="$(mktemp)"
  local err_file
  err_file="$(mktemp)"
  local exit_code=0

  cd "${REPO_ROOT}"
  set +e
  timeout "${TEST_TIMEOUT}" "$DEV_PROFILE" "$@" > "${out_file}" 2> "${err_file}"
  exit_code=$?
  set -e
  if [[ ${exit_code} -eq 124 ]]; then
    echo "FAIL: ${name} (timed out after ${TEST_TIMEOUT}s)"
    ((TESTS_FAILED++)) || true
    rm -f "${out_file}" "${err_file}"
    return
  fi

  if [[ ${exit_code} -ne 0 ]]; then
    echo "FAIL: ${name} (expected exit 0, got ${exit_code})"
    echo "  stdout:"
    sed 's/^/    /' "${out_file}"
    echo "  stderr:"
    sed 's/^/    /' "${err_file}"
    ((TESTS_FAILED++)) || true
    rm -f "${out_file}" "${err_file}"
    return
  fi

  # Must contain dry-run section and DRY-RUN commands (no actual docker run)
  local failed=0
  if ! grep -q "=== Captured Arguments ===" "${out_file}"; then
    echo "FAIL: ${name} (stdout missing '=== Captured Arguments ===')"
    ((failed++)) || true
  fi
  if ! grep -q "dry-run:                   true" "${out_file}"; then
    echo "FAIL: ${name} (stdout missing 'dry-run: true')"
    ((failed++)) || true
  fi
  if ! grep -q "\[DRY-RUN\]" "${out_file}"; then
    echo "FAIL: ${name} (stdout missing any [DRY-RUN] line)"
    ((failed++)) || true
  fi

  if [[ ${failed} -gt 0 ]]; then
    ((TESTS_FAILED++)) || true
    echo "  stdout (first 80 lines):"
    head -80 "${out_file}" | sed 's/^/    /'
  else
    echo "PASS: ${name}"
    ((TESTS_PASSED++)) || true
  fi
  rm -f "${out_file}" "${err_file}"
}

# Run a negative test (expect exit 1 and [ERROR] in stderr or stdout)
run_negative_test() {
  local name="$1"
  local expected_exit="${2:-1}"
  shift 2
  local out_file
  out_file="$(mktemp)"
  local err_file
  err_file="$(mktemp)"
  local exit_code=0

  cd "${REPO_ROOT}"
  set +e
  timeout "${TEST_TIMEOUT}" "$DEV_PROFILE" "$@" > "${out_file}" 2> "${err_file}"
  exit_code=$?
  set -e
  if [[ ${exit_code} -eq 124 ]]; then
    echo "FAIL: ${name} (timed out after ${TEST_TIMEOUT}s)"
    ((TESTS_FAILED++)) || true
    rm -f "${out_file}" "${err_file}"
    return
  fi
  if [[ ${exit_code} -ne ${expected_exit} ]]; then
    echo "FAIL: ${name} (expected exit ${expected_exit}, got ${exit_code})"
    echo "  stdout:"
    sed 's/^/    /' "${out_file}"
    echo "  stderr:"
    sed 's/^/    /' "${err_file}"
    ((TESTS_FAILED++)) || true
    rm -f "${out_file}" "${err_file}"
    return
  fi

  if ! grep -q "\[ERROR\]" "${out_file}" && ! grep -q "\[ERROR\]" "${err_file}"; then
    echo "FAIL: ${name} (expected [ERROR] in output)"
    echo "  stdout:"
    sed 's/^/    /' "${out_file}"
    echo "  stderr:"
    sed 's/^/    /' "${err_file}"
    ((TESTS_FAILED++)) || true
  else
    echo "PASS: ${name}"
    ((TESTS_PASSED++)) || true
  fi
  rm -f "${out_file}" "${err_file}"
}

# Path to generated.env for a profile (under deploy/docker/developer-profiles).
generated_env_path() {
  local profile="${1}"
  echo "${REPO_ROOT}/deploy/docker/developer-profiles/dev-profile-${profile}/generated.env"
}

# Read a variable value from a profile's generated.env (key=value, value is rest of line).
get_generated_env_value() {
  local env_file="${1}"
  local var="${2}"
  if [[ -f "${env_file}" ]]; then
    grep "^${var}=" "${env_file}" 2>/dev/null | cut -d= -f2- | head -1
  fi
}

# Read the value from a profile .env's commented line for KEY that contains sbsa (the line that DGX-SPARK will activate).
# Used so DGX-SPARK tests assert "script activated the sbsa variant" without hardcoding tag versions.
get_commented_sbsa_value() {
  local env_file="${1}"
  local key="${2}"
  [[ -f "${env_file}" ]] || return
  grep -E "^#[[:space:]]*${key}=" "${env_file}" 2>/dev/null | grep -F 'sbsa' | head -1 | cut -d= -f2-
}

# Discover env var names that have a commented line with sbsa in the value (same pattern as dev-profile.sh).
# Output: one key per line. Use when a profile may have zero or more sbsa-tagged vars.
get_commented_sbsa_keys() {
  local env_file="${1}"
  [[ -f "${env_file}" ]] || return
  grep -E '^#[[:space:]]*[A-Za-z0-9_]+=.*sbsa' "${env_file}" 2>/dev/null | sed -nE 's/^#[[:space:]]*([A-Za-z0-9_]+)=.*/\1/p' | sort -u
}

# Run one DGX-SPARK dry-run test for a profile: discover sbsa keys from profile .env, run up -H DGX-SPARK, assert.
# Skips if profile .env is missing. Alerts gets -m real-time.
run_spark_test_for_profile() {
  local profile="${1}"
  local env_file="${REPO_ROOT}/deploy/docker/developer-profiles/dev-profile-${profile}/.env"
  [[ -f "${env_file}" ]] || return 0
  local check_args=("HARDWARE_PROFILE" "DGX-SPARK")
  local key val
  while IFS= read -r key; do
    [[ -z "${key}" ]] && continue
    val="$(get_commented_sbsa_value "${env_file}" "${key}")"
    [[ -n "${val}" ]] && check_args+=("${key}" "${val}")
  done < <(get_commented_sbsa_keys "${env_file}")
  local run_args=(-i 127.0.0.1 -H DGX-SPARK -d)
  [[ "${profile}" == "alerts" ]] && run_args+=(-m real-time)
  run_dry_run_up_and_check_generated_env "generated.env DGX-SPARK swaps to sbsa tags (${profile})" "${profile}" \
    "${run_args[@]}" -- "${check_args[@]}"
}

# Run dev-profile up with dry-run, then assert expected key=value in generated.env, then restore.
# Usage: run_dry_run_up_and_check_generated_env "test name" "profile" "arg1" "arg2" ... -- "VAR1" "value1" "VAR2" "value2" ...
# Args after -- are pairs: var name, expected value (optional; if omitted for a var, only check var is set and non-empty).
run_dry_run_up_and_check_generated_env() {
  local name="${1}"
  local profile="${2}"
  shift 2
  local args=()
  local checks=()
  local sep_seen=0
  while [[ $# -gt 0 ]]; do
    if [[ "${1}" == "--" ]]; then
      sep_seen=1
      shift
      continue
    fi
    if [[ ${sep_seen} -eq 0 ]]; then
      args+=("${1}")
    else
      checks+=("${1}")
    fi
    shift
  done

  local gen_env
  gen_env="$(generated_env_path "${profile}")"
  local backup_file=""
  if [[ -f "${gen_env}" ]]; then
    backup_file="$(mktemp)"
    cp "${gen_env}" "${backup_file}"
    CLEANUP_RESTORES+=("${backup_file}|${gen_env}")
  fi

  cd "${REPO_ROOT}"
  set +e
  local out_file
  out_file="$(mktemp)"
  local err_file
  err_file="$(mktemp)"
  timeout "${TEST_TIMEOUT}" "$DEV_PROFILE" up -p "${profile}" "${args[@]}" > "${out_file}" 2> "${err_file}"
  local exit_code=$?
  set -e

  if [[ ${exit_code} -eq 124 ]]; then
    echo "FAIL: ${name} (timed out)"
    ((TESTS_FAILED++)) || true
    [[ -n "${backup_file}" && -f "${backup_file}" ]] && mv "${backup_file}" "${gen_env}"
    rm -f "${out_file}" "${err_file}"
    return
  fi
  if [[ ${exit_code} -ne 0 ]]; then
    echo "FAIL: ${name} (dev-profile exit ${exit_code})"
    sed 's/^/    /' "${out_file}"
    ((TESTS_FAILED++)) || true
    [[ -n "${backup_file}" && -f "${backup_file}" ]] && mv "${backup_file}" "${gen_env}"
    rm -f "${out_file}" "${err_file}"
    return
  fi

  local failed=0
  local i=0
  while [[ $i -lt ${#checks[@]} ]]; do
    local var="${checks[$i]}"
    local expected=""
    if [[ $((i + 1)) -lt ${#checks[@]} ]]; then
      expected="${checks[$((i+1))]}"
    fi
    local actual
    actual="$(get_generated_env_value "${gen_env}" "${var}")"
    if [[ -z "${actual}" ]]; then
      if [[ -n "${expected}" ]]; then
        echo "FAIL: ${name} (generated.env missing or empty: ${var})"
        ((failed++)) || true
      fi
      # When expected is empty, empty actual is acceptable
    elif [[ -n "${expected}" && "${actual}" != "${expected}" ]]; then
      echo "FAIL: ${name} (generated.env ${var}: expected '${expected}', got '${actual}')"
      ((failed++)) || true
    fi
    i=$((i + 2))
  done

  if [[ -n "${backup_file}" && -f "${backup_file}" ]]; then
    mv "${backup_file}" "${gen_env}"
  else
    rm -f "${gen_env}"
  fi

  rm -f "${out_file}" "${err_file}"
  if [[ ${failed} -gt 0 ]]; then
    ((TESTS_FAILED++)) || true
  else
    echo "PASS: ${name}"
    ((TESTS_PASSED++)) || true
  fi
}

# --- Help (exit 0, no [ERROR]) ---
out_file="$(mktemp)"
err_file="$(mktemp)"
cd "${REPO_ROOT}"
set +e
timeout "${TEST_TIMEOUT}" "$DEV_PROFILE" --help > "${out_file}" 2> "${err_file}"
exit_code=$?
set -e
if [[ ${exit_code} -eq 124 ]]; then
  echo "FAIL: --help (timed out)"
  ((TESTS_FAILED++)) || true
elif [[ ${exit_code} -ne 0 ]]; then
  echo "FAIL: --help (expected exit 0, got ${exit_code})"
  ((TESTS_FAILED++)) || true
elif ! grep -q "Usage:" "${out_file}"; then
  echo "FAIL: --help (stdout missing 'Usage:')"
  ((TESTS_FAILED++)) || true
else
  echo "PASS: --help"
  ((TESTS_PASSED++)) || true
fi
rm -f "${out_file}" "${err_file}"

out_file="$(mktemp)"
err_file="$(mktemp)"
timeout "${TEST_TIMEOUT}" "$DEV_PROFILE" -h > "${out_file}" 2> "${err_file}"
exit_code=$?
if [[ ${exit_code} -eq 124 ]]; then
  echo "FAIL: -h (timed out)"
  ((TESTS_FAILED++)) || true
elif [[ ${exit_code} -ne 0 ]]; then
  echo "FAIL: -h (expected exit 0, got ${exit_code})"
  ((TESTS_FAILED++)) || true
else
  echo "PASS: -h"
  ((TESTS_PASSED++)) || true
fi
rm -f "${out_file}" "${err_file}"

# --- Negative: invalid or missing args ---
run_negative_test "getopt invalid usage (unknown option)" 1 up -p base --unknown-option
run_negative_test "getopt invalid usage (unknown short option)" 1 up -p base -z
run_negative_test "invalid option -k (use NGC_CLI_API_KEY env)" 1 up -p base -k x
run_negative_test "no args → desired-state required" 1
run_negative_test "invalid desired-state" 1 invalid_state
run_negative_test "up without --profile" 1 up
NGC_CLI_API_KEY= run_negative_test "up without ngc key (no env)" 1 up -p base
run_negative_test "invalid profile" 1 up -p invalid
run_negative_test "invalid hardware-profile" 1 up -p base -H INVALID
# Fail-fast: requested hardware_profile must match detected GPU (nvidia-smi); OTHER is catchall when no match.
SKIP_HARDWARE_CHECK= run_negative_test "hardware profile does not match (no GPU, requested DGX-SPARK)" 1 up -p base -i 127.0.0.1 -H DGX-SPARK -d
run_negative_test "DGX-SPARK only valid for base or alerts (not lvs)" 1 up -p lvs -i 127.0.0.1 -H DGX-SPARK
run_negative_test "DGX-SPARK only valid for base or alerts (not search)" 1 up -p search -i 127.0.0.1 -H DGX-SPARK
run_negative_test "alerts without --mode" 1 up -p alerts -i 127.0.0.1
run_negative_test "IGX-THOR only valid for base or alerts (not lvs)" 1 up -p lvs -i 127.0.0.1 -H IGX-THOR
run_negative_test "IGX-THOR only valid for base or alerts (not search)" 1 up -p search -i 127.0.0.1 -H IGX-THOR
run_negative_test "AGX-THOR only valid for base or alerts (not lvs)" 1 up -p lvs -i 127.0.0.1 -H AGX-THOR
run_negative_test "AGX-THOR only valid for base or alerts (not search)" 1 up -p search -i 127.0.0.1 -H AGX-THOR
run_negative_test "invalid mode for alerts" 1 up -p alerts -m invalid
run_negative_test "mode only accepted for alerts profile" 1 up -p base -m verification
run_negative_test "down with extra option not allowed" 1 down --profile base
run_dry_run_test "search allows --vlm" up -p search -i 127.0.0.1 --vlm nvidia/cosmos-reason1-7b -d
run_dry_run_test "search allows --vlm-device-id" up -p search -i 127.0.0.1 --vlm-device-id 2 -d
run_negative_test "invalid option --llm-mode" 1 up -p base --llm-mode remote
run_negative_test "invalid option --shared-llm-vlm-device-id" 1 up -p base --shared-llm-vlm-device-id 0

LLM_ENDPOINT_URL=http://127.0.0.1:8000 VLM_ENDPOINT_URL=http://127.0.0.1:8001 run_dry_run_test "DGX-SPARK allows remote+remote" up -p base -i 127.0.0.1 -H DGX-SPARK --use-remote-llm --llm x --use-remote-vlm --vlm y -d
LLM_ENDPOINT_URL=http://127.0.0.1:8000 run_dry_run_test "DGX-SPARK allows remote + local_shared (LLM remote, VLM local_shared)" up -p base -i 127.0.0.1 -H DGX-SPARK --use-remote-llm --llm x -d
VLM_ENDPOINT_URL=http://127.0.0.1:8001 run_dry_run_test "DGX-SPARK allows remote + local_shared (LLM local_shared, VLM remote)" up -p base -i 127.0.0.1 -H DGX-SPARK --use-remote-vlm --vlm y -d
LLM_ENDPOINT_URL=http://127.0.0.1:8000 run_dry_run_test "DGX-SPARK remote + local_shared without device-id options (device ID set to 0)" up -p base -i 127.0.0.1 -H DGX-SPARK --use-remote-llm --llm x -d
VLM_ENDPOINT_URL=http://127.0.0.1:8001 run_negative_test "edge hardware rejects --llm-device-id" 1 up -p base -i 127.0.0.1 -H DGX-SPARK --use-remote-vlm --vlm y --llm-device-id 0 -d
LLM_ENDPOINT_URL=http://127.0.0.1:8000 run_negative_test "edge hardware rejects --vlm-device-id" 1 up -p alerts -i 127.0.0.1 -m verification -H DGX-SPARK --use-remote-llm --llm x --vlm-device-id 0 -d
VLM_ENDPOINT_URL=http://127.0.0.1:8001 run_dry_run_up_and_check_generated_env "generated.env edge hardware LLM_DEVICE_ID VLM_DEVICE_ID=0 (DGX-SPARK remote+local_shared)" "base" \
 -i 127.0.0.1 -H DGX-SPARK --use-remote-vlm --vlm y -d -- \
  "LLM_DEVICE_ID" "0" "VLM_DEVICE_ID" "0"
# Base on IGX-THOR: same VLM constraints as alerts on IGX-THOR (no --use-remote-vlm, etc.)
LLM_ENDPOINT_URL=http://127.0.0.1:8000 VLM_ENDPOINT_URL=http://127.0.0.1:8001 run_negative_test "base on IGX-THOR rejects --use-remote-vlm" 1 up -p base -i 127.0.0.1 -H IGX-THOR --use-remote-llm --llm x --use-remote-vlm --vlm y -d
LLM_ENDPOINT_URL=http://127.0.0.1:8000 VLM_ENDPOINT_URL=http://127.0.0.1:8001 run_negative_test "base on AGX-THOR rejects --use-remote-vlm" 1 up -p base -i 127.0.0.1 -H AGX-THOR --use-remote-llm --llm x --use-remote-vlm --vlm y -d
run_dry_run_up_and_check_generated_env "generated.env base IGX-THOR VLM and RTVI vars and device IDs" "base" \
 -i 127.0.0.1 -H IGX-THOR -d -- \
  "LLM_DEVICE_ID" "0" "VLM_DEVICE_ID" "0" "VLM_NAME_SLUG" "none" "VLM_NAME" "nim_nvidia_cosmos-reason2-8b_hf-1208" "VLM_BASE_URL" "http://127.0.0.1:8018" "VLM_MODEL_TYPE" "rtvi" "RTVI_VLM_MODEL_PATH" "ngc:nim/nvidia/cosmos-reason2-8b:hf-1208" "RTVI_VLM_MODEL_TO_USE" "cosmos-reason2" "RTVI_VLLM_GPU_MEMORY_UTILIZATION" "0.35"
run_dry_run_up_and_check_generated_env "generated.env base AGX-THOR VLM and RTVI vars (same as IGX-THOR)" "base" \
 -i 127.0.0.1 -H AGX-THOR -d -- \
  "LLM_DEVICE_ID" "0" "VLM_DEVICE_ID" "0" "VLM_NAME_SLUG" "none" "VLM_NAME" "nim_nvidia_cosmos-reason2-8b_hf-1208" "VLM_BASE_URL" "http://127.0.0.1:8018" "VLM_MODEL_TYPE" "rtvi" "RTVI_VLM_MODEL_PATH" "ngc:nim/nvidia/cosmos-reason2-8b:hf-1208" "RTVI_VLM_MODEL_TO_USE" "cosmos-reason2" "RTVI_VLLM_GPU_MEMORY_UTILIZATION" "0.35"
run_negative_test "base on IGX-THOR rejects --vlm" 1 up -p base -i 127.0.0.1 -H IGX-THOR --vlm nvidia/cosmos-reason2-8b -d
run_negative_test "base on AGX-THOR rejects --vlm" 1 up -p base -i 127.0.0.1 -H AGX-THOR --vlm nvidia/cosmos-reason2-8b -d
run_negative_test "base on IGX-THOR rejects --vlm-env-file" 1 up -p base -i 127.0.0.1 -H IGX-THOR --vlm-env-file /some/vlm.env -d
run_negative_test "base on AGX-THOR rejects --vlm-env-file" 1 up -p base -i 127.0.0.1 -H AGX-THOR --vlm-env-file /some/vlm.env -d
# LLM remote (set by --use-remote-llm): forbidden options and LLM_ENDPOINT_URL required when flag passed
run_negative_test "LLM_ENDPOINT_URL must be set when --use-remote-llm is passed" 1 up -p base --use-remote-llm --llm x
_tmp_llm_env="$(mktemp)"
LLM_ENDPOINT_URL=http://localhost:8000 run_negative_test "llm-device-id not allowed when LLM_MODE=remote" 1 up -p base --use-remote-llm --llm-device-id 0
LLM_ENDPOINT_URL=http://localhost:8000 run_negative_test "llm-env-file not allowed when LLM_MODE=remote" 1 up -p base --use-remote-llm --llm-env-file "${_tmp_llm_env}"
rm -f "${_tmp_llm_env}"
run_negative_test "invalid LLM model name" 1 up -p base --llm invalid-llm
run_negative_test "invalid option --nvidia-api-key (use NVIDIA_API_KEY env)" 1 up -p base --nvidia-api-key x
run_negative_test "llm-model-type not allowed when LLM_MODE not remote" 1 up -p base --llm-model-type openai
run_negative_test "vlm-model-type not allowed when VLM_MODE not remote" 1 up -p base --vlm-model-type openai
run_negative_test "invalid option --openai-api-key (use OPENAI_API_KEY env)" 1 up -p base --openai-api-key sk-x
LLM_ENDPOINT_URL=http://localhost:8000 VLM_ENDPOINT_URL=http://localhost:8001 run_negative_test "invalid llm-model-type when LLM_MODE=remote" 1 up -p base --use-remote-llm --llm m --use-remote-vlm --vlm m --llm-model-type foo

# Search profile: VLM env file (must exist; same rules as other profiles)
_tmp_search_vlm_env="$(mktemp)"
_search_vlm_env_abs="$(cd "$(dirname "${_tmp_search_vlm_env}")" && pwd)/$(basename "${_tmp_search_vlm_env}")"
run_dry_run_up_and_check_generated_env "generated.env search allows VLM_ENV_FILE" "search" \
  -i 127.0.0.1 --vlm-env-file "${_search_vlm_env_abs}" -d -- \
  "VLM_ENV_FILE" "${_search_vlm_env_abs}"
rm -f "${_tmp_search_vlm_env}"

# VLM remote (set by --use-remote-vlm): forbidden options and VLM_ENDPOINT_URL required when flag passed
# When VLM is remote, host VLM_CUSTOM_WEIGHTS is ignored (not written to generated.env), no error
run_negative_test "VLM_ENDPOINT_URL must be set when --use-remote-vlm is passed" 1 up -p base --use-remote-vlm --vlm y
VLM_ENDPOINT_URL=http://localhost:8000 run_negative_test "vlm-device-id not allowed when VLM_MODE=remote" 1 up -p base --use-remote-vlm --vlm-device-id 0
_tmp_vlm_env="$(mktemp)"
LLM_ENDPOINT_URL=http://localhost:8000 VLM_ENDPOINT_URL=http://localhost:8000 run_negative_test "vlm-env-file not allowed when VLM_MODE=remote" 1 up -p base --use-remote-llm --use-remote-vlm --vlm-env-file "${_tmp_vlm_env}"
rm -f "${_tmp_vlm_env}"
run_negative_test "invalid VLM model name" 1 up -p base --vlm invalid-vlm

# RESERVED_DEVICE_IDS: device IDs in profile .env must not be used (alerts has RESERVED_DEVICE_IDS='0')
# Note: shared-llm-vlm-device-id is now from profile only; reserved check for it would require profile to set SHARED_LLM_VLM_DEVICE_ID=0
run_negative_test "llm-device-id must not be in RESERVED_DEVICE_IDS" 1 up -p alerts -i 127.0.0.1 -m verification --llm-device-id 0 --vlm-device-id 1
run_negative_test "vlm-device-id must not be in RESERVED_DEVICE_IDS" 1 up -p alerts -i 127.0.0.1 -m verification --llm-device-id 1 --vlm-device-id 0

# L40S: neither LLM nor VLM may be local_shared (device ID cannot be shared with other services)
run_negative_test "L40S rejects local_shared LLM" 1 up -p search -i 127.0.0.1 -H L40S -d
run_negative_test "L40S rejects local_shared VLM" 1 up -p base -i 127.0.0.1 -H L40S --llm-device-id 0 --vlm-device-id 0 -d

# Edge hardware: device IDs fixed to 0; profile defaults used for mode when no base URL override
run_dry_run_test "edge (DGX-SPARK) local_shared+local_shared uses device ID 0" up -p alerts -i 127.0.0.1 -m verification -H DGX-SPARK -d
# Alerts on IGX-THOR / AGX-THOR: VLM options not accepted (any mode); fixed VLM/RTVI env set for all alerts
run_dry_run_test "edge (IGX-THOR) alerts verification uses device ID 0" up -p alerts -i 127.0.0.1 -m verification -H IGX-THOR -d
run_dry_run_test "edge (AGX-THOR) alerts verification uses device ID 0" up -p alerts -i 127.0.0.1 -m verification -H AGX-THOR -d
run_dry_run_test "edge (IGX-THOR) alerts real-time uses device ID 0 (no VLM overrides)" up -p alerts -i 127.0.0.1 -m real-time -H IGX-THOR -d
run_dry_run_test "edge (AGX-THOR) alerts real-time uses device ID 0 (no VLM overrides)" up -p alerts -i 127.0.0.1 -m real-time -H AGX-THOR -d
# Alerts on IGX-THOR / AGX-THOR: RT_VLM_DEVICE_ID hardcoded to 0; RTVI_VLLM_GPU_MEMORY_UTILIZATION is an option (mirrors NIM hw-H100.env pattern: ${VLM_NIM_KVCACHE_PERCENT}), flows through from env (unset → empty).
run_dry_run_up_and_check_generated_env "generated.env alerts IGX-THOR VLM vars (RT_VLM_DEVICE_ID=0)" "alerts" \
  -i 127.0.0.1 -m verification -H IGX-THOR -d -- \
  "VLM_NAME_SLUG" "none" "VLM_NAME" "nim_nvidia_cosmos-reason2-8b_hf-1208" "VLM_BASE_URL" "http://127.0.0.1:8018" "RTVI_VLM_MODEL_PATH" "ngc:nim/nvidia/cosmos-reason2-8b:hf-1208" "RTVI_VLM_MODEL_TO_USE" "cosmos-reason2" "RT_VLM_DEVICE_ID" "0"
run_dry_run_up_and_check_generated_env "generated.env alerts AGX-THOR VLM vars (RT_VLM_DEVICE_ID=0)" "alerts" \
  -i 127.0.0.1 -m verification -H AGX-THOR -d -- \
  "VLM_NAME_SLUG" "none" "VLM_NAME" "nim_nvidia_cosmos-reason2-8b_hf-1208" "VLM_BASE_URL" "http://127.0.0.1:8018" "RTVI_VLM_MODEL_PATH" "ngc:nim/nvidia/cosmos-reason2-8b:hf-1208" "RTVI_VLM_MODEL_TO_USE" "cosmos-reason2" "RT_VLM_DEVICE_ID" "0"
# Alerts on IGX-THOR/AGX-THOR: RTVI_VLLM_GPU_MEMORY_UTILIZATION env var flows through to generated.env (option pattern, like ${VLM_NIM_KVCACHE_PERCENT} in NIM hw-H100.env).
RTVI_VLLM_GPU_MEMORY_UTILIZATION=0.5 run_dry_run_up_and_check_generated_env "generated.env alerts IGX-THOR RTVI_VLLM_GPU_MEMORY_UTILIZATION env passes through" "alerts" \
  -i 127.0.0.1 -m verification -H IGX-THOR -d -- \
  "RTVI_VLLM_GPU_MEMORY_UTILIZATION" "0.5"
RTVI_VLLM_GPU_MEMORY_UTILIZATION=0.6 run_dry_run_up_and_check_generated_env "generated.env alerts AGX-THOR RTVI_VLLM_GPU_MEMORY_UTILIZATION env passes through" "alerts" \
  -i 127.0.0.1 -m verification -H AGX-THOR -d -- \
  "RTVI_VLLM_GPU_MEMORY_UTILIZATION" "0.6"
# Alerts on DGX-SPARK (edge, forced device 0 → local_shared): RTVI_VLLM_GPU_MEMORY_UTILIZATION hardcoded to 0.35 (shared), mirrors NIM hw-DGX-SPARK-shared.env pattern (hardcoded KV cache).
run_dry_run_up_and_check_generated_env "generated.env alerts DGX-SPARK RTVI_VLLM_GPU_MEMORY_UTILIZATION=0.35 (local_shared hardcoded)" "alerts" \
  -i 127.0.0.1 -m verification -H DGX-SPARK -d -- \
  "RTVI_VLLM_GPU_MEMORY_UTILIZATION" "0.35"
# Alerts on OTHER (no NIM hw env file counterpart): RTVI_VLLM_GPU_MEMORY_UTILIZATION not set by script (stays as profile .env default, which is empty).
run_dry_run_up_and_check_generated_env "generated.env alerts OTHER RTVI_VLLM_GPU_MEMORY_UTILIZATION not set by script" "alerts" \
  -i 127.0.0.1 -m verification -H OTHER -d -- \
  "RTVI_VLLM_GPU_MEMORY_UTILIZATION" ""
run_negative_test "alerts on IGX-THOR rejects --use-remote-vlm" 1 up -p alerts -i 127.0.0.1 -m verification -H IGX-THOR --use-remote-vlm --vlm y -d
run_negative_test "alerts on AGX-THOR rejects --use-remote-vlm" 1 up -p alerts -i 127.0.0.1 -m verification -H AGX-THOR --use-remote-vlm --vlm y -d
run_negative_test "alerts on IGX-THOR rejects --vlm" 1 up -p alerts -i 127.0.0.1 -m verification -H IGX-THOR --vlm nvidia/cosmos-reason2-8b -d
run_negative_test "alerts on AGX-THOR rejects --vlm" 1 up -p alerts -i 127.0.0.1 -m verification -H AGX-THOR --vlm nvidia/cosmos-reason2-8b -d
run_negative_test "alerts on IGX-THOR rejects --vlm-device-id" 1 up -p alerts -i 127.0.0.1 -m real-time -H IGX-THOR --vlm-device-id 0 -d
run_negative_test "alerts on AGX-THOR rejects --vlm-device-id" 1 up -p alerts -i 127.0.0.1 -m real-time -H AGX-THOR --vlm-device-id 0 -d
run_negative_test "alerts on IGX-THOR rejects --vlm-model-type" 1 up -p alerts -i 127.0.0.1 -m real-time -H IGX-THOR --vlm-model-type nim -d
run_negative_test "alerts on AGX-THOR rejects --vlm-model-type" 1 up -p alerts -i 127.0.0.1 -m real-time -H AGX-THOR --vlm-model-type nim -d
run_negative_test "alerts on IGX-THOR rejects --vlm-env-file" 1 up -p alerts -i 127.0.0.1 -m real-time -H IGX-THOR --vlm-env-file /some/vlm.env -d
run_negative_test "alerts on AGX-THOR rejects --vlm-env-file" 1 up -p alerts -i 127.0.0.1 -m real-time -H AGX-THOR --vlm-env-file /some/vlm.env -d

VLM_CUSTOM_WEIGHTS=/nonexistent/vlm-weights-path run_negative_test "VLM custom weights path must exist (fail fast)" 1 up -p base -i 127.0.0.1
VLM_CUSTOM_WEIGHTS=/nonexistent/vlm-weights-path run_negative_test "VLM custom weights path must exist in dry-run" 1 up -p base -i 127.0.0.1 -d
VLM_CUSTOM_WEIGHTS=./relative/path run_negative_test "VLM_CUSTOM_WEIGHTS must be absolute path" 1 up -p base -i 127.0.0.1 -d

# Positive: dry-run with existing VLM custom weights path (from host env VLM_CUSTOM_WEIGHTS)
_vlm_weights_tmp="$(mktemp -d)"
CLEANUP_DIRS+=("${_vlm_weights_tmp}")
out_vlm="$(mktemp)"
err_vlm="$(mktemp)"
cd "${REPO_ROOT}"
set +e
VLM_CUSTOM_WEIGHTS="${_vlm_weights_tmp}" timeout "${TEST_TIMEOUT}" "$DEV_PROFILE" up -p base -i 127.0.0.1 -d > "${out_vlm}" 2> "${err_vlm}"
_vlm_exit=$?
set -e
rm -rf "${_vlm_weights_tmp}"
if [[ ${_vlm_exit} -eq 0 ]] && grep -q "Using VLM custom weights path" "${out_vlm}"; then
  echo "PASS: up dry-run with existing VLM custom weights path"
  ((TESTS_PASSED++)) || true
else
  echo "FAIL: up dry-run with existing VLM custom weights path (exit ${_vlm_exit})"
  ((TESTS_FAILED++)) || true
fi
rm -f "${out_vlm}" "${err_vlm}"

# down: only --dry-run allowed
run_negative_test "down only accepts dry-run" 1 down --profile base
# --- Negative: source .env missing ---
_source_env_base="${REPO_ROOT}/deploy/docker/developer-profiles/dev-profile-base/.env"
if [[ -f "${_source_env_base}" ]]; then
  _env_backup="$(mktemp)"
  cp "${_source_env_base}" "${_env_backup}"
  CLEANUP_RESTORES+=("${_env_backup}|${_source_env_base}")
  rm -f "${_source_env_base}"
  out_file="$(mktemp)"
  err_file="$(mktemp)"
  cd "${REPO_ROOT}"
  set +e
  timeout "${TEST_TIMEOUT}" "$DEV_PROFILE" up -p base -i 127.0.0.1 -d > "${out_file}" 2> "${err_file}"
  _exit=$?
  set -e
  mv "${_env_backup}" "${_source_env_base}"
  if [[ ${_exit} -ne 1 ]]; then
    echo "FAIL: source .env missing (expected exit 1, got ${_exit})"
    ((TESTS_FAILED++)) || true
  elif grep -q "Profile .env file not found" "${out_file}" || grep -q "Profile .env file not found" "${err_file}"; then
    echo "PASS: source .env missing (fail-fast: profile .env not found)"
    ((TESTS_PASSED++)) || true
  else
    echo "FAIL: source .env missing (expected 'Profile .env file not found' in output, got other or no error)"
    ((TESTS_FAILED++)) || true
  fi
  rm -f "${out_file}" "${err_file}"
else
  echo "SKIP: source .env missing (base .env not found)"
fi

# --- Negative: remote API failure (unreachable URL, no --llm override) ---
LLM_ENDPOINT_URL=http://127.0.0.1:1 VLM_ENDPOINT_URL=http://127.0.0.1:1 run_negative_test "remote LLM API failure when /v1/models unreachable" 1 up -p base -i 127.0.0.1 --use-remote-llm --use-remote-vlm -d
# Assert the error message (run_negative_test already checks [ERROR]; ensure it's the API message)
out_api="$(mktemp)"
err_api="$(mktemp)"
cd "${REPO_ROOT}"
LLM_ENDPOINT_URL=http://127.0.0.1:1 VLM_ENDPOINT_URL=http://127.0.0.1:1 timeout "${TEST_TIMEOUT}" "$DEV_PROFILE" up -p base -i 127.0.0.1 --use-remote-llm --use-remote-vlm -d > "${out_api}" 2> "${err_api}" || true
if grep -q "Could not get LLM model name" "${out_api}" || grep -q "Could not get LLM model name" "${err_api}"; then
  echo "PASS: remote API failure message mentions LLM model name"
  ((TESTS_PASSED++)) || true
else
  echo "FAIL: remote API failure (expected 'Could not get LLM model name' in output)"
  ((TESTS_FAILED++)) || true
fi
rm -f "${out_api}" "${err_api}"

# --- Positive: dry-run up (no docker compose started) ---
# Use -i 127.0.0.1 to avoid ip route lookup (faster, no network)
run_dry_run_test "up base dry-run with NGC_CLI_API_KEY from env" up -p base -i 127.0.0.1 -d
run_dry_run_test "up base dry-run" up -p base -i 127.0.0.1 -d
run_dry_run_test "up search dry-run" up -p search -i 127.0.0.1 --dry-run
run_dry_run_test "up lvs dry-run" up -p lvs -i 127.0.0.1 -d
run_dry_run_test "up alerts dry-run with mode verification" up -p alerts -i 127.0.0.1 -m verification -d
run_dry_run_test "up base with hardware-profile RTXPRO6000BW" up -p base -i 127.0.0.1 -H RTXPRO6000BW -d
run_dry_run_test "up base with hardware-profile OTHER" up -p base -i 127.0.0.1 -H OTHER -d
run_dry_run_test "up base with llm/vlm" up -p base -i 127.0.0.1 --llm nvidia/nemotron-3-nano --vlm nvidia/cosmos-reason1-7b -d
run_negative_test "llm-env-file must exist" 1 up -p base -i 127.0.0.1 --llm-env-file /nonexistent/llm.env -d
run_negative_test "vlm-env-file must exist" 1 up -p base -i 127.0.0.1 --vlm-env-file ./nonexistent-vlm.env -d
run_dry_run_test "up alerts real-time mode" up -p alerts -i 127.0.0.1 -m real-time -d
# L40S forbids local_shared for LLM/VLM; search profile default is local_shared (device 1 in FIXED_SHARED). Use remote LLM so L40S is allowed.
LLM_ENDPOINT_URL=http://127.0.0.1:1 run_dry_run_test "up search with L40S (allowed)" up -p search -i 127.0.0.1 -H L40S --use-remote-llm --llm x -d

# Search: critic enabled by default → generated.env ENABLE_CRITIC=true when unset or truthy; ENABLE_CRITIC=false + VLM_NAME_SLUG=none when explicitly false
run_dry_run_up_and_check_generated_env "generated.env search default ENABLE_CRITIC=true" "search" \
  -i 127.0.0.1 -d -- \
  "ENABLE_CRITIC" "true"
ENABLE_CRITIC=true run_dry_run_up_and_check_generated_env "generated.env search ENABLE_CRITIC=true sets ENABLE_CRITIC" "search" \
  -i 127.0.0.1 -d -- \
  "ENABLE_CRITIC" "true"
ENABLE_CRITIC=TRUE run_dry_run_up_and_check_generated_env "generated.env search ENABLE_CRITIC=TRUE normalizes to true" "search" \
  -i 127.0.0.1 -d -- \
  "ENABLE_CRITIC" "true"
ENABLE_CRITIC=false run_dry_run_up_and_check_generated_env "generated.env search ENABLE_CRITIC=false sets ENABLE_CRITIC false" "search" \
  -i 127.0.0.1 -d -- \
  "ENABLE_CRITIC" "false"
ENABLE_CRITIC=false run_dry_run_up_and_check_generated_env "generated.env search ENABLE_CRITIC=false sets VLM_NAME_SLUG none" "search" \
  -i 127.0.0.1 -d -- \
  "VLM_NAME_SLUG" "none"
ENABLE_CRITIC=FALSE run_dry_run_up_and_check_generated_env "generated.env search ENABLE_CRITIC=FALSE normalizes to false" "search" \
  -i 127.0.0.1 -d -- \
  "ENABLE_CRITIC" "false"

# --- Setup paths: data directory and selective downloads (assert dry-run output) ---
_out_setup="$(mktemp)"
cd "${REPO_ROOT}"
timeout "${TEST_TIMEOUT}" "$DEV_PROFILE" up -p base -i 127.0.0.1 -d > "${_out_setup}" 2>&1
if grep -q "Creating data directories" "${_out_setup}" && grep -q "Setting permissions on data_log" "${_out_setup}"; then
  echo "PASS: up dry-run output includes data directory setup"
  ((TESTS_PASSED++)) || true
else
  echo "FAIL: up dry-run output missing data directory setup (Creating data directories / Setting permissions on data_log)"
  ((TESTS_FAILED++)) || true
fi
if grep -q "Setting permissions on agent_eval" "${_out_setup}"; then
  echo "PASS: up dry-run output includes agent_eval directory setup"
  ((TESTS_PASSED++)) || true
else
  echo "FAIL: up dry-run output missing agent_eval directory setup (Setting permissions on agent_eval)"
  ((TESTS_FAILED++)) || true
fi
if grep "data-directory:" "${_out_setup}" | grep -q "data-dir"; then
  echo "PASS: up dry-run data-directory path is deploy/docker/data-dir"
  ((TESTS_PASSED++)) || true
else
  echo "FAIL: up dry-run data-directory path missing or not deploy/docker/data-dir"
  ((TESTS_FAILED++)) || true
fi
# VSS kernel settings are applied only when not in dry-run; dry-run must not show the message
if ! grep -q "Applying VSS Linux kernel settings" "${_out_setup}"; then
  echo "PASS: up dry-run does not apply VSS kernel settings (step skipped in dry-run)"
  ((TESTS_PASSED++)) || true
else
  echo "FAIL: up dry-run must not run VSS kernel settings (Applying VSS Linux kernel settings should not appear in dry-run)"
  ((TESTS_FAILED++)) || true
fi
rm -f "${_out_setup}"

# VSS kernel settings: script must define set_vss_linux_kernel_settings and write 99-vss.conf (non-dry-run only)
if grep -q "function set_vss_linux_kernel_settings" "${DEV_PROFILE}" && grep -q "99-vss.conf" "${DEV_PROFILE}"; then
  echo "PASS: dev-profile.sh defines set_vss_linux_kernel_settings and 99-vss.conf"
  ((TESTS_PASSED++)) || true
else
  echo "FAIL: dev-profile.sh must define set_vss_linux_kernel_settings and reference 99-vss.conf"
  ((TESTS_FAILED++)) || true
fi

# Alerts profile: dry-run must include NGC model download steps (rtdetr-its, trafficcamnet, gdino/mask_grounding_dino)
_out_alerts="$(mktemp)"
timeout "${TEST_TIMEOUT}" "$DEV_PROFILE" up -p alerts -i 127.0.0.1 -m verification -d > "${_out_alerts}" 2>&1
if grep -q "models/rtdetr-its" "${_out_alerts}" && grep -q "trafficcamnet" "${_out_alerts}" && grep -q "models/gdino" "${_out_alerts}" && grep -q "mask_grounding_dino" "${_out_alerts}" && grep -q "mgdino_mask_head_pruned_dynamic_batch.onnx" "${_out_alerts}" && grep -q "ngc registry model" "${_out_alerts}"; then
  echo "PASS: alerts dry-run output includes NGC model download steps"
  ((TESTS_PASSED++)) || true
else
  echo "FAIL: alerts dry-run output missing NGC model download steps (models/rtdetr-its, trafficcamnet, models/gdino, mask_grounding_dino, mgdino_mask_head_pruned_dynamic_batch.onnx, ngc registry model)"
  ((TESTS_FAILED++)) || true
fi
rm -f "${_out_alerts}"

# Search profile: dry-run must include NGC model download steps (RT-DETR warehouse from nvstaging TAO).
_out_search="$(mktemp)"
timeout "${TEST_TIMEOUT}" "$DEV_PROFILE" up -p search -i 127.0.0.1 -d > "${_out_search}" 2>&1
if grep -q "Downloading RT-DETR model from NGC" "${_out_search}" && grep -q "nvstaging/tao/rtdetr_2d_warehouse" "${_out_search}" && grep -q "rtdetr_warehouse_v1.0.1.fp16.onnx" "${_out_search}" && grep -q -- "--org nvstaging" "${_out_search}" && grep -q "ngc registry model" "${_out_search}"; then
  echo "PASS: search dry-run output includes NGC model download steps"
  ((TESTS_PASSED++)) || true
else
  echo "FAIL: search dry-run output missing NGC model download steps (Downloading RT-DETR model from NGC, nvstaging/tao/rtdetr_2d_warehouse, rtdetr_warehouse_v1.0.1.fp16.onnx, --org nvstaging, ngc registry model)"
  ((TESTS_FAILED++)) || true
fi
rm -f "${_out_search}"

# --- generated.env content: dry-run up still writes/updates the file ---
# Run up with specific options and assert generated.env contains expected vars, then restore.
run_dry_run_up_and_check_generated_env "generated.env HOST_IP and HARDWARE_PROFILE from options" "base" \
 -i 127.0.0.1 -H RTXPRO6000BW -d -- \
  "HOST_IP" "127.0.0.1" "HARDWARE_PROFILE" "RTXPRO6000BW"
run_dry_run_up_and_check_generated_env "generated.env HARDWARE_PROFILE OTHER" "base" \
 -i 127.0.0.1 -H OTHER -d -- \
  "HARDWARE_PROFILE" "OTHER"

# DGX-SPARK: for each profile (including search), run dry-run with -H DGX-SPARK and assert sbsa variants (keys from profile .env).
# DGX-SPARK (and IGX-THOR) are only valid for base and alerts
for _profile in base alerts; do
  run_spark_test_for_profile "${_profile}"
done

run_dry_run_up_and_check_generated_env "generated.env LLM/VLM slugs and names" "base" \
 -i 127.0.0.1 --llm nvidia/nemotron-3-nano --vlm nvidia/cosmos-reason1-7b -d -- \
  "LLM_NAME_SLUG" "nemotron-3-nano" "LLM_NAME" "nvidia/nemotron-3-nano" \
  "VLM_NAME_SLUG" "cosmos-reason1-7b" "VLM_NAME" "nvidia/cosmos-reason1-7b"

run_dry_run_up_and_check_generated_env "generated.env MODE for alerts" "alerts" \
 -i 127.0.0.1 -m verification -d -- \
  "MODE" "2d_cv" \
  "NEXT_PUBLIC_APP_SUBTITLE" '"Vision (Alerts - CV)"'

run_dry_run_up_and_check_generated_env "generated.env alerts UI subtitle follows real-time MODE" "alerts" \
 -i 127.0.0.1 -m real-time -d -- \
  "MODE" "2d_vlm" \
  "NEXT_PUBLIC_APP_SUBTITLE" '"Vision (Alerts - VLM)"'

# Base profile: when LLM_DEVICE_ID and VLM_DEVICE_ID match (e.g. both 0), derived modes are local_shared for both; when they differ, both are local
run_dry_run_up_and_check_generated_env "generated.env LLM_MODE VLM_MODE HOST_IP (base defaults)" "base" \
 -i 127.0.0.1 -d -- \
  "LLM_MODE" "local_shared" "VLM_MODE" "local_shared" "HOST_IP" "127.0.0.1"

# When LLM_DEVICE_ID=VLM_DEVICE_ID (same device), derived modes are local_shared for both
_base_env="${REPO_ROOT}/deploy/docker/developer-profiles/dev-profile-base/.env"
if [[ -f "${_base_env}" ]]; then
  _backup_base="$(mktemp)"
  cp "${_base_env}" "${_backup_base}"
  CLEANUP_RESTORES+=("${_backup_base}|${_base_env}")
  # Temporarily set both device IDs to 1 so we derive local_shared for both
  sed -i 's/^LLM_DEVICE_ID=.*/LLM_DEVICE_ID=1/' "${_base_env}"
  sed -i 's/^VLM_DEVICE_ID=.*/VLM_DEVICE_ID=1/' "${_base_env}"
fi
run_dry_run_up_and_check_generated_env "generated.env LLM_MODE VLM_MODE local_shared when same device ID" "base" \
 -i 127.0.0.1 -d -- \
  "LLM_MODE" "local_shared" "VLM_MODE" "local_shared" "LLM_DEVICE_ID" "1" "VLM_DEVICE_ID" "1"

# When one model is remote, the other's device ID is not used for local vs local_shared (so the local side stays "local" unless its device ID is in FIXED_SHARED_DEVICE_IDS)
LLM_ENDPOINT_URL=http://127.0.0.1:9999 run_dry_run_up_and_check_generated_env "generated.env VLM_MODE local when LLM remote (vlm_device_id not compared to llm)" "base" \
  -i 127.0.0.1 --use-remote-llm --llm my-llm --vlm nvidia/cosmos-reason1-7b --vlm-device-id 1 -d -- \
  "LLM_MODE" "remote" "VLM_MODE" "local"
VLM_ENDPOINT_URL=http://127.0.0.1:9998 run_dry_run_up_and_check_generated_env "generated.env LLM_MODE local when VLM remote (llm_device_id not compared to vlm)" "base" \
  -i 127.0.0.1 --use-remote-vlm --vlm my-vlm --llm nvidia/nemotron-3-nano --llm-device-id 1 -d -- \
  "LLM_MODE" "local" "VLM_MODE" "remote"

run_dry_run_up_and_check_generated_env "generated.env EXTERNAL_IP from -e" "base" \
 -i 127.0.0.1 -e 192.168.1.100 -d -- \
  "EXTERNAL_IP" "192.168.1.100" "HOST_IP" "127.0.0.1"
# LLM_ENV_FILE and VLM_ENV_FILE: paths are resolved to absolute and must exist
_llm_env_tmp="$(mktemp)"
_vlm_env_tmp="$(mktemp)"
_llm_env_abs="$(cd "$(dirname "${_llm_env_tmp}")" && pwd)/$(basename "${_llm_env_tmp}")"
_vlm_env_abs="$(cd "$(dirname "${_vlm_env_tmp}")" && pwd)/$(basename "${_vlm_env_tmp}")"
run_dry_run_up_and_check_generated_env "generated.env LLM_ENV_FILE and VLM_ENV_FILE (absolute)" "base" \
 -i 127.0.0.1 --llm-env-file "${_llm_env_abs}" --vlm-env-file "${_vlm_env_abs}" -d -- \
  "LLM_ENV_FILE" "${_llm_env_abs}" "VLM_ENV_FILE" "${_vlm_env_abs}"
rm -f "${_llm_env_tmp}" "${_vlm_env_tmp}"

# Relative path from different CWD: script run from another directory; --llm-env-file and --vlm-env-file
# relative paths are resolved to absolute. VLM_CUSTOM_WEIGHTS must be absolute (pass absolute path here).
_cwd_tmp="$(mktemp -d)"
CLEANUP_DIRS+=("${_cwd_tmp}")
touch "${_cwd_tmp}/llm.env"
touch "${_cwd_tmp}/vlm.env"
mkdir -p "${_cwd_tmp}/vlm_weights"
_cwd_canon="$(cd "${_cwd_tmp}" && pwd)"
_expected_llm="${_cwd_canon}/llm.env"
_expected_vlm="${_cwd_canon}/vlm.env"
_expected_weights="${_cwd_canon}/vlm_weights"
_gen_env_cwd="$(generated_env_path "base")"
_backup_cwd=""
if [[ -f "${_gen_env_cwd}" ]]; then
  _backup_cwd="$(mktemp)"
  cp "${_gen_env_cwd}" "${_backup_cwd}"
fi
set +e
(cd "${_cwd_tmp}" && VLM_CUSTOM_WEIGHTS="${_expected_weights}" timeout "${TEST_TIMEOUT}" "$DEV_PROFILE" up -p base -i 127.0.0.1 --llm-env-file ./llm.env --vlm-env-file ./vlm.env -d > "${_cwd_tmp}/out" 2> "${_cwd_tmp}/err")
_cwd_exit=$?
set -e
if [[ ${_cwd_exit} -eq 124 ]]; then
  echo "FAIL: relative path from different CWD (timed out)"
  ((TESTS_FAILED++)) || true
elif [[ ${_cwd_exit} -ne 0 ]]; then
  echo "FAIL: relative path from different CWD (exit ${_cwd_exit})"
  [[ -f "${_cwd_tmp}/err" ]] && sed 's/^/    /' "${_cwd_tmp}/err"
  ((TESTS_FAILED++)) || true
else
  _actual_llm="$(get_generated_env_value "${_gen_env_cwd}" "LLM_ENV_FILE")"
  _actual_vlm="$(get_generated_env_value "${_gen_env_cwd}" "VLM_ENV_FILE")"
  _actual_weights="$(get_generated_env_value "${_gen_env_cwd}" "VLM_CUSTOM_WEIGHTS")"
  _failed_cwd=0
  # Assert only absolute path is set (never the relative form we passed)
  if [[ "${_actual_llm}" == ./* ]] || [[ "${_actual_llm}" != "${_expected_llm}" ]]; then
    echo "FAIL: relative path from different CWD (LLM_ENV_FILE must be absolute, expected '${_expected_llm}', got '${_actual_llm}')"
    _failed_cwd=1
  fi
  if [[ "${_actual_vlm}" == ./* ]] || [[ "${_actual_vlm}" != "${_expected_vlm}" ]]; then
    echo "FAIL: relative path from different CWD (VLM_ENV_FILE must be absolute, expected '${_expected_vlm}', got '${_actual_vlm}')"
    _failed_cwd=1
  fi
  if [[ "${_actual_weights}" == ./* ]] || [[ "${_actual_weights}" != "${_expected_weights}" ]]; then
    echo "FAIL: relative path from different CWD (VLM_CUSTOM_WEIGHTS must be absolute, expected '${_expected_weights}', got '${_actual_weights}')"
    _failed_cwd=1
  fi
  if [[ ${_failed_cwd} -eq 0 ]]; then
    echo "PASS: relative path from different CWD stored as absolute in generated.env (correct for CWD)"
    ((TESTS_PASSED++)) || true
  else
    ((TESTS_FAILED++)) || true
  fi
fi
if [[ -n "${_backup_cwd}" && -f "${_backup_cwd}" ]]; then
  mv "${_backup_cwd}" "${_gen_env_cwd}"
else
  rm -f "${_gen_env_cwd}"
fi

# Relative path when CWD is REPO_ROOT: relative --llm-env-file is resolved to absolute
_rel_under_repo="${REPO_ROOT}/tests/rel_llm.env"
touch "${_rel_under_repo}"
run_dry_run_up_and_check_generated_env "generated.env relative --llm-env-file from REPO_ROOT stored as absolute" "base" \
 -i 127.0.0.1 --llm-env-file "tests/rel_llm.env" -d -- \
  "LLM_ENV_FILE" "${REPO_ROOT}/tests/rel_llm.env"
rm -f "${_rel_under_repo}"

run_dry_run_up_and_check_generated_env "generated.env other LLM model openai/gpt-oss-20b" "base" \
 -i 127.0.0.1 --llm openai/gpt-oss-20b -d -- \
  "LLM_NAME_SLUG" "gpt-oss-20b" "LLM_NAME" "openai/gpt-oss-20b"

run_dry_run_up_and_check_generated_env "generated.env other VLM model Qwen/Qwen3-VL-8B-Instruct" "base" \
 -i 127.0.0.1 --vlm Qwen/Qwen3-VL-8B-Instruct -d -- \
  "VLM_NAME_SLUG" "qwen3-vl-8b-instruct" "VLM_NAME" "Qwen/Qwen3-VL-8B-Instruct"

# Real-time (2d_vlm) with local VLM: script does NOT override VLM_PORT, RTVI_VLM_ENDPOINT, or RTVI_VLM_MODEL_TO_USE; values come from profile .env defaults (rtvi-vlm on 8018, cosmos-reason2).
run_dry_run_up_and_check_generated_env "generated.env alerts real-time local VLM preserves .env defaults (rtvi-vlm on 8018)" "alerts" \
 -i 127.0.0.1 -m real-time -d -- \
  "MODE" "2d_vlm" "VLM_PORT" "8018" "RTVI_VLM_ENDPOINT" "http://\${HOST_IP}:8018/v1" "RTVI_VLM_MODEL_TO_USE" "cosmos-reason2"

# Real-time (2d_vlm) with remote VLM: script overrides VLM_PORT to 30082 and RTVI_VLM_MODEL_TO_USE to openai-compat; RTVI_VLM_ENDPOINT comes from --vlm-base-url.
LLM_ENDPOINT_URL=http://127.0.0.1:9999 VLM_ENDPOINT_URL=http://127.0.0.1:9998 run_dry_run_up_and_check_generated_env "generated.env alerts real-time remote VLM sets VLM_PORT=30082 and openai-compat" "alerts" \
 -i 127.0.0.1 -H OTHER -m real-time --use-remote-llm --llm my-llm --use-remote-vlm --vlm my-vlm -d -- \
  "VLM_MODE" "remote" "VLM_PORT" "30082" "RTVI_VLM_ENDPOINT" "http://127.0.0.1:9998/v1" "RTVI_VLM_MODEL_TO_USE" "openai-compat"

# Alerts profile: PERCEPTION_DOCKERFILE_PREFIX and VLM_AS_VERIFIER_CONFIG_FILE_PREFIX (conditional on HARDWARE_PROFILE and VLM_MODE)
run_dry_run_up_and_check_generated_env "generated.env alerts prefixes non-DGX-SPARK (empty)" "alerts" \
 -i 127.0.0.1 -H OTHER -m verification -d -- \
  "PERCEPTION_DOCKERFILE_PREFIX" "" "VLM_AS_VERIFIER_CONFIG_FILE_PREFIX" ""
# DGX-SPARK uses default config.yml (empty prefix); only IGX-THOR/AGX-THOR get EDGE-LOCAL-VLM- prefix
run_dry_run_up_and_check_generated_env "generated.env alerts prefixes DGX-SPARK local VLM" "alerts" \
 -i 127.0.0.1 -H DGX-SPARK -m real-time -d -- \
  "PERCEPTION_DOCKERFILE_PREFIX" "EDGE-" "VLM_AS_VERIFIER_CONFIG_FILE_PREFIX" ""
run_dry_run_up_and_check_generated_env "generated.env alerts prefixes IGX-THOR local VLM" "alerts" \
 -i 127.0.0.1 -H IGX-THOR -m real-time -d -- \
  "PERCEPTION_DOCKERFILE_PREFIX" "EDGE-" "VLM_AS_VERIFIER_CONFIG_FILE_PREFIX" "EDGE-LOCAL-VLM-"
run_dry_run_up_and_check_generated_env "generated.env alerts prefixes AGX-THOR local VLM" "alerts" \
 -i 127.0.0.1 -H AGX-THOR -m real-time -d -- \
  "PERCEPTION_DOCKERFILE_PREFIX" "EDGE-" "VLM_AS_VERIFIER_CONFIG_FILE_PREFIX" "EDGE-LOCAL-VLM-"
# Both-remote alerts prefix check (OTHER allows remote+remote; IGX-THOR does not accept --use-remote-vlm for alerts)
LLM_ENDPOINT_URL=http://127.0.0.1:9999 VLM_ENDPOINT_URL=http://127.0.0.1:9998 run_dry_run_up_and_check_generated_env "generated.env alerts prefixes both remote (OTHER)" "alerts" \
 -i 127.0.0.1 -H OTHER -m real-time --use-remote-llm --llm x --use-remote-vlm --vlm y -d -- \
  "PERCEPTION_DOCKERFILE_PREFIX" "" "VLM_AS_VERIFIER_CONFIG_FILE_PREFIX" ""

# --- Remote with explicit model name via --llm/--vlm (no API call) ---
LLM_ENDPOINT_URL=http://127.0.0.1:9999 VLM_ENDPOINT_URL=http://127.0.0.1:9998 run_dry_run_up_and_check_generated_env "generated.env LLM_NAME from --llm when remote" "base" \
 -i 127.0.0.1 --use-remote-llm --llm my-remote-llm --use-remote-vlm --vlm my-remote-vlm -d -- \
  "LLM_MODE" "remote" "LLM_NAME" "my-remote-llm" "LLM_BASE_URL" "http://127.0.0.1:9999"

LLM_ENDPOINT_URL=http://127.0.0.1:9999 VLM_ENDPOINT_URL=http://127.0.0.1:9998 run_dry_run_up_and_check_generated_env "generated.env VLM_NAME from --vlm when remote" "base" \
 -i 127.0.0.1 --use-remote-llm --llm my-llm --use-remote-vlm --vlm my-remote-vlm -d -- \
  "VLM_MODE" "remote" "VLM_NAME" "my-remote-vlm" "VLM_BASE_URL" "http://127.0.0.1:9998"

LLM_ENDPOINT_URL=http://127.0.0.1:9999 VLM_ENDPOINT_URL=http://127.0.0.1:9998 OPENAI_API_KEY=sk-test-key run_dry_run_up_and_check_generated_env "generated.env LLM_MODEL_TYPE VLM_MODEL_TYPE OPENAI_API_KEY from env" "base" \
 -i 127.0.0.1 --use-remote-llm --llm my-llm --llm-model-type openai --use-remote-vlm --vlm my-vlm --vlm-model-type openai -d -- \
  "LLM_MODEL_TYPE" "openai" "VLM_MODEL_TYPE" "openai" "OPENAI_API_KEY" "sk-test-key"

# API keys from env are written to generated.env regardless of remote/local (optional, not mandatory)
NVIDIA_API_KEY=nv-test-key run_dry_run_up_and_check_generated_env "generated.env NVIDIA_API_KEY from env when local" "base" \
 -i 127.0.0.1 -d -- \
  "NVIDIA_API_KEY" "nv-test-key"

LLM_ENDPOINT_URL=http://127.0.0.1:9999 VLM_ENDPOINT_URL=http://127.0.0.1:9998 run_dry_run_up_and_check_generated_env "generated.env LLM_MODEL_TYPE VLM_MODEL_TYPE from profile defaults when remote" "base" \
 -i 127.0.0.1 --use-remote-llm --llm my-llm --use-remote-vlm --vlm my-vlm -d -- \
  "LLM_MODEL_TYPE" "nim" "VLM_MODEL_TYPE" "nim"

# --- Remote: model name from mock API (Python mock server) ---
gen_env_mock="$(generated_env_path "base")"
if command -v python3 >/dev/null 2>&1 && command -v jq >/dev/null 2>&1; then
  port_file="$(mktemp)"
  cd "${REPO_ROOT}"
  python3 "${REPO_ROOT}/deploy/docker/test-scripts/mock_v1_models_server.py" 0 "mock-llm-from-api" > "${port_file}" 2>/dev/null &
  mock_pid=$!
  CLEANUP_PIDS+=("${mock_pid}")
  # Wait for port to be written and server to accept connections
  for _ in 1 2 3 4 5; do
    sleep 1
    mock_port="$(cat "${port_file}" 2>/dev/null)"
    if [[ -n "${mock_port}" ]] && [[ "${mock_port}" =~ ^[0-9]+$ ]]; then
      if curl -s -f "http://127.0.0.1:${mock_port}/v1/models" >/dev/null 2>&1; then
        break
      fi
    fi
  done
  mock_port="$(cat "${port_file}" 2>/dev/null)"
  if [[ -n "${mock_port}" ]] && [[ "${mock_port}" =~ ^[0-9]+$ ]]; then
    mock_base="http://127.0.0.1:${mock_port}"
    backup_mock=""
    if [[ -f "${gen_env_mock}" ]]; then
      backup_mock="$(mktemp)"
      cp "${gen_env_mock}" "${backup_mock}"
      CLEANUP_RESTORES+=("${backup_mock}|${gen_env_mock}")
    fi
    out_mock="$(mktemp)"
    err_mock="$(mktemp)"
    set +e
    # Both modes must be remote; VLM gets name from API too (same mock server)
    LLM_ENDPOINT_URL="${mock_base}" VLM_ENDPOINT_URL="${mock_base}" timeout "${TEST_TIMEOUT}" "$DEV_PROFILE" up -p base -i 127.0.0.1 --use-remote-llm --use-remote-vlm -d > "${out_mock}" 2> "${err_mock}"
    mock_exit=$?
    set -e
    kill "${mock_pid}" 2>/dev/null || true
    wait "${mock_pid}" 2>/dev/null || true
    if [[ ${mock_exit} -eq 0 ]]; then
      actual_llm="$(get_generated_env_value "${gen_env_mock}" "LLM_NAME")"
      actual_vlm="$(get_generated_env_value "${gen_env_mock}" "VLM_NAME")"
      if [[ "${actual_llm}" == "mock-llm-from-api" ]] && [[ "${actual_vlm}" == "mock-llm-from-api" ]]; then
        echo "PASS: generated.env LLM_NAME and VLM_NAME from remote API (mock server)"
        ((TESTS_PASSED++)) || true
      else
        echo "FAIL: generated.env from remote API (LLM: ${actual_llm}, VLM: ${actual_vlm}; expected mock-llm-from-api)"
        ((TESTS_FAILED++)) || true
      fi
    else
      echo "FAIL: generated.env from remote API (dev-profile exit ${mock_exit})"
      sed 's/^/    /' "${err_mock}"
      ((TESTS_FAILED++)) || true
    fi
    [[ -n "${backup_mock}" && -f "${backup_mock}" ]] && mv "${backup_mock}" "${gen_env_mock}" || rm -f "${gen_env_mock}"
    rm -f "${out_mock}" "${err_mock}"
  else
    echo "SKIP: generated.env from remote API (mock server port not ready)"
    kill "${mock_pid}" 2>/dev/null || true
  fi
  rm -f "${port_file}"
else
  echo "SKIP: generated.env from remote API (python3 or jq not found)"
fi

# --- Brev: HAProxy + VSS_PUBLIC_HOST in generated.env (agent_ui uses HAPROXY_* / VSS_PUBLIC_HOST only; no BREV_* compose vars) ---
# Brev writes template literals ${PROXY_PORT:-7777} and ${BREV_ENV_ID} for docker compose to expand at runtime.
BREV_ENV_ID=test-env run_dry_run_up_and_check_generated_env "generated.env Brev HAProxy + VSS_PUBLIC_HOST" "base" \
 -i 127.0.0.1 -d -- \
  "HAPROXY_PORT" '${PROXY_PORT:-7777}' \
  "VSS_PUBLIC_HTTP_PROTOCOL" "https" \
  "VSS_PUBLIC_WS_PROTOCOL" "wss" \
  "VSS_PUBLIC_HOST" '${PROXY_PORT:-7777}0-${BREV_ENV_ID}.brevlab.com' \
  "VSS_PUBLIC_PORT" "443"

# Brev with custom PROXY_PORT in env: same literals in generated.env (compose expands using env)
BREV_ENV_ID=test-env PROXY_PORT=8080 run_dry_run_up_and_check_generated_env "generated.env Brev with custom PROXY_PORT (templates unchanged)" "base" \
 -i 127.0.0.1 -d -- \
  "HAPROXY_PORT" '${PROXY_PORT:-7777}' \
  "VSS_PUBLIC_HTTP_PROTOCOL" "https" \
  "VSS_PUBLIC_WS_PROTOCOL" "wss" \
  "VSS_PUBLIC_HOST" '${PROXY_PORT:-7777}0-${BREV_ENV_ID}.brevlab.com' \
  "VSS_PUBLIC_PORT" "443"

# Non-Brev: profile HAProxy defaults (script does not inject https/wss or Brev host templates)
run_dry_run_up_and_check_generated_env "generated.env no Brev HAProxy overrides when BREV_ENV_ID unset" "base" \
 -i 127.0.0.1 -d -- \
  "HAPROXY_PORT" "7777" \
  "VSS_PUBLIC_HTTP_PROTOCOL" "http" \
  "VSS_PUBLIC_WS_PROTOCOL" "ws" \
  "VSS_PUBLIC_HOST" '${EXTERNAL_IP}' \
  "VSS_PUBLIC_PORT" '${HAPROXY_PORT}'

# --- Positive: dry-run down ---
out_file="$(mktemp)"
err_file="$(mktemp)"
cd "${REPO_ROOT}"
set +e
timeout "${TEST_TIMEOUT}" "$DEV_PROFILE" down --dry-run > "${out_file}" 2> "${err_file}"
exit_code=$?
set -e
if [[ ${exit_code} -eq 124 ]]; then
  echo "FAIL: down dry-run (timed out after ${TEST_TIMEOUT}s)"
  ((TESTS_FAILED++)) || true
elif [[ ${exit_code} -ne 0 ]]; then
  echo "FAIL: down dry-run (expected exit 0, got ${exit_code})"
  cat "${out_file}" "${err_file}" | sed 's/^/    /'
  ((TESTS_FAILED++)) || true
elif ! grep -q "\[DRY-RUN\] docker compose -p mdx down -v --remove-orphans" "${out_file}"; then
  echo "FAIL: down dry-run (stdout missing '[DRY-RUN] docker compose -p mdx down -v --remove-orphans')"
  ((TESTS_FAILED++)) || true
elif ! grep -q "State down completed" "${out_file}"; then
  echo "FAIL: down dry-run (stdout missing 'State down completed')"
  ((TESTS_FAILED++)) || true
else
  echo "PASS: down dry-run"
  ((TESTS_PASSED++)) || true
fi
rm -f "${out_file}" "${err_file}"

# --- Summary ---
echo ""
echo "=========================================="
echo "Results: ${TESTS_PASSED} passed, ${TESTS_FAILED} failed"
echo "=========================================="
if [[ ${TESTS_FAILED} -gt 0 ]]; then
  exit 1
fi
exit 0
