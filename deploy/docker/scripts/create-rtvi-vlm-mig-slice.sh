#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Create one MIG slice for a Docker Compose RTVI-VLM test, or ask the NVIDIA
# GPU Operator to configure a Kubernetes node for the Helm chart.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  # Docker Compose: create one slice directly on an otherwise idle host GPU.
  sudo ./create-rtvi-vlm-mig-slice.sh --gpu <index> [--profile <name>]

  # Kubernetes: have GPU Operator configure every MIG-capable GPU on a node.
  ./create-rtvi-vlm-mig-slice.sh --kubernetes-node <node> --profile <name>

The Docker Compose form creates exactly one MIG GPU and compute instance, then
prints the MIG UUID to use for RT_VLM_DEVICE_ID and
RTVI_VLM_NVIDIA_VISIBLE_DEVICES. It must not be used to reconfigure a GPU
managed by Kubernetes GPU Operator.

The Kubernetes form changes the node's nvidia.com/mig.config label to
all-<profile>. GPU Operator then owns the reconfiguration and advertises the
result as nvidia.com/mig-<profile>. This changes every MIG-capable GPU on the
node with the Operator's single strategy; cordon/drain and obtain approval for
the whole node before using it. The Operator must be Ready before this command
will make the change.

Choose a profile advertised by the node's <node>-mig-config ConfigMap. For
example, an H100 NVL supports 3g.47gb, which Helm requests as
nvidia.com/mig-3g.47gb. Use `nvidia-smi mig -i <index> -lgip` for direct-host
profiles, or `kubectl get configmap -n gpu-operator <node>-mig-config -o yaml`
for GPU Operator profiles.

The script will not delete existing MIG instances. To restore the full GPU
after the test, stop all users of the slice and have the host owner remove the
MIG instances and disable MIG mode explicitly.
EOF
}

gpu_index=""
kubernetes_node=""
profile_name=""

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
    --kubernetes-node)
      kubernetes_node="${2:-}"
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

if [[ -n "$kubernetes_node" ]]; then
  if [[ -n "$gpu_index" ]]; then
    echo "ERROR: --gpu and --kubernetes-node are mutually exclusive." >&2
    exit 2
  fi
  if [[ -z "$profile_name" ]]; then
    echo "ERROR: --profile is required with --kubernetes-node." >&2
    exit 2
  fi
  if ! command -v kubectl >/dev/null 2>&1; then
    echo "ERROR: kubectl is required for --kubernetes-node." >&2
    exit 1
  fi

  if ! kubectl get node "$kubernetes_node" >/dev/null; then
    echo "ERROR: Kubernetes node '${kubernetes_node}' was not found." >&2
    exit 1
  fi
  if ! kubectl get clusterpolicy cluster-policy -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' | grep -qx 'True'; then
    echo "ERROR: GPU Operator ClusterPolicy is not Ready. Fix GPU Operator before changing MIG configuration." >&2
    exit 1
  fi
  if ! kubectl get configmap -n gpu-operator "${kubernetes_node}-mig-config" -o jsonpath='{.data.config\.yaml}' | grep -Fqx "  all-${profile_name}:"; then
    echo "ERROR: GPU Operator does not advertise profile '${profile_name}' for node '${kubernetes_node}'." >&2
    echo "Inspect: kubectl get configmap -n gpu-operator ${kubernetes_node}-mig-config -o yaml" >&2
    exit 1
  fi

  cat <<EOF
WARNING: This asks GPU Operator to repartition every MIG-capable GPU on node
${kubernetes_node} using the all-${profile_name} geometry. GPU workloads on
that node will be interrupted while GPU Operator reconfigures it.
EOF
  kubectl label node "$kubernetes_node" "nvidia.com/mig.config=all-${profile_name}" --overwrite

  cat <<EOF

GPU Operator reconfiguration requested. Wait for these conditions before Helm:
  kubectl get node ${kubernetes_node} -o jsonpath='{.metadata.labels.nvidia\\.com/mig\\.config\\.state}{"\\n"}'
  kubectl get node ${kubernetes_node} -o jsonpath='{.status.allocatable.nvidia\\.com/mig-${profile_name}}{"\\n"}'

Then deploy with this resource name:
  nvidia.com/mig-${profile_name}
EOF
  exit 0
fi

if [[ ! "$gpu_index" =~ ^[0-9]+$ ]]; then
  echo "ERROR: --gpu must be a numeric GPU index." >&2
  usage >&2
  exit 2
fi

if [[ -z "$profile_name" ]]; then
  profile_name="2g.48gb"
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
