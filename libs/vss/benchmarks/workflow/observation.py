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

"""Explicit observer controls; mandatory completion witnesses remain enabled."""

from contextlib import ExitStack

from benchmarks.resources import ResourceSampler
from benchmarks.scorecard import resource_summary
from benchmarks.workflow.gpu import DeviceSampler
from vss.observability import collect_vss_metrics

POLICIES = ("minimal", "collection", "resources", "combined")


def policy(name):
    if name not in POLICIES:
        raise ValueError("observation_policy")
    sampled = name in {"resources", "combined"}
    return {
        "name": name,
        "cpu_ram": "worker_process_tree" if sampled else "not_collected",
        "host_cpu_ram": "host_system_shared" if sampled else "not_collected",
        "worker_interval_s": 0.05 if sampled else None,
        "gpu": "device_wide_including_resident_models" if sampled else "not_collected",
        "gpu_interval_s": 0.25 if sampled else None,
        "library_scalar_collection": name in {"collection", "combined"},
        "completion_witnesses": "retained_not_calibrated",
    }


class Observers:
    def __init__(self, name, *, maximum_duration_s=None):
        self.policy = policy(name)
        if maximum_duration_s is not None and self.policy["cpu_ram"] != "not_collected":
            # Leave headroom for start/end samples, without silently truncating
            # otherwise valid ten-minute workflows and their bounded cleanup.
            self.policy["worker_interval_s"] = max(0.05, maximum_duration_s / 5900)
            self.policy["gpu_interval_s"] = max(0.25, maximum_duration_s / 3900)
        self.resources = self.gpu = self.collector = None

    def __enter__(self):
        self.stack = ExitStack()
        try:
            if self.policy["cpu_ram"] != "not_collected":
                self.resources = self.stack.enter_context(
                    ResourceSampler(self.policy["worker_interval_s"])
                )
                self.gpu = self.stack.enter_context(
                    DeviceSampler(self.policy["gpu_interval_s"])
                )
            if self.policy["library_scalar_collection"]:
                self.collector = self.stack.enter_context(
                    collect_vss_metrics(max_events=2048)
                )
        except BaseException:
            self.stack.close()
            raise
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)

    def report(self):
        resources = self.resources.trace if self.resources else None
        gpu = self.gpu.trace if self.gpu else None
        dropped = self.collector.dropped_count if self.collector else None
        return {
            "observation_policy": self.policy,
            "worker_resources": resources.model_dump(mode="json")
            if resources
            else None,
            "gpu": gpu,
            "resource_summary": resource_summary(resources, gpu),
            "scalar_collection": {
                "scope": "worker_library_only_not_normal_service",
                "events": len(self.collector.observations) if self.collector else None,
                "dropped": dropped,
            },
            "observer_complete": (
                (resources is None or resources.complete)
                and (gpu is None or not gpu["available"] or gpu["complete"])
                and (dropped is None or dropped == 0)
                and (self.collector is None or self.collector.complete)
            ),
        }
