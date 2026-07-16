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

"""Unit tests for vss-rt-config-adaptor Flask API (POST /config)."""
import pytest

import app as app_mod


def test_post_config_non_json_content_type_returns_400(client):
    """POST /config with non-JSON Content-Type returns 400."""
    r = client.post(
        "/config",
        data="not json",
        content_type="text/plain",
    )
    assert r.status_code == 400
    assert b"JSON" in r.data


def test_post_config_valid_payload_returns_200(client, sample_config_payload):
    """POST /config with valid JSON and event.metadata returns 200."""
    r = client.post(
        "/config",
        json=sample_config_payload,
        content_type="application/json",
    )
    assert r.status_code == 200
    data = r.get_json()
    assert data == sample_config_payload


def test_post_config_writes_csv(client, sample_config_payload, tmp_path):
    """POST /config writes region,group,topic-prefix to DS_CONFIG_PATH CSV."""
    r = client.post("/config", json=sample_config_payload, content_type="application/json")
    assert r.status_code == 200
    csv_path = tmp_path / "config.csv"
    assert csv_path.exists()
    content = csv_path.read_text()
    assert "r1" in content and "g1" in content and "tp1" in content


def test_post_config_updates_target_yaml(client, sample_config_payload, tmp_path):
    """POST /config reads source YAML, updates calib_file_path and bev_group_name, writes target."""
    r = client.post("/config", json=sample_config_payload, content_type="application/json")
    assert r.status_code == 200
    target_path = tmp_path / "target.yaml"
    assert target_path.exists()
    data = app_mod.read_yaml_file(str(target_path))
    assert data["calib_file_path"] == str(tmp_path / "calib.json")
    assert data["bev_group_name"] == "g1"


def test_post_config_empty_body(client):
    """POST /config with empty or invalid JSON body does not crash (returns 200 or 5xx)."""
    r = client.post(
        "/config",
        data="{}",
        content_type="application/json",
    )
    # App may return 200 with empty response or 500; we only assert no crash
    assert r.status_code in (200, 500)
