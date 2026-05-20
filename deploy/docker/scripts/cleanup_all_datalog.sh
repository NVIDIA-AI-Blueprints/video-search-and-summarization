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
delete_calibration_data="true"
delete_vst_data="true"
delete_backup_files="true"
revert_from_oldest_backup="true"
env_file="${script_dir}/../.env"

function usage() {
  echo "Usage: ${script_name} (-h|--help)"
  echo "   or: ${script_name} [options]"
  echo ""
  echo "options:"
  echo "-e, --env-file                      path to env file used to start the blueprint"
  echo "-b, --blueprint                     name of the blueprint, one of warehouse / public-safety / smartcities"
  echo "-d, --dev-profile                   name of the dev-profile, one of base / lvs / alerts / search"
  echo "--skip-delete-calibration-data      skip deletion of calibration data"
  echo "--skip-delete-vst-data              skip deletion of vst data"
  echo "--skip-delete-backup-files          skip deletion of blueprint-configurator backup files (*.backup_*) under VSS_DATA_DIR, the met-blueprints repo root, and VSS_APPS_DIR when set"
  echo "--skip-revert-from-oldest-backup    skip reverting from the oldest available blueprint-configurator backup file"
  echo "-h, --help                          provide usage information"
  echo ""
  echo "note: only one of env-file or blueprint should be provided"
  echo ""
}

function process_args() {
  local _args _all_good _valid_args _need_help _env_files
  _args=("${@}")
  _all_good=0
  _need_help="false"
  _env_files=()
  _valid_args=$(getopt -q -o e:b:d:h --long env-file:,blueprint:,dev-profile:,skip-delete-calibration-data,skip-delete-vst-data,skip-delete-backup-files,skip-revert-from-oldest-backup,help -- "${_args[@]}")
  _all_good=$(( _all_good + $? ))
  if [[ _all_good -gt 0 ]]; then
    echo ""
    echo "Invalid usage: ${_args[*]}"
    usage
    exit 1
  else
    eval set -- "${_valid_args}"
    while true; do
      case "${1}" in
        -e | --env-file) shift; _env_files+=("${1}"); shift; ;;
        -b | --blueprint) shift; _env_files+=("${script_dir}/../industry-profiles/${1}/.env"); shift; ;;
        -d | --dev-profile) shift; _env_files+=("${script_dir}/../developer-profiles/dev-profile-${1}/.env"); shift; ;;
        --skip-delete-calibration-data) delete_calibration_data="false"; shift; ;;
        --skip-delete-vst-data) delete_vst_data="false"; shift; ;;
        --skip-delete-backup-files) delete_backup_files="false"; shift; ;;
        --skip-revert-from-oldest-backup) revert_from_oldest_backup="false"; shift; ;;
        -h | --help) _need_help="true"; shift; ;;
        --) shift; break ;;
      esac
    done
  fi
  if [[ ${_need_help} == "true" ]]; then
    echo ""
    usage
    exit 0
  elif [[ "${#_env_files[@]}" -gt 1 ]]; then
    echo ""
    echo "Invalid usage: ${_args[*]}"
    echo "Ambiguous env file: ${_args[*]}"
    usage
    exit 1
  elif [[ "${#_env_files[@]}" -eq 1 ]]; then
    env_file="${_env_files[0]}"
  fi
}

function load_env() {
  # Save pre-existing environment variables
  local _saved_vss_data_dir="${VSS_DATA_DIR}"
  local _saved_vss_apps_dir="${VSS_APPS_DIR}"

  if [[ -f "${env_file}" ]]; then
    source "${env_file}"
    echo "✅ Sourced env file: ${env_file}"

    # Restore pre-existing environment variables if they were set
    if [[ -n "${_saved_vss_data_dir}" ]]; then
      export VSS_DATA_DIR="${_saved_vss_data_dir}"
      echo "Using pre-set exported vars VSS_DATA_DIR: ${VSS_DATA_DIR}"
    fi
    if [[ -n "${_saved_vss_apps_dir}" ]]; then
      export VSS_APPS_DIR="${_saved_vss_apps_dir}"
      echo "Using pre-set exported vars VSS_APPS_DIR: ${VSS_APPS_DIR}"
    fi
  else
    echo "Error: env file '${env_file}' not found"
    exit 1
  fi
}

function info() {
  if [[ -d "${VSS_DATA_DIR}" ]]; then
    echo "Assuming the path of the VSS data dir as: ${VSS_DATA_DIR}"
    if [ "${delete_calibration_data}" == false ]; then
      echo "Calibration data will not be deleted"
    fi
    if [ "${delete_vst_data}" == false ]; then
      echo "VST data will not be deleted"
    fi
    if [ "${delete_backup_files}" == false ]; then
      echo "Blueprint-configurator backup files will not be deleted"
    fi
    if [ "${revert_from_oldest_backup}" == false ]; then
      echo "Revert from oldest backup will be skipped"
    fi
  else
    echo "Error: VSS data dir '${VSS_DATA_DIR}' not found"
  fi
}

# Revert each original file from its oldest backup (*.backup_YYYYMMDD_HHMMSS*).
# When the app runs multiple times, the oldest backup holds the original content.
#
# Blueprint-configurator names backups: {stem}.backup_YYYYMMDD_HHMMSS{suffix}
# (see profile_config_manager._create_backup). Do not use ${_path%.backup_*} — it strips
# the file extension from the restored path (e.g. cfg.backup_TS.json -> cfg instead of cfg.json).
function run_revert_from_oldest_backup() {
  local _search_dir _backup_path _base _oldest _dir _fn _ost _oex _glob
  local -A _seen_base
  local -a _revert_roots

  _revert_roots=("${VSS_DATA_DIR}" "$(dirname "${script_dir}")")
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
      # Oldest backup sorts first (e.g. .backup_20250201_120000 before .backup_20250212_143000)
      _oldest=$(sudo find "${_search_dir}" -type f -path "${_glob}" 2>/dev/null | sort | head -1)
      if [[ -n "${_oldest}" && -f "${_oldest}" ]]; then
        echo "Reverting ${_base} from oldest backup: ${_oldest}"
        sudo cp "${_oldest}" "${_base}"
      fi
    done < <(sudo find "${_search_dir}" -type f -name '*.backup_*' 2>/dev/null)
  done
}

function cleanup() {
  local _vst_volume _nvstreamer_volume

  if [ -d "${VSS_DATA_DIR}/data_log/kafka" ]; then
    sudo rm -rf ${VSS_DATA_DIR}/data_log/kafka/*
  fi

  if [ -d "${VSS_DATA_DIR}/data_log/elastic/data" ]; then
    sudo rm -rf ${VSS_DATA_DIR}/data_log/elastic/data/*
  fi

  if [ -d "${VSS_DATA_DIR}/data_log/elastic/logs" ]; then
    sudo rm -rf ${VSS_DATA_DIR}/data_log/elastic/logs/*
  fi

  if [ -d "${VSS_DATA_DIR}/data_log/behavior_learning_data" ]; then
    sudo rm -rf ${VSS_DATA_DIR}/data_log/behavior_learning_data/*
  fi

  if [ -d "${VSS_DATA_DIR}/data_log/vss_video_analytics_api/" ]; then
    sudo rm -rf ${VSS_DATA_DIR}/data_log/vss_video_analytics_api/*
  fi

  if [ -d "${VSS_DATA_DIR}/data_log/redis/data" ]; then
      sudo rm -rf ${VSS_DATA_DIR}/data_log/redis/data/*
  fi

  if [ -d "${VSS_DATA_DIR}/data_log/redis/log" ]; then
      sudo rm -rf ${VSS_DATA_DIR}/data_log/redis/log/*
  fi

  # Clean up sdrc runtime artifacts (logs and rendered wdm env files)
  local _sdrc_dir="${script_dir}/../services/infra/sdrc"

  if [ -d "${_sdrc_dir}/log" ]; then
      sudo rm -rf "${_sdrc_dir}/log"
  fi

  if [ -d "${_sdrc_dir}/.wdm-env" ]; then
      sudo rm -rf "${_sdrc_dir}/.wdm-env"
  fi

  # Delete render-service generated sdrc config files. Every rendered file in
  # */sdrc/configs/ has a sibling *.tmpl template; remove the rendered sibling
  # so the next run regenerates it cleanly from the template.
  local _docker_dir _tmpl _rendered
  _docker_dir="$(dirname "${script_dir}")"
  while IFS= read -r _tmpl; do
    [[ -z "${_tmpl}" ]] && continue
    _rendered="${_tmpl%.tmpl}"
    if [[ -f "${_rendered}" ]]; then
      echo "Deleting rendered sdrc config: ${_rendered}"
      sudo rm -f "${_rendered}"
    fi
  done < <(find "${_docker_dir}" -type f -path '*/sdrc/configs/*.tmpl' 2>/dev/null)

  if [[ "${delete_calibration_data}" == true ]]; then
      sudo rm -rf ${VSS_DATA_DIR}/data_log/calibration_toolkit/*
  fi

  if [[ "${delete_vst_data}" == true ]]; then
      _vst_volume="${VSS_DATA_DIR}/data_log/vst"

      if [ -d "${_vst_volume}" ]; then
          sudo rm -rf "${_vst_volume}"
      fi

      _nvstreamer_volume="${VSS_DATA_DIR}/data_log/nvstreamer"

      if [ -d "${_nvstreamer_volume}" ]; then
          sudo rm -rf "${_nvstreamer_volume}"
      fi
  fi

  # Delete blueprint-configurator backup files (*.backup_YYYYMMDD_HHMMSS*)
  if [[ "${delete_backup_files}" == true ]]; then
    local _backup_count _root
    local -a _backup_roots

    _backup_roots=("${VSS_DATA_DIR}" "$(dirname "${script_dir}")")
    if [[ -n "${VSS_APPS_DIR:-}" && -d "${VSS_APPS_DIR}" ]]; then
      _backup_roots+=("${VSS_APPS_DIR}")
    fi

    for _root in "${_backup_roots[@]}"; do
      [[ -d "${_root}" ]] || continue
      _backup_count=$(sudo find "${_root}" -type f -name '*.backup_*' 2>/dev/null | wc -l)
      if [[ "${_backup_count}" -gt 0 ]]; then
        echo "Deleting ${_backup_count} backup file(s) under ${_root}"
        sudo find "${_root}" -type f -name '*.backup_*' -print -delete
      fi
    done
  fi
}

process_args "${@}"
load_env
info
[[ "${revert_from_oldest_backup}" == true ]] && run_revert_from_oldest_backup
cleanup
