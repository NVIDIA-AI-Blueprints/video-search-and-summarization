#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

enable_marker="/etc/vss-skill-eval/direct-gpu-runner.enabled"
if [[ ! -e "$enable_marker" ]]; then
  echo "direct GPU runner is staged but not activated; legacy jobs must drain first" >&2
  exit 1
fi

# During cutover, fail closed if a legacy coordinator still has an agent
# connected over SSH. Do not let local startup cleanup tear down that trial.
for agent_pid in $(pgrep -f \
  '[c]laude --verbose --output-format=stream-json|[c]odex exec' || true)
do
  ancestor="$agent_pid"
  for _ in $(seq 1 8); do
    ancestor="$(ps -o ppid= -p "$ancestor" | tr -d ' ')"
    [[ -n "$ancestor" && "$ancestor" != 0 ]] || break
    if [[ "$(ps -o comm= -p "$ancestor")" == sshd ]]; then
      echo "legacy remote skill-eval is still active; refusing local overlap" >&2
      exit 1
    fi
  done
done

# Skill trials can install a newer NVIDIA userspace package than the OpenShell
# guest's loaded module. Restore the staged matching libraries before admission.
nvidia_status=0
nvidia_output="$(nvidia-smi 2>&1)" || nvidia_status=$?
if ((nvidia_status != 0)); then
  if [[ "$nvidia_output" != *"Driver/library version mismatch"* ]]; then
    printf '%s\n' "$nvidia_output" >&2
    echo "nvidia-smi failed for a reason that is unsafe to auto-repair" >&2
    exit "$nvidia_status"
  fi
  if [[ -z "${SKILL_EVAL_NVIDIA_PIN:-}" ]]; then
    echo "NVIDIA userspace mismatch detected but no pinned driver was configured" >&2
    exit 1
  fi

  cd /usr/lib/x86_64-linux-gnu
  test -s "libnvidia-ml.so.${SKILL_EVAL_NVIDIA_PIN}"
  test -s "libcuda.so.${SKILL_EVAL_NVIDIA_PIN}"
  old_nvml_link="$(readlink libnvidia-ml.so.1 2>/dev/null || true)"
  old_cuda_link="$(readlink libcuda.so.1 2>/dev/null || true)"
  quarantine="/var/lib/vss-skill-eval/nvidia-quarantine/$(date +%s)-$$"
  mkdir -p "$quarantine"
  candidates=()
  for library in libnvidia-*.so.* libcuda.so.*; do
    [[ -f "$library" && ! -L "$library" ]] || continue
    if [[ "$library" =~ \.so\.([0-9]{3}([.][0-9]+)+)$ ]]; then
      version="${BASH_REMATCH[1]}"
      [[ "$version" != "$SKILL_EVAL_NVIDIA_PIN" ]] || continue
      pinned_library="${library%."$version"}.${SKILL_EVAL_NVIDIA_PIN}"
      if [[ ! -s "$pinned_library" ]]; then
        echo "cannot safely replace $library: missing $pinned_library" >&2
        exit 1
      fi
      candidates+=("$library")
    fi
  done

  repair_status=0
  for library in "${candidates[@]}"; do
    mv -- "$library" "$quarantine/" || {
      repair_status=$?
      break
    }
  done
  if ((repair_status == 0)); then
    ln -sfn "libnvidia-ml.so.${SKILL_EVAL_NVIDIA_PIN}" \
      libnvidia-ml.so.1 || repair_status=$?
  fi
  if ((repair_status == 0)); then
    ln -sfn "libcuda.so.${SKILL_EVAL_NVIDIA_PIN}" \
      libcuda.so.1 || repair_status=$?
  fi
  if ((repair_status == 0)); then
    ldconfig || repair_status=$?
  fi
  if ((repair_status == 0)); then
    nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml \
      --disable-hooks enable-cuda-compat >/dev/null 2>&1 \
      || repair_status=$?
  fi
  if ((repair_status == 0)); then
    nvidia_output="$(nvidia-smi 2>&1)" || repair_status=$?
  fi

  if ((repair_status != 0)); then
    for library in "$quarantine"/*; do
      [[ -e "$library" ]] || continue
      mv -f -- "$library" . || true
    done
    if [[ -n "$old_nvml_link" ]]; then
      ln -sfn "$old_nvml_link" libnvidia-ml.so.1 || true
    else
      rm -f libnvidia-ml.so.1 || true
    fi
    if [[ -n "$old_cuda_link" ]]; then
      ln -sfn "$old_cuda_link" libcuda.so.1 || true
    else
      rm -f libcuda.so.1 || true
    fi
    ldconfig || true
    printf '%s\n' "$nvidia_output" >&2
    echo "pinned NVIDIA recovery failed; restored quarantined libraries" >&2
    exit "$repair_status"
  fi
fi

nvidia-smi --query-gpu=index,name,driver_version --format=csv,noheader
docker info >/dev/null
