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

"""Unit tests for crop_evidence utility and CropEvidenceService (MVP2 VM Step 3)."""

import base64
from collections.abc import Generator
import copy
import io
import json
from pathlib import Path
import tempfile
from typing import ClassVar
from unittest.mock import MagicMock
from unittest.mock import patch

import cv2
import httpx
import numpy as np
import pytest

from vss_agents.utils.crop_evidence import BudgetedDetector
from vss_agents.utils.crop_evidence import CropCallBudget
from vss_agents.utils.crop_evidence import CropCallCapReachedError
from vss_agents.utils.crop_evidence import CropEvidenceService
from vss_agents.utils.crop_evidence import CropResult
from vss_agents.utils.crop_evidence import DefaultDetector
from vss_agents.utils.crop_evidence import Detection
from vss_agents.utils.crop_evidence import FakeDetector
from vss_agents.utils.crop_evidence import FakeZoomNamer
from vss_agents.utils.crop_evidence import InMemoryEvidenceStore
from vss_agents.utils.crop_evidence import R2EvidenceStore
from vss_agents.utils.crop_evidence import ZoomLook
from vss_agents.utils.crop_evidence import ZoomVerdict
from vss_agents.utils.crop_evidence import apply_box_padding
from vss_agents.utils.crop_evidence import crop_evidence_item
from vss_agents.utils.crop_evidence import encode_jpeg
from vss_agents.utils.crop_evidence import evidence_items
from vss_agents.utils.crop_evidence import evidence_key
from vss_agents.utils.crop_evidence import extract_frame
from vss_agents.utils.crop_evidence import get_crop_evidence_service
from vss_agents.utils.crop_evidence import looks_agree
from vss_agents.utils.crop_evidence import next_run_batch_id
from vss_agents.utils.crop_evidence import normalize_box
from vss_agents.utils.crop_evidence import parse_zoom_reply
from vss_agents.utils.crop_evidence import set_crop_evidence_service
from vss_agents.utils.crop_evidence import zoom_crop
from vss_agents.utils.crop_evidence import zoom_names_enabled
from vss_agents.utils.track_evidence import OBJECT_TRACK_SOURCE
from vss_agents.utils.track_evidence import ObjectSeed
from vss_agents.utils.track_evidence import ObjectTrackingOutcome
from vss_agents.utils.track_evidence import ObjectTrackingRequest
from vss_agents.utils.track_evidence import Sample
from vss_agents.utils.track_evidence import Track
from vss_agents.utils.track_evidence import TrackingOutcome
from vss_agents.utils.track_evidence import TrackingRequest


@pytest.fixture
def tiny_video_path() -> Generator[Path]:
    """Generate a tiny 20-frame (2-second at 10fps) video fixture."""
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as temp_file:
        video_path = Path(temp_file.name)

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    fps = 10.0
    width, height = 120, 80
    writer = cv2.VideoWriter(str(video_path), fourcc, fps, (width, height))

    # Frame 0-9: blue background with white rectangle in center
    for _ in range(10):
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        frame[:, :] = (255, 0, 0)  # Blue
        frame[20:60, 30:90] = (255, 255, 255)  # White box
        writer.write(frame)

    # Frame 10-19: green background with red rectangle in center
    for _ in range(10):
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        frame[:, :] = (0, 255, 0)  # Green
        frame[10:50, 40:80] = (0, 0, 255)  # Red box
        writer.write(frame)

    writer.release()

    try:
        yield video_path
    finally:
        if video_path.exists():
            video_path.unlink()


class TestEvidenceKey:
    """Test the never-overwritten evidence key layout."""

    def test_canonical_key_format(self) -> None:
        assert evidence_key("uploads/clip.mp4", "run-1", "E1") == "evidence/uploads/clip.mp4/run-1/E1.jpg"

    def test_item_ids_are_unique_across_types_so_no_collection_folder(self) -> None:
        keys = {evidence_key("v", "b", item_id) for item_id in ("E1", "I1", "A1")}
        assert len(keys) == 3

    def test_batch_ids_count_up_from_the_highest_existing_run(self) -> None:
        assert next_run_batch_id([]) == "run-1"
        assert next_run_batch_id(["run-1", "run-3", "edit-9", "x"]) == "run-4"

    @pytest.mark.asyncio
    async def test_in_memory_store_names_the_next_run_per_video(self) -> None:
        store = InMemoryEvidenceStore()
        assert await store.next_batch_id("uploads/a.mp4") == "run-1"
        await store.put_image("evidence/uploads/a.mp4/run-1/E1.jpg", b"x")
        await store.put_image("evidence/uploads/a.mp4.bak/run-7/E1.jpg", b"x")
        assert await store.next_batch_id("uploads/a.mp4") == "run-2"

    @pytest.mark.asyncio
    async def test_r2_store_refuses_to_overwrite_and_lists_batches_by_prefix(self) -> None:
        client = MagicMock()
        client.get_paginator.return_value.paginate.return_value = [
            {
                "CommonPrefixes": [
                    {"Prefix": "evidence/uploads/a.mp4/run-1/"},
                    {"Prefix": "evidence/uploads/a.mp4/run-2/"},
                ]
            }
        ]
        store = R2EvidenceStore(bucket="b", client=client)
        await store.put_image("evidence/uploads/a.mp4/run-3/E1.jpg", b"x")
        assert client.put_object.call_args.kwargs["IfNoneMatch"] == "*"
        assert await store.next_batch_id("uploads/a.mp4") == "run-3"
        assert client.get_paginator.return_value.paginate.call_args.kwargs["Prefix"] == "evidence/uploads/a.mp4/"


class TestBoxPaddingAndNormalization:
    """Test 5% padding clamping and coordinate normalization."""

    def test_padding_calculation(self) -> None:
        # Box [100, 200, 300, 400] on 1000x1000 frame -> bw=200, bh=200.
        # 5% padding = 10 px margin.
        c_ymin, c_xmin, c_ymax, c_xmax = apply_box_padding([100, 200, 300, 400], frame_w=1000, frame_h=1000)
        assert (c_ymin, c_xmin, c_ymax, c_xmax) == (90, 190, 310, 410)

    def test_clamping_to_frame_boundaries(self) -> None:
        # Box at origin [0, 0, 100, 100] -> left/top clamped to 0.
        c_ymin, c_xmin, c_ymax, c_xmax = apply_box_padding([0, 0, 100, 100], frame_w=1000, frame_h=1000)
        assert c_ymin == 0
        assert c_xmin == 0
        assert c_ymax == 105
        assert c_xmax == 105

        # Box at bottom right [900, 900, 1000, 1000] -> bottom/right clamped to frame max.
        c_ymin, c_xmin, c_ymax, c_xmax = apply_box_padding([900, 900, 1000, 1000], frame_w=1000, frame_h=1000)
        assert c_ymin == 895
        assert c_xmin == 895
        assert c_ymax == 1000
        assert c_xmax == 1000

    def test_normalize_box(self) -> None:
        norm = normalize_box(c_ymin=50, c_xmin=100, c_ymax=150, c_xmax=300, frame_w=1000, frame_h=500)
        assert norm == [100, 100, 300, 300]

    def test_apply_box_padding_invalid_dimensions(self) -> None:
        with pytest.raises(ValueError, match="Expected 4 bounding box coordinates"):
            apply_box_padding([10, 20], frame_w=100, frame_h=100)


class TestFrameExtractionAndEncoding:
    """Test video frame extraction and JPEG encoding."""

    def test_extract_frame_valid(self, tiny_video_path: Path) -> None:
        frame_t0 = extract_frame(tiny_video_path, timestamp_seconds=0)
        assert frame_t0 is not None
        assert frame_t0.shape == (80, 120, 3)
        # Blue background at t=0
        assert frame_t0[0, 0, 0] > 200

        frame_t1 = extract_frame(tiny_video_path, timestamp_seconds=1)
        assert frame_t1 is not None
        assert frame_t1.shape == (80, 120, 3)
        # Green background at t=1
        assert frame_t1[0, 0, 1] > 200

    def test_extract_frame_nonexistent_file(self) -> None:
        frame = extract_frame("/path/does/not/exist.mp4", timestamp_seconds=0)
        assert frame is None

    def test_encode_jpeg_quality(self) -> None:
        sample_img = np.zeros((50, 50, 3), dtype=np.uint8)
        jpeg_bytes = encode_jpeg(sample_img, quality=85)
        assert jpeg_bytes is not None
        assert len(jpeg_bytes) > 0
        # Valid JPEG header
        assert jpeg_bytes[:2] == b"\xff\xd8"


class TestCropEvidenceItem:
    """Test crop_evidence_item standalone function."""

    def test_crop_with_fake_detector(self, tiny_video_path: Path) -> None:
        detector = FakeDetector(
            detections=[
                Detection(box=[250, 250, 750, 750], score=0.92, label="person"),
            ]
        )
        result = crop_evidence_item(
            video_ref=tiny_video_path,
            timestamp_seconds=0,
            label="person",
            detector=detector,
        )
        assert isinstance(result, CropResult)
        assert result.score == 0.92
        assert result.frame_size == (120, 80)
        assert len(result.normalized_box) == 4
        assert result.image_bytes[:2] == b"\xff\xd8"

    def test_not_located_fallback_keeps_the_frame_with_no_box(self, tiny_video_path: Path) -> None:
        detector = FakeDetector(detections=[])
        assert crop_evidence_item(tiny_video_path, 0, "weapon", detector=detector) is None
        result = crop_evidence_item(tiny_video_path, 0, "weapon", detector=detector, not_located_fallback=True)
        assert result is not None
        assert result.located is False
        assert result.normalized_box is None and result.image_bytes is None
        assert result.box_seconds == 0
        assert result.frame_size == (120, 80)
        assert result.frame_bytes is not None and result.frame_bytes[:2] == b"\xff\xd8"

    def test_a_hit_carries_the_whole_frame_at_native_size(self, tiny_video_path: Path) -> None:
        detector = FakeDetector(detections=[Detection(box=[250, 250, 750, 750], score=0.9, label="person")])
        result = crop_evidence_item(tiny_video_path, 0, "person", detector=detector)
        assert result is not None and result.located is True and result.box_seconds == 0
        assert result.frame_bytes is not None
        frame = cv2.imdecode(np.frombuffer(result.frame_bytes, np.uint8), cv2.IMREAD_COLOR)
        assert frame.shape[:2] == (80, 120)

    def test_retries_nearby_seconds_and_records_the_second_that_hit(self, tiny_video_path: Path) -> None:
        # The tiny video is blue at second 0 and green at second 1; the detector only sees the green frame.
        class GreenOnly:
            def detect(self, frame: np.ndarray, query: str) -> list[Detection]:
                return [Detection(box=[100, 100, 500, 500], score=0.9)] if frame[0, 0, 1] > 200 else []

        assert crop_evidence_item(tiny_video_path, 0, "x", detector=GreenOnly()) is None
        result = crop_evidence_item(tiny_video_path, 0, "x", detector=GreenOnly(), retry_offsets=(1, -1, 2, -2))
        assert result is not None and result.located is True
        assert result.box_seconds == 1

    def test_retries_skip_seconds_before_the_start_and_try_the_requested_second_first(
        self, tiny_video_path: Path
    ) -> None:
        seen: list[float] = []

        def spy(frame: np.ndarray, query: str) -> list[Detection]:
            seen.append(float(frame[0, 0, 1]))
            return []

        result = crop_evidence_item(
            tiny_video_path, 0, "x", detector=spy, not_located_fallback=True, retry_offsets=(1, -1)
        )
        assert result is not None and result.located is False and result.box_seconds == 0
        assert len(seen) == 2  # second 0, then +1; -1 is before the start

    def test_a_supplied_frame_is_never_retried(self) -> None:
        frame = np.zeros((40, 60, 3), dtype=np.uint8)
        calls: list[int] = []

        def spy(f: np.ndarray, query: str) -> list[Detection]:
            calls.append(1)
            return []

        assert crop_evidence_item(frame, 5, "x", detector=spy, retry_offsets=(1, -1, 2, -2)) is None
        assert len(calls) == 1

    def test_each_retry_second_spends_one_budgeted_detector_call(self, tiny_video_path: Path) -> None:
        budget = CropCallBudget(2)
        detector = BudgetedDetector(FakeDetector(detections=[]), budget)
        with pytest.raises(CropCallCapReachedError):
            crop_evidence_item(
                tiny_video_path, 0, "x", detector=detector, not_located_fallback=True, retry_offsets=(1, -1, 2, -2)
            )
        assert budget.calls == 2 and budget.refused == 1

    def test_crop_picks_best_box(self, tiny_video_path: Path) -> None:
        detector = FakeDetector(
            detections=[
                Detection(box=[100, 100, 200, 200], score=0.40, label="crowbar"),
                Detection(box=[200, 200, 600, 600], score=0.88, label="crowbar"),
                Detection(box=[150, 150, 300, 300], score=0.65, label="crowbar"),
            ]
        )
        result = crop_evidence_item(
            video_ref=tiny_video_path,
            timestamp_seconds=1,
            label="crowbar",
            detector=detector,
        )
        assert result is not None
        assert result.score == 0.88

    def test_crop_returns_none_when_no_detections(self, tiny_video_path: Path) -> None:
        detector = FakeDetector(detections=[])
        result = crop_evidence_item(
            video_ref=tiny_video_path,
            timestamp_seconds=0,
            label="unknown",
            detector=detector,
        )
        assert result is None

    def test_crop_returns_none_when_detector_unavailable(self, tiny_video_path: Path) -> None:
        detector = DefaultDetector(api_key="")
        assert not detector.is_available()
        result = crop_evidence_item(
            video_ref=tiny_video_path,
            timestamp_seconds=0,
            label="person",
            detector=detector,
        )
        assert result is None

    def test_crop_returns_none_when_detector_is_none(self, tiny_video_path: Path) -> None:
        result = crop_evidence_item(
            video_ref=tiny_video_path,
            timestamp_seconds=0,
            label="person",
            detector=None,
        )
        assert result is None

    def test_crop_returns_none_for_invalid_bounding_box(self, tiny_video_path: Path) -> None:
        detector = FakeDetector(detections=[Detection(box=[500, 500, 200, 200], score=0.9, label="person")])
        result = crop_evidence_item(
            video_ref=tiny_video_path,
            timestamp_seconds=0,
            label="person",
            detector=detector,
        )
        assert result is None


class TestEvidenceStoreAdapters:
    """Test EvidenceStore port and in-memory adapter."""

    @pytest.mark.asyncio
    async def test_in_memory_store_put_and_get(self) -> None:
        store = InMemoryEvidenceStore()
        key = "evidence/Video1/batch/E1.jpg"
        data = b"\xff\xd8\xff\xe0test_image"

        stored_key = await store.put_image(key, data)
        assert stored_key == key

        retrieved = await store.get_image(key)
        assert retrieved == data

        non_existent = await store.get_image("evidence/missing.jpg")
        assert non_existent is None

    @pytest.mark.asyncio
    async def test_r2_store_puts_and_gets_image(self) -> None:
        client = MagicMock()
        client.get_object.return_value = {"Body": io.BytesIO(b"jpeg-bytes")}
        store = R2EvidenceStore(bucket="bucket", client=client)

        key = await store.put_image("evidence/V1/batch/E1.jpg", b"jpeg-bytes")

        assert key == "evidence/V1/batch/E1.jpg"
        client.put_object.assert_called_once_with(
            Bucket="bucket", Key=key, Body=b"jpeg-bytes", ContentType="image/jpeg", IfNoneMatch="*"
        )
        assert await store.get_image(key) == b"jpeg-bytes"

    @pytest.mark.asyncio
    async def test_r2_store_get_image_returns_none_for_missing_key(self) -> None:
        class NoSuchKeyError(Exception):
            response: ClassVar[dict[str, dict[str, str]]] = {"Error": {"Code": "NoSuchKey"}}

        client = MagicMock()
        client.get_object.side_effect = NoSuchKeyError()
        store = R2EvidenceStore(bucket="bucket", client=client)

        assert await store.get_image("evidence/missing.jpg") is None


class TestDefaultDetector:
    """Test the remote-VLM-backed open-vocabulary detector."""

    def _frame(self) -> np.ndarray:
        return np.zeros((80, 120, 3), dtype=np.uint8)

    def test_detect_converts_vlm_box_to_ymin_xmin_ymax_xmax(self) -> None:
        detector = DefaultDetector(api_key="k", base_url="https://vlm.example/v1")
        reply = MagicMock()
        reply.json.return_value = {
            "choices": [{"message": {"content": '```json\n{"bbox_2d": [100, 200, 300, 400], "label": "knife"}\n```'}}]
        }
        with patch("vss_agents.utils.crop_evidence.httpx.post", return_value=reply) as post:
            detections = detector.detect(self._frame(), "knife")

        assert detections == [Detection(box=[200, 100, 400, 300], score=1.0, label="knife")]
        assert post.call_args.args[0] == "https://vlm.example/v1/chat/completions"
        assert post.call_args.kwargs["headers"] == {"Authorization": "Bearer k"}

    def test_detect_returns_nothing_when_item_not_visible(self) -> None:
        detector = DefaultDetector(api_key="k")
        reply = MagicMock()
        reply.json.return_value = {"choices": [{"message": {"content": '{"bbox_2d": null}'}}]}
        with patch("vss_agents.utils.crop_evidence.httpx.post", return_value=reply):
            assert detector.detect(self._frame(), "unicorn") == []

    def test_a_call_budget_counts_every_http_attempt_including_retries(self) -> None:
        limited = MagicMock(status_code=429, headers={"retry-after": "0"})
        limited.json.return_value = {}
        budget = CropCallBudget(2)
        detector = DefaultDetector(api_key="k").with_call_budget(budget)
        with (
            patch("vss_agents.utils.crop_evidence.httpx.post", return_value=limited) as post,
            patch("vss_agents.utils.crop_evidence.time.sleep"),
            pytest.raises(CropCallCapReachedError),
        ):
            detector.detect(self._frame(), "knife")
        assert post.call_count == 2  # the third attempt was refused before it was sent
        assert budget.summary() == {
            "cap": 2,
            "calls": 2,
            "refused_by_cap": 1,
            "cap_reached": True,
            "tokens": 0,
            "calls_without_usage": 0,  # a 429 carries no usage and is not billed
        }

    def test_a_budgeted_copy_leaves_the_shared_detector_unbudgeted_and_records_tokens(self) -> None:
        reply = MagicMock(status_code=200)
        reply.json.return_value = {
            "choices": [{"message": {"content": '{"bbox_2d": null}'}}],
            "usage": {"prompt_tokens": 900, "completion_tokens": 20},
        }
        shared = DefaultDetector(api_key="k")
        budget = CropCallBudget(5)
        with patch("vss_agents.utils.crop_evidence.httpx.post", return_value=reply):
            shared.with_call_budget(budget).detect(self._frame(), "knife")
            shared.detect(self._frame(), "knife")
        assert shared.call_budget is None
        assert budget.calls == 1 and budget.tokens == 920 and budget.calls_without_usage == 0

    def test_a_cap_below_one_is_refused(self) -> None:
        with pytest.raises(ValueError):
            CropCallBudget(0)

    def test_detect_without_credentials_makes_no_call(self) -> None:
        detector = DefaultDetector(api_key="")
        with patch("vss_agents.utils.crop_evidence.httpx.post") as post:
            assert detector.detect(self._frame(), "knife") == []
        post.assert_not_called()


REPORT = {
    "incident": {"title": "Burglary", "type": "burglary"},
    "entities": [
        {
            "entity_id": "E1",
            "type": "human",
            "description": "Suspect in hoodie",
            "timestamp_seconds": 0,
            "first_seen_seconds": 0,
            "last_seen_seconds": 2,
            "label": "person",
        }
    ],
    "instruments": [
        {
            "instrument_id": "I1",
            "entity_id": "E1",
            "name": "crowbar",
            "description": "Heavy metal tool",
            "threat_level": 3,
            "timestamp_seconds": 1,
            "label": "crowbar",
        }
    ],
    "assets": [
        {
            "asset_id": "A1",
            "name": "cash register",
            "description": "Store register",
            "timestamp_seconds": 1,
            "label": "cash register",
        }
    ],
}


class TestEvidenceItems:
    def test_lists_entities_instruments_and_assets_with_the_evidence_columns(self) -> None:
        items = evidence_items(REPORT)
        assert [(i["item_type"], i["item_id"], i["item_name"]) for i in items] == [
            ("entity", "E1", "human"),
            ("instrument", "I1", "crowbar"),
            ("asset", "A1", "cash register"),
        ]
        instrument = items[1]
        assert instrument["threat_level"] == 3 and instrument["holder_entity_id"] == "E1"
        assert instrument["timestamp_seconds"] == 1 and instrument["label"] == "crowbar"
        assert (items[0]["first_seen_seconds"], items[0]["last_seen_seconds"]) == (0, 2)
        assert "first_seen_seconds" not in instrument and "last_seen_seconds" not in items[2]

    def test_entities_stored_before_the_seen_span_fall_back_to_their_timestamp(self) -> None:
        (item,) = evidence_items({"entities": [{"entity_id": "E1", "timestamp_seconds": 5}]})
        assert (item["first_seen_seconds"], item["last_seen_seconds"]) == (5, 5)

    def test_items_without_an_id_are_dropped_and_timestamps_are_whole_seconds(self) -> None:
        items = evidence_items(
            {"entities": [{"type": "human"}, {"entity_id": "E2", "timestamp_seconds": 2.6}], "assets": None}
        )
        assert [i["item_id"] for i in items] == ["E2"]
        assert items[0]["timestamp_seconds"] == 3


class TestCropEvidenceService:
    """Test CropEvidenceService report processing."""

    @pytest.mark.asyncio
    async def test_process_incident_report_uploads_a_unique_crop_per_item(self, tiny_video_path: Path) -> None:
        detector = FakeDetector(detections=[Detection(box=[200, 200, 600, 600], score=0.95, label="person")])
        store = InMemoryEvidenceStore()
        service = CropEvidenceService(detector=detector, store=store)

        results = await service.process_incident_report(
            video_ref=tiny_video_path,
            video_id="vabc",
            video_key="uploads/Burglary001.mp4",
            report=REPORT,
            batch_id="run-1",
        )

        assert [r["item_id"] for r in results] == ["E1", "I1", "A1"]
        for entry in results:
            assert entry["image"] == f"evidence/uploads/Burglary001.mp4/run-1/{entry['item_id']}.jpg"
            assert entry["frame"] == f"evidence/uploads/Burglary001.mp4/run-1/{entry['item_id']}-frame.jpg"
            assert entry["image_gap"] is None
            assert entry["located"] is True and entry["box_seconds"] == entry["timestamp_seconds"]
            assert len(entry["bounding_box"]) == 4
            assert await store.get_image(entry["image"]) is not None
            assert await store.get_image(entry["frame"]) is not None

    @pytest.mark.asyncio
    async def test_an_object_that_is_not_found_stores_its_frame_alone_and_says_so(self, tiny_video_path: Path) -> None:
        store = InMemoryEvidenceStore()
        service = CropEvidenceService(detector=FakeDetector(), store=store)
        results = await service.process_incident_report(
            video_ref=tiny_video_path, video_id="v", video_key="uploads/a.mp4", report=REPORT, batch_id="run-1"
        )
        for entry in results:
            assert entry["located"] is False
            assert entry["frame"] == f"evidence/uploads/a.mp4/run-1/{entry['item_id']}-frame.jpg"
            assert entry["image"] is None and entry["bounding_box"] is None
            assert "not located" in entry["image_gap"]
            assert await store.get_image(entry["frame"]) is not None
        assert len(store.images) == len(results)  # no crop was stored beside the frames

    @pytest.mark.asyncio
    async def test_items_beyond_the_crop_call_cap_keep_a_cap_gap(self, tiny_video_path: Path) -> None:
        detector = FakeDetector(detections=[Detection(box=[200, 200, 600, 600], score=0.95, label="x")])
        budget = CropCallBudget(2)
        service = CropEvidenceService(detector=detector, store=InMemoryEvidenceStore())
        results = await service.process_incident_report(
            video_ref=tiny_video_path, video_id="v", report=REPORT, detector=BudgetedDetector(detector, budget)
        )
        assert sum(1 for r in results if r["image"]) == 2
        assert [r["image_gap"] for r in results if not r["image"]] == [
            "crop-call cap reached: crop-call cap of 2 reached"
        ]
        assert budget.calls == 2 and budget.refused == 1

    @pytest.mark.asyncio
    async def test_each_run_gets_the_next_run_number_so_keys_are_never_overwritten(self, tiny_video_path: Path) -> None:
        detector = FakeDetector(detections=[Detection(box=[200, 200, 600, 600], score=0.95, label="person")])
        service = CropEvidenceService(detector=detector, store=InMemoryEvidenceStore())
        first = await service.process_incident_report(video_ref=tiny_video_path, video_id="v", report=REPORT)
        second = await service.process_incident_report(video_ref=tiny_video_path, video_id="v", report=REPORT)
        assert {e["image"] for e in first}.isdisjoint({e["image"] for e in second})
        assert {e["image"] for e in second} == {f"evidence/v/run-2/{e['item_id']}.jpg" for e in second}

    @pytest.mark.asyncio
    async def test_no_store_records_the_gap_for_every_item(self, tiny_video_path: Path) -> None:
        service = CropEvidenceService(detector=FakeDetector(), store=None)
        results = await service.process_incident_report(video_ref=tiny_video_path, video_id="v", report=REPORT)
        assert len(results) == 3
        assert all(r["image"] is None and r["bounding_box"] is None for r in results)
        assert all("store" in r["image_gap"] for r in results)

    @pytest.mark.asyncio
    async def test_unavailable_detector_records_the_gap_instead_of_dropping_items(self, tiny_video_path: Path) -> None:
        service = CropEvidenceService(detector=DefaultDetector(api_key=""), store=InMemoryEvidenceStore())
        assert not service.is_detector_available()
        results = await service.process_incident_report(video_ref=tiny_video_path, video_id="Vid1", report=REPORT)
        assert len(results) == 3
        assert all(r["image"] is None and "detector" in r["image_gap"] for r in results)

    @pytest.mark.asyncio
    async def test_upload_failure_is_recorded_per_item(self, tiny_video_path: Path) -> None:
        store = MagicMock()

        async def put_image(key: str, data: bytes, content_type: str = "image/jpeg") -> str:
            if key.endswith("I1.jpg"):
                raise RuntimeError("r2 down")
            return key

        store.put_image = put_image
        detector = FakeDetector(detections=[Detection(box=[200, 200, 600, 600], score=0.95, label="person")])
        service = CropEvidenceService(detector=detector, store=store)
        results = await service.process_incident_report(
            video_ref=tiny_video_path, video_id="v", report=REPORT, batch_id="b"
        )
        by_id = {r["item_id"]: r for r in results}
        assert by_id["E1"]["image"] == "evidence/v/b/E1.jpg"
        assert by_id["I1"]["image"] is None and "r2 down" in by_id["I1"]["image_gap"]
        assert by_id["A1"]["image"] == "evidence/v/b/A1.jpg"

    def test_global_service_get_and_set(self) -> None:
        orig = get_crop_evidence_service()
        custom = CropEvidenceService(detector=FakeDetector())
        set_crop_evidence_service(custom)
        assert get_crop_evidence_service() is custom
        set_crop_evidence_service(orig)


class TestTrackingEvidence:
    """Tracks decide the first-seen second of a matched person and are stored beside the evidence."""

    DETECTOR = FakeDetector(detections=[Detection(box=[200, 200, 600, 600], score=0.95, label="person")])

    @staticmethod
    def _request(outcome: TrackingOutcome) -> TrackingRequest:
        async def runner(video_ref: object, *, sensor_id: str, **_: object) -> TrackingOutcome:
            return outcome

        return TrackingRequest(sensor_id="incident-x-run", runner=runner)

    @staticmethod
    def _track() -> Track:
        # the person overlaps the located box from 0.3 s (first seen) and is biggest, and most confident, at 1.5 s
        return Track(
            "7",
            [
                Sample(0.3, (150, 150, 650, 650), 0.9),
                Sample(1.5, (100, 100, 900, 900), 0.95),
                Sample(1.9, (300, 300, 400, 400), 0.4),
            ],
        )

    @pytest.mark.asyncio
    async def test_a_matched_person_gets_the_first_seen_second_the_track_file_and_its_span(
        self, tiny_video_path: Path
    ) -> None:
        store = InMemoryEvidenceStore()
        request = self._request(TrackingOutcome(tracks=[self._track()], elapsed_ms=12.0))
        service = CropEvidenceService(detector=self.DETECTOR, store=store)
        results = await service.process_incident_report(
            video_ref=tiny_video_path,
            video_id="v",
            video_key="uploads/a.mp4",
            report=REPORT,
            batch_id="run-1",
            tracking=request,
        )
        person, instrument, asset = results
        assert person["track"] == "evidence/uploads/a.mp4/run-1/E1-track.json"
        assert (person["track_first_seen_seconds"], person["track_last_seen_seconds"]) == (0.3, 1.9)
        assert person["located"] is True and person["box_seconds"] == 0.3
        assert person["bounding_box"] == pytest.approx(
            [150, 150, 650, 650], abs=60
        )  # the first sample's box plus padding
        assert person["image"] == "evidence/uploads/a.mp4/run-1/E1.jpg"
        stored = json.loads(await store.get_image(person["track"]))
        assert [row["t"] for row in stored] == [0.3, 1.5, 1.9]
        for other in (instrument, asset):
            assert "track" not in other and other["box_seconds"] == other["timestamp_seconds"]
        assert request.summary.matched == 1 and request.summary.tracks == 1 and request.summary.skipped is None

    @pytest.mark.asyncio
    async def test_an_entity_that_overlaps_no_track_keeps_its_ordinary_result(self, tiny_video_path: Path) -> None:
        elsewhere = Track("9", [Sample(0.0, (800, 800, 950, 950), 0.9)])
        service = CropEvidenceService(detector=self.DETECTOR, store=InMemoryEvidenceStore())
        request = self._request(TrackingOutcome(tracks=[elsewhere]))
        results = await service.process_incident_report(
            video_ref=tiny_video_path, video_id="v", report=REPORT, batch_id="b", tracking=request
        )
        assert all("track" not in r for r in results)
        assert results[0]["bounding_box"] == pytest.approx([200, 200, 600, 600], abs=60)
        assert results[0]["detected_box"] == pytest.approx([200, 200, 600, 600], abs=1), "the unpadded detector box"
        assert results[0]["box_seconds"] == 0
        assert request.summary.matched == 0 and request.summary.tracks == 1

    @pytest.mark.asyncio
    async def test_a_skipped_run_leaves_every_entry_as_it_was_and_reports_why(self, tiny_video_path: Path) -> None:
        plain = await CropEvidenceService(
            detector=self.DETECTOR, store=InMemoryEvidenceStore()
        ).process_incident_report(video_ref=tiny_video_path, video_id="v", report=REPORT, batch_id="b")
        request = self._request(TrackingOutcome(skipped="no frames from perception"))
        tracked = await CropEvidenceService(
            detector=self.DETECTOR, store=InMemoryEvidenceStore()
        ).process_incident_report(
            video_ref=tiny_video_path, video_id="v", report=REPORT, batch_id="b", tracking=request
        )
        assert tracked == plain
        assert request.summary.skipped == "no frames from perception" and request.summary.matched == 0

    @pytest.mark.asyncio
    async def test_a_runner_that_raises_never_fails_the_evidence(self, tiny_video_path: Path) -> None:
        async def runner(video_ref: object, *, sensor_id: str, **_: object) -> TrackingOutcome:
            raise RuntimeError("boom")

        request = TrackingRequest(sensor_id="s", runner=runner)
        results = await CropEvidenceService(
            detector=self.DETECTOR, store=InMemoryEvidenceStore()
        ).process_incident_report(
            video_ref=tiny_video_path, video_id="v", report=REPORT, batch_id="b", tracking=request
        )
        assert len(results) == 3 and all(r["image"] for r in results)
        assert request.summary.skipped is not None and "boom" not in request.summary.skipped

    @pytest.mark.asyncio
    async def test_without_a_store_tracking_is_skipped_and_never_started(self, tiny_video_path: Path) -> None:
        started = []

        async def runner(video_ref: object, *, sensor_id: str, **_: object) -> TrackingOutcome:
            started.append(sensor_id)
            return TrackingOutcome()

        request = TrackingRequest(sensor_id="s", runner=runner)
        await CropEvidenceService(detector=self.DETECTOR, store=None).process_incident_report(
            video_ref=tiny_video_path, video_id="v", report=REPORT, tracking=request
        )
        assert started == [] and request.summary.skipped

    @pytest.mark.asyncio
    async def test_a_marked_entity_keeps_its_track_even_without_box_overlap(self, tiny_video_path: Path) -> None:
        # The detector box (200..600) overlaps neither track, so only the mark can match E1.
        marked = Track("12", [Sample(0.0, (800, 800, 950, 950), 0.9), Sample(1.0, (700, 700, 950, 950), 0.95)])
        other = Track("13", [Sample(0.0, (210, 210, 590, 590), 0.9)])
        request = self._request(TrackingOutcome(tracks=[marked, other]))
        request.marked = {"E1": marked}
        request.summary.marks = {"rendered": True, "parsed": 0, "fallback": 0, "invalid": 0}
        results = await CropEvidenceService(
            detector=self.DETECTOR, store=InMemoryEvidenceStore()
        ).process_incident_report(
            video_ref=tiny_video_path, video_id="v", report=REPORT, batch_id="b", tracking=request
        )
        person = results[0]
        assert person["track"] == "evidence/v/b/E1-track.json" and person["box_seconds"] == 0.0
        assert person["bounding_box"] == pytest.approx(
            [800, 800, 950, 950], abs=60
        )  # cut at the mark's track's first sample
        assert request.summary.matched == 1
        assert request.summary.marks == {"rendered": True, "parsed": 1, "fallback": 0, "invalid": 0}

    @pytest.mark.asyncio
    async def test_an_unmarked_entity_falls_back_to_overlap_with_the_tracks_no_mark_took(
        self, tiny_video_path: Path
    ) -> None:
        report = copy.deepcopy(REPORT)
        report["entities"].append({**report["entities"][0], "entity_id": "E2", "label": "second person"})
        overlapping = self._track()  # overlaps the detector box, so both entities would pick it by IoU
        request = self._request(TrackingOutcome(tracks=[overlapping]))
        request.marked = {"E2": overlapping}
        request.summary.marks = {"rendered": True, "parsed": 0, "fallback": 0, "invalid": 0}
        results = await CropEvidenceService(
            detector=self.DETECTOR, store=InMemoryEvidenceStore()
        ).process_incident_report(
            video_ref=tiny_video_path, video_id="v", report=report, batch_id="b", tracking=request
        )
        by_id = {r["item_id"]: r for r in results}
        assert by_id["E2"]["track"] and "track" not in by_id["E1"]  # the marked track is not offered to E1
        assert request.summary.marks["parsed"] == 1 and request.summary.marks["fallback"] == 0

        fallback = self._request(TrackingOutcome(tracks=[self._track()]))
        fallback.summary.marks = {"rendered": True, "parsed": 0, "fallback": 0, "invalid": 1}
        results = await CropEvidenceService(
            detector=self.DETECTOR, store=InMemoryEvidenceStore()
        ).process_incident_report(
            video_ref=tiny_video_path, video_id="v", report=REPORT, batch_id="b", tracking=fallback
        )
        assert results[0]["track"] and fallback.summary.marks["fallback"] == 1 and fallback.summary.marks["parsed"] == 0


class _SecondDetector:
    """Answers the person query from a per-second script (the tiny video's second 0 is blue, second 1 green)."""

    def __init__(self, person_boxes: dict[int, list[int]]) -> None:
        self.person_boxes = person_boxes
        self.person_seconds: list[int] = []

    def is_available(self) -> bool:
        return True

    def detect(self, frame: np.ndarray, query: str) -> list[Detection]:
        if query != "person":
            return [Detection(box=[100, 100, 300, 300], score=1.0, label=query)]
        second = 0 if frame[0, 0, 0] > frame[0, 0, 1] else 1
        self.person_seconds.append(second)
        box = self.person_boxes.get(second)
        return [Detection(box=box, score=1.0, label=query)] if box else []


class TestTrackConfirmedEntityBox:
    """A person's box that no solid person track confirms is retried, then moved onto a track."""

    SCREEN: ClassVar[list[int]] = [200, 200, 600, 600]  # what the detector boxes first, not a tracked person
    PERSON: ClassVar[list[int]] = [700, 700, 950, 950]
    NOWHERE: ClassVar[list[int]] = [10, 10, 100, 100]

    @staticmethod
    def _request(*tracks: Track) -> TrackingRequest:
        async def runner(video_ref: object, *, sensor_id: str, **_: object) -> TrackingOutcome:
            return TrackingOutcome(tracks=list(tracks))

        return TrackingRequest(sensor_id="s", runner=runner)

    @staticmethod
    def _person_track(track_id: str, start: float, box: tuple[int, int, int, int]) -> Track:
        return Track(track_id, [Sample(start, box, 0.9), Sample(start + 0.2, box, 0.9)])

    async def _run(
        self,
        video: Path,
        detector: _SecondDetector,
        request: TrackingRequest | None,
        report: dict | None = None,
    ) -> list[dict]:
        return await CropEvidenceService(detector=detector, store=InMemoryEvidenceStore()).process_incident_report(
            video_ref=video, video_id="v", report=report or REPORT, batch_id="b", tracking=request
        )

    @pytest.mark.asyncio
    async def test_an_unconfirmed_box_retries_to_the_second_a_track_confirms(self, tiny_video_path: Path) -> None:
        detector = _SecondDetector({0: self.SCREEN, 1: self.PERSON})
        request = self._request(self._person_track("2", 1.0, (700, 700, 950, 950)))
        person = (await self._run(tiny_video_path, detector, request))[0]
        assert detector.person_seconds == [0, 1], "the first box, then the first retry second"
        assert person["box_seconds"] == 1.0 and person["track"] == "evidence/v/b/E1-track.json"
        assert person["bounding_box"] == pytest.approx(self.PERSON, abs=60)
        assert request.summary.matched == 1

    @pytest.mark.asyncio
    async def test_with_no_confirming_box_it_falls_back_to_the_nearest_track_sample_in_its_span(
        self, tiny_video_path: Path
    ) -> None:
        detector = _SecondDetector({0: self.SCREEN, 1: self.NOWHERE})
        request = self._request(self._person_track("2", 1.0, (700, 700, 950, 950)))
        person = (await self._run(tiny_video_path, detector, request))[0]
        assert detector.person_seconds[:2] == [0, 1], "the remaining locate seconds were tried before the fallback"
        assert person["located"] is True and person["box_seconds"] == 1.0 and person["track"]
        assert person["bounding_box"] == pytest.approx(self.PERSON, abs=60)

    @pytest.mark.asyncio
    async def test_a_track_outside_the_entitys_span_is_not_a_fallback(self, tiny_video_path: Path) -> None:
        report = copy.deepcopy(REPORT)
        report["entities"][0]["last_seen_seconds"] = 0
        detector = _SecondDetector({0: self.SCREEN, 1: self.NOWHERE})
        request = self._request(self._person_track("2", 1.0, (700, 700, 950, 950)))
        person = (await self._run(tiny_video_path, detector, request, report))[0]
        assert person["box_seconds"] == 0 and "track" not in person
        assert person["bounding_box"] == pytest.approx(self.SCREEN, abs=60)

    @pytest.mark.asyncio
    async def test_two_unconfirmed_entities_fall_back_to_different_tracks(self, tiny_video_path: Path) -> None:
        report = copy.deepcopy(REPORT)
        report["entities"].append({**report["entities"][0], "entity_id": "E2"})
        detector = _SecondDetector({0: self.SCREEN, 1: self.NOWHERE})
        request = self._request(
            self._person_track("2", 1.0, (700, 700, 950, 950)), self._person_track("3", 1.2, (100, 500, 400, 700))
        )
        first, second = (await self._run(tiny_video_path, detector, request, report))[:2]
        assert first["box_seconds"] == 1.0 and second["box_seconds"] == pytest.approx(1.2)
        assert first["bounding_box"] != second["bounding_box"]

    @pytest.mark.asyncio
    async def test_a_confirmed_first_box_makes_no_extra_detector_call(self, tiny_video_path: Path) -> None:
        detector = _SecondDetector({0: self.SCREEN})
        request = self._request(self._person_track("2", 0.0, (210, 210, 590, 590)))
        person = (await self._run(tiny_video_path, detector, request))[0]
        assert detector.person_seconds == [0]
        assert person["track"] and person["bounding_box"] == pytest.approx(self.SCREEN, abs=60)

    @pytest.mark.asyncio
    async def test_without_solid_tracks_the_first_box_stands(self, tiny_video_path: Path) -> None:
        ghost = Track("9", [Sample(1.0, (700, 700, 950, 950), 0.9)])  # one detection: not solid
        for tracks in ((), (ghost,)):
            detector = _SecondDetector({0: self.SCREEN, 1: self.PERSON})
            person = (await self._run(tiny_video_path, detector, self._request(*tracks)))[0]
            assert detector.person_seconds == [0]
            assert person["box_seconds"] == 0 and "track" not in person

    @pytest.mark.asyncio
    async def test_with_tracking_off_the_first_box_stands(self, tiny_video_path: Path) -> None:
        detector = _SecondDetector({0: self.SCREEN, 1: self.PERSON})
        person = (await self._run(tiny_video_path, detector, None))[0]
        assert detector.person_seconds == [0] and person["box_seconds"] == 0 and "track" not in person

    @pytest.mark.asyncio
    async def test_instruments_and_assets_keep_their_first_box(self, tiny_video_path: Path) -> None:
        detector = _SecondDetector({0: self.SCREEN})
        request = self._request(self._person_track("2", 1.0, (700, 700, 950, 950)))
        _, instrument, asset = await self._run(tiny_video_path, detector, request)
        assert instrument["box_seconds"] == 1 and asset["box_seconds"] == 1


class TestObjectTrackingEvidence:
    """Instrument and asset tracks are stored like person tracks, marked with their source."""

    DETECTOR = FakeDetector(detections=[Detection(box=[200, 200, 600, 600], score=0.95, label="x")])

    @staticmethod
    def _request(outcome: ObjectTrackingOutcome | None = None, limit: int = 10) -> tuple[ObjectTrackingRequest, list]:
        calls: list = []

        async def runner(video_ref: object, seeds: list[ObjectSeed], *, detector: object, **_: object) -> object:
            calls.append((video_ref, seeds, detector))
            return outcome if outcome is not None else ObjectTrackingOutcome()

        return ObjectTrackingRequest(budget=CropCallBudget(limit), runner=runner), calls

    @staticmethod
    def _track(item_id: str, moment: float = 1.5) -> Track:
        return Track(
            f"object-{item_id}",
            [
                Sample(0.5, (150, 150, 650, 650), 0.9),
                Sample(moment, (100, 100, 900, 900), 1.0),
                Sample(1.9, (300, 300, 400, 400), -0.1),
            ],
            source=OBJECT_TRACK_SOURCE,
        )

    @pytest.mark.asyncio
    async def test_instruments_and_assets_are_seeded_from_their_own_unpadded_box_and_second(
        self, tiny_video_path: Path
    ) -> None:
        request, calls = self._request()
        await CropEvidenceService(detector=self.DETECTOR, store=InMemoryEvidenceStore()).process_incident_report(
            video_ref=tiny_video_path, video_id="v", report=REPORT, batch_id="b", object_tracking=request
        )
        ((video_ref, seeds, detector),) = calls
        assert video_ref == tiny_video_path
        assert [(s.item_id, s.label, s.box, s.seconds) for s in seeds] == [
            ("I1", "crowbar", (200, 200, 600, 600), 1.0),
            ("A1", "cash register", (200, 200, 600, 600), 1.0),
        ]  # the person keeps the DeepStream path
        assert isinstance(detector, BudgetedDetector) and detector.budget is request.budget
        assert request.summary.seeds == 2 and request.summary.budget["cap"] == 10

    @pytest.mark.asyncio
    async def test_a_tracked_item_gets_a_track_file_a_source_and_a_frame_at_its_first_seen_second(
        self, tiny_video_path: Path
    ) -> None:
        store = InMemoryEvidenceStore()
        outcome = ObjectTrackingOutcome(tracks={"I1": self._track("I1")}, items={"I1": {"samples": 3}}, tracker="MIL")
        request, _ = self._request(outcome)
        results = await CropEvidenceService(detector=self.DETECTOR, store=store).process_incident_report(
            video_ref=tiny_video_path,
            video_id="v",
            video_key="uploads/a.mp4",
            report=REPORT,
            batch_id="run-1",
            object_tracking=request,
        )
        person, instrument, asset = results
        assert instrument["track"] == "evidence/uploads/a.mp4/run-1/I1-track.json"
        assert instrument["track_source"] == OBJECT_TRACK_SOURCE
        assert (instrument["track_first_seen_seconds"], instrument["track_last_seen_seconds"]) == (0.5, 1.9)
        assert instrument["box_seconds"] == 0.5 and instrument["located"] is True  # re-cut at the first sample
        assert instrument["bounding_box"] == pytest.approx([150, 150, 650, 650], abs=60)
        assert [row["t"] for row in json.loads(await store.get_image(instrument["track"]))] == [0.5, 1.5, 1.9]
        for other in (person, asset):
            assert "track" not in other and "track_source" not in other
        assert asset["box_seconds"] == asset["timestamp_seconds"]
        assert request.summary.tracked == 1 and request.summary.tracker == "MIL" and request.summary.items["I1"]

    @pytest.mark.asyncio
    async def test_person_counters_ignore_object_tracks_and_person_entries_have_no_source(
        self, tiny_video_path: Path
    ) -> None:
        async def people(video_ref: object, *, sensor_id: str, **_: object) -> TrackingOutcome:
            # solid: two real detections (a ghost track backs no entity)
            return TrackingOutcome(
                tracks=[Track("7", [Sample(0.0, (210, 210, 590, 590), 0.9), Sample(0.2, (210, 210, 590, 590), 0.9)])]
            )

        person_request = TrackingRequest(sensor_id="s", runner=people)
        request, _ = self._request(ObjectTrackingOutcome(tracks={"I1": self._track("I1")}))
        results = await CropEvidenceService(
            detector=self.DETECTOR, store=InMemoryEvidenceStore()
        ).process_incident_report(
            video_ref=tiny_video_path,
            video_id="v",
            report=REPORT,
            batch_id="b",
            tracking=person_request,
            object_tracking=request,
        )
        person, instrument, _ = results
        assert person["track"] and "track_source" not in person  # as before the object tracker existed
        assert instrument["track_source"] == OBJECT_TRACK_SOURCE
        assert person_request.summary.matched == 1 and person_request.summary.tracks == 1

    @pytest.mark.asyncio
    async def test_without_a_request_nothing_changes(self, tiny_video_path: Path) -> None:
        results = await CropEvidenceService(
            detector=self.DETECTOR, store=InMemoryEvidenceStore()
        ).process_incident_report(video_ref=tiny_video_path, video_id="v", report=REPORT, batch_id="b")
        assert all("track" not in r and "track_source" not in r for r in results)

    @pytest.mark.asyncio
    async def test_nothing_located_means_nothing_to_follow_and_the_runner_is_not_started(
        self, tiny_video_path: Path
    ) -> None:
        request, calls = self._request()
        await CropEvidenceService(detector=FakeDetector(), store=InMemoryEvidenceStore()).process_incident_report(
            video_ref=tiny_video_path, video_id="v", report=REPORT, batch_id="b", object_tracking=request
        )
        assert calls == [] and request.summary.seeds == 0 and request.summary.skipped

    @pytest.mark.asyncio
    async def test_without_a_store_the_runner_is_not_started(self, tiny_video_path: Path) -> None:
        request, calls = self._request()
        await CropEvidenceService(detector=self.DETECTOR, store=None).process_incident_report(
            video_ref=tiny_video_path, video_id="v", report=REPORT, object_tracking=request
        )
        assert calls == [] and request.summary.skipped

    @pytest.mark.asyncio
    async def test_a_runner_that_raises_never_fails_the_evidence(self, tiny_video_path: Path) -> None:
        async def runner(*_: object, **__: object) -> ObjectTrackingOutcome:
            raise RuntimeError("boom")

        request = ObjectTrackingRequest(budget=CropCallBudget(5), runner=runner)
        results = await CropEvidenceService(
            detector=self.DETECTOR, store=InMemoryEvidenceStore()
        ).process_incident_report(
            video_ref=tiny_video_path, video_id="v", report=REPORT, batch_id="b", object_tracking=request
        )
        assert len(results) == 3 and all(r["image"] for r in results)
        assert request.summary.skipped is not None and "boom" not in request.summary.skipped

    @pytest.mark.asyncio
    async def test_the_frame_is_re_cut_at_the_object_tracks_first_seen_second(self, tiny_video_path: Path) -> None:
        request, _ = self._request(ObjectTrackingOutcome(tracks={"I1": self._track("I1")}))
        results = await CropEvidenceService(
            detector=self.DETECTOR, store=InMemoryEvidenceStore()
        ).process_incident_report(
            video_ref=tiny_video_path,
            video_id="v",
            report=REPORT,
            batch_id="b",
            object_tracking=request,
        )
        instrument = results[1]
        assert instrument["track_source"] == OBJECT_TRACK_SOURCE and instrument["track"]
        assert instrument["box_seconds"] == 0.5 and instrument["located"] is True


class TestZoomSecondLook:
    """The zoomed second look: a close-up of each located item is named again and a confident correction applied."""

    LOCATED: ClassVar[list[Detection]] = [Detection(box=[200, 200, 600, 600], score=0.95, label="x")]
    BAG_REPLY: ClassVar[dict] = {
        "name": "plastic bag",
        "description": "A crumpled white plastic bag on the floor.",
        "confidence": 0.9,
        "changed": True,
    }

    def _service(self, namer: FakeZoomNamer) -> CropEvidenceService:
        return CropEvidenceService(
            detector=FakeDetector(detections=self.LOCATED), store=InMemoryEvidenceStore(), namer=namer
        )

    async def _run(self, service: CropEvidenceService, video: Path, report: dict, **kwargs: object) -> dict[str, dict]:
        results = await service.process_incident_report(video_ref=video, video_id="v", report=report, **kwargs)
        return {r["item_id"]: r for r in results}

    def test_zoom_crop_takes_a_25_percent_margin_and_upscales_a_small_cut_for_the_model(self) -> None:
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        box = [500, 500, 550, 540]  # 24 x 25.6 px at 480p: the "25-pixel smudge"
        zoomed = zoom_crop(frame, box)
        assert zoomed is not None
        width, height = cv2.imdecode(np.frombuffer(zoomed, np.uint8), cv2.IMREAD_COLOR).shape[1::-1]
        assert min(width, height) == 256 and max(width, height) < 400  # upscaled, aspect kept
        # The stored crop beside it keeps the 5% margin and is never upscaled.
        c_ymin, c_xmin, c_ymax, c_xmax = apply_box_padding(box, 640, 480, padding_fraction=0.05)
        assert (c_ymax - c_ymin, c_xmax - c_xmin) == (26, 28)
        z_ymin, z_xmin, z_ymax, z_xmax = apply_box_padding(box, 640, 480, padding_fraction=0.25)
        assert (z_ymax - z_ymin, z_xmax - z_xmin) == (36, 38)

    def test_a_located_crop_carries_its_zoom_bytes_but_the_stored_crop_is_unchanged(
        self, tiny_video_path: Path
    ) -> None:
        result = crop_evidence_item(tiny_video_path, 0, "x", FakeDetector(detections=self.LOCATED))
        assert result is not None and result.zoom_bytes is not None and result.image_bytes is not None
        assert result.zoom_frame_bytes is not None
        context = cv2.imdecode(np.frombuffer(result.zoom_frame_bytes, np.uint8), cv2.IMREAD_COLOR)
        assert context.shape[:2] == (80, 120)  # the whole frame, with the box drawn on it
        _, green, red = (int(v) for v in context[16, 24])  # the top-left corner of the drawn box (BGR)
        assert red > 150 and green < 60  # red ink on the blue frame, allowing for JPEG chroma blur
        crop_h = cv2.imdecode(np.frombuffer(result.image_bytes, np.uint8), cv2.IMREAD_COLOR).shape[0]
        zoom_h = cv2.imdecode(np.frombuffer(result.zoom_bytes, np.uint8), cv2.IMREAD_COLOR).shape[0]
        assert crop_h < 80 and zoom_h >= 256  # the 80 px frame yields a small stored crop and an upscaled close-up

    @pytest.mark.parametrize(
        "reply",
        [
            {"description": "x", "confidence": 0.9, "changed": True},
            {"name": "bag", "description": "x", "confidence": 1.5, "changed": True},
            {"name": "bag", "description": "x", "confidence": "high", "changed": True},
            {"name": "bag", "description": "x", "confidence": 0.9, "changed": "yes"},
            {"name": "", "description": "x", "confidence": 0.9, "changed": False},
        ],
    )
    def test_parse_zoom_reply_is_strict(self, reply: dict) -> None:
        with pytest.raises(ValueError):
            parse_zoom_reply(reply)

    def test_parse_zoom_reply_normalises_whitespace(self) -> None:
        assert parse_zoom_reply(
            {"name": " plastic  bag ", "description": " a bag ", "confidence": 1, "changed": False}
        ) == (ZoomVerdict(name="plastic bag", description="a bag", confidence=1.0, changed=False))

    def test_flag_is_on_unless_set_to_a_false_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("INCIDENT_ZOOM_NAMES_ENABLED", raising=False)
        assert zoom_names_enabled() is True
        for value in ("0", "false", "No", " off "):
            monkeypatch.setenv("INCIDENT_ZOOM_NAMES_ENABLED", value)
            assert zoom_names_enabled() is False, value
        monkeypatch.setenv("INCIDENT_ZOOM_NAMES_ENABLED", "1")
        assert zoom_names_enabled() is True

    @pytest.mark.asyncio
    async def test_a_confident_correction_renames_the_entry_and_the_report_item(self, tiny_video_path: Path) -> None:
        report = copy.deepcopy(REPORT)
        namer = FakeZoomNamer({"cash register": self.BAG_REPLY})
        by_id = await self._run(self._service(namer), tiny_video_path, report, zoom_names=True)

        asset = by_id["A1"]
        assert asset["item_name"] == "plastic bag" and asset["description"] == self.BAG_REPLY["description"]
        assert asset["name_before"] == "cash register" and asset["description_before"] == "Store register"
        assert asset["zoom_checked"] is True and asset["zoom_confidence"] == 0.9 and asset["zoom_gap"] is None
        # Never touched: id, label (the detector query), timestamps, image keys.
        assert asset["item_id"] == "A1" and asset["label"] == "cash register" and asset["timestamp_seconds"] == 1
        assert asset["image"] == "evidence/v/run-1/A1.jpg" and asset["located"] is True
        # The report payload agrees, so the title-level text the console shows matches the evidence card.
        assert report["assets"][0]["name"] == "plastic bag"
        assert report["assets"][0]["description"] == self.BAG_REPLY["description"]
        assert report["assets"][0]["asset_id"] == "A1" and report["assets"][0]["label"] == "cash register"
        assert report["instruments"] == REPORT["instruments"] and report["entities"] == REPORT["entities"]
        # The model saw the close-up with the item's kind, name and description.
        call = next(c for c in namer.calls if c["name"] == "cash register")
        assert call["item_type"] == "asset" and call["description"] == "Store register"
        assert call["jpeg_bytes"] > 0 and call["context_bytes"] > 0  # the close-up and the frame with the box

    @pytest.mark.asyncio
    async def test_an_instrument_correction_updates_its_name_and_description(self, tiny_video_path: Path) -> None:
        report = copy.deepcopy(REPORT)
        reply = {"name": "tyre iron", "description": "An L-shaped tyre iron.", "confidence": 0.75, "changed": True}
        by_id = await self._run(
            self._service(FakeZoomNamer({"crowbar": reply})), tiny_video_path, report, zoom_names=True
        )
        assert by_id["I1"]["item_name"] == "tyre iron" and by_id["I1"]["name_before"] == "crowbar"
        assert report["instruments"][0]["name"] == "tyre iron"
        assert report["instruments"][0]["description"] == "An L-shaped tyre iron."
        assert report["instruments"][0]["threat_level"] == 3 and report["instruments"][0]["entity_id"] == "E1"

    @pytest.mark.asyncio
    async def test_an_entity_keeps_its_type_but_its_description_can_be_corrected(self, tiny_video_path: Path) -> None:
        report = copy.deepcopy(REPORT)
        reply = {"name": "animal", "description": "Person in a dark jacket.", "confidence": 0.95, "changed": True}
        by_id = await self._run(
            self._service(FakeZoomNamer({"human": reply})), tiny_video_path, report, zoom_names=True
        )
        entity = by_id["E1"]
        assert entity["item_name"] == "human" and entity["description"] == "Person in a dark jacket."
        assert entity["name_before"] == "human" and entity["description_before"] == "Suspect in hoodie"
        assert (
            report["entities"][0]["type"] == "human"
            and report["entities"][0]["description"] == "Person in a dark jacket."
        )

    @pytest.mark.asyncio
    async def test_a_low_confidence_correction_is_ignored_and_recorded(self, tiny_video_path: Path) -> None:
        report = copy.deepcopy(REPORT)
        reply = {**self.BAG_REPLY, "confidence": 0.69}
        by_id = await self._run(
            self._service(FakeZoomNamer({"cash register": reply})), tiny_video_path, report, zoom_names=True
        )
        asset = by_id["A1"]
        assert asset["item_name"] == "cash register" and asset["name_before"] is None
        assert asset["zoom_checked"] is True and asset["zoom_confidence"] == 0.69
        assert "plastic bag" in asset["zoom_gap"] and "below 0.7" in asset["zoom_gap"]
        assert report == REPORT

    @pytest.mark.asyncio
    async def test_an_unchanged_verdict_only_records_the_check(self, tiny_video_path: Path) -> None:
        report = copy.deepcopy(REPORT)
        by_id = await self._run(self._service(FakeZoomNamer()), tiny_video_path, report, zoom_names=True)
        for entry in by_id.values():
            assert entry["zoom_checked"] is True and entry["zoom_confidence"] == 1.0
            assert entry["name_before"] is None and entry["zoom_gap"] is None and "description_before" not in entry
        assert report == REPORT

    @pytest.mark.asyncio
    async def test_a_malformed_or_failed_reply_never_fails_the_item(self, tiny_video_path: Path) -> None:
        report = copy.deepcopy(REPORT)
        namer = FakeZoomNamer({"cash register": "not json at all", "crowbar": RuntimeError("gateway timed out")})
        by_id = await self._run(self._service(namer), tiny_video_path, report, zoom_names=True)
        assert by_id["A1"]["image"] == "evidence/v/run-1/A1.jpg" and by_id["A1"]["image_gap"] is None
        assert by_id["A1"]["zoom_checked"] is False and by_id["A1"]["zoom_confidence"] is None
        assert by_id["A1"]["zoom_gap"].startswith("second look failed: JSONDecodeError")
        assert by_id["I1"]["zoom_checked"] is False and "gateway timed out" in by_id["I1"]["zoom_gap"]
        assert by_id["E1"]["zoom_checked"] is True
        assert report == REPORT

    @pytest.mark.asyncio
    async def test_the_flag_off_makes_no_call(self, tiny_video_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("INCIDENT_ZOOM_NAMES_ENABLED", "0")
        namer = FakeZoomNamer({"cash register": self.BAG_REPLY})
        report = copy.deepcopy(REPORT)
        by_id = await self._run(self._service(namer), tiny_video_path, report)  # default: the flag decides
        assert namer.calls == []
        for entry in by_id.values():
            assert entry["image"] is not None and entry["zoom_checked"] is False
            assert entry["zoom_gap"] == "zoomed second look disabled (INCIDENT_ZOOM_NAMES_ENABLED)"
        assert report == REPORT

    @pytest.mark.asyncio
    async def test_an_item_that_is_not_located_gets_no_second_look(self, tiny_video_path: Path) -> None:
        namer = FakeZoomNamer({"cash register": self.BAG_REPLY})
        service = CropEvidenceService(detector=FakeDetector(), store=InMemoryEvidenceStore(), namer=namer)
        by_id = await self._run(service, tiny_video_path, copy.deepcopy(REPORT), zoom_names=True)
        assert namer.calls == []
        assert all(e["zoom_checked"] is False and e["zoom_gap"] == "object not located" for e in by_id.values())

    @pytest.mark.asyncio
    async def test_a_detector_without_credentials_is_not_asked_for_a_second_look(self, tiny_video_path: Path) -> None:
        service = CropEvidenceService(
            detector=FakeDetector(detections=self.LOCATED),
            store=InMemoryEvidenceStore(),
            namer=DefaultDetector(api_key=""),
        )
        with patch("vss_agents.utils.crop_evidence.httpx.post") as post:
            by_id = await self._run(service, tiny_video_path, copy.deepcopy(REPORT), zoom_names=True)
        post.assert_not_called()
        assert all("key missing" in e["zoom_gap"] and e["zoom_checked"] is False for e in by_id.values())

    def test_the_default_detector_is_also_the_namer_and_its_zoom_calls_are_outside_the_budget(self) -> None:
        detector = DefaultDetector(api_key="k", base_url="https://vlm.example", model_name="m")
        service = CropEvidenceService(detector=detector, store=InMemoryEvidenceStore())
        assert service.namer is detector
        budget = CropCallBudget(1)
        budgeted = detector.with_call_budget(budget)

        def reply(content: str) -> MagicMock:
            response = MagicMock(status_code=200)
            response.json.return_value = {"choices": [{"message": {"content": content}}]}
            return response

        replies = [
            reply('{"name": "plastic bag", "description": "A crumpled bag.", "confidence": 0.9}'),  # frame look
            reply(
                '```json\n{"name": "plastic bag", "description": "A white bag.", "confidence": 0.8}\n```'
            ),  # close-up
            reply('{"name": "plastic bag", "description": "A bag on the floor.", "confidence": 0.95, "changed": true}'),
        ]
        with patch("vss_agents.utils.crop_evidence.httpx.post", side_effect=replies) as post:
            verdict = budgeted.zoom_name(
                b"jpeg", item_type="asset", name="soccer ball", description="A ball.", context_jpeg=b"frame"
            )
        assert verdict == ZoomVerdict(
            name="plastic bag",
            description="A bag on the floor.",
            confidence=0.8,  # the lowest of the three calls
            changed=True,
            seen={"frame": "plastic bag", "close_up": "plastic bag"},
        )
        assert budget.calls == 0  # three zoom calls, none counted against the grounding cap
        assert post.call_count == 3 and all(
            c.args[0] == "https://vlm.example/v1/chat/completions" for c in post.call_args_list
        )
        bodies = [c.kwargs["json"] for c in post.call_args_list]
        assert all(b["model"] == "m" and b["response_format"] == {"type": "json_object"} for b in bodies)
        contents = [b["messages"][0]["content"] for b in bodies]
        assert [[part["type"] for part in c] for c in contents] == [
            ["image_url", "text"],  # the frame with the box
            ["image_url", "image_url", "text"],  # the frame and the close-up
            ["text"],  # the text-only merge
        ]
        assert contents[1][1]["image_url"]["url"].endswith(base64.b64encode(b"jpeg").decode())
        # The blind looks never see the report's name; only the merge does.
        assert "soccer ball" not in contents[0][1]["text"] and "soccer ball" not in contents[1][2]["text"]
        assert '"soccer ball"' in contents[2][0]["text"] and '"plastic bag"' in contents[2][0]["text"]

    def test_a_rate_limited_zoom_call_is_retried_once(self) -> None:
        detector = DefaultDetector(api_key="k")
        limited = MagicMock(status_code=429, headers={"retry-after": "0"})
        ok = MagicMock(status_code=200, headers={})
        ok.json.return_value = {
            "choices": [{"message": {"content": '{"name": "bag", "description": "", "confidence": 1}'}}]
        }
        with (
            patch("vss_agents.utils.crop_evidence.httpx.post", side_effect=[limited, ok, limited, limited]) as post,
            patch("vss_agents.utils.crop_evidence.time.sleep") as sleep,
        ):
            assert detector._chat_json([b"jpeg"], "look", dict)["name"] == "bag"
            limited.raise_for_status.side_effect = RuntimeError("429")
            with pytest.raises(RuntimeError):
                detector._chat_json([b"jpeg"], "look", dict)  # a second 429 is not retried again
        assert post.call_count == 4 and sleep.call_count == 2

    def test_request_limits_give_a_copy_that_keeps_the_budget(self) -> None:
        budget = CropCallBudget(5)
        detector = DefaultDetector(api_key="k", call_budget=budget)
        limited = detector.with_request_limits(15.0, 1)
        assert limited is not detector and limited.call_budget is budget
        assert (limited.timeout_seconds, limited.max_attempts) == (15.0, 1)
        assert (detector.timeout_seconds, detector.max_attempts) == (120.0, 4)

    def test_a_single_attempt_detector_does_not_retry_a_rate_limit(self) -> None:
        detector = DefaultDetector(api_key="k").with_request_limits(15.0, 1)
        limited = MagicMock(status_code=429, headers={"retry-after": "60"})
        limited.raise_for_status.side_effect = httpx.HTTPStatusError("429", request=MagicMock(), response=limited)
        with (
            patch("vss_agents.utils.crop_evidence.httpx.post", return_value=limited) as post,
            patch("vss_agents.utils.crop_evidence.time.sleep") as sleep,
        ):
            with pytest.raises(httpx.HTTPStatusError):
                detector._request_box({})
        assert post.call_count == 1 and sleep.call_count == 0
        assert post.call_args.kwargs["timeout"] == 15.0

    def test_disagreeing_looks_skip_the_merge_call_and_keep_the_report(self) -> None:
        detector = DefaultDetector(api_key="k")

        def reply(content: str) -> MagicMock:
            response = MagicMock(status_code=200)
            response.json.return_value = {"choices": [{"message": {"content": content}}]}
            return response

        replies = [
            reply('{"name": "wheelchair", "description": "", "confidence": 0.8}'),
            reply('{"name": "shoe", "description": "", "confidence": 0.6}'),
        ]
        with patch("vss_agents.utils.crop_evidence.httpx.post", side_effect=replies) as post:
            verdict = detector.zoom_name(
                b"jpeg", item_type="asset", name="soccer ball", description="A ball.", context_jpeg=b"frame"
            )
        assert post.call_count == 2  # no text merge
        assert verdict == ZoomVerdict(
            name="soccer ball",
            description="A ball.",
            confidence=0.6,
            changed=False,
            seen={"frame": "wheelchair", "close_up": "shoe"},
        )

    @pytest.mark.parametrize(
        ("first", "second", "agree"),
        [
            ("plastic bag", "crumpled plastic bag", True),
            ("A plastic bag", "plastic bag", True),
            ("Plastic Bag", "plastic bag", True),
            ("wheelchair", "shoe", False),
            ("black bag", "black ball", False),
            ("", "bag", False),
        ],
    )
    def test_looks_agree_on_content_words(self, first: str, second: str, agree: bool) -> None:
        assert looks_agree(ZoomLook(first, "", 1.0), ZoomLook(second, "", 1.0)) is agree

    def test_without_a_context_frame_the_close_up_is_the_only_look(self) -> None:
        detector = DefaultDetector(api_key="k")
        look = MagicMock(status_code=200)
        look.json.return_value = {
            "choices": [{"message": {"content": '{"name": "bag", "description": "", "confidence": 1}'}}]
        }
        merge = MagicMock(status_code=200)
        merge.json.return_value = {
            "choices": [
                {"message": {"content": '{"name": "bag", "description": "", "confidence": 1, "changed": false}'}}
            ]
        }
        with patch("vss_agents.utils.crop_evidence.httpx.post", side_effect=[look, merge]) as post:
            verdict = detector.zoom_name(b"jpeg", item_type="asset", name="bag", description="")
        assert post.call_count == 2 and verdict.seen == {"frame": "bag", "close_up": "bag"}

    @pytest.mark.asyncio
    async def test_disagreeing_looks_keep_the_report_and_say_so(self, tiny_video_path: Path) -> None:
        report = copy.deepcopy(REPORT)
        service = self._service(FakeZoomNamer())
        service.namer.replies["cash register"] = ZoomVerdict(
            name="cash register",
            description="Store register",
            confidence=0.0,
            changed=False,
            seen={"frame": "wheelchair", "close_up": "shoe"},
        )
        by_id = await self._run(service, tiny_video_path, report, zoom_names=True)
        asset = by_id["A1"]
        assert asset["item_name"] == "cash register" and asset["zoom_checked"] is True
        assert asset["zoom_seen"] == {"frame": "wheelchair", "close_up": "shoe"}
        assert asset["zoom_gap"] == "looks disagree, report kept: frame saw 'wheelchair', close_up saw 'shoe'"
        assert report == REPORT

    def test_the_default_detector_raises_on_a_malformed_zoom_reply(self) -> None:
        detector = DefaultDetector(api_key="k")
        reply = MagicMock(status_code=200)
        reply.json.return_value = {"choices": [{"message": {"content": '{"bbox_2d": [1, 2, 3, 4]}'}}]}
        with patch("vss_agents.utils.crop_evidence.httpx.post", return_value=reply), pytest.raises(ValueError):
            detector.zoom_name(b"jpeg", item_type="asset", name="ball", description="")
