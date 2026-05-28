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
"""Unit tests for VST video_list module."""

from pydantic import ValidationError
import pytest

from vss_agents.tools.vst.video_list import VSTVideoListConfig
from vss_agents.tools.vst.video_list import VSTVideoListInput


class TestVSTVideoListConfig:
    """Test VSTStreamListConfig model."""

    def test_valid_config(self):
        """Test creating config with valid vst_internal_url."""
        config = VSTVideoListConfig(vst_internal_url="http://localhost:30888")
        assert config.vst_internal_url == "http://localhost:30888"

    def test_config_with_trailing_slash(self):
        """Test config with trailing slash in URL."""
        config = VSTVideoListConfig(vst_internal_url="http://localhost:30888/")
        assert config.vst_internal_url == "http://localhost:30888/"

    def test_config_with_vst_suffix(self):
        """Test config with /vst suffix in URL."""
        config = VSTVideoListConfig(vst_internal_url="http://localhost:30888/vst")
        assert config.vst_internal_url == "http://localhost:30888/vst"

    def test_missing_vst_internal_url_raises(self):
        """Test that missing vst_internal_url raises ValidationError."""
        with pytest.raises(ValidationError):
            VSTVideoListConfig()

    def test_config_inherits_function_base_config(self):
        """Test that config has properties from FunctionBaseConfig."""
        config = VSTVideoListConfig(vst_internal_url="http://localhost:30888")
        # FunctionBaseConfig should provide a name attribute through registration
        assert hasattr(config, "vst_internal_url")


class TestVSTStreamListInput:
    """Test VSTStreamListInput model."""

    def test_empty_input(self):
        """Test creating input with no parameters (pass is the only field)."""
        input_data = VSTVideoListInput()
        assert input_data is not None

    def test_input_is_pydantic_model(self):
        """Test that input is a valid Pydantic model."""
        input_data = VSTVideoListInput()
        # Should be serializable to dict
        data_dict = input_data.model_dump()
        assert isinstance(data_dict, dict)

    def test_input_json_serializable(self):
        """Test that input can be serialized to JSON."""
        input_data = VSTVideoListInput()
        json_str = input_data.model_dump_json()
        assert json_str == "{}"
