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

"""Dashboard references ("Ask for a chart", methodology v2.0 §4 E, R4): the allowlist, every valid spec, records.

The allowlist's source is the console's TypeScript; ``npm run export:prompts`` exports it to
``contracts/prompts/chart/allowlist.json``, which :func:`load_allowlist` reads. :func:`parse_spec` is an independent
re-implementation of the console's ``parseChartSpec`` (exact keys, allowlisted values, the dimension's metric, and
no type or severity filter on a dimension that takes none), and :func:`all_specs` enumerates every valid spec (1,690
today). A reference is a chart request with its expected spec, or ``{"unsupported": true}``: a generator writes the
request for a code-chosen spec, a blind validator re-derives the spec from the request alone, and the record is
``ai_validated`` only when they agree exactly. Generating requests is an approval-gated run (RB2).
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from evalv2.core.hashing import sha256_json
from evalv2.datasets.records import new_record, promote_validated

ALLOWLIST_PATH = Path(__file__).resolve().parents[3] / "contracts" / "prompts" / "chart" / "allowlist.json"
SPEC_KEYS = ("metric", "dimension", "period", "incidentType", "severity")
UNSUPPORTED = {"unsupported": True}


class DashboardReferenceError(ValueError):
    """A malformed allowlist, request record or validator packet."""


def load_allowlist(path: Path = ALLOWLIST_PATH) -> dict[str, Any]:
    if not path.is_file():
        raise DashboardReferenceError(f"{path} is missing: run `npm run export:prompts` in incident-console-v2")
    allowlist: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return allowlist


def parse_spec(reply: Any, allowlist: Mapping[str, Any]) -> dict[str, Any] | None:
    """The spec a reply describes, or ``None`` (a refusal, an unknown key or value, or an inconsistent spec)."""
    if not isinstance(reply, dict) or set(reply) != set(SPEC_KEYS):
        return None
    dimension = allowlist["dimensions"].get(reply["dimension"])
    if (dimension is None or reply["metric"] not in allowlist["metrics"] or reply["period"] not in allowlist["periods"]
            or reply["incidentType"] not in (None, *allowlist["incidentTypes"])
            or reply["severity"] not in (None, *allowlist["severities"])):
        return None
    if dimension["metric"] != reply["metric"]:
        return None
    if not dimension["filterable"] and (reply["incidentType"] is not None or reply["severity"] is not None):
        return None
    return {key: reply[key] for key in SPEC_KEYS}


def is_refusal(reply: Any) -> bool:
    return isinstance(reply, dict) and reply == UNSUPPORTED


def all_specs(allowlist: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every valid spec, in a fixed order."""
    out = []
    for name, dimension in allowlist["dimensions"].items():
        types = (None, *allowlist["incidentTypes"]) if dimension["filterable"] else (None,)
        severities = (None, *allowlist["severities"]) if dimension["filterable"] else (None,)
        for period, incident_type, severity in itertools.product(allowlist["periods"], types, severities):
            out.append({"metric": dimension["metric"], "dimension": name, "period": period,
                        "incidentType": incident_type, "severity": severity})
    return out


def generator_packet(expected: Mapping[str, Any], *, prompt_id: str) -> dict[str, Any]:
    """The generator words a request for this spec (or an unsupported request when ``expected`` is the refusal)."""
    return {"role": "generator", "prompt": prompt_id, "expected": dict(expected)}


def validator_packet(record: Mapping[str, Any], allowlist: Mapping[str, Any], *, prompt_id: str) -> dict[str, Any]:
    """The blind pass: the request and the allowlist, never the expected spec."""
    return {"role": "validator", "prompt": prompt_id, "record_id": record["record_id"],
            "request": record["payload"]["request"], "allowlist": dict(allowlist)}


def assert_blind(validator: Mapping[str, Any], record: Mapping[str, Any]) -> None:
    if validator.get("role") != "validator" or {"expected", "paraphrases"} & set(validator):
        raise DashboardReferenceError("the validator packet holds the expected spec or paraphrases")
    if validator.get("request") != record["payload"]["request"]:
        raise DashboardReferenceError("the validator packet is for another request")


def build_record(request: str, expected: Mapping[str, Any], allowlist: Mapping[str, Any], *,
                 paraphrases: Sequence[str], dataset: str, generator: Mapping[str, Any],
                 rederived: Any = None, validator: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """One request record; ``expected`` must be a valid spec or the refusal."""
    refusal = is_refusal(expected)
    if not refusal and parse_spec(dict(expected), allowlist) is None:
        raise DashboardReferenceError(f"expected spec is not on the allowlist: {expected}")
    kind = "unsupported" if refusal else "spec"
    record_id = f"dash:{kind}:{sha256_json({'request': request, 'expected': dict(expected)})[:12]}"
    strata = {"class": kind, "dimension": None if refusal else expected["dimension"]}
    record = new_record(record_id=record_id, dataset=dataset, kind="dashboard_request", strata=strata,
                        payload={"request": request, "paraphrases": list(paraphrases), "expected": dict(expected)},
                        generator=dict(generator))
    if rederived is None or validator is None:
        return record
    agrees = is_refusal(rederived) if refusal else parse_spec(rederived, allowlist) == dict(expected)
    checks = [{"name": "expected_on_allowlist", "passed": True},
              {"name": "paraphrases_distinct", "passed": len({request, *paraphrases}) == 1 + len(paraphrases)}]
    return promote_validated(record, checks=checks, validator={**dict(validator), "agrees": agrees})
