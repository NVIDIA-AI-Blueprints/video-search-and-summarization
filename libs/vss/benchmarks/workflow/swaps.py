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

"""Read-only comparison of explicitly named single-module diagnostic arms."""

import re
from copy import deepcopy

from benchmarks.e2e.config import fingerprint
from benchmarks.workflow.relay import request_identity

OPERATIONAL_FIELDS = {
    "video",
    "stack_manifest",
    "normal_config_sha256",
    "caption_input",
    "caption_capture",
    "output_capture",
}


def contract(profile):
    body = profile.model_dump(mode="json", exclude=OPERATIONAL_FIELDS)
    body["caption_capture_enabled"] = profile.caption_capture is not None
    body["output_capture_enabled"] = profile.output_capture is not None
    for field in ("caption_prompt", "summary_prompt"):
        body[field + "_sha256"] = fingerprint(body.pop(field))
    return body


def compare(reports, slot):
    if slot not in {"store_backend", "captioning"} or not 2 <= len(reports) <= 3:
        raise ValueError("swap_group")
    controls = []
    values = []
    rows = []
    for field in ("software_sha256", "lockfile_sha256", "harness_sha256"):
        values_ = [r.get(field) for r in reports]
        if (
            any(
                not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value)
                for value in values_
            )
            or len(set(values_)) != 1
        ):
            raise ValueError("software_or_lock_identity")
    if not reports[0].get("observation_policy") or any(
        r.get("observation_policy") != reports[0]["observation_policy"]
        for r in reports[1:]
    ):
        raise ValueError("observation_policy_changed")
    for report in reports:
        if (
            report["status"] != "completed_diagnostic"
            or report["qualified"] is not False
            or report["comparative_statistics"] is not None
        ):
            raise ValueError("failed_or_qualified_arm")
        body = deepcopy(report["module_comparison_contract"])
        values.append(body.pop(slot))
        controls.append(body)
        op = report["operation"]
        if (
            op["status"] != "completed_diagnostic"
            or not all(op["cleanup"].values())
            or op["cleanup"].get("store_closed") is not True
            or op["cleanup"].get("relay_sealed_complete") is not True
        ):
            raise ValueError("incomplete_arm")
        rows.append(op)
    if len(set(values)) != len(values) or any(
        body != controls[0] for body in controls[1:]
    ):
        raise ValueError("more_than_one_slot_changed")
    upstream = [
        (op["resolved_endpoint_sha256"]["rtvi_url"], op["llm_relay"]["upstream_sha256"])
        for op in rows
    ]
    if any(value != upstream[0] for value in upstream[1:]):
        raise ValueError("provider_changed")
    summary_inputs = []
    controls_complete = True
    for op in rows:
        calls = [
            c
            for c in op["llm_relay"]["records"]
            if c["phase"] == "after_client_admission"
        ]
        identities = [request_identity(call) for call in calls]
        controls_complete = (
            controls_complete
            and bool(identities)
            and all(identity is not None for identity in identities)
        )
        summary_inputs.append(identities)
    caption_hashes = [op["caption_sha256"] for op in rows]
    if slot == "captioning":
        actual = [
            (
                op["frame_provider_observation"]["selected"],
                op["frame_provider_observation"]["inputs"],
            )
            for op in rows
        ]
        inputs_equal = all(value == actual[0] for value in actual[1:])
    else:
        inputs_equal = len(set(caption_hashes)) == 1
    return {
        "scope": "single_module_diagnostics",
        "qualified": False,
        "comparative_statistics": None,
        "slot": slot,
        "values": values,
        "one_requested_slot_verified": True,
        "operational_fields_excluded": sorted(OPERATIONAL_FIELDS),
        "effective_caption_inputs_equal": inputs_equal,
        "summary_request_controls_complete": controls_complete,
        "summary_requests_equal": controls_complete
        and all(value == summary_inputs[0] for value in summary_inputs[1:]),
        "output_equality_required": False,
        "caption_outputs_equal": len(set(caption_hashes)) == 1,
        "summary_outputs_equal": len({op["summary_sha256"] for op in rows}) == 1,
        "semantic_quality": "not_reviewed",
        "rows": [
            {
                "module": value,
                "boundary": op["timing_boundary"],
                "workflow_to_result_s": op["workflow_to_result_s"],
                "stages_s": op["stages_s"],
            }
            for value, op in zip(values, rows, strict=True)
        ],
    }
