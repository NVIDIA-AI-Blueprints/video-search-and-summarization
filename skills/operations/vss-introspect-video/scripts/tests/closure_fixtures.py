# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import copy

import closure_gate

ANCHORS = {
    "subject": "target",
    "event": "approach",
    "state": "before turn",
    "reference_frame": "camera",
}


def contract_for(plan):
    return {
        c["claim_id"]: {
            "depends_on": [],
            "requirements": [
                {
                    "requirement_id": "fact",
                    "test": c["support_test"],
                    "applicability": copy.deepcopy(ANCHORS),
                }
            ],
        }
        for c in plan["claims"]
    }


def upgrade_for_cli(current):
    """Adapt legacy publication fixtures; new gate behavior has dedicated tests."""
    current = copy.deepcopy(current)
    current["closure_contract"] = contract_for(current["plan"])
    current["closure_certificates"] = {
        s["claim_id"]: [
            {
                "requirement_id": "fact",
                "coverage_sufficient": s["coverage"] == "sufficient",
                "evidence": [
                    {"observation_id": oid, "applicability": copy.deepcopy(ANCHORS)}
                    for oid in s["observation_ids"]
                ],
            }
        ]
        for s in current["claims"]
    }
    closure_gate.refresh(current)
    return current
