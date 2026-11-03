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

"""Shared chat-task fixtures: a three-incident ground truth's references and Track B predictions (no heavy imports)."""

from __future__ import annotations

import sys
from pathlib import Path

from evalv2.references import chat as refs

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_v2_chat_references import GT  # noqa: E402

CLIPS = sorted(GT["by_incident_id"])
GEN = {"prompt": "gen-chat-wording-v1", "model": "claude"}
VAL = {"id": "gen-chat-rederive-v1", "model": "claude"}


def _record(question_class, params, *, validated=True, question=None):
    expected = None if question_class == "incident.facts" else refs.answer(question_class, GT, params)
    kwargs = {"facts": ["a man hits another man"]} if question_class == "incident.facts" else {}
    if validated and expected is not None:
        kwargs |= {"rederived": expected, "validator": VAL}
    return refs.build_record(question_class, params, GT, question=question or f"Q {question_class} {params}?",
                             paraphrases=[f"P1 {question_class} {params}"], dataset="chat-test", generator=GEN,
                             **kwargs)


RECORDS = [
    _record("incident.type", {"incident_id": "Assault001"}),
    _record("incident.people_count", {"incident_id": "Burglary003"}),
    _record("incident.unanswerable", {"incident_id": "Assault002"}, question="What is the licence plate?"),
    _record("incident.facts", {"incident_id": "Assault001"}, validated=False),
    _record("library.type_count", {"type": "assault", "corpus": "dev"}, question="How many assault incidents?"),
    _record("library.retrieval", {"targets": ["Burglary003"], "corpus": "dev"}, validated=False),
    _record("library.type_count", {"type": "burglary", "corpus": "test"}),
]


def _prediction(clip, *, valid=True):
    report = GT["by_incident_id"][clip]["p1_shaped"]
    return {"predictions": {"final": report if valid else None, "p1": report},
            "evidence": [{"item_id": "E1", "item_type": "entity", "box_seconds": 3, "image": "evidence/x/E1.jpg",
                          "frame": "frames/x.jpg", "description": "a man"}]}
