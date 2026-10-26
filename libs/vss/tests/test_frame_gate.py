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

"""Frame counts alone cannot qualify selected inputs or endpoint delivery."""

import json
from types import SimpleNamespace

import numpy as np
import pytest

from benchmarks.workflow import frame_gate
from vss.ingestion.vlm import get_vlm_backend


@pytest.fixture(autouse=True)
def restore_backend_registry(monkeypatch):
    import vss.ingestion.vlm as vlm

    monkeypatch.setattr(vlm, "_VLM_BACKEND_REGISTRY", dict(vlm._VLM_BACKEND_REGISTRY))


async def test_actual_recorder_copies_reference_and_compares_candidate(monkeypatch):
    pixels = np.zeros((2, 2, 3), dtype=np.uint8)

    class Pipeline:
        async def ingest(self, *args, **kwargs):
            async with get_vlm_backend(self.vlm_backend) as backend:
                await backend.infer(
                    SimpleNamespace(
                        chunk_idx=0,
                        start_s=0,
                        end_s=2,
                        frames=[SimpleNamespace(timestamp_ns=0, tensor=pixels)],
                    )
                )

    monkeypatch.setattr(frame_gate, "prepare", lambda *args: ("fake", {}))
    monkeypatch.setattr(frame_gate, "get_backend", lambda name: Pipeline())
    profile = SimpleNamespace(video="fake")
    reference, retained, _ = await frame_gate.capture(profile, {})
    pixels[:] = 2
    candidate, _, _ = await frame_gate.capture(profile, {}, retained)
    assert retained[(0, 0)].max() == 0
    assert candidate[0]["frames"][0]["rgb_mae"] == 2
    result = frame_gate.compare(reference, candidate, [0])
    assert result["same_frame_counts"] and result["same_frame_pts"]
    assert result["pixels_checked_for_every_selected_frame"]
    assert result["endpoint_delivery"].startswith("not_tested")
    candidate[0]["frames"][0]["pts_ns"] = 1
    assert not frame_gate.compare(reference, candidate, [0])["same_frame_pts"]


def test_empty_missing_windows_or_unknown_pixels_cannot_pass():
    assert not frame_gate.compare([], [], [0])[
        "pixels_checked_for_every_selected_frame"
    ]
    row = {"index": 0, "window": [0, 2], "frames": [{"pts_ns": 0}]}
    result = frame_gate.compare([row], [row], [0, 1])
    assert not result["expected_occupied_windows"]
    assert not result["pixels_checked_for_every_selected_frame"]


def setup_probe(monkeypatch, tmp_path, **updates):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"fixture")
    stream = dict(width=2, height=2, duration=2, time_base="1/100", nb_read_frames=2)
    stream.update(updates)
    monkeypatch.setattr(frame_gate.shutil, "which", lambda _: "ffprobe")
    monkeypatch.setattr(
        frame_gate,
        "bounded_tool",
        lambda *args, **kwargs: json.dumps(
            {
                "streams": [stream],
                "frames": [
                    {"best_effort_timestamp": 0},
                    {"best_effort_timestamp": 100},
                ],
            }
        ).encode(),
    )
    return video


def selected(pts):
    return [
        {
            "index": 0,
            "window": [0, 2_000_000_000],
            "frames": [{"pts_ns": pts + index, "rgb_mae": 0} for index in range(8)],
        }
    ]


async def test_each_sampling_contract_uses_its_own_cpu_reference(monkeypatch, tmp_path):
    video = setup_probe(monkeypatch, tmp_path)

    async def capture(profile, admission, references):
        contract = profile.sampling_contract
        if profile.captioning == "gpu_vlm":
            assert references == {contract: "reference"}
        pts = 100 if contract == "backend_native" else 200
        return selected(pts), {contract: "reference"}, {"contract": contract}

    monkeypatch.setattr(frame_gate, "capture", capture)
    report = await frame_gate.run(video)
    assert report["status"] == "completed_diagnostic"
    for row in report["observations"]:
        if row["captioning"] == "gpu_vlm":
            assert (
                row["comparison_reference"]["sampling_contract"]
                == row["sampling_contract"]
            )
            assert row["comparison"]["same_frame_pts"]


async def test_failure_preserves_completed_and_partial_evidence_then_halts(
    monkeypatch, tmp_path
):
    video = setup_probe(monkeypatch, tmp_path)
    calls = []

    async def capture(profile, admission, references):
        calls.append(profile.captioning)
        if profile.captioning == "gpu_vlm":
            raise frame_gate.CaptureFailure(
                "DecodeError", selected(0), {"contract": profile.sampling_contract}
            )
        return (
            selected(0),
            {"pixels": "reference"},
            {"contract": profile.sampling_contract},
        )

    monkeypatch.setattr(frame_gate, "capture", capture)
    report = await frame_gate.run(video)
    assert report["status"] == "incomplete"
    assert calls == ["cpu_vlm", "gpu_vlm"]
    rows = report["observations"]
    assert rows[0]["chunks"] and rows[0]["status"] == "completed_diagnostic"
    assert rows[1]["failure"] == "DecodeError" and rows[1]["chunks"]
    assert [row["status"] for row in rows[2:]] == ["not_run", "not_run"]
    assert report["media_admission"]["frames"] == 2


@pytest.mark.parametrize("updates", [{"width": 1921}, {"height": 0}, {"width": True}])
async def test_geometry_rejected_before_decoder_open(monkeypatch, tmp_path, updates):
    video = setup_probe(monkeypatch, tmp_path, **updates)

    async def capture(*args):
        pytest.fail("decoder opened before admission")

    monkeypatch.setattr(frame_gate, "capture", capture)
    with pytest.raises(ValueError, match="geometry"):
        await frame_gate.run(video)
