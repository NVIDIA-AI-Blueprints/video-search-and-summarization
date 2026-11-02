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

"""The console's own chart code, replayed in Node on recorded dashboard answers (the third check of R4).

``scripts/v2_chart_replay.mjs`` imports the console's ``generateChart`` and its RPC mapper through the console's
test alias loader. It runs each case with three stand-ins: an interpreter that returns the case's spec, a
repository that answers only recorded RPC results (keyed by the exact arguments the console sends), and no
renderer.

:func:`replay_input` records every answer a case needs: its own query, plus the official and not-official queries
of a bar chart, which a split dimension asks for. :func:`run_replay` runs Node and returns its answers;
:func:`check_against` compares them with :func:`evalv2.references.dashboard_figures.chart` over the same answers.

Node must strip TypeScript: Node 22.18 or later, or 23.6 or later, named by ``EVALV2_NODE`` (else ``node``). The
console's dependencies must be installed (``npm ci`` in ``incident-console-v2``). Nothing here calls a model, a
database or the network.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evalv2.references import dashboard_figures as figures

REPLAY_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "v2_chart_replay.mjs"
NODE_ENV = "EVALV2_NODE"


class ReplayUnavailable(RuntimeError):
    """No Node that can strip TypeScript, or the console's modules do not load."""


def rpc_key(params: Mapping[str, Any]) -> str:
    """The recorded-answer key, byte for byte as the replay script builds it from ``dashboardRpcArgs``."""
    return json.dumps({name: params.get(name) for name in sorted(figures.RPC_KEYS)}, separators=(",", ":"),
                      ensure_ascii=False)


def _instant(now: datetime) -> str:
    moment = now.astimezone(UTC) if now.tzinfo is not None else now.replace(tzinfo=UTC)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def replay_input(cases: Sequence[Mapping[str, Any]], *, timezone: str, now: datetime, allowlist: Mapping[str, Any],
                 fetch: figures.Fetch) -> dict[str, Any]:
    """The replay's input: the cases (``id``, ``spec`` or ``None``, ``chartType``) and every answer they need."""
    rpc: dict[str, Any] = {}
    for case in cases:
        if case.get("spec") is None:
            continue
        query = figures.chart_query(case["spec"], timezone=timezone, now=now, allowlist=allowlist)
        queries = [query]
        if case["chartType"] == "bar":
            queries += [{**query, "p_scope": scope} for scope in ("official", "not_official")]
        for params in queries:
            rpc.setdefault(rpc_key(params), fetch(params))
    return {"now": _instant(now), "timezone": timezone,
            "cases": [{"id": c["id"], "spec": c.get("spec"), "chartType": c["chartType"]} for c in cases], "rpc": rpc}


def node_command() -> list[str]:
    return [os.environ.get(NODE_ENV) or "node", "--experimental-strip-types", "--no-warnings"]


def available() -> bool:
    try:
        done = subprocess.run([*node_command(), str(REPLAY_SCRIPT), "--check"], capture_output=True, text=True,
                              timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0 and '"ok":true' in done.stdout


def run_replay(payload: Mapping[str, Any]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "replay-input.json"
        path.write_text(json.dumps(payload, ensure_ascii=False))
        try:
            done = subprocess.run([*node_command(), str(REPLAY_SCRIPT), str(path)], capture_output=True, text=True,
                                  timeout=600, check=False)
        except OSError as exc:
            raise ReplayUnavailable(f"cannot run Node: {exc}") from exc
    if done.returncode != 0:
        raise ReplayUnavailable(f"the replay failed ({done.returncode}): {done.stderr.strip()[-2000:]}")
    return json.loads(done.stdout)


def check_against(payload: Mapping[str, Any], replayed: Mapping[str, Any], *, allowlist: Mapping[str, Any]
                  ) -> dict[str, list[str]]:
    """Per case, where the console's answer differs from :func:`dashboard_figures.chart` on the same answers."""
    now = datetime.fromisoformat(payload["now"].replace("Z", "+00:00"))

    def fetch(params: Mapping[str, Any]) -> Mapping[str, Any]:
        return payload["rpc"][rpc_key(params)]

    by_id = {r["id"]: r for r in replayed["results"]}
    out: dict[str, list[str]] = {}
    for case in payload["cases"]:
        result = by_id.get(case["id"])
        if result is None or "error" in result:
            out[case["id"]] = [f"replay error: {(result or {}).get('error', 'no result')}"]
            continue
        expected = figures.chart(case["spec"], chart_type=case["chartType"], timezone=payload["timezone"], now=now,
                                 allowlist=allowlist, fetch=fetch)
        problems = figures.differences(figures.comparable(expected),
                                       figures.comparable(figures.from_console(result["answer"])))
        if problems:
            out[case["id"]] = problems
    return out
