# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Clip ownership, source timing, and mocked subprocess orchestration.

Default materialization is stream copy, with complete GOPs covering the request.
ffprobe establishes keyframe bounds and verifies the result; source time is
normalized to clip-local zero. Unknown or unverifiable bounds are errors rather
than falsely exact metadata. Artifact cleanup is synchronous and idempotent.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest


def artifact(processing: Any, path: Path, *, owned: bool = True) -> Any:
    # Public construction is borrowed. This private test seam models the
    # ownership that only successful materialize_clip grants in production.
    constructor = (
        processing.ClipArtifact._from_owned_path if owned else processing.ClipArtifact
    )
    return constructor(
        path=path,
        source_id="stable-video",
        requested_start_ns=3_000_000_000,
        requested_end_ns=5_000_000_000,
        actual_start_ns=2_000_000_000,
        actual_end_ns=6_000_000_000,
        pts_offset_ns=2_000_000_000,
        duration_s=4.0,
        container="mp4",
        audio=True,
    )


def test_clip_exposes_requested_actual_and_source_time_mapping(
    processing: Any,
    tmp_path: Path,
) -> None:
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"clip")
    clip = artifact(processing, path)
    assert clip.actual_start_ns <= clip.requested_start_ns
    assert clip.actual_end_ns >= clip.requested_end_ns
    assert clip.pts_offset_ns + 1_000_000_000 == 3_000_000_000
    assert clip.source_id == "stable-video"
    clip.cleanup()
    clip.cleanup()
    assert not path.exists()


def test_clip_context_manager_cleans_up_after_exception(
    processing: Any,
    tmp_path: Path,
) -> None:
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"clip")
    with pytest.raises(RuntimeError, match="consumer failed"):
        with artifact(processing, path) as clip:
            assert clip.path == path
            raise RuntimeError("consumer failed")
    assert not path.exists()


def test_cleanup_only_removes_owned_clip(processing: Any, tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"original source")
    output = tmp_path / "clip.mp4"
    output.write_bytes(b"temporary clip")
    artifact(processing, output).cleanup()
    assert source.read_bytes() == b"original source"


def test_publicly_constructed_artifact_cannot_delete_borrowed_source(
    processing: Any, tmp_path: Path
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"caller-owned video")
    borrowed = artifact(processing, source, owned=False)
    with borrowed:
        assert borrowed.path == source
    borrowed.cleanup()
    borrowed.cleanup()
    assert source.read_bytes() == b"caller-owned video"


class FakeProcess:
    def __init__(
        self,
        payload: dict[str, Any],
        *,
        code: int = 0,
        block: bool = False,
        ignore_terminate: bool = False,
    ) -> None:
        self.payload = payload
        self.returncode: int | None = None if block else code
        self.block = block
        self.ignore_terminate = ignore_terminate
        self.terminated = False
        self.killed = False
        self.reaped = False
        self.released = asyncio.Event()

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        if self.block:
            await self.released.wait()
        return json.dumps(self.payload).encode(), b"synthetic process diagnostic"

    def terminate(self) -> None:
        self.terminated = True
        if self.ignore_terminate:
            return
        self.returncode = -15
        self.released.set()

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self.released.set()

    async def wait(self) -> int:
        if self.block and self.returncode is None:
            await self.released.wait()
        self.reaped = True
        return self.returncode or 0


@pytest.fixture
def processes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    import shutil
    import tempfile

    monkeypatch.setattr(shutil, "which", lambda name: f"/external/bin/{name}")
    # Keep every candidate implementation's temporary files inside this test.
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    commands: list[tuple[str, ...]] = []
    instances: list[FakeProcess] = []
    state: dict[str, Any] = {
        "fail": False,
        "block": False,
        "ignore_terminate": False,
        "source_origin": 0.0,
        "output_origin": 0.0,
        "audio": True,
        "drop_audio_output": False,
        "output_fault": None,
        "repeated_hashes": False,
    }
    started = asyncio.Event()

    async def spawn(*args: Any, **kwargs: Any) -> FakeProcess:
        command = tuple(str(arg) for arg in args)
        commands.append(command)
        if "ffmpeg" in Path(command[0]).name:
            output = Path(command[-1])
            output.write_bytes(b"synthetic temporary clip")
            process = FakeProcess(
                {},
                code=1 if state["fail"] else 0,
                block=state["block"],
                ignore_terminate=state["ignore_terminate"],
            )
            instances.append(process)
            started.set()
            return process
        # ffprobe receives the input as its final positional argument. Return
        # both packet and decoded-frame timing so either verification method is
        # supported without prescribing the exact subprocess command spelling.
        is_output = Path(command[-1]).read_bytes() == b"synthetic temporary clip"
        duration = 4 if is_output else 10
        origin = state["output_origin"] if is_output else state["source_origin"]
        source_indices = list(range(10, 30)) if is_output else list(range(50))
        if is_output and state["output_fault"] == "short_coverage":
            source_indices = list(range(10, 20))
            duration = 2
        packets = [
            {
                "stream_index": 0,
                "pts_time": f"{origin + position / 5:.3f}",
                "dts_time": f"{origin + position / 5:.3f}",
                "duration_time": "0.2",
                "flags": "K_" if source_index % 10 == 0 else "__",
                "data_hash": (
                    f"SHA256:{source_index % 10:064x}"
                    if state["repeated_hashes"]
                    else f"SHA256:{source_index:064x}"
                ),
            }
            for position, source_index in enumerate(source_indices)
        ]
        if is_output and state["output_fault"] == "packet_identity":
            for packet in packets:
                packet["data_hash"] = "SHA256:" + "f" * 64
        if is_output and state["output_fault"] == "relative_timing":
            packets[3]["pts_time"] = f"{origin + 0.7:.3f}"
        if is_output and state["output_fault"] == "missing_timing":
            for packet in packets:
                packet.pop("pts_time")
                packet.pop("dts_time")
        frames = [
            {
                "media_type": "video",
                "stream_index": 0,
                "key_frame": int(packet["flags"] == "K_"),
                **(
                    {
                        "best_effort_timestamp_time": packet["pts_time"],
                        "pts_time": packet["pts_time"],
                    }
                    if "pts_time" in packet
                    else {}
                ),
                "pkt_duration_time": "0.2",
            }
            for packet in packets
        ]
        payload = {
            "format": {
                "duration": str(duration),
                # Container/audio clock deliberately differs from video PTS.
                "start_time": str(origin - 0.1),
                "format_name": "mov,mp4",
            },
            "streams": [
                {
                    "index": 0,
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 3,
                    "height": 2,
                    "avg_frame_rate": "5/1",
                    "r_frame_rate": "5/1",
                    "time_base": "1/1000",
                    "start_time": str(origin),
                    "duration": str(duration),
                    "nb_frames": str(duration * 5),
                },
                *(
                    [{"index": 1, "codec_type": "audio", "codec_name": "aac"}]
                    if state["audio"] and not (is_output and state["drop_audio_output"])
                    else []
                ),
            ],
            "frames": frames,
            "packets": packets
            + (
                [
                    {
                        "stream_index": 1,
                        "pts_time": "2.0",
                        "duration_time": "0.2",
                        "data_hash": "SHA256:" + "a" * 64,
                    }
                ]
                if state["audio"] and not (is_output and state["drop_audio_output"])
                else []
            ),
        }
        process = FakeProcess(
            payload,
            code=1 if is_output and state["output_fault"] == "probe_failure" else 0,
        )
        instances.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    return commands, instances, state, started


async def test_materialization_uses_external_copy_and_reports_expanded_bounds(
    processing: Any,
    video_path: Path,
    processes: Any,
) -> None:
    commands, _, _, _ = processes
    clip = await processing.materialize_clip(video_path, 3, 5, source_id="stable-video")
    try:
        assert clip.path.exists()
        assert clip.source_id == "stable-video"
        assert clip.requested_start_ns == 3_000_000_000
        assert clip.requested_end_ns == 5_000_000_000
        assert clip.actual_start_ns == 2_000_000_000
        assert clip.actual_end_ns == 6_000_000_000
        assert clip.pts_offset_ns == 2_000_000_000
        assert clip.audio is True
        copy_commands = [cmd for cmd in commands if "ffmpeg" in Path(cmd[0]).name]
        assert len(copy_commands) == 1
        assert "copy" in copy_commands[0]
        assert str(video_path) in copy_commands[0]
    finally:
        clip.cleanup()
    assert video_path.exists()


async def test_ambiguous_repeated_video_packets_cannot_establish_source_offset(
    processing: Any, video_path: Path, processes: Any, tmp_path: Path
) -> None:
    # Identical encoded GOPs can repeat at different source times. Matching the
    # first hash sequence would falsely identify the mock's 2..6s output as
    # 0..4s and accept a request for 0.5..1.5s. Requested seek alone is no proof.
    _, _, state, _ = processes
    state["repeated_hashes"] = True
    with pytest.raises(processing.ClipError):
        await processing.materialize_clip(video_path, 0.5, 1.5)
    assert list(tmp_path.rglob("*.mp4")) == [video_path]


@pytest.mark.parametrize(
    "start,end",
    [
        (-1, 2),
        (2, 2),
        (3, 2),
        (0, float("inf")),
        (float("nan"), 1),
        (0, 11),
    ],
)
async def test_invalid_clip_bounds_do_not_create_output(
    processing: Any,
    video_path: Path,
    processes: Any,
    start: float,
    end: float,
) -> None:
    commands, _, _, _ = processes
    with pytest.raises(ValueError):
        await processing.materialize_clip(video_path, start, end)
    assert not any("ffmpeg" in Path(cmd[0]).name for cmd in commands)


async def test_missing_external_tools_is_actionable(
    processing: Any,
    video_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _: None)
    with pytest.raises(processing.ClipError, match="ffmpeg|ffprobe"):
        await processing.materialize_clip(video_path, 0.1, 0.5)


async def test_failed_copy_removes_partial_output(
    processing: Any,
    video_path: Path,
    processes: Any,
    tmp_path: Path,
) -> None:
    _, _, state, _ = processes
    state["fail"] = True
    with pytest.raises(processing.ClipError):
        await processing.materialize_clip(video_path, 3, 5)
    assert list(tmp_path.rglob("*.mp4")) == [video_path]


async def test_cancelled_copy_terminates_reaps_and_removes_output(
    processing: Any,
    video_path: Path,
    processes: Any,
    tmp_path: Path,
) -> None:
    _, instances, state, started = processes
    state["block"] = True
    task = asyncio.create_task(processing.materialize_clip(video_path, 3, 5))
    await asyncio.wait_for(started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    child = next(instance for instance in instances if instance.block)
    assert child.terminated or child.killed
    assert child.reaped
    assert list(tmp_path.rglob("*.mp4")) == [video_path]


@pytest.mark.parametrize("source_origin,output_origin", [(5.0, -0.4), (-2.0, 0.3)])
async def test_clip_mapping_uses_matching_video_packets_not_container_origin(
    processing: Any,
    video_path: Path,
    processes: Any,
    source_origin: float,
    output_origin: float,
) -> None:
    _, _, state, _ = processes
    state["source_origin"] = source_origin
    state["output_origin"] = output_origin
    clip = await processing.materialize_clip(video_path, 3, 5)
    try:
        assert clip.actual_start_ns == 2_000_000_000
        assert clip.actual_end_ns == 6_000_000_000
        assert clip.pts_offset_ns == 2_000_000_000
        assert clip.duration_s == pytest.approx(4.0)
    finally:
        clip.cleanup()


@pytest.mark.parametrize(
    "fault",
    [
        "packet_identity",
        "relative_timing",
        "missing_timing",
        "short_coverage",
        "probe_failure",
    ],
)
async def test_unverified_output_is_rejected_and_removed(
    processing: Any, video_path: Path, processes: Any, tmp_path: Path, fault: str
) -> None:
    _, _, state, _ = processes
    state["output_fault"] = fault
    with pytest.raises(processing.ClipError):
        await processing.materialize_clip(video_path, 3, 5)
    assert list(tmp_path.iterdir()) == [video_path]


async def test_video_without_audio_materializes_without_inventing_audio(
    processing: Any, video_path: Path, processes: Any
) -> None:
    _, _, state, _ = processes
    state["audio"] = False
    clip = await processing.materialize_clip(video_path, 3, 5)
    try:
        assert clip.audio is False
        assert clip.path.exists()
    finally:
        clip.cleanup()


async def test_lost_audio_overlapping_retained_video_is_rejected(
    processing: Any, video_path: Path, processes: Any, tmp_path: Path
) -> None:
    _, _, state, _ = processes
    state["drop_audio_output"] = True
    with pytest.raises(processing.ClipError, match="preserve the source audio"):
        await processing.materialize_clip(video_path, 3, 5)
    assert list(tmp_path.rglob("*.mp4")) == [video_path]


@pytest.mark.parametrize("missing", ["ffmpeg", "ffprobe"])
async def test_each_external_executable_is_required_independently(
    processing: Any,
    video_path: Path,
    processes: Any,
    monkeypatch: pytest.MonkeyPatch,
    missing: str,
) -> None:
    import shutil

    commands, _, _, _ = processes
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: None if name == missing else f"/external/bin/{name}",
    )
    with pytest.raises(processing.ClipError, match=missing):
        await processing.materialize_clip(video_path, 3, 5)
    assert not commands


async def test_clip_json_parse_and_packet_mapping_run_off_event_loop(
    processing: Any,
    video_path: Path,
    processes: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib
    import json
    import threading
    import time

    clips = importlib.import_module("vss.video_processing.clips")
    event_loop_thread = threading.current_thread()
    parse_threads: list[threading.Thread] = []
    verify_threads: list[threading.Thread] = []
    original_loads = json.loads
    original_verify = clips._verify_mapping

    def parse_off_loop(payload: str | bytes) -> Any:
        parse_threads.append(threading.current_thread())
        time.sleep(0.03)
        return original_loads(payload)

    def verify_off_loop(*args: Any) -> Any:
        verify_threads.append(threading.current_thread())
        time.sleep(0.08)
        return original_verify(*args)

    monkeypatch.setattr(clips.json, "loads", parse_off_loop)
    monkeypatch.setattr(clips, "_verify_mapping", verify_off_loop)
    clip = await processing.materialize_clip(video_path, 3, 5)
    clip.cleanup()

    assert parse_threads and all(
        thread is not event_loop_thread for thread in parse_threads
    )
    assert verify_threads and all(
        thread is not event_loop_thread for thread in verify_threads
    )


async def test_cancelled_copy_kills_child_that_ignores_termination(
    processing: Any, video_path: Path, processes: Any, tmp_path: Path
) -> None:
    _, instances, state, started = processes
    state.update(block=True, ignore_terminate=True)
    task = asyncio.create_task(processing.materialize_clip(video_path, 3, 5))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=10)
    finally:
        for instance in instances:
            if instance.block and instance.returncode is None:
                instance.kill()
        if not task.done():
            task.cancel()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=2)
    child = next(instance for instance in instances if instance.block)
    assert child.terminated
    assert child.killed
    assert child.reaped
    assert list(tmp_path.iterdir()) == [video_path]


async def test_ffmpeg_input_seek_uses_video_origin_and_aligned_gop_clock(
    processing: Any, video_path: Path, processes: Any
) -> None:
    commands, _, state, _ = processes
    state["source_origin"] = 5.0
    # The fixture reports container/audio start=4.9 while the first video PTS
    # is 5.0. Request 3..5 aligns to video-relative 2..6, hence raw-video 7..11.
    clip = await processing.materialize_clip(video_path, 3, 5)
    try:
        command = next(cmd for cmd in commands if "ffmpeg" in Path(cmd[0]).name)
        input_options = command[: command.index("-i")]
        seek = float(input_options[input_options.index("-ss") + 1])
        absolute_mode = (
            "-seek_timestamp" in input_options
            and input_options[input_options.index("-seek_timestamp") + 1] == "1"
        )
        # FFmpeg's default -ss is relative to FILE start; -seek_timestamp 1
        # instead makes it absolute. Either correct translation is acceptable.
        actual_seek = seek if absolute_mode else seek + 4.9
        assert actual_seek == pytest.approx(7.0, abs=1e-9)
        assert clip.actual_start_ns == 2_000_000_000
        assert clip.actual_end_ns == 6_000_000_000
    finally:
        clip.cleanup()


async def test_repeated_clip_cancellation_reaps_before_removing_output(
    processing: Any,
    video_path: Path,
    processes: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands, instances, state, started = processes
    state.update(block=True, ignore_terminate=True)
    termination_wait_started = asyncio.Event()
    reaped_with_output_present = asyncio.Event()
    original_wait = FakeProcess.wait

    async def observed_wait(child: FakeProcess) -> int:
        if child.block and not child.killed:
            # The child ignores terminate(), so this wait stays pending until
            # bounded teardown escalates to kill. Cancel the caller HERE.
            termination_wait_started.set()
        result = await original_wait(child)
        if child.block:
            output = Path(
                next(cmd[-1] for cmd in commands if "ffmpeg" in Path(cmd[0]).name)
            )
            assert output.exists(), "partial output removed while child was still alive"
            reaped_with_output_present.set()
        return result

    monkeypatch.setattr(FakeProcess, "wait", observed_wait)
    task = asyncio.create_task(processing.materialize_clip(video_path, 3, 5))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        await asyncio.wait_for(termination_wait_started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=10)
        child = next(instance for instance in instances if instance.block)
        assert child.killed and child.reaped
        assert reaped_with_output_present.is_set()
        assert list(tmp_path.iterdir()) == [video_path]
    finally:
        # Failed implementations must not leave this test's synthetic child or
        # async task blocked after the assertion has exposed the lifecycle bug.
        for instance in instances:
            if instance.block and instance.returncode is None:
                instance.kill()
        if not task.done():
            task.cancel()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=2)
