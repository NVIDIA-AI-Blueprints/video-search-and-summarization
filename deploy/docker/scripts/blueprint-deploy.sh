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

# Flags to track explicitly provided options
options_provided=()

# Path under deploy/docker/ where a deployment's .env lives (-d warehouse → industry-profiles/warehouse-operations).
function deployment_rel_path() {
  case "${1}" in
    warehouse) echo "industry-profiles/warehouse-operations" ;;
    *) echo "${1}" ;;
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
    _val="${_val#[\'\"]}"
    _val="${_val%[\'\"]}"
    echo "${_val}"
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
  echo "  -m, --mode                       Deployment mode."
  echo "                                   • Default: 2d"
  echo "                                   • Pass -m 3d for 3D deployment"
  echo "  -p, --bp-profile                Blueprint profile."
  echo "                                   • bp_wh (default), bp_wh_kafka, bp_wh_redis"
  echo "  -i, --host-ip                    Host IP."
  echo "                                   • Default: primary IP from ip route"
  echo "  -e, --external-ip                Externally accessible IP."
  echo "  -D, --data-dir PATH             [REQUIRED] Path for sample data (VSS_DATA_DIR/MDX_DATA_DIR)."
  echo "                                   • Where warehouse sample data tar is extracted"
  echo "                                   • Contains: models, videos, data_log, playback"
  echo "                                   • Also required for 'down' (same path used with 'up')"
  echo "  -E, --es, --elasticsearch-mode  Elasticsearch mode: gpu (GPU-accelerated) or cpu (CPU-only)."
  echo "                                   • Default: cpu"
  echo "  -s, --sample-video-dataset      [Warehouse only] Override sample video dataset."
  echo "                                   • Default by mode+profile:"
  echo "                                     2d+bp_wh: nv-warehouse-4cams (4 streams)"
  echo "                                     2d+bp_wh_kafka/bp_wh_redis: warehouse-loading-dock-3cams-synthetic (3 streams)"
  echo "                                     3d: warehouse-4cams-20mx20m-synthetic (4 streams)"
  echo ""
  echo "  [LLM/VLM - for 2d only: warehouse bp_wh (NIM + agents)]"
  echo "  -H, --hardware-profile          H100, L40S, RTXPRO6000BW, DGX-SPARK, etc."
  echo "  --llm                           LLM model (e.g. nvidia/nvidia-nemotron-nano-9b-v2)"
  echo "  --vlm                           VLM model (e.g. nvidia/cosmos-reason2-8b)"
  echo "  --llm-device-id                 GPU device ID for LLM"
  echo "  --vlm-device-id                 GPU device ID for VLM"
  echo "  --use-remote-llm                Use remote LLM (LLM_ENDPOINT_URL)"
  echo "  --use-remote-vlm               Use remote VLM (VLM_ENDPOINT_URL)"
  echo "  --llm-model-type               nim or openai (when --use-remote-llm)"
  echo "  --vlm-model-type               nim or openai (when --use-remote-vlm)"
  echo "  --llm-env-file                 Path to LLM env file"
  echo "  --vlm-env-file                 Path to VLM env file"
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

function validate_args() {
  local _args _valid_args _all_good
  _args=("${@}")
  _all_good=0

  _valid_args=$(getopt -q -o d:m:p:H:i:e:s:D:n:h:E: --long deployment:,mode:,bp-profile:,hardware-profile:,host-ip:,external-ip:,sample-video-dataset:,elasticsearch-mode:,es:,llm:,vlm:,llm-device-id:,vlm-device-id:,use-remote-llm,use-remote-vlm,llm-model-type:,vlm-model-type:,llm-env-file:,vlm-env-file:,data-dir:,data-directory:,dry-run,skip-revert-from-oldest-backup,help -- "${_args[@]}")
  if [[ $? -ne 0 ]]; then
    echo "[ERROR] Invalid usage: ${_args[*]}"
    ((_all_good++))
  else
    eval set -- "${_valid_args}"

    while true; do
      case "${1}" in
        -h | --help) usage; exit 0 ;;
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

  _valid_args=$(getopt -q -o d:m:p:H:i:e:s:D:n:h:E: --long deployment:,mode:,bp-profile:,hardware-profile:,host-ip:,external-ip:,sample-video-dataset:,elasticsearch-mode:,es:,llm:,vlm:,llm-device-id:,vlm-device-id:,use-remote-llm,use-remote-vlm,llm-model-type:,vlm-model-type:,llm-env-file:,vlm-env-file:,data-dir:,data-directory:,dry-run,skip-revert-from-oldest-backup,help -- "${_args[@]}")
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
      -n | --dry-run)
        dry_run="true"
        options_provided+=("dry-run")
        shift
        ;;
      --skip-revert-from-oldest-backup)
        revert_from_oldest_backup="false"
        options_provided+=("skip-revert-from-oldest-backup")
        shift
        ;;
      -h | --help)
        shift
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
      if [[ ! -f "${_deploy_env}" ]]; then
        echo "[ERROR] Deployment .env file not found: ${_deploy_env}"
        ((_all_good++))
      fi
    fi

    if [[ -n "${deployment}" ]] && [[ -f "${deployment_directory}/$(deployment_rel_path "${deployment}")/.env" ]]; then
      local _deploy_env="${deployment_directory}/$(deployment_rel_path "${deployment}")/.env"

      # Mode: default 2d; warehouse can pass 3d
      if ! contains_element "mode" "${options_provided[@]}"; then
        mode="2d"
      fi
      # Profile: warehouse default bp_wh
      if ! contains_element "bp-profile" "${options_provided[@]}"; then
        bp_profile="bp_wh"
      fi
      # LLM/VLM: populate from .env when not provided (2d only: warehouse bp_wh)
      if [[ "${mode}" == "2d" ]] && [[ "${deployment}" == "warehouse" ]] && [[ "${bp_profile}" == "bp_wh" ]]; then
        if ! contains_element "hardware-profile" "${options_provided[@]}"; then
          hardware_profile="$(get_env_value "${_deploy_env}" "HARDWARE_PROFILE")"
        fi
        if ! contains_element "llm-device-id" "${options_provided[@]}"; then
          llm_device_id="$(get_env_value "${_deploy_env}" "LLM_DEVICE_ID")"
        fi
        if ! contains_element "vlm-device-id" "${options_provided[@]}"; then
          vlm_device_id="$(get_env_value "${_deploy_env}" "VLM_DEVICE_ID")"
        fi
        if ! contains_element "llm-model-type" "${options_provided[@]}"; then
          llm_model_type="$(get_env_value "${_deploy_env}" "LLM_MODEL_TYPE")"
        fi
        if ! contains_element "vlm-model-type" "${options_provided[@]}"; then
          vlm_model_type="$(get_env_value "${_deploy_env}" "VLM_MODEL_TYPE")"
        fi
      fi

      if [[ "${deployment}" == "warehouse" ]]; then
        _valid_modes=('2d' '3d')
        if [[ -n "${mode}" ]] && ! contains_element "${mode}" "${_valid_modes[@]}"; then
          echo "[ERROR] Invalid mode: ${mode}. Must be one of: 2d, 3d"
          ((_all_good++))
        fi
        _valid_wh_profiles=('bp_wh' 'bp_wh_kafka' 'bp_wh_redis')
        if [[ -n "${bp_profile}" ]] && ! contains_element "${bp_profile}" "${_valid_wh_profiles[@]}"; then
          echo "[ERROR] Invalid bp-profile for warehouse: ${bp_profile}. Must be one of: bp_wh, bp_wh_kafka, bp_wh_redis"
          ((_all_good++))
        fi
      fi
      # Elasticsearch mode: default cpu; populate from .env when not provided
      if ! contains_element "elasticsearch-mode" "${options_provided[@]}"; then
        elasticsearch_mode="$(get_env_value "${_deploy_env}" "ELASTICSEARCH_MODE")"
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
    fi

    if [[ "${mode}" == "2d" ]] && [[ "${deployment}" == "warehouse" ]] && [[ "${bp_profile}" == "bp_wh" ]]; then
      if [[ -z "${ngc_cli_api_key}" ]]; then
        local _llm_mode _vlm_mode
        if contains_element "use-remote-llm" "${options_provided[@]}"; then
          _llm_mode="remote"
        else
          _llm_mode="$(get_env_value "${deployment_directory}/$(deployment_rel_path "${deployment}")/.env" "LLM_MODE")"
        fi
        if contains_element "use-remote-vlm" "${options_provided[@]}"; then
          _vlm_mode="remote"
        else
          _vlm_mode="$(get_env_value "${deployment_directory}/$(deployment_rel_path "${deployment}")/.env" "VLM_MODE")"
        fi
        if [[ "${_llm_mode}" =~ ^(local|local_shared)$ ]] || [[ "${_vlm_mode}" =~ ^(local|local_shared)$ ]]; then
          echo "[ERROR] NGC_CLI_API_KEY is required for 'up' when LLM_MODE or VLM_MODE is local or local_shared (warehouse bp_wh)"
          ((_all_good++))
        fi
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
    echo "elasticsearch-mode:       ${elasticsearch_mode}"
    if [[ "${deployment}" == "warehouse" ]] && [[ -n "${sample_video_dataset}" ]]; then
      echo "sample-video-dataset:      ${sample_video_dataset}"
    fi
    if [[ "${mode}" == "2d" ]] && [[ "${deployment}" == "warehouse" ]] && [[ "${bp_profile}" == "bp_wh" ]]; then
      [[ -n "${hardware_profile}" ]] && echo "hardware-profile:          ${hardware_profile}"
      [[ -n "${llm}" ]] && echo "llm:                       ${llm}"
      [[ -n "${vlm}" ]] && echo "vlm:                       ${vlm}"
      [[ -n "${llm_device_id}" ]] && echo "llm-device-id:             ${llm_device_id}"
      [[ -n "${vlm_device_id}" ]] && echo "vlm-device-id:             ${vlm_device_id}"
      contains_element "use-remote-llm" "${options_provided[@]}" && echo "use-remote-llm:            true"
      contains_element "use-remote-vlm" "${options_provided[@]}" && echo "use-remote-vlm:            true"
    fi
    echo "host-ip:                  ${host_ip}"
    if [[ -n "${external_ip}" ]]; then
      echo "external-ip:               ${external_ip}"
    fi
    echo "ngc-cli-api-key:          $(mask_secret "${ngc_cli_api_key}")"
  fi
  echo "=========================="
}

function state_up() {
  local _deploy_rel _deploy_dir _source_env _generated_env
  _deploy_rel="$(deployment_rel_path "${deployment}")"
  _deploy_dir="${deployment_directory}/${_deploy_rel}"
  _source_env="${_deploy_dir}/.env"
  _generated_env="${_deploy_dir}/generated.env"

  echo "[INFO] Generating environment file for deployment '${deployment}'..."

  if [[ ! -f "${_source_env}" ]]; then
    echo "[ERROR] Source .env file not found: ${_source_env}"
    exit 1
  fi

  cp "${_source_env}" "${_generated_env}"
  echo "[INFO] Copied ${_source_env} to ${_generated_env}"

  # Append compose-wide defaults for variables not already defined in the profile
  local _compose_defaults="${deployment_directory}/vst/compose-defaults.env"
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

  set_env_var "VSS_APPS_DIR" "${deployment_directory}"
  set_env_var "MDX_SAMPLE_APPS_DIR" "${deployment_directory}"
  set_env_var "VSS_DATA_DIR" "${data_directory}"
  set_env_var "MDX_DATA_DIR" "${data_directory}"
  set_env_var "HOST_IP" "${host_ip}"
  if [[ -n "${external_ip}" ]]; then
    set_env_var "EXTERNAL_IP" "${external_ip}"
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

  # Warehouse 3d: LLM/VLM not used; set to none
  if [[ "${deployment}" == "warehouse" ]] && { [[ "${mode}" == "3d" ]] || [[ "${bp_profile}" == "bp_wh_kafka" ]] || [[ "${bp_profile}" == "bp_wh_redis" ]]; }; then
    set_env_var "LLM_MODE" "none"
    set_env_var "VLM_MODE" "none"
  fi

  # LLM/VLM configuration for 2d only: warehouse bp_wh (NIM + agents)
  if [[ "${mode}" == "2d" ]] && [[ "${deployment}" == "warehouse" ]] && [[ "${bp_profile}" == "bp_wh" ]]; then
    local _llm_mode _vlm_mode
    if [[ -n "${llm_base_url}" ]] || contains_element "use-remote-llm" "${options_provided[@]}"; then
      _llm_mode="remote"
    else
      _llm_mode="$(get_env_value "${_source_env}" "LLM_MODE")"
      _llm_mode="${_llm_mode:-local}"
    fi
    if [[ -n "${vlm_base_url}" ]] || contains_element "use-remote-vlm" "${options_provided[@]}"; then
      _vlm_mode="remote"
    else
      _vlm_mode="$(get_env_value "${_source_env}" "VLM_MODE")"
      _vlm_mode="${_vlm_mode:-local}"
    fi
    set_env_var "LLM_MODE" "${_llm_mode}"
    set_env_var "VLM_MODE" "${_vlm_mode}"
    if [[ -n "${hardware_profile}" ]]; then
      set_env_var "HARDWARE_PROFILE" "${hardware_profile}"
    fi
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
      set_env_var "LLM_NAME" "${llm}"
      set_env_var "LLM_NAME_SLUG" "$(get_llm_slug "${llm}")"
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
    # STREAM_TYPE: redis only for bp_wh_redis; kafka for bp_wh and bp_wh_kafka
    if [[ "${bp_profile}" == "bp_wh_redis" ]]; then
      set_env_var "STREAM_TYPE" "redis"
    else
      set_env_var "STREAM_TYPE" "kafka"
    fi
    # SAMPLE_VIDEO_DATASET and NUM_STREAMS per mode+profile (see warehouse .env comments)
    local _sample_dataset _num_streams
    if [[ -n "${sample_video_dataset}" ]]; then
      _sample_dataset="${sample_video_dataset}"
      _num_streams="$(get_env_value "${_source_env}" "NUM_STREAMS")"
      _num_streams="${_num_streams:-4}"
    elif [[ "${mode}" == "3d" ]]; then
      _sample_dataset="warehouse-4cams-20mx20m-synthetic"
      _num_streams="4"
    elif [[ "${bp_profile}" == "bp_wh" ]]; then
      _sample_dataset="nv-warehouse-4cams"
      _num_streams="4"
    else
      _sample_dataset="warehouse-loading-dock-3cams-synthetic"
      _num_streams="3"
    fi
    set_env_var "SAMPLE_VIDEO_DATASET" "${_sample_dataset}"
    set_env_var "NUM_STREAMS" "${_num_streams}"

    # Select the appropriate COMPOSE_PROFILES variable based on BP_PROFILE, MODE, and MINIMAL_PROFILE
    local _minimal_profile _cp_var
    _minimal_profile="$(get_env_value "${_source_env}" "MINIMAL_PROFILE")"
    case "${bp_profile}_${mode}" in
      bp_wh_2d)       _cp_var="COMPOSE_PROFILES_WH_2D" ;;
      bp_wh_kafka_2d) _cp_var="COMPOSE_PROFILES_WH_KAFKA_2D" ;;
      bp_wh_redis_2d) _cp_var="COMPOSE_PROFILES_WH_REDIS_2D" ;;
      bp_wh_kafka_3d) _cp_var="COMPOSE_PROFILES_WH_KAFKA_3D" ;;
      bp_wh_redis_3d) _cp_var="COMPOSE_PROFILES_WH_REDIS_3D" ;;
      *)
        echo "[ERROR] Unknown bp_profile/mode combination: ${bp_profile}/${mode}"
        return 1
        ;;
    esac
    if [[ -n "${_minimal_profile}" ]] && [[ "${bp_profile}" != "bp_wh" ]]; then
      _cp_var="${_cp_var}_MINIMAL"
    fi
    set_env_var "COMPOSE_PROFILES" "\${${_cp_var}}"
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
    local _sample_dataset _num_streams
    if [[ -n "${sample_video_dataset}" ]]; then
      _sample_dataset="${sample_video_dataset}"
    elif [[ "${mode}" == "3d" ]]; then
      _sample_dataset="warehouse-4cams-20mx20m-synthetic"
    elif [[ "${bp_profile}" == "bp_wh" ]]; then
      _sample_dataset="nv-warehouse-4cams"
    else
      _sample_dataset="warehouse-loading-dock-3cams-synthetic"
    fi
    mkdir -p "${data_directory}/videos/${_sample_dataset}"
    mkdir -p "${data_directory}/playback"
  fi

  echo "[INFO] Setting permissions on data_log directory..."
  chmod -R 777 "${data_directory}/data_log" 2>/dev/null || true

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
    echo "[DRY-RUN] cd ${deployment_directory} && docker compose --env-file ${_deploy_rel}/generated.env up --detach --force-recreate --build"
  else
    cd "${deployment_directory}" && docker compose \
      --env-file "${_deploy_rel}/generated.env" \
      up \
      --detach \
      --force-recreate \
      --build
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
  if [[ -n "${MDX_SAMPLE_APPS_DIR:-}" && -d "${MDX_SAMPLE_APPS_DIR}" ]]; then
    _revert_roots+=("${MDX_SAMPLE_APPS_DIR}")
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
# Backup files: same roots as cleanup_all_datalog.sh (data dir, repo root, optional MDX_SAMPLE_APPS_DIR), with sudo when needed
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
      echo "[DRY-RUN] Would revert live files from oldest *.backup_* under ${data_directory}, $(dirname "${script_dir}"), and MDX_SAMPLE_APPS_DIR (if set), then clean data_log and delete backups"
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
  # MDX_DATA_DIR (-D), met-blueprints repo root (parent of deploy/docker/), and MDX_SAMPLE_APPS_DIR when set.
  local _backup_count _root
  local -a _backup_roots

  _backup_roots=("${_data_dir}" "$(dirname "${script_dir}")")
  if [[ -n "${MDX_SAMPLE_APPS_DIR:-}" && -d "${MDX_SAMPLE_APPS_DIR}" ]]; then
    _backup_roots+=("${MDX_SAMPLE_APPS_DIR}")
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
  local _deploy_dir_names _deploy_dir_name _generated_env

  echo "[INFO] Cleaning up generated.env files from warehouse..."
  _deploy_dir_names=('industry-profiles/warehouse-operations')
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
      echo "${dangling_volumes}" | xargs docker volume rm 2>/dev/null || true
    else
      echo "[INFO] No dangling volumes to remove"
    fi
  fi

  echo "[INFO] Cleaning VSS_DATA_DIR data_log (kafka, elastic, redis, vst, nvstreamer, vss_video_analytics_api, etc.)..."
  run_data_log_cleanup

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
