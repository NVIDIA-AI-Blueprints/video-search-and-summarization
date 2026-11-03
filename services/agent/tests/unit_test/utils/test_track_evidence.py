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

"""Unit tests for person tracks: time base, grouping, sampling, association and the run lifecycle."""

import json
from pathlib import Path

import pytest

from vss_agents.utils.perception_client import FakePerceptionClient
from vss_agents.utils.perception_client import Frame
from vss_agents.utils.perception_client import FrameObject
from vss_agents.utils.perception_client import PerceptionError
from vss_agents.utils.perception_client import parse_frame
from vss_agents.utils.track_evidence import ClipInfo
from vss_agents.utils.track_evidence import Sample
from vss_agents.utils.track_evidence import Track
from vss_agents.utils.track_evidence import TrackingSettings
from vss_agents.utils.track_evidence import TrackingUnavailableError
from vss_agents.utils.track_evidence import associate_tracks
from vss_agents.utils.track_evidence import box_confirmed
from vss_agents.utils.track_evidence import build_tracks
from vss_agents.utils.track_evidence import cut_at_replay
from vss_agents.utils.track_evidence import first_seen_sample
from vss_agents.utils.track_evidence import is_hdr
from vss_agents.utils.track_evidence import is_real
from vss_agents.utils.track_evidence import is_solid
from vss_agents.utils.track_evidence import nearest_sample
from vss_agents.utils.track_evidence import parse_probe
from vss_agents.utils.track_evidence import run_tracking
from vss_agents.utils.track_evidence import sensor_id_for
from vss_agents.utils.track_evidence import track_json
from vss_agents.utils.track_evidence import tracking_enabled
from vss_agents.utils.track_evidence import visible_samples

FIXTURES = Path(__file__).parent / "fixtures" / "perception"
CLIP_8206 = ClipInfo(width=854, height=480, fps=29.97, frames=2823, duration_seconds=94.2)
CLIP_REV116 = ClipInfo(width=1388, height=752, fps=30.0, frames=759, duration_seconds=25.3)
CLIP_REV119 = ClipInfo(width=320, height=240, fps=30.0, frames=912, duration_seconds=30.4)
CLIP_REV124 = ClipInfo(width=320, height=240, fps=30.0, frames=1767, duration_seconds=58.9)
CLIP_9170 = ClipInfo(
    width=270,
    height=480,
    fps=29.97,
    frames=715,
    duration_seconds=23.9,
    color_primaries="bt2020",
    color_transfer="arib-std-b67",
)


def _frames(name: str) -> list[Frame]:
    raw = json.loads((FIXTURES / name).read_text())["frames"]
    return sorted((parse_frame(r) for r in raw), key=lambda f: f.timestamp)


def _frame(frame_id: int, *objects: FrameObject) -> Frame:
    return Frame(frame_id, f"2026-10-31T08:00:00.{frame_id % 1000:03d}Z", tuple(objects))


def _person(object_id: str, box=(10.0, 20.0, 110.0, 220.0), confidence=0.9, type_="Person") -> FrameObject:
    return FrameObject(object_id, type_, confidence, box)


class TestTimeBaseAndCut:
    def test_the_read_is_cut_at_the_first_frame_id_decrease(self):
        frames = _frames("frames_8206.json")
        assert frames[-1].frame_id < max(f.frame_id for f in frames)  # the fixture ends in the replay pass
        kept = cut_at_replay(frames)
        assert kept[-1].frame_id == 2737
        assert [f.frame_id for f in kept] == sorted(f.frame_id for f in kept)

    def test_a_frame_id_is_read_as_its_own_second_with_no_start_loss(self):
        clip = ClipInfo(100, 100, 10.0, 200, 20.0)
        frames = [_frame(100, _person("1")), _frame(190, _person("1"))]
        (track,) = build_tracks(frames, clip)
        assert [s.t for s in track.samples] == [10.0, 19.0]

    def test_a_stream_that_stopped_short_of_the_end_is_not_shifted(self):
        clip = ClipInfo(100, 100, 10.0, 2000, 200.0)
        frames = [_frame(100, _person("1")), _frame(500, _person("1"))]  # 1500 frames short of the end
        (track,) = build_tracks(frames, clip)
        assert [s.t for s in track.samples] == [10.0, 50.0]

    def test_the_last_person_box_of_a_recorded_clip_is_at_its_own_frame_second(self):
        tracks = build_tracks(_frames("frames_8206.json"), CLIP_8206)
        last = max(track.last_seen for track in tracks)
        assert last == pytest.approx(2737 / 29.97, abs=0.2)  # the last frame read, not the end of the clip

    def test_the_recorded_clips_are_not_shifted_by_the_unread_tail(self):
        """Revisions 116, 119 and 124 of the person-box diagnosis: the stream stopped 98, 111 and 136 frames short."""
        for name, clip, last_id in (
            ("frames_rev116.json", CLIP_REV116, 661),
            ("frames_rev119.json", CLIP_REV119, 801),
            ("frames_rev124.json", CLIP_REV124, 1631),
        ):
            tracks = build_tracks(_frames(name), clip)
            assert tracks
            assert min(track.first_seen for track in tracks) == pytest.approx(3 / clip.fps, abs=0.1)
            assert max(track.last_seen for track in tracks) <= last_id / clip.fps  # nothing later than the last frame
            for track in tracks:
                for sample in track.samples:
                    assert sample.t * clip.fps == pytest.approx(round(sample.t * clip.fps), abs=0.02)  # id / fps

    def test_a_moving_person_keeps_the_box_the_locate_step_found_at_the_same_second(self):
        """124 E1 and E2 are located boxes no fallback touched: the track sample of the same second overlaps them."""
        tracks = build_tracks(_frames("frames_rev124.json"), CLIP_REV124)
        assert box_confirmed([241, 616, 567, 725], 54.0, tracks, 0.3)  # 54.0 s, IoU 0.87 (0.29 with the old +2.85 s)
        assert box_confirmed([264, 536, 710, 685], 50.85, tracks, 0.3)

    def test_the_first_seen_sample_of_a_person_is_the_second_they_enter(self):
        """119 E3 was first seen at 3.65 s under the old time base, which carried +2.85 s; it is 0.8 s now."""
        tracks = {t.track_id: t for t in build_tracks(_frames("frames_rev119.json"), CLIP_REV119)}
        assert first_seen_sample(tracks["9"]).t == pytest.approx(0.8, abs=0.05)
        assert first_seen_sample(tracks["12"]).t == pytest.approx(22.133, abs=0.05)

    def test_a_real_detection_wins_its_bucket_over_a_tracker_only_box(self):
        clip = ClipInfo(100, 100, 30.0, 300, 10.0)
        frames = [
            _frame(60, _person("1", confidence=-0.1)),
            _frame(61, _person("1", box=(30.0, 30.0, 90.0, 90.0), confidence=0.9)),  # same 200 ms bucket
            _frame(62, _person("1", confidence=-0.1)),
        ]
        (track,) = build_tracks(frames, clip)
        assert [(s.t, s.score) for s in track.samples] == [(round(61 / 30, 3), 0.9)]

    def test_is_solid_does_not_depend_on_the_frame_parity(self):
        """A detector that publishes every second frame is solid whichever parity the ids have."""
        clip = ClipInfo(100, 100, 30.0, 300, 10.0)
        for parity in (0, 1):
            frames = [_frame(i, _person("1", confidence=0.9 if i % 2 == parity else -0.1)) for i in range(30, 90)]
            (track,) = build_tracks(frames, clip)
            assert is_solid(track)
            assert all(is_real(sample) for sample in track.samples)


class TestGroupingAndSampling:
    def test_only_people_are_kept_and_boxes_are_normalised_top_left_bottom_right(self):
        clip = ClipInfo(width=200, height=100, fps=10.0, frames=100, duration_seconds=10.0)
        frames = [_frame(95, _person("7", (20.0, 10.0, 120.0, 60.0)), _person("8", type_="Forklift"))]
        (track,) = build_tracks(frames, clip)
        assert track.track_id == "7"
        # leftX 20, topY 10, rightX 120, bottomY 60 on a 200 x 100 clip
        assert track.samples[0].box == (100, 100, 600, 600)

    def test_a_box_outside_the_frame_is_clamped(self):
        clip = ClipInfo(100, 100, 10.0, 100, 10.0)
        (track,) = build_tracks([_frame(95, _person("1", (-5.0, -5.0, 120.0, 130.0)))], clip)
        assert track.samples[0].box == (0, 0, 1000, 1000)

    def test_samples_with_score_minus_0_1_are_kept(self):
        clip = ClipInfo(100, 100, 10.0, 100, 10.0)
        frames = [_frame(10, _person("1", confidence=-0.1)), _frame(30, _person("1", confidence=0.9))]
        (track,) = build_tracks(frames, clip)
        assert [s.score for s in track.samples] == [-0.1, 0.9]

    def test_at_most_five_samples_a_second_the_first_of_each_200_ms_bucket(self):
        clip = ClipInfo(100, 100, 30.0, 300, 10.0)
        frames = [_frame(i, _person("1")) for i in range(100, 130)]  # one second of 30 fps
        (track,) = build_tracks(frames, clip)
        assert len(track.samples) <= 6  # the second straddles at most six buckets
        buckets = [int(s.t * 5) for s in track.samples]
        assert len(set(buckets)) == len(buckets)  # one sample per 200 ms bucket

    def test_the_recorded_clips_keep_the_two_men_and_the_three_people_as_separate_tracks(self):
        tracks = {t.track_id: t for t in build_tracks(_frames("frames_8206.json"), CLIP_8206)}
        # ids 3 and 4 are the two men at 78 to 80 s (frame id / fps); both have a sample at 82 s with different boxes
        a = min(tracks["3"].samples, key=lambda s: abs(s.t - 79.2))
        b = min(tracks["4"].samples, key=lambda s: abs(s.t - 79.2))
        assert abs(a.t - 79.2) < 0.5 and abs(b.t - 79.2) < 0.5 and a.box != b.box
        people = build_tracks(_frames("frames_9170.json"), CLIP_9170)
        at_23 = [t for t in people if any(abs(s.t - 20.0) < 0.3 for s in t.samples)]
        assert len(at_23) == 3


class TestAssociation:
    @staticmethod
    def _track(track_id: str, box, t: float = 10.0, score: float = 0.9) -> Track:
        """A solid track: three real detections ending at ``t``."""
        return Track(track_id, [Sample(t - 0.4, box, score), Sample(t - 0.2, box, score), Sample(t, box, score)])

    @staticmethod
    def _ghost(box, t: float = 5.0) -> Track:
        """The IMG_4569 shape: one real detection at ``t``, then tracker-only boxes drifting away from it."""
        drift = [Sample(t + 0.2 * i, (box[0], box[1] - 40 * i, box[2], box[3] - 40 * i), -0.1) for i in range(1, 30)]
        return Track("ghost", [Sample(t, box, 0.6), *drift])

    def test_the_entity_matches_the_track_it_overlaps(self):
        near = self._track("1", (100, 100, 500, 400))
        far = self._track("2", (600, 600, 900, 900))
        matched = associate_tracks([("E1", (110, 110, 500, 400), 10.0)], [far, near], 0.3)
        assert matched == {"E1": near}

    def test_no_match_below_the_iou_threshold_or_outside_half_a_second(self):
        track = self._track("1", (100, 100, 500, 400))
        assert associate_tracks([("E1", (450, 350, 900, 900), 10.0)], [track], 0.3) == {}
        assert associate_tracks([("E1", (100, 100, 500, 400), 10.6)], [track], 0.3) == {}
        assert associate_tracks([("E1", (100, 100, 500, 400), 10.4)], [track], 0.3) == {"E1": track}

    def test_two_entities_on_one_track_the_better_overlap_wins_and_the_other_has_none(self):
        track = self._track("1", (100, 100, 500, 400))
        matched = associate_tracks(
            [("E1", (150, 150, 500, 400), 10.0), ("E2", (100, 100, 500, 400), 10.0)], [track], 0.3
        )
        assert matched == {"E2": track}

    def test_each_entity_takes_its_own_track(self):
        left = self._track("1", (100, 50, 500, 300))
        right = self._track("2", (100, 600, 500, 900))
        matched = associate_tracks(
            [("E1", (100, 600, 500, 900), 10.0), ("E2", (100, 50, 500, 300), 10.0)], [left, right], 0.3
        )
        assert matched == {"E1": right, "E2": left}


class TestGhostTracks:
    PANEL = (15, 237, 492, 381)  # the model's box on the lift panel

    def test_a_track_with_one_real_detection_is_not_solid(self):
        assert not is_solid(TestAssociation._ghost(self.PANEL))
        assert not is_solid(Track("1", [Sample(1.0, self.PANEL, 0.9)]))

    def test_a_track_that_is_mostly_tracker_only_is_not_solid(self):
        samples = [Sample(1.0, self.PANEL, 0.9), Sample(1.2, self.PANEL, 0.9)]
        samples += [Sample(1.4 + 0.2 * i, self.PANEL, -0.1) for i in range(5)]
        assert not is_solid(Track("1", samples))
        samples += [Sample(2.5 + 0.2 * i, self.PANEL, 0.9) for i in range(5)]
        assert is_solid(Track("1", samples))

    def test_the_entity_box_on_the_ghosts_one_detection_matches_nothing(self):
        ghost = TestAssociation._ghost(self.PANEL)
        assert associate_tracks([("E1", self.PANEL, 5.038)], [ghost], 0.3) == {}
        # the same box on a solid track still matches, so the ghost is the only thing that changed
        solid = TestAssociation._track("1", self.PANEL, t=5.0)
        assert associate_tracks([("E1", self.PANEL, 5.038)], [ghost, solid], 0.3) == {"E1": solid}

    def test_tracker_only_boxes_far_from_the_last_detection_are_drift_not_visible(self):
        ghost = TestAssociation._ghost(self.PANEL)
        seen = visible_samples(ghost)
        assert seen[0].t == 5.0 and max(sample.t for sample in seen) <= 6.0
        assert len(seen) < len(ghost.samples)
        assert visible_samples(Track("1", [Sample(1.0, self.PANEL, -0.1)])) == []


class TestFirstSeenSample:
    def test_the_first_sample_wins_over_a_larger_or_more_confident_later_one(self):
        track = Track(
            "1",
            [
                Sample(1.0, (0, 0, 400, 400), 0.5),
                Sample(2.0, (0, 0, 800, 800), 0.95),
                Sample(3.0, (0, 0, 500, 500), 0.86),
            ],
        )
        assert first_seen_sample(track).t == 1.0

    def test_a_tracker_only_first_sample_is_still_the_first_seen(self):
        track = Track("1", [Sample(1.0, (0, 0, 400, 400), -0.1), Sample(2.0, (0, 0, 600, 600), 0.5)])
        assert first_seen_sample(track).t == 1.0

    def test_the_stored_track_is_a_list_of_t_box_score(self):
        track = Track("1", [Sample(1.5, (1, 2, 3, 4), 0.9)])
        assert json.loads(track_json(track)) == [{"t": 1.5, "box": [1, 2, 3, 4], "score": 0.9}]


class TestProbeAndHdr:
    def test_probe_output_becomes_clip_info(self):
        info = parse_probe(
            {
                "streams": [
                    {
                        "width": 854,
                        "height": 480,
                        "r_frame_rate": "30000/1001",
                        "nb_frames": "2823",
                        "duration": "94.194",
                        "color_primaries": "bt709",
                    }
                ]
            }
        )
        assert (info.width, info.height, info.frames) == (854, 480, 2823)
        assert info.fps == pytest.approx(29.97, abs=0.001)

    def test_frames_fall_back_to_duration_times_fps(self):
        info = parse_probe(
            {"streams": [{"width": 10, "height": 10, "r_frame_rate": "30/1"}], "format": {"duration": "2.0"}}
        )
        assert info.frames == 60

    def test_a_clip_without_a_video_stream_is_unavailable(self):
        with pytest.raises(TrackingUnavailableError):
            parse_probe({"streams": []})

    @pytest.mark.parametrize(
        ("primaries", "transfer", "hdr"),
        [
            ("bt2020", "arib-std-b67", True),
            ("bt2020", "bt709", True),
            ("bt709", "smpte2084", True),
            ("bt709", "arib-std-b67", True),
            ("bt709", "bt709", False),
            ("", "", False),
        ],
    )
    def test_hdr_is_bt2020_primaries_or_an_hlg_or_pq_transfer(self, primaries, transfer, hdr):
        assert is_hdr(ClipInfo(1, 1, 1.0, 1, 1.0, primaries, transfer)) is hdr


class FakeTime:
    """A clock that only moves when the run sleeps, so timeouts take no real time."""

    def __init__(self) -> None:
        self.now = 0.0

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += max(seconds, 0.5)


def _pass_one(name: str = "frames_8206.json") -> list[dict]:
    frames = json.loads((FIXTURES / name).read_text())["frames"]
    ordered = sorted(frames, key=lambda f: f["timestamp"])
    cut = []
    for f in ordered:
        if cut and int(f["id"]) < int(cut[-1]["id"]):
            break
        cut.append(f)
    return cut


class TestRunLifecycle:
    def _run(self, client, clip=CLIP_8206, transcode=None, **settings):
        time = FakeTime()
        calls = {"transcode": 0}

        async def probe(path: Path) -> ClipInfo:
            return clip

        async def default_transcode(source: Path, target: Path) -> Path:
            calls["transcode"] += 1
            target.write_bytes(b"sdr")
            return target

        return (
            time,
            calls,
            run_tracking(
                self.video,
                sensor_id="incident-abc-run1",
                client=client,
                settings=TrackingSettings(**settings),
                probe=probe,
                transcode=transcode or default_transcode,
                sleep=time.sleep,
                clock=time.clock,
            ),
        )

    @pytest.fixture(autouse=True)
    def _video(self, tmp_path: Path):
        self.video = tmp_path / "clip.mp4"
        self.video.write_bytes(b"video")

    @pytest.mark.asyncio
    async def test_a_run_uploads_adds_reads_the_tracks_then_removes_and_deletes(self):
        client = FakePerceptionClient([_pass_one()])
        _, calls, run = self._run(client)
        outcome = await run
        assert outcome.skipped is None and len(outcome.tracks) > 0
        assert client.calls[:3] == ["upload", "stream_url", "add_stream"]
        assert client.removed == [("incident-abc-run1", "rtsp://fake/incident-abc-run1")]
        assert client.deleted == ["incident-abc-run1"]
        assert calls["transcode"] == 0  # an SDR clip is pushed as it is
        assert client.uploaded["incident-abc-run1"] == self.video

    @pytest.mark.asyncio
    async def test_an_hdr_clip_is_transcoded_and_the_sdr_copy_is_what_is_pushed(self):
        client = FakePerceptionClient([_pass_one("frames_9170.json")])
        _, calls, run = self._run(client, clip=CLIP_9170)
        outcome = await run
        assert calls["transcode"] == 1
        assert client.uploaded["incident-abc-run1"].name == "sdr.mp4"
        assert outcome.skipped is None and outcome.tracks

    @pytest.mark.asyncio
    async def test_the_replay_pass_is_ignored_when_the_run_waits_for_the_margin(self):
        client = FakePerceptionClient([json.loads((FIXTURES / "frames_8206.json").read_text())])
        _, _, run = self._run(client)
        outcome = await run
        assert outcome.skipped is None
        assert max(t.last_seen for t in outcome.tracks) <= 94.2

    @pytest.mark.asyncio
    async def test_a_stream_short_of_the_end_is_read_until_the_deadline_not_the_100_frame_margin(self):
        # 119: the last frame with a person is id 801 of 912; the old 100-frame margin stopped the read there.
        client = FakePerceptionClient([_pass_one("frames_rev119.json")])
        time, _, run = self._run(client, clip=CLIP_REV119, end_margin_seconds=20.0)
        outcome = await run
        assert time.now > CLIP_REV119.duration_seconds + 20.0  # waited to the deadline
        assert outcome.end_reached is False and outcome.clip_frames == 912
        assert outcome.last_frame_id == 801 and outcome.frames > 0
        assert max(t.last_seen for t in outcome.tracks) == pytest.approx(801 / 30.0, abs=0.2)

    @pytest.mark.asyncio
    async def test_a_stream_that_reaches_the_last_frame_ends_the_read_early(self):
        clip = ClipInfo(width=320, height=240, fps=30.0, frames=803, duration_seconds=26.8)
        client = FakePerceptionClient([_pass_one("frames_rev119.json")])
        time, _, run = self._run(client, clip=clip, end_margin_seconds=20.0)
        outcome = await run
        assert outcome.end_reached is True and time.now < clip.duration_seconds

    @pytest.mark.asyncio
    async def test_a_replay_of_the_file_means_the_end_was_reached(self):
        class Replaying(FakePerceptionClient):
            latest = iter([1000, 2737, 5])  # the counter resets when DeepStream replays the file

            async def latest_frame_id(self, sensor_id: str) -> int | None:
                return next(self.latest)

        client = Replaying([json.loads((FIXTURES / "frames_8206.json").read_text())])
        time, _, run = self._run(client)
        outcome = await run
        assert outcome.skipped is None and outcome.last_frame_id == 2737
        assert outcome.end_reached is True and time.now < CLIP_8206.duration_seconds

    @pytest.mark.asyncio
    async def test_no_frames_within_40_seconds_skips_and_still_removes_and_deletes(self):
        client = FakePerceptionClient([])
        _, _, run = self._run(client)
        outcome = await run
        assert outcome.skipped == "no frames from perception"
        assert len(client.removed) == 1 and client.deleted == ["incident-abc-run1"]

    @pytest.mark.asyncio
    async def test_services_down_is_soft_and_nothing_is_left_to_remove(self):
        client = FakePerceptionClient(fail={"upload": PerceptionError("POST /api/v1/storage/file: ConnectError")})
        _, _, run = self._run(client)
        outcome = await run
        assert outcome.skipped == "perception failed: PerceptionError"
        assert client.removed == [] and client.deleted == []

    @pytest.mark.asyncio
    async def test_a_failure_after_the_add_still_removes_the_stream_and_deletes_the_file(self):
        client = FakePerceptionClient([_pass_one()], fail={"frames": PerceptionError("boom")})
        _, _, run = self._run(client)
        outcome = await run
        assert outcome.skipped == "perception failed: PerceptionError"
        assert len(client.removed) == 1 and client.deleted == ["incident-abc-run1"]

    @pytest.mark.asyncio
    async def test_a_failing_remove_does_not_stop_the_file_delete(self):
        client = FakePerceptionClient([_pass_one()], fail={"remove_stream": PerceptionError("500")})
        _, _, run = self._run(client)
        outcome = await run
        assert outcome.skipped is None
        assert client.deleted == ["incident-abc-run1"]

    @pytest.mark.asyncio
    async def test_a_failed_add_still_tries_the_remove(self):
        client = FakePerceptionClient(fail={"add_stream": PerceptionError("400")})
        _, _, run = self._run(client)
        outcome = await run
        assert outcome.skipped is not None
        assert len(client.removed) == 1 and client.deleted == ["incident-abc-run1"]

    @pytest.mark.asyncio
    async def test_a_missing_ffprobe_skips_without_touching_the_stack(self):
        client = FakePerceptionClient([_pass_one()])
        time = FakeTime()

        async def probe(path: Path) -> ClipInfo:
            raise TrackingUnavailableError("ffprobe not found on the agent host")

        outcome = await run_tracking(
            self.video,
            sensor_id="s",
            client=client,
            settings=TrackingSettings(),
            probe=probe,
            sleep=time.sleep,
            clock=time.clock,
        )
        assert outcome.skipped == "ffprobe not found on the agent host"
        assert client.calls == []

    @pytest.mark.asyncio
    async def test_an_unreadable_clip_is_a_skip_not_an_exception(self, tmp_path: Path):
        outcome = await run_tracking(tmp_path / "missing.mp4", sensor_id="s", client=FakePerceptionClient())
        assert outcome.skipped == "the clip is not a readable file"


class TestSettings:
    def test_tracking_is_off_unless_the_flag_is_truthy(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("INCIDENT_TRACKING_ENABLED", raising=False)
        assert tracking_enabled() is False
        for value in ("1", "true", "YES", "on"):
            monkeypatch.setenv("INCIDENT_TRACKING_ENABLED", value)
            assert tracking_enabled() is True
        monkeypatch.setenv("INCIDENT_TRACKING_ENABLED", "0")
        assert tracking_enabled() is False

    def test_thresholds_default_and_read_the_environment(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("INCIDENT_TRACKING_MIN_IOU", raising=False)
        monkeypatch.delenv("INCIDENT_TRACKING_END_MARGIN_SECONDS", raising=False)
        assert TrackingSettings.from_env().min_iou == 0.3 and TrackingSettings.from_env().end_margin_seconds == 20.0
        monkeypatch.setenv("INCIDENT_TRACKING_MIN_IOU", "0.5")
        monkeypatch.setenv("INCIDENT_TRACKING_END_MARGIN_SECONDS", "nonsense")
        assert TrackingSettings.from_env().min_iou == 0.5 and TrackingSettings.from_env().end_margin_seconds == 20.0

    def test_the_sensor_id_is_unique_per_run_and_safe(self):
        a = sensor_id_for("uploads/clip.mp4", "run 1/../x")
        assert a.startswith("incident-") and "/" not in a and " " not in a
        assert a != sensor_id_for("uploads/clip.mp4", "run-2")
        assert a != sensor_id_for("uploads/other.mp4", "run 1/../x")


class TestConfirmation:
    TRACK = Track("2", [Sample(81.0, (233, 417, 448, 482), 0.9), Sample(81.2, (233, 417, 448, 482), 0.9)])
    GHOST = Track("9", [Sample(81.0, (233, 417, 448, 482), 0.9)])

    def test_a_box_is_confirmed_by_a_solid_track_sample_within_the_window(self) -> None:
        assert box_confirmed((271, 385, 494, 460), 81.0, [self.TRACK], 0.3)
        assert not box_confirmed((115, 488, 229, 523), 81.0, [self.TRACK], 0.3), "the TV box"
        assert not box_confirmed((271, 385, 494, 460), 79.0, [self.TRACK], 0.3), "no sample within 0.5 s"
        assert not box_confirmed((271, 385, 494, 460), 81.0, [self.GHOST], 0.3), "a ghost confirms nothing"

    def test_the_nearest_sample_stays_in_the_span_and_skips_excluded_tracks(self) -> None:
        other = Track("3", [Sample(81.75, (400, 100, 700, 300), 0.9), Sample(81.95, (400, 100, 700, 300), 0.9)])
        found = nearest_sample([self.TRACK, other, self.GHOST], 80.0, 80, 92)
        assert found is not None and found[0].track_id == "2" and found[1].t == 81.0
        found = nearest_sample([self.TRACK, other], 80.0, 80, 92, exclude={"2"})
        assert found is not None and found[0].track_id == "3"
        assert nearest_sample([self.TRACK], 80.0, 80, 80.5) is None
