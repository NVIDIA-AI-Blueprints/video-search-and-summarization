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

"""Unit tests for config.Config class (env-based configuration)."""
import importlib
import os

import pytest


def test_config_port_default_when_env_unset(monkeypatch):
    """Config.PORT is 9002 when PORT is not set."""
    monkeypatch.delenv("PORT", raising=False)
    import config as config_mod
    importlib.reload(config_mod)
    assert config_mod.Config.PORT == 9002


def test_config_port_from_env(monkeypatch):
    """Config.PORT reflects PORT env var when set."""
    monkeypatch.setenv("PORT", "9003")
    import config as config_mod
    importlib.reload(config_mod)
    assert config_mod.Config.PORT == "9003"


def test_config_ds_config_path_default(monkeypatch):
    """Config.DS_CONFIG_PATH uses default when env unset."""
    monkeypatch.delenv("DS_CONFIG_PATH", raising=False)
    import config as config_mod
    importlib.reload(config_mod)
    assert config_mod.Config.DS_CONFIG_PATH == "/tmp/data/vss-rt-config-adaptor/config.csv"


def test_config_ds_config_path_from_env(monkeypatch):
    """Config.DS_CONFIG_PATH reflects env when set."""
    monkeypatch.setenv("DS_CONFIG_PATH", "/custom/config.csv")
    import config as config_mod
    importlib.reload(config_mod)
    assert config_mod.Config.DS_CONFIG_PATH == "/custom/config.csv"


def test_config_ds_config_path_empty_string_uses_default(monkeypatch):
    """Config.DS_CONFIG_PATH uses default when env is empty string."""
    monkeypatch.setenv("DS_CONFIG_PATH", "   ")
    import config as config_mod
    importlib.reload(config_mod)
    assert config_mod.Config.DS_CONFIG_PATH == "/tmp/data/vss-rt-config-adaptor/config.csv"


def test_config_yaml_paths_defaults(monkeypatch):
    """Config YAML source/target use defaults when env unset."""
    monkeypatch.delenv("DS_CONFIG_YAML_SOURCE_PATH", raising=False)
    monkeypatch.delenv("DS_CONFIG_YAML_TARGET_PATH", raising=False)
    import config as config_mod
    importlib.reload(config_mod)
    assert config_mod.Config.DS_CONFIG_YAML_SOURCE_PATH == "/ds-config/config.yaml"
    assert config_mod.Config.DS_CONFIG_YAML_TARGET_PATH == "/tmp/data/vss-rt-config-adaptor/config.yaml"


def test_config_yaml_paths_from_env(monkeypatch):
    """Config YAML paths reflect env when set."""
    monkeypatch.setenv("DS_CONFIG_YAML_SOURCE_PATH", "/src/config.yaml")
    monkeypatch.setenv("DS_CONFIG_YAML_TARGET_PATH", "/tgt/config.yaml")
    import config as config_mod
    importlib.reload(config_mod)
    assert config_mod.Config.DS_CONFIG_YAML_SOURCE_PATH == "/src/config.yaml"
    assert config_mod.Config.DS_CONFIG_YAML_TARGET_PATH == "/tgt/config.yaml"


def test_config_event_and_metadata_fields_defaults(monkeypatch):
    """Config event/metadata field names use defaults when env unset."""
    monkeypatch.delenv("EVENT_OBJECT_FIELD", raising=False)
    monkeypatch.delenv("METADATA_OBJECT_FIELD", raising=False)
    import config as config_mod
    importlib.reload(config_mod)
    assert config_mod.Config.EVENT_OBJECT_FIELD == "event"
    assert config_mod.Config.METADATA_OBJECT_FIELD == "metadata"


def test_config_event_and_metadata_fields_from_env(monkeypatch):
    """Config event/metadata field names reflect env when set."""
    monkeypatch.setenv("EVENT_OBJECT_FIELD", "ev")
    monkeypatch.setenv("METADATA_OBJECT_FIELD", "meta")
    import config as config_mod
    importlib.reload(config_mod)
    assert config_mod.Config.EVENT_OBJECT_FIELD == "ev"
    assert config_mod.Config.METADATA_OBJECT_FIELD == "meta"


def test_config_calib_file_path_default(monkeypatch):
    """Config.CALIB_FILE_PATH uses default when env unset."""
    monkeypatch.delenv("CALIB_FILE_PATH", raising=False)
    import config as config_mod
    importlib.reload(config_mod)
    assert config_mod.Config.CALIB_FILE_PATH == "/tmp/data/vss-rt-config-adaptor/calibration_grouped.json"


def test_config_calib_file_path_from_env(monkeypatch):
    """Config.CALIB_FILE_PATH reflects env when set."""
    monkeypatch.setenv("CALIB_FILE_PATH", "/data/calib.json")
    import config as config_mod
    importlib.reload(config_mod)
    assert config_mod.Config.CALIB_FILE_PATH == "/data/calib.json"
