#!/usr/bin/env sh
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -eu

password_file="${TURN_PASSWORD_FILE:-/run/secrets/vss-turn/turn-password}"
password_bytes="${TURN_PASSWORD_BYTES:-32}"
password_uid="${TURN_PASSWORD_UID:-0}"
password_gid="${TURN_PASSWORD_GID:-65534}"

case "${password_bytes}" in
  ''|*[!0-9]*)
    echo "[turnserver-init] TURN_PASSWORD_BYTES must be a positive integer" >&2
    exit 1
    ;;
esac

if [ "${password_bytes}" -lt 16 ]; then
  echo "[turnserver-init] TURN_PASSWORD_BYTES must be at least 16" >&2
  exit 1
fi

password_dir="$(dirname "${password_file}")"
mkdir -p "${password_dir}"
chown "${password_uid}:${password_gid}" "${password_dir}"
chmod 750 "${password_dir}"

if [ -s "${password_file}" ]; then
  chown "${password_uid}:${password_gid}" "${password_file}"
  chmod 440 "${password_file}"
  echo "[turnserver-init] using existing TURN password file"
  exit 0
fi

tmp_file="${password_file}.tmp.$$"
cleanup() {
  rm -f "${tmp_file}"
}
trap cleanup EXIT INT TERM

umask 077
od -An -N "${password_bytes}" -tx1 /dev/urandom | tr -d ' \n' > "${tmp_file}"
printf '\n' >> "${tmp_file}"
chown "${password_uid}:${password_gid}" "${tmp_file}"
chmod 440 "${tmp_file}"
mv "${tmp_file}" "${password_file}"
trap - EXIT INT TERM

echo "[turnserver-init] generated TURN password file"
