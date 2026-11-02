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

"""Unit tests for the re-blur of stored evidence: same entries, new images, the head detector as the only model call."""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
import tempfile
from typing import TYPE_CHECKING
from typing import Any

import cv2
import numpy as np
import pytest

from vss_agents.utils import privacy_blur
from vss_agents.utils import reblur_evidence
from vss_agents.utils import track_evidence
from vss_agents.utils.crop_evidence import Detection
from vss_agents.utils.crop_evidence import InMemoryEvidenceStore
from vss_agents.utils.crop_evidence import evidence_frame_key
from vss_agents.utils.crop_evidence import evidence_heads_key
from vss_agents.utils.crop_evidence import evidence_key

if TYPE_CHECKING:
    from collections.abc import Generator

WIDTH, HEIGHT = 200, 100
VIDEO_KEY = "uploads/clip.mp4"
PERSON_BOX = [200, 200, 800, 500]  # the detector's own box of the person (before the crop padding)
PADDED_PERSON_BOX = [170, 170, 830, 530]  # what the original run stored as ``bounding_box``
HEAD_A = [200, 300, 400, 400]
SOURCE_BATCH = "run-1"


@pytest.fixture
def video() -> Generator[Path]:
    """Two seconds at 10 fps of one textured frame, so every second shows the same picture."""
    rng = np.random.default_rng(7)
    blocks = rng.integers(0, 256, size=(HEIGHT // 4, WIDTH // 4, 3), dtype=np.uint8)
    frame = np.ascontiguousarray(np.repeat(np.repeat(blocks, 4, axis=0), 4, axis=1))
    with tempfile.NamedTemporaryFile(suffix=".avi", delete=False) as handle:
        path = Path(handle.name)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (WIDTH, HEIGHT))
    for _ in range(20):
        writer.write(frame)
    writer.release()
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


class HeadsOnlyDetector:
    """Answers the whole-frame head query; any other query (grounding) is a failure of the test."""

    def __init__(self, heads: list[list[int]] | None = None, fail: bool = False) -> None:
        self.heads = [HEAD_A] if heads is None else heads
        self.fail = fail
        self.queries: list[str] = []

    def is_available(self) -> bool:
        return True

    def detect(self, frame: np.ndarray, query: str) -> list[Detection]:
        self.queries.append(query)
        if query != privacy_blur.HEADS_QUERY:
            raise AssertionError(f"only the head detection may run, not {query!r}")
        if self.fail:
            raise RuntimeError("gateway down")
        return [Detection(box=list(head), score=1.0) for head in self.heads]


def _source_entries() -> list[dict[str, Any]]:
    """A person (located, tracked, zoom-checked) and an asset the detector never located, as a run stored them."""
    return [
        {
            "item_type": "entity",
            "item_id": "E1",
            "item_name": "man",
            "description": "a man in a hoodie",
            "label": "man",
            "threat_level": 0,
            "holder_entity_id": None,
            "timestamp_seconds": 1,
            "frame": evidence_frame_key(VIDEO_KEY, SOURCE_BATCH, "E1"),
            "image": evidence_key(VIDEO_KEY, SOURCE_BATCH, "E1"),
            "bounding_box": PADDED_PERSON_BOX,
            "detected_box": PERSON_BOX,
            "box_seconds": 1.0,
            "located": True,
            "image_gap": None,
            "track": f"evidence/{VIDEO_KEY}/{SOURCE_BATCH}/E1-track.json",
            "track_first_seen_seconds": 0.0,
            "track_last_seen_seconds": 1.9,
            "zoom_checked": True,
            "zoom_confidence": 0.9,
        },
        {
            "item_type": "asset",
            "item_id": "A1",
            "item_name": "panel",
            "description": "a control panel",
            "label": "panel",
            "threat_level": 0,
            "holder_entity_id": None,
            "timestamp_seconds": 0,
            "frame": evidence_frame_key(VIDEO_KEY, SOURCE_BATCH, "A1"),
            "image": None,
            "bounding_box": None,
            "box_seconds": 0.0,
            "located": False,
            "image_gap": "object not located at 0s or within 2s either side",
            "zoom_gap": "object not located",
        },
    ]


def _store_with_source(entries: list[dict[str, Any]]) -> InMemoryEvidenceStore:
    store = InMemoryEvidenceStore()
    for entry in entries:
        store.images[entry["frame"]] = b"SOURCE-FRAME"
        if entry["image"]:
            store.images[entry["image"]] = b"SOURCE-CROP"
    track = [{"t": 1.0, "box": [200, 200, 800, 500], "score": 0.9}]
    store.images[entries[0]["track"]] = json.dumps(track).encode()
    return store


def _run(video: Path, store: InMemoryEvidenceStore, entries: list[dict[str, Any]], **kwargs: Any) -> list[dict]:
    kwargs.setdefault("blur", False)
    return asyncio.run(
        reblur_evidence.reblur_evidence(
            video_ref=video, video_id="v1", source_evidence=entries, store=store, video_key=VIDEO_KEY, **kwargs
        )
    )


def _decode(data: bytes) -> np.ndarray:
    decoded = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    assert decoded is not None
    return np.asarray(decoded)


class TestReblurOn:
    def test_images_are_blurred_under_a_new_run_and_everything_else_is_the_sources(self, video: Path) -> None:
        source = _source_entries()
        before = copy.deepcopy(source)
        store = _store_with_source(source)
        detector = HeadsOnlyDetector()
        privacy = privacy_blur.PrivacySummary()

        result = _run(video, store, source, blur=True, detector=detector, privacy=privacy)

        assert source == before  # the source entries are not touched
        person = next(e for e in result if e["item_id"] == "E1")
        assert person["frame"] == "evidence/uploads/clip.mp4/run-2/E1-frame.jpg"
        assert person["image"] == "evidence/uploads/clip.mp4/run-2/E1.jpg"
        assert person["heads"] == evidence_heads_key(VIDEO_KEY, "run-2", "E1")
        assert person["blur_regions"]
        # Boxes, seconds, the track and the second look are exactly the source's.
        for field in ("bounding_box", "detected_box", "box_seconds", "located", "track", "zoom_checked", "item_name"):
            assert person[field] == before[0][field]
        assert [e["item_id"] for e in result] == ["E1", "A1"]

    def test_the_source_images_are_never_overwritten(self, video: Path) -> None:
        source = _source_entries()
        store = _store_with_source(source)

        _run(video, store, source, blur=True, detector=HeadsOnlyDetector(), privacy=privacy_blur.PrivacySummary())

        assert store.images[source[0]["frame"]] == b"SOURCE-FRAME"
        assert store.images[source[0]["image"]] == b"SOURCE-CROP"
        assert store.images[source[1]["frame"]] == b"SOURCE-FRAME"

    def test_only_the_head_detection_is_called(self, video: Path) -> None:
        source = _source_entries()
        detector = HeadsOnlyDetector()

        _run(
            video,
            _store_with_source(source),
            source,
            blur=True,
            detector=detector,
            privacy=privacy_blur.PrivacySummary(),
        )

        assert detector.queries
        assert set(detector.queries) == {privacy_blur.HEADS_QUERY}

    def test_the_stored_frame_and_crop_are_pixelated_on_the_head(self, video: Path) -> None:
        source = _source_entries()
        store = _store_with_source(source)

        result = _run(
            video, store, source, blur=True, detector=HeadsOnlyDetector(), privacy=privacy_blur.PrivacySummary()
        )

        plain_store = _store_with_source(source)
        plain = _run(video, plain_store, source, blur=False)
        blurred = _decode(store.images[result[0]["frame"]]).astype(int)
        unblurred = _decode(plain_store.images[plain[0]["frame"]]).astype(int)
        y0, x0, y1, x1 = privacy_blur.pixel_box(result[0]["blur_regions"][0], WIDTH, HEIGHT)
        inside = np.abs(blurred[y0:y1, x0:x1] - unblurred[y0:y1, x0:x1]).mean()
        assert inside > 10  # the head is pixelated
        assert np.abs(blurred[:, -20:] - unblurred[:, -20:]).mean() < 2  # far from any head the frame is as cut

    def test_the_clip_heads_are_stored_for_the_console(self, video: Path) -> None:
        source = _source_entries()
        store = _store_with_source(source)
        privacy = privacy_blur.PrivacySummary()

        result = _run(video, store, source, blur=True, detector=HeadsOnlyDetector(), privacy=privacy)

        heads = json.loads(store.images[result[0]["heads"]])
        assert heads["samples"]
        assert privacy.clip_samples > 0 and privacy.detector_calls > 0

    def test_the_regions_the_earlier_blur_recorded_are_kept_when_the_detector_fails(self, video: Path) -> None:
        source = _source_entries()
        source[0]["blur_regions"] = [[100, 100, 300, 300]]
        store = _store_with_source(source)
        privacy = privacy_blur.PrivacySummary()

        result = _run(video, store, source, blur=True, detector=HeadsOnlyDetector(fail=True), privacy=privacy)

        assert [100, 100, 300, 300] in result[0]["blur_regions"]
        assert result[0]["image"] is not None
        assert privacy.fallbacks > 0

    def test_an_item_whose_heads_cannot_be_searched_and_no_person_is_known_stores_no_image(self, video: Path) -> None:
        source = _source_entries()
        store = _store_with_source(source)
        privacy = privacy_blur.PrivacySummary()

        result = _run(video, store, source, blur=True, detector=HeadsOnlyDetector(fail=True), privacy=privacy)

        panel = next(e for e in result if e["item_id"] == "A1")
        assert panel["frame"] is None and panel["image"] is None
        assert "privacy blur failed" in panel["image_gap"]
        assert privacy.failed == 1

    def test_a_blur_needs_a_detector_and_a_summary(self, video: Path) -> None:
        source = _source_entries()
        with pytest.raises(ValueError, match="head detector"):
            _run(video, _store_with_source(source), source, blur=True)


class TestReblurOff:
    def test_images_are_stored_unblurred_and_nothing_is_detected(self, video: Path) -> None:
        source = _source_entries()
        source[0]["blur_regions"] = [[100, 100, 300, 300]]
        source[0]["heads"] = f"evidence/{VIDEO_KEY}/{SOURCE_BATCH}/E1-heads.json"
        store = _store_with_source(source)

        result = _run(video, store, source, blur=False)

        person = result[0]
        assert person["frame"] == "evidence/uploads/clip.mp4/run-2/E1-frame.jpg"
        assert "blur_regions" not in person and "heads" not in person
        assert not [key for key in store.images if key.endswith("-heads.json")]
        frame = _decode(store.images[person["frame"]])
        assert frame.shape[:2] == (HEIGHT, WIDTH)
        crop = _decode(store.images[person["image"]])
        # The crop is the stored box cut from the frame: the detector's box with the original 5 % padding.
        assert crop.shape[:2] == (66, 66)
        assert np.abs(crop.astype(int) - frame[17:83, 37:103].astype(int)).mean() < 40  # the same pixels, re-encoded

    def test_an_unlocated_item_keeps_a_frame_and_no_crop(self, video: Path) -> None:
        source = _source_entries()
        store = _store_with_source(source)

        result = _run(video, store, source, blur=False)

        panel = result[1]
        assert panel["frame"] == "evidence/uploads/clip.mp4/run-2/A1-frame.jpg"
        assert panel["image"] is None and panel["located"] is False
        assert panel["image_gap"] == source[1]["image_gap"]
        assert evidence_key(VIDEO_KEY, "run-2", "A1") not in store.images

    def test_an_older_entry_without_a_detected_box_is_cut_at_its_stored_box(self, video: Path) -> None:
        source = _source_entries()
        del source[0]["detected_box"]
        store = _store_with_source(source)

        result = _run(video, store, source, blur=False)

        crop = _decode(store.images[result[0]["image"]])
        assert crop.shape[:2] == (66, 72)
        assert result[0]["bounding_box"] == PADDED_PERSON_BOX

    def test_an_entry_the_source_stored_no_frame_for_stays_as_it_was(self, video: Path) -> None:
        source = _source_entries()
        source.append(
            {
                **{k: source[1][k] for k in ("item_type", "item_name", "description", "label")},
                "item_id": "A2",
                "timestamp_seconds": 0,
                "frame": None,
                "image": None,
                "image_gap": "no frame produced",
            }
        )
        store = _store_with_source(source[:2])

        result = _run(video, store, source, blur=False)

        assert result[2] == source[2]

    def test_a_video_that_cannot_be_read_leaves_no_image_rather_than_the_sources(self, tmp_path: Path) -> None:
        source = _source_entries()
        store = _store_with_source(source)

        result = _run(tmp_path / "missing.avi", store, source, blur=False)

        for entry in result:
            assert entry["frame"] is None and entry["image"] is None
            assert "re-cut failed" in entry["image_gap"]
        assert not [key for key in store.images if "/run-2/" in key]

    def test_a_failed_upload_leaves_no_image(self, video: Path) -> None:
        source = _source_entries()
        store = _store_with_source(source)

        async def refuse(key: str, image_bytes: bytes, content_type: str = "image/jpeg") -> str:
            raise RuntimeError("R2 down")

        store.put_image = refuse  # type: ignore[method-assign]

        result = _run(video, store, source, blur=False)

        assert all(entry["frame"] is None and entry["image"] is None for entry in result)
        assert "image upload failed" in result[0]["image_gap"]


class TestStoredTracks:
    def test_only_person_tracks_are_read(self) -> None:
        store = InMemoryEvidenceStore()
        rows = json.dumps([{"t": 1.0, "box": [0, 0, 10, 10], "score": 0.9}]).encode()
        store.images["person.json"] = rows
        store.images["object.json"] = rows
        entries = [
            {"track": "person.json"},
            {"track": "object.json", "track_source": "object-tracker"},
            {"track": None},
        ]

        tracks = asyncio.run(reblur_evidence.stored_person_tracks(entries, store))

        assert [t.track_id for t in tracks] == ["person.json"]
        assert tracks[0].source == track_evidence.PERSON_TRACK_SOURCE

    def test_a_missing_or_malformed_track_is_skipped(self) -> None:
        store = InMemoryEvidenceStore()
        store.images["bad.json"] = b"not json"

        tracks = asyncio.run(
            reblur_evidence.stored_person_tracks([{"track": "bad.json"}, {"track": "gone.json"}], store)
        )

        assert tracks == []
