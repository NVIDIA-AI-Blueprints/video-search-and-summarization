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
import pytest
from pydantic import ValidationError

from src.config import NIMConfig, NMPConfig, settings


def test_nmp_config_initialization():
    # Arrange
    datastore_url = "http://datastore.example.com"
    nemo_url = "http://nemo.example.com"
    nim_url = "http://nim.example.com"
    namespace = "test-namespace"

    # Act
    config = NMPConfig(
        datastore_base_url=datastore_url,
        nemo_base_url=nemo_url,
        nim_base_url=nim_url,
        nmp_namespace=namespace,
    )

    # Assert
    assert config.datastore_base_url == datastore_url
    assert config.nemo_base_url == nemo_url
    assert config.nim_base_url == nim_url
    assert config.nmp_namespace == namespace


def test_nmp_config_attributes_are_readonly():
    # Arrange
    config = NMPConfig(
        datastore_base_url="http://datastore.example.com",
        nemo_base_url="http://nemo.example.com",
        nim_base_url="http://nim.example.com",
        nmp_namespace="test-namespace",
    )

    # Assert
    with pytest.raises(ValidationError):
        config.datastore_base_url = "new-url"
    with pytest.raises(ValidationError):
        config.nemo_base_url = "new-url"
    with pytest.raises(ValidationError):
        config.nim_base_url = "new-url"
    with pytest.raises(ValidationError):
        config.nmp_namespace = "new-namespace"


def test_llm_judge_config_initialization():
    # Arrange
    config_data = {
        "model_name": "meta/llama-3.1-8b-instruct",
        "context_length": 32768,
        "gpus": 1,
        "pvc_size": "25Gi",
        "tag": "1.8.3",
    }

    # Act
    llm_config = NIMConfig(**config_data)

    # Assert
    assert llm_config.model_name == "meta/llama-3.1-8b-instruct"
    assert llm_config.context_length == 32768
    assert llm_config.gpus == 1
    assert llm_config.pvc_size == "25Gi"
    assert llm_config.tag == "1.8.3"
    assert llm_config.registry_base == "nvcr.io/nim"
    assert llm_config.customization_enabled is False


def test_llm_judge_config_nmp_model_name():
    # Arrange
    config_data = {
        "model_name": "meta/llama-3.1-8b-instruct",
        "context_length": 32768,
        "gpus": 1,
        "pvc_size": "25Gi",
        "tag": "1.8.3",
    }

    # Act
    llm_config = NIMConfig(**config_data)

    # Assert — NMP 26.03 entity names use ``[a-z0-9-]+``; ``.`` collapses to ``-``.
    assert llm_config.nmp_model_name() == "meta-llama-3-1-8b-instruct"


def test_llm_judge_config_to_dms_config():
    # Arrange
    config_data = {
        "model_name": "meta/llama-3.1-8b-instruct",
        "context_length": 32768,
        "gpus": 1,
        "pvc_size": "25Gi",
        "tag": "1.8.3",
    }

    # Act
    llm_config = NIMConfig(**config_data)
    dms_config = llm_config.to_dms_config()

    # NMP 26.03 deployment_config payload — flat (no nested `config`), workspace replaces namespace,
    # pvc_size is renamed to disk_size in NIMDeploymentParam.
    assert dms_config["name"] == "meta-llama-3-1-8b-instruct"
    assert dms_config["workspace"] == settings.system_config.nmp_config.workspace
    assert dms_config["nim_deployment"]["image_name"] == "nvcr.io/nim/meta/llama-3.1-8b-instruct"
    assert dms_config["nim_deployment"]["image_tag"] == "1.8.3"
    assert dms_config["nim_deployment"]["disk_size"] == "25Gi"
    assert dms_config["nim_deployment"]["gpu"] == 1
    assert dms_config["nim_deployment"]["additional_envs"] == {
        "NIM_GUIDED_DECODING_BACKEND": "outlines",
    }


def test_llm_judge_config_validation():
    # Arrange
    invalid_config_data = {
        "model_name": "meta/llama-3.1-8b-instruct",
        "context_length": "invalid",  # Should be int
        "gpus": "invalid",  # Should be int or None
        "pvc_size": 123,  # Should be str or None
        "tag": 123,  # Should be str or None
    }

    # Act & Assert
    with pytest.raises(ValidationError):
        NIMConfig(**invalid_config_data)


def test_llm_judge_config_optional_fields():
    # Arrange
    minimal_config_data = {
        "model_name": "meta/llama-3.1-8b-instruct",
        "context_length": 32768,
    }

    # Act
    llm_config = NIMConfig(**minimal_config_data)

    # Assert
    assert llm_config.gpus is None
    assert llm_config.pvc_size is None
    assert llm_config.tag is None
    assert llm_config.customization_enabled is False
