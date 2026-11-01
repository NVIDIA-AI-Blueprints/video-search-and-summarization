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

"""Unit tests for the privacy blur: head-region maths, the pixelation, the whole-frame head search and the evidence write sites."""

from collections.abc import Generator
import json
from pathlib import Path
import tempfile
from typing import Any

import cv2
import numpy as np
import pytest

from vss_agents.utils import privacy_blur
from vss_agents.utils import run_provenance
from vss_agents.utils.crop_evidence import BudgetedDetector
from vss_agents.utils.crop_evidence import CropCallBudget
from vss_agents.utils.crop_evidence import CropEvidenceService
from vss_agents.utils.crop_evidence import DefaultDetector
from vss_agents.utils.crop_evidence import Detection
from vss_agents.utils.crop_evidence import FakeZoomNamer
from vss_agents.utils.crop_evidence import InMemoryEvidenceStore
from vss_agents.utils.privacy_blur import PrivacySummary
from vss_agents.utils.privacy_blur import blur_frame
from vss_agents.utils.privacy_blur import centre_inside
from vss_agents.utils.privacy_blur import clip_sample_times
from vss_agents.utils.privacy_blur import clip_window
from vss_agents.utils.privacy_blur import head_region
from vss_agents.utils.privacy_blur import heads_json
from vss_agents.utils.privacy_blur import overlaps
from vss_agents.utils.privacy_blur import pad_box
from vss_agents.utils.privacy_blur import pixel_box
from vss_agents.utils.privacy_blur import track_heads_at
from vss_agents.utils.privacy_blur import unique_regions
from vss_agents.utils.track_evidence import Sample
from vss_agents.utils.track_evidence import Track
from vss_agents.utils.track_evidence import TrackingOutcome
from vss_agents.utils.track_evidence import TrackingRequest

WIDTH, HEIGHT = 200, 100


def _texture(seed: int = 7) -> np.ndarray:
    """A frame of coarse random blocks: detail a pixelation visibly removes and JPEG keeps."""
    rng = np.random.default_rng(seed)
    blocks = rng.integers(0, 256, size=(HEIGHT // 4, WIDTH // 4, 3), dtype=np.uint8)
    return np.ascontiguousarray(np.repeat(np.repeat(blocks, 4, axis=0), 4, axis=1))


class TestHeadRegionMaths:
    def test_the_head_is_the_top_30_percent_raised_and_widened_by_10_percent(self) -> None:
        assert head_region((200, 400, 600, 500)) == (160, 390, 320, 510)

    def test_the_head_region_is_clamped_to_the_frame(self) -> None:
        assert head_region((0, 0, 1000, 1000)) == (0, 0, 300, 1000)
        assert head_region((10, 980, 410, 1000)) == (0, 978, 130, 1000)

    def test_padding_grows_every_side_and_clamps(self) -> None:
        assert pad_box((100, 100, 200, 300), 0.25) == (75, 50, 225, 350)
        assert pad_box((0, 900, 100, 1000), 0.5) == (0, 850, 150, 1000)

    def test_centre_inside_and_overlap(self) -> None:
        assert centre_inside((100, 100, 200, 200), (0, 0, 150, 150))
        assert not centre_inside((100, 100, 200, 200), (0, 0, 140, 140))
        assert overlaps((0, 0, 100, 100), (99, 99, 200, 200))
        assert not overlaps((0, 0, 100, 100), (100, 0, 200, 100))  # touching edges share no area

    def test_track_heads_come_from_every_track_with_a_sample_near_the_second(self) -> None:
        near = Track("1", [Sample(0.0, (0, 0, 500, 100), 0.9), Sample(1.0, (100, 0, 600, 100), 0.9)])
        far = Track("2", [Sample(5.0, (0, 500, 500, 600), 0.9)])
        assert track_heads_at([near, far], 0.9) == [head_region((100, 0, 600, 100))]
        assert track_heads_at([near, far], 3.0) == []
        assert track_heads_at([Track("3", [])], 0.0) == []

    def test_a_ghosts_drifting_boxes_are_not_blurred_and_do_not_count_as_a_solid_head(self) -> None:
        ghost = Track("g", [Sample(5.0, (100, 400, 600, 500), 0.6)])
        ghost.samples += [Sample(5.0 + 0.2 * i, (0, 100, 100, 300), -0.1) for i in range(1, 20)]
        assert track_heads_at([ghost], 5.2) == [head_region((0, 100, 100, 300))]  # soon after the detection: kept
        assert track_heads_at([ghost], 8.0) == []  # drift, 3 s on
        assert track_heads_at([ghost], 5.0, solid_only=True) == []  # one detection is not a solid track

    def test_unique_regions_drop_duplicates_and_empty_boxes(self) -> None:
        assert unique_regions([(0, 0, 10, 10), (0, 0, 10, 10), (5, 5, 5, 9), (1, 2, 3)]) == [(0, 0, 10, 10)]

    def test_pixel_boxes_round_outwards_and_clamp(self) -> None:
        assert pixel_box((101, 101, 199, 199), 200, 100) == (10, 20, 20, 40)
        assert pixel_box((0, 990, 1000, 1000), 205, 103) == (0, 202, 103, 205)


class TestBlurFrame:
    def test_only_the_regions_change_and_the_input_frame_is_kept(self) -> None:
        frame = _texture()
        original = frame.copy()
        blurred = blur_frame(frame, [(0, 0, 500, 500)])
        assert np.array_equal(frame, original)
        assert np.array_equal(blurred[50:, :], frame[50:, :])
        assert np.array_equal(blurred[:, 100:], frame[:, 100:])
        assert not np.array_equal(blurred[:50, :100], frame[:50, :100])

    def test_a_region_is_reduced_to_at_most_six_blocks_across(self) -> None:
        blurred = blur_frame(_texture(), [(0, 0, 600, 300)])  # 60 x 60 pixels
        patch = blurred[:60, :60].reshape(-1, 3)
        assert len(np.unique(patch, axis=0)) <= privacy_blur.PIXEL_BLOCKS**2

    def test_no_regions_is_a_plain_copy(self) -> None:
        frame = _texture()
        assert np.array_equal(blur_frame(frame, []), frame)


# --- the evidence write sites ------------------------------------------------------------------------------------


@pytest.fixture
def textured_video() -> Generator[Path]:
    """Two seconds at 10 fps of one textured frame, so every second shows the same picture."""
    with tempfile.NamedTemporaryFile(suffix=".avi", delete=False) as handle:
        path = Path(handle.name)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (WIDTH, HEIGHT))
    frame = _texture()
    for _ in range(20):
        writer.write(frame)
    writer.release()
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


PERSON_BOX = [200, 200, 800, 500]  # [ymin, xmin, ymax, xmax] of the person and of the object (0..1000)
HEAD_A = [200, 300, 400, 400]  # a head inside the person box
HEAD_B = [600, 800, 750, 900]  # a bystander's head, far from the person box and from any track

# Everything at second 0, so one frame stands for every item and the head search runs once for it.
REPORT: dict[str, Any] = {
    "incident": {"type": "theft"},
    "entities": [{"entity_id": "E1", "type": "human", "description": "man", "timestamp_seconds": 0, "label": "man"}],
    "instruments": [
        {"instrument_id": "I1", "entity_id": "E1", "name": "bag", "description": "bag", "timestamp_seconds": 0}
    ],
    "assets": [],
}
# A report with no person item at all: a control panel with a face in the frame (IMG_4569 A2).
PANEL_REPORT: dict[str, Any] = {
    "incident": {"type": "theft"},
    "entities": [],
    "instruments": [],
    "assets": [{"asset_id": "A1", "name": "panel", "description": "control panel", "timestamp_seconds": 0}],
}


class RecordingDetector:
    """Answers the locate query with PERSON_BOX and the whole-frame head query with ``heads`` (or fails it)."""

    def __init__(self, heads: list[list[int]] | None = None, fail_heads: bool = False) -> None:
        self.heads = [HEAD_A, HEAD_B] if heads is None else heads
        self.fail_heads = fail_heads
        self.queries: list[str] = []

    def is_available(self) -> bool:
        return True

    def detect(self, frame: np.ndarray, query: str) -> list[Detection]:
        self.queries.append(query)
        if query == privacy_blur.HEADS_QUERY:
            if self.fail_heads:
                raise RuntimeError("gateway down")
            return [Detection(box=list(head), score=1.0) for head in self.heads]
        return [Detection(box=list(PERSON_BOX), score=0.9)]

    @property
    def head_calls(self) -> int:
        return self.queries.count(privacy_blur.HEADS_QUERY)


def _tracking(*tracks: Track) -> TrackingRequest:
    async def runner(video_ref: object, *, sensor_id: str, **_: object) -> TrackingOutcome:
        return TrackingOutcome(tracks=list(tracks))

    return TrackingRequest(sensor_id="s", runner=runner)


async def _run(
    video: Path,
    *,
    detector: Any = None,
    privacy: PrivacySummary | None = None,
    tracking: TrackingRequest | None = None,
    namer: FakeZoomNamer | None = None,
    report: dict[str, Any] | None = None,
    video_seconds: float | None = 2.0,
) -> tuple[dict[str, dict], InMemoryEvidenceStore]:
    store = InMemoryEvidenceStore()
    service = CropEvidenceService(detector=detector or RecordingDetector(), store=store, namer=namer)
    entries = await service.process_incident_report(
        video_ref=video,
        video_id="v",
        video_key="uploads/a.mp4",
        report=report or REPORT,
        batch_id="run-1",
        tracking=tracking,
        zoom_names=namer is not None,
        privacy=privacy,
        video_seconds=video_seconds,
    )
    return {entry["item_id"]: entry for entry in entries}, store


def _decode(data: bytes | None) -> np.ndarray:
    assert data is not None
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR).astype(np.int16)


def _difference(a: np.ndarray, b: np.ndarray, region: tuple[int, int, int, int]) -> float:
    y0, x0, y1, x1 = region
    return float(np.abs(a[y0:y1, x0:x1] - b[y0:y1, x0:x1]).mean())


def _padded(head: list[int]) -> list[int]:
    return list(privacy_blur.pad_box(head, privacy_blur.DETECTED_HEAD_PADDING))


def _heads_file(store: InMemoryEvidenceStore, entry: dict[str, Any]) -> dict[str, Any]:
    return json.loads(store.images[entry["heads"]])


class TestWholeFrameHeads:
    @pytest.mark.asyncio
    async def test_without_the_flag_nothing_is_blurred_searched_or_stored(self, textured_video: Path) -> None:
        detector = RecordingDetector()
        entries, store = await _run(textured_video, detector=detector)
        assert all("blur_regions" not in entry and "heads" not in entry for entry in entries.values())
        assert detector.head_calls == 0
        assert not any(key.endswith("-heads.json") for key in store.images)

    @pytest.mark.asyncio
    async def test_every_detected_head_is_blurred_in_person_and_non_person_frames_and_crops(
        self, textured_video: Path
    ) -> None:
        plain, plain_store = await _run(textured_video)
        privacy = PrivacySummary()
        entries, store = await _run(textured_video, privacy=privacy)

        for item_id in ("E1", "I1"):  # a person item and an instrument
            entry = entries[item_id]
            assert _padded(HEAD_A) in entry["blur_regions"]
            assert _padded(HEAD_B) in entry["blur_regions"]
            frame = _decode(store.images[entry["frame"]])
            before = _decode(plain_store.images[plain[item_id]["frame"]])
            for head in (HEAD_A, HEAD_B):
                assert _difference(frame, before, pixel_box(_padded(head), WIDTH, HEIGHT)) > 20  # pixelated
            assert _difference(frame, before, (80, 0, HEIGHT, 90)) < 6  # clear of every head: only JPEG noise
            assert store.images[entry["image"]] != plain_store.images[plain[item_id]["image"]]
        assert privacy.frames == 2 and privacy.failed == 0 and privacy.fallbacks == 0

    @pytest.mark.asyncio
    async def test_a_face_in_a_frame_with_no_person_item_is_blurred(self, textured_video: Path) -> None:
        """IMG_4569 A2: the item is a control panel, yet a man's face is in the frame."""
        plain, plain_store = await _run(textured_video, report=PANEL_REPORT)
        privacy = PrivacySummary()
        entries, store = await _run(textured_video, report=PANEL_REPORT, privacy=privacy)
        assert entries["A1"]["blur_regions"] == [_padded(HEAD_A), _padded(HEAD_B)]
        frame = _decode(store.images[entries["A1"]["frame"]])
        before = _decode(plain_store.images[plain["A1"]["frame"]])
        assert _difference(frame, before, pixel_box(_padded(HEAD_A), WIDTH, HEIGHT)) > 20

    @pytest.mark.asyncio
    async def test_one_search_per_distinct_second_counted_against_its_own_cap(self, textured_video: Path) -> None:
        privacy = PrivacySummary()
        detector = RecordingDetector()
        await _run(textured_video, detector=detector, privacy=privacy)
        # a 2 s video: the stored frames share second 0 with the first clip sample; samples at 0, 0.5, 1 and 1.5 s
        assert detector.head_calls == privacy.detector_calls == 4
        assert privacy.clip_samples == 3 and privacy.head_detections == 8 and privacy.cap_refused == 0

    @pytest.mark.asyncio
    async def test_known_people_are_blurred_too_as_a_union_with_the_detected_heads(self, textured_video: Path) -> None:
        bystander = Track("2", [Sample(t, (0, 700, 400, 900), 0.9) for t in (0.1, 0.2, 0.3)])  # the detector missed it
        privacy = PrivacySummary()
        entries, _ = await _run(
            textured_video, detector=RecordingDetector(heads=[HEAD_A]), privacy=privacy, tracking=_tracking(bystander)
        )
        for item_id in ("E1", "I1"):
            regions = entries[item_id]["blur_regions"]
            assert _padded(HEAD_A) in regions  # the detector's head
            assert list(head_region((0, 700, 400, 900))) in regions  # the track's head
            assert list(head_region(entries["E1"]["bounding_box"])) in regions  # the located person's own box head

    @pytest.mark.asyncio
    async def test_no_detected_head_still_blurs_the_located_persons_box(self, textured_video: Path) -> None:
        privacy = PrivacySummary()
        entries, _ = await _run(textured_video, detector=RecordingDetector(heads=[]), privacy=privacy)
        expected = [list(head_region(entries["E1"]["bounding_box"]))]
        assert entries["E1"]["blur_regions"] == expected and entries["I1"]["blur_regions"] == expected
        assert privacy.fallbacks == 0  # the search worked; it found nothing more

    @pytest.mark.asyncio
    async def test_a_ghost_track_on_the_wrong_thing_does_not_stop_the_real_head_being_blurred(
        self, textured_video: Path
    ) -> None:
        """IMG_4569 E1: the model's box and a ghost track sit on the wall; the two men are elsewhere."""
        ghost = Track("ghost", [Sample(0.0, tuple(PERSON_BOX), 0.6)])
        ghost.samples += [Sample(0.2 * i, (200, 200 - 40 * i, 800, 500 - 40 * i), -0.1) for i in range(1, 10)]
        entries, _ = await _run(textured_video, privacy=PrivacySummary(), tracking=_tracking(ghost))
        assert "track" not in entries["E1"]
        for item_id in ("E1", "I1"):
            assert _padded(HEAD_A) in entries[item_id]["blur_regions"]
            assert _padded(HEAD_B) in entries[item_id]["blur_regions"]

    @pytest.mark.asyncio
    async def test_the_search_does_not_use_the_crop_call_budget(self, textured_video: Path) -> None:
        budget = CropCallBudget(2)  # the two locate calls and nothing else
        privacy = PrivacySummary()
        entries, _ = await _run(textured_video, detector=BudgetedDetector(RecordingDetector(), budget), privacy=privacy)
        assert budget.summary()["calls"] == 2 and budget.summary()["refused_by_cap"] == 0
        assert privacy.detector_calls == 4 and privacy.fallbacks == 0
        assert _padded(HEAD_B) in entries["E1"]["blur_regions"]

    @pytest.mark.asyncio
    async def test_a_spent_cap_blurs_the_known_people_only_and_records_the_fallbacks(
        self, textured_video: Path
    ) -> None:
        privacy = PrivacySummary(call_cap=1)  # the stored frame's second gets the one call; the clip samples do not
        with run_provenance.collecting() as provenance:
            entries, store = await _run(textured_video, privacy=privacy)
        assert _padded(HEAD_B) in entries["E1"]["blur_regions"]
        assert privacy.detector_calls == 1 and privacy.cap_refused == 3 and privacy.fallbacks == 3
        # The run provenance counts each head search the cap refused before sending.
        assert provenance.to_dict(contract_version="v", flags={})["stages"]["privacy_heads"]["refused"] == 3
        samples = _heads_file(store, entries["E1"])["samples"]
        assert [(sample["t"], sample.get("fallback", False)) for sample in samples] == [
            (0.0, False),
            (0.5, True),
            (1.0, True),
            (1.5, True),
        ]
        assert samples[1]["boxes"] == [list(head_region(entries["E1"]["bounding_box"]))]

    @pytest.mark.asyncio
    async def test_a_failed_search_falls_back_to_every_known_person_head(self, textured_video: Path) -> None:
        privacy = PrivacySummary()
        entries, store = await _run(textured_video, detector=RecordingDetector(fail_heads=True), privacy=privacy)
        expected = [list(head_region(entries["E1"]["bounding_box"]))]
        assert entries["E1"]["blur_regions"] == expected and entries["I1"]["blur_regions"] == expected
        assert store.images[entries["E1"]["frame"]]  # stored, blurred on the known head
        assert privacy.fallbacks == 4 and privacy.failed == 0
        assert all(sample["fallback"] for sample in _heads_file(store, entries["E1"])["samples"])

    @pytest.mark.asyncio
    async def test_a_failed_search_with_no_known_person_stores_no_image(self, textured_video: Path) -> None:
        privacy = PrivacySummary()
        entries, store = await _run(
            textured_video, detector=RecordingDetector(fail_heads=True), privacy=privacy, report=PANEL_REPORT
        )
        assert not any(key.endswith((".jpg", "-heads.json")) for key in store.images)
        assert entries["A1"]["frame"] is None and "privacy blur failed" in entries["A1"]["image_gap"]
        assert "heads" not in entries["A1"] and privacy.failed == 1

    @pytest.mark.asyncio
    async def test_a_failed_blur_stores_no_image_at_all(
        self, textured_video: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def broken(frame: np.ndarray, regions: object) -> np.ndarray:
            raise RuntimeError("no memory")

        monkeypatch.setattr(privacy_blur, "blur_frame", broken)
        privacy = PrivacySummary()
        entries, store = await _run(textured_video, privacy=privacy)
        assert store.images == {}
        assert all(entry["frame"] is None and "privacy blur failed" in entry["image_gap"] for entry in entries.values())
        assert privacy.failed == 2

    @pytest.mark.asyncio
    async def test_the_second_look_still_sees_the_unblurred_close_up(self, textured_video: Path) -> None:
        plain_namer, blurred_namer = FakeZoomNamer(), FakeZoomNamer()
        await _run(textured_video, namer=plain_namer)
        await _run(textured_video, namer=blurred_namer, privacy=PrivacySummary())

        def by_name(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return sorted(calls, key=lambda call: call["name"])

        assert by_name(blurred_namer.calls) == by_name(plain_namer.calls)  # the same unblurred close-up, item by item


class TestClipHeads:
    @pytest.mark.asyncio
    async def test_each_item_stores_its_clip_heads_sampled_every_half_second(self, textured_video: Path) -> None:
        entries, store = await _run(textured_video, privacy=PrivacySummary())
        for item_id in ("E1", "I1"):
            entry = entries[item_id]
            assert entry["heads"] == f"evidence/uploads/a.mp4/run-1/{item_id}-heads.json"
            file = _heads_file(store, entry)
            assert file["step_seconds"] == 0.5
            assert [sample["t"] for sample in file["samples"]] == [0.0, 0.5, 1.0, 1.5]
            assert all(sample["boxes"] == [_padded(HEAD_A), _padded(HEAD_B)] for sample in file["samples"])

    @pytest.mark.asyncio
    async def test_no_clip_heads_without_a_stored_video_to_sample(self, textured_video: Path) -> None:
        privacy = PrivacySummary()
        service = CropEvidenceService(detector=RecordingDetector(), store=None)
        frame = _decode(cv2.imencode(".png", _texture())[1].tobytes()).astype(np.uint8)
        entries = await service.process_incident_report(video_ref=frame, video_id="v", report=REPORT, privacy=privacy)
        assert all("heads" not in entry for entry in entries) and privacy.clip_samples == 0

    def test_the_clip_window_is_the_consoles(self) -> None:
        assert clip_window(7, 30) == (5.0, 4.0)
        assert clip_window(1, 30) == (0.0, 4.0)  # clamped at the start
        assert clip_window(29, 30) == (26.0, 4.0)  # shifted to stay inside the video
        assert clip_window(1, 2.5) == (0.0, 2.5)  # a short video is clipped whole
        assert clip_window(6.3, None) == (4.3, 4.0)

    def test_sample_times_cover_the_window_and_one_step_each_side_nearest_first(self) -> None:
        times = clip_sample_times(7, 30)
        assert sorted(times) == [4.5 + 0.5 * k for k in range(0, 11)]  # 4.5 .. 9.5
        assert times[0] == 7.0 and len(set(times)) == len(times)
        assert max(clip_sample_times(29, 30)) < 30  # none past the end of the video
        assert min(clip_sample_times(0, None)) == 0.0

    def test_the_heads_file_is_sorted_and_marks_fallbacks(self) -> None:
        data = json.loads(heads_json([(1.0, [(1, 2, 30, 40)], True), (0.5, [], False)]))
        assert data == {
            "step_seconds": 0.5,
            "samples": [{"t": 0.5, "boxes": []}, {"t": 1.0, "boxes": [[1, 2, 30, 40]], "fallback": True}],
        }


class TestDetectAll:
    def _detector(self, monkeypatch: pytest.MonkeyPatch, reply: dict[str, Any]) -> DefaultDetector:
        detector = DefaultDetector(api_key="k", base_url="http://gateway")
        monkeypatch.setattr(detector, "_request_box", lambda _body: reply)
        return detector

    def test_every_box_is_returned_as_a_detection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reply = {"detections": [{"bbox_2d": [10, 20, 30, 40], "label": "head"}, {"bbox_2d": [100, 200, 300, 400]}]}
        found = self._detector(monkeypatch, reply).detect_all(_texture(), privacy_blur.HEADS_QUERY)
        assert [d.box for d in found] == [[20, 10, 40, 30], [200, 100, 400, 300]]  # [ymin, xmin, ymax, xmax]

    def test_none_malformed_rows_the_single_box_form_and_the_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        frame = _texture()
        assert self._detector(monkeypatch, {"detections": []}).detect_all(frame, "head") == []
        assert self._detector(monkeypatch, {"bbox_2d": None}).detect_all(frame, "head") == []
        rows = {"detections": [{"bbox_2d": [1, 2, 3]}, {"bbox_2d": ["a", 2, 3, 4]}, "x", {"bbox_2d": [1, 2, 30, 40]}]}
        assert len(self._detector(monkeypatch, rows).detect_all(frame, "head")) == 1
        assert len(self._detector(monkeypatch, {"bbox_2d": [1, 2, 30, 40]}).detect_all(frame, "head")) == 1
        many = {"detections": [{"bbox_2d": [i, i, i + 5, i + 5]} for i in range(10)]}
        assert len(self._detector(monkeypatch, many).detect_all(frame, "head", limit=3)) == 3

    def test_without_a_key_it_finds_nothing_and_sends_nothing(self) -> None:
        assert DefaultDetector(api_key="", base_url="http://gateway").detect_all(_texture(), "head") == []


class TestSummary:
    def test_the_summary_reports_what_was_blurred_searched_and_fallen_back_on(self) -> None:
        summary = PrivacySummary(regions=3, frames=2, head_detections=5, call_cap=10)
        assert summary.take_call() and summary.take_call()
        assert summary.to_dict() == {
            "blur_faces": True,
            "regions": 3,
            "frames": 2,
            "head_detections": 5,
            "detector_calls": 2,
            "call_cap": 10,
            "cap_refused": 0,
            "clip_samples": 0,
            "fallbacks": 0,
            "failed": 0,
        }

    def test_the_cap_refuses_calls_beyond_it_and_a_refund_gives_one_back(self) -> None:
        summary = PrivacySummary(call_cap=1)
        assert summary.take_call() and not summary.take_call() and summary.cap_refused == 1
        summary.refund_call()
        assert summary.take_call()

    def test_the_cap_comes_from_the_environment_when_it_is_a_positive_integer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert PrivacySummary().call_cap == privacy_blur.DEFAULT_CALL_CAP
        for value, expected in (("12", 12), ("0", privacy_blur.DEFAULT_CALL_CAP), ("x", privacy_blur.DEFAULT_CALL_CAP)):
            monkeypatch.setenv(privacy_blur.CALL_CAP_ENV, value)
            assert PrivacySummary().call_cap == expected


class TestZoomCapThroughThePipeline:
    """P4's zoom-call cap, run through process_incident_report with a real namer and a fake upstream (review gap)."""

    @pytest.mark.asyncio
    async def test_the_zoom_cap_holds_and_later_items_keep_a_zoom_gap(
        self, textured_video: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from vss_agents.utils import crop_evidence

        look = {"name": "bag", "description": "a bag", "confidence": 0.9, "changed": False}
        sent: list[dict[str, Any]] = []

        class _Reply:
            status_code = 200

            def __init__(self) -> None:
                self.headers: dict[str, str] = {}

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, Any]:
                return {"choices": [{"message": {"content": json.dumps(look)}}], "usage": {"total_tokens": 5}}

        def fake_post(*_args: Any, json: dict[str, Any], **_kwargs: Any) -> _Reply:
            sent.append(json)
            return _Reply()

        monkeypatch.setattr(crop_evidence.httpx, "post", fake_post)
        budget = CropCallBudget(4)  # one item's three calls, and one of the next item's
        namer = crop_evidence.with_zoom_budget(DefaultDetector(model_name="grounder", api_key="k"), budget)
        report = {
            **REPORT,
            "assets": [{"asset_id": "A1", "name": "panel", "description": "panel", "timestamp_seconds": 0}],
        }
        store = InMemoryEvidenceStore()
        service = CropEvidenceService(detector=RecordingDetector(), store=store, namer=namer)
        with run_provenance.collecting() as provenance:
            entries = await service.process_incident_report(
                video_ref=textured_video,
                video_id="v",
                video_key="uploads/a.mp4",
                report=report,
                batch_id="run-1",
                zoom_names=True,
                video_seconds=2.0,
            )
        summary = budget.summary()
        assert len(sent) == summary["calls"] <= 4, "never more HTTP attempts than the cap"
        assert summary["refused_by_cap"] >= 1 and summary["cap_reached"] is True
        gaps = [e.get("zoom_gap") or "" for e in entries]
        assert any(gap.startswith("zoom-call cap reached") for gap in gaps)
        zoom = provenance.to_dict(contract_version="v", flags={})["stages"]["zoom"]
        assert zoom["calls"] == len(sent) and zoom["refused"] == summary["refused_by_cap"]
