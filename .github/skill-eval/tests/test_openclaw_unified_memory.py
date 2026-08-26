# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json

from agents.openclaw_unified_memory import GROUP_PREFIX, GROUP_SUFFIX, _group_envelope


def test_group_envelope_requires_four_turns() -> None:
    video_id = "vss-sample-warehouse-4min"
    payload = {
        "kind": "unified-memory-group",
        "group_id": video_id,
        "turns": [
            {"case_id": f"{video_id}-{index}", "prompt": "q"}
            for index in range(1, 5)
        ],
    }
    instruction = f"preamble\n{GROUP_PREFIX}{json.dumps(payload)}{GROUP_SUFFIX}\n"
    assert _group_envelope(instruction) == payload
