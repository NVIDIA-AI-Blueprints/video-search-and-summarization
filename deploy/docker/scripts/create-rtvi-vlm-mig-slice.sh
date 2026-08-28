#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Create one MIG slice for an RTVI-VLM test. This intentionally refuses to
# repartition a GPU that has compute workloads or existing MIG devices.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: sudo ./create-rtvi-vlm-mig-slice.sh --gpu <index> [--profile <name>]

Creates exactly one MIG GPU instance and compute instance on an otherwise idle
GPU, then prints the MIG UUID to use for RT_VLM_DEVICE_ID and
RTVI_VLM_NVIDIA_VISIBLE_DEVICES.

Default profile: 2g.48gb. This is suitable for the RTX PRO 6000 Blackwell
server GPU used for the RT-VLM BF16 test. Use `nvidia-smi mig -i <index> -lgip`
to see the profiles supported by another GPU.

The script will not delete existing MIG instances. To restore the full GPU
after the test, stop all users of the slice and have the host owner remove the
MIG instances and disable MIG mode explicitly.
EOF
}

gpu_index=""
profile_name="2g.48gb"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --gpu)
      gpu_index="${2:-}"
      shift 2
      ;;
    --profile)
      profile_name="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! "$gpu_index" =~ ^[0-9]+$ ]]; then
  echo "ERROR: --gpu must be a numeric GPU index." >&2
  usage >&2
  exit 2
fi

if [[ $EUID -ne 0 ]]; then
  echo "ERROR: Run this script with sudo; NVIDIA requires administrator access to change MIG mode." >&2
  exit 1
fi

smi="$(command -v nvidia-smi || true)"
if [[ -z "$smi" ]]; then
  echo "ERROR: nvidia-smi is not installed or not on PATH." >&2
  exit 1
fi

# Do not exit awk early here: with pipefail, that would make nvidia-smi fail
# from SIGPIPE and terminate this script before its diagnostic can be printed.
gpu_row="$($smi --query-gpu=index,uuid,mig.mode.current --format=csv,noheader | awk -F', ' -v target_index="$gpu_index" '$1 == target_index {row = $0} END {if (row != "") print row}')"
if [[ -z "$gpu_row" ]]; then
  echo "ERROR: GPU ${gpu_index} does not exist." >&2
  exit 1
fi

gpu_uuid="$(awk -F', ' '{print $2}' <<<"$gpu_row")"
mig_mode="$(awk -F', ' '{print $3}' <<<"$gpu_row")"

if $smi -L | awk -v target_gpu="$gpu_index" '
  $1 == "GPU" && $2 == target_gpu ":" {in_target_gpu = 1; next}
  $1 == "GPU" {in_target_gpu = 0}
  in_target_gpu && /MIG/ && /UUID: MIG-/ {found = 1}
  END {exit !found}
'; then
  echo "ERROR: GPU ${gpu_index} already has MIG instances. Refusing to alter its partitioning." >&2
  $smi -L >&2
  exit 1
fi

busy_processes="$($smi --query-compute-apps=gpu_uuid,pid,process_name,used_memory --format=csv,noheader 2>/dev/null | grep -F "$gpu_uuid" || true)"
if [[ -n "$busy_processes" ]]; then
  echo "ERROR: GPU ${gpu_index} (${gpu_uuid}) has active compute workloads. Refusing to enable MIG:" >&2
  echo "$busy_processes" >&2
  exit 1
fi

# Table rows are "| GPU MIG <profile-name> <profile-id> ...". Compare the
# profile-name column exactly so 2g.48gb does not accidentally select
# 2g.48gb-me (or another suffix variant).
profile_id="$($smi mig -i "$gpu_index" -lgip | awk -v profile="$profile_name" '$3 == "MIG" && $4 == profile {profile_id = $5} END {if (profile_id != "") print profile_id}')"
if [[ ! "$profile_id" =~ ^[0-9]+$ ]]; then
  echo "ERROR: GPU ${gpu_index} does not support the requested MIG profile '${profile_name}'." >&2
  $smi mig -i "$gpu_index" -lgip >&2
  exit 1
fi

if [[ "$mig_mode" != "Enabled" ]]; then
  echo "Enabling MIG mode on GPU ${gpu_index} (${gpu_uuid})..."
  $smi -i "$gpu_index" -mig 1
fi

current_mode="$($smi --query-gpu=mig.mode.current --format=csv,noheader -i "$gpu_index")"
if [[ "$current_mode" != "Enabled" ]]; then
  echo "ERROR: MIG mode is pending rather than enabled. Reboot the host if NVIDIA requires it, then rerun this script." >&2
  exit 1
fi

echo "Creating one ${profile_name} GPU and compute instance on GPU ${gpu_index}..."
$smi mig -i "$gpu_index" -cgi "$profile_id" -C

# Recent Blackwell drivers report MIG UUIDs as either MIG-<uuid> or the older
# MIG-GPU-<gpu-uuid>/<gi>/<ci> form. Do not use head here: with pipefail it can
# terminate nvidia-smi's producer early and hide this final status message.
mig_uuid="$($smi -L | awk -v target_gpu="$gpu_index" '
  $1 == "GPU" && $2 == target_gpu ":" {in_target_gpu = 1; next}
  $1 == "GPU" {in_target_gpu = 0}
  in_target_gpu && /MIG/ && /UUID:/ {
    match($0, /UUID: (MIG-[^)]*)/, uuid)
    if (uuid[1] != "") result = uuid[1]
  }
  END {if (result != "") print result}
')"
if [[ -z "$mig_uuid" ]]; then
  echo "ERROR: The MIG instance was created but its UUID could not be read." >&2
  $smi -L >&2
  exit 1
fi

cat <<EOF

Created MIG slice: ${mig_uuid}

Use these exact values for the VSS Docker Compose RT-VLM deployment:
export RT_VLM_DEVICE_ID='${mig_uuid}'
export RTVI_VLM_NVIDIA_VISIBLE_DEVICES='${mig_uuid}'
EOF
