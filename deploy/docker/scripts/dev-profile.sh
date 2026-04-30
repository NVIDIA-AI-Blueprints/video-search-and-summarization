#!/bin/bash

# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

script_dir="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
repo_root="$( cd -- "${script_dir}/../../.." &> /dev/null && pwd )"

# Default values
desired_state=""
profile=""
deployment_directory="${repo_root}/deploy/docker"
data_directory="${deployment_directory}/data-dir"
hardware_profile=""
host_ip="$(ip route get 1.1.1.1 | awk '/src/ {for (i=1;i<=NF;i++) if ($i=="src") print $(i+1)}')"
external_ip=""
mode=""
mode_env=""
ngc_cli_api_key="${NGC_CLI_API_KEY:-}"
# NVIDIA_API_KEY and OPENAI_API_KEY from environment (optional); always written to generated.env
nvidia_api_key="${NVIDIA_API_KEY:-}"
openai_api_key="${OPENAI_API_KEY:-}"
dry_run="false"

# NIM-related defaults
# LLM configuration
llm_mode=""
llm=""
llm_device_id=""
llm_base_url=""

# VLM configuration
vlm_mode=""
vlm=""
vlm_device_id=""
vlm_base_url=""
vlm_custom_weights=""
# Optional env file paths (absolute or relative to CWD)
llm_env_file=""
vlm_env_file=""
# Remote LLM/VLM model type (nim, openai)
llm_model_type=""
vlm_model_type=""


# Flags to track explicitly provided options
options_provided=()

# Edge hardware profiles (e.g. DGX-SPARK, IGX-THOR, AGX-THOR): device ID options not accepted
edge_hardware_profiles=('DGX-SPARK' 'IGX-THOR' 'AGX-THOR')

# Returns the first GPU's product name from nvidia-smi (display name), or empty string if nvidia-smi fails or no GPU.
function get_nvidia_smi_gpu_name() {
  local _name
  _name="$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -n1)"
  _name="${_name#"${_name%%[![:space:]]*}"}"
  _name="${_name%"${_name##*[![:space:]]}"}"
  echo "${_name}"
}

# Maps GPU product name (from nvidia-smi) to a canonical hardware type for detection. Returns OTHER if no match.
# AGX-THOR and IGX-THOR both map to THOR (single canonical type). Matching is case-insensitive.
function get_detected_hardware_profile() {
  local _gpu_name="${1}"
  local _gpu_lower="${_gpu_name,,}"
  case "${_gpu_lower}" in
    *h100*) echo "H100" ;;
    *l40s*) echo "L40S" ;;
    *rtx*pro*6000*blackwell*) echo "RTXPRO6000BW" ;;
    *gb10*) echo "DGX-SPARK" ;;
    *thor*) echo "THOR" ;;
    *) echo "OTHER" ;;
  esac
}

# Maps requested hardware_profile (CLI/env) to the same canonical type used by get_detected_hardware_profile.
# AGX-THOR and IGX-THOR both map to THOR; all other profiles map to themselves.
function get_canonical_hardware_profile() {
  local _profile="${1}"
  case "${_profile}" in
    AGX-THOR|IGX-THOR) echo "THOR" ;;
    *) echo "${_profile}" ;;
  esac
}

# Reverse lookup: canonical type -> slash-separated hardware_profile name(s) for display.
function get_canonical_display_name() {
  local _canonical="${1}"
  case "${_canonical}" in
    THOR) echo "AGX-THOR / IGX-THOR" ;;
    *) echo "${_canonical}" ;;
  esac
}

# LLM/VLM model name to slug mapping (for paths and config lookup)
function get_llm_slug() {
  local _name="${1}"
  case "${_name}" in
    nvidia/nvidia-nemotron-nano-9b-v2) echo "nvidia-nemotron-nano-9b-v2" ;;
    nvidia/NVIDIA-Nemotron-Nano-9B-v2-FP8) echo "nvidia-nemotron-nano-9b-v2-fp8" ;;
    nvidia/nemotron-3-nano) echo "nemotron-3-nano" ;;
    nvidia/llama-3.3-nemotron-super-49b-v1.5) echo "llama-3.3-nemotron-super-49b-v1.5" ;;
    openai/gpt-oss-20b) echo "gpt-oss-20b" ;;
    *) echo "" ;;
  esac
}

function get_vlm_slug() {
  local _name="${1}"
  case "${_name}" in
    nvidia/cosmos-reason1-7b) echo "cosmos-reason1-7b" ;;
    nvidia/cosmos-reason2-8b) echo "cosmos-reason2-8b" ;;
    Qwen/Qwen3-VL-8B-Instruct) echo "qwen3-vl-8b-instruct" ;;
    *) echo "" ;;
  esac
}

# Mode: accepted CLI values verification | real-time; written to MODE in env as 2d_cv | 2d_vlm
function get_mode_env_value() {
  local _mode="${1}"
  case "${_mode}" in
    verification) echo "2d_cv" ;;
    real-time) echo "2d_vlm" ;;
    *) echo "" ;;
  esac
}
function get_mode_display_value() {
  local _env_val="${1}"
  case "${_env_val}" in
    2d_cv) echo "verification" ;;
    2d_vlm) echo "real-time" ;;
    *) echo "${_env_val}" ;;
  esac
}

# Gets model name from remote API endpoint (works for both LLM and VLM)
# Arguments: base_url (e.g., http://localhost:30082/v1)
# Returns: model name from the /models endpoint, or empty string on error
function get_remote_model_name() {
  local _base_url="${1}"
  local _model_name _curl_exit_code
  
  _model_name="$(curl -s -f "${_base_url}/v1/models" 2>/dev/null | jq -r '.data[0].id // empty' 2>/dev/null)"
  _curl_exit_code=$?
  
  if [[ ${_curl_exit_code} -ne 0 ]] || [[ -z "${_model_name}" ]]; then
    echo "[WARNING] Failed to retrieve model name from ${_base_url}/v1/models" >&2
    echo ""
    return 1
  fi
  
  echo "${_model_name}"
  return 0
}

function get_env_value() {
  local _env_file="${1}"
  local _var_name="${2}"
  local _val
  if [[ -f "${_env_file}" ]]; then
    _val="$(grep "^${_var_name}=" "${_env_file}" 2>/dev/null | cut -d'=' -f2- | head -1)"
    _val="${_val#[\'\"]}"
    _val="${_val%[\'\"]}"
    echo "${_val}"
  fi
}

# Resolve path to absolute (relative paths are relative to current working directory).
# Outputs normalized absolute path, or empty on error.
function resolve_abs_path() {
  local p="${1}"
  [[ -z "${p}" ]] && { echo ""; return; }
  if [[ "${p}" != /* ]]; then
    p="$(pwd)/${p}"
  fi
  local dir base
  dir="$(dirname "${p}")"
  base="$(basename "${p}")"
  if [[ -d "${dir}" ]]; then
    echo "$(cd "${dir}" && pwd)/${base}"
  else
    echo "${p}"
  fi
}

function mask_secret() {
  local _secret="${1}"
  local _len="${#_secret}"
  if [[ ${_len} -le 6 ]]; then
    echo "******"
  else
    local _first="${_secret:0:3}"
    local _last="${_secret: -3}"
    local _middle_len=$((_len - 6))
    local _mask=$(printf '%*s' "${_middle_len}" '' | tr ' ' '*')
    echo "${_first}${_mask}${_last}"
  fi
}


# Apply VSS kernel settings (IPv6 disable, TCP buffer sizes). Persistent across reboots via /etc/sysctl.d/99-vss.conf.
function set_vss_linux_kernel_settings() {
  sudo mkdir -p /etc/sysctl.d
  sudo bash -c "printf '%s\n' \
    'net.ipv6.conf.all.disable_ipv6 = 1' \
    'net.ipv6.conf.default.disable_ipv6 = 1' \
    'net.ipv6.conf.lo.disable_ipv6 = 1' \
    'net.core.rmem_max = 5242880' \
    'net.core.wmem_max = 5242880' \
    'net.ipv4.tcp_rmem = 4096 87380 16777216' \
    'net.ipv4.tcp_wmem = 4096 65536 16777216' \
    > /etc/sysctl.d/99-vss.conf"
  sudo sysctl --system
}

function usage() {
  echo "Usage: ${0} (up|down) [options]"
  echo "   or: ${0} (-h|--help)"
  echo ""
  echo "Positional arguments:"
  echo "  desired-state                    up or down"
  echo ""
  echo "NOTE: The following are read from the environment (no CLI options):"
  echo "  • NGC_CLI_API_KEY     — required for 'up'"
  echo "  • NVIDIA_API_KEY      — optional; used for accessing remote LLM/VLM endpoints"
  echo "  • OPENAI_API_KEY      — optional; used for accessing remote LLM/VLM endpoints"
  echo "  • LLM_ENDPOINT_URL    — optional; required when --use-remote-llm is passed (both must be set)"
  echo "  • VLM_ENDPOINT_URL    — optional; required when --use-remote-vlm is passed (both must be set)"
  echo "  • VLM_CUSTOM_WEIGHTS  — optional; when --use-remote-vlm is not passed: absolute path to custom weights dir; when --use-remote-vlm is passed, ignored"
  echo "  • ENABLE_CRITIC       — optional; search profile: when true (case-insensitive), enables the critic agent; local VLM is deployed only when the critic agent is enabled and --use-remote-vlm is not passed"
  echo ""
  echo "Options for 'up':"
  echo "  -p, --profile                    [REQUIRED] Profile."
  echo "                                   • One of:"
  echo "                                     - base"
  echo "                                     - lvs"
  echo "                                     - search"
  echo "                                     - alerts"
  echo "                                   • Required for 'up'"
  echo "  -H, --hardware-profile           Hardware profile."
  echo "                                   • One of:"
  echo "                                     - H100"
  echo "                                     - L40S"
  echo "                                     - RTXPRO6000BW"
  echo "                                     - DGX-SPARK"
  echo "                                     - IGX-THOR"
  echo "                                     - AGX-THOR"
  echo "                                     - OTHER"
  echo "                                   • DGX-SPARK, IGX-THOR, and AGX-THOR only valid when profile is base or alerts"
  echo "                                   • DGX-SPARK, IGX-THOR, AGX-THOR: --llm-device-id, --vlm-device-id not accepted"
  echo "  -i, --host-ip                    Host IP."
  echo "                                   • Default: primary IP from ip route"
  echo "  -e, --external-ip                Externally accessible IP."
  echo "  -m, --mode                       Mode for alerts profile."
  echo "                                   • One of:"
  echo "                                     - verification"
  echo "                                     - real-time"
  echo "                                   • Required when profile is alerts"
  echo ""
  echo "  --llm                            LLM model name."
  echo "                                   • One of (local):"
  echo "                                     - nvidia/nvidia-nemotron-nano-9b-v2"
  echo "                                     - nvidia/NVIDIA-Nemotron-Nano-9B-v2-FP8"
  echo "                                     - nvidia/nemotron-3-nano"
  echo "                                     - nvidia/llama-3.3-nemotron-super-49b-v1.5"
  echo "                                     - openai/gpt-oss-20b"
  echo "                                   • When --use-remote-llm is passed, any model name can be passed"
  echo "  --llm-device-id                  LLM device ID."
  echo "                                   • Not allowed when --use-remote-llm is passed"
  echo "                                   • DGX-SPARK, IGX-THOR, AGX-THOR: not accepted"
  echo "  --use-remote-llm                 Use remote LLM; requires LLM_ENDPOINT_URL on the host (both are required together)."
  echo "  --llm-model-type                 LLM backend type when --use-remote-llm is passed: nim or openai."
  echo "  --llm-env-file                   Path to LLM env file. Absolute or relative to CWD."
  echo "                                   • Not allowed when --use-remote-llm is passed"
  echo ""
  echo "  --vlm                            VLM model name."
  echo "                                   • One of (local):"
  echo "                                     - nvidia/cosmos-reason1-7b"
  echo "                                     - nvidia/cosmos-reason2-8b"
  echo "                                     - Qwen/Qwen3-VL-8B-Instruct"
  echo "                                   • Not accepted for profile=alerts or base on IGX-THOR or AGX-THOR"
  echo "                                   • When --use-remote-vlm is passed, any model name can be passed"
  echo "  --vlm-device-id                  VLM device ID."
  echo "                                   • Not allowed when --use-remote-vlm is passed"
  echo "                                   • DGX-SPARK, IGX-THOR, AGX-THOR: not accepted"
  echo "  --use-remote-vlm                 Use remote VLM; requires VLM_ENDPOINT_URL on the host (both are required together)."
  echo "                                   • Not accepted for profile=alerts or base on IGX-THOR or AGX-THOR"
  echo "  --vlm-model-type                 VLM backend type when --use-remote-vlm is passed: nim or openai."
  echo "  --vlm-env-file                   Path to VLM env file. Absolute or relative to CWD."
  echo "                                   • Not allowed when --use-remote-vlm is passed"
  echo "                                   • Not accepted for profile=alerts or base on IGX-THOR or AGX-THOR"
  echo ""
  echo "Options for 'up' and 'down':"
  echo "  -d, --dry-run                    print commands without executing them"
  echo "  -h, --help                       show this help message"
}

function contains_element() {
  local _element _ref_array _array_element
  _element="${1}"
  _ref_array=("${@:2}")
  for _array_element in "${_ref_array[@]}"
  do
    if [[ "${_element}" == "${_array_element}" ]]; then
      return 0
    fi
  done
  return 1
}

function validate_args() {
  local _args _valid_args _valid_desired_states _valid_profiles _valid_modes _all_good
  _args=("${@}")
  _all_good=0

  _valid_args=$(getopt -q -o p:H:i:e:m:dh --long profile:,hardware-profile:,host-ip:,external-ip:,mode:,llm-device-id:,vlm-device-id:,use-remote-llm,use-remote-vlm,llm:,vlm:,llm-model-type:,vlm-model-type:,llm-env-file:,vlm-env-file:,dry-run,help -- "${_args[@]}")
  if [[ $? -ne 0 ]]; then
    echo "[ERROR] Invalid usage: ${_args[*]}"
    ((_all_good++))
  else
    eval set -- "${_valid_args}"

    # Check for help flag first
    while true; do
      case "${1}" in
        -h | --help) usage; exit 0 ;;
        --) shift; break ;;
        *) shift ;;
      esac
    done

    # Get positional argument (desired-state)
    if [[ -z "${1}" ]]; then
      echo "[ERROR] desired-state is required"
      ((_all_good++))
    else
      _valid_desired_states=('up' 'down')
      if ! contains_element "${1}" "${_valid_desired_states[@]}"; then
        echo "[ERROR] Invalid desired-state: ${1}. Must be 'up' or 'down'"
        ((_all_good++))
      fi
    fi
  fi

  if [[ _all_good -gt 0 ]]; then
    echo ""
    usage
    exit 1
  fi
}

function process_args() {
  local _args _valid_args _valid_profiles _valid_modes _all_good
  _args=("${@}")
  _all_good=0

  _valid_args=$(getopt -q -o p:H:i:e:m:dh --long profile:,hardware-profile:,host-ip:,external-ip:,mode:,llm-device-id:,vlm-device-id:,use-remote-llm,use-remote-vlm,llm:,vlm:,llm-model-type:,vlm-model-type:,llm-env-file:,vlm-env-file:,dry-run,help -- "${_args[@]}")
  eval set -- "${_valid_args}"

  # Parse options
  while true; do
    case "${1}" in
      -p | --profile)
        shift
        profile="${1}"
        options_provided+=("profile")
        shift
        ;;
      -H | --hardware-profile)
        shift
        hardware_profile="${1}"
        options_provided+=("hardware-profile")
        shift
        ;;
      -i | --host-ip)
        shift
        host_ip="${1}"
        options_provided+=("host-ip")
        shift
        ;;
      -e | --external-ip)
        shift
        external_ip="${1}"
        options_provided+=("external-ip")
        shift
        ;;
      -m | --mode)
        shift
        mode="${1}"
        options_provided+=("mode")
        shift
        ;;
      --llm-device-id)
        shift
        llm_device_id="${1}"
        options_provided+=("llm-device-id")
        shift
        ;;
      --vlm-device-id)
        shift
        vlm_device_id="${1}"
        options_provided+=("vlm-device-id")
        shift
        ;;
      --use-remote-llm)
        llm_base_url="${LLM_ENDPOINT_URL:-}"
        options_provided+=("use-remote-llm")
        shift
        ;;
      --use-remote-vlm)
        vlm_base_url="${VLM_ENDPOINT_URL:-}"
        options_provided+=("use-remote-vlm")
        shift
        ;;
      --llm)
        shift
        llm="${1}"
        options_provided+=("llm")
        shift
        ;;
      --vlm)
        shift
        vlm="${1}"
        options_provided+=("vlm")
        shift
        ;;
      --llm-model-type)
        shift
        llm_model_type="${1}"
        options_provided+=("llm-model-type")
        shift
        ;;
      --vlm-model-type)
        shift
        vlm_model_type="${1}"
        options_provided+=("vlm-model-type")
        shift
        ;;
      --llm-env-file)
        shift
        llm_env_file="${1}"
        options_provided+=("llm-env-file")
        shift
        ;;
      --vlm-env-file)
        shift
        vlm_env_file="${1}"
        options_provided+=("vlm-env-file")
        shift
        ;;
      -d | --dry-run)
        dry_run="true"
        options_provided+=("dry-run")
        shift
        ;;
      -h | --help)
        shift
        ;;
      --)
        shift
        break
        ;;
    esac
  done

  # Get positional argument
  desired_state="${1}"

  # Validation based on desired-state
  if [[ "${desired_state}" == "down" ]]; then
    # Only dry-run option is allowed for 'down'
    for _opt in "${options_provided[@]}"; do
      if [[ "${_opt}" != "dry-run" ]]; then
        echo "[ERROR] Only --dry-run option is allowed for desired-state 'down'"
        echo "[ERROR] Invalid option provided: ${_opt}"
        ((_all_good++))
        break
      fi
    done
  elif [[ "${desired_state}" == "up" ]]; then
    # Validate required options for 'up'
    if ! contains_element "profile" "${options_provided[@]}"; then
      echo "[ERROR] --profile is required for desired-state 'up'"
      ((_all_good++))
    fi
    if [[ -z "${ngc_cli_api_key}" ]]; then
      echo "[ERROR] NGC_CLI_API_KEY is required for desired-state 'up'"
      ((_all_good++))
    fi

    # Validate profile value
    _valid_profiles=('base' 'lvs' 'search' 'alerts')
    if [[ -n "${profile}" ]]; then
      if ! contains_element "${profile}" "${_valid_profiles[@]}"; then
        echo "[ERROR] Invalid profile: ${profile}. Must be one of: base, lvs, search, alerts"
        ((_all_good++))
      fi
    fi

    # Fail fast: profile .env must exist for 'up'
    if [[ -n "${profile}" ]] && contains_element "${profile}" "${_valid_profiles[@]}"; then
      local _profile_env_check="${deployment_directory}/developer-profiles/dev-profile-${profile}/.env"
      if [[ ! -f "${_profile_env_check}" ]]; then
        echo "[ERROR] Profile .env file not found: ${_profile_env_check}"
        ((_all_good++))
      fi
    fi

    # Only run profile-based lookups and subsequent validation when profile is valid and .env exists.
    # This avoids cascading errors (e.g. invalid hardware-profile, invalid LLM/VLM configuration) when profile validation already failed.
    if [[ -n "${profile}" ]] && contains_element "${profile}" "${_valid_profiles[@]}" && [[ -f "${deployment_directory}/developer-profiles/dev-profile-${profile}/.env" ]]; then

      # Populate from profile .env when not provided by user (only after .env existence is verified)
      local _profile_env="${deployment_directory}/developer-profiles/dev-profile-${profile}/.env"
      if ! contains_element "hardware-profile" "${options_provided[@]}"; then
        hardware_profile="$(get_env_value "${_profile_env}" "HARDWARE_PROFILE")"
      fi
      if ! contains_element "llm-device-id" "${options_provided[@]}"; then
        llm_device_id="$(get_env_value "${_profile_env}" "LLM_DEVICE_ID")"
      fi
      if ! contains_element "vlm-device-id" "${options_provided[@]}"; then
        vlm_device_id="$(get_env_value "${_profile_env}" "VLM_DEVICE_ID")"
      fi
      local _fixed_shared_raw _fixed_shared_norm _reserved_raw _reserved_norm
      _fixed_shared_raw="$(get_env_value "${_profile_env}" "FIXED_SHARED_DEVICE_IDS")"
      _fixed_shared_raw="${_fixed_shared_raw// /}"
      _fixed_shared_norm=",${_fixed_shared_raw},"
      _reserved_raw="$(get_env_value "${_profile_env}" "RESERVED_DEVICE_IDS")"
      _reserved_raw="${_reserved_raw// /}"
      _reserved_norm=",${_reserved_raw},"
      if ! contains_element "llm-model-type" "${options_provided[@]}"; then
        llm_model_type="$(get_env_value "${_profile_env}" "LLM_MODEL_TYPE")"
      fi
      if ! contains_element "vlm-model-type" "${options_provided[@]}"; then
        vlm_model_type="$(get_env_value "${_profile_env}" "VLM_MODEL_TYPE")"
      fi

      # Validate hardware profile value (from profile .env or --hardware-profile)
      _valid_hardware_profiles=('H100' 'L40S' 'RTXPRO6000BW' 'DGX-SPARK' 'IGX-THOR' 'AGX-THOR' 'OTHER')
      if ! contains_element "${hardware_profile}" "${_valid_hardware_profiles[@]}"; then
        echo "[ERROR] Invalid hardware-profile: ${hardware_profile}. Must be one of: H100, L40S, RTXPRO6000BW, DGX-SPARK, IGX-THOR, AGX-THOR, OTHER"
        ((_all_good++))
      fi

      # Fail fast: requested hardware_profile must match detected GPU (from nvidia-smi display name).
      # Both sides use canonical types (AGX-THOR and IGX-THOR map to THOR for comparison).
      # Set SKIP_HARDWARE_CHECK=true to skip (e.g. in CI/tests without matching GPU).
      if [[ -n "${hardware_profile}" ]] && [[ "${SKIP_HARDWARE_CHECK,,}" != "true" ]]; then
        local _gpu_name _detected_canonical
        _gpu_name="$(get_nvidia_smi_gpu_name)"
        if [[ -z "${_gpu_name}" ]]; then
          echo "[ERROR] Hardware profile '${hardware_profile}' does not match detected hardware (no NVIDIA GPU detected)."
          ((_all_good++))
        elif _detected_canonical="$(get_detected_hardware_profile "${_gpu_name}")" && [[ "$(get_canonical_hardware_profile "${hardware_profile}")" != "${_detected_canonical}" ]]; then
          echo "[ERROR] Hardware profile '${hardware_profile}' does not match detected hardware '$(get_canonical_display_name "${_detected_canonical}")'."
          ((_all_good++))
        fi
      fi

      # DGX-SPARK, IGX-THOR, AGX-THOR (edge_hardware_profiles): only valid for base and alerts; device ID options not accepted
      if contains_element "${hardware_profile}" "${edge_hardware_profiles[@]}"; then
        if [[ "${profile}" != "base" ]] && [[ "${profile}" != "alerts" ]]; then
          echo "[ERROR] Hardware profile '${hardware_profile}' is only valid for profile base or alerts, not '${profile}'"
          ((_all_good++))
        fi
        if contains_element "llm-device-id" "${options_provided[@]}"; then
          echo "[ERROR] --llm-device-id is not accepted for hardware profile '${hardware_profile}'"
          ((_all_good++))
        fi
        if contains_element "vlm-device-id" "${options_provided[@]}"; then
          echo "[ERROR] --vlm-device-id is not accepted for hardware profile '${hardware_profile}'"
          ((_all_good++))
        fi
        llm_device_id="0"
        vlm_device_id="0"
      fi

      # Alerts or base profile on IGX-THOR or AGX-THOR: VLM options are not accepted (VLM is fixed for this configuration).
      # Note: --vlm-device-id is already rejected for all IGX-THOR/AGX-THOR/DGX-SPARK in the edge_hardware_profiles block above.
      if ([[ "${hardware_profile}" == "IGX-THOR" ]] || [[ "${hardware_profile}" == "AGX-THOR" ]]) && ([[ "${profile}" == "alerts" ]] || [[ "${profile}" == "base" ]]); then
        if contains_element "use-remote-vlm" "${options_provided[@]}"; then
          echo "[ERROR] --use-remote-vlm is not accepted for ${profile} profile with hardware profile ${hardware_profile}"
          ((_all_good++))
        fi
        if contains_element "vlm" "${options_provided[@]}"; then
          echo "[ERROR] --vlm is not accepted for ${profile} profile with hardware profile ${hardware_profile}"
          ((_all_good++))
        fi
        if contains_element "vlm-model-type" "${options_provided[@]}"; then
          echo "[ERROR] --vlm-model-type is not accepted for ${profile} profile with hardware profile ${hardware_profile}"
          ((_all_good++))
        fi
        if contains_element "vlm-env-file" "${options_provided[@]}"; then
          echo "[ERROR] --vlm-env-file is not accepted for ${profile} profile with hardware profile ${hardware_profile}"
          ((_all_good++))
        fi
      fi

      # Remote predicates (must match llm_mode/vlm_mode == remote) for same-GPU local_shared checks; computed before modes so LLM and VLM branches stay symmetric.
      local _llm_is_remote _vlm_is_remote
      _llm_is_remote=0
      _vlm_is_remote=0
      if contains_element "use-remote-llm" "${options_provided[@]}" && [[ -n "${llm_base_url}" ]]; then
        _llm_is_remote=1
      fi
      if contains_element "use-remote-vlm" "${options_provided[@]}" && [[ -n "${vlm_base_url}" ]]; then
        _vlm_is_remote=1
      fi

      # Derive LLM mode: remote only when --use-remote-llm is passed and LLM_ENDPOINT_URL is set (non-empty llm_base_url); else local_shared if device ID is in RESERVED_DEVICE_IDS, FIXED_SHARED_DEVICE_IDS, or (VLM not remote and equals VLM_DEVICE_ID), else local. Do not use vlm_device_id when VLM is remote.
      if [[ "${_llm_is_remote}" -eq 1 ]]; then
        llm_mode="remote"
      else
        if [[ -n "${llm_device_id}" ]]; then
          if [[ "${_reserved_norm}" == *",${llm_device_id},"* ]] || [[ "${_fixed_shared_norm}" == *",${llm_device_id},"* ]]; then
            llm_mode="local_shared"
          elif [[ "${_vlm_is_remote}" -eq 0 ]] && [[ "${llm_device_id}" == "${vlm_device_id}" ]]; then
            llm_mode="local_shared"
          else
            llm_mode="local"
          fi
        else
          llm_mode="local"
        fi
      fi
      # Derive VLM mode: remote only when --use-remote-vlm is passed and VLM_ENDPOINT_URL is set (non-empty vlm_base_url); else local_shared if device ID is in RESERVED_DEVICE_IDS, FIXED_SHARED_DEVICE_IDS, or (LLM not remote and equals LLM_DEVICE_ID), else local. Do not use llm_device_id when LLM is remote.
      if [[ "${_vlm_is_remote}" -eq 1 ]]; then
        vlm_mode="remote"
      else
        if [[ -n "${vlm_device_id}" ]]; then
          if [[ "${_reserved_norm}" == *",${vlm_device_id},"* ]] || [[ "${_fixed_shared_norm}" == *",${vlm_device_id},"* ]]; then
            vlm_mode="local_shared"
          elif [[ "${_llm_is_remote}" -eq 0 ]] && [[ "${vlm_device_id}" == "${llm_device_id}" ]]; then
            vlm_mode="local_shared"
          else
            vlm_mode="local"
          fi
        else
          vlm_mode="local"
        fi
      fi

      # --use-remote-* without a host URL is invalid (remote mode requires both the flag and the endpoint env var).
      if contains_element "use-remote-llm" "${options_provided[@]}" && [[ -z "${llm_base_url}" ]]; then
        echo "[ERROR] LLM_ENDPOINT_URL must be set when --use-remote-llm is passed"
        ((_all_good++))
      fi
      if contains_element "use-remote-vlm" "${options_provided[@]}" && [[ -z "${vlm_base_url}" ]]; then
        echo "[ERROR] VLM_ENDPOINT_URL must be set when --use-remote-vlm is passed"
        ((_all_good++))
      fi

      # When VLM is not remote, use host env VLM_CUSTOM_WEIGHTS if set; when remote, ignore it (do not set in generated.env).
      if [[ "${vlm_mode}" != "remote" ]]; then
        vlm_custom_weights="${VLM_CUSTOM_WEIGHTS:-}"
      else
        vlm_custom_weights=""
      fi

      # Validate mode based on profile
      if [[ "${profile}" == "alerts" ]]; then
        if ! contains_element "mode" "${options_provided[@]}" || [[ -z "${mode}" ]]; then
          echo "[ERROR] For alerts profile, --mode is required. Must be one of: verification, real-time"
          ((_all_good++))
        else
          _valid_modes=('verification' 'real-time')
          if ! contains_element "${mode}" "${_valid_modes[@]}"; then
            echo "[ERROR] Invalid mode: ${mode}. For alerts profile, must be one of: verification, real-time"
            ((_all_good++))
          else
            mode_env="$(get_mode_env_value "${mode}")"
          fi
        fi
      else
        # For non-alert profiles, mode option is not allowed
        if contains_element "mode" "${options_provided[@]}"; then
          echo "[ERROR] --mode is only accepted when profile is 'alerts'"
          ((_all_good++))
        fi
      fi

      # Validate LLM and VLM mode values (from profile)
      _valid_mode_values=('local_shared' 'local' 'remote')
      if ! contains_element "${llm_mode}" "${_valid_mode_values[@]}"; then
        echo "[ERROR] Invalid LLM configuration: ${llm_mode}. Must be one of: local_shared, local, remote"
        ((_all_good++))
      fi
      if ! contains_element "${vlm_mode}" "${_valid_mode_values[@]}"; then
        echo "[ERROR] Invalid VLM configuration: ${vlm_mode}. Must be one of: local_shared, local, remote"
        ((_all_good++))
      fi

      # L40S: neither LLM nor VLM may use local_shared (device ID cannot be shared with other services)
      if [[ "${hardware_profile}" == "L40S" ]]; then
        if [[ "${llm_mode}" == "local_shared" ]]; then
          echo "[ERROR] On L40S, the device ID for the LLM cannot be shared with other services"
          ((_all_good++))
        fi
        if [[ "${vlm_mode}" == "local_shared" ]]; then
          echo "[ERROR] On L40S, the device ID for the VLM cannot be shared with other services"
          ((_all_good++))
        fi
      fi

      # Device IDs must not be in profile RESERVED_DEVICE_IDS (comma-separated list; may be empty).
      # Exception: DGX-SPARK, IGX-THOR, AGX-THOR are exempt (device ID options not accepted).
      if ! contains_element "${hardware_profile}" "${edge_hardware_profiles[@]}"; then
        if [[ -n "${profile}" ]] && [[ -f "${deployment_directory}/developer-profiles/dev-profile-${profile}/.env" ]]; then
          local _profile_env_reserved="${deployment_directory}/developer-profiles/dev-profile-${profile}/.env"
          local _reserved_raw
          _reserved_raw="$(get_env_value "${_profile_env_reserved}" "RESERVED_DEVICE_IDS")"
          _reserved_raw="${_reserved_raw// /}"  # normalize: remove spaces so "0, 1" matches id "0" and "1"
          local _reserved_norm=",${_reserved_raw},"
          if [[ "${llm_mode}" != "remote" ]] && [[ -n "${llm_device_id}" ]]; then
            if [[ "${_reserved_norm}" == *",${llm_device_id},"* ]]; then
              echo "[ERROR] Device ID ${llm_device_id} is reserved and cannot be assigned to LLM or VLM for this profile"
              ((_all_good++))
            fi
          fi
          if [[ "${vlm_mode}" != "remote" ]] && [[ -n "${vlm_device_id}" ]]; then
            if [[ "${_reserved_norm}" == *",${vlm_device_id},"* ]]; then
              echo "[ERROR] Device ID ${vlm_device_id} is reserved and cannot be assigned to LLM or VLM for this profile"
              ((_all_good++))
            fi
          fi
        fi
      fi

      # Resolve and validate optional env file paths (must exist; stored as absolute)
      if [[ -n "${llm_env_file}" ]]; then
        llm_env_file="$(resolve_abs_path "${llm_env_file}")"
        if [[ ! -f "${llm_env_file}" ]]; then
          echo "[ERROR] LLM env file not found: ${llm_env_file}"
          ((_all_good++))
        fi
      fi
      if [[ -n "${vlm_env_file}" ]]; then
        vlm_env_file="$(resolve_abs_path "${vlm_env_file}")"
        if [[ ! -f "${vlm_env_file}" ]]; then
          echo "[ERROR] VLM env file not found: ${vlm_env_file}"
          ((_all_good++))
        fi
      fi

      # ===== LLM Validations =====
    
      # Validate LLM options when --use-remote-llm is passed
      if [[ "${llm_mode}" == "remote" ]]; then
        if contains_element "llm-device-id" "${options_provided[@]}"; then
          echo "[ERROR] --llm-device-id is not allowed when --use-remote-llm is passed"
          ((_all_good++))
        fi
        if contains_element "llm-env-file" "${options_provided[@]}"; then
          echo "[ERROR] --llm-env-file is not allowed when --use-remote-llm is passed"
          ((_all_good++))
        fi
        if [[ -z "${llm_base_url}" ]]; then
          echo "[ERROR] LLM_ENDPOINT_URL must be set when --use-remote-llm is passed"
          ((_all_good++))
        fi
        # When --use-remote-llm is passed, validate llm-model-type value if provided
        if contains_element "llm-model-type" "${options_provided[@]}" && [[ -n "${llm_model_type}" ]]; then
          _valid_llm_types=('nim' 'openai')
          if ! contains_element "${llm_model_type}" "${_valid_llm_types[@]}"; then
            echo "[ERROR] Invalid llm-model-type: ${llm_model_type}. Must be one of: nim, openai"
            ((_all_good++))
          fi
        fi
      else
        # Validate LLM model name if provided (only for non-remote modes; known names map to a slug)
        if contains_element "llm" "${options_provided[@]}"; then
          if [[ -z "$(get_llm_slug "${llm}")" ]]; then
            echo "[ERROR] Invalid LLM model name: ${llm}. Must be one of: nvidia/nvidia-nemotron-nano-9b-v2, nvidia/NVIDIA-Nemotron-Nano-9B-v2-FP8, nvidia/nemotron-3-nano, nvidia/llama-3.3-nemotron-super-49b-v1.5, openai/gpt-oss-20b"
            ((_all_good++))
          fi
        fi
        if contains_element "llm-model-type" "${options_provided[@]}"; then
          echo "[ERROR] --llm-model-type is only allowed when --use-remote-llm is passed"
          ((_all_good++))
        fi
      fi

      # ===== VLM Validations =====

      # Validate VLM options when --use-remote-vlm is passed
      if [[ "${vlm_mode}" == "remote" ]]; then
        if contains_element "vlm-device-id" "${options_provided[@]}"; then
          echo "[ERROR] --vlm-device-id is not allowed when --use-remote-vlm is passed"
          ((_all_good++))
        fi
        if contains_element "vlm-env-file" "${options_provided[@]}"; then
          echo "[ERROR] --vlm-env-file is not allowed when --use-remote-vlm is passed"
          ((_all_good++))
        fi
        if [[ -z "${vlm_base_url}" ]]; then
          echo "[ERROR] VLM_ENDPOINT_URL must be set when --use-remote-vlm is passed"
          ((_all_good++))
        fi
        # When --use-remote-vlm is passed, validate vlm-model-type value if provided
        if contains_element "vlm-model-type" "${options_provided[@]}" && [[ -n "${vlm_model_type}" ]]; then
          _valid_vlm_types=('nim' 'openai')
          if ! contains_element "${vlm_model_type}" "${_valid_vlm_types[@]}"; then
            echo "[ERROR] Invalid vlm-model-type: ${vlm_model_type}. Must be one of: nim, openai"
            ((_all_good++))
          fi
        fi
      else
        if contains_element "vlm-model-type" "${options_provided[@]}"; then
          echo "[ERROR] --vlm-model-type is only allowed when --use-remote-vlm is passed"
          ((_all_good++))
        fi
        if contains_element "vlm" "${options_provided[@]}"; then
          if [[ -z "$(get_vlm_slug "${vlm}")" ]]; then
            echo "[ERROR] Invalid VLM model name: ${vlm}. Must be one of: nvidia/cosmos-reason1-7b, nvidia/cosmos-reason2-8b, Qwen/Qwen3-VL-8B-Instruct"
            ((_all_good++))
          fi
        fi
      fi

      # Fail fast: VLM_CUSTOM_WEIGHTS must be an absolute path and the directory must exist (even in dry-run)
      if [[ "${vlm_mode}" != "remote" ]]; then
        if [[ -n "${vlm_custom_weights}" ]]; then
          if [[ "${vlm_custom_weights}" != /* ]]; then
            echo "[ERROR] VLM_CUSTOM_WEIGHTS must be an absolute path: ${vlm_custom_weights}"
            ((_all_good++))
          elif [[ ! -d "${vlm_custom_weights}" ]]; then
            echo "[ERROR] Specified VLM custom weights path does not exist: ${vlm_custom_weights}"
            ((_all_good++))
          fi
        fi
      fi

    fi
    # end: only run profile-based lookups when profile is valid and .env exists

  fi

  if [[ _all_good -gt 0 ]]; then
    echo ""
    usage
    exit 1
  fi
}

function print_args() {
  echo "=== Captured Arguments ==="
  echo "desired-state:             ${desired_state}"
  echo "deployment-directory:      ${deployment_directory}"
  echo "data-directory:            ${data_directory}"
  echo "dry-run:                   ${dry_run}"
  if [[ "${desired_state}" == "up" ]]; then
    echo "profile:                   ${profile}"
    echo "host-ip:                   ${host_ip}"
    if [[ -n "${external_ip}" ]]; then
      echo "external-ip:               ${external_ip}"
    fi
    echo "ngc-cli-api-key:           $(mask_secret "${ngc_cli_api_key}")"
    local _env_file="${deployment_directory}/developer-profiles/dev-profile-${profile}/.env"
    local _llm_mode="${llm_mode:-$(get_env_value "${_env_file}" "LLM_MODE")}"
    local _vlm_mode="${vlm_mode:-$(get_env_value "${_env_file}" "VLM_MODE")}"

    echo "hardware-profile:          ${hardware_profile:-$(get_env_value "${_env_file}" "HARDWARE_PROFILE")}"
    if [[ "${profile}" == "alerts" ]]; then
      echo "mode:                      ${mode:-$(get_mode_display_value "$(get_env_value "${_env_file}" "MODE")")}"
    fi

    echo "llm-mode:                  ${_llm_mode}"
    local _llm_model
    if [[ "${_llm_mode}" == "remote" ]] && [[ -n "${llm_base_url}" ]]; then
      if [[ -n "${llm}" ]]; then
        _llm_model="${llm}"
      else
        _llm_model="$(get_remote_model_name "${llm_base_url}")"
      fi
    else
      _llm_model="${llm:-$(get_env_value "${_env_file}" "LLM_NAME")}"
    fi
    echo "llm:                       ${_llm_model}"
    if [[ "${_llm_mode}" != "remote" ]]; then
      local _llm_device_id="${llm_device_id:-$(get_env_value "${_env_file}" "LLM_DEVICE_ID")}"
      echo "llm-device-id:             ${_llm_device_id}"
    fi
    if [[ "${_llm_mode}" == "remote" ]]; then
      local _llm_base_url="${llm_base_url:-$(get_env_value "${_env_file}" "LLM_BASE_URL")}"
      echo "llm-base-url:              ${_llm_base_url}"
      local _llm_model_type="${llm_model_type:-$(get_env_value "${_env_file}" "LLM_MODEL_TYPE")}"
      if [[ -n "${_llm_model_type}" ]]; then
        echo "llm-model-type:            ${_llm_model_type}"
      fi
    fi
    if [[ -n "${llm_env_file}" ]]; then
      echo "llm-env-file:              ${llm_env_file}"
    fi

    echo "vlm-mode:                  ${_vlm_mode}"
    local _vlm_model
    if [[ "${_vlm_mode}" == "remote" ]] && [[ -n "${vlm_base_url}" ]]; then
      if [[ -n "${vlm}" ]]; then
        _vlm_model="${vlm}"
      else
        _vlm_model="$(get_remote_model_name "${vlm_base_url}")"
      fi
    else
      _vlm_model="${vlm:-$(get_env_value "${_env_file}" "VLM_NAME")}"
    fi
    echo "vlm:                       ${_vlm_model}"
    if [[ "${_vlm_mode}" != "remote" ]]; then
      local _vlm_device_id="${vlm_device_id:-$(get_env_value "${_env_file}" "VLM_DEVICE_ID")}"
      echo "vlm-device-id:             ${_vlm_device_id}"
    fi
    if [[ "${_vlm_mode}" == "remote" ]]; then
      local _vlm_base_url="${vlm_base_url:-$(get_env_value "${_env_file}" "VLM_BASE_URL")}"
      echo "vlm-base-url:              ${_vlm_base_url}"
      local _vlm_model_type="${vlm_model_type:-$(get_env_value "${_env_file}" "VLM_MODEL_TYPE")}"
      if [[ -n "${_vlm_model_type}" ]]; then
        echo "vlm-model-type:            ${_vlm_model_type}"
      fi
    fi
    if [[ -n "${vlm_custom_weights}" ]]; then
      echo "vlm-custom-weights:        ${vlm_custom_weights}"
    fi
    if [[ -n "${vlm_env_file}" ]]; then
      echo "vlm-env-file:              ${vlm_env_file}"
    fi
  fi
  if [[ -n "${nvidia_api_key}" ]]; then
    echo "nvidia-api-key:            $(mask_secret "${nvidia_api_key}")"
  fi
  if [[ -n "${openai_api_key}" ]]; then
    echo "openai-api-key:            $(mask_secret "${openai_api_key}")"
  fi
  echo "=========================="
}

function state_up() {
  local _profile_dir _source_env _generated_env
  _profile_dir="${deployment_directory}/developer-profiles/dev-profile-${profile}"
  _source_env="${_profile_dir}/.env"
  _generated_env="${_profile_dir}/generated.env"

  echo "[INFO] Generating environment file for profile '${profile}'..."

  # Check if source .env exists
  if [[ ! -f "${_source_env}" ]]; then
    echo "[ERROR] Source .env file not found: ${_source_env}"
    exit 1
  fi

  # Copy source .env to generated.env
  cp "${_source_env}" "${_generated_env}"
  echo "[INFO] Copied ${_source_env} to ${_generated_env}"

  # Append compose-wide defaults for variables not already defined in the profile
  local _compose_defaults="${deployment_directory}/services/vios/compose-defaults.env"
  if [[ -f "${_compose_defaults}" ]]; then
    while IFS= read -r line || [[ -n "${line}" ]]; do
      [[ "${line}" =~ ^[[:space:]]*# ]] && continue
      [[ -z "${line// }" ]] && continue
      local _var_name="${line%%=*}"
      if ! grep -q "^${_var_name}=" "${_generated_env}"; then
        echo "${line}" >> "${_generated_env}"
      fi
    done < "${_compose_defaults}"
  fi

  # Function to set or update a variable in the generated.env
  # Usage: set_env_var <var_name> <var_value> [mask]
  # If mask is "true", the value will be masked in the output
  # This function will uncomment and update commented variables (e.g., #VAR=value)
  set_env_var() {
    local var_name="${1}"
    local var_value="${2}"
    local mask="${3:-false}"
    local display_value="${var_value}"
    if [[ "${mask}" == "true" ]]; then
      display_value="$(mask_secret "${var_value}")"
    fi
    if grep -q "^${var_name}=" "${_generated_env}"; then
      # Variable exists (uncommented), update it
      sed -i "s|^${var_name}=.*|${var_name}=${var_value}|" "${_generated_env}"
    elif grep -Eq "^#[[:space:]]*${var_name}=" "${_generated_env}"; then
      # Variable exists but is commented (with optional whitespace), uncomment and update it
      sed -i -E "s|^#[[:space:]]*${var_name}=.*|${var_name}=${var_value}|" "${_generated_env}"
    else
      # Variable doesn't exist, append it
      echo "${var_name}=${var_value}" >> "${_generated_env}"
    fi
    echo "[INFO] Set ${var_name}=${display_value}"
  }

  # Set the required environment variables
  set_env_var "MDX_SAMPLE_APPS_DIR" "${deployment_directory}"
  set_env_var "MDX_DATA_DIR" "${data_directory}"
  set_env_var "HOST_IP" "${host_ip}"
  if [[ -n "${external_ip}" ]]; then
    set_env_var "EXTERNAL_IP" "${external_ip}"
  fi

  # ===== Brev secure links =====
  # Brev launchables use a hostname of the form <port>0-<env>.brevlab.com (e.g. 77770-<id>.brevlab.com).
  # Point HAProxy and browser-facing compose vars at that host with https/wss; keep URL templates in
  # profile .env (${VSS_PUBLIC_HTTP_PROTOCOL}://${VSS_PUBLIC_HOST}:${VSS_PUBLIC_PORT}, etc.) so one origin is used.
  if [[ -n "${BREV_ENV_ID:-}" ]]; then
    local _proxy_port="${PROXY_PORT:-7777}"
    echo "[INFO] Brev environment detected (${BREV_ENV_ID}). Setting HAProxy ingress to secure-link host (port ${_proxy_port}, prefix ${_proxy_port}0)..."
    set_env_var "HAPROXY_PORT" '${PROXY_PORT:-7777}'
    set_env_var "VSS_PUBLIC_HTTP_PROTOCOL" "https"
    set_env_var "VSS_PUBLIC_WS_PROTOCOL" "wss"
    set_env_var "VSS_PUBLIC_HOST" '${PROXY_PORT:-7777}0-${BREV_ENV_ID}.brevlab.com'
    set_env_var "VSS_PUBLIC_PORT" "443"
  fi

  set_env_var "NGC_CLI_API_KEY" "${ngc_cli_api_key}" "true"
  set_env_var "HARDWARE_PROFILE" "${hardware_profile}"
  if [[ -n "${mode_env}" ]]; then
    set_env_var "MODE" "${mode_env}"
  fi

  # ===== LLM Configuration =====
  # Derived LLM_MODE written to generated.env (remote when --use-remote-llm and LLM_ENDPOINT_URL; else local_shared or local from device IDs and FIXED_SHARED_DEVICE_IDS)
  set_env_var "LLM_MODE" "${llm_mode}"
  if [[ "${llm_mode}" == "remote" ]] && [[ -n "${llm_base_url}" ]]; then
    local _llm_name
    if [[ -n "${llm}" ]]; then
      _llm_name="${llm}"
    else
      _llm_name="$(get_remote_model_name "${llm_base_url}")"
      if [[ -z "${_llm_name}" ]]; then
        echo "[ERROR] Could not get LLM model name from ${llm_base_url}/v1/models. Pass --llm <model-name> to override."
        exit 1
      fi
    fi
    set_env_var "LLM_NAME" "${_llm_name}"
    set_env_var "LLM_NAME_SLUG" "none"
  elif [[ -n "${llm}" ]]; then
    set_env_var "LLM_NAME" "${llm}"
    set_env_var "LLM_NAME_SLUG" "$(get_llm_slug "${llm}")"
  fi
  if contains_element "${hardware_profile}" "${edge_hardware_profiles[@]}"; then
    set_env_var "LLM_DEVICE_ID" "0"
    set_env_var "VLM_DEVICE_ID" "0"
  else
    if [[ "${llm_mode}" != "remote" ]] && [[ -n "${llm_device_id}" ]]; then
      set_env_var "LLM_DEVICE_ID" "${llm_device_id}"
    fi
    if [[ "${vlm_mode}" != "remote" ]] && [[ -n "${vlm_device_id}" ]]; then
      set_env_var "VLM_DEVICE_ID" "${vlm_device_id}"
    fi
  fi
  if [[ -n "${llm_base_url}" ]]; then
    set_env_var "LLM_BASE_URL" "${llm_base_url}"
  fi
  if [[ "${llm_mode}" == "remote" ]]; then
    local _llm_type="${llm_model_type:-$(get_env_value "${_source_env}" "LLM_MODEL_TYPE")}"
    if [[ -n "${_llm_type}" ]]; then
      set_env_var "LLM_MODEL_TYPE" "${_llm_type}"
    fi
  fi
  if [[ -n "${nvidia_api_key}" ]]; then
    set_env_var "NVIDIA_API_KEY" "${nvidia_api_key}" "true"
  fi
  if [[ -n "${llm_env_file}" ]]; then
    set_env_var "LLM_ENV_FILE" "${llm_env_file}"
  fi

  # ===== VLM Configuration =====
  # Derived VLM_MODE written to generated.env (remote when --use-remote-vlm and VLM_ENDPOINT_URL; else local_shared or local from device IDs and FIXED_SHARED_DEVICE_IDS)
  set_env_var "VLM_MODE" "${vlm_mode}"
  if [[ "${vlm_mode}" == "remote" ]] && [[ -n "${vlm_base_url}" ]]; then
    local _vlm_name
    if [[ -n "${vlm}" ]]; then
      _vlm_name="${vlm}"
    else
      _vlm_name="$(get_remote_model_name "${vlm_base_url}")"
      if [[ -z "${_vlm_name}" ]]; then
        echo "[ERROR] Could not get VLM model name from ${vlm_base_url}/v1/models. Pass --vlm <model-name> to override."
        exit 1
      fi
    fi
    set_env_var "VLM_NAME" "${_vlm_name}"
    set_env_var "VLM_NAME_SLUG" "none"
  elif [[ -n "${vlm}" ]]; then
    set_env_var "VLM_NAME" "${vlm}"
    set_env_var "VLM_NAME_SLUG" "$(get_vlm_slug "${vlm}")"
  fi
  if [[ "${vlm_mode}" == "remote" ]]; then
    set_env_var "VLM_NAME_SLUG" "none"
  fi
  if [[ -n "${vlm_base_url}" ]]; then
    set_env_var "VLM_BASE_URL" "${vlm_base_url}"
    set_env_var "RTVI_VLM_ENDPOINT" "${vlm_base_url}/v1"
    set_env_var "RTVI_VLM_MODEL_PATH" "none"
  fi
  if [[ "${vlm_mode}" == "remote" ]]; then
    local _vlm_type="${vlm_model_type:-$(get_env_value "${_source_env}" "VLM_MODEL_TYPE")}"
    if [[ -n "${_vlm_type}" ]]; then
      set_env_var "VLM_MODEL_TYPE" "${_vlm_type}"
    fi
  fi
  if [[ -n "${openai_api_key}" ]]; then
    set_env_var "OPENAI_API_KEY" "${openai_api_key}" "true"
  fi

  # For local_shared/local VLM modes with 2d_vlm, configure rtvi-vlm to use the shared NIM endpoint
  if [[ "${profile}" == "alerts" ]] && [[ "${mode_env}" == "2d_vlm" ]] && [[ "${vlm_mode}" != "remote" ]]; then
    local _vlm_port
    _vlm_port="$(get_env_value "${_source_env}" "VLM_PORT")"
    _vlm_port="${_vlm_port:-30082}"
    set_env_var "RTVI_VLM_MODEL_PATH" "none"
    set_env_var "RTVI_VLM_ENDPOINT" "http://\${HOST_IP}:${_vlm_port}/v1"
    echo "[INFO] Configured rtvi-vlm to use shared NIM endpoint on port ${_vlm_port}"
  fi

  # Handle custom weights for VLM
  # Skip if vlm_mode=remote (VLM hosted remotely)
  if [[ "${vlm_mode}" == "remote" ]]; then
    echo "[INFO] Skipping VLM custom weights - not required when VLM is remote"
  elif [[ -n "${vlm_custom_weights}" ]]; then
    echo "[INFO] Using VLM custom weights path: ${vlm_custom_weights}"
    set_env_var "VLM_CUSTOM_WEIGHTS" "${vlm_custom_weights}"
  fi
  if [[ -n "${vlm_env_file}" ]]; then
    set_env_var "VLM_ENV_FILE" "${vlm_env_file}"
  fi

  # Search profile: critic agent is enabled by default. Host ENABLE_CRITIC case-insensitive false → write ENABLE_CRITIC=false and force VLM_NAME_SLUG=none (skip local VLM).
  # Otherwise write ENABLE_CRITIC=true (VLM_NAME_SLUG is not overridden here; remote VLM block already sets it to none when --use-remote-vlm is passed).
  if [[ "${profile}" == "search" ]]; then
    if [[ "${ENABLE_CRITIC+set}" == "set" ]] && [[ "${ENABLE_CRITIC,,}" == "false" ]]; then
      set_env_var "ENABLE_CRITIC" "false"
      set_env_var "VLM_NAME_SLUG" "none"
    else
      set_env_var "ENABLE_CRITIC" "true"
    fi
  fi

  # Alerts profile: conditionally set perception prefix for edge (DGX-SPARK, IGX-THOR, AGX-THOR)
  if [[ "${profile}" == "alerts" ]] && contains_element "${hardware_profile}" "${edge_hardware_profiles[@]}"; then
    set_env_var "PERCEPTION_DOCKERFILE_PREFIX" "EDGE-"
  fi
  # Alerts profile: conditionally set vlm-as-verifier config prefix for IGX-THOR, AGX-THOR only; DGX-SPARK uses default config.yml
  if [[ "${profile}" == "alerts" ]] && ([[ "${hardware_profile}" == "IGX-THOR" ]] || [[ "${hardware_profile}" == "AGX-THOR" ]]) && [[ "${vlm_mode}" != "remote" ]]; then
    set_env_var "VLM_AS_VERIFIER_CONFIG_FILE_PREFIX" "EDGE-LOCAL-VLM-"
  fi

  # DGX-SPARK, IGX-THOR, AGX-THOR with alerts profile only: set RTVI VLM input dimensions and frame rate (not set on any other platform or profile)
  if [[ "${profile}" == "alerts" ]] && contains_element "${hardware_profile}" "${edge_hardware_profiles[@]}"; then
    set_env_var "RTVI_VLM_INPUT_WIDTH" "860"
    set_env_var "RTVI_VLM_INPUT_HEIGHT" "467"
    set_env_var "RTVI_VLM_DEFAULT_NUM_FRAMES_PER_SECOND_OR_FIXED_FRAMES_CHUNK" "20"
  fi

  # Alerts or base profile on IGX-THOR or AGX-THOR: set VLM name/slug, base URL, and RTVI-related env (fixed configuration)
  if ([[ "${hardware_profile}" == "IGX-THOR" ]] || [[ "${hardware_profile}" == "AGX-THOR" ]]) && ([[ "${profile}" == "alerts" ]] || [[ "${profile}" == "base" ]]); then
    set_env_var "VLM_NAME_SLUG" "none"
    set_env_var "VLM_NAME" "nim_nvidia_cosmos-reason2-8b_hf-1208"
    set_env_var "VLM_BASE_URL" "http://${host_ip}:8018"
    set_env_var "RTVI_VLM_MODEL_PATH" "ngc:nim/nvidia/cosmos-reason2-8b:hf-1208"
    set_env_var "RTVI_VLM_MODEL_TO_USE" "cosmos-reason2"
    set_env_var "RTVI_VLLM_GPU_MEMORY_UTILIZATION" "0.35"
  fi
  # Base profile only on IGX-THOR or AGX-THOR: set VLM_MODEL_TYPE to rtvi (alerts does not use rtvi)
  if ([[ "${hardware_profile}" == "IGX-THOR" ]] || [[ "${hardware_profile}" == "AGX-THOR" ]]) && [[ "${profile}" == "base" ]]; then
    set_env_var "VLM_MODEL_TYPE" "rtvi"
  fi

  # When hardware profile is DGX-SPARK: for any env var that has a commented line with sbsa in the value,
  # comment the uncommented line (non-sbsa) and uncomment the sbsa line. Discover keys from the file.
  # Comment format may be "# VAR=..." or "#VAR=..." (optional space after #).
  if [[ "${hardware_profile}" == "DGX-SPARK" ]]; then
    local _key
    while IFS= read -r _key; do
      [[ -z "${_key}" ]] && continue
      # Comment the uncommented line for this key when value does not contain sbsa
      sed -i -E "/sbsa/! s/^(${_key})=(.*)/# \1=\2/" "${_generated_env}"
      # Uncomment the commented line for this key when value contains sbsa
      sed -i -E "/sbsa/ s/^#[[:space:]]*(${_key})=(.*)/\1=\2/" "${_generated_env}"
      echo "[INFO] Swapped to SBSA (DGX-SPARK): ${_key}"
    done < <(grep -E '^#[[:space:]]*[A-Za-z0-9_]+=.*sbsa' "${_generated_env}" 2>/dev/null | sed -nE 's/^#[[:space:]]*([A-Za-z0-9_]+)=.*/\1/p' | sort -u)
  fi

  echo "[INFO] Generated environment file: ${_generated_env}"

  # ----- Optional knowledge retrieval fragment ---------------------------------
  # If config_rag.yml exists alongside the profile's config.yml, deep-merge it
  # in and point VSS_AGENT_CONFIG_FILE at the merged result. With the fragment
  # absent, this is a no-op and the profile loads exactly as today.
  local _agent_configs_dir _base_cfg _rag_cfg _merged_cfg
  _agent_configs_dir="${_profile_dir}/vss-agent/configs"
  _base_cfg="${_agent_configs_dir}/config.yml"
  _rag_cfg="${_agent_configs_dir}/config_rag.yml"
  _merged_cfg="${_agent_configs_dir}/config.merged.yml"
  if [[ -f "${_rag_cfg}" && -f "${_base_cfg}" ]]; then
    echo "[INFO] Knowledge retrieval fragment detected: ${_rag_cfg}"
    if ! command -v python3 >/dev/null 2>&1; then
      echo "[WARN] python3 not found; skipping config_rag.yml merge. Knowledge layer will not be wired."
    else
      python3 - "${_base_cfg}" "${_rag_cfg}" "${_merged_cfg}" <<'PY'
import sys
import yaml

base_path, frag_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
with open(base_path) as f:
    base = yaml.safe_load(f) or {}
with open(frag_path) as f:
    frag = yaml.safe_load(f) or {}

def merge(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            out[k] = merge(a[k], v) if k in a else v
        return out
    if isinstance(a, list) and isinstance(b, list):
        return a + [x for x in b if x not in a]
    return b

merged = merge(base, frag)

# workflow.prompt_extras: pop and append to workflow.prompt so the agent
# config schema (which has no prompt_extras field) stays unchanged.
wf = merged.get("workflow") or {}
extras = wf.pop("prompt_extras", None) if isinstance(wf, dict) else None
if extras:
    wf["prompt"] = (wf.get("prompt", "") or "") + "\n\n" + extras
    merged["workflow"] = wf

with open(out_path, "w") as f:
    yaml.safe_dump(merged, f, sort_keys=False)
print(f"[INFO] Wrote merged agent config: {out_path}")
PY
      # Point the agent at the merged file. The path inside the container is
      # the same as on the host (mounted at /vss-agent/deploy/docker:ro), but
      # the .env stores the path relative to ${MDX_SAMPLE_APPS_DIR}.
      local _merged_rel
      _merged_rel="./deploy/docker/developer-profiles/dev-profile-${profile}/vss-agent/configs/config.merged.yml"
      set_env_var "VSS_AGENT_CONFIG_FILE" "${_merged_rel}"
    fi
  fi

  # Create required directories
  echo "[INFO] Creating data directories..."
  mkdir -p "${data_directory}/data_log/analytics_cache"
  mkdir -p "${data_directory}/data_log/calibration_toolkit"
  mkdir -p "${data_directory}/data_log/elastic/data"
  mkdir -p "${data_directory}/data_log/elastic/logs"
  mkdir -p "${data_directory}/data_log/kafka"
  mkdir -p "${data_directory}/data_log/redis/data"
  mkdir -p "${data_directory}/data_log/redis/log"
  mkdir -p "${data_directory}/agent_eval/dataset/"
  mkdir -p "${data_directory}/agent_eval/results/"

  # Create alerts-specific directories and download models
  if [[ "${profile}" == "alerts" ]]; then
    echo "[INFO] Creating alerts-specific directories..."

    if [[ "${dry_run}" == "true" ]]; then
      echo "[DRY-RUN] mkdir -p ${data_directory}/data_log/vss_video_analytics_api"
      echo "[DRY-RUN] mkdir -p ${data_directory}/videos/dev-profile-alerts"
      echo "[DRY-RUN] mkdir -p ${deployment_directory}/engines/gdino"
      echo "[DRY-RUN] mkdir -p ${deployment_directory}/engines/rtdetr-its"
      echo "[DRY-RUN] chmod -R 777 ${deployment_directory}/engines"
    else
      mkdir -p "${data_directory}/data_log/vss_video_analytics_api"
      mkdir -p "${data_directory}/videos/dev-profile-alerts"
      mkdir -p "${deployment_directory}/engines/gdino"
      mkdir -p "${deployment_directory}/engines/rtdetr-its"
      chmod -R 777 "${deployment_directory}/engines"
    fi

    # Download alerts models from NGC
    echo "[INFO] Downloading alerts models from NGC..."

    if [[ "${dry_run}" == "true" ]]; then
      echo "[DRY-RUN] rm -rf ${data_directory}/models"
      echo "[DRY-RUN] mkdir -p ${data_directory}/models/rtdetr-its"
      echo "[DRY-RUN] mkdir -p ${data_directory}/models/gdino"
      echo "[DRY-RUN] NGC_CLI_API_KEY=<ngc-cli-api-key> ngc registry model download-version nvidia/tao/trafficcamnet_transformer_lite:deployable_resnet50_v2.0"
      echo "[DRY-RUN] mv trafficcamnet_transformer_lite_vdeployable_resnet50_v2.0/resnet50_trafficcamnet_rtdetr.fp16.onnx ${data_directory}/models/rtdetr-its/model_epoch_035.fp16.onnx"
      echo "[DRY-RUN] rm -rf trafficcamnet_transformer_lite_vdeployable_resnet50_v2.0"
      echo "[DRY-RUN] NGC_CLI_API_KEY=<ngc-cli-api-key> ngc registry model download-version nvidia/tao/mask_grounding_dino:mask_grounding_dino_swin_tiny_commercial_deployable_v2.1_wo_mask_arm"
      echo "[DRY-RUN] mv mask_grounding_dino_vmask_grounding_dino_swin_tiny_commercial_deployable_v2.1_wo_mask_arm/mgdino_mask_head_pruned_dynamic_batch.onnx ${data_directory}/models/gdino/mgdino_mask_head_pruned_dynamic_batch.onnx"
      echo "[DRY-RUN] rm -rf mask_grounding_dino_vmask_grounding_dino_swin_tiny_commercial_deployable_v2.1_wo_mask_arm"
      echo "[DRY-RUN] chmod -R 777 ${data_directory}/models"
    else
      rm -rf "${data_directory}/models"

      mkdir -p "${data_directory}/models/rtdetr-its"
      mkdir -p "${data_directory}/models/gdino"

      # Download and install trafficcamnet RT-DETR model
      NGC_CLI_API_KEY="${ngc_cli_api_key}" ngc \
        registry \
        model \
        download-version \
        nvidia/tao/trafficcamnet_transformer_lite:deployable_resnet50_v2.0

      mv trafficcamnet_transformer_lite_vdeployable_resnet50_v2.0/resnet50_trafficcamnet_rtdetr.fp16.onnx \
        "${data_directory}/models/rtdetr-its/model_epoch_035.fp16.onnx"

      rm -rf trafficcamnet_transformer_lite_vdeployable_resnet50_v2.0

      # Download and install grounding DINO model
      NGC_CLI_API_KEY="${ngc_cli_api_key}" ngc \
        registry \
        model \
        download-version \
        nvidia/tao/mask_grounding_dino:mask_grounding_dino_swin_tiny_commercial_deployable_v2.1_wo_mask_arm

      mv mask_grounding_dino_vmask_grounding_dino_swin_tiny_commercial_deployable_v2.1_wo_mask_arm/mgdino_mask_head_pruned_dynamic_batch.onnx \
        "${data_directory}/models/gdino/mgdino_mask_head_pruned_dynamic_batch.onnx"

      rm -rf mask_grounding_dino_vmask_grounding_dino_swin_tiny_commercial_deployable_v2.1_wo_mask_arm

      chmod -R 777 "${data_directory}/models"
      echo "[INFO] Alerts models downloaded and installed to ${data_directory}/models"
    fi
  fi

  if [[ "${profile}" == "search" ]]; then
    echo "[INFO] Creating search-specific directories..."

    if [[ "${dry_run}" == "true" ]]; then
      echo "[DRY-RUN] mkdir -p ${data_directory}/data_log/vss_video_analytics_api"
    else
      mkdir -p "${data_directory}/data_log/vss_video_analytics_api"
    fi

    # Download RT-DETR model from NGC (host-staged, bind-mounted into container).
    echo "[INFO] Downloading RT-DETR model from NGC..."

    if [[ "${dry_run}" == "true" ]]; then
      echo "[DRY-RUN] mkdir -p ${data_directory}/models"
      echo "[DRY-RUN] NGC_CLI_API_KEY=<ngc-cli-api-key> ngc registry model download-version nvidia/tao/rtdetr_2d_warehouse:deployable_efficientvit_l2_v1.0.1"
      echo "[DRY-RUN] mv rtdetr_2d_warehouse_vdeployable_efficientvit_l2_v1.0.1/rtdetr_warehouse_v1.0.1.fp16.onnx ${data_directory}/models/rtdetr_warehouse_v1.0.1.fp16.onnx"
      echo "[DRY-RUN] rm -rf rtdetr_2d_warehouse_vdeployable_efficientvit_l2_v1.0.1"
      echo "[DRY-RUN] chmod -R 777 ${data_directory}/models"
    else
      mkdir -p "${data_directory}/models"

      NGC_CLI_API_KEY="${ngc_cli_api_key}" ngc \
        registry \
        model \
        download-version \
        nvidia/tao/rtdetr_2d_warehouse:deployable_efficientvit_l2_v1.0.1

      mv rtdetr_2d_warehouse_vdeployable_efficientvit_l2_v1.0.1/rtdetr_warehouse_v1.0.1.fp16.onnx "${data_directory}/models/rtdetr_warehouse_v1.0.1.fp16.onnx"

      rm -rf rtdetr_2d_warehouse_vdeployable_efficientvit_l2_v1.0.1

      chmod -R 777 "${data_directory}/models"
      echo "[INFO] RT-DETR model downloaded and installed to ${data_directory}/models"
    fi
  fi

  # Set permissions on data_log directory
  echo "[INFO] Setting permissions on data_log directory..."
  chmod -R 777 "${data_directory}/data_log"

  # Set permissions on agent_eval directory
  echo "[INFO] Setting permissions on agent_eval directory..."
  chmod -R 777 "${data_directory}/agent_eval"

  # VSS kernel settings (non-dry-run only)
  if [[ "${dry_run}" != "true" ]]; then
    echo "[INFO] Applying VSS Linux kernel settings..."
    set_vss_linux_kernel_settings
  fi

  # Docker login to nvcr.io
  echo "[INFO] Logging into nvcr.io..."
  if [[ "${dry_run}" == "true" ]]; then
    echo "[DRY-RUN] docker login --username '\$oauthtoken' --password <ngc-cli-api-key> nvcr.io"
  else
    docker login \
      --username '$oauthtoken' \
      --password "${ngc_cli_api_key}" \
      nvcr.io
  fi

  # Docker compose up
  echo "[INFO] Starting docker compose..."
  if [[ "${dry_run}" == "true" ]]; then
    echo "[DRY-RUN] cd ${deployment_directory} && docker compose --env-file developer-profiles/dev-profile-${profile}/generated.env up --detach --force-recreate --build"
  else
    cd "${deployment_directory}" && docker compose \
      --env-file "developer-profiles/dev-profile-${profile}/generated.env" \
      up \
      --detach \
      --force-recreate \
      --build
  fi

  echo "[INFO] State up completed"
}

function state_down() {
  local _profile_dir_names _profile_dir_name _generated_env

  echo "[INFO] Cleaning up generated.env files from all profiles..."
  _profile_dir_names=('base' 'lvs' 'search' 'alerts')
  for _profile_dir_name in "${_profile_dir_names[@]}"; do
    _generated_env="${deployment_directory}/developer-profiles/dev-profile-${_profile_dir_name}/generated.env"
    if [[ -f "${_generated_env}" ]]; then
      if [[ "${dry_run}" == "true" ]]; then
        echo "[DRY-RUN] rm -f ${_generated_env}"
      else
        rm -f "${_generated_env}"
        echo "[INFO] Deleted ${_generated_env}"
      fi
    fi
  done

  echo "[INFO] Bringing down docker compose project 'mdx' (with volumes)..."
  if [[ "${dry_run}" == "true" ]]; then
    echo "[DRY-RUN] docker compose -p mdx down -v --remove-orphans"
  else
    docker compose -p mdx down -v --remove-orphans
  fi

  echo "[INFO] Removing dangling docker volumes..."
  if [[ "${dry_run}" == "true" ]]; then
    echo "[DRY-RUN] docker volume ls -q -f \"dangling=true\" | xargs docker volume rm"
  else
    dangling_volumes=$(docker volume ls -q -f "dangling=true")
    if [[ -n "${dangling_volumes}" ]]; then
      echo "${dangling_volumes}" | xargs docker volume rm
    else
      echo "[INFO] No dangling volumes to remove"
    fi
  fi

  echo "[INFO] Deleting data directory: ${data_directory}..."
  # Use sudo only when not already root (CI containers run as root without sudo installed).
  local _sudo=""
  if [[ "$(id -u)" -ne 0 ]] && command -v sudo >/dev/null 2>&1; then
    _sudo="sudo"
  fi
  if [[ "${dry_run}" == "true" ]]; then
    echo "[DRY-RUN] ${_sudo:+sudo }rm -rf ${data_directory}"
  else
    if [[ -d "${data_directory}" ]]; then
      $_sudo rm -rf "${data_directory}"
      echo "[INFO] Data directory deleted"
    else
      echo "[INFO] Data directory does not exist, skipping"
    fi
  fi

  echo "[INFO] State down completed"
}

# Main execution
validate_args "${@}"
process_args "${@}"
print_args

if [[ "${desired_state}" == "up" ]]; then
  state_down
  state_up
elif [[ "${desired_state}" == "down" ]]; then
  state_down
fi
