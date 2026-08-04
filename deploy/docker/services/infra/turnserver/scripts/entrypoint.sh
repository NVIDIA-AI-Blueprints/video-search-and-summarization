#!/usr/bin/env sh
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -eu

config_file="${TURN_CONFIG_FILE:-/tmp/turnserver.conf}"
password_file="${TURN_PASSWORD_FILE:-/run/secrets/vss-turn/turn-password}"
password_wait_seconds="${TURN_PASSWORD_WAIT_SECONDS:-30}"
turn_password=""

case "${password_wait_seconds}" in
  ''|*[!0-9]*)
    password_wait_seconds=30
    ;;
esac

elapsed=0
while [ ! -s "${password_file}" ] && [ "${elapsed}" -lt "${password_wait_seconds}" ]; do
  sleep 1
  elapsed=$((elapsed + 1))
done

if [ -n "${password_file}" ] && [ -s "${password_file}" ]; then
  turn_password="$(tr -d '\r\n' < "${password_file}")"
fi

if [ -z "${turn_password}" ]; then
  echo "[turnserver-entrypoint] TURN password is empty; ensure turnserver-init generated the mounted password file" >&2
  exit 1
fi

turn_username="${TURN_USERNAME:-}"
if [ -z "${turn_username}" ]; then
  echo "[turnserver-entrypoint] TURN_USERNAME is empty; set a deployment-specific TURN username" >&2
  exit 1
fi

legacy_username="vss"
legacy_password="${legacy_username}-turn"
if [ "${turn_username}" = "${legacy_username}" ] && [ "${turn_password}" = "${legacy_password}" ]; then
  echo "[turnserver-entrypoint] refusing known default TURN credentials; choose deployment-specific credentials" >&2
  exit 1
fi
unset legacy_password
turn_realm="${TURN_REALM:-vss.local}"
turn_port="${TURN_PORT:-3478}"
turn_min_relay_port="${TURN_MIN_RELAY_PORT:-49160}"
turn_max_relay_port="${TURN_MAX_RELAY_PORT:-49200}"
turn_external_ip="${TURN_EXTERNAL_IP:-${EXTERNAL_IP:-${HOST_IP:-}}}"

{
  echo "no-cli"
  echo "no-tls"
  echo "no-dtls"
  echo "fingerprint"
  echo "lt-cred-mech"
  echo "realm=${turn_realm}"
  echo "user=${turn_username}:${turn_password}"
  echo "listening-ip=0.0.0.0"
  echo "listening-port=${turn_port}"
  echo "min-port=${turn_min_relay_port}"
  echo "max-port=${turn_max_relay_port}"
  if [ -n "${turn_external_ip}" ] && [ "${turn_external_ip}" != "<HOST_IP>" ]; then
    echo "external-ip=${turn_external_ip}"
  fi
  echo "log-file=stdout"
} > "${config_file}"

unset turn_password
exec turnserver -c "${config_file}"
