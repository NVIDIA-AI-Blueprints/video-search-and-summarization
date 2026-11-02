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

"""Unit tests for ``POST /api/v1/incidents/{video_id}/box/describe`` and its reply parser."""

from __future__ import annotations

from typing import ClassVar
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import numpy as np
import pytest

from vss_agents.api.incident_box_describe import create_incident_box_describe_router
from vss_agents.utils import crop_evidence

URL = "/api/v1/incidents/vid-1/box/describe"
BODY = {"seconds": 2.5, "box": [100, 200, 600, 700]}
GOOD = {
    "item_type": "instrument",
    "name": " crowbar ",
    "description": "A steel crowbar.",
    "holder": "man in the grey hoodie",
    "threat_level": 4,
    "confidence": 0.8,
}


class FakeModel:
    """Stands in for the detector: records what it was sent, answers with a canned reply or raises it."""

    def __init__(self, reply: object = None, *, available: bool = True) -> None:
        self.reply = reply
        self.available = available
        self.calls: list[tuple[bytes, bytes]] = []

    def is_available(self) -> bool:
        return self.available

    def describe_box(self, jpeg: bytes, *, context_jpeg: bytes) -> crop_evidence.BoxProposal:
        self.calls.append((jpeg, context_jpeg))
        if isinstance(self.reply, Exception):
            raise self.reply
        assert isinstance(self.reply, dict)
        return crop_evidence.parse_box_proposal(self.reply)


def _post(model: FakeModel, *, video: object = {"filepath": "uploads/a.mp4"}, frame: object = "ok", body=BODY):
    db = MagicMock()
    db.get_video = AsyncMock(return_value=video)
    app = FastAPI()
    app.include_router(create_incident_box_describe_router(model))
    frame_value = np.zeros((90, 160, 3), dtype=np.uint8) if frame == "ok" else frame
    with (
        patch("vss_agents.api.incident_box_describe.incident_db.get_db", new_callable=AsyncMock, return_value=db),
        patch("vss_agents.api.incident_box_describe.crop_evidence.extract_frame", return_value=frame_value) as read,
    ):
        response = TestClient(app).post(URL, json=body)
    return response, read


def test_returns_the_proposal_and_sends_the_close_up_and_marked_frame() -> None:
    model = FakeModel(GOOD)
    response, read = _post(model)
    assert response.status_code == 200
    assert response.json() == {
        "proposal": {
            "item_type": "instrument",
            "name": "crowbar",
            "description": "A steel crowbar.",
            "holder": "man in the grey hoodie",
            "threat_level": 4,
            "confidence": 0.8,
        },
        "reason": None,
    }
    assert read.call_args.args[1] == 2.5
    assert len(model.calls) == 1
    assert all(model.calls[0])  # both JPEGs were cut


def test_entity_and_asset_drop_holder_and_threat_level() -> None:
    reply = {**GOOD, "item_type": "entity", "name": "person"}
    proposal = _post(FakeModel(reply))[0].json()["proposal"]
    assert (proposal["holder"], proposal["threat_level"]) == (None, None)


def test_model_that_cannot_tell_gives_null_proposal_with_a_reason() -> None:
    response, _ = _post(FakeModel({"item_type": None, "reason": "Too blurry to tell."}))
    assert response.status_code == 200
    assert response.json() == {"proposal": None, "reason": "Too blurry to tell."}


def test_unknown_video_is_404() -> None:
    assert _post(FakeModel(GOOD), video=None)[0].status_code == 404


def test_second_with_no_frame_is_422() -> None:
    response, _ = _post(FakeModel(GOOD), frame=None)
    assert response.status_code == 422


@pytest.mark.parametrize("error", [httpx.ConnectError("down"), ValueError("not json")])
def test_model_failure_is_502(error: Exception) -> None:
    assert _post(FakeModel(error))[0].status_code == 502


def test_model_timeout_is_502_with_a_plain_message() -> None:
    response, _ = _post(FakeModel(httpx.ReadTimeout("")))
    assert response.status_code == 502
    assert "did not answer in time" in response.json()["detail"]


def test_missing_vlm_key_is_503() -> None:
    assert _post(FakeModel(GOOD, available=False))[0].status_code == 503


def test_unconfigured_database_is_503() -> None:
    app = FastAPI()
    app.include_router(create_incident_box_describe_router(FakeModel(GOOD)))
    with patch("vss_agents.api.incident_box_describe.incident_db.get_db", new_callable=AsyncMock, return_value=None):
        assert TestClient(app).post(URL, json=BODY).status_code == 503


@pytest.mark.parametrize(
    "body",
    [
        {"seconds": -1, "box": [1, 2, 3, 4]},
        {"seconds": 1, "box": [1, 2, 3]},
        {"seconds": 1, "box": [500, 2, 100, 4]},
        {"seconds": 1, "box": [0, 0, 1001, 10]},
        {"seconds": 1},
    ],
)
def test_bad_body_is_422(body: dict) -> None:
    assert _post(FakeModel(GOOD), body=body)[0].status_code == 422


@pytest.mark.parametrize(
    "reply",
    [
        {"item_type": "vehicle", "name": "x", "description": "y", "confidence": 0.5},
        {"item_type": "asset", "name": "", "description": "y", "confidence": 0.5},
        {"item_type": "asset", "name": "x", "description": "", "confidence": 0.5},
        {"item_type": "asset", "name": "x", "description": "y", "confidence": 1.5},
        {"item_type": "instrument", "name": "x", "description": "y", "threat_level": 9, "confidence": 0.5},
        {"item_type": "instrument", "name": "x", "description": "y", "holder": 3, "confidence": 0.5},
    ],
)
def test_parser_refuses_anything_but_the_strict_reply(reply: dict) -> None:
    with pytest.raises(ValueError):
        crop_evidence.parse_box_proposal(reply)


def test_detector_sends_describe_box_prompt_with_frame_then_close_up(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict] = []

    class Reply:
        status_code = 200
        headers: ClassVar[dict] = {}

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            import json

            return {"choices": [{"message": {"content": json.dumps(GOOD)}}]}

    def fake_post(url: str, *, json: dict, headers: dict, timeout: float) -> Reply:
        sent.append(json)
        return Reply()

    monkeypatch.setattr(crop_evidence.httpx, "post", fake_post)
    detector = crop_evidence.DefaultDetector(model_name="m", base_url="http://x.invalid", api_key="k")
    assert detector.describe_box(b"close-up", context_jpeg=b"frame").name == "crowbar"
    content = sent[0]["messages"][0]["content"]
    assert [part["type"] for part in content] == ["image_url", "image_url", "text"]
    assert content[2]["text"].startswith("The first image is a video frame with one thing marked")


def _reply_class() -> type:
    class Reply:
        status_code = 200
        headers: ClassVar[dict] = {}

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            import json

            return {"choices": [{"message": {"content": json.dumps(GOOD)}}]}

    return Reply


def test_detector_turns_reasoning_off_and_uses_the_shorter_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[dict, float]] = []

    def fake_post(url: str, *, json: dict, headers: dict, timeout: float):
        seen.append((json, timeout))
        return _reply_class()()

    monkeypatch.setattr(crop_evidence.httpx, "post", fake_post)
    detector = crop_evidence.DefaultDetector(model_name="m", base_url="http://x.invalid", api_key="k")
    detector.describe_box(b"close-up", context_jpeg=b"frame")
    body, timeout = seen[0]
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert timeout == crop_evidence.DESCRIBE_BOX_TIMEOUT_SECONDS


def test_detector_tries_a_timed_out_describe_once_more(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def fake_post(url: str, *, json: dict, headers: dict, timeout: float):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ReadTimeout("")
        return _reply_class()()

    monkeypatch.setattr(crop_evidence.httpx, "post", fake_post)
    detector = crop_evidence.DefaultDetector(model_name="m", base_url="http://x.invalid", api_key="k")
    assert detector.describe_box(b"close-up", context_jpeg=b"frame").name == "crowbar"
    assert len(calls) == 2


def test_detector_gives_up_after_two_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def fake_post(url: str, *, json: dict, headers: dict, timeout: float):
        calls.append(1)
        raise httpx.ReadTimeout("")

    monkeypatch.setattr(crop_evidence.httpx, "post", fake_post)
    detector = crop_evidence.DefaultDetector(model_name="m", base_url="http://x.invalid", api_key="k")
    with pytest.raises(httpx.ReadTimeout):
        detector.describe_box(b"close-up", context_jpeg=b"frame")
    assert len(calls) == 2
