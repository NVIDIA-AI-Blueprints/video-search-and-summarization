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
"""Decide whether a stream add/remove response reports a failure.

Used by scripts/add-streams.sh. Standard library only: add-streams.sh runs on the
host with the system python3, before any venv exists.

The REST API answers HTTP 200 and reports the failure in the body (bug 6558401),
so the status code cannot be trusted on its own. The wording is not stable either,
so this looks for the shape rather than an exact string: a success/ok field that is
false, or any string that folds to something like stream_add_fail.

Usage:  response_reports_failure.py RESPONSE_FILE add|remove
Exit:   0 the response reports a failure, 1 it does not (or is unreadable)
"""
import json
import re
import sys

path, action = sys.argv[1], sys.argv[2]

try:
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
except OSError:
    sys.exit(1)


def normalized(value):
    return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")


def value_reports_failure(value):
    folded = normalized(value)
    return (
        f"stream_{action}_fail" in folded
        or f"stream_{action}_failed" in folded
        or (
            "stream" in folded
            and action in folded
            and ("fail" in folded or "error" in folded)
        )
    )


try:
    payload = json.loads(text)
except json.JSONDecodeError:
    sys.exit(0 if value_reports_failure(text) else 1)

stack = [payload]
while stack:
    item = stack.pop()
    if isinstance(item, dict):
        for key, value in item.items():
            if normalized(key) in {"success", "ok"} and value is False:
                sys.exit(0)
            stack.append(value)
    elif isinstance(item, list):
        stack.extend(item)
    elif isinstance(item, str) and value_reports_failure(item):
        sys.exit(0)

sys.exit(1)
