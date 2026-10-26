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

"""Deterministic library observer controls; no model or network requests."""

import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path

from benchmarks.common import software_hash
from benchmarks.run import FixtureGenerator, corpus
from benchmarks.workflow.observation import POLICIES, Observers
from vss.summarization import summarize_video


async def calls(chunks, count):
    digest = hashlib.sha256()
    for _ in range(count):
        output = await summarize_video(chunks, generator=FixtureGenerator())
        digest.update((output.choices[0].message.content or "").encode())
    return digest.hexdigest()


def run(output, blocks=10, repeats=20):
    if blocks != 10 or repeats != 20:
        raise ValueError("fixed_control_schedule")
    output.mkdir(mode=0o700)
    rows = []
    for block in range(blocks):
        for name in POLICIES[block % 4 :] + POLICIES[: block % 4]:
            rows.append({"block": block, "policy": name, "status": "not_run"})
    root = Path(__file__).resolve().parents[2]
    source = software_hash(root)
    intent = {
        "schema_version": "observer_controls.1",
        "blocks": blocks,
        "repeats_per_slot": repeats,
        "warmups_per_policy": 1,
        "chunks_per_call": 100,
        "schedule": rows,
        "software_sha256": source,
        "scope": "library_fixture_only_not_normal_VSS_or_relay_witness",
        "thresholds_approved": False,
        "qualified": False,
    }
    (output / "intent.json").write_text(json.dumps(intent, indent=2))
    chunks = corpus(100)
    expected = asyncio.run(calls(chunks, repeats))  # untimed warmup and output binding
    for name in POLICIES:
        with Observers(name) as observers:
            actual = asyncio.run(calls(chunks, repeats))
        if actual != expected or not observers.report()["observer_complete"]:
            raise ValueError("control_warmup_output_or_observation")
    started = time.monotonic()
    failure = None
    for row in rows:
        try:
            if time.monotonic() - started > 240:
                raise TimeoutError("fixture_batch_deadline")
            before = time.perf_counter()
            with Observers(row["policy"]) as observers:
                work_started = time.perf_counter()
                actual = asyncio.run(calls(chunks, repeats))
                row["work_wall_s"] = time.perf_counter() - work_started
            row["policy_wall_s"] = time.perf_counter() - before
            row["observation"] = observers.report()
            if actual != expected or not row["observation"]["observer_complete"]:
                raise ValueError("control_output_or_observation")
            row.update(status="completed", output_sha256=actual)
        except Exception as error:
            row.update(status="failed", failure=type(error).__name__)
            failure = type(error).__name__
            break
    if software_hash(root) != source:
        failure = "source_drift"
    receipt = intent | {
        "rows": rows,
        "failure": failure,
        "status": "complete" if failure is None else "incomplete",
        "elapsed_s": time.monotonic() - started,
        "comparative_statistics": None,
    }
    (output / "controls.json").write_text(json.dumps(receipt, indent=2))
    return {
        "status": receipt["status"],
        "completed": sum(r["status"] == "completed" for r in rows),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    print(json.dumps(run(parser.parse_args().output)))


if __name__ == "__main__":
    main()
