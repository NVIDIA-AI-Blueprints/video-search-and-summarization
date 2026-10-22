# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
import os
from unittest.mock import patch

import pytest

from src.config import NIMConfig, Settings


@pytest.fixture
def clear_api_key():
    """Fixture to clear any existing API key environment variables."""
    if "TEST_API_KEY" in os.environ:
        del os.environ["TEST_API_KEY"]
    yield
    if "TEST_API_KEY" in os.environ:
        del os.environ["TEST_API_KEY"]


def test_target_model_for_evaluation_internal(clear_api_key):
    """Test target_model_for_evaluation with internal NIM."""
    with patch.object(Settings, "get_api_key") as mock_get_api_key:
        # Create NIM config for internal deployment
        nim_config = NIMConfig(
            model_name="test/model",
            context_length=2048,
            gpus=1,
            pvc_size="10Gi",
            tag="latest",
        )

        # Get target model config
        target_model = nim_config.target_model_for_evaluation()

        # Verify it returns the correct model name (original, not namespace-replaced)
        assert isinstance(target_model, str)
        assert target_model == "test/model"  # Use original model name

        # Verify get_api_key was never called
        mock_get_api_key.assert_not_called()


def test_nim_config_registry_base():
    """Test that registry_base is set correctly and is frozen."""
    # Create NIM config with default registry base
    nim_config = NIMConfig(
        model_name="test-model",
        context_length=2048,
        gpus=1,
        pvc_size="10Gi",
        tag="latest",
    )

    # Verify registry base is set to default value
    assert nim_config.registry_base == "nvcr.io/nim"

    # Create NIM config with custom registry base
    nim_config = NIMConfig(
        model_name="test-model",
        context_length=2048,
        gpus=1,
        pvc_size="10Gi",
        tag="latest",
        registry_base="custom.registry.io/nim",
    )

    # Verify registry base is set to custom value
    assert nim_config.registry_base == "custom.registry.io/nim"

    # Verify registry base is frozen
    with pytest.raises(ValueError):
        nim_config.registry_base = "new.registry.io/nim"


def test_nim_config_to_dms_config():
    """Test conversion of NIMConfig to DMS deployment configuration."""
    # Create NIM config for internal deployment
    nim_config = NIMConfig(
        model_name="test/model",
        context_length=2048,
        gpus=1,
        pvc_size="10Gi",
        tag="latest",
    )

    dms_config = nim_config.to_dms_config()

    # NMP 26.03 deployment_config payload — flat (no nested `config`), workspace replaces namespace.
    assert dms_config["name"] == "test-model"  # Slash replaced with dash
    assert dms_config["workspace"] == "dfwbp"
    assert dms_config["nim_deployment"]["image_name"] == "nvcr.io/nim/test/model"
    assert dms_config["nim_deployment"]["image_tag"] == "latest"
    # pvc_size in 25.12 maps to disk_size in 26.03's NIMDeploymentParam.
    assert dms_config["nim_deployment"]["disk_size"] == "10Gi"
    assert dms_config["nim_deployment"]["gpu"] == 1

    nim_config = NIMConfig(
        model_name="test/model",
        context_length=2048,
        gpus=1,
        pvc_size="10Gi",
        tag="latest",
        registry_base="custom.registry.io/nim",
    )

    dms_config = nim_config.to_dms_config()

    assert dms_config["name"] == "test-model"
    assert dms_config["workspace"] == "dfwbp"
    assert dms_config["nim_deployment"]["image_name"] == "custom.registry.io/nim/test/model"
    assert dms_config["nim_deployment"]["image_tag"] == "latest"
    assert dms_config["nim_deployment"]["disk_size"] == "10Gi"
    assert dms_config["nim_deployment"]["gpu"] == 1
