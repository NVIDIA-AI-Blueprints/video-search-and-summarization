# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Environment rendering tests for canonical Data Flywheel configuration."""

import re
from pathlib import Path

import pytest
import yaml

from src.config.base import Settings
from src.config_renderer import load_rendered_yaml, render_environment_values

REPO_ROOT = Path(__file__).resolve().parents[3]
SHIPPED_DEFT_CONFIG = REPO_ROOT / "config" / "config.deft.yaml"
SOURCE_COMPOSE = REPO_ROOT / "deploy" / "docker-compose.yaml"
#: Compose services running the Data Flywheel application image.
DFW_COMPOSE_SERVICES = ("api", "celery_worker", "celery_parent_worker", "celery_beat")


def test_renderer_preserves_types_and_supports_defaults() -> None:
    """Complete scalar placeholders render strings, booleans, nulls, and fallbacks."""
    template = {
        "required": "${REQUIRED}",
        "enabled": "${ENABLED|bool:-true}",
        "optional": "${OPTIONAL|nullable-string:-null}",
        "nested": ["${WITH_DEFAULT:-fallback}"],
    }

    rendered = render_environment_values(template, environ={"REQUIRED": "value"})

    assert rendered == {
        "required": "value",
        "enabled": True,
        "optional": None,
        "nested": ["fallback"],
    }


@pytest.mark.parametrize(
    ("template", "environ", "message"),
    [
        ("${REQUIRED}", {}, "REQUIRED"),
        ("${REQUIRED}", {"REQUIRED": ""}, "REQUIRED"),
        ("prefix-${REQUIRED}", {"REQUIRED": "value"}, "entire YAML scalar"),
        ("${REQUIRED", {"REQUIRED": "value"}, "Invalid environment placeholder"),
        ("${REQUIRED|int}", {"REQUIRED": "1"}, "supported format"),
        ("${ENABLED|bool}", {"ENABLED": "yes"}, "true or false"),
    ],
)
def test_renderer_rejects_invalid_placeholders(
    template: str,
    environ: dict[str, str],
    message: str,
) -> None:
    """Malformed, missing, empty, and mistyped values fail before model parsing."""
    with pytest.raises(ValueError, match=message):
        render_environment_values(template, environ=environ)


def test_shipped_deft_config_defaults_match_existing_runtime() -> None:
    """Rendering without overrides preserves the source deployment's current values."""
    rendered = load_rendered_yaml(SHIPPED_DEFT_CONFIG, environ={"AOI_SELECTION_TAG": "aoi1"})

    assert rendered["mlflow_config"]["tracking_uri"] == "http://localhost:5000"
    assert rendered["mlflow_config"]["artifact_location"] == "./mlruns"
    assert rendered["deft_config"]["deft_base_url"] == "http://deft:8000"
    assert rendered["deft_config"]["storage_backend_url"] == ("http://storage-backend:8080")
    assert rendered["deft_config"]["object_store_uri"] == "s3://aoi-artifacts"
    assert rendered["model_manager"]["base_url"] == "http://localhost:8090"
    assert rendered["aoi_job_config"]["model_name"] == "changenet-aoi"
    assert rendered["aoi_job_config"]["selection"]["filters"]["tag"] == "aoi1"
    assert rendered["aoi_job_config"]["kpi"]["uri"] is None
    assert rendered["aoi_job_config"]["deft"]["config"]["model"]["checkpoint"] == (
        "s3://aoi-models/baseline/1.0.0/model.pt"
    )
    assert rendered["aoi_job_config"]["promotion_policy"]["hitl"] == {
        "pull": False,
        "deploy": False,
        "canary": True,
        "full": True,
    }
    assert rendered["aoi_job_config"]["promotion_policy"]["deployment_stages"] == {
        "shadow": True,
        "canary": True,
    }


def test_shipped_deft_config_requires_selection_tag() -> None:
    """Every DEFT deployment must scope its training snapshot by AOI tag."""
    with pytest.raises(ValueError, match="AOI_SELECTION_TAG"):
        load_rendered_yaml(SHIPPED_DEFT_CONFIG, environ={})


def test_settings_load_connector_style_environment_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The application loader consumes deployment-derived endpoints and typed values."""
    overrides = {
        "BACKEND": "deft",
        "DFW_MLFLOW_URL": "http://mlflow:5000",
        "DFW_MLFLOW_ARTIFACT_LOCATION": "mlflow-artifacts:/",
        "DFW_DEFT_BASE_URL": "http://deft-service:8000",
        "DFW_STORAGE_BACKEND_URL": "http://storage-backend:8000",
        "DFW_OBJECT_STORE_URI": "s3://aoi-samples",
        "DFW_MODEL_MANAGER_URL": "http://model-manager:8090",
        "DFW_MODEL_NAME": "custom-aoi",
        "DFW_KPI_SET_URI": "s3://aoi-samples/kpi/testing/",
        "DFW_BASELINE_MODEL_CHECKPOINT": "s3://aoi-samples/models/baseline/model.pth",
        "DFW_HITL_CANARY": "false",
        "DFW_HITL_FULL": "true",
        "DFW_STAGE_SHADOW": "true",
        "DFW_STAGE_CANARY": "false",
        "AOI_SELECTION_TAG": "aoi1",
    }
    for name, value in overrides.items():
        monkeypatch.setenv(name, value)

    settings = Settings.from_yaml(SHIPPED_DEFT_CONFIG)
    system = settings.system_config
    aoi = settings.job_configs["aoi_job"]

    assert system.mlflow_config.tracking_uri == "http://mlflow:5000"
    assert system.mlflow_config.artifact_location == "mlflow-artifacts:/"
    assert system.deft_config.deft_base_url == "http://deft-service:8000"
    assert system.deft_config.storage_backend_url == "http://storage-backend:8000"
    assert system.deft_config.object_store_uri == "s3://aoi-samples"
    assert system.model_manager.base_url == "http://model-manager:8090"
    assert aoi.model_name == "custom-aoi"
    assert aoi.selection.filters["tag"] == "aoi1"
    assert aoi.kpi.uri == "s3://aoi-samples/kpi/testing/"
    assert aoi.promotion_policy.hitl.canary is False
    assert aoi.promotion_policy.deployment_stages.canary is False


def test_source_compose_passes_canonical_config_inputs_to_every_dfw_process() -> None:
    """All processes receive config inputs without imposing DEFT rules on NMP."""
    compose = yaml.safe_load(SOURCE_COMPOSE.read_text(encoding="utf-8"))
    expected = {
        "DFW_MLFLOW_URL",
        "DFW_MLFLOW_ARTIFACT_LOCATION",
        "DFW_DEFT_BASE_URL",
        "DFW_STORAGE_BACKEND_URL",
        "DFW_OBJECT_STORE_URI",
        "DFW_MODEL_MANAGER_URL",
        "DFW_MODEL_NAME",
        "DFW_ITS_MODEL_NAME",
        "DFW_KPI_SET_URI",
        "DFW_BASELINE_MODEL_CHECKPOINT",
        "DFW_HITL_CANARY",
        "DFW_HITL_FULL",
        "DFW_STAGE_SHADOW",
        "DFW_STAGE_CANARY",
        "DFW_ITS_KPI_SET_URI",
        "AOI_SELECTION_TAG",
    }

    for service_name in DFW_COMPOSE_SERVICES:
        assert expected <= compose["services"][service_name]["environment"].keys()
        assert compose["services"][service_name]["environment"]["AOI_SELECTION_TAG"] == (
            "${AOI_SELECTION_TAG:-}"
        )


def test_source_compose_forwards_every_variable_the_shipped_config_references() -> None:
    """No config input reaches the containers unset — the credential gap regression.

    ``StorageBackendClient._headers`` simply OMITS the ``Authorization`` header
    when the variable named by ``storage_api_key_env`` is absent, so a Compose
    file that fails to forward a credential produces silent unauthenticated
    calls rather than an error. This test derives the required set from the
    shipped config instead of restating it, so a newly referenced variable
    cannot be added to the config without also being wired into every DFW
    process — including one named INDIRECTLY, as an ``*_api_key_env`` value.
    """
    compose = yaml.safe_load(SOURCE_COMPOSE.read_text(encoding="utf-8"))
    config = yaml.safe_load(SHIPPED_DEFT_CONFIG.read_text(encoding="utf-8"))
    referenced: set[str] = set()

    def collect(node: object, key: str | None = None) -> None:
        if isinstance(node, dict):
            for child_key, child in node.items():
                collect(child, child_key)
        elif isinstance(node, list):
            for child in node:
                collect(child, key)
        elif isinstance(node, str):
            # Placeholders resolved at render time, e.g. "${DFW_KPI_SET_URI|nullable-string:-null}".
            referenced.update(re.findall(r"\$\{([A-Z][A-Z0-9_]*)[|:}]", node))
            # Credential variables are named indirectly: the config carries the
            # NAME of the env var, which the client reads via os.environ.
            if key is not None and key.endswith("api_key_env"):
                referenced.add(node)

    collect(config)

    # The indirect arm of `collect` is doing work: this is a credential the
    # config names by VARIABLE NAME rather than by ${...} placeholder, so a
    # regex-only sweep would miss it and the assertion below would pass vacuously.
    assert "STORAGE_BACKEND_API_KEY" in referenced, "config no longer names the credential"

    for service_name in DFW_COMPOSE_SERVICES:
        environment = compose["services"][service_name]["environment"]
        missing = sorted(referenced - environment.keys())
        assert not missing, f"{service_name} does not receive config inputs: {missing}"


def test_nmp_config_preserves_literal_environment_syntax(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Environment rendering is DEFT-only, so NMP strings remain byte-for-byte values."""
    monkeypatch.delenv("BACKEND", raising=False)
    monkeypatch.setenv("LITERAL_NAMESPACE", "must-not-render")
    config = tmp_path / "config.yaml"
    nmp_config = yaml.safe_load((REPO_ROOT / "config" / "config.yaml").read_text())
    nmp_config["nmp_config"]["nmp_namespace"] = "${LITERAL_NAMESPACE}"
    config.write_text(yaml.safe_dump(nmp_config), encoding="utf-8")

    settings = Settings.from_yaml(config)

    assert settings.system_config.nmp_config.nmp_namespace == "${LITERAL_NAMESPACE}"
