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

"""Diagnose the disposable CI Elasticsearch service before native tests.

This stdlib-only check targets the job's unauthenticated elasticsearch:9200
service. It creates no indices, loads no credentials, and saves only connection
diagnostics. It does not replace client/server acceptance in pytest.
"""

from __future__ import annotations

import http.client
import json
import socket
import time
from pathlib import Path
from typing import Any


def probe() -> dict[str, Any]:
    """Classify DNS, connection and HTTP failures without exposing response bodies."""
    result: dict[str, Any] = {"ready": False, "retryable": True}
    try:
        addresses = socket.getaddrinfo("elasticsearch", 9200, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        return {**result, "category": "dns", "error_code": error.errno}
    result["addresses"] = sorted({address[4][0] for address in addresses})
    connection = http.client.HTTPConnection("elasticsearch", 9200, timeout=2.0)
    try:
        connection.request("GET", "/")
        response = connection.getresponse()
        result["http_status"] = response.status
        if response.status != 200:
            return {
                **result,
                "category": "http",
                "retryable": response.status in {429, 502, 503, 504},
            }
        try:
            payload = json.loads(response.read(16 * 1024 + 1))
            version = payload["version"]["number"]
            product = response.getheader("X-Elastic-Product")
            if product != "Elasticsearch" or not isinstance(version, str):
                raise ValueError
        except (ValueError, TypeError, KeyError):
            return {**result, "category": "wrong-service", "retryable": False}
        return {
            **result,
            "category": "ready",
            "ready": True,
            "retryable": False,
            "version": version,
        }
    except (OSError, http.client.HTTPException) as error:
        return {**result, "category": "connection", "error_type": type(error).__name__}
    finally:
        connection.close()


def main(
    *, report_path: Path = Path("es-service-check.json"), timeout_s: float = 30.0
) -> int:
    """Retry startup once per second, save diagnostics, and fail before pytest."""
    deadline = time.monotonic() + timeout_s
    previous = None
    while True:
        result = probe()
        if result != previous:
            print("Elasticsearch service: " + json.dumps(result), flush=True)
            previous = result
        remaining = deadline - time.monotonic()
        if result["ready"] or not result["retryable"] or remaining <= 0:
            report_path.write_text(
                json.dumps(result, indent=2) + "\n", encoding="utf-8"
            )
            if result["ready"]:
                return 0
            print(
                "Elasticsearch service preflight failed. Inspect the runner's service "
                "startup logs and Docker networking; FF_NETWORK_PER_BUILD requires "
                "network_mode to be unset. Storage native tests were not started.",
                flush=True,
            )
            return 1
        time.sleep(min(1.0, remaining))


if __name__ == "__main__":
    raise SystemExit(main())
