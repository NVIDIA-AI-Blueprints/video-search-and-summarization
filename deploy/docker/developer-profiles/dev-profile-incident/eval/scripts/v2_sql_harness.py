# /// script
# requires-python = ">=3.12"
# dependencies = ["pgserver==0.1.4", "psycopg[binary]>=3.2,<4"]
# ///
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
"""Run production SQL on a frozen snapshot (throwaway environment; pgserver and psycopg are not eval dependencies).

    uv run --no-project --script scripts/v2_sql_harness.py dashboard --snapshot S.json --params P.json
    uv run --no-project --script scripts/v2_sql_harness.py summaries --snapshot S.json --params P.json

Applies ``supabase/migrations/`` to a fresh Postgres, loads the snapshot (``evalv2-sql-snapshot-v1``) and prints
``{"snapshot_sha256", "params", "result"}``. Dashboard calls need explicit period bounds, timezone and scope.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evalv2.sql.harness import SqlHarness  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("call", choices=("dashboard", "summaries"))
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--params", type=Path, required=True)
    args = parser.parse_args()
    snapshot = json.loads(args.snapshot.read_text())
    params = json.loads(args.params.read_text())
    with SqlHarness.start() as harness:
        digest = harness.load(snapshot)
        result = harness.dashboard(**params) if args.call == "dashboard" else harness.report_summaries(**params)
    print(json.dumps({"snapshot_sha256": digest, "params": params, "result": result}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
