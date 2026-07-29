#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -u

while true; do
  /opt/actions-runner/launch.sh
  rc=$?
  printf 'runner exited rc=%s; restarting in 10 seconds\n' "$rc" >&2
  sleep 10
done
