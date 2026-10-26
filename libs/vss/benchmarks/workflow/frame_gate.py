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

"""Inspect actual CPU/GPU VLM frame selection without calling model services.

The recorder replaces inference only. It observes the real ingestion pipeline's
selected frames; passing this gate does not prove delivery through an endpoint.
RGB references stay in bounded process memory and are never written to reports.
"""

import argparse
import asyncio
import hashlib
import json
import shutil
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from benchmarks.common import import_origins, software_hash
from benchmarks.e2e.__main__ import frame_admission
from benchmarks.e2e.config import fingerprint
from benchmarks.media import MAX_INPUT_BYTES, bounded_tool, file_hash
from benchmarks.workflow.sampling import prepare
from vss.ingestion import RequestSource
from vss.ingestion.backend import get_backend
from vss.ingestion.vlm import VLMBackend, register_vlm_backend
from vss.ingestion.vlm_contracts import VlmChunkResponse


class CaptureFailure(Exception):
    def __init__(self, reason, rows, sampling):
        self.reason, self.rows, self.sampling = reason, rows, sampling
        super().__init__(reason)


def compare(reference, candidate, expected_windows):
    counts = [len(chunk["frames"]) for chunk in candidate]
    return {
        "same_chunk_windows": [row["window"] for row in reference]
        == [row["window"] for row in candidate],
        "same_frame_pts": [[f["pts_ns"] for f in row["frames"]] for row in reference]
        == [[f["pts_ns"] for f in row["frames"]] for row in candidate],
        "same_frame_counts": [len(row["frames"]) for row in reference] == counts,
        "requested_eight_frames_per_occupied_window": bool(counts)
        and all(n == 8 for n in counts),
        "expected_occupied_windows": [row["index"] for row in candidate]
        == expected_windows,
        "pixels_checked_for_every_selected_frame": all(
            f.get("rgb_mae") is not None and f["rgb_mae"] <= 3
            for row in candidate
            for f in row["frames"]
        )
        and bool(counts)
        and all(counts),
        "pixel_tolerance": "full_RGB_mean_absolute_error_le_3_descriptive_oracle",
        "endpoint_delivery": "not_tested_recorder_only",
    }


async def capture(profile, admission, references=None):
    rows, retained = [], {}
    pipeline, sampling = prepare(profile, admission)

    class Recorder(VLMBackend):
        async def infer(self, chunk, **kwargs):
            row = {
                "index": chunk.chunk_idx,
                "window": [round(chunk.start_s * 1e9), round(chunk.end_s * 1e9)],
                "frames": [],
            }
            if len(rows) >= 20:
                raise ValueError("frame gate chunk bound")
            for frame in chunk.frames:
                array = frame.tensor
                if (
                    not isinstance(array, np.ndarray)
                    or array.dtype != np.uint8
                    or (
                        array.ndim != 3
                        or array.shape[2] != 3
                        or array.shape[0] > 1080
                        or array.shape[1] > 1920
                    )
                ):
                    raise ValueError("frame gate RGB contract")
                key = (chunk.chunk_idx, frame.timestamp_ns)
                if key in retained or len(row["frames"]) >= 8:
                    raise ValueError("frame gate multiplicity")
                item = {
                    "pts_ns": frame.timestamp_ns,
                    "shape": list(array.shape),
                    "rgb_sha256": hashlib.sha256(array.tobytes()).hexdigest(),
                }
                if references is None:
                    retained[key] = array.copy()
                    if sum(a.nbytes for a in retained.values()) > 1024**3:
                        raise ValueError("frame gate reference memory")
                elif key in references and references[key].shape == array.shape:
                    item["rgb_mae"] = float(
                        np.abs(
                            array.astype(np.int16) - references[key].astype(np.int16)
                        ).mean()
                    )
                row["frames"].append(item)
            rows.append(row)
            return VlmChunkResponse(vlm_response="recorder_not_model_inference")

        async def cancel(self, source_id):
            return False

        async def check_health(self):
            return True

        def capability_map(self):
            return {"recorder": True}

    register_vlm_backend("m8_frame_gate", Recorder)
    backend = get_backend(pipeline)
    backend.vlm_backend = "m8_frame_gate"
    try:
        await backend.ingest(
            RequestSource(source_id="frame-gate", url=str(profile.video)),
            model="frame-gate-recorder",
            prompt="frame-gate-recorder",
            chunk_duration=30,
            chunk_overlap_duration=0,
        )
    except Exception as error:
        raise CaptureFailure(type(error).__name__, rows, sampling) from error
    return rows, retained, sampling


def admit(video):
    if not video.is_file() or not 0 < video.stat().st_size <= MAX_INPUT_BYTES:
        raise ValueError("video bound")
    source = file_hash(video)
    probe = shutil.which("ffprobe")
    if probe is None:
        raise ValueError("FFprobe required")
    media = json.loads(
        bounded_tool(
            [
                probe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-count_frames",
                "-show_frames",
                "-show_entries",
                "stream=width,height,duration,time_base,nb_frames,nb_read_frames:format=duration:frame=best_effort_timestamp",
                "-of",
                "json",
                str(video),
            ],
            limit=4 * 1024**2,
            timeout=60,
        )
    )
    stream = media["streams"][0]
    if any(
        type(stream.get(key)) is not int or not 0 < stream[key] <= limit
        for key, limit in (("width", 1920), ("height", 1080))
    ):
        raise ValueError("geometry bound")
    duration = float(stream.get("duration") or media["format"]["duration"])
    if not 0 < duration <= 600:
        raise ValueError("duration bound")
    admission = {
        "width": stream["width"],
        "height": stream["height"],
        "duration_s": duration,
        "duration_quality": "video" if stream.get("duration") else "container",
        "selected_stream": "v:0",
        "source_sha256": source,
        "oracle_sha256": fingerprint(media),
        **frame_admission(
            media, SimpleNamespace(duration_s=duration, chunk_duration_s=30)
        ),
    }
    if len(admission["occupied_windows"]) > 20:
        raise ValueError("window bound")
    return admission


async def run(video):
    admission = admit(video)
    source, duration = admission["source_sha256"], admission["duration_s"]
    outputs, references, baselines = [], {}, {}
    halted = False
    for captioning, contract in (
        ("cpu_vlm", "shared_duration_grid.v1"),
        ("gpu_vlm", "shared_duration_grid.v1"),
        ("cpu_vlm", "backend_native"),
        ("gpu_vlm", "backend_native"),
    ):
        row = {"captioning": captioning, "sampling_contract": contract}
        outputs.append(row)
        if halted:
            row.update(status="not_run", reason="previous_arm_settlement_unconfirmed")
            continue
        profile = SimpleNamespace(
            video=video,
            video_sha256=source,
            duration_s=duration,
            captioning=captioning,
            sampling_contract=contract,
        )
        started = time.monotonic()
        try:
            rows, saved, sampling = await capture(
                profile, admission, references.get(contract)
            )
            row.update(
                status="completed_diagnostic",
                sampling=sampling,
                chunks=rows,
                diagnostic_elapsed_s=time.monotonic() - started,
            )
            if captioning == "cpu_vlm":
                references[contract], baselines[contract] = saved, rows
                row["comparison"] = None
            else:
                row["comparison_reference"] = {
                    "captioning": "cpu_vlm",
                    "sampling_contract": contract,
                }
                row["comparison"] = compare(
                    baselines[contract], rows, admission["occupied_windows"]
                )
                del references[contract]
        except Exception as error:
            row.update(
                status="failed",
                failure=error.reason
                if isinstance(error, CaptureFailure)
                else type(error).__name__,
                diagnostic_elapsed_s=time.monotonic() - started,
                local_decoder_settlement="unconfirmed_halt_remaining_arms",
            )
            if isinstance(error, CaptureFailure):
                row.update(chunks=error.rows, sampling=error.sampling)
            halted = True
    if source != file_hash(video):
        raise ValueError("source drift")
    return {
        "schema_version": "frame_selection_gate.1",
        "status": "incomplete" if halted else "completed_diagnostic",
        "acceptance_eligible": False,
        "media_admission": admission,
        "observations": outputs,
        "scope": "pipeline_pre_inference_frames_no_endpoint_no_perf_claim",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    report = {"status": "failed", "acceptance_eligible": False}
    source = software_hash(root)
    with args.output.open("x") as stream:
        try:
            report.update(asyncio.run(run(args.video)))
        except Exception as error:
            report.update(status="failed", failure=type(error).__name__)
        finally:
            report.update(software_sha256=source, import_origins=import_origins(root))
            if software_hash(root) != source:
                report.update(status="failed", failure="software_drift")
            json.dump(report, stream, indent=2, allow_nan=False)
    print(json.dumps({"status": report["status"], "output": str(args.output)}))
    return 0 if report["status"] == "completed_diagnostic" else 1


if __name__ == "__main__":
    raise SystemExit(main())
