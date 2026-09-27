#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Credential gate for vss-build-vision-ai. Validates the keys a deploy needs
# (NGC / NVIDIA_API_KEY / HF_TOKEN) against their services so a bad key fails in
# seconds, not after a cold NIM start. Read-only: it reads env vars and curls —
# it does NOT write override.env (the skill writes the resolved key per
# credentials.md). Each probe reports the key as validated, rejected by the
# service, not validated because the service never answered, or skipped.
# Requiredness is mode-dependent and comes from
# --require, so the exit code is the verdict the caller branches on:
# 0 gate passed, 1 usage error, 2 gate failed.
set -u

usage() {
  cat <<'EOF'
Usage: check_credentials.sh [--require <name>[,<name>...]]...

Validate configured VSS deployment credentials without modifying them.

Options:
  --require ngc         NGC key is required (any local NIM image pull:
                        LLM_MODE / VLM_MODE of local or local_shared)
  --require nvidia-api  NVIDIA_API_KEY is required (remote NIM endpoints)
  --require hf          HF_TOKEN is required (standalone RT-VLM / RT-Embed
                        Hugging Face checkpoints)
  -h, --help            Print this help and exit without probing

Environment variables:
  NGC_CLI_API_KEY, NGC_API_KEY  NGC registry key for local NIM images
  NVIDIA_API_KEY                build.nvidia.com API key for remote NIMs
  HF_TOKEN                      Hugging Face token for gated checkpoints

A credential that is unset, rejected, or left unvalidated by an unreachable or
erroring service is a blocker only when its --require name was passed;
otherwise it is reported and does not gate. Conflicting NGC_CLI_API_KEY /
NGC_API_KEY values always gate. Each probe is bounded at 5s to connect and 15s
in total, so the gate cannot hang on a host with no egress.

Exit codes:
  0  every required credential validated
  1  usage error
  2  gate failed (see the BLOCKER summary on stderr)
EOF
  return 0
}

require_ngc=0
require_nvidia=0
require_hf=0

add_requirement() {
  local name
  local IFS=','
  if [[ -z "$1" ]]; then
    echo "ERROR: --require needs a value" >&2
    usage >&2
    exit 1
  fi
  for name in $1; do
    case "$name" in
      ngc) require_ngc=1 ;;
      nvidia-api) require_nvidia=1 ;;
      hf) require_hf=1 ;;
      *)
        echo "ERROR: unknown --require value: $name" >&2
        usage >&2
        exit 1
        ;;
    esac
  done
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --require)
      if [[ $# -lt 2 ]]; then
        echo "ERROR: --require needs a value" >&2
        usage >&2
        exit 1
      fi
      add_requirement "$2"
      shift 2
      ;;
    --require=*)
      add_requirement "${1#--require=}"
      shift
      ;;
    *)
      echo "ERROR: unexpected argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

blockers=()

# Report a failed probe. It gates only when the caller declared the credential
# required for this build; an unrelated stale key must not block a deploy that
# never reads it.
report_failure() {
  local required="$1" message="$2"
  if [[ "$required" == 1 ]]; then
    echo "$message"
    blockers+=("$message")
  else
    echo "$message — not required by this build"
  fi
}

# HTTP status of a read-only probe, or 000 when the request never completed.
# Deliberately not `curl -f`: -f collapses a refused connection, a DNS failure
# and a real 401 into one non-zero exit, which reports a working key as invalid
# on a host with no egress. Timeouts are mandatory for a gate that promises to
# fail in seconds: without them, blackholed egress or a server that accepts and
# stalls leaves curl on the OS timeout, so the whole preflight hangs instead of
# reporting 000. Same bounds as
# skills/operations/vss-search-archive/scripts/select_brev_origin.sh.
http_status() {
  curl -s -o /dev/null -w '%{http_code}' --connect-timeout 5 --max-time 15 "$@" 2>/dev/null || true
}

# Turn a probe status into a verdict for one credential. Only 401/403 is a
# statement about the credential itself; 000 and 429/5xx mean the service never
# gave one, and reporting those as "rejected" sends someone to rotate a working
# key. Both still gate when the credential is required — an unvalidated
# requirement is not a pass — so this changes the message, not the exit code.
report_status() {
  local status="$1" required="$2" label="$3" host="$4" reject_hint="${5:-}"
  case "$status" in
    2??) echo "$label ok" ;;
    000) report_failure "$required" "$label not validated — $host did not answer within the probe timeout" ;;
    401|403) report_failure "$required" "$label rejected by $host (HTTP $status)${reject_hint:+ — $reject_hint}" ;;
    429|5??) report_failure "$required" "$label not validated — $host returned HTTP $status (rate limit or service error), which is not a verdict on the credential; retry" ;;
    *) report_failure "$required" "$label not validated — unexpected HTTP $status from $host" ;;
  esac
}

# NGC — local NIM image pulls. NGC_CLI_API_KEY (NGC CLI / VSS env) and
# NGC_API_KEY (NIM / RT-VLM containers) are the SAME personal NGC key under two
# names; resolve to one. Refuse to proceed if both are set and differ.
if [[ -n "${NGC_CLI_API_KEY:-}" ]] && [[ -n "${NGC_API_KEY:-}" ]] && \
   [[ "$NGC_CLI_API_KEY" != "$NGC_API_KEY" ]]; then
  # Unconditional: credentials.md says stop and ask which key to use rather
  # than silently choosing one, whatever the mode needs.
  ngc_conflict="NGC: NGC_CLI_API_KEY and NGC_API_KEY differ — choose one NGC personal API key"
  echo "$ngc_conflict"
  blockers+=("$ngc_conflict")
elif [[ -n "${NGC_CLI_API_KEY:-${NGC_API_KEY:-}}" ]]; then
  ngc_resolved="${NGC_CLI_API_KEY:-${NGC_API_KEY:-}}"
  # Probe the registry pull scope (what image pulls actually use), not
  # service=ngc - a key scoped only for nvcr.io pulls is valid for a deploy
  # but is rejected by the ngc platform scope (false negative).
  ngc_status=$(http_status -u "\$oauthtoken:${ngc_resolved}" \
    "https://authn.nvidia.com/token?service=registry&scope=repository:nvidia/vss-core/vss-agent:pull")
  report_status "$ngc_status" "$require_ngc" "NGC key" "authn.nvidia.com"
elif [[ "$require_ngc" == 1 ]]; then
  ngc_missing="NGC: not set — required for any local NIM image pull"
  echo "$ngc_missing"
  blockers+=("$ngc_missing")
else
  echo "NGC: not set — skip (required for any local NIM)"
fi

# build.nvidia.com — remote NIM endpoints
if [[ -n "${NVIDIA_API_KEY:-}" ]]; then
  nvidia_status=$(http_status -H "Authorization: Bearer ${NVIDIA_API_KEY}" \
    "https://integrate.api.nvidia.com/v1/models")
  report_status "$nvidia_status" "$require_nvidia" "NVIDIA_API_KEY" "integrate.api.nvidia.com"
elif [[ "$require_nvidia" == 1 ]]; then
  nvidia_missing="NVIDIA_API_KEY: not set — required for remote NIM endpoints"
  echo "$nvidia_missing"
  blockers+=("$nvidia_missing")
else
  echo "NVIDIA_API_KEY: not set — skip (required only for remote NIM)"
fi

# HF — not needed by any in-tree edge path; kept for RT-VLM / RT-Embed HF checkpoints
if [[ -n "${HF_TOKEN:-}" ]]; then
  hf_status=$(http_status -H "Authorization: Bearer ${HF_TOKEN}" \
    "https://huggingface.co/api/models/Qwen/Qwen3-VL-8B-Instruct")
  report_status "$hf_status" "$require_hf" "HF_TOKEN" "huggingface.co" \
    "the token is invalid or has no access to the probed model"
elif [[ "$require_hf" == 1 ]]; then
  hf_missing="HF_TOKEN: not set — required for the standalone RT-VLM / RT-Embed Hugging Face checkpoints"
  echo "$hf_missing"
  blockers+=("$hf_missing")
else
  echo "HF_TOKEN: not set — skip (no in-tree edge path needs it; used by RT-VLM / RT-Embed HF checkpoints)"
fi

if [[ "${#blockers[@]}" -gt 0 ]]; then
  {
    echo "BLOCKER: credential gate failed:"
    for blocker in "${blockers[@]}"; do
      echo "  - $blocker"
    done
  } >&2
  exit 2
fi

exit 0
