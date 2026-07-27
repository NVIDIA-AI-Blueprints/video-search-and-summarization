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

"""Resolution of alert_agent.processes."""

import pytest

from utils import process_scaling
from utils.process_scaling import resolve_process_count


class TestResolveProcessCount:
    def test_absent_key_defaults_to_single_process(self):
        assert resolve_process_count({}) == 1
        assert resolve_process_count({"alert_agent": {}}) == 1
        assert resolve_process_count(None) == 1

    def test_explicit_null_defaults_to_single_process(self):
        assert resolve_process_count({"alert_agent": {"processes": None}}) == 1

    def test_integer_value(self):
        assert resolve_process_count({"alert_agent": {"processes": 4}}) == 4

    def test_numeric_string_value(self):
        assert resolve_process_count({"alert_agent": {"processes": " 6 "}}) == 6

    def test_auto_uses_available_cpus(self, monkeypatch):
        monkeypatch.setattr(process_scaling, "available_cpus", lambda: 12)
        assert resolve_process_count({"alert_agent": {"processes": "AUTO"}}) == 12

    def test_auto_never_returns_zero(self, monkeypatch):
        monkeypatch.setattr(process_scaling, "available_cpus", lambda: 0)
        assert resolve_process_count({"alert_agent": {"processes": "auto"}}) == 1

    @pytest.mark.parametrize("value", [0, -1, True, 2.5, "many", ""])
    def test_invalid_values_fail_startup(self, value):
        with pytest.raises(ValueError):
            resolve_process_count({"alert_agent": {"processes": value}})


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
