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

"""Unit tests for the reviewer-box tracker (``utils/box_track.py``) and ``POST /api/v1/incidents/{id}/box/track``.

The clips and the oracle tracker and detector are the ones of ``test_object_tracking``: a bright square moving right.
"""

from pathlib import Path
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from vss_agents.api.incident_box_track import create_incident_box_track_router
from vss_agents.utils.box_track import BoxTrackUnreadableError
from vss_agents.utils.box_track import detector_label
from vss_agents.utils.box_track import track_from_box

from .test_object_tracking import FPS
from .test_object_tracking import OracleDetector
from .test_object_tracking import OracleTracker
from .test_object_tracking import clip  # noqa: F401  (pytest fixture)
from .test_object_tracking import seed_at
from .test_object_tracking import settings
from .test_object_tracking import vanishing_clip  # noqa: F401  (pytest fixture)


def call(path: Path, seconds: float, detector: OracleDetector | None = None, **overrides: object) -> dict:
    seed = seed_at(seconds)
    args: dict = {
        "seconds": seconds,
        "box": list(seed.box),
        "name": "crowbar",
        "description": "grey steel bar",
        "detect": (detector or OracleDetector()).detect,
        "settings": settings(),
        "tracker_factory": OracleTracker,
    }
    args.update(overrides)
    return track_from_box(path, **args)


class TestTrackFromBox:
    def test_follows_both_ways_and_is_complete(self, clip: Path) -> None:  # noqa: F811
        track = call(clip, 3.0)
        assert track["source"] == "reviewer"
        assert track["partial"] is False and track["lost_at"] is None and track["reason"] is None
        first, last = track["span"]
        assert first == pytest.approx(0.0, abs=0.01) and last == pytest.approx(6.0, abs=0.3)
        assert any(sample["t"] == pytest.approx(3.0, abs=0.01) for sample in track["samples"])
        assert [s["t"] for s in track["samples"]] == sorted(s["t"] for s in track["samples"])
        assert set(track["samples"][0]) == {"t", "box", "conf", "score"}

    def test_window_sizes_are_respected(self, clip: Path) -> None:  # noqa: F811
        track = call(clip, 3.0, back_seconds=1.0, forward_seconds=0.0)
        assert track["span"][0] == pytest.approx(2.0, abs=0.01)
        assert track["span"][1] == pytest.approx(3.0, abs=0.01)

    def test_asks_the_detector_with_name_and_description(self, clip: Path) -> None:  # noqa: F811
        detector = OracleDetector()
        call(clip, 3.0, detector)
        assert detector.queries and set(detector.queries) == {"crowbar: grey steel bar"}

    def test_lost_item_is_partial_with_lost_at(self, vanishing_clip: Path) -> None:  # noqa: F811
        track = call(vanishing_clip, 3.0, forward_seconds=4.0)
        assert track["partial"] is True
        assert track["lost_at"] == pytest.approx(5.0, abs=0.3)
        assert track["span"][1] < 5.0
        assert "lost the item at 0:0" in track["reason"]

    def test_big_seed_box_is_partial_with_one_sample(self, clip: Path) -> None:  # noqa: F811
        track = call(clip, 3.0, box=[0, 0, 1000, 1000])
        assert track["partial"] is True and len(track["samples"]) == 1
        assert track["span"] == [3.0, 3.0] and "covers" in track["reason"]

    def test_detector_budget_marks_partial(self, clip: Path) -> None:  # noqa: F811
        from vss_agents.utils.crop_evidence import CropCallBudget
        from vss_agents.utils.crop_evidence import with_call_budget

        detector = with_call_budget(OracleDetector(), CropCallBudget(1))
        track = call(clip, 3.0, detect=detector.detect, settings=settings(detect_interval_seconds=0.5))
        assert track["partial"] is True and "detector call limit" in track["reason"]

    def test_unreadable_clip_raises(self, tmp_path: Path) -> None:
        missing = tmp_path / "nope.mp4"
        with pytest.raises(BoxTrackUnreadableError):
            call(missing, 3.0)

    def test_label(self) -> None:
        assert detector_label(" bag ", "") == "bag"
        assert detector_label("bag", "black duffel") == "bag: black duffel"
        assert len(detector_label("a", "b" * 1000)) == 240

    def test_fps_constant_sanity(self) -> None:
        assert FPS == 10


def client(video: dict | None, *, db_available: bool = True, tracker: object = OracleTracker) -> TestClient:
    db = MagicMock()
    db.get_video = AsyncMock(return_value=video)
    app = FastAPI()
    app.include_router(create_incident_box_track_router())
    patches = [
        patch(
            "vss_agents.api.incident_box_track.incident_db.get_db",
            new_callable=AsyncMock,
            return_value=db if db_available else None,
        ),
        patch("vss_agents.api.incident_box_track.select_tracker", return_value=("fake", tracker)),
    ]
    for p in patches:
        p.start()
    test_client = TestClient(app)
    test_client.__dict__["_patches"] = patches
    return test_client


@pytest.fixture(autouse=True)
def _stop_patches() -> None:
    yield
    patch.stopall()


def body(**overrides: object) -> dict:
    seed = seed_at(3.0)
    return {"seconds": 3.0, "box": list(seed.box), "item_type": "instrument", "name": "crowbar", **overrides}


class TestEndpoint:
    def test_returns_the_contract_track(self, clip: Path) -> None:  # noqa: F811
        with (
            patch("vss_agents.api.incident_box_track.resolve_r2_url", return_value=str(clip)),
            patch(
                "vss_agents.api.incident_box_track.DefaultDetector.detect",
                side_effect=lambda frame, query: OracleDetector().detect(frame, query),
            ),
        ):
            response = client({"id": "V1", "filepath": "videos/V1.mp4"}).post(
                "/api/v1/incidents/V1/box/track", json=body()
            )
        assert response.status_code == 200
        track = response.json()["track"]
        assert track["source"] == "reviewer" and track["partial"] is False and len(track["samples"]) > 3

    def test_unknown_video_is_404(self) -> None:
        assert client(None).post("/api/v1/incidents/V1/box/track", json=body()).status_code == 404

    def test_video_without_file_is_404(self) -> None:
        assert (
            client({"id": "V1", "filepath": None}).post("/api/v1/incidents/V1/box/track", json=body()).status_code
            == 404
        )

    def test_no_database_is_503(self) -> None:
        assert client(None, db_available=False).post("/api/v1/incidents/V1/box/track", json=body()).status_code == 503

    def test_unreadable_video_is_502(self, tmp_path: Path) -> None:
        with patch("vss_agents.api.incident_box_track.resolve_r2_url", return_value=str(tmp_path / "x.mp4")):
            response = client({"filepath": "k"}).post("/api/v1/incidents/V1/box/track", json=body())
        assert response.status_code == 502

    @pytest.mark.parametrize(
        "overrides",
        [
            {"box": [10, 10, 5, 50]},
            {"box": [1, 2, 3]},
            {"box": [0, 0, 1001, 5]},
            {"seconds": -1},
            {"name": ""},
            {"forward_seconds": 999},
        ],
    )
    def test_invalid_body_is_422(self, overrides: dict) -> None:
        assert (
            client({"filepath": "k"}).post("/api/v1/incidents/V1/box/track", json=body(**overrides)).status_code == 422
        )
