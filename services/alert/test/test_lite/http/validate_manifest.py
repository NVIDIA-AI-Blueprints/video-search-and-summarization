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

"""Validate that alert_expectations.yaml covers all JSON payloads."""
from pathlib import Path
import sys
import yaml


def main() -> int:
    base_dir = Path(__file__).parent
    manifest_path = base_dir / 'alert_expectations.yaml'
    with open(manifest_path, 'r') as f:
        data = yaml.safe_load(f) or {}
    data.pop('defaults', None)
    manifest_keys = set(data.keys())
    payload_files = {p.name for p in base_dir.glob('*.json')}
    missing = payload_files - manifest_keys
    stale = manifest_keys - payload_files
    if missing:
        print(f"Missing manifest entries: {', '.join(sorted(missing))}", file=sys.stderr)
    if stale:
        print(f"Stale manifest entries: {', '.join(sorted(stale))}", file=sys.stderr)
    if missing or stale:
        return 1
    print('Manifest covers all payloads.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())



