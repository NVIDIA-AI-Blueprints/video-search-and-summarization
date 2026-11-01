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

"""Unit tests for object_tracking: a seeded OpenCV tracker corrected by the detector, on synthetic clips.

The clips hold one bright square moving right on a black background. The fake tracker and the fake detector read the
square off the frame itself (an oracle), so a test decides what is found by deciding what the frames show.
"""

from collections.abc import Generator
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from typing import Any

import cv2
import numpy as np
import pytest

from vss_agents.utils import object_tracking
from vss_agents.utils.crop_evidence import BudgetedDetector
from vss_agents.utils.crop_evidence import CropCallBudget
from vss_agents.utils.crop_evidence import Detection
from vss_agents.utils.object_tracking import ObjectTrackingSettings
from vss_agents.utils.object_tracking import TrackerUnavailableError
from vss_agents.utils.object_tracking import object_tracking_enabled
from vss_agents.utils.object_tracking import run_object_tracking
from vss_agents.utils.object_tracking import select_tracker
from vss_agents.utils.track_evidence import OBJECT_TRACK_SOURCE
from vss_agents.utils.track_evidence import PERSON_TRACK_SOURCE
from vss_agents.utils.track_evidence import ObjectSeed
from vss_agents.utils.track_evidence import Sample
from vss_agents.utils.track_evidence import Track
from vss_agents.utils.track_evidence import box_iou
from vss_agents.utils.track_evidence import track_json

WIDTH, HEIGHT, FPS, SECONDS = 200, 100, 10, 8
SIDE = 20
Y0 = 40


def square_box(index: int) -> tuple[int, int, int, int]:
    """The square's [ymin, xmin, ymax, xmax] in pixels at frame ``index`` (it moves 2 px a frame)."""
    x = 10 + 2 * index
    return Y0, x, Y0 + SIDE, x + SIDE


def draw(index: int, visible: bool = True) -> np.ndarray:
    frame = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    if visible:
        ymin, xmin, ymax, xmax = square_box(index)
        frame[ymin:ymax, xmin:xmax] = 255
    return frame


def seen(frame: np.ndarray) -> list[int] | None:
    """The bright square in a frame as a 0..1000 box, None when the frame shows none (the oracle)."""
    ys, xs = np.nonzero(frame[:, :, 0] > 128)
    if ys.size == 0:
        return None
    height, width = frame.shape[:2]
    return [
        round(ys.min() * 1000 / height),
        round(xs.min() * 1000 / width),
        round((ys.max() + 1) * 1000 / height),
        round((xs.max() + 1) * 1000 / width),
    ]


class OracleTracker:
    """Follows the square exactly and reports failure when the frame shows none."""

    def init(self, frame: np.ndarray, rect: tuple[int, int, int, int]) -> None:
        return None

    def update(self, frame: np.ndarray) -> tuple[bool, tuple[int, int, int, int]]:
        box = seen(frame)
        if box is None:
            return False, (0, 0, 0, 0)
        height, width = frame.shape[:2]
        return True, (
            round(box[1] * width / 1000),
            round(box[0] * height / 1000),
            round((box[3] - box[1]) * width / 1000),
            round((box[2] - box[0]) * height / 1000),
        )


class StubbornTracker(OracleTracker):
    """Like OpenCV's MIL: it never reports failure, when the square is gone it keeps returning its last box."""

    def __init__(self) -> None:
        self.rect = (0, 0, 1, 1)

    def init(self, frame: np.ndarray, rect: tuple[int, int, int, int]) -> None:
        self.rect = rect

    def update(self, frame: np.ndarray) -> tuple[bool, tuple[int, int, int, int]]:
        found, rect = super().update(frame)
        if found:
            self.rect = rect
        return True, self.rect


class OracleDetector:
    """Finds the square on the frame it is given (the whole frame, as the real detector is), and counts its calls."""

    def __init__(self, shift: int = 0, blind: bool = False, error_at: int | None = None) -> None:
        self.shift, self.blind, self.error_at = shift, blind, error_at
        self.calls = 0
        self.queries: list[str] = []

    def detect(self, frame: np.ndarray, query: str) -> list[Detection]:
        self.calls += 1
        self.queries.append(query)
        if self.error_at is not None and self.calls == self.error_at:
            raise RuntimeError("detector down")
        box = None if self.blind else seen(frame)
        if box is None:
            return []
        ymin, xmin, ymax, xmax = box
        return [Detection(box=[ymin, xmin + self.shift, ymax, xmax + self.shift], score=1.0, label=query)]


def write_video(path: Path, vanish_at: int | None = None) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), float(FPS), (WIDTH, HEIGHT))
    for index in range(FPS * SECONDS):
        writer.write(draw(index, visible=vanish_at is None or index < vanish_at))
    writer.release()


@pytest.fixture
def clip() -> Generator[Path]:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "clip.mp4"
        write_video(path)
        yield path


@pytest.fixture
def vanishing_clip() -> Generator[Path]:
    """The square disappears at frame 50 (5.0 s)."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "clip.mp4"
        write_video(path, vanish_at=50)
        yield path


def seed_at(seconds: float, item_id: str = "I1") -> ObjectSeed:
    index = round(seconds * FPS)
    ymin, xmin, ymax, xmax = square_box(index)
    box = (
        round(ymin * 1000 / HEIGHT),
        round(xmin * 1000 / WIDTH),
        round(ymax * 1000 / HEIGHT),
        round(xmax * 1000 / WIDTH),
    )
    return ObjectSeed(item_id=item_id, label="crowbar", box=box, seconds=float(seconds))


def settings(**overrides: Any) -> ObjectTrackingSettings:
    values: dict[str, Any] = {
        "sample_fps": 5.0,
        "detect_interval_seconds": 1.0,
        "span_seconds": 3.0,
        "time_cap_seconds": 60.0,
        "max_detector_calls": 100,
    }
    return ObjectTrackingSettings(**{**values, **overrides})


async def run(
    clip_path: Path,
    detector: Any,
    *seeds: ObjectSeed,
    tracker: Any = OracleTracker,
    config: ObjectTrackingSettings | None = None,
) -> Any:
    return await run_object_tracking(
        clip_path, list(seeds), detector=detector, settings=config or settings(), tracker_factory=tracker
    )


class TestPropagation:
    @pytest.mark.asyncio
    async def test_an_item_is_followed_forwards_and_backwards_from_its_seed(self, clip: Path) -> None:
        outcome = await run(clip, OracleDetector(), seed_at(4.0))
        track = outcome.tracks["I1"]
        times = [sample.t for sample in track.samples]
        assert times == sorted(times) and len(set(times)) == len(times)
        assert track.first_seen == pytest.approx(1.0, abs=0.25) and track.last_seen == pytest.approx(7.0, abs=0.25)
        assert 4.0 in times
        # the box moves right with the square, in both directions from the seed
        before, after = track.samples[0], track.samples[-1]
        assert before.box[1] < track.samples[len(times) // 2].box[1] < after.box[1]
        for sample in track.samples:
            truth = seen(draw(round(sample.t * FPS)))
            assert truth is not None and box_iou(sample.box, truth) > 0.6
        assert outcome.items["I1"]["forward_samples"] > 0 and outcome.items["I1"]["backward_samples"] > 0

    @pytest.mark.asyncio
    async def test_a_track_is_marked_as_an_object_track_and_stops_at_the_ends_of_the_clip(self, clip: Path) -> None:
        outcome = await run(clip, OracleDetector(), seed_at(0.5), config=settings(span_seconds=30.0))
        track = outcome.tracks["I1"]
        assert track.source == OBJECT_TRACK_SOURCE and track.track_id == "object-I1"
        assert track.first_seen == pytest.approx(0.0, abs=0.25) and track.last_seen <= SECONDS
        assert outcome.tracker == "custom" and outcome.skipped is None

    @pytest.mark.asyncio
    async def test_the_track_file_has_the_shape_of_a_person_track_file(self, clip: Path) -> None:
        outcome = await run(clip, OracleDetector(), seed_at(4.0))
        person = Track("7", [Sample(0.0, (210, 210, 590, 590), 0.9)])
        assert person.source == PERSON_TRACK_SOURCE
        rows = json.loads(track_json(outcome.tracks["I1"]))
        assert set(rows[0]) == set(json.loads(track_json(person))[0]) == {"t", "box", "score"}
        assert all(len(row["box"]) == 4 and all(isinstance(v, int) for v in row["box"]) for row in rows)
        assert all(0 <= v <= 1000 for row in rows for v in row["box"])

    @pytest.mark.asyncio
    async def test_items_are_followed_independently(self, clip: Path) -> None:
        outcome = await run(clip, OracleDetector(), seed_at(3.0, "I1"), seed_at(5.0, "A1"))
        assert set(outcome.tracks) == {"I1", "A1"} and set(outcome.items) == {"I1", "A1"}


class TestReanchoring:
    @pytest.mark.asyncio
    async def test_the_detector_is_asked_for_the_label_every_interval_and_its_box_is_taken(self, clip: Path) -> None:
        detector = OracleDetector()
        outcome = await run(clip, detector, seed_at(4.0))
        item = outcome.items["I1"]
        # 3 s each way at one check a second
        assert detector.calls == item["detector_calls"] == 6 and set(detector.queries) == {"crowbar"}
        assert item["reanchors"] == 6 and item["lost"] == 0
        confirmed = [s for s in outcome.tracks["I1"].samples if s.score == 1.0]
        tracked = [s for s in outcome.tracks["I1"].samples if s.score == -0.1]
        assert len(confirmed) == 6 + 1  # the checks and the seed
        assert tracked and len(confirmed) + len(tracked) == item["samples"]

    @pytest.mark.asyncio
    async def test_a_detection_that_does_not_overlap_the_tracked_box_is_not_taken(self, clip: Path) -> None:
        detector = OracleDetector(shift=120)  # the detector's box is a whole square away from the tracker's
        outcome = await run(clip, detector, seed_at(4.0))
        assert outcome.items["I1"]["reanchors"] == 0 and detector.calls > 0
        assert outcome.items["I1"]["lost"] == 2  # two misses in a row end each pass

    @pytest.mark.asyncio
    async def test_a_detector_that_fails_does_not_end_a_segment_that_the_tracker_still_follows(
        self, clip: Path
    ) -> None:
        outcome = await run(clip, OracleDetector(error_at=2), seed_at(4.0))
        item = outcome.items["I1"]
        assert item["detector_errors"] == 1 and item["lost"] == 0 and item["samples"] > 5


class TestLoss:
    @pytest.mark.asyncio
    async def test_a_segment_ends_when_the_tracker_fails_and_the_detector_finds_nothing(
        self, vanishing_clip: Path
    ) -> None:
        outcome = await run(vanishing_clip, OracleDetector(), seed_at(3.0), config=settings(span_seconds=4.0))
        track, item = outcome.tracks["I1"], outcome.items["I1"]
        assert item["lost"] == 1  # forwards; the backward pass reaches the start of the clip's span
        assert track.last_seen < 5.0  # nothing after the square vanished at 5.0 s
        assert track.last_seen > 4.5  # but the visible part was followed to the end

    @pytest.mark.asyncio
    async def test_a_tracker_that_never_fails_is_ended_by_the_detectors_misses_and_unconfirmed_samples_are_dropped(
        self, vanishing_clip: Path
    ) -> None:
        detector = OracleDetector()
        config = settings(span_seconds=4.0, detect_interval_seconds=1.0, max_misses=2)
        outcome = await run(vanishing_clip, detector, seed_at(3.0), tracker=StubbornTracker, config=config)
        track, item = outcome.tracks["I1"], outcome.items["I1"]
        assert item["lost"] >= 1
        # The detector agreed at 4.0 s and found nothing at 5.0 s and 6.0 s: the samples after 4.0 s were never
        # confirmed, so they are dropped (the 4.2 to 4.8 s ones were right, which is the price of not trusting drift).
        assert track.last_seen == pytest.approx(4.0, abs=0.01)
        assert (
            detector.calls == 3 + 3
        )  # forwards: 4.0, 5.0, 6.0 (then lost); backwards: 2.0, 1.0, 0.0 (the clip's start)

    @pytest.mark.asyncio
    async def test_a_box_that_leaves_the_frame_is_a_failed_tracker(self, clip: Path) -> None:
        class Escaping:
            calls = 0

            def init(self, frame: np.ndarray, rect: tuple[int, int, int, int]) -> None:
                Escaping.calls = 0

            def update(self, frame: np.ndarray) -> tuple[bool, tuple[int, int, int, int]]:
                Escaping.calls += 1
                return True, (WIDTH - 2 + Escaping.calls * 10, Y0, SIDE, SIDE)  # slides off the right edge

        outcome = await run(
            clip, OracleDetector(blind=True), seed_at(1.0), tracker=Escaping, config=settings(span_seconds=3.0)
        )
        # blind detector: the tracker's own failure ends the forward pass at once, no sample is kept after the seed
        assert outcome.tracks == {} and outcome.items["I1"]["lost"] >= 1

    @pytest.mark.asyncio
    async def test_an_item_that_gains_no_sample_beyond_its_seed_has_no_track(self, vanishing_clip: Path) -> None:
        outcome = await run(vanishing_clip, OracleDetector(blind=True), seed_at(4.9), config=settings(span_seconds=0.0))
        assert outcome.tracks == {} and outcome.items["I1"]["samples"] == 0


class TestCaps:
    @pytest.mark.asyncio
    async def test_every_detector_call_counts_against_the_budget_and_the_cap_stops_the_pass(self, clip: Path) -> None:
        budget = CropCallBudget(3)
        detector = BudgetedDetector(OracleDetector(), budget)
        outcome = await run(clip, detector, seed_at(4.0))
        item = outcome.items["I1"]
        assert budget.calls == 3 and budget.refused >= 1 and item["detector_calls"] == 3
        assert item["capped"] == "detector-budget"
        assert outcome.tracks["I1"].samples  # what was followed up to the cap is kept

    @pytest.mark.asyncio
    async def test_the_time_cap_stops_the_run_and_reports_it(self, clip: Path) -> None:
        outcome = await run(clip, OracleDetector(), seed_at(4.0), config=settings(time_cap_seconds=0.0))
        assert outcome.time_cap_reached is True and outcome.items["I1"]["capped"] == "time"
        assert outcome.tracks == {}

    def test_a_pass_stops_at_its_deadline(self, clip: Path) -> None:
        reader = object_tracking.ClipReader(clip)
        frames = reader.frames(4.0, forward=True, sample_fps=5.0, span_seconds=3.0)
        result = object_tracking.follow(
            frames,
            seed_at(4.0),
            detect=OracleDetector().detect,
            settings=settings(),
            tracker_factory=OracleTracker,
            deadline=0.0,
        )
        reader.close()
        assert result.capped == "time" and result.samples == []


class TestRun:
    @pytest.mark.asyncio
    async def test_a_frame_array_is_not_a_clip(self) -> None:
        outcome = await run_object_tracking(draw(0), [seed_at(1.0)], detector=OracleDetector())
        assert outcome.skipped and outcome.tracks == {}

    @pytest.mark.asyncio
    async def test_an_unreadable_clip_gives_an_item_without_a_track_and_never_raises(self, tmp_path: Path) -> None:
        outcome = await run(tmp_path / "missing.mp4", OracleDetector(), seed_at(1.0))
        assert outcome.tracks == {} and outcome.items["I1"]["samples"] == 0

    @pytest.mark.asyncio
    async def test_an_opencv_without_a_tracker_skips_the_run_with_the_reason(self, clip: Path) -> None:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(object_tracking, "cv2", SimpleNamespace())
            outcome = await run_object_tracking(clip, [seed_at(1.0)], detector=OracleDetector())
        assert outcome.skipped and "tracker" in outcome.skipped and outcome.tracks == {}


class TestTrackerSelection:
    def test_csrt_is_preferred_then_kcf_then_mil(self) -> None:
        legacy = SimpleNamespace(TrackerCSRT_create=lambda: "csrt-legacy", TrackerKCF_create=lambda: "kcf-legacy")
        fake = SimpleNamespace(legacy=legacy, TrackerMIL_create=lambda: "mil", TrackerKCF_create=lambda: "kcf")
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(object_tracking, "cv2", fake)
            name, factory = select_tracker()
            assert (name, factory()) == ("CSRT", "csrt-legacy")
            patch.setattr(
                object_tracking, "cv2", SimpleNamespace(TrackerKCF_create=lambda: "kcf", TrackerMIL_create=object)
            )
            assert select_tracker()[0] == "KCF"
            patch.setattr(object_tracking, "cv2", SimpleNamespace(TrackerMIL_create=lambda: "mil"))
            assert select_tracker()[0] == "MIL"
            patch.setattr(object_tracking, "cv2", SimpleNamespace())
            with pytest.raises(TrackerUnavailableError):
                select_tracker()

    def test_the_installed_opencv_has_a_tracker(self) -> None:
        name, factory = select_tracker()
        assert name in {"CSRT", "KCF", "MIL"} and callable(factory)


class TestFlagAndSettings:
    def test_the_flag_is_off_unless_truthy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(object_tracking.ENABLED_ENV_VAR, raising=False)
        assert object_tracking_enabled() is False
        for value, expected in (("1", True), ("true", True), ("ON", True), ("0", False), ("off", False), ("", False)):
            monkeypatch.setenv(object_tracking.ENABLED_ENV_VAR, value)
            assert object_tracking_enabled() is expected

    def test_settings_come_from_the_environment_with_defaults_for_bad_values(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(object_tracking.SAMPLE_FPS_ENV_VAR, "5")
        monkeypatch.setenv(object_tracking.MIN_IOU_ENV_VAR, "nonsense")
        monkeypatch.setenv(object_tracking.DETECTOR_CALLS_ENV_VAR, "12")
        config = ObjectTrackingSettings.from_env()
        assert config.sample_fps == 5.0 and config.reanchor_iou == object_tracking.DEFAULT_MIN_IOU
        assert config.max_detector_calls == 12
        assert config.detect_interval_seconds == object_tracking.DEFAULT_DETECT_INTERVAL_SECONDS


class TestRealTracker:
    """The tracker the installed OpenCV provides follows a textured square without the detector's help."""

    @pytest.mark.asyncio
    async def test_it_follows_a_moving_textured_square(self) -> None:
        rng = np.random.default_rng(0)
        background = rng.integers(0, 60, (HEIGHT, WIDTH, 3), dtype=np.uint8)
        patch = rng.integers(150, 256, (SIDE, SIDE, 3), dtype=np.uint8)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "textured.mp4"
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), float(FPS), (WIDTH, HEIGHT))
            for index in range(FPS * SECONDS):
                frame = background.copy()
                ymin, xmin, ymax, xmax = square_box(index)
                frame[ymin:ymax, xmin:xmax] = patch
                writer.write(frame)
            writer.release()
            detector = OracleDetector(blind=True)
            config = settings(
                sample_fps=10.0, detect_interval_seconds=60.0, span_seconds=2.0
            )  # no check: the tracker alone
            outcome = await run_object_tracking(path, [seed_at(3.0)], detector=detector, settings=config)
        assert outcome.tracker in {"CSRT", "KCF", "MIL"} and detector.calls == 0
        track = outcome.tracks["I1"]
        moved = [s for s in track.samples if s.score == -0.1]
        assert len(moved) >= 6
        overlaps = []
        for sample in moved:
            ymin, xmin, ymax, xmax = square_box(round(sample.t * FPS))
            overlaps.append(box_iou(sample.box, (ymin * 10, xmin * 5, ymax * 10, xmax * 5)))
        # MIL is the weakest OpenCV tracker (it lags a moving item, hence the detector's corrections), but it follows
        # MIL samples its updates at random, so a single frame can stray: the median is what must hold
        assert sorted(overlaps)[len(overlaps) // 2] > 0.3
        assert track.samples[-1].box[1] > track.samples[0].box[1] + 100
