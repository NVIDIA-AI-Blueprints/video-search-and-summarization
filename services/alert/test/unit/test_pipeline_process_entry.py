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

"""Child entry point: which pipeline process owns the instance-wide work."""

import os
from unittest.mock import MagicMock, patch

import pytest

import enhance_alert_with_vlm as entry


@pytest.fixture
def built():
    """Capture the instance_leader each child would construct itself with."""
    seen = []

    def fake_enhancer(config_path, instance_leader=True):
        seen.append(instance_leader)
        return MagicMock()

    with patch.object(entry, "AnomalyEnhancer", side_effect=fake_enhancer), \
         patch.object(entry, "_exit_when_parent_dies"), \
         patch.object(entry, "_log_instance_concurrency"):
        yield seen


class TestInstanceLeaderElection:
    """Prompt seeding and the verdict reaper are per instance, not per pipeline.

    Running them in every child multiplies writes against a shared
    Elasticsearch and defeats the reaper's own request-rate throttle.
    """

    def test_child_zero_leads(self, built):
        entry._run_pipeline_process("config.yaml", 0, os.getpid(), process_count=4)
        assert built == [True]

    @pytest.mark.parametrize("index", [1, 2, 7])
    def test_every_other_child_follows(self, built, index):
        entry._run_pipeline_process("config.yaml", index, os.getpid(), process_count=8)
        assert built == [False]

    def test_exactly_one_leader_across_the_instance(self, built):
        for index in range(6):
            entry._run_pipeline_process("config.yaml", index, os.getpid(), process_count=6)
        assert built.count(True) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
