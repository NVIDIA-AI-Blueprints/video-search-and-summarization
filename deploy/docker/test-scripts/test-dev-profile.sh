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

# Read the value from a profile overrides.env's commented line for KEY that contains sbsa (the line that DGX-SPARK will activate).
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

# Run one DGX-SPARK dry-run test for a profile: discover sbsa keys from profile overrides.env, run up -H DGX-SPARK, assert.
# Skips if profile overrides.env is missing. Alerts gets -m real-time.
run_spark_test_for_profile() {
  local profile="${1}"
  local env_file="${REPO_ROOT}/deploy/docker/developer-profiles/dev-profile-${profile}/overrides.env"
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
out_file="$(mktemp)"
err_file="$(mktemp)"
set +e
timeout "${TEST_TIMEOUT}" "$DEV_PROFILE" up -p base --external-ip 192.168.1.100 --unknown-option > "${out_file}" 2> "${err_file}"
exit_code=$?
set -e
failed=0
if [[ ${exit_code} -eq 124 ]]; then
  echo "FAIL: getopt invalid usage masks external-ip (timed out)"
  ((failed++)) || true
elif [[ ${exit_code} -ne 1 ]]; then
  echo "FAIL: getopt invalid usage masks external-ip (expected exit 1, got ${exit_code})"
  ((failed++)) || true
else
  if grep -Fq "192.168.1.100" "${out_file}" "${err_file}"; then
    echo "FAIL: getopt invalid usage masks external-ip (unmasked value appeared in output)"
    ((failed++)) || true
  fi
  if ! grep -Fq -- "--external-ip 192*******100" "${out_file}" "${err_file}"; then
    echo "FAIL: getopt invalid usage masks external-ip (masked value missing from output)"
    ((failed++)) || true
  fi
fi
rm -f "${out_file}" "${err_file}"
if [[ ${failed} -gt 0 ]]; then
  ((TESTS_FAILED++)) || true
else
  echo "PASS: getopt invalid usage masks external-ip"
  ((TESTS_PASSED++)) || true
fi
run_negative_test "invalid option -k (use NGC_CLI_API_KEY env)" 1 up -p base -k x
run_negative_test "no args → desired-state required" 1
run_negative_test "invalid desired-state" 1 invalid_state
run_negative_test "up without --profile" 1 up
NGC_CLI_API_KEY= run_negative_test "up without ngc key (no env)" 1 up -p base
run_negative_test "invalid profile" 1 up -p invalid
run_negative_test "invalid hardware-profile" 1 up -p base -H INVALID
# Fail-fast: requested hardware_profile must match detected GPU (nvidia-smi); OTHER is catchall when no match.
SKIP_HARDWARE_CHECK= run_negative_test "hardware profile does not match (no GPU, requested DGX-SPARK)" 1 up -p base -i 127.0.0.1 -H DGX-SPARK -d
_mock_nvidia_smi_dir="$(mktemp -d)"
CLEANUP_DIRS+=("${_mock_nvidia_smi_dir}")
cat > "${_mock_nvidia_smi_dir}/nvidia-smi" <<'EOF'
#!/bin/bash
echo "NVIDIA H100 80GB HBM3"
EOF
chmod +x "${_mock_nvidia_smi_dir}/nvidia-smi"
PATH="${_mock_nvidia_smi_dir}:${PATH}" SKIP_HARDWARE_CHECK= run_dry_run_test "OTHER accepted when detected GPU is supported" up -p base -i 127.0.0.1 -H OTHER -d
_mock_rtx4500_nvidia_smi_dir="$(mktemp -d)"
CLEANUP_DIRS+=("${_mock_rtx4500_nvidia_smi_dir}")
cat > "${_mock_rtx4500_nvidia_smi_dir}/nvidia-smi" <<'EOF'
#!/bin/bash
echo "NVIDIA RTX PRO 4500 Blackwell"
EOF
chmod +x "${_mock_rtx4500_nvidia_smi_dir}/nvidia-smi"
PATH="${_mock_rtx4500_nvidia_smi_dir}:${PATH}" SKIP_HARDWARE_CHECK= run_dry_run_test "RTXPRO4500BW accepted when detected GPU is RTX PRO 4500 Blackwell" up -p base -i 127.0.0.1 -H RTXPRO4500BW -d
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
  "LLM_DEVICE_ID" "0" "VLM_DEVICE_ID" "0" "VLM_NAME_SLUG" "none" "VLM_NAME" "nim_nvidia_cosmos3-nano-reasoner_bf16-final" "VLM_BASE_URL" "http://rtvi-vlm:8000" "VLM_MODEL_TYPE" "rtvi" "RTVI_VLM_MODEL_PATH" "ngc:nim/nvidia/cosmos3-nano-reasoner:bf16-final" "RTVI_VLM_MODEL_TO_USE" "cosmos-reason3" "RTVI_VLLM_GPU_MEMORY_UTILIZATION" "0.35"
run_dry_run_up_and_check_generated_env "generated.env base AGX-THOR VLM and RTVI vars (same as IGX-THOR)" "base" \
 -i 127.0.0.1 -H AGX-THOR -d -- \
  "LLM_DEVICE_ID" "0" "VLM_DEVICE_ID" "0" "VLM_NAME_SLUG" "none" "VLM_NAME" "nim_nvidia_cosmos3-nano-reasoner_bf16-final" "VLM_BASE_URL" "http://rtvi-vlm:8000" "VLM_MODEL_TYPE" "rtvi" "RTVI_VLM_MODEL_PATH" "ngc:nim/nvidia/cosmos3-nano-reasoner:bf16-final" "RTVI_VLM_MODEL_TO_USE" "cosmos-reason3" "RTVI_VLLM_GPU_MEMORY_UTILIZATION" "0.35"
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

# L40S forbids a local_shared LLM (no hw-L40S-shared.env). Search RT-VLM may share GPU 0 with RT-CV.
run_negative_test "L40S rejects local_shared LLM" 1 up -p search -i 127.0.0.1 -H L40S -d
run_negative_test "L40S rejects LLM and VLM on the same GPU" 1 up -p base -i 127.0.0.1 -H L40S --llm-device-id 0 --vlm-device-id 0 -d

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
  "VLM_NAME_SLUG" "none" "VLM_NAME" "nim_nvidia_cosmos3-nano-reasoner_bf16-final" "VLM_BASE_URL" "http://rtvi-vlm:8000" "RTVI_VLM_MODEL_PATH" "ngc:nim/nvidia/cosmos3-nano-reasoner:bf16-final" "RTVI_VLM_MODEL_TO_USE" "cosmos-reason3" "RT_VLM_DEVICE_ID" "0"
run_dry_run_up_and_check_generated_env "generated.env alerts AGX-THOR VLM vars (RT_VLM_DEVICE_ID=0)" "alerts" \
  -i 127.0.0.1 -m verification -H AGX-THOR -d -- \
  "VLM_NAME_SLUG" "none" "VLM_NAME" "nim_nvidia_cosmos3-nano-reasoner_bf16-final" "VLM_BASE_URL" "http://rtvi-vlm:8000" "RTVI_VLM_MODEL_PATH" "ngc:nim/nvidia/cosmos3-nano-reasoner:bf16-final" "RTVI_VLM_MODEL_TO_USE" "cosmos-reason3" "RT_VLM_DEVICE_ID" "0"
# Alerts on IGX-THOR/AGX-THOR: RTVI_VLLM_GPU_MEMORY_UTILIZATION env var flows through to generated.env (option pattern, like ${VLM_NIM_KVCACHE_PERCENT} in NIM hw-H100.env).
RTVI_VLLM_GPU_MEMORY_UTILIZATION=0.5 run_dry_run_up_and_check_generated_env "generated.env alerts IGX-THOR RTVI_VLLM_GPU_MEMORY_UTILIZATION env passes through" "alerts" \
  -i 127.0.0.1 -m verification -H IGX-THOR -d -- \
  "RTVI_VLLM_GPU_MEMORY_UTILIZATION" "0.5"
RTVI_VLLM_GPU_MEMORY_UTILIZATION=0.6 run_dry_run_up_and_check_generated_env "generated.env alerts AGX-THOR RTVI_VLLM_GPU_MEMORY_UTILIZATION env passes through" "alerts" \
  -i 127.0.0.1 -m verification -H AGX-THOR -d -- \
  "RTVI_VLLM_GPU_MEMORY_UTILIZATION" "0.6"
# Alerts RT-VLM local VLM memory sizing.
run_dry_run_up_and_check_generated_env "generated.env alerts DGX-SPARK shared RTVI_VLLM_GPU_MEMORY_UTILIZATION=0.4" "alerts" \
  -i 127.0.0.1 -m verification -H DGX-SPARK -d -- \
  "RTVI_VLLM_GPU_MEMORY_UTILIZATION" "0.4"
run_dry_run_up_and_check_generated_env "generated.env alerts H100 shared RTVI_VLLM_GPU_MEMORY_UTILIZATION=0.4" "alerts" \
  -i 127.0.0.1 -m verification -H H100 -d -- \
  "RTVI_VLLM_GPU_MEMORY_UTILIZATION" "0.4"
run_dry_run_up_and_check_generated_env "generated.env alerts H100 local RTVI_VLLM_GPU_MEMORY_UTILIZATION=0.7" "alerts" \
  -i 127.0.0.1 -m verification -H H100 --llm-device-id 2 --vlm-device-id 1 -d -- \
  "RTVI_VLLM_GPU_MEMORY_UTILIZATION" "0.7"
run_dry_run_up_and_check_generated_env "generated.env alerts RTXPRO6000BW shared RTVI_VLLM_GPU_MEMORY_UTILIZATION=0.4" "alerts" \
  -i 127.0.0.1 -m verification -H RTXPRO6000BW -d -- \
  "RTVI_VLLM_GPU_MEMORY_UTILIZATION" "0.4"
run_dry_run_up_and_check_generated_env "generated.env alerts RTXPRO6000BW local RTVI_VLLM_GPU_MEMORY_UTILIZATION=0.7" "alerts" \
  -i 127.0.0.1 -m verification -H RTXPRO6000BW --llm-device-id 2 --vlm-device-id 1 -d -- \
  "RTVI_VLLM_GPU_MEMORY_UTILIZATION" "0.7"
run_dry_run_up_and_check_generated_env "generated.env alerts L40S local RTVI_VLLM_GPU_MEMORY_UTILIZATION=0.8" "alerts" \
  -i 127.0.0.1 -m verification -H L40S --llm-device-id 2 --vlm-device-id 1 -d -- \
  "RTVI_VLLM_GPU_MEMORY_UTILIZATION" "0.8"
run_dry_run_up_and_check_generated_env "generated.env alerts RTXPRO4500BW RTVI tuning" "alerts" \
  -i 127.0.0.1 -m verification -H RTXPRO4500BW -d -- \
  "RTVI_VLLM_GPU_MEMORY_UTILIZATION" "0.8" "RTVI_VLM_MAX_MODEL_LEN" "18000" "RTVI_VLM_MODEL_PATH" "ngc:nim/nvidia/cosmos3-nano-reasoner:bf16-final" "VLM_NAME" "nim_nvidia_cosmos3-nano-reasoner_bf16-final"
run_dry_run_up_and_check_generated_env "generated.env lvs RTXPRO4500BW RTVI tuning" "lvs" \
  -i 127.0.0.1 -H RTXPRO4500BW -d -- \
  "RTVI_VLLM_GPU_MEMORY_UTILIZATION" "0.8" "RTVI_VLM_MAX_MODEL_LEN" "18000" "RTVI_VLM_MODEL_PATH" "ngc:nim/nvidia/cosmos3-nano-reasoner:bf16-final" "VLM_NAME" "nim_nvidia_cosmos3-nano-reasoner_bf16-final"
run_dry_run_up_and_check_generated_env "generated.env base RTXPRO4500BW RTVI tuning" "base" \
  -i 127.0.0.1 -H RTXPRO4500BW -d -- \
  "RTVI_VLLM_GPU_MEMORY_UTILIZATION" "0.8" "RTVI_VLM_MAX_MODEL_LEN" "18000" "RTVI_VLM_MODEL_PATH" "ngc:nim/nvidia/cosmos3-nano-reasoner:bf16-final" "VLM_NAME" "nim_nvidia_cosmos3-nano-reasoner_bf16-final"
run_dry_run_up_and_check_generated_env "generated.env alerts OTHER RTVI_VLLM_GPU_MEMORY_UTILIZATION=0.7" "alerts" \
  -i 127.0.0.1 -m verification -H OTHER -d -- \
  "RTVI_VLLM_GPU_MEMORY_UTILIZATION" "0.7"
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

# --- Negative: profile overrides.env missing ---
_overrides_env_base="${REPO_ROOT}/deploy/docker/developer-profiles/dev-profile-base/overrides.env"
if [[ -f "${_overrides_env_base}" ]]; then
  _overrides_env_backup="$(mktemp)"
  cp "${_overrides_env_base}" "${_overrides_env_backup}"
  CLEANUP_RESTORES+=("${_overrides_env_backup}|${_overrides_env_base}")
  rm -f "${_overrides_env_base}"
  out_file="$(mktemp)"
  err_file="$(mktemp)"
  cd "${REPO_ROOT}"
  set +e
  timeout "${TEST_TIMEOUT}" "$DEV_PROFILE" up -p base -i 127.0.0.1 -d > "${out_file}" 2> "${err_file}"
  _exit=$?
  set -e
  mv "${_overrides_env_backup}" "${_overrides_env_base}"
  if [[ ${_exit} -ne 1 ]]; then
    echo "FAIL: profile overrides.env missing (expected exit 1, got ${_exit})"
    ((TESTS_FAILED++)) || true
  elif grep -q "Profile overrides env file not found" "${out_file}" || grep -q "Profile overrides env file not found" "${err_file}"; then
    echo "PASS: profile overrides.env missing (fail-fast: profile overrides env not found)"
    ((TESTS_PASSED++)) || true
  else
    echo "FAIL: profile overrides.env missing (expected 'Profile overrides env file not found' in output, got other or no error)"
    ((TESTS_FAILED++)) || true
  fi
  rm -f "${out_file}" "${err_file}"
else
  echo "SKIP: profile overrides.env missing (base overrides.env not found)"
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
run_dry_run_test "up base with hardware-profile RTXPRO4500BW" up -p base -i 127.0.0.1 -H RTXPRO4500BW -d
run_dry_run_test "up base with hardware-profile RTXPRO6000BW" up -p base -i 127.0.0.1 -H RTXPRO6000BW -d
run_dry_run_test "up base with hardware-profile OTHER" up -p base -i 127.0.0.1 -H OTHER -d
run_dry_run_up_and_check_generated_env "up base with llm keeps fixed RT-VLM" "base" \
  -i 127.0.0.1 --llm nvidia/nemotron-3-nano -d -- \
  "LLM_NAME" "nvidia/nemotron-3-nano" "LLM_NAME_SLUG" "nemotron-3-nano" \
  "VLM_NAME" "nim_nvidia_cosmos3-nano-reasoner_bf16-final" "VLM_NAME_SLUG" "none" \
  "VLM_BASE_URL" "http://rtvi-vlm:8000" "VLM_MODEL_TYPE" "rtvi"
run_negative_test "llm-env-file must exist" 1 up -p base -i 127.0.0.1 --llm-env-file /nonexistent/llm.env -d
run_negative_test "vlm-env-file must exist" 1 up -p base -i 127.0.0.1 --vlm-env-file ./nonexistent-vlm.env -d
run_dry_run_test "up alerts real-time mode" up -p alerts -i 127.0.0.1 -m real-time -d
# L40S search with a remote LLM is allowed; local RT-VLM shares GPU 0 with RT-CV.
LLM_ENDPOINT_URL=http://127.0.0.1:1 run_dry_run_up_and_check_generated_env "generated.env search L40S remote LLM keeps local RT-VLM on GPU 0" "search" \
  -i 127.0.0.1 -H L40S --use-remote-llm --llm x -d -- \
  "LLM_MODE" "remote" "VLM_MODE" "local_shared" "VLM_DEVICE_ID" "0" "RT_VLM_DEVICE_ID" "0"

_out_compose_env_order="$(mktemp)"
_err_compose_env_order="$(mktemp)"
cd "${REPO_ROOT}"
set +e
timeout "${TEST_TIMEOUT}" "$DEV_PROFILE" up -p base -i 127.0.0.1 -d > "${_out_compose_env_order}" 2> "${_err_compose_env_order}"
_compose_env_order_exit=$?
set -e
if [[ ${_compose_env_order_exit} -ne 0 ]]; then
  echo "FAIL: dry-run compose command exited with ${_compose_env_order_exit}"
  ((TESTS_FAILED++)) || true
elif ! grep -Fq "docker compose --env-file containers.env --env-file developer-profiles/dev-profile-base/.env --env-file developer-profiles/dev-profile-base/generated.env up" "${_out_compose_env_order}"; then
  echo "FAIL: dry-run compose command should preserve container, profile, override precedence"
  ((TESTS_FAILED++)) || true
elif ! grep -Fq "up --detach --pull always --force-recreate --build" "${_out_compose_env_order}"; then
  echo "FAIL: dry-run compose command should refresh moving image tags"
  ((TESTS_FAILED++)) || true
else
  echo "PASS: dry-run compose command preserves env precedence and refreshes images"
  ((TESTS_PASSED++)) || true
fi
rm -f "${_out_compose_env_order}" "${_err_compose_env_order}"

# Search: RT-VLM (vss-rtvi-vlm) is always deployed because it serves both the critic and
# video_understanding. It is activated via the explicit "rtvi-vlm" compose profile (no vlm_
# NIM profile) and the agent is wired to it with VLM_NAME_SLUG=none, VLM_MODEL_TYPE=rtvi,
# VLM_BASE_URL=http://rtvi-vlm:8000. RT-VLM shares GPU 0 with RT-CV, so device 0 is in
# FIXED_SHARED_DEVICE_IDS and VLM_MODE derives to local_shared, which caps RT-VLM at the
# 0.4 H100 shared fraction so RT-CV keeps its headroom.
run_dry_run_up_and_check_generated_env "generated.env search default wires RT-VLM" "search" \
  -i 127.0.0.1 -d -- \
  "VLM_DEVICE_ID" "0" "VLM_MODE" "local_shared" "VLM_NAME_SLUG" "none" "VLM_MODEL_TYPE" "rtvi" \
  "VLM_NAME" "nim_nvidia_cosmos3-nano-reasoner_modelopt-fp8-final_format_fix" \
  "VLM_BASE_URL" "http://rtvi-vlm:8000" "RT_VLM_DEVICE_ID" "0" \
  "RTVI_VLM_MODEL_TO_USE" "cosmos-reason3" \
  "RTVI_VLLM_GPU_MEMORY_UTILIZATION" "0.4"
run_dry_run_up_and_check_generated_env "generated.env search selects SBSA RT Embed tag" "search" \
  -i 127.0.0.1 -H OTHER --use-sbsa-images -d -- \
  "VSS_RT_EMBED_TAG" "develop-latest-sbsa"
_mock_brev_one_gpu_dir="$(mktemp -d)"
CLEANUP_DIRS+=("${_mock_brev_one_gpu_dir}")
cat > "${_mock_brev_one_gpu_dir}/nvidia-smi" <<'EOF'
#!/bin/bash
if [[ "$*" == *"--query-gpu=index"* ]]; then
  printf '0\n'
else
  printf 'NVIDIA RTX PRO 6000 Blackwell\n'
fi
EOF
chmod +x "${_mock_brev_one_gpu_dir}/nvidia-smi"
# One GPU cannot host RT-CV + RT-VLM and RT-Embed + LLM, so local RT-VLM is rejected.
PATH="${_mock_brev_one_gpu_dir}:${PATH}" BREV_ENV_ID=test-env run_negative_test "search Brev 1 GPU rejects default local RT-VLM" 1 up -p search -i 127.0.0.1 -d
PATH="${_mock_brev_one_gpu_dir}:${PATH}" BREV_ENV_ID=test-env VLM_ENDPOINT_URL=http://127.0.0.1:9998 run_dry_run_up_and_check_generated_env "generated.env search Brev 1 GPU allows remote VLM" "search" \
  -i 127.0.0.1 --use-remote-vlm --vlm my-remote-vlm -d -- \
  "VLM_MODE" "remote" "RTVI_VLM_MODEL_PATH" "none" "RT_VLM_DEVICE_ID" "0"

_mock_brev_two_gpu_dir="$(mktemp -d)"
CLEANUP_DIRS+=("${_mock_brev_two_gpu_dir}")
cat > "${_mock_brev_two_gpu_dir}/nvidia-smi" <<'EOF'
#!/bin/bash
if [[ "$*" == *"--query-gpu=index"* ]]; then
  printf '0\n1\n'
else
  printf 'NVIDIA RTX PRO 6000 Blackwell\nNVIDIA RTX PRO 6000 Blackwell\n'
fi
EOF
chmod +x "${_mock_brev_two_gpu_dir}/nvidia-smi"
# Two GPUs are enough for a local RT-VLM now that it shares GPU 0 with RT-CV.
PATH="${_mock_brev_two_gpu_dir}:${PATH}" BREV_ENV_ID=test-env run_dry_run_up_and_check_generated_env "generated.env search Brev 2 GPU wires local RT-VLM" "search" \
  -i 127.0.0.1 -d -- \
  "VLM_DEVICE_ID" "0" "VLM_MODE" "local_shared" "VLM_NAME_SLUG" "none" "VLM_MODEL_TYPE" "rtvi" \
  "VLM_BASE_URL" "http://rtvi-vlm:8000" "RT_VLM_DEVICE_ID" "0"
PATH="${_mock_brev_two_gpu_dir}:${PATH}" BREV_ENV_ID=test-env VLM_ENDPOINT_URL=http://127.0.0.1:9998 run_dry_run_up_and_check_generated_env "generated.env search Brev 2 GPU allows remote VLM" "search" \
  -i 127.0.0.1 --use-remote-vlm --vlm my-remote-vlm -d -- \
  "VLM_MODE" "remote" "VLM_NAME_SLUG" "none" "VLM_MODEL_TYPE" "rtvi" \
  "VLM_BASE_URL" "http://127.0.0.1:9998" "VLM_PORT" "30082" \
  "RTVI_VLM_ENDPOINT" "http://127.0.0.1:9998/v1" "RTVI_VLM_MODEL_TO_USE" "openai-compat" \
  "RTVI_VLM_MODEL_PATH" "none" "RT_VLM_DEVICE_ID" "0"
_mock_brev_three_gpu_dir="$(mktemp -d)"
CLEANUP_DIRS+=("${_mock_brev_three_gpu_dir}")
cat > "${_mock_brev_three_gpu_dir}/nvidia-smi" <<'EOF'
#!/bin/bash
if [[ "$*" == *"--query-gpu=index"* ]]; then
  printf '0\n1\n2\n'
else
  printf 'NVIDIA RTX PRO 6000 Blackwell\nNVIDIA RTX PRO 6000 Blackwell\nNVIDIA RTX PRO 6000 Blackwell\n'
fi
EOF
chmod +x "${_mock_brev_three_gpu_dir}/nvidia-smi"
# Placement comes from the profile env, not the host GPU count, so a 3-GPU host still
# co-locates RT-VLM with RT-CV on GPU 0 and leaves GPU 2 unused.
PATH="${_mock_brev_three_gpu_dir}:${PATH}" BREV_ENV_ID=test-env run_dry_run_up_and_check_generated_env "generated.env search Brev 3 GPU wires RT-VLM" "search" \
  -i 127.0.0.1 -d -- \
  "VLM_DEVICE_ID" "0" "VLM_NAME_SLUG" "none" "VLM_MODEL_TYPE" "rtvi" \
  "VLM_BASE_URL" "http://rtvi-vlm:8000" "RT_VLM_DEVICE_ID" "0"

# --- Setup paths: data directory and profile-specific setup messaging (assert dry-run output) ---
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

# Alerts profile: dry-run should indicate model download runs in ds-start phase 0.
_out_alerts="$(mktemp)"
timeout "${TEST_TIMEOUT}" "$DEV_PROFILE" up -p alerts -i 127.0.0.1 -m verification -d > "${_out_alerts}" 2>&1
if grep -q "Alerts model download runs in ds-start.sh phase 0 (perception)." "${_out_alerts}" && ! grep -q "ngc registry model download-version" "${_out_alerts}"; then
  echo "PASS: alerts dry-run output reflects ds-start phase-0 model download"
  ((TESTS_PASSED++)) || true
else
  echo "FAIL: alerts dry-run output should show ds-start phase-0 handoff and no direct NGC model download commands"
  ((TESTS_FAILED++)) || true
fi
rm -f "${_out_alerts}"

# Search profile: dry-run should indicate model download runs in ds-start phase 0.
_out_search="$(mktemp)"
timeout "${TEST_TIMEOUT}" "$DEV_PROFILE" up -p search -i 127.0.0.1 -d > "${_out_search}" 2>&1
if grep -q "Search model download runs in ds-start.sh phase 0 (perception)." "${_out_search}" && ! grep -q "ngc registry model download-version" "${_out_search}"; then
  echo "PASS: search dry-run output reflects ds-start phase-0 model download"
  ((TESTS_PASSED++)) || true
else
  echo "FAIL: search dry-run output should show ds-start phase-0 handoff and no direct NGC model download commands"
  ((TESTS_FAILED++)) || true
fi
rm -f "${_out_search}"

# --- Warehouse RT-CV model acquisition: manifests and flattened paths ---
_warehouse_model_config_failed=0
_warehouse_root="${REPO_ROOT}/deploy/docker/industry-profiles/warehouse-operations"
_warehouse_2d_manifest="${_warehouse_root}/warehouse-2d-app/models-download.json"
_warehouse_3d_manifest="${_warehouse_root}/warehouse-3d-app/models-download.json"
_warehouse_mv3dt_manifest="${_warehouse_root}/warehouse-mv3dt-app/models-download.json"

if ! jq -e '
  .downloads == [{
    "model": "nvidia/tao/rtdetr_2d_warehouse:deployable_rn50_v1.0.2",
    "org": "nvidia",
    "sourcePath": "rtdetr_2d_warehouse_vdeployable_rn50_v1.0.2/rtdetr_warehouse_v1.0.2.fp16.onnx",
    "destPath": "rtdetr_warehouse_v1.0.2.fp16.onnx"
  }]
' "${_warehouse_2d_manifest}" >/dev/null; then
  echo "FAIL: warehouse 2D manifest should download RT-DETR to the flattened model root"
  ((_warehouse_model_config_failed++)) || true
fi

if ! jq -e '
  .downloads == [{
    "model": "nvidia/tao/sparse4d_rn50:deployable_v2.2",
    "org": "nvidia",
    "sourcePath": "sparse4d_warehouse_v2.2_r50.onnx",
    "destPath": "sparse4d/sparse4d_warehouse_v2.2.onnx"
  }]
' "${_warehouse_3d_manifest}" >/dev/null; then
  echo "FAIL: warehouse 3D manifest should download only Sparse4D to its flattened path"
  ((_warehouse_model_config_failed++)) || true
fi

if ! jq -e '
  (.downloads | length) == 2
  and any(.downloads[]; .model == "nvidia/tao/rtdetr_2d_warehouse:deployable_rn50_v1.0.2" and .destPath == "rtdetr_warehouse_v1.0.2.fp16.onnx")
  and any(.downloads[]; .model == "nvidia/tao/bodypose3dnet:deployable_accuracy_onnx_1.0" and .sourcePath == "bodypose3dnet_accuracy.onnx" and .destPath == "BodyPose3DNet/bodypose3dnet_accuracy.onnx")
' "${_warehouse_mv3dt_manifest}" >/dev/null; then
  echo "FAIL: warehouse MV3DT manifest should download flattened RT-DETR and BodyPose3DNet artifacts"
  ((_warehouse_model_config_failed++)) || true
fi

if [[ ! -s "${_warehouse_root}/warehouse-3d-app/deepstream/anchors/_ov_kmeans900_v2.2.npy" ]]; then
  echo "FAIL: warehouse 3D repository anchor asset is missing or empty"
  ((_warehouse_model_config_failed++)) || true
fi

if ! grep -q '^onnx_file: /opt/storage/sparse4d/sparse4d_warehouse_v2.2.onnx$' "${_warehouse_root}/warehouse-3d-app/deepstream/configs/config.yaml" \
  || ! grep -q '^engine_file: /opt/storage/sparse4d/model.engine$' "${_warehouse_root}/warehouse-3d-app/deepstream/configs/config.yaml"; then
  echo "FAIL: warehouse 3D config should use namespaced flattened model and engine paths"
  ((_warehouse_model_config_failed++)) || true
fi

if grep -E 'models/mtmc|models/sparse4d/ov|models-download-warehouse-' \
  "${_warehouse_root}/warehouse-2d-app/warehouse-2d-app.yml" \
  "${_warehouse_root}/warehouse-3d-app/warehouse-3d-app.yml" \
  "${_warehouse_root}/warehouse-mv3dt-app/warehouse-mv3dt-app.yml" >/dev/null; then
  echo "FAIL: warehouse Compose should not use legacy app-data model mounts or download init services"
  ((_warehouse_model_config_failed++)) || true
fi

for _wh_yml in \
  "${_warehouse_root}/warehouse-2d-app/warehouse-2d-app.yml" \
  "${_warehouse_root}/warehouse-3d-app/warehouse-3d-app.yml" \
  "${_warehouse_root}/warehouse-mv3dt-app/warehouse-mv3dt-app.yml"; do
  if ! grep -q 'models-download.json:/opt/config/models-download.json:ro' "${_wh_yml}"; then
    echo "FAIL: ${_wh_yml} should mount models-download.json for ds-start phase 0"
    ((_warehouse_model_config_failed++)) || true
  fi
done

_rtvi_compose="${REPO_ROOT}/deploy/docker/services/rtvi/rtvi-cv/compose.yaml"
if grep -q '^  download-models:' "${_rtvi_compose}" \
  || ! grep -q 'download-models.sh' "${_rtvi_compose}" \
  || ! grep -q 'user: "0:0"' "${_rtvi_compose}"; then
  echo "FAIL: base rtvi-cv compose should drop download-models service and run perception as root with download script mounted"
  ((_warehouse_model_config_failed++)) || true
fi

_helm_job="${REPO_ROOT}/deploy/helm/services/rtvi/charts/rtvi-cv/templates/job-download-models.yaml"
_helm_ss="${REPO_ROOT}/deploy/helm/services/rtvi/charts/rtvi-cv/templates/statefulset.yaml"
if [[ -e "${_helm_job}" ]] \
  || grep -q 'wait-for-models' "${_helm_ss}" \
    "${REPO_ROOT}/deploy/helm/services/rtvi/charts/rtvi-cv/templates/statefulset-standalone-2d.yaml" \
    "${REPO_ROOT}/deploy/helm/services/rtvi/charts/rtvi-cv/templates/statefulset-standalone-3d.yaml" \
    "${REPO_ROOT}/deploy/helm/services/rtvi/charts/rtvi-cv/templates/statefulset-standalone-mv3dt.yaml" \
  || ! grep -q 'ensure_models_from_manifest' \
    "${REPO_ROOT}/deploy/docker/services/rtvi/rtvi-cv/ds-start.sh" \
    "${REPO_ROOT}/deploy/helm/services/rtvi/charts/rtvi-cv/files/ds-start.sh"; then
  echo "FAIL: no-init download contract missing (Job/wait removed; ensure_models present)"
  ((_warehouse_model_config_failed++)) || true
fi

_standalone_skill_defaults="${REPO_ROOT}/skills/vss-deploy-detection-tracking-2d/assets/deploy-defaults.yml"
if grep -E 'vss-warehouse-app-data/models/(mtmc|sparse4d/ov)' "${_standalone_skill_defaults}" >/dev/null \
  || ! grep -q 'ref: *nvidia/tao/rtdetr_2d_warehouse:deployable_rn50_v1.0.2' "${_standalone_skill_defaults}" \
  || ! grep -q 'ref: *nvidia/tao/sparse4d_rn50:deployable_v2.2' "${_standalone_skill_defaults}" \
  || ! grep -q 'kind: *repo' "${_standalone_skill_defaults}"; then
  echo "FAIL: standalone detection skill should use NGC model packages and repository Sparse4D companions"
  ((_warehouse_model_config_failed++)) || true
fi

if [[ ${_warehouse_model_config_failed} -eq 0 ]]; then
  echo "PASS: warehouse RT-CV model manifests and flattened paths are aligned"
  ((TESTS_PASSED++)) || true
else
  ((TESTS_FAILED++)) || true
fi

_mdx_volume_decl_matches="$(grep -R -n -E --include='*.yml' --include='*.yaml' '(^[[:space:]]+- mdx-(elastic|kafka|logstash|nvstreamer|calibration-toolkit)[A-Za-z0-9_-]*:|^[[:space:]]+mdx-(elastic|kafka|logstash|nvstreamer|calibration-toolkit)[A-Za-z0-9_-]*:)' "${REPO_ROOT}/deploy/docker" || true)"
if [[ -n "${_mdx_volume_decl_matches}" ]]; then
  echo "FAIL: Compose volume declarations should not use mdx-* names"
  echo "${_mdx_volume_decl_matches}" | sed 's/^/    /'
  ((TESTS_FAILED++)) || true
else
  echo "PASS: Compose volume declarations use non-mdx names"
  ((TESTS_PASSED++)) || true
fi

_helm_mv3dt_values="${REPO_ROOT}/deploy/helm/industry-profiles/warehouse-operations/warehouse-mv3dt-app/values.yaml"
_helm_mv3dt_statefulset="${REPO_ROOT}/deploy/helm/services/rtvi/charts/rtvi-cv/templates/statefulset-standalone-mv3dt.yaml"
_helm_mv3dt_defaults="${REPO_ROOT}/deploy/helm/services/rtvi/charts/rtvi-cv/values.yaml"
if grep -q 'downloadModelsFromNgc: true' "${_helm_mv3dt_values}" \
  && grep -q 'model: nvidia/tao/rtdetr_2d_warehouse:deployable_rn50_v1.0.2' "${_helm_mv3dt_values}" \
  && grep -q 'model: nvidia/tao/bodypose3dnet:deployable_accuracy_onnx_1.0' "${_helm_mv3dt_values}" \
  && grep -q 'destPath: BodyPose3DNet/bodypose3dnet_accuracy.onnx' "${_helm_mv3dt_values}" \
  && grep -q 'DS_MODEL_DOWNLOAD' "${_helm_mv3dt_statefulset}" \
  && grep -q 'name: ensure-mv3dt-engine-dirs' "${_helm_mv3dt_statefulset}" \
  && ! grep -q 'wait-for-models' "${_helm_mv3dt_statefulset}" \
  && ! grep -Eq 'prepare-mv3dt-models|runtime-storage|rtdetrPvcSubPath|bodyPosePvcSubPath' \
    "${_helm_mv3dt_statefulset}" "${_helm_mv3dt_defaults}"; then
  echo "PASS: warehouse Helm MV3DT uses per-file models via ds-start phase 0 and direct PVC storage"
  ((TESTS_PASSED++)) || true
else
  echo "FAIL: warehouse Helm MV3DT should use flattened model downloads without wait-for-models or legacy copies"
  ((TESTS_FAILED++)) || true
fi

BLUEPRINT_DEPLOY="${REPO_ROOT}/deploy/docker/scripts/blueprint-deploy.sh"
if grep -Fq 'Warehouse RT-CV model download runs in ds-start phase 0' "${BLUEPRINT_DEPLOY}" \
  && grep -q 'mkdir -p "${data_directory}/models"' "${BLUEPRINT_DEPLOY}" \
  && ! grep -q 'models/mv3dt/BodyPose3DNet' "${BLUEPRINT_DEPLOY}"; then
  echo "PASS: blueprint-deploy.sh prepares flattened warehouse models dir and delegates RT-CV download to ds-start"
  ((TESTS_PASSED++)) || true
else
  echo "FAIL: blueprint-deploy.sh should create flattened models/ and log ds-start warehouse model download"
  ((TESTS_FAILED++)) || true
fi

_warehouse_3d_skill="${REPO_ROOT}/skills/vss-deploy-detection-tracking-3d"
if ! grep -R -E 'models/mv3dt/BodyPose3DNet|models/mtmc' \
  "${_warehouse_3d_skill}/SKILL.md" \
  "${_warehouse_3d_skill}/references" \
  "${_warehouse_3d_skill}/evals" >/dev/null; then
  echo "PASS: warehouse MV3DT skill uses flattened per-file model paths"
  ((TESTS_PASSED++)) || true
else
  echo "FAIL: warehouse MV3DT skill should not reference legacy app-data model paths"
  ((TESTS_FAILED++)) || true
fi

_compose_mv3dt_root="${_warehouse_root}/warehouse-mv3dt-app"
_helm_mv3dt_start="${REPO_ROOT}/deploy/helm/services/rtvi/charts/rtvi-cv/files/warehouse-standalone-mv3dt/deepstream/init-scripts/ds-start-mv3dt.sh"
if cmp -s "${_compose_mv3dt_root}/deepstream/init-scripts/ds-start-mv3dt.sh" "${_helm_mv3dt_start}" \
  && grep -q 'PERCEPTION_IMAGE:-nvcr.io/nvstaging/vss-core/vss-rt-cv' "${_compose_mv3dt_root}/warehouse-mv3dt-app.yml" \
  && grep -q 'VSS_RT_CV_TAG:-3.3.0-26.07.2' "${_compose_mv3dt_root}/warehouse-mv3dt-app.yml"; then
  echo "PASS: warehouse MV3DT startup script and perception fallback are aligned across Compose and Helm"
  ((TESTS_PASSED++)) || true
else
  echo "FAIL: warehouse MV3DT Compose and Helm startup semantics or perception fallback diverged"
  ((TESTS_FAILED++)) || true
fi

# NGC download failures must stop before kernel setup, docker login, or compose.
run_ngc_download_fail_fast_test() {
  local name="${1}"
  local scenario="${2}"
  local expected_error="${3}"
  shift 3
  local args=("$@")
  local mock_dir
  mock_dir="$(mktemp -d)"
  CLEANUP_DIRS+=("${mock_dir}")
  local ngc_state_file="${mock_dir}/ngc-state"

  cat > "${mock_dir}/ngc" <<'EOF'
#!/bin/bash
scenario="${NGC_FAIL_SCENARIO:-}"
state_file="${NGC_MOCK_STATE_FILE:-}"
case "${scenario}" in
  search)
    echo "mock ngc RT-DETR warehouse failure" >&2
    exit 42
    ;;
  alerts-first)
    echo "mock ngc trafficcamnet failure" >&2
    exit 42
    ;;
  alerts-second)
    count=0
    if [[ -n "${state_file}" && -f "${state_file}" ]]; then
      count="$(cat "${state_file}")"
    fi
    count=$((count + 1))
    if [[ -n "${state_file}" ]]; then
      echo "${count}" > "${state_file}"
    fi
    if [[ ${count} -eq 1 ]]; then
      mkdir -p trafficcamnet_transformer_lite_vdeployable_resnet50_v2.0
      printf 'mock trafficcamnet onnx\n' > trafficcamnet_transformer_lite_vdeployable_resnet50_v2.0/resnet50_trafficcamnet_rtdetr.fp16.onnx
      exit 0
    fi
    echo "mock ngc grounding DINO failure" >&2
    exit 43
    ;;
  *)
    echo "unknown NGC mock scenario: ${scenario}" >&2
    exit 44
    ;;
esac
EOF
  cat > "${mock_dir}/docker" <<'EOF'
#!/bin/bash
echo "MOCK_DOCKER_REACHED $*" >&2
exit 0
EOF
  cat > "${mock_dir}/sudo" <<'EOF'
#!/bin/bash
echo "MOCK_SUDO_REACHED $*" >&2
exit 0
EOF
  cat > "${mock_dir}/sysctl" <<'EOF'
#!/bin/bash
echo "MOCK_SYSCTL_REACHED $*" >&2
exit 0
EOF
  cat > "${mock_dir}/bash" <<'EOF'
#!/bin/bash
echo "MOCK_BASH_REACHED $*" >&2
exit 0
EOF
  cat > "${mock_dir}/chmod" <<'EOF'
#!/bin/bash
exit 0
EOF
  cat > "${mock_dir}/id" <<'EOF'
#!/bin/bash
if [[ "${1:-}" == "-u" ]]; then
  echo 1000
else
  /usr/bin/id "$@"
fi
EOF
  chmod +x "${mock_dir}"/*

  local ngc_profiles=(base lvs search alerts)
  local ngc_gen_envs=()
  local ngc_backups=()
  local ngc_profile ngc_gen_env ngc_backup
  for ngc_profile in "${ngc_profiles[@]}"; do
    ngc_gen_env="$(generated_env_path "${ngc_profile}")"
    ngc_gen_envs+=("${ngc_gen_env}")
    if [[ -f "${ngc_gen_env}" ]]; then
      ngc_backup="$(mktemp)"
      cp "${ngc_gen_env}" "${ngc_backup}"
      CLEANUP_RESTORES+=("${ngc_backup}|${ngc_gen_env}")
    else
      ngc_backup=""
    fi
    ngc_backups+=("${ngc_backup}")
  done

  local out_file err_file exit_code failed
  out_file="$(mktemp)"
  err_file="$(mktemp)"
  cd "${REPO_ROOT}"
  set +e
  NGC_FAIL_SCENARIO="${scenario}" NGC_MOCK_STATE_FILE="${ngc_state_file}" PATH="${mock_dir}:${PATH}" timeout "${TEST_TIMEOUT}" "$DEV_PROFILE" up "${args[@]}" > "${out_file}" 2> "${err_file}"
  exit_code=$?
  set -e
  failed=0

  if [[ ${exit_code} -eq 124 ]]; then
    echo "FAIL: ${name} (timed out)"
    ((failed++)) || true
  elif [[ ${exit_code} -ne 1 ]]; then
    echo "FAIL: ${name} (expected exit 1, got ${exit_code})"
    ((failed++)) || true
  fi
  if ! grep -q "${expected_error}" "${out_file}" "${err_file}"; then
    echo "FAIL: ${name} (missing download failure error: ${expected_error})"
    ((failed++)) || true
  fi
  if grep -q "Logging into nvcr.io" "${out_file}" "${err_file}" || grep -q "Starting docker compose" "${out_file}" "${err_file}" || grep -q "MOCK_DOCKER_REACHED login" "${out_file}" "${err_file}" || grep -q "MOCK_DOCKER_REACHED compose --env-file" "${out_file}" "${err_file}"; then
    echo "FAIL: ${name} (docker login/compose up path was reached)"
    ((failed++)) || true
  fi
  if grep -q "Applying VSS Linux kernel settings" "${out_file}" "${err_file}" || grep -q "MOCK_SUDO_REACHED bash -c" "${out_file}" "${err_file}" || grep -q "MOCK_SUDO_REACHED sysctl" "${out_file}" "${err_file}" || grep -q "MOCK_BASH_REACHED" "${out_file}" "${err_file}" || grep -q "MOCK_SYSCTL_REACHED" "${out_file}" "${err_file}"; then
    echo "FAIL: ${name} (kernel settings path was reached)"
    ((failed++)) || true
  fi

  local ngc_idx
  for ngc_idx in "${!ngc_gen_envs[@]}"; do
    ngc_gen_env="${ngc_gen_envs[${ngc_idx}]}"
    ngc_backup="${ngc_backups[${ngc_idx}]}"
    if [[ -n "${ngc_backup}" && -f "${ngc_backup}" ]]; then
      mv "${ngc_backup}" "${ngc_gen_env}"
    else
      rm -f "${ngc_gen_env}"
    fi
  done
  rm -f "${out_file}" "${err_file}"

  if [[ ${failed} -gt 0 ]]; then
    ((TESTS_FAILED++)) || true
  else
    echo "PASS: ${name}"
    ((TESTS_PASSED++)) || true
  fi
}

# --- Profile env split: stable .env plus script-modifiable overrides.env ---
_common_overrides_env_keys=(
  HARDWARE_PROFILE COMPOSE_PROJECT_NAME COMPOSE_PROFILES
  LLM_DEVICE_ID VLM_DEVICE_ID LLM_MODE VLM_MODE
  LLM_NAME LLM_NAME_SLUG LLM_ENV_FILE LLM_BASE_URL LLM_MODEL_TYPE
  VLM_NAME VLM_NAME_SLUG VLM_ENV_FILE VLM_BASE_URL VLM_MODEL_TYPE
  VSS_APPS_DIR VSS_DATA_DIR HOST_IP EXTERNAL_IP
  HAPROXY_PORT HAPROXY_HOST_PORT VSS_PUBLIC_HTTP_PROTOCOL VSS_PUBLIC_WS_PROTOCOL VSS_PUBLIC_HOST VSS_PUBLIC_PORT
  VST_CONFIG_PATH VST_EXTERNAL_URL VST_BASE_URL VSS_AGENT_REPORTS_BASE_URL VSS_AGENT_EXTERNAL_URL
  VSS_UI_HOST_PORT VSS_AGENT_HOST_PORT PHOENIX_HOST_PORT REDIS_HOST_PORT
  VST_INGRESS_HOST_PORT SENSOR_HTTP_HOST_PORT STREAM_PROCESSOR_HTTP_HOST_PORT RTSP_SERVER_HOST_PORT RTSP_SERVER_HOST_PORT_END
  NGC_CLI_API_KEY NVIDIA_API_KEY OPENAI_API_KEY
)
_split_failed=0
for _profile in base lvs search alerts; do
  _stable_env="${REPO_ROOT}/deploy/docker/developer-profiles/dev-profile-${_profile}/.env"
  _overrides_env="${REPO_ROOT}/deploy/docker/developer-profiles/dev-profile-${_profile}/overrides.env"
  if [[ ! -f "${_stable_env}" || ! -f "${_overrides_env}" ]]; then
    echo "FAIL: dev-profile-${_profile} should have both .env and overrides.env"
    ((_split_failed++)) || true
    continue
  fi
  _expected_override_keys=("${_common_overrides_env_keys[@]}")
  _allowed_duplicate_keys=()
  case "${_profile}" in
    base)
      _expected_override_keys+=(EVAL_LLM_JUDGE_NAME EVAL_LLM_JUDGE_BASE_URL RTVI_VLM_PORT RTVI_VLM_IMAGE_TAG RTVI_VLM_ENDPOINT RTVI_VLM_MODEL_TO_USE RTVI_VLLM_GPU_MEMORY_UTILIZATION RTVI_VLM_MAX_MODEL_LEN RTVI_VLM_MODEL_PATH)
      _expected_stable_keys=(MODE RTVI_VLM_MAX_MODEL_LEN)
      _allowed_duplicate_keys=(RTVI_VLM_MAX_MODEL_LEN)
      ;;
    lvs)
      _expected_override_keys+=(RT_VLM_DEVICE_ID VLM_PORT RTVI_VLM_PORT EVAL_LLM_JUDGE_NAME EVAL_LLM_JUDGE_BASE_URL SDR_CONTROLLER_CONFIG_PATH NVSTREAMER_CONFIG_DIR RTVI_VLM_ENDPOINT RTVI_VLM_MODEL_TO_USE RTVI_VLLM_GPU_MEMORY_UTILIZATION RTVI_VLM_MAX_MODEL_LEN RTVI_VLM_MODEL_PATH)
      _expected_override_keys+=(NVSTREAMER_HTTP_HOST_PORT BACKEND_HOST_PORT LVS_MCP_HOST_PORT ELASTICSEARCH_HOST_PORT KAFKA_HOST_PORT KIBANA_HOST_PORT DCGM_EXPORTER_HOST_PORT SDRC_CONTROLLER_HOST_PORT SDRC_PROXY_HOST_PORT SDRC_DIRECT_HOST_PORT SDRC_ENVOY_ADMIN_HOST_PORT)
      _expected_stable_keys=(MODE LVS_TAG RTVI_VLM_IMAGE_TAG)
      ;;
    search)
      _expected_override_keys+=(MEDIA_SERVICE_ENDPOINT REACT_APP_API_ENDPOINT_BASE_URL EVAL_LLM_JUDGE_NAME EVAL_LLM_JUDGE_BASE_URL SDR_CONTROLLER_CONFIG_PATH NVSTREAMER_CONFIG_DIR RT_VLM_DEVICE_ID RTVI_VLM_PORT RTVI_VLM_IMAGE_TAG RTVI_VLM_ENDPOINT RTVI_VLM_MODEL_TO_USE RTVI_VLLM_GPU_MEMORY_UTILIZATION RTVI_VLM_MAX_MODEL_LEN RTVI_VLM_MODEL_PATH)
      _expected_override_keys+=(VIDEO_ANALYTICS_API_HOST_PORT RTVI_CV_HOST_PORT NVSTREAMER_HTTP_HOST_PORT ELASTICSEARCH_HOST_PORT KAFKA_HOST_PORT KIBANA_HOST_PORT SDRC_CONTROLLER_HOST_PORT SDRC_PROXY_HOST_PORT SDRC_DIRECT_HOST_PORT SDRC_ENVOY_ADMIN_HOST_PORT)
      _expected_stable_keys=(MODE VSS_RT_CV_TAG)
      ;;
    alerts)
      _expected_override_keys+=(MODE RT_VLM_DEVICE_ID VLM_PORT RTVI_VLM_PORT PERCEPTION_DOCKERFILE_PREFIX VLM_AS_VERIFIER_CONFIG_FILE_PREFIX VLM_AS_VERIFIER_CONFIG_FILE VLM_AS_VERIFIER_ALERT_TYPE_CONFIG_FILE NVSTREAMER_CONFIG_DIR NEXT_PUBLIC_APP_SUBTITLE VSS_RT_CV_TAG RTVI_VLM_IMAGE_TAG RTVI_VLM_ENDPOINT RTVI_VLM_MODEL_TO_USE RTVI_VLLM_GPU_MEMORY_UTILIZATION RTVI_VLM_MAX_MODEL_LEN RTVI_VLM_MODEL_PATH RTVI_VLM_OPENAI_MODEL_DEPLOYMENT_NAME SDR_CONTROLLER_CONFIG_PATH)
      _expected_override_keys+=(VIDEO_ANALYTICS_API_HOST_PORT RTVI_CV_HOST_PORT VSS_VA_MCP_HOST_PORT ALERT_BRIDGE_HOST_PORT NVSTREAMER_HTTP_HOST_PORT ELASTICSEARCH_HOST_PORT KAFKA_HOST_PORT KIBANA_HOST_PORT SDRC_CONTROLLER_HOST_PORT SDRC_PROXY_HOST_PORT SDRC_DIRECT_HOST_PORT SDRC_ENVOY_ADMIN_HOST_PORT)
      _expected_stable_keys=()
      ;;
  esac
  for _key in "${_expected_override_keys[@]}"; do
    _allow_duplicate=0
    for _duplicate_key in "${_allowed_duplicate_keys[@]}"; do
      if [[ "${_key}" == "${_duplicate_key}" ]]; then
        _allow_duplicate=1
        break
      fi
    done
    if [[ ${_allow_duplicate} -eq 0 ]] && grep -Eq "^${_key}=" "${_stable_env}"; then
      echo "FAIL: dev-profile-${_profile}/.env should not define override-layer ${_key}"
      ((_split_failed++)) || true
    fi
    if ! grep -Eq "^${_key}=" "${_overrides_env}"; then
      echo "FAIL: dev-profile-${_profile}/overrides.env should define ${_key}"
      ((_split_failed++)) || true
    fi
  done
  for _key in "${_expected_stable_keys[@]}"; do
    _allow_duplicate=0
    for _duplicate_key in "${_allowed_duplicate_keys[@]}"; do
      if [[ "${_key}" == "${_duplicate_key}" ]]; then
        _allow_duplicate=1
        break
      fi
    done
    if ! grep -Eq "^${_key}=" "${_stable_env}"; then
      echo "FAIL: dev-profile-${_profile}/.env should keep static ${_key}"
      ((_split_failed++)) || true
    fi
    if [[ ${_allow_duplicate} -eq 0 ]] && grep -Eq "^${_key}=" "${_overrides_env}"; then
      echo "FAIL: dev-profile-${_profile}/overrides.env should not define static ${_key}"
      ((_split_failed++)) || true
    fi
  done
  if grep -Eq '^[A-Za-z_][A-Za-z0-9_]*HOST_PORT[A-Za-z0-9_]*=' "${_stable_env}"; then
    echo "FAIL: dev-profile-${_profile}/.env should not define host-published port overrides"
    ((_split_failed++)) || true
  fi
  _override_key_pattern="$(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "${_overrides_env}" | cut -d= -f1 | paste -sd'|' -)"
  if [[ -n "${_override_key_pattern}" ]] && grep -En "^[A-Za-z_][A-Za-z0-9_]*=.*\$\{(${_override_key_pattern})([:}])|^[A-Za-z_][A-Za-z0-9_]*=.*\$(${_override_key_pattern})([^A-Za-z0-9_]|$)" "${_stable_env}" >/dev/null; then
    echo "FAIL: dev-profile-${_profile}/.env should not reference overrides.env keys; Compose expands .env before overrides.env"
    ((_split_failed++)) || true
  fi
done
_warehouse_stable_env="${REPO_ROOT}/deploy/docker/industry-profiles/warehouse-operations/.env"
_warehouse_overrides_env="${REPO_ROOT}/deploy/docker/industry-profiles/warehouse-operations/overrides.env"
_warehouse_host_port_keys=(
  HAPROXY_HOST_PORT VSS_UI_HOST_PORT VSS_AGENT_HOST_PORT VSS_VA_MCP_HOST_PORT ALERT_BRIDGE_HOST_PORT
  VIDEO_ANALYTICS_API_HOST_PORT RTVI_CV_HOST_PORT RTVI_CV_MV3DT_HOST_PORT RTVI_VLM_PORT NVSTREAMER_HTTP_HOST_PORT PHOENIX_HOST_PORT ELASTICSEARCH_HOST_PORT
  KAFKA_HOST_PORT REDIS_HOST_PORT KIBANA_HOST_PORT TURN_HOST_PORT TURN_MIN_RELAY_HOST_PORT TURN_MAX_RELAY_HOST_PORT
  MQTT_HOST_PORT VST_INGRESS_HOST_PORT SENSOR_HTTP_HOST_PORT STREAM_PROCESSOR_HTTP_HOST_PORT RTSP_SERVER_HOST_PORT RTSP_SERVER_HOST_PORT_END
  SDRC_CONTROLLER_HOST_PORT SDRC_PROXY_HOST_PORT SDRC_DIRECT_HOST_PORT SDRC_ENVOY_ADMIN_HOST_PORT
  DCGM_EXPORTER_HOST_PORT PROMETHEUS_HOST_PORT GRAFANA_HOST_PORT NODE_EXPORTER_HOST_PORT CADVISOR_HOST_PORT
  VSS_AUTO_CALIBRATION_HOST_PORT VSS_AUTO_CALIBRATION_UI_HOST_PORT
)
if [[ -f "${_warehouse_stable_env}" && -f "${_warehouse_overrides_env}" ]]; then
  _warehouse_compose_profile_keys=(
    COMPOSE_PROFILES_WH_2D
    COMPOSE_PROFILES_WH_KAFKA_2D COMPOSE_PROFILES_WH_REDIS_2D COMPOSE_PROFILES_WH_KAFKA_3D COMPOSE_PROFILES_WH_REDIS_3D
    COMPOSE_PROFILES_WH_KAFKA_MV3DT COMPOSE_PROFILES_WH_REDIS_MV3DT
    COMPOSE_PROFILES_WH_AUTO_CALIB_2D COMPOSE_PROFILES_WH_AUTO_CALIB_3D COMPOSE_PROFILES_WH_AUTO_CALIB_MV3DT
    COMPOSE_PROFILES_PLAYBACK_KAFKA_2D COMPOSE_PROFILES_PLAYBACK_REDIS_2D COMPOSE_PROFILES_PLAYBACK_KAFKA_3D COMPOSE_PROFILES_PLAYBACK_REDIS_3D
    COMPOSE_PROFILES_PLAYBACK_KAFKA_MV3DT COMPOSE_PROFILES_PLAYBACK_REDIS_MV3DT
    COMPOSE_PROFILES
  )
  if grep -Eq "^COMPOSE_PROJECT_NAME=" "${_warehouse_stable_env}"; then
    echo "FAIL: warehouse .env should not define user-facing compose project name COMPOSE_PROJECT_NAME"
    ((_split_failed++)) || true
  fi
  if ! grep -Eq "^COMPOSE_PROJECT_NAME=" "${_warehouse_overrides_env}"; then
    echo "FAIL: warehouse overrides.env should define user-facing compose project name COMPOSE_PROJECT_NAME"
    ((_split_failed++)) || true
  fi
  for _key in NVSTREAMER_2D_CONFIG_DIR NVSTREAMER_3D_CONFIG_DIR NVSTREAMER_MV3DT_CONFIG_DIR; do
    if grep -Eq "^${_key}=" "${_warehouse_stable_env}"; then
      echo "FAIL: warehouse .env should not define blueprint path ${_key}"
      ((_split_failed++)) || true
    fi
    if ! grep -Eq "^${_key}=" "${_warehouse_overrides_env}"; then
      echo "FAIL: warehouse overrides.env should define blueprint path ${_key}"
      ((_split_failed++)) || true
    fi
  done
  for _key in "${_warehouse_compose_profile_keys[@]}"; do
    if grep -Eq "^${_key}=" "${_warehouse_stable_env}"; then
      echo "FAIL: warehouse .env should not define user-facing compose profile value ${_key}"
      ((_split_failed++)) || true
    fi
    if ! grep -Eq "^${_key}=" "${_warehouse_overrides_env}"; then
      echo "FAIL: warehouse overrides.env should define user-facing compose profile value ${_key}"
      ((_split_failed++)) || true
    fi
  done
  if grep -Eq '(^_WH_|\$\{_WH_)' "${_warehouse_stable_env}" "${_warehouse_overrides_env}"; then
    echo "FAIL: warehouse env files should not define or reference _WH helper variables"
    ((_split_failed++)) || true
  fi
  for _key in "${_warehouse_host_port_keys[@]}"; do
    if grep -Eq "^${_key}=" "${_warehouse_stable_env}"; then
      echo "FAIL: warehouse .env should not define host-published port override ${_key}"
      ((_split_failed++)) || true
    fi
    if ! grep -Eq "^${_key}=" "${_warehouse_overrides_env}"; then
      echo "FAIL: warehouse overrides.env should define host-published port override ${_key}"
      ((_split_failed++)) || true
    fi
  done
else
  echo "FAIL: warehouse profile should have both .env and overrides.env"
  ((_split_failed++)) || true
fi
_smartcities_overrides_env="${REPO_ROOT}/deploy/docker/industry-profiles/smartcities/overrides.env"
if [[ -f "${_smartcities_overrides_env}" ]]; then
  _smartcities_inherited_override_keys=(COMPOSE_PROJECT_NAME VIDEO_ANALYTICS_API_HOST_PORT)
  for _key in "${_smartcities_inherited_override_keys[@]}"; do
    if grep -Eq "^${_key}=" "${_smartcities_overrides_env}"; then
      echo "FAIL: smartcities overlay should inherit user-facing override ${_key} from the selected base profile"
      ((_split_failed++)) || true
    fi
  done
else
  echo "FAIL: smartcities profile should have overrides.env"
  ((_split_failed++)) || true
fi
_shared_service_env_specs=(
  "deploy/docker/services/agent/agent.env:VSS_AGENT_HOST VSS_AGENT_PORT VSS_AGENT_OBJECT_STORE_TYPE PHOENIX_ENDPOINT VSS_ES_PORT VSS_VA_MCP_PORT VIDEO_ANALYSIS_MCP_URL"
  "deploy/docker/services/alert/alert.env:ALERT_BRIDGE_PORT ALERT_BRIDGE_URL"
  "deploy/docker/services/ui/ui.env:NEXT_PUBLIC_APP_TITLE NEXT_PUBLIC_ENABLE_CHAT_SIDEBAR NEXT_PUBLIC_ENABLE_CHAT_TAB NEXT_PUBLIC_ENABLE_MAP_TAB"
  "deploy/docker/services/infra/infra.env:ELASTICSEARCH_CONNECTION_MAX_ATTEMPTS"
  "deploy/docker/services/nim/nim.env:LLM_PORT VLM_PORT VLM_NIM_KVCACHE_PERCENT"
  "deploy/docker/services/nvstreamer/.env:NVSTREAMER_IMAGE_TAG NVSTREAMER_HTTP_PORT NVSTREAMER_INSTALL_ADDITIONAL_PACKAGES"
  "deploy/docker/services/rtvi/rtvi.env:RTVI_VLM_BASE_URL RTVI_VLM_KAFKA_BOOTSTRAP_SERVERS RTVI_VLM_KAFKA_INCIDENT_TOPIC RTVI_VLM_KAFKA_ENABLED RTVI_EMBED_IMAGE RTVI_EMBED_TAG RTVI_EMBED_PORT PERCEPTION_IMAGE OTEL_SDK_DISABLED OTEL_EXPORTER_OTLP_ENDPOINT OTEL_METRICS_EXPORTER"
  "deploy/docker/services/vios/vst.env:VST_PORT VST_INGRESS_HTTP_PORT RTSP_SERVER_PORT_END VST_INTERNAL_IP VST_INGRESS_ENDPOINT VST_INTERNAL_URL VST_STREAM_PROCESSOR_IMAGE_TAG VST_SENSOR_IMAGE_TAG VST_INGRESS_IMAGE_TAG"
)
for _spec in "${_shared_service_env_specs[@]}"; do
  _file="${REPO_ROOT}/${_spec%%:*}"
  _keys="${_spec#*:}"
  if [[ ! -f "${_file}" ]]; then
    echo "FAIL: shared service env file missing: ${_file}"
    ((_split_failed++)) || true
    continue
  fi
  for _key in ${_keys}; do
    if ! grep -Eq "^${_key}=" "${_file}"; then
      echo "FAIL: ${_file} should define shared service default ${_key}"
      ((_split_failed++)) || true
    fi
  done
done
_nvstreamer_shared_compose="${REPO_ROOT}/deploy/docker/services/nvstreamer/compose.yml"
if ! grep -Eq '^  nvstreamer-base:' "${_nvstreamer_shared_compose}"; then
  echo "FAIL: shared NVStreamer Compose should define nvstreamer-base"
  ((_split_failed++)) || true
fi
if grep -Eq '(developer-profiles|industry-profiles)/' "${_nvstreamer_shared_compose}"; then
  echo "FAIL: shared NVStreamer Compose should not reference blueprint directories"
  ((_split_failed++)) || true
fi
_nvstreamer_service_definition_specs=(
  "nvstreamer-alerts:deploy/docker/developer-profiles/dev-profile-alerts/compose.yml deploy/docker/industry-profiles/smartcities/compose.yml"
  "nvstreamer-lvs:deploy/docker/developer-profiles/dev-profile-lvs/compose.yml"
  "nvstreamer-2d-fusion:deploy/docker/developer-profiles/dev-profile-search/video-analytics-2d-app/compose.yml"
  "nvstreamer-2d:deploy/docker/industry-profiles/warehouse-operations/warehouse-2d-app/warehouse-2d-app.yml"
  "nvstreamer-3d:deploy/docker/industry-profiles/warehouse-operations/warehouse-3d-app/warehouse-3d-app.yml"
  "nvstreamer-mv3dt:deploy/docker/industry-profiles/warehouse-operations/warehouse-mv3dt-app/warehouse-mv3dt-app.yml"
)
for _spec in "${_nvstreamer_service_definition_specs[@]}"; do
  _service="${_spec%%:*}"
  _expected_definition_paths="${_spec#*:}"
  _expected_definition_count="$(wc -w <<< "${_expected_definition_paths}")"
  _definition_count="$(grep -R -E --include='*.yml' --include='*.yaml' "^  ${_service}:" \
    "${REPO_ROOT}/deploy/docker/developer-profiles" \
    "${REPO_ROOT}/deploy/docker/industry-profiles" | wc -l)"
  if [[ "${_definition_count}" -ne "${_expected_definition_count}" ]]; then
    echo "FAIL: ${_service} should have ${_expected_definition_count} blueprint-owned Compose definition(s) (found ${_definition_count})"
    ((_split_failed++)) || true
  fi
  for _definition_path in ${_expected_definition_paths}; do
    if ! grep -Eq "^  ${_service}:" "${REPO_ROOT}/${_definition_path}"; then
      echo "FAIL: ${_service} definition missing from ${_definition_path}"
      ((_split_failed++)) || true
    fi
  done
done
if [[ ${_split_failed} -eq 0 ]]; then
  echo "PASS: developer profile env split keeps profile-specific override-layer values isolated"
  ((TESTS_PASSED++)) || true
else
  ((TESTS_FAILED++)) || true
fi

# --- COMPOSE_PROFILES service-list integrity ---
_profile_tags_file="$(mktemp)"
while IFS= read -r -d '' _compose_file; do
  sed -nE 's/^[[:space:]]*profiles:[[:space:]]*\["([^"]+)"\].*/\1/p' "${_compose_file}"
done < <(find "${REPO_ROOT}/deploy/docker" -type f \( -name '*.yml' -o -name '*.yaml' \) ! -path '*/services/nim/*' -print0) | sort -u > "${_profile_tags_file}"

load_compose_env_values() {
  local env_file="${1}"
  local line key value
  while IFS= read -r line || [[ -n "${line}" ]]; do
    [[ "${line}" =~ ^[[:space:]]*# ]] && continue
    [[ "${line}" =~ ^[[:space:]]*$ ]] && continue
    if [[ "${line}" =~ ^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
      key="${BASH_REMATCH[1]}"
      value="${BASH_REMATCH[2]}"
      value="${value%$'\r'}"
      value="${value#\"}"; value="${value%\"}"
      value="${value#\'}"; value="${value%\'}"
      _compose_env_values["${key}"]="${value}"
    fi
  done < "${env_file}"
}

resolve_compose_env_value() {
  local value="${1}"
  local depth key replacement
  for ((depth = 0; depth < 10; depth++)); do
    if [[ "${value}" =~ \$\{([A-Za-z_][A-Za-z0-9_]*)\} ]]; then
      key="${BASH_REMATCH[1]}"
      replacement="${_compose_env_values[${key}]:-}"
      value="${value//\$\{${key}\}/${replacement}}"
    else
      break
    fi
  done
  printf '%s' "${value}"
}

_compose_profiles_failed=0
for _compose_env_file in \
  "${REPO_ROOT}"/deploy/docker/developer-profiles/dev-profile-*/.env \
  "${REPO_ROOT}"/deploy/docker/developer-profiles/dev-profile-*/overrides.env \
  "${REPO_ROOT}"/deploy/docker/industry-profiles/*/.env \
  "${REPO_ROOT}"/deploy/docker/industry-profiles/*/overrides.env; do
  [[ -f "${_compose_env_file}" ]] || continue
  declare -A _compose_env_values=()
  _sibling_env_file="$(dirname "${_compose_env_file}")/.env"
  if [[ "$(basename "${_compose_env_file}")" == "overrides.env" && -f "${_sibling_env_file}" ]]; then
    load_compose_env_values "${_sibling_env_file}"
  fi
  load_compose_env_values "${_compose_env_file}"
  for _var in "${!_compose_env_values[@]}"; do
    [[ "${_var}" == COMPOSE_PROFILES* ]] || continue
    _resolved_profiles="$(resolve_compose_env_value "${_compose_env_values[${_var}]}")"
    IFS=',' read -r -a _profile_tokens <<< "${_resolved_profiles}"
    for _token in "${_profile_tokens[@]}"; do
      _token="${_token//[[:space:]]/}"
      [[ -n "${_token}" ]] || continue
      case "${_token}" in
        llm_*|vlm_*) continue ;;
      esac
      if ! grep -Fxq "${_token}" "${_profile_tags_file}"; then
        echo "FAIL: ${_compose_env_file} ${_var} references missing service profile '${_token}'"
        ((_compose_profiles_failed++)) || true
      fi
    done
  done
  unset _compose_env_values
done
rm -f "${_profile_tags_file}"
if [[ ${_compose_profiles_failed} -eq 0 ]]; then
  echo "PASS: COMPOSE_PROFILES service lists reference existing non-NIM compose profiles"
  ((TESTS_PASSED++)) || true
else
  ((TESTS_FAILED++)) || true
fi

# --- generated.env content: dry-run up still writes/updates the file ---
# Run up with specific options and assert generated.env contains expected vars, then restore.
run_dry_run_up_and_check_generated_env "generated.env HOST_IP and HARDWARE_PROFILE from options" "base" \
 -i 127.0.0.1 -H RTXPRO6000BW -d -- \
  "HOST_IP" "127.0.0.1" "HARDWARE_PROFILE" "RTXPRO6000BW"
run_dry_run_up_and_check_generated_env "generated.env HARDWARE_PROFILE RTXPRO4500BW" "base" \
 -i 127.0.0.1 -H RTXPRO4500BW -d -- \
  "HARDWARE_PROFILE" "RTXPRO4500BW"
run_dry_run_up_and_check_generated_env "generated.env HARDWARE_PROFILE OTHER" "base" \
 -i 127.0.0.1 -H OTHER -d -- \
  "HARDWARE_PROFILE" "OTHER"

run_dry_run_up_and_check_generated_env "generated.env LVS defaults to Nemotron 3.5 Lightning on H100" "lvs" \
 -i 127.0.0.1 -H H100 -d -- \
  "HARDWARE_PROFILE" "H100" \
  "LLM_NAME" "nvidia/nemotron-3.5-lightning-30b-a3b" \
  "LLM_NAME_SLUG" "nemotron-3.5-lightning-30b-a3b"

run_dry_run_up_and_check_generated_env "generated.env LVS defaults to Nemotron 3.5 Lightning on GB300" "lvs" \
 -i 127.0.0.1 -H GB300 -d -- \
  "HARDWARE_PROFILE" "GB300" \
  "LLM_NAME" "nvidia/nemotron-3.5-lightning-30b-a3b" \
  "LLM_NAME_SLUG" "nemotron-3.5-lightning-30b-a3b"

for _nemotron_3_5_env in \
  "${REPO_ROOT}"/deploy/docker/services/nim/nemotron-3.5-lightning-30b-a3b/hw-*.env; do
  if grep -Fq -- "--reasoning-parser nemotron_v3 --enable-auto-tool-choice --tool-call-parser qwen3_coder" "${_nemotron_3_5_env}"; then
    echo "PASS: $(basename "${_nemotron_3_5_env}") enables Nemotron 3.5 reasoning and tool calling"
    ((TESTS_PASSED++)) || true
  else
    echo "FAIL: $(basename "${_nemotron_3_5_env}") does not enable Nemotron 3.5 reasoning and tool calling"
    ((TESTS_FAILED++)) || true
  fi
done


# DGX-SPARK: for each profile, run dry-run with -H DGX-SPARK and assert sbsa variants (keys from profile overrides.env).
# DGX-SPARK (and IGX-THOR) are only valid for base and alerts
for _profile in base alerts; do
  run_spark_test_for_profile "${_profile}"
done

run_dry_run_up_and_check_generated_env "generated.env LLM slugs and names" "base" \
 -i 127.0.0.1 --llm nvidia/nemotron-3-nano -d -- \
  "LLM_NAME_SLUG" "nemotron-3-nano" "LLM_NAME" "nvidia/nemotron-3-nano"

run_dry_run_up_and_check_generated_env "generated.env base local VLM uses RT-VLM integrated checkpoint" "base" \
 -i 127.0.0.1 -H OTHER -d -- \
  "VLM_MODE" "local_shared" "VLM_NAME" "nim_nvidia_cosmos3-nano-reasoner_bf16-final" "VLM_NAME_SLUG" "none" \
  "VLM_BASE_URL" "http://rtvi-vlm:8000" "VLM_MODEL_TYPE" "rtvi" "VLM_PORT" "8018" \
  "RTVI_VLM_ENDPOINT" "''" "RTVI_VLM_MODEL_TO_USE" "cosmos-reason3" \
  "RTVI_VLM_MODEL_PATH" "ngc:nim/nvidia/cosmos3-nano-reasoner:bf16-final" \
  "RTVI_VLM_KAFKA_ENABLED" "false"

run_dry_run_up_and_check_generated_env "generated.env MODE for alerts" "alerts" \
 -i 127.0.0.1 -m verification -d -- \
  "MODE" "2d_cv" \
  "NEXT_PUBLIC_APP_SUBTITLE" '"Vision (Alerts - CV)"' \
  "RTVI_VLM_KAFKA_ENABLED" "false"

run_dry_run_up_and_check_generated_env "generated.env alerts UI subtitle follows real-time MODE" "alerts" \
 -i 127.0.0.1 -m real-time -d -- \
  "MODE" "2d_vlm" \
  "NEXT_PUBLIC_APP_SUBTITLE" '"Vision (Alerts - VLM)"'

# Real-time alerts are driven by RT-VLM's Kafka events, so the verification-only
# RTVI_VLM_KAFKA_ENABLED=false override must be commented out for MODE=2d_vlm.
_alerts_gen_env="$(generated_env_path "alerts")"
_alerts_gen_env_backup=""
if [[ -f "${_alerts_gen_env}" ]]; then
  _alerts_gen_env_backup="$(mktemp)"
  cp "${_alerts_gen_env}" "${_alerts_gen_env_backup}"
  CLEANUP_RESTORES+=("${_alerts_gen_env_backup}|${_alerts_gen_env}")
fi
if timeout "${TEST_TIMEOUT}" "$DEV_PROFILE" up -p alerts -i 127.0.0.1 -m real-time -d > /dev/null 2>&1 \
  && ! grep -q '^RTVI_VLM_KAFKA_ENABLED=' "${_alerts_gen_env}" \
  && grep -Eq '^#[[:space:]]*RTVI_VLM_KAFKA_ENABLED=' "${_alerts_gen_env}"; then
  echo "PASS: alerts real-time comments out RTVI_VLM_KAFKA_ENABLED so RT-VLM publishes to Kafka"
  ((TESTS_PASSED++)) || true
else
  echo "FAIL: alerts real-time should comment out RTVI_VLM_KAFKA_ENABLED (verification-only override)"
  ((TESTS_FAILED++)) || true
fi
if [[ -n "${_alerts_gen_env_backup}" && -f "${_alerts_gen_env_backup}" ]]; then
  mv "${_alerts_gen_env_backup}" "${_alerts_gen_env}"
fi

_alerts_overrides_env="${REPO_ROOT}/deploy/docker/developer-profiles/dev-profile-alerts/overrides.env"
_alerts_overrides_env_backup="$(mktemp)"
cp "${_alerts_overrides_env}" "${_alerts_overrides_env_backup}"
CLEANUP_RESTORES+=("${_alerts_overrides_env_backup}|${_alerts_overrides_env}")
_alerts_overrides_env_without_newline="$(mktemp)"
printf '%s' "$(cat "${_alerts_overrides_env}")" > "${_alerts_overrides_env_without_newline}"
mv "${_alerts_overrides_env_without_newline}" "${_alerts_overrides_env}"
LLM_ENDPOINT_URL=http://127.0.0.1:9999 VLM_ENDPOINT_URL=http://127.0.0.1:9998 run_dry_run_up_and_check_generated_env "generated.env appends vars after overrides.env without trailing newline" "alerts" \
 -i 127.0.0.1 -H RTXPRO6000BW -m verification --use-remote-llm --llm my-llm --use-remote-vlm --vlm my-vlm -d -- \
  "GF_SECURITY_ADMIN_USER" "''" \
  "VST_CONFIG_PATH" "${REPO_ROOT}/deploy/docker/services/vios/configs"
mv "${_alerts_overrides_env_backup}" "${_alerts_overrides_env}"
if grep -Fq 'SDR_CONTROLLER_CONFIG_PATH=${VSS_APPS_DIR}/developer-profiles/dev-profile-alerts/sdrc/${MODE}' "${REPO_ROOT}/deploy/docker/developer-profiles/dev-profile-alerts/overrides.env"; then
  echo "PASS: alerts SDR_CONTROLLER_CONFIG_PATH stays with override-layer path/mode values"
  ((TESTS_PASSED++)) || true
else
  echo "FAIL: alerts SDR_CONTROLLER_CONFIG_PATH should stay in overrides.env with path/mode values"
  ((TESTS_FAILED++)) || true
fi

# Base profile: when LLM_DEVICE_ID and VLM_DEVICE_ID match (e.g. both 0), derived modes are local_shared for both; when they differ, both are local
run_dry_run_up_and_check_generated_env "generated.env LLM_MODE VLM_MODE HOST_IP (base defaults)" "base" \
 -i 127.0.0.1 -d -- \
  "LLM_MODE" "local_shared" "VLM_MODE" "local_shared" "HOST_IP" "127.0.0.1"

# When LLM_DEVICE_ID=VLM_DEVICE_ID (same device), derived modes are local_shared for both
_base_overrides_env="${REPO_ROOT}/deploy/docker/developer-profiles/dev-profile-base/overrides.env"
if [[ -f "${_base_overrides_env}" ]]; then
  _backup_base="$(mktemp)"
  cp "${_base_overrides_env}" "${_backup_base}"
  CLEANUP_RESTORES+=("${_backup_base}|${_base_overrides_env}")
  # Temporarily set both device IDs to 1 so we derive local_shared for both
  sed -i 's/^LLM_DEVICE_ID=.*/LLM_DEVICE_ID=1/' "${_base_overrides_env}"
  sed -i 's/^VLM_DEVICE_ID=.*/VLM_DEVICE_ID=1/' "${_base_overrides_env}"
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

# EXTERNAL_IP is written unmasked to generated.env, but must be redacted in script output.
_gen_env_external_mask="$(generated_env_path "base")"
_backup_external_mask=""
if [[ -f "${_gen_env_external_mask}" ]]; then
  _backup_external_mask="$(mktemp)"
  cp "${_gen_env_external_mask}" "${_backup_external_mask}"
fi
_out_external_mask="$(mktemp)"
_err_external_mask="$(mktemp)"
cd "${REPO_ROOT}"
set +e
timeout "${TEST_TIMEOUT}" "$DEV_PROFILE" up -p base -i 127.0.0.1 -e 192.168.1.100 -d > "${_out_external_mask}" 2> "${_err_external_mask}"
_exit_external_mask=$?
set -e
_failed_external_mask=0
if [[ ${_exit_external_mask} -eq 124 ]]; then
  echo "FAIL: EXTERNAL_IP masked in output (timed out)"
  ((_failed_external_mask++)) || true
elif [[ ${_exit_external_mask} -ne 0 ]]; then
  echo "FAIL: EXTERNAL_IP masked in output (dev-profile exit ${_exit_external_mask})"
  sed 's/^/    /' "${_out_external_mask}"
  ((_failed_external_mask++)) || true
else
  if grep -Fq "192.168.1.100" "${_out_external_mask}" "${_err_external_mask}"; then
    echo "FAIL: EXTERNAL_IP masked in output (unmasked value appeared in output)"
    ((_failed_external_mask++)) || true
  fi
  if ! grep -Fq "external-ip:               192*******100" "${_out_external_mask}"; then
    echo "FAIL: EXTERNAL_IP masked in output (argument summary missing masked value)"
    ((_failed_external_mask++)) || true
  fi
  if ! grep -Fq "[INFO] Set EXTERNAL_IP=192*******100" "${_out_external_mask}"; then
    echo "FAIL: EXTERNAL_IP masked in output (env log missing masked value)"
    ((_failed_external_mask++)) || true
  fi
fi
if [[ -n "${_backup_external_mask}" && -f "${_backup_external_mask}" ]]; then
  mv "${_backup_external_mask}" "${_gen_env_external_mask}"
else
  rm -f "${_gen_env_external_mask}"
fi
rm -f "${_out_external_mask}" "${_err_external_mask}"
if [[ ${_failed_external_mask} -gt 0 ]]; then
  ((TESTS_FAILED++)) || true
else
  echo "PASS: EXTERNAL_IP masked in output"
  ((TESTS_PASSED++)) || true
fi
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
mkdir -p "$(dirname "${_rel_under_repo}")"
touch "${_rel_under_repo}"
run_dry_run_up_and_check_generated_env "generated.env relative --llm-env-file from REPO_ROOT stored as absolute" "base" \
 -i 127.0.0.1 --llm-env-file "tests/rel_llm.env" -d -- \
  "LLM_ENV_FILE" "${REPO_ROOT}/tests/rel_llm.env"
rm -f "${_rel_under_repo}"
rmdir "${REPO_ROOT}/tests" 2>/dev/null || true

run_dry_run_up_and_check_generated_env "generated.env other LLM model openai/gpt-oss-20b" "base" \
 -i 127.0.0.1 --llm openai/gpt-oss-20b -d -- \
  "LLM_NAME_SLUG" "gpt-oss-20b" "LLM_NAME" "openai/gpt-oss-20b"

run_dry_run_up_and_check_generated_env "generated.env base --vlm cosmos-reason1 maps to RT-VLM path+basename" "base" \
 -i 127.0.0.1 --vlm nvidia/cosmos-reason1-7b -d -- \
  "VLM_NAME_SLUG" "none" "VLM_NAME" "nim_nvidia_cosmos-reason1-7b_1_1-fp8-dynamic" \
  "RTVI_VLM_MODEL_PATH" "ngc:nim/nvidia/cosmos-reason1-7b:1.1-fp8-dynamic" \
  "RTVI_VLM_MODEL_TO_USE" "cosmos-reason1" "VLM_MODEL_TYPE" "rtvi"

run_dry_run_up_and_check_generated_env "generated.env base --vlm cosmos-reason2 maps to RT-VLM path+basename" "base" \
 -i 127.0.0.1 --vlm nvidia/cosmos-reason2-8b -d -- \
  "VLM_NAME_SLUG" "none" "VLM_NAME" "nim_nvidia_cosmos-reason2-8b_hf-0303" \
  "RTVI_VLM_MODEL_PATH" "ngc:nim/nvidia/cosmos-reason2-8b:hf-0303" \
  "RTVI_VLM_MODEL_TO_USE" "cosmos-reason2" "VLM_MODEL_TYPE" "rtvi"

run_dry_run_up_and_check_generated_env "generated.env base --vlm cosmos3-reasoner maps to RT-VLM path+basename" "base" \
 -i 127.0.0.1 --vlm nvidia/cosmos3-reasoner -d -- \
  "VLM_NAME_SLUG" "none" "VLM_NAME" "nim_nvidia_cosmos3-nano-reasoner_bf16-final" \
  "RTVI_VLM_MODEL_PATH" "ngc:nim/nvidia/cosmos3-nano-reasoner:bf16-final" \
  "RTVI_VLM_MODEL_TO_USE" "cosmos-reason3" "VLM_MODEL_TYPE" "rtvi"

run_dry_run_up_and_check_generated_env "generated.env base --vlm cosmos3-reasoner-fp8 maps to RT-VLM FP8 path+basename" "base" \
 -i 127.0.0.1 --vlm nvidia/cosmos3-reasoner-fp8 -d -- \
  "VLM_NAME_SLUG" "none" "VLM_NAME" "nim_nvidia_cosmos3-nano-reasoner_modelopt-fp8-final_format_fix" \
  "RTVI_VLM_MODEL_PATH" "ngc:nim/nvidia/cosmos3-nano-reasoner:modelopt-fp8-final_format_fix" \
  "RTVI_VLM_MODEL_TO_USE" "cosmos-reason3" "VLM_MODEL_TYPE" "rtvi"

run_dry_run_up_and_check_generated_env "generated.env base --vlm Qwen maps to RT-VLM git path+basename" "base" \
 -i 127.0.0.1 --vlm Qwen/Qwen3-VL-8B-Instruct -d -- \
  "VLM_NAME_SLUG" "none" "VLM_NAME" "Qwen3-VL-8B-Instruct" \
  "RTVI_VLM_MODEL_PATH" "git:https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct" \
  "RTVI_VLM_MODEL_TO_USE" "vllm-compatible" "VLM_MODEL_TYPE" "rtvi"

# Search routes --vlm through RT-VLM (integrated checkpoint), same as base/lvs; see the base --vlm tests above.
run_dry_run_up_and_check_generated_env "generated.env search --vlm cosmos3-reasoner-fp8 maps to RT-VLM FP8 path+basename" "search" \
 -i 127.0.0.1 --vlm nvidia/cosmos3-reasoner-fp8 -d -- \
  "VLM_NAME_SLUG" "none" "VLM_NAME" "nim_nvidia_cosmos3-nano-reasoner_modelopt-fp8-final_format_fix" \
  "RTVI_VLM_MODEL_PATH" "ngc:nim/nvidia/cosmos3-nano-reasoner:modelopt-fp8-final_format_fix" \
  "RTVI_VLM_MODEL_TO_USE" "cosmos-reason3" "VLM_MODEL_TYPE" "rtvi"

run_dry_run_up_and_check_generated_env "generated.env search --vlm Qwen maps to RT-VLM git path+basename" "search" \
 -i 127.0.0.1 --vlm Qwen/Qwen3-VL-8B-Instruct -d -- \
  "VLM_NAME_SLUG" "none" "VLM_NAME" "Qwen3-VL-8B-Instruct" \
  "RTVI_VLM_MODEL_PATH" "git:https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct" \
  "RTVI_VLM_MODEL_TO_USE" "vllm-compatible" "VLM_MODEL_TYPE" "rtvi"

# Docker Compose commands use these .env defaults directly, so agent config paths must be container paths.
for _env in \
  "${REPO_ROOT}/deploy/docker/developer-profiles/dev-profile-base/.env" \
  "${REPO_ROOT}/deploy/docker/developer-profiles/dev-profile-search/.env" \
  "${REPO_ROOT}/deploy/docker/developer-profiles/dev-profile-lvs/.env" \
  "${REPO_ROOT}/deploy/docker/developer-profiles/dev-profile-alerts/.env" \
  "${REPO_ROOT}/deploy/docker/industry-profiles/warehouse-operations/.env"; do
  if grep -q "^VSS_AGENT_CONFIG_FILE=/vss-agent/deploy/docker/" "${_env}"; then
    echo "PASS: ${_env} uses an in-container VSS_AGENT_CONFIG_FILE path"
    ((TESTS_PASSED++)) || true
  else
    echo "FAIL: ${_env} should use an in-container VSS_AGENT_CONFIG_FILE path"
    ((TESTS_FAILED++)) || true
  fi
  if grep -q "^VSS_VA_MCP_CONFIG_FILE=" "${_env}"; then
    if grep -q "^VSS_VA_MCP_CONFIG_FILE=/vss-agent/deploy/docker/" "${_env}"; then
      echo "PASS: ${_env} uses an in-container VSS_VA_MCP_CONFIG_FILE path"
      ((TESTS_PASSED++)) || true
    else
      echo "FAIL: ${_env} should use an in-container VSS_VA_MCP_CONFIG_FILE path"
      ((TESTS_FAILED++)) || true
    fi
  fi
done

# Search vss-agent config validates RTVI_CV_ENDPOINT at startup; compose must export it.
if grep -q "RTVI_CV_ENDPOINT: \${RTVI_CV_ENDPOINT:-http://vss-rtvi-cv:\${RTVI_CV_PORT:-9000}}" "${REPO_ROOT}/deploy/docker/services/agent/compose.yml"; then
  echo "PASS: vss-agent compose exports RTVI_CV_ENDPOINT for search config"
  ((TESTS_PASSED++)) || true
else
  echo "FAIL: vss-agent compose should export RTVI_CV_ENDPOINT for search config"
  ((TESTS_FAILED++)) || true
fi

# Helm passes bare VST host aliases as well as URL-form endpoints; Docker agent needs the same contract.
_agent_compose="${REPO_ROOT}/deploy/docker/services/agent/compose.yml"
if grep -Fq "EXTERNAL_IP:" "${_agent_compose}" && grep -Fq "INTERNAL_IP:" "${_agent_compose}" && grep -Fq "VST_BASE_URL:" "${_agent_compose}"; then
  echo "PASS: vss-agent compose exports Helm-compatible VST host aliases"
  ((TESTS_PASSED++)) || true
else
  echo "FAIL: vss-agent compose should export Helm-compatible VST host aliases"
  ((TESTS_FAILED++)) || true
fi

_vst_service_env="${REPO_ROOT}/deploy/docker/services/vios/vst.env"
if grep -Fq "VST_INTERNAL_IP=" "${_vst_service_env}" && grep -Fq "VST_INGRESS_ENDPOINT=" "${_vst_service_env}" && grep -Fq "VST_INTERNAL_URL=" "${_vst_service_env}"; then
  echo "PASS: VIOS service env exposes shared Helm-compatible VST endpoint defaults"
  ((TESTS_PASSED++)) || true
else
  echo "FAIL: VIOS service env should expose shared Helm-compatible VST endpoint defaults"
  ((TESTS_FAILED++)) || true
fi
for _profile in base search lvs alerts; do
  _env="${REPO_ROOT}/deploy/docker/developer-profiles/dev-profile-${_profile}/.env"
  _overrides_env="${REPO_ROOT}/deploy/docker/developer-profiles/dev-profile-${_profile}/overrides.env"
  if ! grep -Fq "VST_INTERNAL_IP=" "${_env}" && ! grep -Fq "VST_INGRESS_ENDPOINT=" "${_env}" && grep -Fq "VST_BASE_URL=" "${_overrides_env}"; then
    echo "PASS: dev-profile-${_profile} uses shared VST defaults and keeps external VST override variables in overrides.env"
    ((TESTS_PASSED++)) || true
  else
    echo "FAIL: dev-profile-${_profile} should use shared VST defaults and keep external VST override variables in overrides.env"
    ((TESTS_FAILED++)) || true
  fi
done
_env="${REPO_ROOT}/deploy/docker/industry-profiles/warehouse-operations/.env"
_overrides_env="${REPO_ROOT}/deploy/docker/industry-profiles/warehouse-operations/overrides.env"
if ! grep -Fq "VST_INTERNAL_IP=" "${_env}" && ! grep -Fq "VST_INGRESS_ENDPOINT=" "${_env}" && grep -Fq "VST_BASE_URL=" "${_overrides_env}"; then
  echo "PASS: warehouse profile uses shared VST defaults and keeps external VST override variables in overrides.env"
  ((TESTS_PASSED++)) || true
else
  echo "FAIL: warehouse profile should use shared VST defaults and keep external VST override variables in overrides.env"
  ((TESTS_FAILED++)) || true
fi

# Alert bridge verifier configs need the internal VST URL for media lookup.
for _cfg in \
  "${REPO_ROOT}/deploy/docker/developer-profiles/dev-profile-alerts/vlm-as-verifier/configs/config.yml" \
  "${REPO_ROOT}/deploy/docker/developer-profiles/dev-profile-alerts/vlm-as-verifier/configs/EDGE-LOCAL-VLM-config.yml"; do
  _vst_base_count="$(grep -c "base_url: \${VST_INTERNAL_URL}" "${_cfg}" || true)"
  if [[ "${_vst_base_count}" -eq 2 ]]; then
    echo "PASS: alert verifier config ${_cfg} uses VST_INTERNAL_URL for media lookup"
    ((TESTS_PASSED++)) || true
  else
    echo "FAIL: alert verifier config ${_cfg} should set both VST base_url entries to VST_INTERNAL_URL"
    ((TESTS_FAILED++)) || true
  fi
done

# Real-time (2d_vlm) with local VLM: script keeps profile overrides.env defaults for VLM_PORT, RTVI_VLM_ENDPOINT, and RTVI_VLM_MODEL_TO_USE (rtvi-vlm on the Compose network, cosmos-reason3).
run_dry_run_up_and_check_generated_env "generated.env alerts real-time local VLM preserves overrides.env defaults (rtvi-vlm on the Compose network)" "alerts" \
 -i 127.0.0.1 -m real-time -d -- \
  "MODE" "2d_vlm" "VLM_PORT" "8018" "RTVI_VLM_ENDPOINT" "http://rtvi-vlm:8000/v1" "RTVI_VLM_MODEL_TO_USE" "cosmos-reason3"

# Real-time (2d_vlm) with remote VLM: script overrides VLM_PORT to 30082 and RTVI_VLM_MODEL_TO_USE to openai-compat; RTVI_VLM_ENDPOINT comes from --vlm-base-url.
LLM_ENDPOINT_URL=http://127.0.0.1:9999 VLM_ENDPOINT_URL=http://127.0.0.1:9998 run_dry_run_up_and_check_generated_env "generated.env alerts real-time remote VLM sets VLM_PORT=30082 and openai-compat" "alerts" \
 -i 127.0.0.1 -H OTHER -m real-time --use-remote-llm --llm my-llm --use-remote-vlm --vlm my-vlm -d -- \
  "VLM_MODE" "remote" "VLM_PORT" "30082" "RTVI_VLM_ENDPOINT" "http://127.0.0.1:9998/v1" "RTVI_VLM_MODEL_TO_USE" "openai-compat"

_expected_lvs_compose_profiles='kibana-init-container-lvs,nvstreamer-lvs,vss-agent,phoenix,elasticsearch,elasticsearch-init-container,kafka,kafka-topic-init-container,redis,kibana,logstash,broker-health-check,vss-haproxy-ingress,init-dirs,render-config,wdm-env-from-config,wait-for-redis,wait-for-docker-workloads,sdr-controller,rtvi-vlm,vss-ui,lvs-server,centralizedb,vst-ingress,sensor-ms,streamprocessing-ms,dcgm-exporter,llm_${LLM_MODE}_${LLM_NAME_SLUG}'

# LVS with local/local_shared VLM: route LVS through RT-VLM and let RT-VLM load the integrated Cosmos checkpoint.
run_dry_run_up_and_check_generated_env "generated.env lvs local VLM uses RT-VLM integrated checkpoint" "lvs" \
 -i 127.0.0.1 -H OTHER -d -- \
  "VLM_MODE" "local_shared" "VLM_NAME" "nim_nvidia_cosmos3-nano-reasoner_bf16-final" "VLM_NAME_SLUG" "none" \
  "VLM_BASE_URL" "http://rtvi-vlm:8000" "VLM_MODEL_TYPE" "rtvi" "VLM_PORT" "8018" \
  "RTVI_VLM_ENDPOINT" "''" "RTVI_VLM_MODEL_TO_USE" "cosmos-reason3" \
  "RTVI_VLM_MODEL_PATH" "ngc:nim/nvidia/cosmos3-nano-reasoner:bf16-final" \
  "COMPOSE_PROFILES" "${_expected_lvs_compose_profiles}"

# LVS with remote VLM: keep RT-VLM in the stack and point only RT-VLM at the remote OpenAI-compatible endpoint.
LLM_ENDPOINT_URL=http://127.0.0.1:9999 VLM_ENDPOINT_URL=http://127.0.0.1:9998 run_dry_run_up_and_check_generated_env "generated.env lvs remote VLM uses RT-VLM proxy to remote endpoint" "lvs" \
 -i 127.0.0.1 -H OTHER --use-remote-llm --llm my-llm --use-remote-vlm --vlm my-vlm -d -- \
  "VLM_MODE" "remote" "VLM_NAME" "my-vlm" "VLM_NAME_SLUG" "none" \
  "VLM_BASE_URL" "http://127.0.0.1:9998" "VLM_MODEL_TYPE" "rtvi" "VLM_PORT" "30082" \
  "RTVI_VLM_ENDPOINT" "http://127.0.0.1:9998/v1" "RTVI_VLM_MODEL_TO_USE" "openai-compat" \
  "RTVI_VLM_DEFAULT_NUM_FRAMES_PER_SECOND_OR_FIXED_FRAMES_CHUNK" "5" \
  "RTVI_VLM_MODEL_PATH" "none" \
  "COMPOSE_PROFILES" "${_expected_lvs_compose_profiles}"

# Remote endpoints can override the default when their image prompt limit differs.
LLM_ENDPOINT_URL=http://127.0.0.1:9999 VLM_ENDPOINT_URL=http://127.0.0.1:9998 RTVI_VLM_DEFAULT_NUM_FRAMES_PER_SECOND_OR_FIXED_FRAMES_CHUNK=8 run_dry_run_up_and_check_generated_env "generated.env lvs remote VLM preserves explicit frame default" "lvs" \
 -i 127.0.0.1 -H OTHER --use-remote-llm --llm my-llm --use-remote-vlm --vlm my-vlm -d -- \
  "RTVI_VLM_DEFAULT_NUM_FRAMES_PER_SECOND_OR_FIXED_FRAMES_CHUNK" "8"

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

special_env_value='ampersand&backslash\pipe|end'
NVIDIA_API_KEY="${special_env_value}" run_dry_run_up_and_check_generated_env "generated.env preserves literal NVIDIA_API_KEY characters" "base" \
 -i 127.0.0.1 -d -- \
  "NVIDIA_API_KEY" "${special_env_value}"

LLM_ENDPOINT_URL=http://127.0.0.1:9999 VLM_ENDPOINT_URL=http://127.0.0.1:9998 run_dry_run_up_and_check_generated_env "generated.env LLM_MODEL_TYPE VLM_MODEL_TYPE from profile defaults when remote" "base" \
 -i 127.0.0.1 --use-remote-llm --llm my-llm --use-remote-vlm --vlm my-vlm -d -- \
  "LLM_MODEL_TYPE" "nim" "VLM_MODEL_TYPE" "rtvi" "VLM_PORT" "30082" "RTVI_VLM_MODEL_TO_USE" "openai-compat"

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
# Brev resolves secure-link vars at generation time. Pin BREV_LINK_DOMAIN so this test is deterministic on hosts with NetBird/Skybridge configured.
BREV_ENV_ID=test-env BREV_LINK_DOMAIN=brevlab.com run_dry_run_up_and_check_generated_env "generated.env Brev HAProxy + VSS_PUBLIC_HOST" "base" \
 -i 127.0.0.1 -d -- \
  "HAPROXY_PORT" "7777" \
  "VSS_PUBLIC_HTTP_PROTOCOL" "https" \
  "VSS_PUBLIC_WS_PROTOCOL" "wss" \
  "VSS_PUBLIC_HOST" "7777-test-env.brevlab.com" \
  "VSS_PUBLIC_PORT" "443"

# Brev with custom PROXY_PORT in env: generated.env records the resolved proxy port and secure-link host.
BREV_ENV_ID=test-env BREV_LINK_DOMAIN=brevlab.com PROXY_PORT=8080 run_dry_run_up_and_check_generated_env "generated.env Brev with custom PROXY_PORT" "base" \
 -i 127.0.0.1 -d -- \
  "HAPROXY_PORT" "8080" \
  "VSS_PUBLIC_HTTP_PROTOCOL" "https" \
  "VSS_PUBLIC_WS_PROTOCOL" "wss" \
  "VSS_PUBLIC_HOST" "8080-test-env.brevlab.com" \
  "VSS_PUBLIC_PORT" "443"

# Non-Brev: profile HAProxy defaults (script does not inject https/wss or Brev host templates)
run_dry_run_up_and_check_generated_env "generated.env no Brev HAProxy overrides when BREV_ENV_ID unset" "base" \
 -i 127.0.0.1 -d -- \
  "HAPROXY_PORT" "7777" \
  "VSS_PUBLIC_HTTP_PROTOCOL" "http" \
  "VSS_PUBLIC_WS_PROTOCOL" "ws" \
  "VSS_PUBLIC_HOST" '${EXTERNAL_IP}' \
  "VSS_PUBLIC_PORT" '${HAPROXY_HOST_PORT}'

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
elif ! grep -q "\[DRY-RUN\] docker compose -p vss down -v --remove-orphans" "${out_file}"; then
  echo "FAIL: down dry-run (stdout missing '[DRY-RUN] docker compose -p vss down -v --remove-orphans')"
  ((TESTS_FAILED++)) || true
elif ! grep -q "State down completed" "${out_file}"; then
  echo "FAIL: down dry-run (stdout missing 'State down completed')"
  ((TESTS_FAILED++)) || true
else
  echo "PASS: down dry-run"
  ((TESTS_PASSED++)) || true
fi
rm -f "${out_file}" "${err_file}"

# --- Positive: dry-run down honors COMPOSE_PROJECT_NAME from persisted env state ---
_custom_project_overrides="${REPO_ROOT}/deploy/docker/developer-profiles/dev-profile-base/overrides.env"
if [[ -f "${_custom_project_overrides}" ]]; then
  _custom_project_backup="$(mktemp)"
  cp "${_custom_project_overrides}" "${_custom_project_backup}"
  CLEANUP_RESTORES+=("${_custom_project_backup}|${_custom_project_overrides}")
  sed -i 's/^COMPOSE_PROJECT_NAME=.*/COMPOSE_PROJECT_NAME=vss-custom-test/' "${_custom_project_overrides}"
  _custom_project_generated_backups=()
  for _custom_project_profile in base lvs search alerts; do
    _custom_project_generated="${REPO_ROOT}/deploy/docker/developer-profiles/dev-profile-${_custom_project_profile}/generated.env"
    if [[ -f "${_custom_project_generated}" ]]; then
      _custom_project_generated_backup="$(mktemp)"
      cp "${_custom_project_generated}" "${_custom_project_generated_backup}"
      CLEANUP_RESTORES+=("${_custom_project_generated_backup}|${_custom_project_generated}")
      _custom_project_generated_backups+=("${_custom_project_generated_backup}|${_custom_project_generated}")
      if grep -q '^COMPOSE_PROJECT_NAME=' "${_custom_project_generated}"; then
        sed -i 's/^COMPOSE_PROJECT_NAME=.*/COMPOSE_PROJECT_NAME=vss-custom-test/' "${_custom_project_generated}"
      else
        printf '\nCOMPOSE_PROJECT_NAME=vss-custom-test\n' >> "${_custom_project_generated}"
      fi
    fi
  done

  out_file="$(mktemp)"
  err_file="$(mktemp)"
  cd "${REPO_ROOT}"
  set +e
  timeout "${TEST_TIMEOUT}" "$DEV_PROFILE" down --dry-run > "${out_file}" 2> "${err_file}"
  exit_code=$?
  set -e
  mv "${_custom_project_backup}" "${_custom_project_overrides}"
  for _custom_project_restore in "${_custom_project_generated_backups[@]}"; do
    IFS='|' read -r _custom_project_generated_backup _custom_project_generated <<< "${_custom_project_restore}"
    [[ -f "${_custom_project_generated_backup}" ]] && mv "${_custom_project_generated_backup}" "${_custom_project_generated}"
  done
  if [[ ${exit_code} -eq 124 ]]; then
    echo "FAIL: down dry-run with custom COMPOSE_PROJECT_NAME (timed out after ${TEST_TIMEOUT}s)"
    ((TESTS_FAILED++)) || true
  elif [[ ${exit_code} -ne 0 ]]; then
    echo "FAIL: down dry-run with custom COMPOSE_PROJECT_NAME (expected exit 0, got ${exit_code})"
    cat "${out_file}" "${err_file}" | sed 's/^/    /'
    ((TESTS_FAILED++)) || true
  elif ! grep -Fq "[DRY-RUN] docker compose -p vss-custom-test down -v --remove-orphans" "${out_file}"; then
    echo "FAIL: down dry-run with custom COMPOSE_PROJECT_NAME (stdout missing custom project down command)"
    ((TESTS_FAILED++)) || true
  else
    echo "PASS: down dry-run honors custom COMPOSE_PROJECT_NAME"
    ((TESTS_PASSED++)) || true
  fi
  rm -f "${out_file}" "${err_file}"
else
  echo "SKIP: down dry-run honors custom COMPOSE_PROJECT_NAME (base overrides.env not found)"
fi

# --- Positive: dry-run down tears down each distinct COMPOSE_PROJECT_NAME from generated.env files ---
_multi_project_specs=("base:vss-base-test" "lvs:vss-base-test" "alerts:vss-alerts-test")
_multi_project_backups=()
_multi_project_created=()
for _multi_project_spec in "${_multi_project_specs[@]}"; do
  _multi_project_profile="${_multi_project_spec%%:*}"
  _multi_project_name="${_multi_project_spec#*:}"
  _multi_project_generated="${REPO_ROOT}/deploy/docker/developer-profiles/dev-profile-${_multi_project_profile}/generated.env"
  if [[ -f "${_multi_project_generated}" ]]; then
    _multi_project_backup="$(mktemp)"
    cp "${_multi_project_generated}" "${_multi_project_backup}"
    CLEANUP_RESTORES+=("${_multi_project_backup}|${_multi_project_generated}")
    _multi_project_backups+=("${_multi_project_backup}|${_multi_project_generated}")
  else
    _multi_project_created+=("${_multi_project_generated}")
  fi
  printf 'COMPOSE_PROJECT_NAME=%s\n' "${_multi_project_name}" > "${_multi_project_generated}"
done

out_file="$(mktemp)"
err_file="$(mktemp)"
cd "${REPO_ROOT}"
set +e
timeout "${TEST_TIMEOUT}" "$DEV_PROFILE" down --dry-run > "${out_file}" 2> "${err_file}"
exit_code=$?
set -e
for _multi_project_restore in "${_multi_project_backups[@]}"; do
  IFS='|' read -r _multi_project_backup _multi_project_generated <<< "${_multi_project_restore}"
  [[ -f "${_multi_project_backup}" ]] && mv "${_multi_project_backup}" "${_multi_project_generated}"
done
for _multi_project_generated in "${_multi_project_created[@]}"; do
  rm -f "${_multi_project_generated}"
done
if [[ ${exit_code} -eq 124 ]]; then
  echo "FAIL: down dry-run with multiple generated COMPOSE_PROJECT_NAME values (timed out after ${TEST_TIMEOUT}s)"
  ((TESTS_FAILED++)) || true
elif [[ ${exit_code} -ne 0 ]]; then
  echo "FAIL: down dry-run with multiple generated COMPOSE_PROJECT_NAME values (expected exit 0, got ${exit_code})"
  cat "${out_file}" "${err_file}" | sed 's/^/    /'
  ((TESTS_FAILED++)) || true
elif [[ "$(grep -Fc '[DRY-RUN] docker compose -p vss-base-test down -v --remove-orphans' "${out_file}")" != "1" ]]; then
  echo "FAIL: down dry-run with multiple generated COMPOSE_PROJECT_NAME values (vss-base-test not torn down exactly once)"
  ((TESTS_FAILED++)) || true
elif [[ "$(grep -Fc '[DRY-RUN] docker compose -p vss-alerts-test down -v --remove-orphans' "${out_file}")" != "1" ]]; then
  echo "FAIL: down dry-run with multiple generated COMPOSE_PROJECT_NAME values (vss-alerts-test not torn down exactly once)"
  ((TESTS_FAILED++)) || true
else
  echo "PASS: down dry-run tears down each distinct generated COMPOSE_PROJECT_NAME"
  ((TESTS_PASSED++)) || true
fi
rm -f "${out_file}" "${err_file}"

# --- Positive: warehouse down dry-run honors COMPOSE_PROJECT_NAME from env files ---
_warehouse_project_overrides="${REPO_ROOT}/deploy/docker/industry-profiles/warehouse-operations/overrides.env"
_warehouse_project_generated="${REPO_ROOT}/deploy/docker/industry-profiles/warehouse-operations/generated.env"
if [[ -f "${_warehouse_project_overrides}" ]]; then
  _warehouse_project_backup="$(mktemp)"
  cp "${_warehouse_project_overrides}" "${_warehouse_project_backup}"
  CLEANUP_RESTORES+=("${_warehouse_project_backup}|${_warehouse_project_overrides}")
  sed -i 's/^COMPOSE_PROJECT_NAME=.*/COMPOSE_PROJECT_NAME=vss-warehouse-custom-test/' "${_warehouse_project_overrides}"

  _warehouse_generated_backup=""
  if [[ -f "${_warehouse_project_generated}" ]]; then
    _warehouse_generated_backup="$(mktemp)"
    cp "${_warehouse_project_generated}" "${_warehouse_generated_backup}"
    CLEANUP_RESTORES+=("${_warehouse_generated_backup}|${_warehouse_project_generated}")
    sed -i 's/^COMPOSE_PROJECT_NAME=.*/COMPOSE_PROJECT_NAME=vss-warehouse-custom-test/' "${_warehouse_project_generated}"
  fi

  out_file="$(mktemp)"
  err_file="$(mktemp)"
  cd "${REPO_ROOT}"
  set +e
  timeout "${TEST_TIMEOUT}" "$BLUEPRINT_DEPLOY" down -D "${REPO_ROOT}/deploy/docker/data-dir" --dry-run > "${out_file}" 2> "${err_file}"
  exit_code=$?
  set -e
  mv "${_warehouse_project_backup}" "${_warehouse_project_overrides}"
  if [[ -n "${_warehouse_generated_backup}" && -f "${_warehouse_generated_backup}" ]]; then
    mv "${_warehouse_generated_backup}" "${_warehouse_project_generated}"
  fi
  if [[ ${exit_code} -eq 124 ]]; then
    echo "FAIL: warehouse down dry-run with custom COMPOSE_PROJECT_NAME (timed out after ${TEST_TIMEOUT}s)"
    ((TESTS_FAILED++)) || true
  elif [[ ${exit_code} -ne 0 ]]; then
    echo "FAIL: warehouse down dry-run with custom COMPOSE_PROJECT_NAME (expected exit 0, got ${exit_code})"
    cat "${out_file}" "${err_file}" | sed 's/^/    /'
    ((TESTS_FAILED++)) || true
  elif ! grep -Fq "[DRY-RUN] docker compose -p vss-warehouse-custom-test down -v --remove-orphans" "${out_file}"; then
    echo "FAIL: warehouse down dry-run with custom COMPOSE_PROJECT_NAME (stdout missing custom project down command)"
    ((TESTS_FAILED++)) || true
  else
    echo "PASS: warehouse down dry-run honors custom COMPOSE_PROJECT_NAME"
    ((TESTS_PASSED++)) || true
  fi
  rm -f "${out_file}" "${err_file}"
else
  echo "SKIP: warehouse down dry-run honors custom COMPOSE_PROJECT_NAME (warehouse overrides.env not found)"
fi

# --- Summary ---
echo ""
echo "=========================================="
echo "Results: ${TESTS_PASSED} passed, ${TESTS_FAILED} failed"
echo "=========================================="
if [[ ${TESTS_FAILED} -gt 0 ]]; then
  exit 1
fi
exit 0
