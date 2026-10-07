#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Check that an RTSP endpoint accepts a TCP connection before we register it.

Used by scripts/add-streams.sh. Standard library only: add-streams.sh runs on the
host with the system python3, before any venv exists.

MV3DT batches its sources, so one unreachable camera stalls the whole pipeline
rather than failing on its own. Probing first turns that silent stall into a named
error at the point of the add.

A URL we cannot parse, or one with no host, exits 0: this is a pre-check, and it
must never be the reason a valid add is refused.

Usage:  RTSP_URL=<url> RTSP_TIMEOUT=<seconds> probe_rtsp.py
Exit:   0 reachable or not checkable, 1 refused or unresolvable, 2 other failure
"""
import os, socket, sys
from urllib.parse import urlparse

try:
    parsed = urlparse(os.environ["RTSP_URL"])
    host, port = parsed.hostname, parsed.port or 554
except Exception:
    sys.exit(0)
if not host:
    sys.exit(0)

try:
    timeout = float(os.environ.get("RTSP_TIMEOUT", "") or 3)
except ValueError:
    timeout = 3.0

try:
    with socket.create_connection((host, port), timeout=timeout):
        sys.exit(0)
except (ConnectionRefusedError, socket.gaierror) as exc:
    print(f"{host}:{port}: {exc}", file=sys.stderr)
    sys.exit(1)
except Exception as exc:
    print(f"{host}:{port}: {exc}", file=sys.stderr)
    sys.exit(2)
