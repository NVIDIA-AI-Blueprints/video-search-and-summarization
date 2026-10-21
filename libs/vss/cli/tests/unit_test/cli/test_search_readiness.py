# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import json
from unittest.mock import AsyncMock

from click.testing import CliRunner
import pytest

from vss_cli import config as config_mod
from vss_cli.search.group import SearchGroup
from vss_core.search_core import readiness
from vss_core.search_core.errors import BackendUnreachableError


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path))
    config_mod.save(
        config_mod.Deployment(
            base_url="http://example",
            services={
                "elasticsearch": config_mod.Service(url="http://example/elasticsearch", indices=["mdx-raw-2025-01-01"]),
                "vst": config_mod.Service(url="http://example/vst"),
            },
        )
    )


@pytest.mark.parametrize("ready,exit_code", [(True, 0), (False, 6)])
def test_readiness_json_and_exit_without_job_or_model_calls(configured, monkeypatch, ready, exit_code):
    probe = AsyncMock(return_value={"ready": ready, "attribute_gaps": ["clip"]})
    monkeypatch.setattr(readiness, "gallery_readiness", probe)
    result = CliRunner().invoke(SearchGroup().cli(), ["readiness", "--video-source", "clip", "--raw"])
    assert result.exit_code == exit_code, result.output
    assert json.loads(result.output) == {"ready": ready, "attribute_gaps": ["clip"]}
    assert probe.call_args.args[1] == ["clip"]
    assert probe.call_args.args[0].es_endpoint == "http://example/elasticsearch"
    assert probe.call_args.kwargs["require_attributes"] is True


def test_embed_only_switch(configured, monkeypatch):
    probe = AsyncMock(return_value={"ready": True})
    monkeypatch.setattr(readiness, "gallery_readiness", probe)
    result = CliRunner().invoke(
        SearchGroup().cli(), ["readiness", "--video-source", "clip", "--no-require-attributes", "--raw"]
    )
    assert result.exit_code == 0, result.output
    assert probe.call_args.kwargs["require_attributes"] is False


def test_backend_failure_is_not_empty_success(configured, monkeypatch):
    monkeypatch.setattr(
        readiness, "gallery_readiness", AsyncMock(side_effect=BackendUnreachableError("elasticsearch", "offline"))
    )
    result = CliRunner().invoke(SearchGroup().cli(), ["readiness", "--video-source", "clip", "--raw"])
    assert result.exit_code == 3, result.output
    assert "offline" in result.output


def test_no_config_is_typed_failure(tmp_path, monkeypatch):
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path))
    from vss_cli import main

    assert main(["search", "readiness", "--video-source", "clip", "--raw"]) == 4


def test_content_contract_file_forwarded_without_model_calls(configured, monkeypatch, tmp_path):
    value = {"schema_version": 1, "embedding": {"model": "expected", "dimensions": 2, "unit_normalized": True},
             "frame_counts": {"clip": 12}, "document_limit": 2000}
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(value))
    probe = AsyncMock(return_value={"ready": False, "content_validation": {"valid": False}})
    monkeypatch.setattr(readiness, "gallery_readiness", probe)
    result = CliRunner().invoke(SearchGroup().cli(), ["readiness", "--video-source", "clip", "--content-contract", str(path), "--raw"])
    assert result.exit_code == 6, result.output
    assert probe.call_args.kwargs["content_contract"] == value


def test_malformed_content_contract_is_typed_input_error(configured, tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{")
    result = CliRunner().invoke(SearchGroup().cli(), ["readiness", "--video-source", "clip", "--content-contract", str(path), "--raw"])
    assert result.exit_code == 2, result.output
    assert "content contract" in result.output


@pytest.mark.parametrize("value", [None, [], False, "contract"])
def test_explicit_nonobject_contract_never_disables_validation(configured, monkeypatch, tmp_path, value):
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(value))
    probe = AsyncMock(return_value={"ready": True})
    monkeypatch.setattr(readiness, "gallery_readiness", probe)
    result = CliRunner().invoke(SearchGroup().cli(), ["readiness", "--video-source", "clip", "--content-contract", str(path), "--raw"])
    assert result.exit_code == 2, result.output
    probe.assert_not_awaited()
