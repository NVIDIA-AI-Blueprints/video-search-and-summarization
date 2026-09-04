#!/bin/bash

# blueprint-deploy.sh - Deploy Warehouse blueprint
# Similar to dev-profile.sh but for warehouse deployment

script_dir="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
deploy_docker_dir="$( cd -- "${script_dir}/.." &> /dev/null && pwd )"

# Default values
desired_state=""
deployment=""
mode=""
bp_profile=""
compose_profiles_selector=""
compose_variant=""
sample_video_dataset=""
elasticsearch_mode="cpu"
deployment_directory="${deploy_docker_dir}"
data_directory="${deploy_docker_dir}/data-dir"
host_ip="$(ip route get 1.1.1.1 2>/dev/null | awk '/src/ {for (i=1;i<=NF;i++) if ($i=="src") print $(i+1)}' || echo "127.0.0.1")"
external_ip=""
ngc_cli_api_key="${NGC_CLI_API_KEY:-}"
# NVIDIA_API_KEY and OPENAI_API_KEY from environment (optional)
nvidia_api_key="${NVIDIA_API_KEY:-}"
openai_api_key="${OPENAI_API_KEY:-}"
dry_run="false"
# Before removing *.backup_* during data_log cleanup, restore originals from oldest backup (cleanup_all_datalog.sh parity)
revert_from_oldest_backup="true"

# LLM/VLM configuration (for warehouse bp_wh - NIM and agents)
llm=""
vlm=""
llm_device_id=""
vlm_device_id=""
llm_base_url=""
vlm_base_url=""
llm_model_type=""
vlm_model_type=""
llm_env_file=""
vlm_env_file=""
hardware_profile=""
use_sbsa_images="false"

# Flags to track explicitly provided options
options_provided=()

# Path under deploy/docker/ where a deployment's .env lives (-d warehouse → industry-profiles/warehouse-operations).
function deployment_rel_path() {
  case "${1}" in
    warehouse) echo "industry-profiles/warehouse-operations" ;;
    *) echo "${1}" ;;
  esac
}

# MODE × BP_PROFILE matrix. Auto-calibration is a single MODE, not a 2d/3d/mv3dt suffix.
function warehouse_bp_profile_valid_for_mode() {
  local _mode="${1}"
  local _profile="${2}"
  case "${_mode}" in
    2d)
      contains_element "${_profile}" "bp_wh" "bp_wh_kafka" "bp_wh_redis"
      ;;
    3d | mv3dt)
      contains_element "${_profile}" "bp_wh_kafka" "bp_wh_redis"
      ;;
    auto-calibration)
      [[ "${_profile}" == "bp_wh_auto_calib" ]]
      ;;
    *)
      return 1
      ;;
  esac
}

function warehouse_default_bp_profile() {
  local _mode="${1}"
  shift
  local _from_env
  _from_env="$(get_env_value_from_files "BP_PROFILE" "$@")"
  if [[ -n "${_from_env}" ]] && warehouse_bp_profile_valid_for_mode "${_mode}" "${_from_env}"; then
    echo "${_from_env}"
    return 0
  fi
  case "${_mode}" in
    2d) echo "bp_wh" ;;
    3d | mv3dt) echo "bp_wh_kafka" ;;
    auto-calibration) echo "bp_wh_auto_calib" ;;
    *) echo "bp_wh" ;;
  esac
}

function warehouse_sample_video_dataset() {
  local _mode="${1}"
  local _profile="${2}"
  if [[ "${_mode}" == "auto-calibration" ]] || [[ "${_profile}" == "bp_wh_auto_calib" ]]; then
    echo "warehouse-loading-dock-3cams-synthetic"
  elif [[ "${_mode}" == "3d" ]] || [[ "${_mode}" == "mv3dt" ]]; then
    echo "warehouse-4cams-20mx20m-synthetic"
  elif [[ "${_profile}" == "bp_wh" ]]; then
    echo "nv-warehouse-4cams"
  else
    echo "warehouse-loading-dock-3cams-synthetic"
  fi
}

function warehouse_num_streams() {
  local _mode="${1}"
  local _profile="${2}"
  if [[ "${_mode}" == "auto-calibration" ]] || [[ "${_profile}" == "bp_wh_auto_calib" ]]; then
    echo "3"
  elif [[ "${_mode}" == "3d" ]] || [[ "${_mode}" == "mv3dt" ]]; then
    echo "4"
  elif [[ "${_profile}" == "bp_wh" ]]; then
    echo "4"
  else
    echo "3"
  fi
}

# COMPOSE_PROFILES selector: -p/-m (or --minimal/--playback) override generated.env;
# otherwise the assignment in overrides.env is used, e.g.
# COMPOSE_PROFILES=${COMPOSE_PROFILES_WH_KAFKA_3D} or COMPOSE_PROFILES_WH_AUTO_CALIB.
function warehouse_compose_profiles_selector() {
  local _raw
  _raw="$(get_env_value_from_files "COMPOSE_PROFILES" "$@")"
  _raw="${_raw#\$\{}"
  _raw="${_raw%\}}"
  echo "${_raw}"
}

# Accept COMPOSE_PROFILES_WH_AUTO_CALIB, WH_AUTO_CALIB, or ${COMPOSE_PROFILES_WH_AUTO_CALIB}.
function warehouse_normalize_compose_selector() {
  local _in="${1}"
  _in="${_in#\$\{}"
  _in="${_in%\}}"
  case "${_in}" in
    COMPOSE_PROFILES_WH_* | COMPOSE_PROFILES_PLAYBACK_*)
      echo "${_in}"
      ;;
    WH_* | PLAYBACK_*)
      echo "COMPOSE_PROFILES_${_in}"
      ;;
    *)
      return 1
      ;;
  esac
}

# Map BP_PROFILE + MODE (+ optional minimal/playback) to a COMPOSE_PROFILES_* name.
function warehouse_compose_selector_from_profile_mode() {
  local _profile="${1}"
  local _mode="${2}"
  local _kind="${3:-}"
  if [[ "${_profile}" == "bp_wh_auto_calib" ]] || [[ "${_mode}" == "auto-calibration" ]]; then
    if [[ -n "${_kind}" ]]; then
      return 1
    fi
    echo "COMPOSE_PROFILES_WH_AUTO_CALIB"
    return 0
  fi
  if [[ "${_profile}" == "bp_wh" ]]; then
    if [[ "${_mode}" != "2d" ]] || [[ -n "${_kind}" ]]; then
      return 1
    fi
    echo "COMPOSE_PROFILES_WH_2D"
    return 0
  fi
  local _broker=""
  case "${_profile}" in
    bp_wh_kafka) _broker="KAFKA" ;;
    bp_wh_redis) _broker="REDIS" ;;
    *) return 1 ;;
  esac
  local _mode_u=""
  case "${_mode}" in
    2d) _mode_u="2D" ;;
    3d) _mode_u="3D" ;;
    mv3dt) _mode_u="MV3DT" ;;
    *) return 1 ;;
  esac
  case "${_kind}" in
    playback) echo "COMPOSE_PROFILES_PLAYBACK_${_broker}_${_mode_u}" ;;
    minimal) echo "COMPOSE_PROFILES_WH_${_broker}_${_mode_u}_MINIMAL" ;;
    "") echo "COMPOSE_PROFILES_WH_${_broker}_${_mode_u}" ;;
    *) return 1 ;;
  esac
}

function warehouse_print_accepted_p_values() {
  echo "[ERROR] Accepted -p compose lists (industry-profiles/warehouse-operations/overrides.env):"
  echo "[ERROR]   COMPOSE_PROFILES_WH_2D"
  echo "[ERROR]   COMPOSE_PROFILES_WH_KAFKA_2D  COMPOSE_PROFILES_WH_REDIS_2D"
  echo "[ERROR]   COMPOSE_PROFILES_WH_KAFKA_3D  COMPOSE_PROFILES_WH_REDIS_3D"
  echo "[ERROR]   COMPOSE_PROFILES_WH_KAFKA_MV3DT  COMPOSE_PROFILES_WH_REDIS_MV3DT"
  echo "[ERROR]   COMPOSE_PROFILES_WH_KAFKA_2D_MINIMAL  COMPOSE_PROFILES_WH_REDIS_2D_MINIMAL"
  echo "[ERROR]   COMPOSE_PROFILES_WH_KAFKA_3D_MINIMAL  COMPOSE_PROFILES_WH_REDIS_3D_MINIMAL"
  echo "[ERROR]   COMPOSE_PROFILES_WH_KAFKA_MV3DT_MINIMAL  COMPOSE_PROFILES_WH_REDIS_MV3DT_MINIMAL"
  echo "[ERROR]   COMPOSE_PROFILES_WH_AUTO_CALIB"
  echo "[ERROR]   COMPOSE_PROFILES_PLAYBACK_KAFKA_2D  COMPOSE_PROFILES_PLAYBACK_REDIS_2D"
  echo "[ERROR]   COMPOSE_PROFILES_PLAYBACK_KAFKA_3D  COMPOSE_PROFILES_PLAYBACK_REDIS_3D"
  echo "[ERROR]   COMPOSE_PROFILES_PLAYBACK_KAFKA_MV3DT  COMPOSE_PROFILES_PLAYBACK_REDIS_MV3DT"
  echo "[ERROR] Shorthand: WH_2D, WH_KAFKA_2D, WH_AUTO_CALIB, PLAYBACK_REDIS_2D, ..."
  echo "[ERROR] Blueprint aliases: bp_wh | bp_wh_kafka | bp_wh_redis | bp_wh_auto_calib"
  echo "[ERROR]   kafka/redis need -m 2d|3d|mv3dt; optional --minimal or --playback"
}

function warehouse_infer_from_compose_profiles_selector() {
  local _selector="${1}"
  case "${_selector}" in
    COMPOSE_PROFILES_WH_2D)
      echo "bp_wh 2d"
      ;;
    COMPOSE_PROFILES_WH_KAFKA_2D | COMPOSE_PROFILES_WH_KAFKA_2D_MINIMAL)
      echo "bp_wh_kafka 2d"
      ;;
    COMPOSE_PROFILES_WH_REDIS_2D | COMPOSE_PROFILES_WH_REDIS_2D_MINIMAL)
      echo "bp_wh_redis 2d"
      ;;
    COMPOSE_PROFILES_WH_KAFKA_3D | COMPOSE_PROFILES_WH_KAFKA_3D_MINIMAL)
      echo "bp_wh_kafka 3d"
      ;;
    COMPOSE_PROFILES_WH_REDIS_3D | COMPOSE_PROFILES_WH_REDIS_3D_MINIMAL)
      echo "bp_wh_redis 3d"
      ;;
    COMPOSE_PROFILES_WH_KAFKA_MV3DT | COMPOSE_PROFILES_WH_KAFKA_MV3DT_MINIMAL)
      echo "bp_wh_kafka mv3dt"
      ;;
    COMPOSE_PROFILES_WH_REDIS_MV3DT | COMPOSE_PROFILES_WH_REDIS_MV3DT_MINIMAL)
      echo "bp_wh_redis mv3dt"
      ;;
    COMPOSE_PROFILES_WH_AUTO_CALIB)
      echo "bp_wh_auto_calib auto-calibration"
      ;;
    COMPOSE_PROFILES_PLAYBACK_KAFKA_2D)
      echo "bp_wh_kafka 2d"
      ;;
    COMPOSE_PROFILES_PLAYBACK_REDIS_2D)
      echo "bp_wh_redis 2d"
      ;;
    COMPOSE_PROFILES_PLAYBACK_KAFKA_3D)
      echo "bp_wh_kafka 3d"
      ;;
    COMPOSE_PROFILES_PLAYBACK_REDIS_3D)
      echo "bp_wh_redis 3d"
      ;;
    COMPOSE_PROFILES_PLAYBACK_KAFKA_MV3DT)
      echo "bp_wh_kafka mv3dt"
      ;;
    COMPOSE_PROFILES_PLAYBACK_REDIS_MV3DT)
      echo "bp_wh_redis mv3dt"
      ;;
    *)
      return 1
      ;;
  esac
}

# LLM/VLM model name to slug mapping (for paths and config lookup)
function get_llm_slug() {
  local _name="${1}"
  case "${_name}" in
    nvidia/nemotron-3.5-lightning-30b-a3b) echo "nemotron-3.5-lightning-30b-a3b" ;;
    nvidia/NVIDIA-Nemotron-Nano-9B-v2-FP8) echo "nvidia-nemotron-nano-9b-v2-fp8" ;;
    *) echo "" ;;
  esac
}

# Models that used to ship with the blueprint. Returns a migration message for a
# removed model name, or an empty string for a name that was never bundled. Kept
# so a stale --llm fails loudly instead of resolving to an empty slug, which
# Compose renders as zero LLM services with exit 0 (a silent no-LLM deploy).
function get_removed_llm_message() {
  local _name="${1}"
  case "${_name}" in
    nvidia/nvidia-nemotron-nano-9b-v2|nvidia/nemotron-3-nano|nvidia/llama-3.3-nemotron-super-49b-v1.5|openai/gpt-oss-20b)
      echo "'${_name}' was removed from the blueprint. Use nvidia/nemotron-3.5-lightning-30b-a3b, the default on every hardware profile." ;;
    *) echo "" ;;
  esac
}

function get_vlm_slug() {
  local _name="${1}"
  case "${_name}" in
    nvidia/cosmos-reason1-7b) echo "cosmos-reason1-7b" ;;
    nvidia/cosmos-reason2-8b) echo "cosmos-reason2-8b" ;;
    nvidia/cosmos3-reasoner) echo "cosmos3-reasoner" ;;
    Qwen/Qwen3-VL-8B-Instruct) echo "qwen3-vl-8b-instruct" ;;
    *) echo "" ;;
  esac
}

# Hardware-specific RTVI local VLM GPU memory utilization (empty = keep compose/env default).
# Matches deploy/docker/scripts/dev-profile.sh for RTXPRO4500BW.
function get_rtvi_vllm_gpu_memory_utilization() {
  local _hardware_profile="${1}"
  case "${_hardware_profile}" in
    RTXPRO4500BW) echo "0.8" ;;
    *) echo "" ;;
  esac
}

# Hardware-specific RTVI local VLM max model length (empty = keep compose/env default).
# Matches deploy/docker/scripts/dev-profile.sh.
function get_rtvi_vlm_max_model_len() {
  local _hardware_profile="${1}"
  case "${_hardware_profile}" in
    RTXPRO4500BW) echo "18000" ;;
    *) echo "" ;;
  esac
}


# Gets model name from remote API endpoint (works for both LLM and VLM)
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
    _val="${_val#\"}"; _val="${_val%\"}"
    _val="${_val#\'}"; _val="${_val%\'}"
    echo "${_val}"
  fi
}

function env_file_has_var() {
  local _env_file="${1}"
  local _var_name="${2}"
  [[ -f "${_env_file}" ]] && grep -q "^${_var_name}=" "${_env_file}" 2>/dev/null
}

function get_env_value_from_files() {
  local _var_name="${1}"
  shift
  local _env_file _val="" _found="false"
  for _env_file in "$@"; do
    if env_file_has_var "${_env_file}" "${_var_name}"; then
      _val="$(get_env_value "${_env_file}" "${_var_name}")"
      _found="true"
    fi
  done
  if [[ "${_found}" == "true" ]]; then
    echo "${_val}"
  fi
}

function env_var_defined_in_files() {
  local _var_name="${1}"
  shift
  local _env_file
  for _env_file in "$@"; do
    if env_file_has_var "${_env_file}" "${_var_name}"; then
      return 0
    fi
  done
  return 1
}

# ===== Single-GPU device clamp =====
#
# Same problem and same rules as dev-profile.sh, which carries the long-form
# explanation next to its copy of these helpers (the two scripts already keep
# private copies of get_env_value, get_llm_slug and friends). Warehouse is the
# most exposed profile in the tree: RT-CV on device 0, RT-VLM on 1 and the LLM
# on 2 means it needs three GPUs before Compose will start, and asking for an
# index the host does not have is a hard container-start failure rather than a
# fallback.
#
# A host with at least as many GPUs as the highest committed index clamps
# nothing, so the three-GPU layout the warehouse profile was validated on
# renders unchanged. A GPU count of 0 leaves placement alone: an unknown count
# must not silently move a model.
DEVICE_ID_KEYS=(
  'LLM_DEVICE_ID'
  'VLM_DEVICE_ID'
  'SHARED_LLM_VLM_DEVICE_ID'
  'RT_VLM_DEVICE_ID'
  'RT_CV_DEVICE_ID'
  'RT_EMBED_DEVICE_ID'
  'FIXED_SHARED_DEVICE_IDS'
)

# shellcheck disable=SC2034  # read by .github/scripts/check_gpu_device_clamp.py
DEVICE_RESERVATION_KEYS=(
  'RESERVED_DEVICE_IDS'
)

gpu_count=0
device_clamp_applied="false"
shared_llm_vlm_device_id=""
rt_vlm_device_id=""
rt_cv_device_id=""
rt_embed_device_id=""
fixed_shared_device_ids=""
reserved_device_ids=""

function get_nvidia_smi_gpu_count() {
  local _count
  _count="$(nvidia-smi --query-gpu=index --format=csv,noheader 2>/dev/null | sed '/^[[:space:]]*$/d' | wc -l | tr -d '[:space:]')"
  [[ "${_count}" =~ ^[0-9]+$ ]] || _count="0"
  echo "${_count}"
}

# VSS_GPU_COUNT is a test hook (see dev-profile.sh): it makes both the
# single-GPU and the multi-GPU branch reachable on any host. Deploying never
# needs it.
function get_deployment_gpu_count() {
  if [[ "${VSS_GPU_COUNT:-}" =~ ^[0-9]+$ ]]; then
    echo "${VSS_GPU_COUNT}"
    return
  fi
  get_nvidia_smi_gpu_count
}

function clamp_device_index() {
  local _index="${1}" _gpu_count="${2}"
  if [[ ! "${_index}" =~ ^[0-9]+$ ]] || [[ ! "${_gpu_count}" =~ ^[0-9]+$ ]] || (( _gpu_count <= 0 )); then
    echo "${_index}"
    return
  fi
  if (( _index >= _gpu_count )); then
    echo "$(( _gpu_count - 1 ))"
  else
    echo "${_index}"
  fi
}

function clamp_device_index_list() {
  local _list="${1}" _gpu_count="${2}"
  local _entry _clamped _out=""
  local -a _entries
  IFS=',' read -ra _entries <<< "${_list}"
  for _entry in "${_entries[@]}"; do
    [[ -n "${_entry}" ]] || continue
    _clamped="$(clamp_device_index "${_entry}" "${_gpu_count}")"
    case ",${_out}," in *",${_clamped},"*) continue ;; esac
    _out="${_out:+${_out},}${_clamped}"
  done
  echo "${_out}"
}

function filter_existing_device_ids() {
  local _list="${1}" _gpu_count="${2}"
  local _entry _out=""
  local -a _entries
  IFS=',' read -ra _entries <<< "${_list}"
  for _entry in "${_entries[@]}"; do
    [[ -n "${_entry}" ]] || continue
    if [[ "${_entry}" =~ ^[0-9]+$ ]] && [[ "${_gpu_count}" =~ ^[0-9]+$ ]] && (( _gpu_count > 0 )) && (( _entry >= _gpu_count )); then
      continue
    fi
    case ",${_out}," in *",${_entry},"*) continue ;; esac
    _out="${_out:+${_out},}${_entry}"
  done
  echo "${_out}"
}

function count_device_ids() {
  local _list="${1}"
  local _entry _count=0
  local -a _entries
  IFS=',' read -ra _entries <<< "${_list}"
  for _entry in "${_entries[@]}"; do
    [[ -n "${_entry}" ]] && ((_count++))
  done
  echo "${_count}"
}

function get_profile_max_device_index() {
  local _max=0 _key _value _entry
  local -a _env_files=("$@")
  local -a _entries
  for _key in "${DEVICE_ID_KEYS[@]}"; do
    _value="$(get_env_value_from_files "${_key}" "${_env_files[@]}")"
    _value="${_value// /}"
    IFS=',' read -ra _entries <<< "${_value}"
    for _entry in "${_entries[@]}"; do
      [[ "${_entry}" =~ ^[0-9]+$ ]] || continue
      (( _entry > _max )) && _max="${_entry}"
    done
  done
  echo "${_max}"
}

# Clamp one index and say so on stderr; stdout carries the value for capture.
function clamp_and_log_device_index() {
  local _key="${1}" _value="${2}"
  local _clamped
  _clamped="$(clamp_device_index "${_value}" "${gpu_count}")"
  if [[ "${_clamped}" != "${_value}" ]]; then
    echo "[WARNING]   ${_key}: device ${_value} does not exist here, using device ${_clamped}" >&2
  fi
  echo "${_clamped}"
}

function clamp_device_ids_to_gpu_count() {
  local -a _env_files=("$@")
  local _max_index _last_device _reserved_before _reserved_kept _fixed_shared_before

  gpu_count="$(get_deployment_gpu_count)"
  device_clamp_applied="false"

  shared_llm_vlm_device_id="$(get_env_value_from_files "SHARED_LLM_VLM_DEVICE_ID" "${_env_files[@]}")"
  rt_vlm_device_id="$(get_env_value_from_files "RT_VLM_DEVICE_ID" "${_env_files[@]}")"
  rt_cv_device_id="$(get_env_value_from_files "RT_CV_DEVICE_ID" "${_env_files[@]}")"
  rt_embed_device_id="$(get_env_value_from_files "RT_EMBED_DEVICE_ID" "${_env_files[@]}")"
  fixed_shared_device_ids="$(get_env_value_from_files "FIXED_SHARED_DEVICE_IDS" "${_env_files[@]}")"
  fixed_shared_device_ids="${fixed_shared_device_ids// /}"
  reserved_device_ids="$(get_env_value_from_files "RESERVED_DEVICE_IDS" "${_env_files[@]}")"
  reserved_device_ids="${reserved_device_ids// /}"

  _max_index="$(get_profile_max_device_index "${_env_files[@]}")"

  if (( gpu_count <= 0 )); then
    echo "[INFO] GPU count unavailable from nvidia-smi; keeping the committed device placement as-is."
    return
  fi
  if (( _max_index < gpu_count )); then
    return
  fi

  device_clamp_applied="true"
  _last_device=$(( gpu_count - 1 ))
  echo "[WARNING] This deployment's committed placement uses device indices up to ${_max_index}, so it assumes $(( _max_index + 1 )) GPU(s); this host has ${gpu_count}."
  echo "[WARNING] Remapping every index >= ${gpu_count} onto device ${_last_device}. Services that were on separate GPUs will now share one:"

  llm_device_id="$(clamp_and_log_device_index "LLM_DEVICE_ID" "${llm_device_id}")"
  vlm_device_id="$(clamp_and_log_device_index "VLM_DEVICE_ID" "${vlm_device_id}")"
  shared_llm_vlm_device_id="$(clamp_and_log_device_index "SHARED_LLM_VLM_DEVICE_ID" "${shared_llm_vlm_device_id}")"
  rt_vlm_device_id="$(clamp_and_log_device_index "RT_VLM_DEVICE_ID" "${rt_vlm_device_id}")"
  rt_cv_device_id="$(clamp_and_log_device_index "RT_CV_DEVICE_ID" "${rt_cv_device_id}")"
  rt_embed_device_id="$(clamp_and_log_device_index "RT_EMBED_DEVICE_ID" "${rt_embed_device_id}")"

  _fixed_shared_before="${fixed_shared_device_ids}"
  fixed_shared_device_ids="$(clamp_device_index_list "${fixed_shared_device_ids}" "${gpu_count}")"
  if [[ "${fixed_shared_device_ids}" != "${_fixed_shared_before}" ]]; then
    echo "[WARNING]   FIXED_SHARED_DEVICE_IDS: '${_fixed_shared_before}' -> '${fixed_shared_device_ids}'"
  fi

  # A reservation keeps the LLM and VLM off a GPU. Clamping it would leave the
  # models nowhere to go, so drop entries for devices that do not exist and drop
  # it outright once it would cover every GPU the host has.
  _reserved_before="${reserved_device_ids}"
  _reserved_kept="$(filter_existing_device_ids "${reserved_device_ids}" "${gpu_count}")"
  if (( $(count_device_ids "${_reserved_kept}") >= gpu_count )); then
    reserved_device_ids=""
  else
    reserved_device_ids="${_reserved_kept}"
  fi
  if [[ "${reserved_device_ids}" != "${_reserved_before}" ]]; then
    echo "[WARNING]   RESERVED_DEVICE_IDS: '${_reserved_before}' -> '${reserved_device_ids}' (a reservation covering every GPU cannot be honored)"
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

function mask_external_ip_args() {
  local _arg _masked_value
  local _mask_next="false"
  local _masked_args=()
  for _arg in "$@"; do
    if [[ "${_mask_next}" == "true" ]]; then
      _masked_args+=("$(mask_secret "${_arg}")")
      _mask_next="false"
      continue
    fi
    case "${_arg}" in
      -e|--external-ip)
        _masked_args+=("${_arg}")
        _mask_next="true"
        ;;
      --external-ip=*)
        _masked_value="${_arg#--external-ip=}"
        _masked_args+=("--external-ip=$(mask_secret "${_masked_value}")")
        ;;
      -e?*)
        _masked_value="${_arg#-e}"
        _masked_args+=("-e$(mask_secret "${_masked_value}")")
        ;;
      *)
        _masked_args+=("${_arg}")
        ;;
    esac
  done
  echo "${_masked_args[*]}"
}

function usage() {
  echo "Usage: ${0} (up|down) [options]"
  echo "   or: ${0} (-h|--help)"
  echo ""
  echo "Positional arguments:"
  echo "  desired-state                    up or down"
  echo ""
  echo "NOTE: The following are read from the environment:"
  echo "  • NGC_CLI_API_KEY     — required for 'up' when LLM_MODE or VLM_MODE is local/local_shared"
  echo "  • NVIDIA_API_KEY      — optional; for remote LLM/VLM endpoints"
  echo "  • OPENAI_API_KEY      — optional; for remote LLM/VLM endpoints"
  echo "  • LLM_ENDPOINT_URL    — when --use-remote-llm: LLM base URL"
  echo "  • VLM_ENDPOINT_URL    — when --use-remote-vlm: VLM base URL"
  echo ""
  echo "Options for 'up':"
  echo "  -d, --deployment                 [REQUIRED] Deployment type."
  echo "                                   • warehouse — .env under industry-profiles/warehouse-operations/"
  echo "  -m, --mode                       Deployment mode. Used with -p. Default: MODE from overrides.env, else 2d."
  echo "                                   • 2d, 3d, or mv3dt for perception variants"
  echo "                                   • auto-calibration for the single warehouse auto-calib stack"
  echo "  -p, --bp-profile                Warehouse stack to deploy. Writes COMPOSE_PROFILES in generated.env."
  echo "                                   • Compose lists from warehouse-operations/overrides.env:"
  echo "                                     COMPOSE_PROFILES_WH_2D"
  echo "                                     COMPOSE_PROFILES_WH_{KAFKA,REDIS}_{2D,3D,MV3DT}"
  echo "                                     COMPOSE_PROFILES_WH_{KAFKA,REDIS}_{2D,3D,MV3DT}_MINIMAL"
  echo "                                     COMPOSE_PROFILES_WH_AUTO_CALIB"
  echo "                                     COMPOSE_PROFILES_PLAYBACK_{KAFKA,REDIS}_{2D,3D,MV3DT}"
  echo "                                   • Shorthand: WH_2D, WH_KAFKA_3D, WH_AUTO_CALIB, PLAYBACK_REDIS_2D, ..."
  echo "                                   • Blueprint aliases: bp_wh, bp_wh_kafka, bp_wh_redis, bp_wh_auto_calib"
  echo "                                     (kafka/redis need -m 2d|3d|mv3dt; optional --minimal/--playback)"
  echo "                                   • If omitted, COMPOSE_PROFILES in overrides.env is used."
  echo "  --minimal                        With -p bp_wh_kafka or bp_wh_redis: use the *_MINIMAL compose list."
  echo "  --playback                       With -p bp_wh_kafka or bp_wh_redis: use COMPOSE_PROFILES_PLAYBACK_*."
  echo "  -i, --host-ip                    Host IP."
  echo "                                   • Default: primary IP from ip route"
  echo "  -e, --external-ip                Externally accessible IP."
  echo "  -D, --data-dir PATH             [REQUIRED] Path for sample data (VSS_DATA_DIR)."
  echo "                                   • Where warehouse sample data tar is extracted"
  echo "                                   • Contains: models, videos, data_log, playback"
  echo "                                   • Also required for 'down' (same path used with 'up')"
  echo "  -E, --es, --elasticsearch-mode  Elasticsearch mode: gpu (GPU-accelerated) or cpu (CPU-only)."
  echo "                                   • Default: cpu"
  echo "  -s, --sample-video-dataset      [Warehouse only] Override sample video dataset."
  echo "                                   • Default by MODE/BP_PROFILE (or COMPOSE_PROFILES in overrides.env):"
  echo "                                     2d+bp_wh: nv-warehouse-4cams (4 streams)"
  echo "                                     2d+bp_wh_kafka/bp_wh_redis: warehouse-loading-dock-3cams-synthetic (3 streams)"
  echo "                                     3d/mv3dt+bp_wh_kafka/bp_wh_redis: warehouse-4cams-20mx20m-synthetic (4 streams)"
  echo "                                     auto-calibration/bp_wh_auto_calib: warehouse-loading-dock-3cams-synthetic (3 streams)"
  echo ""
  echo "  [LLM/VLM - for 2d only: warehouse bp_wh (NIM + agents)]"
  echo "  -H, --hardware-profile          H100, L40S, RTXPRO6000BW, DGX-SPARK, etc."
  echo "  --llm                           LLM model (e.g. nvidia/nemotron-3.5-lightning-30b-a3b)"
  echo "  --vlm                           VLM model (e.g. nvidia/cosmos-reason2-8b)"
  echo "  --llm-device-id                 GPU device ID for LLM"
  echo "  --vlm-device-id                 GPU device ID for VLM"
  echo "  --use-remote-llm                Use remote LLM (LLM_ENDPOINT_URL)"
  echo "  --use-remote-vlm               Use remote VLM (VLM_ENDPOINT_URL)"
  echo "  --llm-model-type               nim or openai (when --use-remote-llm)"
  echo "  --vlm-model-type               nim or openai (when --use-remote-vlm)"
  echo "  --llm-env-file                 Path to LLM env file"
  echo "  --vlm-env-file                 Path to VLM env file"
  echo "  --use-sbsa-images              Use SBSA-tagged image variants (e.g. RTVI CV) from commented lines in .env"
  echo "                                   • Enabled automatically for -H DGX-SPARK"
  echo "                                   • Use with -H OTHER on GB300/Spark-class hosts that need SBSA images"
  echo ""
  echo "Options for 'up' and 'down':"
  echo "  -n, --dry-run                    Print commands without executing them"
  echo "  --skip-revert-from-oldest-backup Skip reverting live files from oldest *.backup_* before deleting backups"
  echo "                                   • Same meaning as cleanup_all_datalog.sh; applies when data_log cleanup runs"
  echo "  -h, --help                       Show this help message"
}

function contains_element() {
  local _element _ref_array _array_element
  _element="${1}"
  _ref_array=("${@:2}")
  for _array_element in "${_ref_array[@]}"; do
    if [[ "${_element}" == "${_array_element}" ]]; then
      return 0
    fi
  done
  return 1
}

# Auto-calibration's compose profile list has no sdr-controller, so the warehouse SDRC
# routing overrides must fall back to the direct VST defaults in services/vios/vst.env.
# Commented here rather than in overrides.env so other warehouse profiles keep SDRC.
function disable_sdrc_routing_in_env() {
  local _generated_env="${1}"
  local _key
  for _key in VST_USE_SDRC STREAM_PROCESSOR_MODULE_ENDPOINT VST_NGINX_MODE; do
    if grep -qE "^${_key}=" "${_generated_env}"; then
      sed -i -E "s/^(${_key}=.*)/# \1/" "${_generated_env}"
      echo "[INFO] Commented ${_key} for auto-calibration (direct VST mode from services/vios/vst.env)"
    fi
  done
}

# Swap non-SBSA image tag lines for commented *sbsa* variants in generated.env (DGX-SPARK or --use-sbsa-images).
function apply_sbsa_image_tags_to_env() {
  local _generated_env="${1}"
  local _reason="${2}"
  local _key
  while IFS= read -r _key; do
    [[ -z "${_key}" ]] && continue
    sed -i -E "/sbsa/! s/^(${_key})=(.*)/# \1=\2/" "${_generated_env}"
    sed -i -E "/sbsa/ s/^#[[:space:]]*(${_key})=(.*)/\1=\2/" "${_generated_env}"
    echo "[INFO] Swapped to SBSA (${_reason}): ${_key}"
  done < <(grep -E '^#[[:space:]]*[A-Za-z0-9_]+=.*sbsa' "${_generated_env}" 2>/dev/null | sed -nE 's/^#[[:space:]]*([A-Za-z0-9_]+)=.*/\1/p' | sort -u)
}

function validate_args() {
  local _args _valid_args _all_good
  _args=("${@}")
  _all_good=0

  _valid_args=$(getopt -q -o d:m:p:H:i:e:s:D:E: --long deployment:,mode:,bp-profile:,hardware-profile:,host-ip:,external-ip:,sample-video-dataset:,elasticsearch-mode:,es:,llm:,vlm:,llm-device-id:,vlm-device-id:,use-remote-llm,use-remote-vlm,llm-model-type:,vlm-model-type:,llm-env-file:,vlm-env-file:,use-sbsa-images,minimal,playback,data-dir:,data-directory:,dry-run,skip-revert-from-oldest-backup,help -- "${_args[@]}")
  if [[ $? -ne 0 ]]; then
    echo "[ERROR] Invalid usage: $(mask_external_ip_args "${_args[@]}")"
    ((_all_good++))
  else
    eval set -- "${_valid_args}"

    while true; do
      case "${1}" in
        --help) usage; exit 0 ;;
        --) shift; break ;;
        *) shift ;;
      esac
    done

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
  local _args _valid_args _all_good
  _args=("${@}")
  _all_good=0

  _valid_args=$(getopt -q -o d:m:p:H:i:e:s:D:E: --long deployment:,mode:,bp-profile:,hardware-profile:,host-ip:,external-ip:,sample-video-dataset:,elasticsearch-mode:,es:,llm:,vlm:,llm-device-id:,vlm-device-id:,use-remote-llm,use-remote-vlm,llm-model-type:,vlm-model-type:,llm-env-file:,vlm-env-file:,use-sbsa-images,minimal,playback,data-dir:,data-directory:,dry-run,skip-revert-from-oldest-backup,help -- "${_args[@]}")
  eval set -- "${_valid_args}"

  while true; do
    case "${1}" in
      -d | --deployment)
        shift
        deployment="${1}"
        options_provided+=("deployment")
        shift
        ;;
      -m | --mode)
        shift
        mode="${1}"
        options_provided+=("mode")
        shift
        ;;
      -p | --bp-profile)
        shift
        bp_profile="${1}"
        options_provided+=("bp-profile")
        shift
        ;;
      --minimal)
        compose_variant="minimal"
        options_provided+=("minimal")
        shift
        ;;
      --playback)
        compose_variant="playback"
        options_provided+=("playback")
        shift
        ;;
      -H | --hardware-profile)
        shift
        hardware_profile="${1}"
        options_provided+=("hardware-profile")
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
      --use-sbsa-images)
        use_sbsa_images="true"
        options_provided+=("use-sbsa-images")
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
      -s | --sample-video-dataset)
        shift
        sample_video_dataset="${1}"
        options_provided+=("sample-video-dataset")
        shift
        ;;
      -E | --elasticsearch-mode | --es)
        shift
        elasticsearch_mode="${1}"
        options_provided+=("elasticsearch-mode")
        shift
        ;;
      -D | --data-dir | --data-directory)
        shift
        data_directory="${1}"
        options_provided+=("data-dir")
        shift
        ;;
      --dry-run)
        dry_run="true"
        options_provided+=("dry-run")
        shift
        ;;
      --skip-revert-from-oldest-backup)
        revert_from_oldest_backup="false"
        options_provided+=("skip-revert-from-oldest-backup")
        shift
        ;;
      --help)
        usage
        exit 0
        ;;
      --)
        shift
        break
        ;;
      *)
        shift
        ;;
    esac
  done

  desired_state="${1}"

  if [[ "${desired_state}" == "down" ]]; then
    if ! contains_element "data-dir" "${options_provided[@]}"; then
      echo "[ERROR] --data-dir (-D) is required for desired-state 'down'"
      ((_all_good++))
    fi
    for _opt in "${options_provided[@]}"; do
      if [[ "${_opt}" != "dry-run" ]] && [[ "${_opt}" != "data-dir" ]] && [[ "${_opt}" != "skip-revert-from-oldest-backup" ]]; then
        echo "[ERROR] For desired-state 'down', only --dry-run, --data-dir (-D), and --skip-revert-from-oldest-backup are allowed"
        echo "[ERROR] Invalid option provided: ${_opt}"
        ((_all_good++))
        break
      fi
    done
  elif [[ "${desired_state}" == "up" ]]; then
    if ! contains_element "deployment" "${options_provided[@]}"; then
      echo "[ERROR] --deployment (-d) is required for desired-state 'up'"
      ((_all_good++))
    fi
    if ! contains_element "data-dir" "${options_provided[@]}"; then
      echo "[ERROR] --data-dir (-D) is required for desired-state 'up'"
      ((_all_good++))
    fi

    _valid_deployments=('warehouse')
    if [[ -n "${deployment}" ]]; then
      if ! contains_element "${deployment}" "${_valid_deployments[@]}"; then
        echo "[ERROR] Invalid deployment: ${deployment}. Must be: warehouse"
        ((_all_good++))
      fi
    fi

    if [[ -n "${deployment}" ]] && contains_element "${deployment}" "${_valid_deployments[@]}"; then
      local _deploy_env="${deployment_directory}/$(deployment_rel_path "${deployment}")/.env"
      local _deploy_overrides_env="${deployment_directory}/$(deployment_rel_path "${deployment}")/overrides.env"
      if [[ ! -f "${_deploy_env}" ]]; then
        echo "[ERROR] Deployment .env file not found: ${_deploy_env}"
        ((_all_good++))
      fi
      if [[ ! -f "${_deploy_overrides_env}" ]]; then
        echo "[ERROR] Deployment overrides env file not found: ${_deploy_overrides_env}"
        ((_all_good++))
      fi
    fi

    if [[ -n "${deployment}" ]] && [[ -f "${deployment_directory}/$(deployment_rel_path "${deployment}")/.env" ]] && [[ -f "${deployment_directory}/$(deployment_rel_path "${deployment}")/overrides.env" ]]; then
      local _deploy_env="${deployment_directory}/$(deployment_rel_path "${deployment}")/.env"
      local _deploy_overrides_env="${deployment_directory}/$(deployment_rel_path "${deployment}")/overrides.env"

      # Compose list: -p/-m (--minimal/--playback) win over COMPOSE_PROFILES in overrides.env.
      if contains_element "minimal" "${options_provided[@]}" && contains_element "playback" "${options_provided[@]}"; then
        echo "[ERROR] --minimal and --playback cannot be used together"
        ((_all_good++))
      fi
      if ! contains_element "mode" "${options_provided[@]}"; then
        mode="$(get_env_value_from_files "MODE" "${_deploy_env}" "${_deploy_overrides_env}")"
        mode="${mode:-2d}"
      fi
      local _compose_selector="" _inferred="" _p_arg="${bp_profile}"
      if contains_element "bp-profile" "${options_provided[@]}"; then
        if _compose_selector="$(warehouse_normalize_compose_selector "${_p_arg}")" \
          && _inferred="$(warehouse_infer_from_compose_profiles_selector "${_compose_selector}")"; then
          if [[ -n "${compose_variant}" ]]; then
            echo "[ERROR] --minimal/--playback cannot be combined with a compose-list -p (${_p_arg})"
            ((_all_good++))
          else
            local _inferred_profile _inferred_mode
            _inferred_profile="${_inferred%% *}"
            _inferred_mode="${_inferred#* }"
            if contains_element "mode" "${options_provided[@]}" && [[ "${mode}" != "${_inferred_mode}" ]]; then
              echo "[ERROR] -m ${mode} conflicts with -p ${_p_arg} (MODE=${_inferred_mode})"
              ((_all_good++))
            fi
            bp_profile="${_inferred_profile}"
            mode="${_inferred_mode}"
            compose_profiles_selector="${_compose_selector}"
          fi
        elif contains_element "${_p_arg}" "bp_wh" "bp_wh_kafka" "bp_wh_redis" "bp_wh_auto_calib"; then
          bp_profile="${_p_arg}"
          if [[ "${bp_profile}" == "bp_wh_auto_calib" ]]; then
            if contains_element "mode" "${options_provided[@]}" && [[ "${mode}" != "auto-calibration" ]]; then
              echo "[ERROR] -m ${mode} conflicts with -p bp_wh_auto_calib (MODE=auto-calibration)"
              ((_all_good++))
            fi
            mode="auto-calibration"
          fi
          if ! _compose_selector="$(warehouse_compose_selector_from_profile_mode "${bp_profile}" "${mode}" "${compose_variant}")"; then
            echo "[ERROR] Cannot map -p ${bp_profile} -m ${mode} ${compose_variant:+--${compose_variant}} to a COMPOSE_PROFILES list."
            warehouse_print_accepted_p_values
            ((_all_good++))
          elif ! _inferred="$(warehouse_infer_from_compose_profiles_selector "${_compose_selector}")"; then
            echo "[ERROR] Unknown COMPOSE_PROFILES selector: ${_compose_selector}"
            warehouse_print_accepted_p_values
            ((_all_good++))
          else
            compose_profiles_selector="${_compose_selector}"
          fi
        else
          echo "[ERROR] Invalid -p ${_p_arg}."
          warehouse_print_accepted_p_values
          bp_profile=""
          ((_all_good++))
        fi
      else
        if [[ -n "${compose_variant}" ]]; then
          echo "[ERROR] --minimal/--playback requires -p bp_wh_kafka or -p bp_wh_redis"
          ((_all_good++))
        fi
        _compose_selector="$(warehouse_compose_profiles_selector "${_deploy_env}" "${_deploy_overrides_env}")"
        if [[ -z "${_compose_selector}" ]]; then
          echo "[ERROR] COMPOSE_PROFILES is not set in ${_deploy_overrides_env}."
          echo "[ERROR] Pass -p (e.g. -p bp_wh_auto_calib) or set COMPOSE_PROFILES=\${COMPOSE_PROFILES_WH_*} in overrides.env."
          ((_all_good++))
        elif ! _inferred="$(warehouse_infer_from_compose_profiles_selector "${_compose_selector}")"; then
          echo "[ERROR] Unknown COMPOSE_PROFILES selector: ${_compose_selector}"
          warehouse_print_accepted_p_values
          ((_all_good++))
        else
          local _inferred_profile _inferred_mode
          _inferred_profile="${_inferred%% *}"
          _inferred_mode="${_inferred#* }"
          if contains_element "mode" "${options_provided[@]}" && [[ "${mode}" != "${_inferred_mode}" ]]; then
            echo "[WARN] -m ${mode} ignored; COMPOSE_PROFILES=\${${_compose_selector}} selects ${_inferred_mode}. Pass -p to choose the stack."
          fi
          bp_profile="${_inferred_profile}"
          mode="${_inferred_mode}"
          compose_profiles_selector="${_compose_selector}"
        fi
      fi
      if [[ -z "${bp_profile}" ]] && ! contains_element "bp-profile" "${options_provided[@]}"; then
        bp_profile="$(warehouse_default_bp_profile "${mode}" "${_deploy_env}" "${_deploy_overrides_env}")"
      fi
      # HARDWARE_PROFILE: default from .env for any warehouse mode/profile when -H not passed
      if [[ "${deployment}" == "warehouse" ]]; then
        if ! contains_element "hardware-profile" "${options_provided[@]}"; then
          hardware_profile="$(get_env_value_from_files "HARDWARE_PROFILE" "${_deploy_env}" "${_deploy_overrides_env}")"
        fi
      fi
      # LLM/VLM: populate from .env when not provided (2d only: warehouse bp_wh)
      if [[ "${mode}" == "2d" ]] && [[ "${deployment}" == "warehouse" ]] && [[ "${bp_profile}" == "bp_wh" ]]; then
        if ! contains_element "llm-device-id" "${options_provided[@]}"; then
          llm_device_id="$(get_env_value_from_files "LLM_DEVICE_ID" "${_deploy_env}" "${_deploy_overrides_env}")"
        fi
        if ! contains_element "vlm-device-id" "${options_provided[@]}"; then
          vlm_device_id="$(get_env_value_from_files "VLM_DEVICE_ID" "${_deploy_env}" "${_deploy_overrides_env}")"
        fi
        if ! contains_element "llm-model-type" "${options_provided[@]}"; then
          llm_model_type="$(get_env_value_from_files "LLM_MODEL_TYPE" "${_deploy_env}" "${_deploy_overrides_env}")"
        fi
        if ! contains_element "vlm-model-type" "${options_provided[@]}"; then
          vlm_model_type="$(get_env_value_from_files "VLM_MODEL_TYPE" "${_deploy_env}" "${_deploy_overrides_env}")"
        fi
      fi

      # Resolve the committed placement against the GPUs this host actually has.
      # Runs for every warehouse mode, not just 2d/bp_wh: RT_CV_DEVICE_ID and
      # RT_VLM_DEVICE_ID come from the deployment .env and apply to all of them,
      # so gating this on the mode is how the general case gets missed.
      clamp_device_ids_to_gpu_count "${_deploy_env}" "${_deploy_overrides_env}"

      if [[ "${deployment}" == "warehouse" ]]; then
        _valid_modes=('2d' '3d' 'mv3dt' 'auto-calibration')
        if [[ -n "${mode}" ]] && ! contains_element "${mode}" "${_valid_modes[@]}"; then
          echo "[ERROR] Invalid mode: ${mode}. Must be one of: 2d, 3d, mv3dt, auto-calibration"
          ((_all_good++))
        fi
        _valid_wh_profiles=('bp_wh' 'bp_wh_kafka' 'bp_wh_redis' 'bp_wh_auto_calib')
        if [[ -n "${bp_profile}" ]] && ! contains_element "${bp_profile}" "${_valid_wh_profiles[@]}"; then
          echo "[ERROR] Invalid bp-profile for warehouse: ${bp_profile}. Must be one of: bp_wh, bp_wh_kafka, bp_wh_redis, bp_wh_auto_calib"
          ((_all_good++))
        fi
        if [[ -n "${mode}" ]] && [[ -n "${bp_profile}" ]] && ! warehouse_bp_profile_valid_for_mode "${mode}" "${bp_profile}"; then
          echo "[ERROR] Invalid MODE=${mode} with BP_PROFILE=${bp_profile}."
          case "${mode}" in
            2d)
              echo "[ERROR]   MODE=2d supports: bp_wh, bp_wh_kafka, bp_wh_redis"
              ;;
            3d | mv3dt)
              echo "[ERROR]   MODE=${mode} supports: bp_wh_kafka, bp_wh_redis (not bp_wh)"
              ;;
            auto-calibration)
              echo "[ERROR]   MODE=auto-calibration supports: bp_wh_auto_calib"
              ;;
          esac
          ((_all_good++))
        fi
        if [[ "${mode}" != "2d" ]] || [[ "${bp_profile}" != "bp_wh" ]]; then
          for _llm_vlm_opt in llm vlm llm-device-id vlm-device-id use-remote-llm use-remote-vlm llm-model-type vlm-model-type llm-env-file vlm-env-file; do
            if contains_element "${_llm_vlm_opt}" "${options_provided[@]}"; then
              echo "[ERROR] --${_llm_vlm_opt} is only valid for MODE=2d and BP_PROFILE=bp_wh (NIM/agents stack)"
              ((_all_good++))
              break
            fi
          done
        fi
      fi
      # Elasticsearch mode: default cpu; populate from .env when not provided
      if ! contains_element "elasticsearch-mode" "${options_provided[@]}"; then
        elasticsearch_mode="$(get_env_value_from_files "ELASTICSEARCH_MODE" "${_deploy_env}" "${_deploy_overrides_env}")"
        elasticsearch_mode="${elasticsearch_mode:-cpu}"
      fi
    fi

    # Validate elasticsearch-mode
    if [[ -n "${elasticsearch_mode}" ]]; then
      _valid_es_modes=('cpu' 'gpu')
      if ! contains_element "${elasticsearch_mode}" "${_valid_es_modes[@]}"; then
        echo "[ERROR] Invalid elasticsearch-mode: ${elasticsearch_mode}. Must be one of: cpu, gpu"
        ((_all_good++))
      fi
    fi

    if [[ "${mode}" == "2d" ]] && [[ "${deployment}" == "warehouse" ]] && [[ "${bp_profile}" == "bp_wh" ]]; then
      if contains_element "use-remote-llm" "${options_provided[@]}" && [[ -z "${LLM_ENDPOINT_URL:-}" ]]; then
        echo "[ERROR] LLM_ENDPOINT_URL must be set when --use-remote-llm is passed"
        ((_all_good++))
      fi
      if contains_element "use-remote-vlm" "${options_provided[@]}" && [[ -z "${VLM_ENDPOINT_URL:-}" ]]; then
        echo "[ERROR] VLM_ENDPOINT_URL must be set when --use-remote-vlm is passed"
        ((_all_good++))
      fi
      # Validate a locally-served --llm here rather than in state_up: the main
      # path runs state_down first, so a bad name would otherwise destroy a
      # healthy deployment before reporting the error. Remote endpoints serve
      # arbitrary ids, so skip the allowlist when LLM_MODE resolves to remote.
      if [[ -n "${llm}" ]] && ! contains_element "use-remote-llm" "${options_provided[@]}"; then
        local _pa_dir _pa_llm_mode
        _pa_dir="${deployment_directory}/$(deployment_rel_path "${deployment}")"
        _pa_llm_mode="$(get_env_value_from_files "LLM_MODE" "${_pa_dir}/.env" "${_pa_dir}/overrides.env")"
        if [[ "${_pa_llm_mode:-local}" != "remote" ]] && [[ -z "$(get_llm_slug "${llm}")" ]]; then
          local _pa_removed
          _pa_removed="$(get_removed_llm_message "${llm}")"
          if [[ -n "${_pa_removed}" ]]; then
            echo "[ERROR] ${_pa_removed}"
          else
            echo "[ERROR] Invalid LLM model name: ${llm}. Must be one of: nvidia/nemotron-3.5-lightning-30b-a3b, nvidia/NVIDIA-Nemotron-Nano-9B-v2-FP8"
          fi
          ((_all_good++))
        fi
      fi
    fi

    if [[ "${deployment}" == "warehouse" ]] && [[ "${bp_profile}" != "bp_wh_auto_calib" ]] && [[ "${mode}" != "auto-calibration" ]]; then
      if [[ -z "${ngc_cli_api_key}" ]]; then
        echo "[ERROR] NGC_CLI_API_KEY is required for 'up' (warehouse RT-CV model downloads via compose init; also required for local NIM when BP_PROFILE=bp_wh)"
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

function print_args() {
  echo "=== Captured Arguments ==="
  echo "desired-state:             ${desired_state}"
  echo "deployment-directory:      ${deployment_directory}"
  echo "data-directory:           ${data_directory}"
  echo "dry-run:                   ${dry_run}"
  echo "revert-from-oldest-backup: ${revert_from_oldest_backup}"
  if [[ "${desired_state}" == "up" ]]; then
    echo "deployment:                 ${deployment}"
    echo "mode:                     ${mode}"
    echo "bp-profile:               ${bp_profile}"
    if [[ "${deployment}" == "warehouse" ]] && [[ -n "${compose_profiles_selector}" ]]; then
      echo "compose-profiles:         \${${compose_profiles_selector}}"
    fi
    echo "elasticsearch-mode:       ${elasticsearch_mode}"
    if [[ "${deployment}" == "warehouse" ]] && [[ -n "${sample_video_dataset}" ]]; then
      echo "sample-video-dataset:      ${sample_video_dataset}"
    fi
    if [[ "${deployment}" == "warehouse" ]] && [[ -n "${hardware_profile}" ]]; then
      echo "hardware-profile:          ${hardware_profile}"
    fi
    if [[ "${hardware_profile}" == "DGX-SPARK" ]] || [[ "${use_sbsa_images}" == "true" ]]; then
      if [[ "${hardware_profile}" == "DGX-SPARK" ]]; then
        echo "use-sbsa-images:           true (DGX-SPARK)"
      else
        echo "use-sbsa-images:           true (--use-sbsa-images)"
      fi
    fi
    if [[ "${mode}" == "2d" ]] && [[ "${deployment}" == "warehouse" ]] && [[ "${bp_profile}" == "bp_wh" ]]; then
      [[ -n "${llm}" ]] && echo "llm:                       ${llm}"
      [[ -n "${vlm}" ]] && echo "vlm:                       ${vlm}"
      [[ -n "${llm_device_id}" ]] && echo "llm-device-id:             ${llm_device_id}"
      [[ -n "${vlm_device_id}" ]] && echo "vlm-device-id:             ${vlm_device_id}"
      contains_element "use-remote-llm" "${options_provided[@]}" && echo "use-remote-llm:            true"
      contains_element "use-remote-vlm" "${options_provided[@]}" && echo "use-remote-vlm:            true"
    fi
    echo "host-ip:                  ${host_ip}"
    if [[ -n "${external_ip}" ]]; then
      echo "external-ip:               $(mask_secret "${external_ip}")"
    fi
    echo "ngc-cli-api-key:          $(mask_secret "${ngc_cli_api_key}")"
  fi
  echo "=========================="
}

function state_up() {
  local _deploy_rel _deploy_dir _source_env _overrides_env _generated_env
  _deploy_rel="$(deployment_rel_path "${deployment}")"
  _deploy_dir="${deployment_directory}/${_deploy_rel}"
  _source_env="${_deploy_dir}/.env"
  _overrides_env="${_deploy_dir}/overrides.env"
  _generated_env="${_deploy_dir}/generated.env"

  echo "[INFO] Generating environment file for deployment '${deployment}'..."

  if [[ ! -f "${_source_env}" ]]; then
    echo "[ERROR] Source .env file not found: ${_source_env}"
    exit 1
  fi
  if [[ ! -f "${_overrides_env}" ]]; then
    echo "[ERROR] Overrides env file not found: ${_overrides_env}"
    exit 1
  fi

  cp "${_overrides_env}" "${_generated_env}"
  echo "[INFO] Copied ${_overrides_env} to ${_generated_env}"

  ensure_generated_env_trailing_newline() {
    if [[ -s "${_generated_env}" ]] && [[ "$(tail -c 1 "${_generated_env}" | wc -l)" -eq 0 ]]; then
      printf '\n' >> "${_generated_env}"
    fi
  }
  ensure_generated_env_trailing_newline

  if [[ "${deployment}" == "warehouse" ]] \
    && { [[ "${bp_profile}" == "bp_wh_auto_calib" ]] || [[ "${mode}" == "auto-calibration" ]]; }; then
    disable_sdrc_routing_in_env "${_generated_env}"
  fi

  # Append compose-wide defaults for variables not already defined in the profile
  local _compose_defaults="${deployment_directory}/services/vios/compose-defaults.env"
  if [[ -f "${_compose_defaults}" ]]; then
    while IFS= read -r line || [[ -n "${line}" ]]; do
      [[ "${line}" =~ ^[[:space:]]*# ]] && continue
      [[ -z "${line// }" ]] && continue
      local _var_name="${line%%=*}"
      if ! env_var_defined_in_files "${_var_name}" "${_source_env}" "${_generated_env}"; then
        echo "${line}" >> "${_generated_env}"
      fi
    done < "${_compose_defaults}"
  fi

  set_env_var() {
    local var_name="${1}"
    local var_value="${2}"
    local mask="${3:-false}"
    local display_value="${var_value}"
    if [[ "${mask}" == "true" ]]; then
      display_value="$(mask_secret "${var_value}")"
    fi
    if grep -q "^${var_name}=" "${_generated_env}"; then
      sed -i "s|^${var_name}=.*|${var_name}=${var_value}|" "${_generated_env}"
    elif grep -Eq "^#[[:space:]]*${var_name}=" "${_generated_env}"; then
      sed -i -E "s|^#[[:space:]]*${var_name}=.*|${var_name}=${var_value}|" "${_generated_env}"
    else
      echo "${var_name}=${var_value}" >> "${_generated_env}"
    fi
    echo "[INFO] Set ${var_name}=${display_value}"
  }

  # Push the clamped placement into generated.env, the last --env-file and so
  # the one that wins Compose interpolation over the deployment's stable .env.
  # Only keys whose value actually moved are written, so a host with enough GPUs
  # emits nothing here and renders byte-identically. LLM_DEVICE_ID and
  # VLM_DEVICE_ID are written further down from the same clamped variables.
  if [[ "${device_clamp_applied}" == "true" ]]; then
    local _clamp_key _clamp_committed _clamp_effective
    for _clamp_key in SHARED_LLM_VLM_DEVICE_ID RT_VLM_DEVICE_ID RT_CV_DEVICE_ID RT_EMBED_DEVICE_ID FIXED_SHARED_DEVICE_IDS RESERVED_DEVICE_IDS; do
      _clamp_committed="$(get_env_value_from_files "${_clamp_key}" "${_source_env}" "${_overrides_env}")"
      _clamp_committed="${_clamp_committed// /}"
      case "${_clamp_key}" in
        SHARED_LLM_VLM_DEVICE_ID) _clamp_effective="${shared_llm_vlm_device_id}" ;;
        RT_VLM_DEVICE_ID) _clamp_effective="${rt_vlm_device_id}" ;;
        RT_CV_DEVICE_ID) _clamp_effective="${rt_cv_device_id}" ;;
        RT_EMBED_DEVICE_ID) _clamp_effective="${rt_embed_device_id}" ;;
        FIXED_SHARED_DEVICE_IDS) _clamp_effective="${fixed_shared_device_ids}" ;;
        RESERVED_DEVICE_IDS) _clamp_effective="${reserved_device_ids}" ;;
      esac
      if [[ "${_clamp_committed}" != "${_clamp_effective}" ]]; then
        set_env_var "${_clamp_key}" "${_clamp_effective}"
      fi
    done
  fi

  set_env_var "VSS_APPS_DIR" "${deployment_directory}"
  set_env_var "VSS_DATA_DIR" "${data_directory}"
  set_env_var "HOST_IP" "${host_ip}"
  if [[ -n "${external_ip}" ]]; then
    set_env_var "EXTERNAL_IP" "${external_ip}" "true"
  fi
  set_env_var "NGC_CLI_API_KEY" "${ngc_cli_api_key}" "true"
  if [[ -n "${mode}" ]]; then
    set_env_var "MODE" "${mode}"
  fi
  if [[ -n "${bp_profile}" ]]; then
    set_env_var "BP_PROFILE" "${bp_profile}"
  fi
  if [[ -n "${elasticsearch_mode}" ]]; then
    set_env_var "ELASTICSEARCH_MODE" "${elasticsearch_mode}"
  fi

  # HARDWARE_PROFILE from -H / warehouse .env (all modes: 2d, 3d, mv3dt; all bp profiles)
  if [[ "${deployment}" == "warehouse" ]] && [[ -n "${hardware_profile}" ]]; then
    set_env_var "HARDWARE_PROFILE" "${hardware_profile}"
  fi

  # Warehouse 3d/mv3dt, kafka/redis, and auto-calibration: no local NIM LLM/VLM
  if [[ "${deployment}" == "warehouse" ]] && { [[ "${mode}" == "3d" ]] || [[ "${mode}" == "mv3dt" ]] || [[ "${mode}" == "auto-calibration" ]] || [[ "${bp_profile}" == "bp_wh_kafka" ]] || [[ "${bp_profile}" == "bp_wh_redis" ]] || [[ "${bp_profile}" == "bp_wh_auto_calib" ]]; }; then
    set_env_var "LLM_MODE" "none"
    set_env_var "VLM_MODE" "none"
    set_env_var "LLM_NAME_SLUG" "none"
    set_env_var "VLM_NAME_SLUG" "none"
  fi

  # LLM/VLM configuration for 2d only: warehouse bp_wh (NIM + agents)
  if [[ "${mode}" == "2d" ]] && [[ "${deployment}" == "warehouse" ]] && [[ "${bp_profile}" == "bp_wh" ]]; then
    local _llm_mode _vlm_mode
    if [[ -n "${llm_base_url}" ]] || contains_element "use-remote-llm" "${options_provided[@]}"; then
      _llm_mode="remote"
    else
      _llm_mode="$(get_env_value_from_files "LLM_MODE" "${_source_env}" "${_overrides_env}")"
      _llm_mode="${_llm_mode:-local}"
    fi
    if [[ -n "${vlm_base_url}" ]] || contains_element "use-remote-vlm" "${options_provided[@]}"; then
      _vlm_mode="remote"
    else
      _vlm_mode="$(get_env_value_from_files "VLM_MODE" "${_source_env}" "${_overrides_env}")"
      _vlm_mode="${_vlm_mode:-local}"
    fi
    set_env_var "LLM_MODE" "${_llm_mode}"
    set_env_var "VLM_MODE" "${_vlm_mode}"
    if [[ "${_llm_mode}" == "remote" ]] && [[ -n "${llm_base_url}" ]]; then
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
      if [[ "${_llm_mode}" == "remote" ]]; then
        # A remote endpoint serves whatever model id it likes, so the local
        # allowlist must not apply. LLM_MODE=remote can come from the profile env
        # files rather than --use-remote-llm, in which case llm_base_url is empty
        # and this branch, not the one above, is the one taken.
        set_env_var "LLM_NAME" "${llm}"
        set_env_var "LLM_NAME_SLUG" "none"
      else
        # Already validated in process_args, which runs before state_down so an
        # invalid name cannot tear down a healthy deployment first.
        set_env_var "LLM_NAME" "${llm}"
        set_env_var "LLM_NAME_SLUG" "$(get_llm_slug "${llm}")"
      fi
    fi
    if [[ "${_vlm_mode}" == "remote" ]] && [[ -n "${vlm_base_url}" ]]; then
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
    if [[ "${_llm_mode}" != "remote" ]] && [[ -n "${llm_device_id}" ]]; then
      set_env_var "LLM_DEVICE_ID" "${llm_device_id}"
    fi
    if [[ "${_vlm_mode}" != "remote" ]] && [[ -n "${vlm_device_id}" ]]; then
      set_env_var "VLM_DEVICE_ID" "${vlm_device_id}"
    fi
    # RTVI local VLM sizing for RTXPRO4500BW (same as dev-profile.sh).
    # Remote VLM does not host the model locally.
    if [[ "${_vlm_mode}" != "remote" ]]; then
      local _rtvi_vllm_gpu_memory_utilization _rtvi_vlm_max_model_len
      _rtvi_vllm_gpu_memory_utilization="$(get_rtvi_vllm_gpu_memory_utilization "${hardware_profile}")"
      if [[ -n "${_rtvi_vllm_gpu_memory_utilization}" ]]; then
        set_env_var "RTVI_VLLM_GPU_MEMORY_UTILIZATION" "${_rtvi_vllm_gpu_memory_utilization}"
      fi
      _rtvi_vlm_max_model_len="$(get_rtvi_vlm_max_model_len "${hardware_profile}")"
      if [[ -n "${_rtvi_vlm_max_model_len}" ]]; then
        set_env_var "RTVI_VLM_MAX_MODEL_LEN" "${_rtvi_vlm_max_model_len}"
      fi
    fi
    if [[ -n "${llm_base_url}" ]]; then
      set_env_var "LLM_BASE_URL" "${llm_base_url}"
    fi
    if [[ -n "${vlm_base_url}" ]]; then
      set_env_var "VLM_BASE_URL" "${vlm_base_url}"
      set_env_var "RTVI_VLM_ENDPOINT" "${vlm_base_url}/v1"
      set_env_var "RTVI_VLM_MODEL_PATH" "none"
    fi
    if [[ "${_llm_mode}" == "remote" ]] && [[ -n "${llm_model_type}" ]]; then
      set_env_var "LLM_MODEL_TYPE" "${llm_model_type}"
    fi
    if [[ "${_vlm_mode}" == "remote" ]] && [[ -n "${vlm_model_type}" ]]; then
      set_env_var "VLM_MODEL_TYPE" "${vlm_model_type}"
    fi
    if [[ -n "${nvidia_api_key}" ]]; then
      set_env_var "NVIDIA_API_KEY" "${nvidia_api_key}" "true"
    fi
    if [[ -n "${openai_api_key}" ]]; then
      set_env_var "OPENAI_API_KEY" "${openai_api_key}" "true"
    fi
    if [[ -n "${llm_env_file}" ]]; then
      set_env_var "LLM_ENV_FILE" "${llm_env_file}"
    fi
    if [[ -n "${vlm_env_file}" ]]; then
      set_env_var "VLM_ENV_FILE" "${vlm_env_file}"
    fi
  fi

  # Warehouse: bp-configurator uses generated.env (required vars from blueprint-deploy)
  if [[ "${deployment}" == "warehouse" ]]; then
    set_env_var "BP_CONFIGURATOR_ENV_FILE" "${_generated_env}"
    # STREAM_TYPE: redis for bp_wh_redis; kafka for bp_wh, bp_wh_kafka, bp_wh_auto_calib (auto_calib skips broker in compose)
    if [[ "${bp_profile}" == "bp_wh_redis" ]]; then
      set_env_var "STREAM_TYPE" "redis"
    else
      set_env_var "STREAM_TYPE" "kafka"
    fi
    # SAMPLE_VIDEO_DATASET and NUM_STREAMS per mode+profile (see warehouse .env comments)
    local _sample_dataset _num_streams
    if [[ -n "${sample_video_dataset}" ]]; then
      _sample_dataset="${sample_video_dataset}"
      _num_streams="$(get_env_value_from_files "NUM_STREAMS" "${_source_env}" "${_overrides_env}")"
      _num_streams="${_num_streams:-$(warehouse_num_streams "${mode}" "${bp_profile}")}"
    else
      _sample_dataset="$(warehouse_sample_video_dataset "${mode}" "${bp_profile}")"
      _num_streams="$(warehouse_num_streams "${mode}" "${bp_profile}")"
    fi
    set_env_var "SAMPLE_VIDEO_DATASET" "${_sample_dataset}"
    set_env_var "NUM_STREAMS" "${_num_streams}"

    # -p/-m select the compose list; copy of overrides.env is rewritten here.
    if [[ -z "${compose_profiles_selector}" ]]; then
      compose_profiles_selector="$(warehouse_compose_profiles_selector "${_source_env}" "${_overrides_env}")"
    fi
    set_env_var "COMPOSE_PROFILES" "\${${compose_profiles_selector}}"
    echo "[INFO] Warehouse COMPOSE_PROFILES=\${${compose_profiles_selector}}"
  fi

  if [[ "${hardware_profile}" == "DGX-SPARK" ]]; then
    apply_sbsa_image_tags_to_env "${_generated_env}" "DGX-SPARK"
  elif [[ "${use_sbsa_images}" == "true" ]]; then
    apply_sbsa_image_tags_to_env "${_generated_env}" "${hardware_profile:-OTHER} (--use-sbsa-images)"
  fi

  echo "[INFO] Generated environment file: ${_generated_env}"

  echo "[INFO] Creating data directories..."
  mkdir -p "${data_directory}/data_log/analytics_cache"
  mkdir -p "${data_directory}/data_log/calibration_toolkit"
  mkdir -p "${data_directory}/data_log/elastic/data"
  mkdir -p "${data_directory}/data_log/elastic/logs"
  mkdir -p "${data_directory}/data_log/kafka"
  mkdir -p "${data_directory}/data_log/redis/data"
  mkdir -p "${data_directory}/data_log/redis/log"
  mkdir -p "${data_directory}/data_log/nvstreamer/vst_data"
  mkdir -p "${data_directory}/data_log/vss_video_analytics_api"

  if [[ "${deployment}" == "warehouse" ]]; then
    local _sample_dataset
    if [[ -n "${sample_video_dataset}" ]]; then
      _sample_dataset="${sample_video_dataset}"
    else
      _sample_dataset="$(warehouse_sample_video_dataset "${mode}" "${bp_profile}")"
    fi
    mkdir -p "${data_directory}/videos/${_sample_dataset}"
    mkdir -p "${data_directory}/playback"
    mkdir -p "${data_directory}/models"
    chmod -R 777 "${data_directory}/models" 2>/dev/null || true
    if [[ "${bp_profile}" != "bp_wh_auto_calib" ]]; then
      echo "[INFO] Warehouse RT-CV model download runs in ds-start phase 0 (perception / ds-start-mv3dt)."
    fi
  fi

  echo "[INFO] Setting permissions on data_log directory..."
  chmod -R 777 "${data_directory}/data_log" 2>/dev/null || true

  local _compose_file_args=(-f compose.yml -f services/infra/compose-no-turn-tcp-relay.yml)
  local _compose_file_args_text=" ${_compose_file_args[*]}"
  echo "[INFO] TURN TCP relay host-port publishing disabled for blueprint-deploy.sh"

  # Resolve and display the managed container channel before deployment.
  set -a
  # shellcheck disable=SC1091
  source "${deployment_directory}/containers.env"
  set +a
  echo "[INFO] Managed container registry: ${VSS_CONTAINER_REGISTRY}"
  echo "[INFO] Managed container tag:      ${VSS_CONTAINER_TAG}"
  echo "[INFO] Resolved compose images:"
  (
    cd "${deployment_directory}"
    docker compose \
      "${_compose_file_args[@]}" \
      --env-file containers.env \
      --env-file "${_deploy_rel}/.env" \
      --env-file "${_deploy_rel}/generated.env" \
      config --images | sort -u
  )

  echo "[INFO] Logging into nvcr.io..."
  if [[ "${dry_run}" == "true" ]]; then
    echo "[DRY-RUN] docker login --username '\$oauthtoken' --password <ngc-cli-api-key> nvcr.io"
  else
    docker login \
      --username '$oauthtoken' \
      --password "${ngc_cli_api_key}" \
      nvcr.io 2>/dev/null || echo "[WARN] Docker login to nvcr.io may have failed (required for pulling images)"
  fi

  echo "[INFO] Starting docker compose..."
  if [[ "${dry_run}" == "true" ]]; then
    echo "[DRY-RUN] cd ${deployment_directory} && docker compose${_compose_file_args_text} --env-file containers.env --env-file ${_deploy_rel}/.env --env-file ${_deploy_rel}/generated.env up --detach --force-recreate --build"
  else
    if ! (
      cd "${deployment_directory}" && docker compose \
        "${_compose_file_args[@]}" \
        --env-file containers.env \
        --env-file "${_deploy_rel}/.env" \
        --env-file "${_deploy_rel}/generated.env" \
        up \
        --detach \
        --force-recreate \
        --build
    ); then
      echo "[ERROR] docker compose up failed for deployment '${deployment}'"
      return 1
    fi
  fi

  echo "[INFO] State up completed"
}

# Revert each original file from its oldest backup (*.backup_*), same roots/order as cleanup_all_datalog.sh.
# When the configurator runs multiple times, the oldest backup holds the original content.
#
# Blueprint-configurator names backups: {stem}.backup_YYYYMMDD_HHMMSS{suffix}
# (see blueprint-configurator profile_config_manager._create_backup). Do not use
# ${_path%.backup_*} — that strips through the final suffix and drops the original extension
# (e.g. cfg.backup_TS.json incorrectly becomes cfg instead of cfg.json).
function run_revert_from_oldest_backup() {
  local _sudo="${1}"
  local _search_dir _backup_path _base _oldest _dir _fn _ost _oex _glob
  local -A _seen_base
  local -a _revert_roots

  _revert_roots=("${data_directory}" "$(dirname "${script_dir}")")
  if [[ -n "${VSS_APPS_DIR:-}" && -d "${VSS_APPS_DIR}" ]]; then
    _revert_roots+=("${VSS_APPS_DIR}")
  fi

  for _search_dir in "${_revert_roots[@]}"; do
    [[ ! -d "${_search_dir}" ]] && continue
    _seen_base=()
    while IFS= read -r _backup_path; do
      [[ -z "${_backup_path}" ]] && continue
      _base="$(sed -E 's/\.backup_[0-9]{8}_[0-9]{6}//' <<< "${_backup_path}" | tr -d '\n')"
      [[ "${_base}" == "${_backup_path}" ]] && continue
      [[ -n "${_seen_base[${_base}]:-}" ]] && continue
      _seen_base["${_base}"]=1
      _dir="$(dirname "${_base}")"
      _fn="$(basename "${_base}")"
      if [[ "${_fn}" == *.* ]]; then
        _ost="${_fn%.*}"
        _oex=".${_fn##*.}"
      else
        _ost="${_fn}"
        _oex=""
      fi
      _glob="${_dir}/${_ost}.backup_*${_oex}"
      _oldest=$($_sudo find "${_search_dir}" -type f -path "${_glob}" 2>/dev/null | sort | head -1)
      if [[ -n "${_oldest}" && -f "${_oldest}" ]]; then
        echo "[INFO] Reverting ${_base} from oldest backup: ${_oldest}"
        if ! $_sudo cp "${_oldest}" "${_base}"; then
          echo "[ERROR] Failed to revert ${_base} from ${_oldest}; backup will NOT be deleted for this file" >&2
          continue
        fi
      fi
    done < <($_sudo find "${_search_dir}" -type f -name '*.backup_*' 2>/dev/null)
  done
}

# Clean data_log contents (matches cleanup_all_datalog.sh behavior)
# Revert (optional) then: kafka, elastic, redis, vst, nvstreamer, vss_video_analytics_api, calibration_toolkit, backup files
# Backup files: same roots as cleanup_all_datalog.sh (data dir, repo root, optional VSS_APPS_DIR), with sudo when needed
function run_data_log_cleanup() {
  local _data_dir="${data_directory}"
  if [[ ! -d "${_data_dir}" ]]; then
    echo "[INFO] Data directory does not exist, skipping data_log cleanup"
    return
  fi
  # Use sudo only when not already root (CI containers run as root without sudo installed).
  local _sudo=""
  if [[ "$(id -u)" -ne 0 ]] && command -v sudo >/dev/null 2>&1; then
    _sudo="sudo"
  fi
  if [[ "${dry_run}" == "true" ]]; then
    if [[ "${revert_from_oldest_backup}" == "true" ]]; then
      echo "[DRY-RUN] Would revert live files from oldest *.backup_* under ${data_directory}, $(dirname "${script_dir}"), and VSS_APPS_DIR (if set), then clean data_log and delete backups"
    else
      echo "[DRY-RUN] Skipping revert (--skip-revert-from-oldest-backup); would clean data_log under ${_data_dir} and delete *.backup_*"
    fi
    return
  fi
  if [[ "${revert_from_oldest_backup}" == "true" ]]; then
    echo "[INFO] Reverting originals from oldest blueprint-configurator backups (before data_log cleanup and backup deletion)..."
    run_revert_from_oldest_backup "${_sudo}"
  fi
  # Clear contents of data_log subdirs (same as cleanup_all_datalog.sh)
  for _path in "data_log/kafka" "data_log/elastic/data" "data_log/elastic/logs" \
               "data_log/behavior_learning_data" "data_log/vss_video_analytics_api" \
               "data_log/redis/data" "data_log/redis/log" "data_log/calibration_toolkit" \
               "data_log/analytics_cache"; do
    if [[ -d "${_data_dir}/${_path}" ]]; then
      $_sudo rm -rf "${_data_dir}/${_path}"/* 2>/dev/null || true
    fi
  done
  # Remove vst and nvstreamer dirs entirely
  [[ -d "${_data_dir}/data_log/vst" ]] && $_sudo rm -rf "${_data_dir}/data_log/vst"
  [[ -d "${_data_dir}/data_log/nvstreamer" ]] && $_sudo rm -rf "${_data_dir}/data_log/nvstreamer"
  # Delete blueprint-configurator backup files (*.backup_*), same roots as cleanup_all_datalog.sh:
  # VSS_DATA_DIR (-D), met-blueprints repo root (parent of deploy/docker/), and VSS_APPS_DIR when set.
  local _backup_count _root
  local -a _backup_roots

  _backup_roots=("${_data_dir}" "$(dirname "${script_dir}")")
  if [[ -n "${VSS_APPS_DIR:-}" && -d "${VSS_APPS_DIR}" ]]; then
    _backup_roots+=("${VSS_APPS_DIR}")
  fi

  for _root in "${_backup_roots[@]}"; do
    [[ -d "${_root}" ]] || continue
    _backup_count=$($_sudo find "${_root}" -type f -name '*.backup_*' 2>/dev/null | wc -l)
    if [[ "${_backup_count}" -gt 0 ]]; then
      echo "[INFO] Deleting ${_backup_count} backup file(s) under ${_root}"
      $_sudo find "${_root}" -type f -name '*.backup_*' -print -delete 2>/dev/null || true
    fi
  done
  echo "[INFO] data_log cleanup completed"
}

function state_down() {
  local _deploy_dir_names _deploy_dir_name _deploy_dir _source_env _overrides_env _generated_env

  _deploy_dir_names=('industry-profiles/warehouse-operations')

  local _compose_project_name="${COMPOSE_PROJECT_NAME:-}"
  if [[ -z "${_compose_project_name}" ]]; then
    for _deploy_dir_name in "${_deploy_dir_names[@]}"; do
      _deploy_dir="${deployment_directory}/${_deploy_dir_name}"
      _source_env="${_deploy_dir}/.env"
      _overrides_env="${_deploy_dir}/overrides.env"
      _generated_env="${_deploy_dir}/generated.env"
      _compose_project_name="$(get_env_value_from_files "COMPOSE_PROJECT_NAME" "${_source_env}" "${_overrides_env}" "${_generated_env}")"
      [[ -n "${_compose_project_name}" ]] && break
    done
  fi
  _compose_project_name="${_compose_project_name:-vss}"

  echo "[INFO] Cleaning up generated.env files from warehouse..."
  for _deploy_dir_name in "${_deploy_dir_names[@]}"; do
    _generated_env="${deployment_directory}/${_deploy_dir_name}/generated.env"
    if [[ -f "${_generated_env}" ]]; then
      if [[ "${dry_run}" == "true" ]]; then
        echo "[DRY-RUN] rm -f ${_generated_env}"
      else
        rm -f "${_generated_env}"
        echo "[INFO] Deleted ${_generated_env}"
      fi
    fi
  done

  echo "[INFO] Bringing down docker compose project '${_compose_project_name}' (with volumes)..."
  if [[ "${dry_run}" == "true" ]]; then
    echo "[DRY-RUN] docker compose -p ${_compose_project_name} down -v --remove-orphans"
  else
    docker compose -p "${_compose_project_name}" down -v --remove-orphans
  fi

  echo "[INFO] Removing dangling docker volumes..."
  if [[ "${dry_run}" == "true" ]]; then
    echo "[DRY-RUN] docker volume ls -q -f \"dangling=true\" | xargs docker volume rm"
  else
    dangling_volumes=$(docker volume ls -q -f "dangling=true")
    if [[ -n "${dangling_volumes}" ]]; then
      echo "${dangling_volumes}" | xargs docker volume rm 2>/dev/null || true
    else
      echo "[INFO] No dangling volumes to remove"
    fi
  fi

  echo "[INFO] Cleaning VSS_DATA_DIR data_log (kafka, elastic, redis, vst, nvstreamer, vss_video_analytics_api, etc.)..."
  run_data_log_cleanup

  echo "[INFO] State down completed"
}

# Main execution: normalize argv before getopt (short -h/-n are not in the getopt optstring).
_main_args=()
for _arg in "$@"; do
  case "${_arg}" in
    -h | --help)
      usage
      exit 0
      ;;
    -n)
      _main_args+=("--dry-run")
      ;;
    *)
      _main_args+=("${_arg}")
      ;;
  esac
done

validate_args "${_main_args[@]}"
process_args "${_main_args[@]}"
print_args

if [[ "${desired_state}" == "up" ]]; then
  state_down
  state_up
elif [[ "${desired_state}" == "down" ]]; then
  state_down
fi
