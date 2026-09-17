#!/bin/sh
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Wrapper entrypoint: reconcile the OpenClaw model config with the gateway as
# the sandbox user — NemoClaw's own startup reconcile requires root and
# silently no-ops in this image (OCI user `sandbox`) — then hand over to
# NemoClaw's trusted entrypoint unchanged. The reconcile is fail-open and this
# guard is belt-and-braces: startup is never blocked.
python3 /usr/local/bin/vss-model-reconcile || true
exec /usr/local/bin/nemoclaw-start "$@"
