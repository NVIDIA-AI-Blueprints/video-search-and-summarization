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

"""Unit tests for the caption pass: window planning, stamp offsetting, the context block and the R2 scratch lifecycle.

The caption texts come from the vss-chunk-scout run on IMG_8206 (20 s windows, 2 s overlap), stored in
``fixtures/chunk_captions_img8206_w20.json`` with signed URLs removed.
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
import time
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from vss_agents.utils import chunk_captions
from vss_agents.utils import run_provenance
from vss_agents.utils.chunk_captions import CONTEXT_PREAMBLE
from vss_agents.utils.chunk_captions import ChunkCaptionError
from vss_agents.utils.chunk_captions import ChunkSettings
from vss_agents.utils.chunk_captions import InlineScratchStore
from vss_agents.utils.chunk_captions import ScratchStore
from vss_agents.utils.chunk_captions import ScratchStoreError
from vss_agents.utils.chunk_captions import Window
from vss_agents.utils.chunk_captions import caption_request_body
from vss_agents.utils.chunk_captions import caption_video
from vss_agents.utils.chunk_captions import chunk_settings
from vss_agents.utils.chunk_captions import context_block
from vss_agents.utils.chunk_captions import offset_chunk_timestamps
from vss_agents.utils.chunk_captions import parse_caption_events
from vss_agents.utils.chunk_captions import plan_windows
from vss_agents.utils.chunk_captions import scratch_prefix
from vss_agents.utils.chunk_captions import window_is_empty
from vss_agents.utils.crop_evidence import CropCallBudget

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "chunk_captions_img8206_w20.json").read_text())
VIDEO_URL = "https://r2.example/uploads/IMG_8206_480.mp4?X-Amz-Signature=abc"


class TestSettings:
    def test_defaults_are_the_scouts_recommendation_and_off(self, monkeypatch):
        for name in (
            "INCIDENT_CHUNKING_ENABLED",
            "INCIDENT_CHUNK_WINDOW_SECONDS",
            "INCIDENT_CHUNK_OVERLAP_SECONDS",
            "INCIDENT_CHUNK_MIN_CLIP_SECONDS",
            "INCIDENT_CHUNK_NUM_FRAMES",
            "INCIDENT_CHUNK_CONCURRENCY",
        ):
            monkeypatch.delenv(name, raising=False)
        assert chunk_settings() == ChunkSettings(
            enabled=False,
            window_seconds=20.0,
            overlap_seconds=2.0,
            min_clip_seconds=30.0,
            num_frames=32,
            concurrency=4,
        )

    def test_env_overrides_and_the_flag(self, monkeypatch):
        monkeypatch.setenv("INCIDENT_CHUNKING_ENABLED", "1")
        monkeypatch.setenv("INCIDENT_CHUNK_WINDOW_SECONDS", "10")
        monkeypatch.setenv("INCIDENT_CHUNK_OVERLAP_SECONDS", "1.5")
        monkeypatch.setenv("INCIDENT_CHUNK_MIN_CLIP_SECONDS", "0")
        monkeypatch.setenv("INCIDENT_CHUNK_NUM_FRAMES", "64")
        monkeypatch.setenv("INCIDENT_CHUNK_CONCURRENCY", "2")
        assert chunk_settings() == ChunkSettings(True, 10.0, 1.5, 0.0, 64, 2)

    def test_only_the_literal_1_enables(self, monkeypatch):
        monkeypatch.setenv("INCIDENT_CHUNKING_ENABLED", "true")
        assert chunk_settings().enabled is False

    @pytest.mark.parametrize(
        ("name", "value"),
        [
            ("INCIDENT_CHUNK_WINDOW_SECONDS", "abc"),
            ("INCIDENT_CHUNK_WINDOW_SECONDS", "0.5"),
            ("INCIDENT_CHUNK_OVERLAP_SECONDS", "25"),
            ("INCIDENT_CHUNK_CONCURRENCY", "0"),
        ],
    )
    def test_bad_values_are_rejected(self, monkeypatch, name, value):
        monkeypatch.setenv(name, value)
        with pytest.raises(ValueError):
            chunk_settings()


class TestPlanWindows:
    def test_the_scouts_windows_for_the_demo_clip(self):
        windows = plan_windows(FIXTURE["duration_s"], window_seconds=20, overlap_seconds=2)
        assert [(w.index, w.start, w.end) for w in windows] == [
            (c["index"], c["start"], c["end"]) for c in FIXTURE["chunks"]
        ]

    def test_a_clip_at_or_under_the_minimum_gets_no_windows(self):
        assert plan_windows(23.9) == []
        assert plan_windows(30.0) == []
        assert len(plan_windows(30.1)) == 2

    def test_a_remainder_shorter_than_three_seconds_is_merged_into_the_previous_window(self):
        windows = plan_windows(94.19, window_seconds=47, overlap_seconds=0)
        assert [(w.start, w.end) for w in windows] == [(0.0, 47.0), (47.0, 94.19)]
        windows = plan_windows(94.0, window_seconds=20, overlap_seconds=2)  # last would be [90, 94], 4 s: kept
        assert windows[-1] == Window(index=5, start=90.0, end=94.0)
        windows = plan_windows(92.5, window_seconds=20, overlap_seconds=2)  # last would be [90, 92.5], 2.5 s: merged
        assert windows[-1] == Window(index=4, start=72.0, end=92.5)

    def test_a_single_window_is_never_merged_away(self):
        assert plan_windows(31.0, window_seconds=30, overlap_seconds=0) == [Window(0, 0.0, 31.0)]  # 1 s tail merged
        assert plan_windows(31.0, window_seconds=40, overlap_seconds=0, min_clip_seconds=0) == [Window(0, 0.0, 31.0)]

    def test_overlap_must_be_smaller_than_the_window(self):
        with pytest.raises(ValueError):
            plan_windows(60, window_seconds=10, overlap_seconds=10)


class TestStampsAndEvents:
    def test_stamps_are_offset_to_whole_video_seconds(self):
        chunk = FIXTURE["chunks"][4]  # 72-92 s: the fall
        absolute = offset_chunk_timestamps(chunk["content"], chunk["start"], chunk["end"])
        assert absolute.startswith("[72.0s-80.5s]")
        assert "[80.5s-84.6s]" in absolute and "[84.6s-92.0s]" in absolute
        assert "[0.0s-" not in absolute

    def test_stamps_past_the_window_length_are_scaled_first(self):
        assert offset_chunk_timestamps("[0s-10s] a [10s-40s] b", 20.0, 40.0) == "[20.0s-25.0s] a [25.0s-40.0s] b"

    def test_content_without_stamps_is_unchanged(self):
        assert offset_chunk_timestamps("nothing to see", 20.0, 40.0) == "nothing to see"

    def test_events_keep_both_ends_and_the_text_up_to_the_next_stamp(self):
        events = parse_caption_events("[72.0s-80.5s] Quiet hall.\n[80.5s-84.6s] Two men enter. One falls.")
        assert events == [
            {"start": 72.0, "end": 80.5, "text": "Quiet hall."},
            {"start": 80.5, "end": 84.6, "text": "Two men enter. One falls."},
        ]

    def test_the_fall_window_parses_into_three_events(self):
        chunk = FIXTURE["chunks"][4]
        events = parse_caption_events(offset_chunk_timestamps(chunk["content"], chunk["start"], chunk["end"]))
        assert [(e["start"], e["end"]) for e in events] == [(72.0, 80.5), (80.5, 84.6), (84.6, 92.0)]
        assert "trips" in events[1]["text"] or "fall" in events[1]["text"]

    def test_short_stamp_noise_is_dropped_but_a_one_second_fall_is_kept(self):
        caption = "[80.0s-80.0s] blink\n[81.8s-82.8s] the man falls\n[83.0s-90.0s] he is helped up"
        assert chunk_captions.filter_short_duration_lines(caption) == (
            "[81.8s-82.8s] the man falls\n[83.0s-90.0s] he is helped up"
        )


def _timeline() -> list[dict[str, Any]]:
    entries = []
    for chunk in FIXTURE["chunks"]:
        caption = offset_chunk_timestamps(chunk["content"], chunk["start"], chunk["end"])
        entries.append(
            {"start": chunk["start"], "end": chunk["end"], "caption": caption, "events": parse_caption_events(caption)}
        )
    return entries


class TestContextBlock:
    def test_static_windows_are_dropped_and_action_windows_kept(self):
        timeline = _timeline()
        assert [window_is_empty(e["events"]) for e in timeline] == [True, True, True, True, False, False]
        block = context_block(timeline)
        assert block is not None
        assert block.startswith(CONTEXT_PREAMBLE)
        assert "[72.0s-92.0s]\n[72.0s-80.5s]" in block
        assert "[90.0s-94.2s]\n[90.0s-94.2s]" in block
        assert "[0.0s-20.0s]\n" not in block and "[18.0s-38.0s]" not in block

    def test_the_wording_tells_the_model_to_trust_the_video_and_where_times_come_from(self):
        assert "CONTEXT FROM A FIRST PASS OVER THE SAME VIDEO (not evidence by itself)." in CONTEXT_PREAMBLE
        assert "trust the video" in CONTEXT_PREAMBLE
        assert "must come from the video, not from the window bounds" in CONTEXT_PREAMBLE
        assert "static view, no activity" in CONTEXT_PREAMBLE

    def test_all_empty_windows_give_no_block(self):
        assert context_block(_timeline()[:4]) is None
        assert context_block([]) is None

    def test_a_window_without_events_counts_as_empty(self):
        assert window_is_empty([]) is True
        assert window_is_empty([{"start": 0, "end": 2, "text": "A man runs in and falls."}]) is False


class TestCaptionRequest:
    def test_stock_prompts_video_first_and_the_window_length(self):
        body = caption_request_body("vlm", "https://signed/0.mp4", 20.0, 32)
        assert body["messages"][0] == {"role": "system", "content": chunk_captions.CAPTION_SYSTEM_PROMPT}
        parts = body["messages"][1]["content"]
        assert parts[0] == {"type": "video_url", "video_url": {"url": "https://signed/0.mp4"}}
        assert parts[1]["text"].startswith("Describe in detail what is happening in this video")
        assert "START_TIME: 0s" in parts[1]["text"] and "END_TIME: 20.0s" in parts[1]["text"]
        assert body["temperature"] == 0.0 and body["max_tokens"] == 4096
        assert body["media_io_kwargs"] == {"video": {"num_frames": 32}}


class FakeR2:
    """A boto3-like client recording puts and serving list/delete over what was put.

    ``deleted`` lists what the end-of-pass prefix delete removed; ``events`` is every put and delete in order, as
    ``("put" | "delete" | "delete_one", key)`` (``delete_one`` is a single window deleted after use).
    """

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.events: list[tuple[str, str]] = []

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> None:  # noqa: N803
        assert Bucket == "bucket" and ContentType == "video/mp4"
        self.objects[Key] = Body
        self.events.append(("put", Key))

    def delete_object(self, *, Bucket: str, Key: str) -> None:  # noqa: N803
        assert Bucket == "bucket"
        self.objects.pop(Key, None)
        self.events.append(("delete_one", Key))

    def generate_presigned_url(self, operation: str, *, Params: dict[str, str], ExpiresIn: int) -> str:  # noqa: N803
        assert operation == "get_object" and ExpiresIn == 3600
        return f"https://signed.example/{Params['Key']}?sig=1"

    def get_paginator(self, name: str) -> Any:
        assert name == "list_objects_v2"
        client = self

        class Paginator:
            def paginate(self, *, Bucket: str, Prefix: str) -> list[dict[str, Any]]:  # noqa: N803
                return [{"Contents": [{"Key": k} for k in sorted(client.objects) if k.startswith(Prefix)]}]

        return Paginator()

    def delete_objects(self, *, Bucket: str, Delete: dict[str, Any]) -> None:  # noqa: N803
        for entry in Delete["Objects"]:
            self.deleted.append(entry["Key"])
            self.objects.pop(entry["Key"], None)
            self.events.append(("delete", entry["Key"]))


@pytest.fixture
def no_ffmpeg_work(monkeypatch):
    """Skip the real download and ffmpeg cut: write a stub window file instead."""

    async def fake_download(video_url: str, target: Path) -> None:
        assert video_url == VIDEO_URL
        target.write_bytes(b"source")

    async def fake_cut(ffmpeg: str, source: Path, window: Window, target: Path) -> None:
        assert source.exists()
        target.write_bytes(f"window {window.index} {window.start}-{window.end}".encode())

    monkeypatch.setattr(chunk_captions, "download_video", fake_download)
    monkeypatch.setattr(chunk_captions, "cut_window", fake_cut)
    monkeypatch.setattr(chunk_captions, "ffmpeg_path", lambda: "/usr/bin/ffmpeg")


def _gateway(responses_by_key: dict[str, list[httpx.Response]], seen: list[dict[str, Any]]):
    """A fake gateway answering per window key; each list is consumed in order (the last entry repeats)."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        url = body["messages"][1]["content"][0]["video_url"]["url"]
        key = url.split("signed.example/")[1].split("?")[0]
        queue = responses_by_key[key]
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return handler


def _completion(content: str, prompt_tokens: int = 100) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": 10, "total_tokens": prompt_tokens + 10},
        },
    )


SETTINGS = ChunkSettings(enabled=True, window_seconds=20, overlap_seconds=2, min_clip_seconds=30, num_frames=32)


class TestCaptionVideo:
    @pytest.mark.asyncio
    async def test_the_whole_pass_on_the_scout_fixture(self, no_ffmpeg_work, monkeypatch):
        monkeypatch.setattr(chunk_captions, "create_retry_strategy", _no_wait_retry)
        r2 = FakeR2()
        prefix = scratch_prefix("uploads/IMG_8206_480.mp4", "run1")
        responses = {
            f"{prefix}{c['index']}.mp4": [_completion(c["content"], c["usage"]["prompt_tokens"])]
            for c in FIXTURE["chunks"]
        }
        seen: list[dict[str, Any]] = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(_gateway(responses, seen))) as client:
            result = await caption_video(
                video_url=VIDEO_URL,
                video_key="uploads/IMG_8206_480.mp4",
                run_id="run1",
                duration_seconds=FIXTURE["duration_s"],
                client=client,
                vlm_base_url="https://switchyard.example/",
                model="nvidia/cosmos-3-nano-reasoner",
                settings=SETTINGS,
                store=ScratchStore(bucket="bucket", client=r2),
            )
        assert len(result.timeline) == 6
        assert [(e["start"], e["end"]) for e in result.timeline] == [(c["start"], c["end"]) for c in FIXTURE["chunks"]]
        fall = result.timeline[4]
        assert fall["caption"].startswith("[72.0s-80.5s]")
        assert [(e["start"], e["end"]) for e in fall["events"]] == [(72.0, 80.5), (80.5, 84.6), (84.6, 92.0)]
        assert result.context is not None and "[80.5s-84.6s]" in result.context
        assert result.telemetry == {
            "window_seconds": 20,
            "overlap_seconds": 2,
            "num_frames": 32,
            "model": "nvidia/cosmos-3-nano-reasoner",
            "calls": 6,
            "retries": 0,
            "prompt_tokens": sum(c["usage"]["prompt_tokens"] for c in FIXTURE["chunks"]),
            "completion_tokens": 60,
            "elapsed_ms": result.telemetry["elapsed_ms"],
        }
        assert result.telemetry["elapsed_ms"] >= 0
        # Every window went to scratch/analyze/<video key>/<run id>/<index>.mp4 and the prefix is empty afterwards.
        assert sorted(r2.deleted) == sorted(f"{prefix}{i}.mp4" for i in range(6))
        assert r2.objects == {}
        assert all(m["model"] == "nvidia/cosmos-3-nano-reasoner" for m in seen)
        assert {m["messages"][1]["content"][1]["text"].split("END_TIME: ")[1].strip() for m in seen} == {
            "20.0s",
            "4.2s",
        }

    @pytest.mark.asyncio
    async def test_a_429_is_retried_and_counted(self, no_ffmpeg_work, monkeypatch):
        monkeypatch.setattr(chunk_captions, "create_retry_strategy", _no_wait_retry)
        r2 = FakeR2()
        prefix = scratch_prefix("v1", "run1")
        responses = {
            f"{prefix}0.mp4": [httpx.Response(429, text="throttling_error"), _completion("[0s-20s] a")],
            f"{prefix}1.mp4": [_completion("[0s-13s] b")],
        }
        seen: list[dict[str, Any]] = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(_gateway(responses, seen))) as client:
            result = await caption_video(
                video_url=VIDEO_URL,
                video_key="v1",
                run_id="run1",
                duration_seconds=31.0,
                client=client,
                vlm_base_url="https://switchyard.example",
                model="vlm",
                settings=SETTINGS,
                store=ScratchStore(bucket="bucket", client=r2),
            )
        assert result.telemetry["calls"] == 3 and result.telemetry["retries"] == 1
        assert result.timeline[1]["caption"] == "[18.0s-31.0s] b"
        assert r2.objects == {}

    @pytest.mark.asyncio
    async def test_a_window_that_keeps_failing_fails_the_pass_and_the_scratch_is_still_deleted(
        self, no_ffmpeg_work, monkeypatch
    ):
        monkeypatch.setattr(chunk_captions, "create_retry_strategy", _no_wait_retry)
        r2 = FakeR2()
        prefix = scratch_prefix("v1", "run1")
        responses = {
            f"{prefix}0.mp4": [httpx.Response(503, text="down")],
            f"{prefix}1.mp4": [_completion("[0s-13s] b")],
        }
        seen: list[dict[str, Any]] = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(_gateway(responses, seen))) as client:
            with pytest.raises(ChunkCaptionError, match="HTTP 503"):
                await caption_video(
                    video_url=VIDEO_URL,
                    video_key="v1",
                    run_id="run1",
                    duration_seconds=31.0,
                    client=client,
                    vlm_base_url="https://switchyard.example",
                    model="vlm",
                    settings=SETTINGS,
                    store=ScratchStore(bucket="bucket", client=r2),
                )
        assert len([m for m in seen if "0.mp4" in m["messages"][1]["content"][0]["video_url"]["url"]]) == 3
        assert r2.objects == {} and len(r2.deleted) == 2

    @pytest.mark.asyncio
    async def test_a_400_is_not_retried(self, no_ffmpeg_work, monkeypatch):
        monkeypatch.setattr(chunk_captions, "create_retry_strategy", _no_wait_retry)
        r2 = FakeR2()
        prefix = scratch_prefix("v1", "run1")
        responses = {f"{prefix}0.mp4": [httpx.Response(400, text="bad")], f"{prefix}1.mp4": [_completion("[0s-13s] b")]}
        seen: list[dict[str, Any]] = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(_gateway(responses, seen))) as client:
            with pytest.raises(ChunkCaptionError, match="HTTP 400"):
                await caption_video(
                    video_url=VIDEO_URL,
                    video_key="v1",
                    run_id="run1",
                    duration_seconds=31.0,
                    client=client,
                    vlm_base_url="https://switchyard.example",
                    model="vlm",
                    settings=SETTINGS,
                    store=ScratchStore(bucket="bucket", client=r2),
                )
        assert len([m for m in seen if "0.mp4" in m["messages"][1]["content"][0]["video_url"]["url"]]) == 1
        assert r2.objects == {}

    @pytest.mark.asyncio
    async def test_a_short_clip_is_skipped_before_anything_is_touched(self):
        with pytest.raises(ChunkCaptionError, match="at or under INCIDENT_CHUNK_MIN_CLIP_SECONDS"):
            await caption_video(
                video_url=VIDEO_URL,
                video_key="v1",
                run_id="run1",
                duration_seconds=23.9,
                client=MagicMock(),
                vlm_base_url="https://switchyard.example",
                model="vlm",
                settings=SETTINGS,
                store=ScratchStore(bucket="bucket", client=MagicMock()),
            )

    @pytest.mark.asyncio
    async def test_missing_ffmpeg_is_a_plain_reason(self, monkeypatch):
        monkeypatch.setattr(chunk_captions, "ffmpeg_path", lambda: None)
        store_client = MagicMock()
        with pytest.raises(ChunkCaptionError, match="ffmpeg is not installed"):
            await caption_video(
                video_url=VIDEO_URL,
                video_key="v1",
                run_id="run1",
                duration_seconds=60.0,
                client=MagicMock(),
                vlm_base_url="https://switchyard.example",
                model="vlm",
                settings=SETTINGS,
                store=ScratchStore(bucket="bucket", client=store_client),
            )
        store_client.put_object.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_failed_cut_fails_the_pass_and_deletes_what_was_uploaded(self, monkeypatch):
        async def fake_download(video_url: str, target: Path) -> None:
            target.write_bytes(b"source")

        async def failing_cut(ffmpeg: str, source: Path, window: Window, target: Path) -> None:
            raise ChunkCaptionError(f"ffmpeg failed on window {window.index}: boom")

        monkeypatch.setattr(chunk_captions, "download_video", fake_download)
        monkeypatch.setattr(chunk_captions, "cut_window", failing_cut)
        monkeypatch.setattr(chunk_captions, "ffmpeg_path", lambda: "/usr/bin/ffmpeg")
        r2 = FakeR2()
        with pytest.raises(ChunkCaptionError, match="ffmpeg failed"):
            await caption_video(
                video_url=VIDEO_URL,
                video_key="v1",
                run_id="run1",
                duration_seconds=60.0,
                client=MagicMock(),
                vlm_base_url="https://switchyard.example",
                model="vlm",
                settings=SETTINGS,
                store=ScratchStore(bucket="bucket", client=r2),
            )
        assert r2.objects == {}

    @pytest.mark.asyncio
    async def test_without_r2_settings_the_pass_is_skipped(self, no_ffmpeg_work, monkeypatch):
        for name in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY", "R2_SECRET_KEY", "R2_BUCKET"):
            monkeypatch.delenv(name, raising=False)
        with pytest.raises(ChunkCaptionError, match="R2 is not configured"):
            await caption_video(
                video_url=VIDEO_URL,
                video_key="v1",
                run_id="run1",
                duration_seconds=60.0,
                client=MagicMock(),
                vlm_base_url="https://switchyard.example",
                model="vlm",
                settings=SETTINGS,
            )


def _no_wait_retry(retries: int, delay: float = 2, exceptions: tuple = ()) -> Any:
    """The module's retry strategy without the sleep between attempts."""
    from tenacity import AsyncRetrying
    from tenacity import retry_if_exception_type
    from tenacity import stop_after_attempt
    from tenacity import wait_none

    return AsyncRetrying(
        retry=retry_if_exception_type(exceptions), stop=stop_after_attempt(retries), wait=wait_none(), reraise=True
    )


class TestScratchStore:
    @pytest.mark.asyncio
    async def test_put_sign_and_delete_prefix(self, tmp_path):
        r2 = FakeR2()
        store = ScratchStore(bucket="bucket", client=r2)
        path = tmp_path / "0.mp4"
        path.write_bytes(b"mp4")
        key = await store.put_video("scratch/analyze/v1/run1/0.mp4", path)
        assert r2.objects == {"scratch/analyze/v1/run1/0.mp4": b"mp4"}
        assert (await store.signed_url(key)).startswith("https://signed.example/scratch/analyze/v1/run1/0.mp4")
        r2.objects["scratch/analyze/v1/run2/0.mp4"] = b"other run"
        assert await store.delete_prefix("scratch/analyze/v1/run1/") == 1
        assert list(r2.objects) == ["scratch/analyze/v1/run2/0.mp4"]

    def test_scratch_prefix_layout(self):
        assert scratch_prefix("uploads/IMG_8206_480.mp4", "m1") == "scratch/analyze/uploads/IMG_8206_480.mp4/m1/"
        assert scratch_prefix("/v1/", "m1") == "scratch/analyze/v1/m1/"
        assert scratch_prefix("v1", "m1", "/scratch/eval/") == "scratch/eval/v1/m1/"


class TestScratchStores:
    """Blocker X16: the scratch store is a port; the R2 one is confined to its root, the inline one stores nothing."""

    @pytest.mark.asyncio
    async def test_the_r2_store_never_touches_a_key_outside_its_root(self, tmp_path):
        r2 = FakeR2()
        r2.objects["uploads/keep.mp4"] = b"x"
        r2.objects["scratch/analyze/other/run9/0.mp4"] = b"y"
        store = ScratchStore(bucket="bucket", client=r2)
        window = tmp_path / "0.mp4"
        window.write_bytes(b"clip")
        assert await store.put_video("scratch/analyze/v1/run1/0.mp4", window) == "scratch/analyze/v1/run1/0.mp4"
        for key in ("uploads/0.mp4", "scratch/analyzed/v1/0.mp4", "scratch/0.mp4"):
            with pytest.raises(ScratchStoreError, match="outside"):
                await store.put_video(key, window)
        for prefix in ("scratch/analyze/", "scratch/analyze/v1/", "scratch/", "", "uploads/"):
            with pytest.raises(ScratchStoreError):
                await store.delete_prefix(prefix)
        assert await store.delete_prefix("scratch/analyze/v1/run1/") == 1
        assert set(r2.objects) == {"uploads/keep.mp4", "scratch/analyze/other/run9/0.mp4"}

    @pytest.mark.asyncio
    async def test_an_evaluation_root_is_its_own_sandbox(self, tmp_path):
        store = ScratchStore(bucket="bucket", client=FakeR2(), root="scratch/eval")
        window = tmp_path / "0.mp4"
        window.write_bytes(b"clip")
        await store.put_video("scratch/eval/v1/run1/0.mp4", window)
        with pytest.raises(ScratchStoreError):
            await store.put_video("scratch/analyze/v1/run1/0.mp4", window)
        with pytest.raises(ScratchStoreError):
            ScratchStore(bucket="bucket", client=FakeR2(), root="/")

    @pytest.mark.asyncio
    async def test_the_inline_store_sends_windows_as_data_urls_and_keeps_nothing(self, tmp_path):
        store = InlineScratchStore(max_bytes=10)
        window = tmp_path / "0.mp4"
        window.write_bytes(b"clip")
        key = await store.put_video("scratch/analyze/v1/run1/0.mp4", window)
        assert await store.signed_url(key) == "data:video/mp4;base64,Y2xpcA=="
        assert await store.delete_prefix("scratch/analyze/v1/run1/") == 1
        big = tmp_path / "1.mp4"
        big.write_bytes(b"x" * 11)
        with pytest.raises(ScratchStoreError, match="inline limit"):
            await store.put_video("scratch/analyze/v1/run1/1.mp4", big)

    @pytest.mark.asyncio
    async def test_the_whole_pass_runs_with_the_inline_store(self, no_ffmpeg_work, monkeypatch):
        monkeypatch.setattr(chunk_captions, "create_retry_strategy", _no_wait_retry)
        by_index = {c["index"]: c for c in FIXTURE["chunks"]}
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            url = json.loads(request.content)["messages"][1]["content"][0]["video_url"]["url"]
            assert url.startswith("data:video/mp4;base64,")
            index = int(base64.b64decode(url.split(",", 1)[1]).decode().split()[1])  # fake_cut writes "window <i> ..."
            seen.append(url)
            chunk = by_index[index]
            return _completion(chunk["content"], chunk["usage"]["prompt_tokens"])

        store = InlineScratchStore()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await caption_video(
                video_url=VIDEO_URL,
                video_key="uploads/IMG_8206_480.mp4",
                run_id="run1",
                duration_seconds=FIXTURE["duration_s"],
                client=client,
                vlm_base_url="https://switchyard.example/",
                model="nvidia/cosmos-3-nano-reasoner",
                settings=SETTINGS,
                store=store,
            )
        assert len(result.timeline) == 6 and len(seen) == 6
        assert store._windows == {}, "nothing is kept after the pass"


class TestExperimentScratch:
    """Experimental captions (X16): R2 windows deleted after use, the caption-call cap, and nothing left behind."""

    @pytest.mark.asyncio
    async def test_the_r2_store_deletes_a_window_on_release_only_when_it_deletes_after_use(self, tmp_path):
        window = tmp_path / "0.mp4"
        window.write_bytes(b"clip")
        r2 = FakeR2()
        production = ScratchStore(bucket="bucket", client=r2)
        await production.put_video("scratch/analyze/v1/run1/0.mp4", window)
        await production.release("scratch/analyze/v1/run1/0.mp4")
        assert list(r2.objects) == ["scratch/analyze/v1/run1/0.mp4"], "production keeps its end-of-pass delete"
        experiment = ScratchStore(bucket="bucket", client=r2, root="scratch/eval", delete_after_use=True)
        await experiment.put_video("scratch/eval/v1/run1/0.mp4", window)
        await experiment.release("scratch/eval/v1/run1/0.mp4")
        assert list(r2.objects) == ["scratch/analyze/v1/run1/0.mp4"]
        assert r2.events[-1] == ("delete_one", "scratch/eval/v1/run1/0.mp4")
        for key in ("scratch/analyze/v1/run1/0.mp4", "scratch/eval/v1/0.mp4", "uploads/v1/run1/0.mp4"):
            with pytest.raises(ScratchStoreError):
                await experiment.release(key)
        assert list(r2.objects) == ["scratch/analyze/v1/run1/0.mp4"], "a refused release deletes nothing"

    @pytest.mark.asyncio
    async def test_the_inline_store_drops_a_window_once_released(self, tmp_path):
        window = tmp_path / "0.mp4"
        window.write_bytes(b"clip")
        store = InlineScratchStore()
        assert store.max_bytes == chunk_captions.INLINE_MAX_BYTES == 8 * 1024 * 1024
        await store.put_video("inline/v1/run1/0.mp4", window)
        await store.release("inline/v1/run1/0.mp4")
        assert store._windows == {}
        await store.release("inline/v1/run1/0.mp4")  # releasing twice is harmless

    @pytest.mark.asyncio
    async def test_a_pass_under_an_experiment_root_deletes_each_window_right_after_its_call(
        self, no_ffmpeg_work, monkeypatch
    ):
        monkeypatch.setattr(chunk_captions, "create_retry_strategy", _no_wait_retry)
        r2 = FakeR2()
        store = ScratchStore(bucket="bucket", client=r2, root="scratch/eval", delete_after_use=True)

        def handler(request: httpx.Request) -> httpx.Response:
            url = json.loads(request.content)["messages"][1]["content"][0]["video_url"]["url"]
            key = url.split("signed.example/")[1].split("?")[0]
            assert key in r2.objects, "the window exists while the model may fetch it"
            r2.events.append(("call", key))
            return _completion("[0s-10s] a person walks")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await caption_video(
                video_url=VIDEO_URL,
                video_key="v1",
                run_id="exp-1",
                duration_seconds=60.0,
                client=client,
                vlm_base_url="https://switchyard.example",
                model="vlm",
                settings=SETTINGS,
                store=store,
            )
        keys = [key for event, key in r2.events if event == "put"]
        assert keys == [f"scratch/eval/v1/exp-1/{i}.mp4" for i in range(4)] and len(result.timeline) == 4
        for key in keys:
            assert r2.events.index(("call", key)) < r2.events.index(("delete_one", key))
        assert r2.objects == {} and r2.deleted == [], "nothing was left for the end-of-pass delete"

    @pytest.mark.asyncio
    async def test_the_caption_call_cap_refuses_an_attempt_beyond_it(self, no_ffmpeg_work, monkeypatch):
        monkeypatch.setattr(chunk_captions, "create_retry_strategy", _no_wait_retry)
        r2 = FakeR2()
        prefix = scratch_prefix("v1", "run1")
        responses = {
            f"{prefix}0.mp4": [httpx.Response(429, text="throttling_error"), _completion("[0s-20s] a")],
            f"{prefix}1.mp4": [_completion("[0s-13s] b")],
        }
        seen: list[dict[str, Any]] = []
        budget = CropCallBudget(2)
        with run_provenance.collecting() as provenance:
            async with httpx.AsyncClient(transport=httpx.MockTransport(_gateway(responses, seen))) as client:
                with pytest.raises(ChunkCaptionError, match="caption-call cap of 2 reached"):
                    await caption_video(
                        video_url=VIDEO_URL,
                        video_key="v1",
                        run_id="run1",
                        duration_seconds=31.0,
                        client=client,
                        vlm_base_url="https://switchyard.example",
                        model="vlm",
                        settings=SETTINGS,
                        store=ScratchStore(bucket="bucket", client=r2),
                        budget=budget,
                    )
        assert len(seen) == 2, "the retry of the 429 was the third attempt, refused before it was sent"
        assert budget.summary() == {
            "cap": 2,
            "calls": 2,
            "refused_by_cap": 1,
            "cap_reached": True,
            "tokens": 110,
            "calls_without_usage": 0,
        }
        caption = provenance.to_dict(contract_version="v", flags={})["stages"]["caption"]
        assert caption["calls"] == 2 and caption["failed"] == 1 and caption["refused"] == 1
        assert r2.objects == {}

    @pytest.mark.asyncio
    async def test_a_clip_with_more_windows_than_the_cap_is_refused_before_anything_is_touched(self, monkeypatch):
        download = MagicMock(side_effect=AssertionError("nothing is downloaded"))
        monkeypatch.setattr(chunk_captions, "download_video", download)
        store_client = MagicMock()
        with pytest.raises(ChunkCaptionError, match="4 windows, more than the caption-call cap of 3"):
            await caption_video(
                video_url=VIDEO_URL,
                video_key="v1",
                run_id="run1",
                duration_seconds=60.0,
                client=MagicMock(),
                vlm_base_url="https://switchyard.example",
                model="vlm",
                settings=SETTINGS,
                store=ScratchStore(bucket="bucket", client=store_client),
                budget=CropCallBudget(3),
            )
        download.assert_not_called()
        store_client.put_object.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_window_is_still_uploading_when_the_scratch_is_deleted(self, monkeypatch):
        """A failed window no longer leaves its siblings running: a late upload lands before the delete, not after."""

        class SlowR2(FakeR2):
            def put_object(self, **kwargs: Any) -> None:
                time.sleep(0.05)  # in the upload thread: still uploading window 1 when window 0 has failed
                super().put_object(**kwargs)

        async def fake_download(video_url: str, target: Path) -> None:
            target.write_bytes(b"source")

        async def cut(ffmpeg: str, source: Path, window: Window, target: Path) -> None:
            if window.index == 0:
                raise ChunkCaptionError("ffmpeg failed on window 0: boom")
            target.write_bytes(b"late window")

        monkeypatch.setattr(chunk_captions, "download_video", fake_download)
        monkeypatch.setattr(chunk_captions, "cut_window", cut)
        monkeypatch.setattr(chunk_captions, "ffmpeg_path", lambda: "/usr/bin/ffmpeg")
        r2 = SlowR2()
        with pytest.raises(ChunkCaptionError, match="window 0"):
            await caption_video(
                video_url=VIDEO_URL,
                video_key="v1",
                run_id="run1",
                duration_seconds=31.0,
                client=MagicMock(),
                vlm_base_url="https://switchyard.example",
                model="vlm",
                settings=SETTINGS,
                store=ScratchStore(bucket="bucket", client=r2),
            )
        await asyncio.sleep(0.1)  # had window 1 still been running, its upload would land now
        assert r2.objects == {} and r2.deleted == ["scratch/analyze/v1/run1/1.mp4"]
