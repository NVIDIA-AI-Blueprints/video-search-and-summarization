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

"""Isolated workflow worker with cooperative cancellation and a bound receipt."""

import argparse
import asyncio
import json
import signal
import time
from pathlib import Path

from benchmarks.common import software_hash
from benchmarks.e2e.config import fingerprint
from benchmarks.matrix.config import admit_environment
from benchmarks.media import file_hash
from benchmarks.workflow.__main__ import GAPS, preflight
from benchmarks.workflow.barrier import publish
from benchmarks.workflow.config import Workflow
from benchmarks.workflow.observation import Observers
from benchmarks.workflow.operation import operation

ROOT = Path(__file__).resolve().parents[2]


async def execute(intent, intent_sha):
    report = dict(
        schema_version="perf_trial.1",
        trial_id=intent["trial_id"],
        intent_sha256=intent_sha,
        status="failed",
        operational_settlement="not_started",
        qualified=False,
        thresholds_approved=False,
        qualification_gaps=GAPS,
        started_monotonic_s=time.monotonic(),
    )
    try:
        profile = Workflow.model_validate(intent["workflow"])
        if fingerprint(profile.model_dump(mode="json")) != intent["workflow_sha256"]:
            raise ValueError("workflow_identity")
        report["software_sha256"] = software_hash(ROOT)
        if report["software_sha256"] != intent["software_sha256"]:
            raise ValueError("source_changed")
        admit_environment(profile)
        admission = preflight(profile)
        report["media_admission"] = admission
        with Observers(
            profile.observation,
            maximum_duration_s=profile.work_timeout_s
            + 180
            + 8 * profile.cleanup_timeout_s
            + 65,
        ) as observers:
            task = asyncio.create_task(
                operation(
                    profile, len(admission["occupied_windows"]), admission=admission
                )
            )
            loop = asyncio.get_running_loop()
            interrupted = []

            def cancel():
                # Cancel once: repeated signals must not interrupt owned cleanup.
                if not interrupted:
                    interrupted.append(True)
                    task.cancel()

            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, cancel)
            try:
                report["operation"] = await task
            finally:
                for sig in (signal.SIGINT, signal.SIGTERM):
                    loop.remove_signal_handler(sig)
            report.update(
                status=report["operation"]["status"],
                operational_settlement=report["operation"]["operational_settlement"],
            )
            if interrupted:
                report.update(
                    status="cancelled",
                    operational_settlement="interrupted_remote_work_unknown",
                )
        report.update(observers.report())
        if not report["observer_complete"]:
            report.update(status="failed", failure="incomplete_observation")
        if (
            file_hash(profile.video) != profile.video_sha256
            or software_hash(ROOT) != intent["software_sha256"]
        ):
            report.update(status="failed", failure="source_changed")
    except (Exception, asyncio.CancelledError) as error:
        report.update(status="failed", failure=type(error).__name__)
        if "operation" in report:
            report["operational_settlement"] = "client_shutdown_or_observer_unsettled"
    report["finished_monotonic_s"] = time.monotonic()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intent", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    intent = json.loads(args.intent.read_bytes())
    report = asyncio.run(execute(intent, file_hash(args.intent)))
    publish(args.receipt, report)
    return 0 if report["status"] == "completed_diagnostic" else 1


if __name__ == "__main__":
    raise SystemExit(main())
