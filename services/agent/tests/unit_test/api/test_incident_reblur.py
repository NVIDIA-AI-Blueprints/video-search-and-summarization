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

"""Unit tests for ``POST /api/v1/incidents/{incident_id}/reblur``."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING
from typing import Any
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from vss_agents.api.incident_reblur import create_incident_reblur_router

if TYPE_CHECKING:
    from collections.abc import Iterator

PAYLOAD = {"incident": {"type": "theft"}, "entities": [{"entity_id": "E1"}], "instruments": [], "assets": []}
EVIDENCE = [{"item_id": "E1", "frame": "evidence/k/run-1/E1-frame.jpg", "image": "evidence/k/run-1/E1.jpg"}]
NEW_EVIDENCE = [{"item_id": "E1", "frame": "evidence/k/run-2/E1-frame.jpg", "image": "evidence/k/run-2/E1.jpg"}]
BODY = {
    "source_revision_id": 5,
    "video_url": "https://r2.example/v.mp4",
    "video_key": "k",
    "blur_faces": True,
    "model_run_id": "run-new",
    "model": "gemini",
    "video_duration_seconds": 12.5,
}


class Harness:
    def __init__(self, source: dict[str, Any] | None, *, store: bool, detector: bool) -> None:
        self.db = MagicMock()
        self.db.get_revision = AsyncMock(return_value=source)
        self.db.insert_model_run_if_absent = AsyncMock()
        self.db.insert_ai_revision = AsyncMock(return_value={"id": 9, "revision_no": 3})
        self.service = MagicMock()
        self.service.store = MagicMock() if store else None
        self.service.is_detector_available.return_value = detector
        self.reblur = AsyncMock(return_value=NEW_EVIDENCE)
        self.client = TestClient(self._app())

    @staticmethod
    def _app() -> FastAPI:
        app = FastAPI()
        app.include_router(create_incident_reblur_router())
        return app


def _source(**overrides: Any) -> dict[str, Any]:
    return {
        "id": 5,
        "video_id": "v1",
        "revision_no": 2,
        "payload": PAYLOAD,
        "evidence": EVIDENCE,
        "embedding": json.dumps([0.5, 0.25]),
        **overrides,
    }


@pytest.fixture
def harness() -> Iterator[Any]:
    started: list[Any] = []

    def make(
        source: dict[str, Any] | None = None, *, store: bool = True, detector: bool = True, db: bool = True
    ) -> Harness:
        h = Harness(_source() if source is None else source, store=store, detector=detector)
        patchers = [
            patch("vss_agents.api.incident_reblur.incident_db.is_configured", return_value=db),
            patch(
                "vss_agents.api.incident_reblur.incident_db.get_db",
                new_callable=AsyncMock,
                return_value=h.db if db else None,
            ),
            patch("vss_agents.api.incident_reblur.crop_evidence.get_crop_evidence_service", return_value=h.service),
            patch("vss_agents.api.incident_reblur.reblur_evidence.reblur_evidence", h.reblur),
            patch("vss_agents.api.incident_reblur.revision_embedding", new_callable=AsyncMock, return_value=[9.0]),
        ]
        for patcher in patchers:
            patcher.start()
            started.append(patcher)
        return h

    yield make
    for patcher in started:
        patcher.stop()


def test_a_new_revision_keeps_the_payload_and_gets_the_new_images(harness: Any) -> None:
    h = harness()

    response = h.client.post("/api/v1/incidents/v1/reblur", json=BODY)

    assert response.status_code == 200
    body = response.json()
    assert body["revision_id"] == 9 and body["source_revision_id"] == 5 and body["blur_faces"] is True
    assert body["items"] == 1 and body["images"] == 1 and "privacy" in body
    args = h.db.insert_ai_revision.await_args
    assert args.args == ("v1",)
    assert args.kwargs["model_run_id"] == "run-new"
    assert args.kwargs["payload"] is PAYLOAD
    assert args.kwargs["evidence"] == NEW_EVIDENCE
    assert args.kwargs["embedding"] == [0.5, 0.25]  # the source's vector, not computed again
    assert "revision 2" in args.kwargs["note"] and "report unchanged" in args.kwargs["note"]
    h.db.insert_model_run_if_absent.assert_awaited_once()
    call = h.reblur.await_args.kwargs
    assert call["blur"] is True and call["source_evidence"] == EVIDENCE and call["video_key"] == "k"
    assert call["video_seconds"] == 12.5 and call["detector"] is h.service.detector


def test_without_the_blur_no_detector_is_passed_or_needed(harness: Any) -> None:
    h = harness(detector=False)

    response = h.client.post("/api/v1/incidents/v1/reblur", json={**BODY, "blur_faces": False})

    assert response.status_code == 200
    assert "privacy" not in response.json()
    call = h.reblur.await_args.kwargs
    assert call["blur"] is False and call["detector"] is None and call["privacy"] is None
    assert "not blurred" in h.db.insert_ai_revision.await_args.kwargs["note"]


def test_a_source_without_a_stored_vector_gets_one_computed(harness: Any) -> None:
    h = harness(_source(embedding=None))

    h.client.post("/api/v1/incidents/v1/reblur", json=BODY)

    assert h.db.insert_ai_revision.await_args.kwargs["embedding"] == [9.0]


@pytest.mark.parametrize(
    ("kwargs", "source", "status"),
    [
        ({"db": False}, None, 503),
        ({"store": False}, None, 503),
        ({"detector": False}, None, 503),
        ({}, {"__none__": True}, 404),
        ({}, _source(video_id="other"), 409),
        ({}, _source(evidence=None), 409),
        ({}, _source(payload="x"), 409),
    ],
)
def test_the_documented_error_statuses(harness: Any, kwargs: dict[str, Any], source: Any, status: int) -> None:
    h = harness(source if source != {"__none__": True} else None, **kwargs)
    if source == {"__none__": True}:
        h.db.get_revision.return_value = None

    response = h.client.post("/api/v1/incidents/v1/reblur", json=BODY)

    assert response.status_code == status
    h.reblur.assert_not_awaited()
    h.db.insert_ai_revision.assert_not_awaited()


def test_an_invalid_body_is_422(harness: Any) -> None:
    h = harness()

    assert h.client.post("/api/v1/incidents/v1/reblur", json={**BODY, "blur_faces": None}).status_code == 422
    assert h.client.post("/api/v1/incidents/v1/reblur", json={"video_url": "x"}).status_code == 422


def test_an_unexpected_failure_is_a_500_and_inserts_nothing(harness: Any) -> None:
    h = harness()
    h.reblur.side_effect = RuntimeError("boom")

    response = h.client.post("/api/v1/incidents/v1/reblur", json=BODY)

    assert response.status_code == 500 and "boom" in response.json()["detail"]
    h.db.insert_ai_revision.assert_not_awaited()


def test_images_that_take_too_long_are_a_504_and_no_revision_is_written(harness: Any) -> None:
    h = harness()

    async def never(**_: Any) -> list[dict[str, Any]]:
        await asyncio.sleep(10)
        return NEW_EVIDENCE

    h.reblur.side_effect = never
    with patch("vss_agents.api.incident_reblur.REBLUR_DEADLINE_SECONDS", 0.05):
        response = h.client.post("/api/v1/incidents/v1/reblur", json=BODY)

    assert response.status_code == 504 and "nothing was stored" in response.json()["detail"]
    h.db.insert_ai_revision.assert_not_awaited()
    h.db.insert_model_run_if_absent.assert_not_awaited()
