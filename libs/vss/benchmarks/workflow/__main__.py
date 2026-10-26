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

"""One real workflow diagnostic: python -m benchmarks.workflow --help.

Deployment supplies a fresh one-shot relay and owned normal frontend; this CLI
does not create infrastructure. No option enables comparative statistics.
"""

import argparse
import asyncio
import json
import os
import shutil
from pathlib import Path

from benchmarks.common import software_hash
from benchmarks.e2e.__main__ import frame_admission
from benchmarks.e2e.config import fingerprint
from benchmarks.media import MAX_INPUT_BYTES, bounded_tool, file_hash
from benchmarks.scorecard import workflow_index
from benchmarks.workflow.config import load
from benchmarks.workflow.observation import Observers, policy
from benchmarks.workflow.operation import operation
from benchmarks.workflow.swaps import contract

ROOT = Path(__file__).resolve().parents[2]

GAPS = [
    "normal structured aggregation and library explicit final summary differ",
    "effective normal caption prompts/sampling and raw caption completion "
    "are unqualified",
    "RTVI query-to-uploaded-asset binding is unqualified",
    "normal result retention differs from explicit library index cleanup",
    "semantic output quality has not been reviewed",
    "instrumentation overhead, repeated pairs and budgets are unqualified",
    "asset deletion and outer completion do not prove remote inference settlement",
    "worker CPU/RSS and shared host CPU/RAM do not attribute individual service costs",
]


def preflight(profile):
    if (
        not profile.video.is_file()
        or not 0 < profile.video.stat().st_size <= MAX_INPUT_BYTES
    ):
        raise ValueError("video byte budget")
    if file_hash(profile.video) != profile.video_sha256:
        raise ValueError("source hash")
    if profile.stack_manifest is not None and (
        not 0 < profile.stack_manifest.stat().st_size <= 1024**2
        or file_hash(profile.stack_manifest) != profile.stack_manifest_sha256
    ):
        raise ValueError("stack identity")
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise ValueError("FFprobe required")
    raw = bounded_tool(
        [
            ffprobe,
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
            str(profile.video),
        ],
        limit=4 * 1024**2,
        timeout=60,
    )
    media = json.loads(raw)
    stream = media["streams"][0]
    duration = float(stream.get("duration") or media["format"]["duration"])
    if (
        not 0 < stream["width"] <= 1920
        or not 0 < stream["height"] <= 1080
        or abs(duration - profile.duration_s) > 0.1
    ):
        raise ValueError("media dimensions or duration")
    return {
        "width": stream["width"],
        "height": stream["height"],
        "duration_s": duration,
        "duration_quality": "video" if stream.get("duration") else "container",
        "source_sha256": profile.video_sha256,
        "selected_stream": "v:0",
        "oracle_sha256": fingerprint(media),
        **frame_admission(media, profile),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    report = {
        "schema_version": "workflow.1",
        "status": "failed",
        "qualified": False,
        "comparative_statistics": None,
        "qualification_gaps": GAPS,
    }
    output = None
    try:
        if not args.preflight:
            if args.output is None:
                raise ValueError("output required")
            # Reserve before service calls, preventing replay of interrupted work.
            output = args.output.open("x", encoding="utf-8")
            os.chmod(args.output, 0o600)
            output.write(json.dumps({**report, "status": "reserved_not_complete"}))
            output.flush()
        profile = load(args.config)
        admission = preflight(profile)
        report.update(
            config_sha256=fingerprint(profile.model_dump(mode="json")),
            run_index=workflow_index(profile),
            source_sha256=profile.video_sha256,
            stack_manifest_sha256=profile.stack_manifest_sha256,
            normal_config_sha256=profile.normal_config_sha256,
            software_sha256=software_hash(ROOT),
            harness_sha256=fingerprint(
                {
                    str(p.relative_to(ROOT)): file_hash(p)
                    for p in sorted((ROOT / "benchmarks").rglob("*.py"))
                }
            ),
            lockfile_sha256=file_hash(ROOT / "uv.lock"),
            observation_policy=policy(profile.observation),
            media_admission=admission,
            requested_caption_settings=profile.caption_settings(),
            requested_caption_contract=profile.caption_contract,
            module_comparison_contract=contract(profile),
            modules={
                "captioning": profile.captioning,
                "storage": profile.store_backend,
            },
            caption_prompt_sha256=fingerprint(profile.caption_prompt),
            summary_prompt_sha256=fingerprint(profile.summary_prompt),
            requested_summary_cap=profile.summary_max_tokens,
        )
        if args.preflight:
            report.update(status="local_preflight_ok", inference_executed=False)
        else:
            with Observers(
                profile.observation,
                maximum_duration_s=profile.work_timeout_s
                + 180
                + 8 * profile.cleanup_timeout_s
                + 65,
            ) as observers:
                report["operation"] = asyncio.run(
                    operation(
                        profile, len(admission["occupied_windows"]), admission=admission
                    )
                )
            report.update(
                status=report["operation"]["status"],
                **observers.report(),
            )
            if not report["observer_complete"]:
                report.update(
                    status="failed", failure="incomplete_resource_observation"
                )
            if file_hash(profile.video) != profile.video_sha256:
                report.update(status="failed", failure="source_changed")
    except Exception as error:
        report.update(status="failed", failure=type(error).__name__)
    finally:
        raw = json.dumps(report, indent=2, allow_nan=False)
        if output:
            output.seek(0)
            output.write(raw + "\n")
            output.truncate()
            output.close()
        print(raw)
    return (
        0 if report["status"] in {"completed_diagnostic", "local_preflight_ok"} else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
