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

"""Persistent-process 30-minute closed-loop library/RTVI/ES exercise."""

import argparse
import asyncio
import json
import os
import threading
import time
from pathlib import Path
from uuid import uuid4

from benchmarks.common import software_hash
from benchmarks.e2e.config import fingerprint
from benchmarks.media import file_hash
from benchmarks.resources import ResourceSampler
from benchmarks.scorecard import resource_summary, workflow_index
from benchmarks.workflow.__main__ import preflight
from benchmarks.workflow.config import load
from benchmarks.workflow.gpu import DeviceSampler
from benchmarks.workflow.operation import operation
from benchmarks.workflow.relay import Relay


async def drive(window_s, invoke, *, maximum_operations=None, clock=time.monotonic):
    """No imposed idle; report actual request activity apart from load-window time."""
    started = clock()
    rows, failure = [], None
    while maximum_operations is None or len(rows) < maximum_operations:
        if clock() - started >= window_s:
            break
        index = len(rows)
        admitted = clock()
        row = {
            "iteration": index,
            "admitted_monotonic_s": admitted,
            "operation": {"status": "failed", "cleanup": {}},
        }
        rows.append(row)
        try:
            result = await invoke(index)
            finished = clock()
            row.update(finished_monotonic_s=finished, operation=result)
            if (
                result["status"] != "completed_diagnostic"
                or not result["cleanup"]
                or not all(v is True for v in result["cleanup"].values())
            ):
                raise ValueError("soak_operation_or_cleanup")
        except Exception as error:
            failure = type(error).__name__
            row.update(failure=failure, finished_monotonic_s=clock())
            break
    finished = clock()
    if finished - started < window_s and failure is None:
        failure = "maximum_operations_before_window_complete"
    active_s = sum(r["operation"].get("workflow_to_result_s", 0) for r in rows)
    return {
        "status": "complete_closed_loop" if failure is None else "incomplete",
        "failure": failure,
        "load_window_s": window_s,
        "started_monotonic_s": started,
        "finished_monotonic_s": finished,
        "elapsed_s": finished - started,
        "operations": rows,
        "admitted_operations": len(rows),
        "completed_operations": sum(
            r["operation"]["status"] == "completed_diagnostic" for r in rows
        ),
        "request_active_s": active_s,
        "setup_cleanup_and_gap_s": finished - started - active_s,
        "request_active_duty_fraction": active_s / (finished - started)
        if finished > started
        else None,
        "interpretation": "closed_loop_window_not_continuous_GPU_busy_time",
    }


async def exercise(profile, output, expected_chunks):
    upstream = os.environ["M8_SOAK_LLM_UPSTREAM"]
    (output / "captures-private").mkdir(mode=0o700)

    async def invoke(index):
        with Relay(upstream, profile.llm_model, str(uuid4())) as relay:
            thread = threading.Thread(
                target=lambda: relay.serve_forever(poll_interval=0.05), daemon=True
            )
            thread.start()
            token = relay.token
            base = f"http://127.0.0.1:{relay.server_port}"
            os.environ[profile.llm_url_env] = base + "/" + token + "/v1"
            os.environ[profile.relay_control_env] = base + "/control/" + token
            current = profile.model_copy(
                update={
                    "output_capture": output / "captures-private" / f"{index:03d}.json",
                }
            )
            try:
                result = await operation(current, expected_chunks)
                result["profile_sha256"] = fingerprint(current.model_dump(mode="json"))
                return result
            finally:
                relay.shutdown()
                thread.join(timeout=2)
                if thread.is_alive():
                    raise RuntimeError("soak_relay_thread_unsettled")

    return await drive(1800, invoke)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(mode=0o700)
    profile = load(args.config)
    if (
        profile.arm != "library"
        or profile.captioning != "rtvi"
        or profile.store_backend != "elasticsearch"
    ):
        raise ValueError("soak_composition")
    admission = preflight(profile)
    root = Path(__file__).resolve().parents[2]
    source = software_hash(root)
    report = {
        "status": "incomplete",
        "schema_version": "closed_loop_soak.1",
        "qualified": False,
        "thresholds_approved": False,
        "source_sha256": profile.video_sha256,
        "base_config_sha256": file_hash(args.config),
        "run_index": workflow_index(profile),
        "stack_manifest_sha256": profile.stack_manifest_sha256,
        "software_sha256": source,
        "media_admission": admission,
        "observation_scope": "worker_tree_shared_host_and_device_wide_1s_samples",
        "remote_inference_settlement": "unproven",
    }
    try:
        with ResourceSampler(1) as resources, DeviceSampler(1) as gpu:
            report.update(
                asyncio.run(
                    exercise(profile, args.output, len(admission["occupied_windows"]))
                )
            )
        report.update(
            worker_resources=resources.trace.model_dump(mode="json"),
            gpu=gpu.trace,
            resource_summary=resource_summary(resources.trace, gpu.trace),
        )
        report["observers_complete"] = resources.trace.complete and (
            not gpu.trace["available"] or gpu.trace["complete"]
        )
        if (
            not report["observers_complete"]
            or file_hash(profile.video) != profile.video_sha256
            or software_hash(root) != source
        ):
            report.update(status="incomplete", failure="observation_or_source_drift")
    except Exception as error:
        report.update(status="incomplete", failure=type(error).__name__)
    with (args.output / "soak.json").open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
    print(
        json.dumps(
            {
                k: report.get(k)
                for k in ("status", "failure", "completed_operations", "elapsed_s")
            }
        )
    )
    return 0 if report["status"] == "complete_closed_loop" else 1


if __name__ == "__main__":
    raise SystemExit(main())
