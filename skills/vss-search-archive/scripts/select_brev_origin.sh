#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Select the final host-side VSS origin with exactly one public VST request.
# JSON is the only stdout so callers can consume the decision deterministically.
set -euo pipefail

if [[ "$#" -ne 2 ]]; then
  echo "usage: select_brev_origin.sh <public-https-origin> <host-origin>" >&2
  exit 2
fi

PUBLIC_ORIGIN=${1%/}
HOST_ORIGIN=${2%/}
PROBE_BODY=$(mktemp /tmp/vss-public-vst.XXXXXX) || exit 1
trap 'rm -f -- "${PROBE_BODY}"' EXIT

if ! PROBE_STATUS=$(curl -sS --connect-timeout 5 --max-time 15 \
  --max-redirs 0 -o "${PROBE_BODY}" -w '%{http_code}' \
  "${PUBLIC_ORIGIN}/vst/api/v1/sensor/version" 2>/dev/null); then
  PROBE_STATUS=000
fi

if [[ "${PROBE_STATUS}" == 200 ]] &&
   jq -e '.type == "vst" and (.version | type == "string" and length > 0)' \
     "${PROBE_BODY}" >/dev/null 2>&1; then
  SELECTED_ORIGIN=${PUBLIC_ORIGIN}
  MEDIA_SCOPE=public
else
  SELECTED_ORIGIN=${HOST_ORIGIN}
  MEDIA_SCOPE=host-local
fi

jq -cn --arg origin "${SELECTED_ORIGIN}" --arg media_scope "${MEDIA_SCOPE}" \
  '{origin: $origin, media_scope: $media_scope}'
