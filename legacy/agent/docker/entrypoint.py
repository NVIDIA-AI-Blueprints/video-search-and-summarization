#!/usr/local/bin/python3
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
"""Container entrypoint wrapper for the VSS Agent.

The image ships without patent-encumbered multimedia codecs. When the operator
sets ``INSTALL_PROPRIETARY_CODECS=true``, this wrapper installs OpenCV/FFmpeg on
the operator's machine (see ``install_proprietary_codecs.py``), puts it on
``PYTHONPATH``, and then hands off to the real ``nat`` entrypoint via ``execv``
(so signal handling / PID 1 semantics are preserved). Otherwise it execs ``nat``
directly with negligible overhead.
"""

import os
import sys

NAT_BIN = "/vss-agent/.venv/bin/nat"
_TRUTHY = {"1", "true", "yes", "on"}


def _truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in _TRUTHY


def main() -> None:
    if _truthy(os.environ.get("INSTALL_PROPRIETARY_CODECS")):
        # Imported lazily; this script's own directory is on sys.path[0].
        import install_proprietary_codecs

        target = install_proprietary_codecs.install()
        if target:
            existing = os.environ.get("PYTHONPATH", "")
            os.environ["PYTHONPATH"] = target + (os.pathsep + existing if existing else "")

    os.execv(NAT_BIN, [NAT_BIN, *sys.argv[1:]])


if __name__ == "__main__":
    main()
