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
from vss_agents.utils.track_evidence import best_moment
from vss_agents.utils.track_evidence import box_confirmed
from vss_agents.utils.track_evidence import build_tracks
from vss_agents.utils.track_evidence import cut_at_replay
from vss_agents.utils.track_evidence import is_hdr
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

    def test_the_per_run_anchor_puts_the_last_frame_at_the_end_of_the_clip(self):
        tracks = build_tracks(_frames("frames_8206.json"), CLIP_8206)
        last = max(track.last_seen for track in tracks)
        assert 93.0 < last <= 94.2  # the last person box is within the clip, at its end

    def test_the_anchor_is_clip_frames_minus_last_frame_id(self):
        clip = ClipInfo(100, 100, 10.0, 200, 20.0)
        frames = [_frame(100, _person("1")), _frame(190, _person("1"))]  # lost 10 frames = 1 s
        (track,) = build_tracks(frames, clip)
        assert [s.t for s in track.samples] == [11.0, 20.0]

    def test_the_fallback_is_2_85_seconds_when_the_stream_stopped_short_of_the_end(self):
        clip = ClipInfo(100, 100, 10.0, 2000, 200.0)
        frames = [_frame(100, _person("1")), _frame(500, _person("1"))]  # 1500 frames short of the end
        (track,) = build_tracks(frames, clip)
        assert [s.t for s in track.samples] == [12.85, 52.85]


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
        # ids 3 and 4 are the two men at 81 to 83 s; both have a sample at 82 s with different boxes
        a = min(tracks["3"].samples, key=lambda s: abs(s.t - 82.0))
        b = min(tracks["4"].samples, key=lambda s: abs(s.t - 82.0))
        assert abs(a.t - 82.0) < 0.5 and abs(b.t - 82.0) < 0.5 and a.box != b.box
        people = build_tracks(_frames("frames_9170.json"), CLIP_9170)
        at_23 = [t for t in people if any(abs(s.t - 23.0) < 0.3 for s in t.samples)]
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

    def test_best_moment_prefers_a_real_sample_over_a_larger_tracker_only_box(self):
        track = Track("1", [Sample(1.0, (0, 0, 400, 400), 0.5), Sample(2.0, (0, 0, 800, 800), -0.1)])
        assert best_moment(track).t == 1.0


class TestBestMoment:
    def test_the_largest_box_among_confident_samples_wins_over_a_larger_tracked_only_box(self):
        track = Track(
            "1",
            [
                Sample(1.0, (0, 0, 400, 400), 0.9),
                Sample(2.0, (0, 0, 800, 800), -0.1),
                Sample(3.0, (0, 0, 500, 500), 0.86),
            ],
        )
        assert best_moment(track).t == 3.0

    def test_without_a_confident_sample_the_largest_box_is_used(self):
        track = Track("1", [Sample(1.0, (0, 0, 400, 400), -0.1), Sample(2.0, (0, 0, 600, 600), 0.5)])
        assert best_moment(track).t == 2.0

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
