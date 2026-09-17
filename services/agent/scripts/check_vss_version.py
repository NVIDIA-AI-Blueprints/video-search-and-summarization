#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Check ``GET /api/v1/version`` on a VSS deployment.

Prints the deployed version and exits 0, or prints why the deployment's version
cannot be determined and exits 1. Standard library only, so it runs against any
deployment without installing VSS.

    python3 check_vss_version.py http://localhost:8000
"""

import argparse
import json
import re
import sys
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.request import urlopen

# Must stay in step with vss_agents.api.version._SEMVER_PATTERN. Duplicated
# rather than imported so this script needs nothing but Python.
SEMVER_PATTERN = re.compile(r"\d+\.\d+\.\d+(-[A-Za-z0-9\-.]+)?(\+[A-Za-z0-9\-.]+)?")


def check(base_url: str, timeout: float) -> str:
    """Return the deployed version, or raise ``RuntimeError`` explaining why not."""
    url = f"{base_url.rstrip('/')}/api/v1/version"
    try:
        with urlopen(url, timeout=timeout) as response:
            payload = json.load(response)
    except HTTPError as error:
        if error.code == 503:
            raise RuntimeError(
                f"{url} returned 503: the deployment has no usable VSS_AGENT_VERSION, "
                "so its version cannot be determined."
            ) from error
        raise RuntimeError(f"{url} returned HTTP {error.code} {error.reason}.") from error
    except (URLError, TimeoutError) as error:
        raise RuntimeError(f"{url} is not reachable: {error}.") from error
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{url} did not return JSON: {error}.") from error

    if not isinstance(payload, dict) or payload.get("service") != "vss" or not isinstance(payload.get("version"), str):
        raise RuntimeError(
            f'{url} returned {json.dumps(payload)}, expected {{"service": "vss", "version": "<semver>"}}.'
        )

    version = str(payload["version"])
    if not SEMVER_PATTERN.fullmatch(version):
        raise RuntimeError(f"{url} reported version {version!r}, which is not valid Semantic Versioning 2.0.0.")
    return version


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("base_url", help="Deployment origin, e.g. http://localhost:8000")
    parser.add_argument("--timeout", type=float, default=10.0, help="Request timeout in seconds (default: 10)")
    args = parser.parse_args()

    try:
        print(check(args.base_url, args.timeout))
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
