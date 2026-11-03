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

"""The agent's model requests equal the shared request goldens in the profile's ``contracts/golden/``.

The console and eval check the same files, so P1 and RP1 are equal across all three; the caption, grounding and
zoom requests have no other producer, and their goldens pin today's prompts before they move into a registry.
"""

from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from vss_agents.tools import incident_report_gen
from vss_agents.utils import chunk_captions
from vss_agents.utils import crop_evidence
from vss_agents.utils import grounded_chat
from vss_agents.utils import set_of_marks
from vss_agents.utils import track_evidence

GOLDEN_DIR = (
    Path(__file__).resolve().parents[5] / "deploy/docker/developer-profiles/dev-profile-incident/contracts/golden"
)
_spec = importlib.util.spec_from_file_location("load_golden", GOLDEN_DIR / "load_golden.py")
if _spec is None or _spec.loader is None:
    raise ImportError(f"Could not load {GOLDEN_DIR / 'load_golden.py'}")
golden = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(golden)

P1_CASES = ["base", "reviewer", "captions", "marks", "captions-and-marks"]
ZOOM_ITEM_TYPES = ["entity", "asset"]


def _zoom_steps(item_type: str) -> list[str]:
    """The calls of one zoomed second look, in order: two blind looks, then the merge (the looks agree)."""
    return [f"look-{item_type}-frame", f"look-{item_type}-close-up", f"merge-{item_type}"]


TESTED = sorted(
    [f"p1/{name}" for name in P1_CASES]
    + ["rp1/report", "caption/window", "caption/remainder", "grounding/best-match"]
    + ["chat/incident-answer", "chat/library-plan", "chat/library-answer"]
    + [f"zoom/{step}" for item_type in ZOOM_ITEM_TYPES for step in _zoom_steps(item_type)]
    + ["zoom/describe-box"]
)


def _agent_cases() -> list[str]:
    cases = []
    for path in sorted(GOLDEN_DIR.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        cases += [f"{path.stem}/{name}" for name, entry in document["cases"].items() if "agent" in entry["producers"]]
    return sorted(cases)


def _assert_golden(name: str, case: str, produced: dict[str, Any]) -> None:
    problem = golden.mismatch(name, case, produced, "agent")
    assert problem is None, problem


def test_every_agent_case_is_checked_here() -> None:
    assert _agent_cases() == TESTED


def _marks(rows: list[dict[str, Any]]) -> str:
    """The set-of-marks message for marks on screen from ``first_seen`` to ``last_seen``."""
    marks = [
        set_of_marks.Mark(
            row["number"],
            track_evidence.Track(
                f"t{row['number']}",
                [
                    track_evidence.Sample(row["first_seen"], (0, 0, 10, 10), 0.9),
                    track_evidence.Sample(row["last_seen"], (0, 0, 10, 10), 0.9),
                ],
            ),
        )
        for row in rows
    ]
    return set_of_marks.marks_context(marks)


@pytest.mark.parametrize("case", P1_CASES)
def test_p1_request_equals_the_golden(case: str) -> None:
    inputs = golden.case("p1", case)["inputs"]
    context = chunk_captions.context_block(inputs["timeline"]) if "timeline" in inputs else None
    marks = _marks(inputs["marks"]) if "marks" in inputs else None
    body = incident_report_gen.p1_request_body(
        inputs["model"],
        inputs["video_url"],
        reviewer_prompt=inputs.get("reviewer_prompt"),
        context_block=context,
        marks_block=marks,
    )
    _assert_golden("p1", case, body)


def test_rp1_request_equals_the_golden() -> None:
    inputs = golden.case("rp1", "report")["inputs"]
    _assert_golden("rp1", "report", incident_report_gen.rp1_request_body(inputs["report"]))


@pytest.mark.parametrize("case", ["window", "remainder"])
def test_caption_request_equals_the_golden(case: str) -> None:
    inputs = golden.case("caption", case)["inputs"]
    body = chunk_captions.caption_request_body(
        inputs["model"], inputs["video_url"], inputs["window_length"], inputs["num_frames"]
    )
    _assert_golden("caption", case, body)


def test_caption_golden_uses_the_default_window_settings() -> None:
    inputs = golden.case("caption", "window")["inputs"]
    assert inputs["window_length"] == chunk_captions.DEFAULT_WINDOW_SECONDS
    assert inputs["num_frames"] == chunk_captions.DEFAULT_NUM_FRAMES


def _detector(model: str) -> crop_evidence.DefaultDetector:
    return crop_evidence.DefaultDetector(model_name=model, base_url="http://golden.invalid", api_key="golden")


@pytest.mark.parametrize(("case", "method"), [("best-match", "detect")])
def test_grounding_request_equals_the_golden(monkeypatch: pytest.MonkeyPatch, case: str, method: str) -> None:
    inputs = golden.case("grounding", case)["inputs"]
    sent: list[dict[str, Any]] = []

    def fake_request_box(self: crop_evidence.DefaultDetector, body: dict[str, Any]) -> dict[str, Any]:
        sent.append(body)
        return {"bbox_2d": None, "detections": []}

    monkeypatch.setattr(crop_evidence.DefaultDetector, "_request_box", fake_request_box)
    getattr(_detector(inputs["model"]), method)(np.zeros((90, 160, 3), dtype=np.uint8), inputs["query"])
    assert len(sent) == 1
    _assert_golden("grounding", case, sent[0])


class _Reply:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.status_code = 200
        self.headers: dict[str, str] = {}
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {"choices": [{"message": {"content": json.dumps(self._payload)}}]}


@pytest.mark.parametrize("item_type", ZOOM_ITEM_TYPES)
def test_zoom_requests_equal_the_golden(monkeypatch: pytest.MonkeyPatch, item_type: str) -> None:
    steps = _zoom_steps(item_type)
    cases = [golden.case("zoom", step) for step in steps]
    inputs = cases[0]["inputs"]
    sent: list[dict[str, Any]] = []

    def fake_post(url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: float) -> _Reply:
        sent.append(json)
        return _Reply(cases[len(sent) - 1]["inputs"]["reply"])

    monkeypatch.setattr(crop_evidence.httpx, "post", fake_post)
    _detector(inputs["model"]).zoom_name(
        b"close-up",
        item_type=item_type,
        name=inputs["name"],
        description=inputs["description"],
        context_jpeg=b"frame",
    )
    assert len(sent) == len(steps), "the two looks agree, so the merge call is made"
    for step, body in zip(steps, sent, strict=True):
        _assert_golden("zoom", step, body)
    # The golden writes every inline JPEG as one placeholder, so it cannot see the images swapped; the prompt says
    # the frame comes first and the close-up second, and the decoded bytes must say the same.
    assert [_images(body) for body in sent] == [[b"frame"], [b"frame", b"close-up"], []]


def test_describe_box_request_equals_the_golden(monkeypatch: pytest.MonkeyPatch) -> None:
    case = golden.case("zoom", "describe-box")
    sent: list[dict[str, Any]] = []

    def fake_post(url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: float) -> _Reply:
        sent.append(json)
        return _Reply(case["inputs"]["reply"])

    monkeypatch.setattr(crop_evidence.httpx, "post", fake_post)
    _detector(case["inputs"]["model"]).describe_box(b"close-up")
    assert len(sent) == 1
    _assert_golden("zoom", "describe-box", sent[0])
    assert _images(sent[0]) == [b"close-up"]


def _images(body: dict[str, Any]) -> list[bytes]:
    """The inline images of a request, decoded, in the order sent."""
    return [
        base64.b64decode(part["image_url"]["url"].split(",", 1)[1])
        for message in body["messages"]
        if isinstance(message["content"], list)
        for part in message["content"]
        if part.get("type") == "image_url"
    ]


def test_chat_request_equals_the_golden() -> None:
    inputs = golden.case("chat", "incident-answer")["inputs"]
    model = grounded_chat.OpenAIChatModel(model=inputs["model"], base_url="http://golden.invalid", api_key="k")
    context = grounded_chat.report_context(inputs["report"], inputs["evidence"])
    body = model.request_body(grounded_chat.chat_messages(context, inputs["history"]), grounded_chat.ANSWER_SCHEMA)
    _assert_golden("chat", "incident-answer", body)


def test_library_plan_request_equals_the_golden() -> None:
    inputs = golden.case("chat", "library-plan")["inputs"]
    model = grounded_chat.OpenAIChatModel(model=inputs["model"], base_url="http://golden.invalid", api_key="k")
    messages = grounded_chat.plan_messages(inputs["question"], inputs["history"])
    _assert_golden("chat", "library-plan", model.request_body(messages, grounded_chat.PLAN_SCHEMA))


def test_library_answer_request_equals_the_golden() -> None:
    inputs = golden.case("chat", "library-answer")["inputs"]
    model = grounded_chat.OpenAIChatModel(model=inputs["model"], base_url="http://golden.invalid", api_key="k")
    plan = grounded_chat.check_plan(inputs["plan"])
    reports = [grounded_chat.LibraryReport(r["incident_id"], r["version_id"], r["payload"]) for r in inputs["reports"]]
    matching = [report for report in reports if grounded_chat.matches(report, plan)]
    context = grounded_chat.library_context(grounded_chat.rank(matching, plan["query"]), matching, plan)
    body = model.request_body(
        grounded_chat.library_messages(context, inputs["history"]), grounded_chat.LIBRARY_ANSWER_SCHEMA
    )
    _assert_golden("chat", "library-answer", body)
