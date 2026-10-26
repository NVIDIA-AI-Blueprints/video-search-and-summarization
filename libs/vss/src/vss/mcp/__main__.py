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

"""Run the VSS MCP server over standard input/output."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from vss.mcp.server import create_server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", default="cosmos")
    parser.add_argument(
        "--workspace",
        type=Path,
        default=os.environ.get("VSS_MCP_WORKSPACE"),
        help="Root for local media paths (defaults to the current directory)",
    )
    args = parser.parse_args()
    create_server(args.namespace, args.workspace).run(transport="stdio")


if __name__ == "__main__":
    main()
