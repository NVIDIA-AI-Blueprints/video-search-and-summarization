#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# launch-instance.sh - Provision a single 80 GB GPU instance on Lambda GPU Cloud
# for the VSS "base" profile, then print how to connect.
#
# Runs on your laptop / any machine with curl + jq. It does NOT install anything
# on the target; after it prints the SSH command, copy this repo to the instance
# and run deploy/lambda/setup-lambda.sh there.
#
# Requires: LAMBDA_API_KEY in the environment (Lambda Cloud API key).
#
# Usage:
#   export LAMBDA_API_KEY="secret_..."
#   ./deploy/lambda/launch-instance.sh                     # auto-pick an available 1x 80GB GPU
#   ./deploy/lambda/launch-instance.sh --instance-type gpu_1x_h100_pcie --region us-east-1
#   ./deploy/lambda/launch-instance.sh --ssh-key-name my-key
#   ./deploy/lambda/launch-instance.sh --terminate         # tear down instances named "vss-base"
#   ./deploy/lambda/launch-instance.sh --list              # just show available GPU types/regions

set -euo pipefail

API="https://cloud.lambda.ai/api/v1"
NAME="vss-base"
INSTANCE_TYPE=""
REGION=""
SSH_KEY_NAME=""
ACTION="launch"

log()  { printf '\033[1;34m[launch]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[launch] WARNING:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[launch] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --instance-type) INSTANCE_TYPE="$2"; shift 2 ;;
    --region)        REGION="$2"; shift 2 ;;
    --ssh-key-name)  SSH_KEY_NAME="$2"; shift 2 ;;
    --name)          NAME="$2"; shift 2 ;;
    --terminate)     ACTION="terminate"; shift ;;
    --list)          ACTION="list"; shift ;;
    -h|--help)
      # Print the header doc block (skip SPDX lines, stop before the code).
      sed -n '5,/^set -euo/p' "$0" | grep -E '^#( |$)' | sed -E 's/^# ?//'; exit 0 ;;
    *) die "Unknown argument: $1 (use --help)" ;;
  esac
done

command -v curl >/dev/null || die "curl is required"
command -v jq   >/dev/null || die "jq is required (brew install jq / apt-get install jq)"
[[ -n "${LAMBDA_API_KEY:-}" ]] || die "LAMBDA_API_KEY is not set. Get one at https://cloud.lambda.ai/api-keys"

# api METHOD PATH [json-body] -> prints response body, dies on HTTP >= 400
api() {
  local method="$1" path="$2" body="${3:-}"
  local args=(-sS -w '\n%{http_code}' -u "${LAMBDA_API_KEY}:" -X "$method" "${API}${path}")
  [[ -n "$body" ]] && args+=(-H 'Content-Type: application/json' -d "$body")
  local out code
  out="$(curl "${args[@]}")" || die "curl failed calling ${method} ${path}"
  code="$(tail -n1 <<<"$out")"
  out="$(sed '$d' <<<"$out")"
  if [[ "$code" -ge 400 ]]; then
    die "Lambda API ${method} ${path} -> HTTP ${code}: $(jq -r '.error.message // .' <<<"$out" 2>/dev/null || echo "$out")"
  fi
  printf '%s' "$out"
}

# --- terminate --------------------------------------------------------------
if [[ "$ACTION" == "terminate" ]]; then
  ids="$(api GET /instances | jq -r --arg n "$NAME" '.data[] | select(.name==$n) | .id')"
  [[ -n "$ids" ]] || { log "No running instances named '$NAME'."; exit 0; }
  body="$(jq -cn --argjson ids "$(jq -R . <<<"$ids" | jq -s .)" '{instance_ids: $ids}')"
  log "Terminating: $(tr '\n' ' ' <<<"$ids")"
  api POST /instance-operations/terminate "$body" >/dev/null
  log "Terminated. Billing stops for those instances."
  exit 0
fi

# --- choose an instance type + region --------------------------------------
types_json="$(api GET /instance-types)"

# Rows of: <type>\t<region>\t<gpus>\t<gpu_description>\t<price_cents>  for types WITH capacity now.
# Lambda's /instance-types does not reliably expose per-GPU VRAM, so selection below is by
# instance-type name (known >=80 GB single-GPU families), not a numeric VRAM field.
avail="$(jq -r '
  .data | to_entries[]
  | .key as $t
  | (.value.instance_type.specs.gpus // 0) as $gpus
  | (.value.instance_type.gpu_description // .value.instance_type.description // "") as $desc
  | (.value.instance_type.price_cents_per_hour // 0) as $price
  | .value.regions_with_capacity_available[]?
  | [$t, .name, ($gpus|tostring), $desc, ($price|tostring)] | @tsv
' <<<"$types_json")"

if [[ "$ACTION" == "list" ]]; then
  log "GPU instance types with capacity available now (type / region / gpus / gpu / \$per-hr):"
  if [[ -n "$avail" ]]; then
    awk -F'\t' '{printf "  %-22s %-14s %sx  %-28s $%.2f/hr\n", $1,$2,$3,$4,($5/100)}' <<<"$avail"
  else
    echo "  (none available right now)"
  fi
  exit 0
fi

pick=""
if [[ -n "$INSTANCE_TYPE" ]]; then
  pick="$(awk -F'\t' -v t="$INSTANCE_TYPE" -v r="$REGION" '$1==t && (r=="" || $2==r){print; exit}' <<<"$avail")"
  [[ -n "$pick" ]] || die "Requested type '$INSTANCE_TYPE'${REGION:+ in region $REGION} has no capacity now. Try --list."
else
  # Auto-pick a single-GPU node from known >=80 GB families: H100 PCIe first, then any
  # single H100, then H200/GH200/B200. (a100 is skipped: the 1x variant is only 40 GB.)
  for filter in \
    '$1=="gpu_1x_h100_pcie"' \
    '$1 ~ /^gpu_1x_h100/' \
    '$1 ~ /^gpu_1x_(h200|gh200|b200)/'; do
    pick="$(awk -F'\t' -v r="$REGION" "$filter && (r==\"\" || \$2==r){print; exit}" <<<"$avail")"
    [[ -n "$pick" ]] && break
  done
  [[ -n "$pick" ]] || die "No single-GPU 80GB+ node (H100/H200/GH200/B200) has capacity right now. Run --list, retry later, or pass --instance-type."
fi

INSTANCE_TYPE="$(cut -f1 <<<"$pick")"
REGION="$(cut -f2 <<<"$pick")"
GPU_DESC="$(cut -f4 <<<"$pick")"
log "Selected: ${INSTANCE_TYPE} in ${REGION} (${GPU_DESC:-GPU})"

# --- ensure an SSH key is registered ---------------------------------------
keys_json="$(api GET /ssh-keys)"
if [[ -z "$SSH_KEY_NAME" ]]; then
  SSH_KEY_NAME="$(jq -r '.data[0].name // empty' <<<"$keys_json")"
  if [[ -z "$SSH_KEY_NAME" ]]; then
    pub=""
    for cand in ~/.ssh/id_ed25519.pub ~/.ssh/id_rsa.pub; do
      [[ -f "$cand" ]] && { pub="$cand"; break; }
    done
    [[ -n "$pub" ]] || die "No SSH keys on Lambda and none found locally. Create one (ssh-keygen) or pass --ssh-key-name."
    SSH_KEY_NAME="vss-$(whoami)"
    log "Uploading local public key ${pub} as Lambda SSH key '${SSH_KEY_NAME}'"
    api POST /ssh-keys "$(jq -cn --arg n "$SSH_KEY_NAME" --arg k "$(cat "$pub")" '{name:$n, public_key:$k}')" >/dev/null
  fi
else
  jq -e --arg n "$SSH_KEY_NAME" '.data[] | select(.name==$n)' <<<"$keys_json" >/dev/null \
    || die "SSH key '${SSH_KEY_NAME}' not found on Lambda. Registered: $(jq -r '.data[].name' <<<"$keys_json" | tr '\n' ' ')"
fi
log "Using SSH key: ${SSH_KEY_NAME}"

# --- launch -----------------------------------------------------------------
launch_body="$(jq -cn \
  --arg region "$REGION" --arg type "$INSTANCE_TYPE" --arg key "$SSH_KEY_NAME" --arg name "$NAME" \
  '{region_name:$region, instance_type_name:$type, ssh_key_names:[$key], name:$name}')"
log "Launching ${INSTANCE_TYPE} in ${REGION}..."
id="$(api POST /instance-operations/launch "$launch_body" | jq -r '.data.instance_ids[0]')"
[[ -n "$id" && "$id" != "null" ]] || die "Launch did not return an instance id."
log "Instance id: ${id} (waiting for it to become active; usually 1-3 min)"

ip=""
for _ in $(seq 1 60); do
  inst="$(api GET "/instances/${id}")"
  status="$(jq -r '.data.status' <<<"$inst")"
  ip="$(jq -r '.data.ip // empty' <<<"$inst")"
  if [[ "$status" == "active" && -n "$ip" ]]; then break; fi
  if [[ "$status" == "terminated" || "$status" == "error" ]]; then die "Instance entered status '${status}'."; fi
  sleep 10
done
[[ -n "$ip" ]] || die "Timed out waiting for the instance to become active. Check https://cloud.lambda.ai/instances"

cat <<EOF

============================================================================
  VSS instance is up.
  Instance : ${NAME} (${INSTANCE_TYPE}, ${REGION})  id=${id}
  Public IP: ${ip}

  Next steps
  ----------
  1. Copy this repo to the instance (from the repo root on your machine):
       rsync -az --exclude .git ./ ubuntu@${ip}:~/video-search-and-summarization/
     (or: ssh ubuntu@${ip} 'git clone <this-repo-url> ~/video-search-and-summarization')

  2. SSH in and run the setup (needs your NGC key to pull the models):
       ssh ubuntu@${ip}
       cd ~/video-search-and-summarization
       export NGC_CLI_API_KEY='nvapi-...'
       ./deploy/lambda/setup-lambda.sh

  3. Once healthy, open the UI over an SSH tunnel:
       ssh -L 7777:localhost:7777 ubuntu@${ip}
       # then browse to http://localhost:7777

  Tear down when finished (stops billing):
       ./deploy/lambda/launch-instance.sh --terminate
============================================================================
EOF
